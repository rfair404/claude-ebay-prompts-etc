"""lib/live_show.py — eBay Live show support (MVP).

eBay Live has no public API (researched 2026-10-13: nothing in the developer
API catalog, the Sell release notes, or the Notification API's topic list —
the last checked against this app's own credentials). What eBay does give a
seller, per its help page (ebay.com/help/selling/ebay-live, id=5762) and the
Live Studio Resource center (ebay.com/ebaylive/studio/resources, read
2026-10-16):

  - Stream manager: "Add listings" -> "By Item ID/URL" takes pasted Item IDs,
    up to 750 listings per event.
  - "Auction and Buy It Now listings can be added by all sellers."
  - Listings with best offer, reserve pricing, or Auction with Buy It Now
    "are not compatible with Live events" (resources/itemid-listing).
  - Stream manager's "Change to auction" converts a Buy It Now listing in
    place, but only one with a single quantity and a single SKU
    (resources/lineup). An unsold auction reverts to the original listing.
  - Paid Live orders sit in "Paid - wait to ship" for 8 hours from the
    event's *scheduled* start so buyers can combine shipping (help id=5762).
  - Video from the eBay app, or from a broadcast tool such as OBS using the
    event's stream URL.

So the integration seams are (1) the Item ID list we hand Stream manager and
(2) the video we send it. This module covers both halves locally:

  lineup   build_lineup() turns raw Inventory-API offers into ordered lots,
           each flagged against the documented rules above. The eBay calls
           live in tools/live_lineup.py; nothing here touches the network.
  state    which lot is on screen now, plus a free-text line the host types.
  pages    the host control page and the OBS browser-source overlay that
           webapp/server.py serves at /live/<show> and /live/<show>/overlay.

Show data lives in inventory/_live/<show>/ (gitignored, main checkout's
store — see CLAUDE.md "Pipeline data"):

  lineup.json    {"show", "store", "built_at", "scheduled_start", "lots": [...]}
  item_ids.txt   one Item ID per line, eligible lots only — paste this
  state.json     {"lot": <1-based lot number or null>, "note": str}
"""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
LIVE_DIR = REPO / "inventory" / "_live"

SHOW_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LIVE_FORMATS = ("AUCTION", "FIXED_PRICE")
MAX_LOTS = 750   # Stream manager's per-event listing cap (help page)
# Live orders stay "Paid - wait to ship" this long after the event's
# scheduled start, so later purchases can be combined (help id=5762).
SHOPPING_WINDOW = timedelta(hours=8)
BEST_OFFER_FLAG = "Best Offer is on (not allowed on eBay Live)"


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def validate_show_name(name: str) -> str:
    if not isinstance(name, str) or not SHOW_NAME_RE.match(name) or ".." in name:
        raise ValueError(f"invalid show name: {name!r} "
                         f"(letters, digits, '.', '_', '-'; max 64)")
    return name


def show_dir(name: str, base: Optional[Path] = None) -> Path:
    return (base or LIVE_DIR) / validate_show_name(name)


# ---------------------------------------------------------------------------
# lineup
# ---------------------------------------------------------------------------

def live_flags(offer: dict) -> list[str]:
    """Why eBay's own documented rules would keep this offer off a show.

    Only rules eBay states on its eBay Live help page are checked here. An
    empty list means "nothing documented rules it out", not "eBay will
    accept it" — Stream manager has the final say.
    """
    flags = []
    status = (offer.get("listing") or {}).get("listingStatus")
    if status != "ACTIVE":
        flags.append(f"not an active listing (listingStatus={status})")
    fmt = offer.get("format")
    if fmt not in LIVE_FORMATS:
        flags.append(f"format {fmt} (Live takes auction or Buy It Now)")
    terms = (offer.get("listingPolicies") or {}).get("bestOfferTerms") or {}
    if terms.get("bestOfferEnabled"):
        flags.append(BEST_OFFER_FLAG)
    ps = offer.get("pricingSummary") or {}
    if ps.get("auctionReservePrice"):
        flags.append("reserve price set (not allowed on eBay Live)")
    # On an AUCTION offer, pricingSummary.price is the Buy It Now price.
    if fmt == "AUCTION" and _money(ps.get("price")):
        flags.append("Buy It Now price on an auction (not allowed on eBay Live)")
    return flags


