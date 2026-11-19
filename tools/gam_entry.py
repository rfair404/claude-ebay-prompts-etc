#!/usr/bin/env python3
"""gam_entry — queue mall-booth items on the Greensboro Antique Mall portal.

Fills the portal's Add Item form (SimpleConsign "Consignor Access") for every
draft whose `meta.channel` is `mall`, in the signed-in Chrome on green
(`chromium-gam.service`, debug port 9223). It runs in the background on
green's virtual display, so nothing opens on Russell's screen.

    python tools/gam_entry.py inventory/JUG-1 inventory/jug-2   # fill those
    python tools/gam_entry.py --all                             # every mall draft
    python tools/gam_entry.py --all --dry                       # show the rows only

**It never presses Submit.** Each item is saved to the portal's "Items To Be
Entered" list; Russell presses Submit himself. Nothing here talks to eBay.

Rows come from the draft: title -> Item Name (kept as written), quantity,
price, item_specifics.brand / .color / .upc, condition USED* -> Used. Category
is filled only when the draft carries `meta.gam_category`; otherwise it is left
for the portal's category default.

The login lives in green's Chrome profile (see docs/gam-entry.md). If the tab
lands on /login, the run stops and says so; it never types a password.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from draft_io import draft_channel, iter_drafts, parse_draft  # noqa: E402

CDP_DEFAULT = "127.0.0.1:9223"
CDP_TUNNEL_CMD = ("ssh -N -L 127.0.0.1:9223:127.0.0.1:9223 green "
                  "(browser: systemctl --user restart chromium-gam on green)")
PORTAL = "https://greensboroantiquemall.consignoraccess.com"
ADD_URL = PORTAL + "/add-new-item-entry"
LIST_URL = PORTAL + "/new-item-entry-list"

# Portal form field names (read off the live form 2026-11-19).
FIELDS = ("name", "quantity", "retail", "description", "upcs", "brandText", "color", "size")
BOOLS = ("used", "doNotDiscount")


class GamError(RuntimeError):
    """The portal run could not proceed; the message says what to do."""


def _price(v) -> str:
    s = re.sub(r"[^0-9.]", "", str(v or ""))
    return f"{float(s):.2f}" if s else ""


def row_from_draft(path: Path) -> dict:
    d = parse_draft(path)
    spec = d.get("item_specifics") or {}
    meta = d.get("meta") or {}
    cond = str(d.get("condition") or "").upper()
    row = {
        "name": str(d.get("title") or "").strip(),
        "quantity": str(d.get("quantity") or 1),
        "retail": _price(d.get("price")),
        "upcs": str(spec.get("upc") or "").strip(),
        "brandText": str(spec.get("brand") or "").strip(),
        "color": str(spec.get("color") or "").strip(),
        "used": cond.startswith("USED") or cond in {"PRE_OWNED", "FOR_PARTS_OR_NOT_WORKING"},
        "category": str(meta.get("gam_category") or "").strip(),
        "source": str(path.parent.name),
    }
    for junk in ("upcs", "brandText", "color"):
        if row[junk].lower() in {"unbranded", "does not apply", "n/a"}:
            row[junk] = ""
    if not row["name"] or not row["retail"]:
        raise GamError(f"{path.parent.name}: needs a title and a price in draft.md")
    return row


def collect(targets: list[str], all_root: str | None) -> list[dict]:
    paths: list[Path] = []
    if all_root:
        paths += [p for p in iter_drafts(all_root) if draft_channel(parse_draft(p).frontmatter) == "mall"]
    for t in targets:
        p = Path(t)
        p = p / "draft.md" if p.is_dir() else p
        if not p.is_file():
            raise GamError(f"no draft at {t}")
        if draft_channel(parse_draft(p).frontmatter) != "mall":
            raise GamError(f"{p.parent.name} is not a mall item (meta.channel is not 'mall')")
        paths.append(p)
    return [row_from_draft(p) for p in dict.fromkeys(paths)]


# --- page scripts ----------------------------------------------------------

_SETTER = ("const set=(el,v)=>{const proto=el.type==='checkbox'?null:HTMLInputElement.prototype;"
           "if(el.type==='checkbox'){if(el.checked!==v)el.click();return;}"
           "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v);"
           "el.dispatchEvent(new Event('input',{bubbles:true}));"
           "el.dispatchEvent(new Event('change',{bubbles:true}));};")


def fill_js(row: dict) -> str:
    """Set the form fields on the open Add Item form. Returns {ok, missing}."""
    vals = {k: row[k] for k in FIELDS if row.get(k)}
    vals["used"] = bool(row.get("used"))
    return ("(()=>{" + _SETTER + f"const vals={json.dumps(vals)};const missing=[];"
            "for(const [k,v] of Object.entries(vals)){const el=document.querySelector(`input[name=\"${k}\"]`);"
            "if(!el){missing.push(k);continue;}set(el,v);}"
            "return JSON.stringify({ok:missing.length===0,missing});})()")


def category_js(text: str) -> str:
    """Type the category and pick the first listed option that matches."""
    return ("(async()=>{" + _SETTER + "const el=document.querySelector('input[name=\"categoryId\"]');"
            "if(!el)return JSON.stringify({ok:false});el.focus();"
            f"set(el,{json.dumps(text)});await new Promise(r=>setTimeout(r,1200));"
            "const o=[...document.querySelectorAll('[role=option]')][0];"
            "if(!o)return JSON.stringify({ok:false,error:'no category option'});o.click();"
            "return JSON.stringify({ok:true,picked:o.textContent.trim()});})()")


# The only button this tool clicks is a Save button on the Add Item form.
SAVE_LABEL = "Save & Add Another"
SAVE_JS = ("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==="
           f"{json.dumps(SAVE_LABEL)});if(!b)return JSON.stringify({{ok:false}});b.click();"
           "return JSON.stringify({ok:true});})()")
STATE_JS = ("JSON.stringify({url:location.href,errors:[...document.querySelectorAll("
            "'[class*=error i],[role=alert],.Mui-error')].map(e=>e.textContent.trim()).filter(Boolean).slice(0,4)})")
LIST_JS = ("(()=>{const t=document.body.innerText;const m=t.match(/(\\d+)-(\\d+) of (\\d+)/);"
           "return JSON.stringify({total:m?+m[3]:0});})()")


# --- CDP -------------------------------------------------------------------

class Tab:
    def __init__(self, ws, timeout: float = 30.0):
        self.ws, self.timeout, self._id = ws, timeout, 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv(timeout=self.timeout))
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise GamError(f"CDP error: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if r.get("exceptionDetails"):
            raise GamError("page script threw: %s" % r["exceptionDetails"].get("text", "?"))
        v = r.get("result", {}).get("value")
        return json.loads(v) if isinstance(v, str) and v[:1] in "{[" else v

    def goto(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        for _ in range(40):
            if self.js("document.readyState") == "complete" and self.js("location.href").startswith(url[:40]):
                break
            self.js("new Promise(r=>setTimeout(r,500))")
        self.js("new Promise(r=>setTimeout(r,1500))")  # let the app render


def _open(endpoint: str):
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect
    host, _, port = endpoint.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=3).close()
    except OSError:
        raise GamError(f"nothing listening on {endpoint} — start the tunnel to green:  {CDP_TUNNEL_CMD}")
    try:
        with urllib.request.urlopen(f"http://{endpoint}/json/list", timeout=10) as r:
            targets = json.loads(r.read().decode())
    except (OSError, ValueError) as e:
        raise GamError(f"green's Chrome is not answering at {endpoint}  ({e})")
    pages = [t for t in targets if t.get("type") == "page"
             and t.get("url", "").startswith(PORTAL)]
    if not pages:
        try:
            req = urllib.request.Request(f"http://{endpoint}/json/new?{PORTAL}/dashboard", method="PUT")
            with urllib.request.urlopen(req, timeout=10) as r:
                pages = [json.loads(r.read().decode())]
        except (OSError, ValueError) as e:
            raise GamError(f"could not open a portal tab at {endpoint}  ({e})")
    try:
        return connect(pages[0]["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
    except (OSError, WebSocketException) as e:
        raise GamError(f"could not attach to the portal tab  ({e})")


def run(rows: list[dict], endpoint: str | None = None) -> list[dict]:
    """Queue every row. Returns [{source, ok, note}]. Never submits."""
    endpoint = endpoint or os.environ.get("EBAYBIZ_GAM_CDP") or CDP_DEFAULT
    out: list[dict] = []
    with _open(endpoint) as ws:
        tab = Tab(ws)
        tab.goto(LIST_URL)
        if "/login" in tab.js("location.href"):
            raise GamError("the portal is signed out — sign in again on green's Chrome (docs/gam-entry.md)")
        before = tab.js(LIST_JS)["total"]
        for row in rows:
            tab.goto(ADD_URL)
            if "/login" in tab.js("location.href"):
                raise GamError("the portal signed out mid-run; items so far are queued, the rest are not")
            res = tab.js(fill_js(row))
            if not res.get("ok"):
                out.append({"source": row["source"], "ok": False, "note": f"form fields missing: {res.get('missing')}"})
                continue
            note = ""
            if row.get("category"):
                c = tab.js(category_js(row["category"]))
                note = "category " + (c.get("picked") if c.get("ok") else "NOT set: " + str(c.get("error")))
            if not tab.js(SAVE_JS).get("ok"):
                out.append({"source": row["source"], "ok": False, "note": "Save button not found"})
                continue
            tab.js("new Promise(r=>setTimeout(r,1500))")
            st = tab.js(STATE_JS)
            out.append({"source": row["source"], "ok": not st["errors"],
                        "note": (note + " " + "; ".join(st["errors"])).strip()})
        tab.goto(LIST_URL)
        after = tab.js(LIST_JS)["total"]
    queued = sum(1 for o in out if o["ok"])
    if after - before != queued:
        out.append({"source": "(count)", "ok": False,
                    "note": f"queue went {before} -> {after}, expected +{queued}; check the list before Submit"})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("items", nargs="*", help="item folders (or draft.md files) with meta.channel: mall")
    ap.add_argument("--all", action="store_true", help="every mall draft under --root")
    ap.add_argument("--root", default=str(REPO / "inventory"))
    ap.add_argument("--dry", action="store_true", help="print the rows; touch nothing")
    ap.add_argument("--endpoint", help=f"CDP endpoint (default {CDP_DEFAULT}, or $EBAYBIZ_GAM_CDP)")
    a = ap.parse_args(argv)
    try:
        rows = collect(a.items, a.root if a.all else None)
        if not rows:
            raise GamError("no mall items given (name folders, or use --all)")
        if a.dry:
            for r in rows:
                print(json.dumps(r))
            return 0
        res = run(rows, a.endpoint)
    except GamError as e:
        print(f"gam_entry: {e}", file=sys.stderr)
        return 2
    for o in res:
        print(("ok   " if o["ok"] else "FAIL ") + o["source"] + ("  " + o["note"] if o["note"] else ""))
    ok = sum(1 for o in res if o["ok"] and o["source"] != "(count)")
    print(f"{ok} of {len(rows)} queued in 'Items To Be Entered'. Press Submit in the portal yourself.")
    return 0 if all(o["ok"] for o in res) else 1


if __name__ == "__main__":
    sys.exit(main())
