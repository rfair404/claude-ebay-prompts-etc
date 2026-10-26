#!/usr/bin/env python3
"""TOOL LOT — one command per lot for a tool sale: the D5's frame in, a numbered draft out.

Russell puts a lot in view of the D5100 and runs, from the main checkout:

    python tools/live/tool_lot.py

Each run:
  1. grab      copies the live-view feed's latest frame (tools/d5100_webcam.sh
               writes it every 2 s). Never touches gphoto2 or the feed. A frame
               older than MAX_AGE seconds means the feed is down: refused.
  2. identify  one headless `claude -p` call with the frame inline, no tools,
               returning JSON (what's in it, condition notes, size class).
  3. draft     inventory/tool-sale-<yyyy-mm>/tool-lot-<N>/draft.md plus
               listing/D5_0001.jpg. N = highest existing + 1, claimed with an
               atomic mkdir, so two runs at once get two numbers.
  4. record    list_edit.record_draft (`list_edit.py --record`): SKU stamped,
               ledger row DRAFTED. Earlier lots in the sale that were never
               recorded (tool-lot-1, made by hand) are recorded here too.
  5. sync      the offline sync gates first (list_edit.validate_draft_for_sync).
               A fresh lot is unpriced, so the price gate (lib/price_gate.py)
               holds it and the line says so; once PRICE has run, the same
               step runs `list_edit.py --sync` (an UNPUBLISHED offer). Never
               publishes. --no-sync skips this step.

Every stage prints its time. The price on a new lot is a placeholder
(PLACEHOLDER_PRICE) and meta.notes says "PLACEHOLDER - needs PRICE before
publish"; the price gate refuses sync and publish until PRICE has run.

Per-sale settings live in <sale>/sale.json, written on the first run: store
(default "default"), rotate (degrees for PIL .rotate, default -90: the D5
comes in on its side), the two shipping policy ids (small / large lots) and
the identify model. A flag (--store, --rotate, --model) changes it for this
run and saves it for the rest of the sale.

    python tools/live/tool_lot.py                  # the normal run
    python tools/live/tool_lot.py --no-sync        # offline: frame, identify, draft, record
    python tools/live/tool_lot.py --store junk     # this sale lists to the junk store from now on
    python tools/live/tool_lot.py --frame X.jpg    # a saved frame instead of the feed's
    python tools/live/tool_lot.py --sale-dir DIR   # another sale folder (a test, a second sale)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no flock; the atomic mkdir still keeps numbers apart
    fcntl = None

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "lib"))
import show_flow  # noqa: E402  (the feed's frame paths)

INVENTORY = REPO / "inventory"
LOT_PREFIX = "tool-lot-"
LOT_RE = re.compile(rf"^{LOT_PREFIX}(\d+)$")
PHOTO = "listing/D5_0001.jpg"
MAX_AGE = 5.0  # seconds; the feed writes every 2 s
TITLE_MAX = 80
PLACEHOLDER_PRICE = "9.99"
PLACEHOLDER_NOTE = "PLACEHOLDER - needs PRICE before publish"
IDENTIFY_TIMEOUT = 90

# Live shipping (buyer-paid calculated, +$1 handling per extra item) on the
# default store, created 2026-10-22.
LIVE_SMALL = "302129021014"
LIVE_LARGE = "302129111014"
DEFAULTS = {
    "store": "default",
    "rotate": -90,
    "policy_small": LIVE_SMALL,
    "policy_large": LIVE_LARGE,
    # Measured 2026-10-25 on the live frame: Sonnet named the lot right in
    # ~4-5 s; Haiku 4.5 took as long or longer and named items that weren't there.
    "model": "claude-sonnet-5-5",
}
# Size class -> (weight lb, package L x W x D in, which policy). Guesses for
# calculated shipping; meta.notes says so.
SIZES = {
    "small": (1, (10, 7, 2), "policy_small"),
    "medium": (3, (14, 10, 4), "policy_small"),
    "large": (8, (18, 14, 8), "policy_large"),
    "heavy": (20, (20, 16, 10), "policy_large"),
}
CONDITIONS = ("USED_EXCELLENT", "USED_GOOD", "USED_ACCEPTABLE", "FOR_PARTS_OR_NOT_WORKING")

PROMPT = """You catalog a lot for an eBay tool sale from one photo. The plywood
board, the easel, the cloth behind it and the small black scale ruler are the
set, never part of the lot. Reply with JSON only, no code fence:
{"lot": "what's in the lot for a title, most valuable first, at most 62 chars, e.g. Spring Clamps, LED Flashlight & Aviation Snips",
 "items": ["one line per item or group, with count and color, e.g. 2 spring clamps, black with orange tips"],
 "type": "eBay item type, e.g. Tool Lot",
 "brand": "a brand you can read on the tools, else empty",
 "color": "main color",
 "condition": "USED_GOOD unless clearly better or worse: USED_EXCELLENT, USED_GOOD, USED_ACCEPTABLE or FOR_PARTS_OR_NOT_WORKING",
 "condition_notes": "one sentence on visible wear, said positively, e.g. Used shop tools with normal wear.",
 "disclosures": ["required disclosures only, e.g. Flashlight not tested. for anything powered"],
 "size": "small (fits a bubble mailer), medium (a small box), large (a big box) or heavy (over 15 lb)"}