def live_notes(offer: dict, skus_on_listing: int = 1) -> list[str]:
    """Things that don't keep a lot off the show but limit what Stream
    manager can do with it. Only "Change to auction" is checked: it takes a
    Buy It Now listing with a single quantity and a single SKU."""
    if offer.get("format") != "FIXED_PRICE":
        return []
    notes = []
    qty = offer.get("availableQuantity")
    if isinstance(qty, int) and qty > 1:
        notes.append(f"quantity {qty}: Stream manager can't change it to auction")
    if skus_on_listing > 1:
        notes.append(f"{skus_on_listing} SKUs on one listing: Stream manager "
                     f"can't change it to auction")
    return notes


def _money(m: Optional[dict]) -> Optional[str]:
    if not m or m.get("value") in (None, ""):
        return None
    return f"{float(m['value']):.2f}"


def lot_from_offer(offer: dict, title: str = "",
                   shoot: Optional[str] = None, skus_on_listing: int = 1) -> dict:
    ps = offer.get("pricingSummary") or {}
    item_id = (offer.get("listing") or {}).get("listingId")
    return {
        "item_id": item_id,
        "sku": offer.get("sku"),
        "offer_id": offer.get("offerId"),
        "quantity": offer.get("availableQuantity"),
        "title": title,
        "format": offer.get("format"),
        "price": _money(ps.get("price")),
        "auction_start_price": _money(ps.get("auctionStartPrice")),
        "category_id": offer.get("categoryId"),
        "url": f"https://www.ebay.com/itm/{item_id}" if item_id else None,
        "shoot": shoot,
        "flags": live_flags(offer),
        "notes": live_notes(offer, skus_on_listing),
    }


def build_lineup(entries: list[tuple[dict, str, Optional[str]]]) -> list[dict]:
    """(offer, title, shoot) triples -> numbered lots, in the given order.
    A listing that appears twice keeps its first position. Offers that
    share an Item ID are one multi-SKU (variation) listing."""
    skus: dict[str, set] = {}
    for offer, _t, _s in entries:
        iid = (offer.get("listing") or {}).get("listingId")
        if iid:
            skus.setdefault(iid, set()).add(offer.get("sku"))
    lots, seen = [], set()
    for offer, title, shoot in entries:
        iid = (offer.get("listing") or {}).get("listingId")
        lot = lot_from_offer(offer, title, shoot, len(skus.get(iid, ())) or 1)
        key = lot["item_id"] or lot["sku"]
        if key in seen:
            continue
        seen.add(key)
        lots.append(lot)
    for i, lot in enumerate(lots, 1):
        lot["lot"] = i
    return lots


def merge_lineup(existing: list[dict], new: list[dict]) -> list[dict]:
    """Append `new` lots after `existing` (mid-show adds), skipping any
    listing already in the show, and renumber. Existing lot numbers never
    move, so the host's on-screen lot stays correct."""
    have = {l.get("item_id") or l.get("sku") for l in existing}
    out = [dict(l) for l in existing]
    for lot in new:
        key = lot.get("item_id") or lot.get("sku")
        if key in have:
            continue
        have.add(key)
        out.append(dict(lot))
    for i, lot in enumerate(out, 1):
        lot["lot"] = i
    return out


def eligible_ids(lots: list[dict], keep_flagged: bool = False) -> list[str]:
    return [l["item_id"] for l in lots
            if l.get("item_id") and (keep_flagged or not l.get("flags"))]


def fixable(lot: dict) -> bool:
    """Eligible already, or one Best Offer switch away from it. Reserve
    prices, Buy It Now on an auction and ended listings aren't fixed here."""
    return not lot.get("flags") or lot.get("flags") == [BEST_OFFER_FLAG]


def best_offer_only(lots: list[dict]) -> list[dict]:
    """Lots whose one and only flag is Best Offer: turning it off is all it
    takes to make them eligible, so they're what show prep edits."""
    return [l for l in lots if l.get("flags") == [BEST_OFFER_FLAG]
            and l.get("offer_id")]


def carry_restores(old: list[dict], new: list[dict]) -> list[dict]:
    """Copy each old lot's "best_offer_was" (the Best Offer terms prep
    turned off) onto the same listing in `new`, and return the old lots that
    have one but aren't in `new`. Replacing a lineup with those still
    pending would lose the record --restore-best-offer works from."""
    was = {l.get("item_id"): l["best_offer_was"] for l in old
           if l.get("best_offer_was")}
    ids = set()
    for l in new:
        ids.add(l.get("item_id"))
        if l.get("item_id") in was:
            l["best_offer_was"] = was[l["item_id"]]
    return [l for l in old if l.get("best_offer_was") and l.get("item_id") not in ids]


def offer_with_best_offer(offer: dict, writable_keys,
                          terms: Optional[dict]) -> dict:
    """The updateOffer body for `offer` (a GET /offer response) with its
    Best Offer terms set to `terms` (None: turned off) and nothing else
    changed. updateOffer is a full replace, so every writable field is
    carried over as read."""
    body = {k: offer[k] for k in writable_keys if k in offer}
    lp = dict(body.get("listingPolicies") or {})
    if terms:
        lp["bestOfferTerms"] = terms
    else:
        lp.pop("bestOfferTerms", None)
    body["listingPolicies"] = lp
    return body


# ---------------------------------------------------------------------------
# shopping window (pick lists wait for it)
# ---------------------------------------------------------------------------

def _parse_utc(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def shopping_window(lineup: dict) -> Optional[tuple[datetime, datetime]]:
    """(scheduled start, window close) for a lineup with a scheduled start."""
    start = lineup.get("scheduled_start")
    if not start:
        return None
    s = _parse_utc(start)
    return s, s + SHOPPING_WINDOW


def open_windows(store: str, now: Optional[datetime] = None,
                 base: Optional[Path] = None) -> list[dict]:
    """Every show on `store` whose shopping window hasn't closed yet."""
    now = now or datetime.now(timezone.utc)
    root = base or LIVE_DIR
    out: list[dict] = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*/lineup.json")):
        try:
            lu = json.loads(p.read_text(encoding="utf-8"))
            win = shopping_window(lu)
        except (OSError, ValueError):
            continue
        if lu.get("store") != store or not win or now >= win[1]:
            continue
        lots = lu.get("lots") or []
        out.append({"show": lu.get("show") or p.parent.name,
                    "start": win[0], "closes": win[1],
                    "item_ids": {l["item_id"] for l in lots if l.get("item_id")},
                    "skus": {l["sku"] for l in lots if l.get("sku")}})
    return out


def holding_window(order: dict, windows: list[dict]) -> Optional[dict]:
    """The open shopping window that should hold `order` back, if any.

    An order is held when it was placed before the window closes and either
    has a line item from the show's lineup, or was placed after the show's
    scheduled start. The second rule catches lots Stream manager changed to
    auction: those run under a new Item ID that isn't in our lineup.
    """
    created = order.get("creationDate")
    if not created:
        return None
    try:
        at = _parse_utc(created)
    except ValueError:
        return None
    lines = order.get("lineItems") or []
    for w in windows:
        if at >= w["closes"]:
            continue
        if at >= w["start"] or any(
                li.get("legacyItemId") in w["item_ids"] or li.get("sku") in w["skus"]
                for li in lines):
            return w
    return None


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_lineup(name: str, store: str, lots: list[dict],
                keep_flagged: bool = False, base: Optional[Path] = None,
                scheduled_start: Optional[datetime] = None) -> Path:
    """`scheduled_start` (aware) is the event's start as set in Live
    Studio; pick lists hold the show's orders until 8 hours after it."""
    d = show_dir(name, base)
    doc = {"show": name, "store": store,
           "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "scheduled_start": (scheduled_start.astimezone(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ")
                               if scheduled_start else None),
           "lots": lots}
    _atomic_write(d / "lineup.json", json.dumps(doc, indent=1) + "\n")
    ids = eligible_ids(lots, keep_flagged)
    _atomic_write(d / "item_ids.txt", "".join(f"{i}\n" for i in ids))
    return d