Name only what you can see. Write about what is there, never about what is absent."""


class LotError(RuntimeError):
    pass


def _log(stage: str, secs: float, what: str) -> None:
    print(f"  {stage:<9}{secs:5.1f}s  {what}", flush=True)


# --------------------------------------------------------------- settings

def sale_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    return INVENTORY / f"tool-sale-{datetime.now():%Y-%m}"


def settings(sale: Path, overrides: dict) -> dict:
    """The sale's settings: sale.json over DEFAULTS, then this run's flags,
    which are saved for the rest of the sale."""
    path = sale / "sale.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    s = {**DEFAULTS, **saved, **{k: v for k, v in overrides.items() if v is not None}}
    if s != {**DEFAULTS, **saved} or not path.exists():
        sale.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    return s


# --------------------------------------------------------------- 1. grab

def feed_frames() -> list[Path]:
    """Where the running feed writes its frame (show_flow.feed_snaps)."""
    return show_flow.feed_snaps(show_flow.feed_pids())


def grab(sources: list[Path], rotate: float, max_age: float | None = MAX_AGE,
         now: float | None = None) -> Image.Image:
    """The newest frame among `sources`, rotated upright. Refuses a frame
    older than `max_age` seconds (None: any age, for --frame)."""
    found = []
    for p in sources:
        try:
            found.append((p.stat().st_mtime, p))
        except OSError:
            pass
    if not found:
        raise LotError("no D5 frame: the live-view feed isn't running "
                       f"(looked for {', '.join(str(p) for p in sources)}). "
                       "Start it: python tools/live/show_flow.py feed start")
    mtime, path = max(found)
    age = (time.time() if now is None else now) - mtime
    if max_age is not None and age > max_age:
        raise LotError(f"D5 feed is down: its newest frame ({path}) is {age:.0f}s old. "
                       "Check the camera is on, then: python tools/live/show_flow.py feed start")
    for attempt in range(3):  # the feed rewrites the file every 2 s; a read can catch it mid-write
        try:
            im = Image.open(io.BytesIO(path.read_bytes()))
            im.load()
            break
        except (OSError, SyntaxError):
            if attempt == 2:
                raise LotError(f"can't read the D5 frame {path}")
            time.sleep(0.3)
    im = im.convert("RGB")
    return im.rotate(rotate, expand=True) if rotate % 360 else im


# --------------------------------------------------------------- 2. identify

def claude_bin() -> str | None:
    """The claude CLI, found the way tools/tg7_import.sh finds it."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if found:
        return found

    def ver(p: Path):
        return [int(x) if x.isdigit() else 0 for x in p.parent.name.split(".")]
    cands = sorted(Path.home().glob(".config/Claude/claude-code/*/claude"), key=ver)
    return str(cands[-1]) if cands else None


def parse_identify(text: str) -> dict:
    """The JSON object in the model's reply (a stray code fence is fine)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise LotError(f"identify gave no JSON: {text[:200]!r}")
    try:
        data = json.loads(m.group(0))
    except ValueError as e:
        raise LotError(f"identify gave bad JSON ({e}): {text[:200]!r}") from None
    if not isinstance(data, dict) or not str(data.get("lot") or "").strip():
        raise LotError(f"identify named nothing: {text[:200]!r}")
    return data


def identify(im: Image.Image, model: str, run=None) -> dict:
    """One headless claude call with the frame inline: no tools, no settings,
    no session saved, no thinking, so it is a single fast model turn."""
    run = run or subprocess.run
    exe = claude_bin()
    if not exe:
        raise LotError("no claude CLI found (set CLAUDE_BIN)")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    msg = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": "Catalog this lot."}]}}
    cmd = [exe, "-p", "--model", model, "--effort", "low", "--tools", "",
           "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
           "--disable-slash-commands", "--system-prompt", PROMPT,
           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    try:
        r = run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True,
                timeout=IDENTIFY_TIMEOUT, cwd=tempfile.gettempdir(),
                env={**os.environ, "MAX_THINKING_TOKENS": "0"})
    except subprocess.TimeoutExpired:
        raise LotError(f"identify took over {IDENTIFY_TIMEOUT}s") from None
    result = None
    for line in (r.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            result = ev
    if r.returncode or not result or result.get("is_error"):
        why = (result or {}).get("result") or (r.stderr or "").strip()[-200:] or f"exit {r.returncode}"
        raise LotError(f"identify failed: {why}")
    return parse_identify(str(result.get("result") or ""))


# --------------------------------------------------------------- 3. draft

def lot_numbers(sale: Path) -> list[int]:
    out = []
    if sale.is_dir():
        for d in sale.iterdir():
            m = LOT_RE.match(d.name)
            if m and d.is_dir():
                out.append(int(m.group(1)))
    return sorted(out)


def claim_lot(sale: Path) -> tuple[int, Path]:
    """The next lot folder, made with an atomic mkdir: a run that loses the
    race to another one takes the next number instead."""
    sale.mkdir(parents=True, exist_ok=True)
    n = (lot_numbers(sale) or [0])[-1] + 1
    while True:
        d = sale / f"{LOT_PREFIX}{n}"
        try:
            d.mkdir()
            return n, d
        except FileExistsError:
            n += 1


def title_for(n: int, lot: str) -> str:
    """TOOL LOT #<N> <what's in it>, cut at a word to 80 chars."""
    head = f"TOOL LOT #{n}"
    lot = re.sub(r"\s+", " ", lot or "").strip()
    room = TITLE_MAX - len(head) - 1
    if len(lot) > room:
        lot = lot[:room + 1].rsplit(" ", 1)[0].rstrip(" ,&-;")
    return f"{head} {lot}".strip()


def _q(v) -> str:
    """A YAML double-quoted scalar (JSON's escapes are YAML's)."""
    return json.dumps("" if v is None else str(v), ensure_ascii=False)


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return p.as_posix()