def load_lineup(name: str, base: Optional[Path] = None) -> Optional[dict]:
    p = show_dir(name, base) / "lineup.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_state(name: str, base: Optional[Path] = None) -> dict:
    p = show_dir(name, base) / "state.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    return {"lot": st.get("lot"), "note": st.get("note") or ""}


def save_state(name: str, lot: Optional[int], note: str,
               base: Optional[Path] = None) -> dict:
    lineup = load_lineup(name, base)
    if lineup is None:
        raise LookupError(f"show {name!r} has no lineup — run tools/live_lineup.py")
    if lot is not None and not any(l.get("lot") == lot for l in lineup["lots"]):
        raise ValueError(f"show {name!r} has no lot {lot}")
    st = {"lot": lot, "note": (note or "")[:200]}
    _atomic_write(show_dir(name, base) / "state.json", json.dumps(st) + "\n")
    return st


def current(name: str, base: Optional[Path] = None) -> dict:
    """What the overlay polls: the on-screen lot (or null) and the note."""
    lineup = load_lineup(name, base) or {"lots": []}
    st = load_state(name, base)
    lot = next((l for l in lineup["lots"] if l.get("lot") == st["lot"]), None)
    return {"show": name, "lot": lot, "note": st["note"]}


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

_e = html.escape


def render_host(name: str, lineup: dict, state: dict) -> str:
    rows = []
    for l in lineup["lots"]:
        on = l.get("lot") == state.get("lot")
        flags = "".join(f'<div class="flag">{_e(f)}</div>' for f in l.get("flags") or [])
        flags += "".join(f'<div class="m">{_e(n)}</div>' for n in l.get("notes") or [])
        price = ""
        if l.get("format") == "FIXED_PRICE" and l.get("price"):
            price = f"BIN ${_e(l['price'])}"
        elif l.get("format") == "AUCTION":
            start = l.get("auction_start_price")
            price = f"auction (listed start ${_e(start)})" if start else "auction"
        review = (f' · <a href="/review/{_e(l["shoot"])}" target="_blank">review card</a>'
                  if l.get("shoot") else "")
        ebay = (f'<a href="{_e(l["url"])}" target="_blank">{_e(l["item_id"])}</a>'
                if l.get("url") else "(no item id)")
        rows.append(
            f'<tr class="{"on" if on else ""}{" flagged" if l.get("flags") else ""}">'
            f'<td class="n">{l["lot"]}</td>'
            f'<td><div class="t">{_e(l.get("title") or l.get("sku") or "")}</div>'
            f'<div class="m">{ebay} · {price}{review}</div>{flags}</td>'
            f'<td class="b"><button onclick="show({l["lot"]})">{"On screen" if on else "Show"}</button>'
            f'<button class="ghost" onclick="copyId(\'{_e(l.get("item_id") or "")}\', this)">Copy ID</button></td>'
            f'</tr>')
    n_ok = sum(1 for l in lineup["lots"] if not l.get("flags"))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live: {_e(name)}</title>