def build_draft(n: int, lot_dir: Path, ident: dict | None, s: dict, *,
                error: str | None = None, now: datetime | None = None) -> str:
    """draft.md text, in the layout of the hand-made tool-lot-1."""
    now = now or datetime.now(timezone.utc)
    ident = ident or {}
    size = str(ident.get("size") or "medium").strip().lower()
    size = size if size in SIZES else "medium"
    lb, (ln, wd, dp), pol = SIZES[size]
    cond = str(ident.get("condition") or "").strip().upper()
    cond = cond if cond in CONDITIONS else "USED_GOOD"
    items = [str(i).strip() for i in (ident.get("items") or []) if str(i).strip()]
    disclosures = " ".join(str(d).strip() for d in (ident.get("disclosures") or []) if str(d).strip())
    wear = str(ident.get("condition_notes") or "Used, with normal wear.").strip()
    cond_desc = " ".join(x for x in (wear, disclosures) if x)

    notes = [f"TOOL LOT #{n} from one D5 live-view frame, {now.astimezone():%Y-%m-%d} "
             f"(tools/live/tool_lot.py, identify: {s['model']}).",
             f"price: {PLACEHOLDER_NOTE} (${PLACEHOLDER_PRICE} is not a price).",
             f"weight/dims: guesses from size class '{size}', not measured; "
             "calculated shipping charges by them."]
    if error:
        notes.append(f"IDENTIFY FAILED: {error} -- write the title and items by hand.")
    notes_yaml = "\n".join(f"    {line}" for line in notes)

    body = ["A lot of tools, sold together.", ""]
    if items:
        body += ["## What's Included", ""] + [f"- {i}" for i in items] + [""]
    body += ["## Condition", "", f"{wear} {disclosures} Please see the photos.".replace("  ", " "),
             "", "Thank you for looking."]

    return f"""---
template_version: v1

store: {_q(s['store'])}

meta:
  item_id:              {_q(lot_dir.name)}
  shoot_dir:            {_q(_rel(lot_dir))}
  drafted_at:           {_q(now.strftime('%Y-%m-%dT%H:%M:%SZ'))}
  notes: |-
{notes_yaml}
title: {_q(title_for(n, str(ident.get('lot') or '')))}

category_id:            ""
category_path:          "Home & Garden > Tools & Workshop Equipment > Hand Tools > Other Hand Tools"

condition: {_q(cond)}

condition_description: {_q(cond_desc)}

item_specifics:
  type:                       {_q(ident.get('type') or 'Tool Lot')}
  brand:                      {_q(ident.get('brand') or 'Unbranded')}
  upc:                        ""
  collection:                 ""
  subject:                    ""
  material:                   ""
  character_family:           ""
  occasion:                   ""
  color:                      {_q(ident.get('color') or '')}
  country_of_origin:          ""
  pattern:                    ""
  theme:                      ""
  style:                      ""
  size:                       ""
  department:                 ""
  time_period_manufactured:   ""
  finish:                     ""
  extra: {{}}

format: "FIXED_PRICE"
price: {_q(PLACEHOLDER_PRICE)}
cost_of_goods: null
quantity: 1
best_offer:
  enabled: false
  auto_decline_amount: null
  auto_accept_amount: null

shipping:
  weight:
    major_lb: {lb}
    minor_oz: 0
  package_in:
    length: {ln}
    width:  {wd}
    depth:  {dp}
  international: false
  us_only: false
  free_shipping: false
  fulfillment_policy_id: {_q(s[pol])}
  domestic_shipping_type: ""
  primary_service: ""
  handling_time_days: 1
  item_location_zip: ""

photos:
  - {PHOTO}

returns_policy_id: ""

promoted:
  general:  false
  priority: false
---

""" + "\n".join(body) + "\n"


# --------------------------------------------------------------- 4. record

class _SaleLock:
    """Serialises ledger writes between two runs on the same sale."""

    def __init__(self, sale: Path):
        self.path = sale / ".lock"

    def __enter__(self):
        self.fh = open(self.path, "a+")
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def record(sale: Path, lot_dir: Path, store: str) -> tuple[str, list[str]]:
    """Record this lot (SKU + DRAFTED row) and any earlier lot in the sale
    that has a draft but was never recorded. Returns (sku, adopted names)."""
    import list_edit
    from draft_io import parse_draft
    adopted = []
    with _SaleLock(sale):
        for n in lot_numbers(sale):
            d = sale / f"{LOT_PREFIX}{n}"
            if d == lot_dir or not (d / "draft.md").exists():
                continue
            try:
                stamped = parse_draft(d / "draft.md").get("meta.ebay_inventory_sku")
            except Exception:  # noqa: BLE001 — a broken draft is left for a person
                continue
            if not stamped:
                list_edit.record_draft(d, store=store)
                adopted.append(d.name)
        sku, _ = list_edit.record_draft(lot_dir, store=store)
    return sku, adopted


# --------------------------------------------------------------- 5. sync

def sync(lot_dir: Path, run=None) -> tuple[bool, str]:
    """Run the offline sync gates; if they pass, `list_edit.py --sync` (an
    UNPUBLISHED offer). Returns (synced, one line)."""
    import list_edit
    run = run or subprocess.run
    issues = list_edit.validate_draft_for_sync(lot_dir / "draft.md")
    if issues:
        price = [i for i in issues if i.startswith("price:")]
        first = price[0] if price else issues[0]
        more = f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""
        why = "waiting on PRICE" if price else "blocked"
        return False, f"eBay draft not created, {why}: {first[:160]}{more}"
    r = run([sys.executable, str(REPO / "lib" / "list_edit.py"), "--sync", str(lot_dir)],
            capture_output=True, text=True, cwd=REPO)
    lines = [ln.strip() for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()]
    if r.returncode == 0:
        ok = next((ln for ln in lines if ln.startswith("[OK]")), "synced")
        return True, ok
    fail = next((ln for ln in reversed(lines) if ln.startswith(("[FAIL]", "[ERR", "Error", "error"))),
                lines[-1] if lines else f"exit {r.returncode}")
    return False, f"eBay sync failed: {fail[:200]}"


# --------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-sync", action="store_true", help="skip the eBay step (offline)")
    ap.add_argument("--store", help="eBay store for this sale (saved in sale.json)")
    ap.add_argument("--rotate", type=float, help="degrees for PIL .rotate (saved; default -90)")
    ap.add_argument("--model", help="identify model (saved)")
    ap.add_argument("--frame", type=Path, help="use this image instead of the feed's frame")
    ap.add_argument("--sale-dir", help="sale folder (default inventory/tool-sale-<yyyy-mm>)")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    sale = sale_dir(args.sale_dir)
    s = settings(sale, {"store": args.store, "rotate": args.rotate, "model": args.model})

    t = time.monotonic()
    try:
        if args.frame:
            im = grab([args.frame], s["rotate"], max_age=None)
        else:
            im = grab(feed_frames(), s["rotate"])
    except LotError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2
    n, lot_dir = claim_lot(sale)
    (lot_dir / "listing").mkdir()
    im.save(lot_dir / PHOTO, "JPEG", quality=92)
    _log("grab", time.monotonic() - t, f"{_rel(lot_dir / PHOTO)} {im.width}x{im.height}")

    t = time.monotonic()
    ident, err = None, None
    try:
        ident = identify(im, s["model"])
        _log("identify", time.monotonic() - t, str(ident.get("lot")))
    except LotError as e:
        err = str(e)
        _log("identify", time.monotonic() - t, f"FAILED: {err}")

    t = time.monotonic()
    draft = lot_dir / "draft.md"
    draft.write_text(build_draft(n, lot_dir, ident, s, error=err), encoding="utf-8")
    from draft_io import parse_draft
    title = parse_draft(draft).get("title")
    _log("draft", time.monotonic() - t, title)

    t = time.monotonic()
    try:
        sku, adopted = record(sale, lot_dir, s["store"])
    except Exception as e:  # noqa: BLE001 — the draft is on disk; say what didn't happen
        _log("record", time.monotonic() - t, f"FAILED: {e}")
        print(f"[FAIL] TOOL LOT #{n} drafted at {_rel(lot_dir)} but not recorded: "
              f"python lib/list_edit.py --record {_rel(lot_dir)}", file=sys.stderr)
        return 1
    extra = f" (also recorded {', '.join(adopted)})" if adopted else ""
    _log("record", time.monotonic() - t, f"sku {sku}, ledger DRAFTED, store {s['store']}{extra}")

    synced = False
    if args.no_sync:
        _log("sync", 0.0, "skipped (--no-sync)")
    else:
        t = time.monotonic()
        synced, line = sync(lot_dir)
        _log("sync", time.monotonic() - t, line)

    print(f"  total    {time.monotonic() - t0:5.1f}s")
    state = "eBay draft (unpublished)" if synced else "local draft, recorded"
    print(f"[OK] TOOL LOT #{n}: {state}. Not priced, not published.")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