<style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e2dc;--acc:#0b6bcb;--on:#e6f0fb;--warn:#a34b00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ececea;--mut:#9a9a94;--line:#2c2c2a;--acc:#5aa4f0;--on:#16263a;--warn:#f0a35a}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:var(--mut);font-size:13px;margin-bottom:12px}}
.bar{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;align-items:center}}
input{{flex:1;min-width:200px;padding:8px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit}}
button{{padding:7px 12px;border-radius:6px;border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer;font:inherit}}
button.ghost{{background:transparent;color:var(--acc)}}
table{{width:100%;border-collapse:collapse}} td{{border-top:1px solid var(--line);padding:8px 6px;vertical-align:top}}
td.n{{width:36px;font-weight:600;color:var(--mut)}} td.b{{width:170px;text-align:right;white-space:nowrap}}
td.b button{{margin-left:4px}} tr.on{{background:var(--on)}} tr.flagged .t{{color:var(--mut)}}
.m{{color:var(--mut);font-size:13px}} .m a{{color:var(--acc)}} .flag{{color:var(--warn);font-size:13px}}
code{{font-size:12px}}
</style></head><body><main>
<h1>Live show: {_e(name)}</h1>
<div class="sub">store <b>{_e(lineup.get("store") or "?")}</b> · {len(lineup["lots"])} lots, {n_ok} unflagged · built {_e(lineup.get("built_at") or "?")}<br>
OBS browser source: <code id="ovl">/live/{_e(name)}/overlay</code> · paste list: <code>inventory/_live/{_e(name)}/item_ids.txt</code></div>
<div class="bar"><input id="note" placeholder="Overlay line (e.g. Starting bid $5)" value="{_e(state.get('note') or '')}">
<button onclick="setNote()">Set line</button><button class="ghost" onclick="show(null)">Clear screen</button></div>
<table>{''.join(rows)}</table>
</main><script>
const SHOW={json.dumps(name)};
let cur={json.dumps(state.get("lot"))};
async function post(lot,note){{
  const r=await fetch('/live/'+SHOW+'/state',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{lot,note}})}});
  if(!r.ok){{alert('update failed: '+r.status);return false}} return true;
}}
async function show(lot){{ if(await post(lot,document.getElementById('note').value)) location.reload(); }}
async function setNote(){{ await post(cur,document.getElementById('note').value); }}
async function copyId(id,btn){{ try{{await navigator.clipboard.writeText(id);btn.textContent='Copied'}}catch(e){{prompt('Item ID',id)}} }}
document.getElementById('ovl').textContent=location.origin+'/live/'+SHOW+'/overlay';
</script></body></html>"""


def render_overlay(name: str) -> str:
    """OBS browser source: transparent page, a lower-third bar with the lot
    number, title and host's note. Polls /live/<show>/state once a second."""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Overlay: {_e(name)}</title>
<style>
html,body{{margin:0;background:transparent;overflow:hidden}}
#bar{{position:fixed;left:48px;right:48px;bottom:48px;display:none;
 background:rgba(15,15,14,.82);color:#fff;border-radius:14px;padding:20px 28px;
 font:600 40px/1.2 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.35)}}
#lot{{display:inline-block;background:#0b6bcb;border-radius:8px;padding:2px 14px;margin-right:16px;font-size:32px;vertical-align:middle}}
#title{{vertical-align:middle}}
#note{{font:500 30px/1.2 system-ui,sans-serif;color:#ffd27a;margin-top:8px}}
</style></head><body>
<div id="bar"><span id="lot"></span><span id="title"></span><div id="note"></div></div>
<script>
const SHOW={json.dumps(name)};
async function tick(){{
  try{{
    const s=await (await fetch('/live/'+SHOW+'/state',{{cache:'no-store'}})).json();
    const bar=document.getElementById('bar');
    if(!s.lot){{bar.style.display='none';return}}
    document.getElementById('lot').textContent='LOT '+s.lot.lot;
    document.getElementById('title').textContent=s.lot.title||'';
    document.getElementById('note').textContent=s.note||'';
    document.getElementById('note').style.display=s.note?'block':'none';
    bar.style.display='block';
  }}catch(e){{}}
}}
tick(); setInterval(tick,1000);
</script></body></html>"""
