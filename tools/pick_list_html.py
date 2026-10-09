#!/usr/bin/env python3
"""Print-friendly pick list — and packing slip — for one shipment. Open it,
hit print, done.

    python tools/pick_list_html.py <order-id>
        -> http://127.0.0.1:8770/pick/<token>     (expires in 48h)
    python tools/pick_list_html.py <order-id> --local-only
        -> pick_lists/pick_<id>.html

The result is a LINK, not a file path (#151). The rendered page is parked in
lib/pick_store.py's short-lived store and served by webapp/server.py's
`/pick/{token}` route, so the sheet can be opened and printed from a browser
without anyone knowing where on disk it landed — which is what a path is
worth to the person standing at the shelves. Start the server first:

    python -m lib.cli serve

`--local-only` is the escape hatch (and what `--out` / an unreachable store
fall back to): the same page written to pick_lists/ the way it always was.

One page == one box. Several ids may share a page only when the buyer AND the
full ship-to address are identical (the one case eBay merges under a single
shipping label); assert_one_shipment() hard-stops anything else, because a
combined sheet for two destinations is a mis-ship — the picker packs both
items into one box and one buyer never gets their order. Run the tool once
per shipment.

Same data as `python -m lib.cli pick-list`, rendered as a page instead of
terminal text, with a small grayscale thumbnail of the item's hero photo so
picking off a shelf doesn't require re-reading the title. Deliberately
low-res/low-quality/grayscale — this is a pick sheet, not a photo proof, and
should not burn a color cartridge printing it.

No buyer street address and no full buyer name on this page — the buyer reads
as first name + last initial ("Mike H.") plus city and state, which is all a
picker needs to match the box to the label eBay prints. The street address is
deliberately not rendered: the sheet is printed, handed around and
photographed, and the shipping label already carries the full address.
tools/pick_list.py's terminal output (seller-only, stays on this machine) still
shows the full address for actually addressing a box.

That leaves the buyer's first name, last initial and city/state as the only
personal things on a page now served over HTTP, and the fencing around it
stands regardless: the app binds to 127.0.0.1 only, the URL carries 256 bits of
randomness and no order id, the sheet deletes itself when it expires, and the
route sends no-store + noindex. lib/pick_store.py holds those rules and the
reasoning behind each. Local copies still go to pick_lists/ (gitignored), and
no sheet, link or token is ever committed, written to a ledger, or sent
anywhere off this machine.

No seller financials on this page — deliberately. This sheet can end up seen
by the buyer during packing (dropped in the box by mistake, photographed,
whatever), so it shows only the order total, which the buyer already knows
from their own receipt. tools/pick_list.py's terminal output is seller-only
and does show buyer-paid-shipping / net payout; do not port those figures
here. See GH #84.

Stores (#156). This page IS the packing slip, so its letterhead is the
store's identity, read from config.get_store(store):

  * default store — its configured strings, falling back to the POP'S GAMES
    literals this sheet has always printed (an unconfigured single-store
    setup changes nothing);
  * a named store — its own configured strings and NOTHING else. An unset
    field is left off; with none set the sheet goes out with no brand block
    at all. It never falls back to Pop's Games: an as-is junk lot shipped
    under the brand that offers 30-day free returns is the exact
    cross-branding #156 §1 was filed about.

A named store's sheet also says, on screen only (hidden when printed, so the
store's internal name never goes in the box), which eBay account it expects:
the "Buy label" links resolve against whichever account the BROWSER is signed
into, not the one the order came from. `--store NAME` picks the account the
order is looked up in; a group of orders from two stores is refused.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                 # noqa: E402
from PIL import Image                                              # noqa: E402

import pick_store                                                  # noqa: E402
import stores                                                      # noqa: E402
from config import get_store                                       # noqa: E402
from pick_list import (_money, drafts_for, fetch_recent,            # noqa: E402
                       hand_locations_for, ledger_for, order_store, ship_to,
                       shipment_key as _shipment_key)
from sync_actuals import match_sale                                 # noqa: E402

# Fallback letterhead when `store:` in config.yaml leaves a field unset —
# what this sheet has always shown, kept as the default so an unconfigured
# store.yaml changes nothing (see lib/config.get_store()). DEFAULT STORE ONLY:
# a named store never inherits these (#156) — see letterhead().
_DEFAULT_BRAND_NAME = "POP'S GAMES"
_DEFAULT_BRAND_TAGLINE = "BUY · SELL · TRADE"
_DEFAULT_BRAND_STOREFRONT = "ebay.com/usr/popsgames"


def letterhead(store: str) -> dict:
    """The masthead strings for `store`: {name, tagline, storefront}.

    Default store: configured strings, else the POP'S GAMES literals. Named
    store: configured strings only — "" where unset, never Pop's Games (#156
    §1). get_store() already refuses to inherit identity keys from the
    default store; this refuses to re-add them from a literal."""
    s = get_store(store)
    if stores.is_default(store):
        return {"name": s["display_name"] or _DEFAULT_BRAND_NAME,
                "tagline": s["tagline"] or _DEFAULT_BRAND_TAGLINE,
                "storefront": s["storefront_url"] or _DEFAULT_BRAND_STOREFRONT}
    return {"name": s["display_name"], "tagline": s["tagline"],
            "storefront": s["storefront_url"]}


def _sheet_store(orders: list[dict], store: str | None) -> str:
    """The one store these orders belong to. An explicit `store` wins; else
    the orders' own tag (pick_list.order_store); untagged orders keep the
    pre-#156 behaviour of the ambient store. Orders from two stores on one
    page is refused outright — it can't be one box (see shipment_key)."""
    tagged = {order_store(o) for o in orders if o.get("_store")}
    if len(tagged) > 1:
        raise ValueError("orders from more than one store can't share a pick sheet: "
                         + ", ".join(sorted(tagged)))
    if store:
        store = stores.resolve_store_name(store)
        if tagged and tagged != {store}:
            raise ValueError(f"these orders came from store {tagged.pop()!r}, "
                             f"not {store!r} — refusing its letterhead")
        return store
    if tagged:
        return tagged.pop()
    return stores.resolve_store_name(None)

THUMB_PX = 110      # small on purpose — a pick sheet, not a photo proof; also
                    # what keeps a 4-item grouped list on one printed page
JPEG_Q = 55         # low quality on purpose — less ink, smaller file
BG_LUMA_MAX = 40    # shoot backdrop is near-black studio velvet; below this -> white
SCREEN_PCT = 0.5    # blend every pixel 50% toward white — a print "50% screen",
                    # literally half the ink of a full-tone grayscale print
_SCREEN_LUT = [int(round(p + (255 - p) * SCREEN_PCT)) for p in range(256)]
FEATHER_FRAC = 0.24  # outer ~24% of each edge ramps to white — no hard photo
                     # rectangle sitting on the page, whatever the shot's own
                     # background (black studio velvet or a catalog's own
                     # light backdrop) fades into the page instead of a border


def _key_out_dark_bg(im: Image.Image) -> Image.Image:
    """Swap the near-black studio backdrop for white so the thumbnail doesn't
    print as a solid block of ink. Corner-sampled: reads the four corners to
    confirm the backdrop actually is dark before touching anything, so a
    photo shot on a light background passes through untouched."""
    arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
    h, w = arr.shape[:2]
    corners = np.concatenate([arr[:5, :5].reshape(-1, 3), arr[:5, -5:].reshape(-1, 3),
                               arr[-5:, :5].reshape(-1, 3), arr[-5:, -5:].reshape(-1, 3)])
    if corners.mean() > BG_LUMA_MAX:
        return im  # background isn't dark — leave the photo alone
    luma = arr.mean(axis=2)
    mask = luma < BG_LUMA_MAX
    out = arr.copy()
    out[mask] = 255
    return Image.fromarray(out)


def _fade_edges(im: Image.Image, feather_frac: float = FEATHER_FRAC) -> Image.Image:
    """Soft rectangular vignette to white at all four edges, so the thumbnail
    blends into the page instead of reading as a hard-bordered photo — the
    print equivalent of a feathered mask, not a crop."""
    arr = np.asarray(im.convert("L"), dtype=np.float32)
    h, w = arr.shape[:2]
    fx, fy = max(w * feather_frac, 1), max(h * feather_frac, 1)
    xs, ys = np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32)
    dx = np.minimum(xs, w - 1 - xs)
    dy = np.minimum(ys, h - 1 - ys)
    wx = np.clip(dx / fx, 0, 1)
    wy = np.clip(dy / fy, 0, 1)
    weight = np.minimum(wx[None, :], wy[:, None])   # 1 = keep original, 0 = full white at the edge
    out = arr * weight + 255.0 * (1 - weight)
    return Image.fromarray(out.astype(np.uint8))


OUT_DIR = ROOT / "pick_lists"


def _thumb_uri(path: Path) -> str | None:
    if not path or not path.exists():
        return None
    with Image.open(path) as im:
        im = _key_out_dark_bg(im)
        im = im.convert("L")            # grayscale — no color ink for a shelf-pick sheet
        im = im.point(_SCREEN_LUT)      # 50% screen — halves the ink again
        im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        im = _fade_edges(im)            # feather to white — no hard photo border on the page
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_Q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# The listing's own photo, for an item with no local shoot folder: hand listed,
# or its inventory/ folder is gone (the 2026-10 Linux rebuild left none). Off by
# default so render_html() stays offline under test; the real callers (main()
# here, the --poll publisher in pick_list.py) switch it on.
EBAY_PHOTOS = False
EBAY_PHOTO_DIR = ROOT / "inventory" / "_ebay_photos"
_BROWSE_BY_LEGACY_ID = "/buy/browse/v1/item/get_item_by_legacy_id"


def _ebay_photo_path(listing_id: str) -> Path | None:
    """The listing's main photo, fetched from eBay's Browse API once and kept
    as inventory/_ebay_photos/<listing id>.jpg. Browse still answers for an
    item that has just sold. Never raises: a sheet without a photo still
    ships, so a failed lookup is a warning and a "no photo" box."""
    if not (EBAY_PHOTOS and listing_id and str(listing_id).isdigit()):
        return None
    path = EBAY_PHOTO_DIR / f"{listing_id}.jpg"
    if path.exists():
        return path
    try:
        import urllib.request                             # noqa: PLC0415
        import ebay_client as ec                          # noqa: PLC0415
        item = ec.api_get(_BROWSE_BY_LEGACY_ID, query={"legacy_item_id": listing_id})
        url = (item.get("image") or {}).get("imageUrl")
        if not url:
            return None
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
        EBAY_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(data)
        part.replace(path)
        return path
    except Exception as e:                                # noqa: BLE001
        print(f"  ! no eBay photo for item {listing_id} ({e})", file=sys.stderr)
        return None


def _hero_path(folder: str) -> Path | None:
    """The item's cover shot: review_card.md's recorded `hero` line if the item
    went through REVIEW, else the first frame in listing/ as a fallback."""
    if not folder:
        return None
    d = ROOT / folder
    rc = d / "review_card.md"
    if rc.exists():
        for line in rc.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("hero "):
                parts = line.split()
                if len(parts) >= 2:
                    p = d / parts[1]
                    if p.exists():
                        return p
    listing_dir = d / "listing"
    if listing_dir.is_dir():
        frames = sorted(listing_dir.glob("*.jpg")) + sorted(listing_dir.glob("*.JPG"))
        if frames:
            return frames[0]
    return None


_LOCATION_OVERRIDE_RE = re.compile(
    r'describe location as ["“]([^"”]+)["”]', re.IGNORECASE)


def _pick_location(folder: str) -> str:
    """Where a person actually goes to pull this — not the full repo path.

    Walks up from the item's folder to the nearest ancestor holding a
    context.txt (the estate/lot marker, e.g. inventory/ESTATES/SCJ/context.txt)
    and returns just that directory's name, e.g. "SCJ" — unless that
    context.txt itself overrides the display name (e.g. inventory/FREE's
    says `describe location as "BIN-4" and NOT "FREE"`, because the
    directory name isn't what's written on the shelf). Falls back to the
    item folder's own name if no ancestor has a context.txt at all."""
    if not folder:
        return ""
    d = (ROOT / folder).resolve()
    root = ROOT.resolve()
    cur = d
    while root in cur.parents or cur == root:
        ctx = cur / "context.txt"
        if ctx.exists():
            text = ctx.read_text(encoding="utf-8", errors="ignore")
            m = _LOCATION_OVERRIDE_RE.search(text)
            return m.group(1).strip() if m else cur.name
        if cur == root:
            break
        cur = cur.parent
    return d.name


def _short_name(full: str) -> str:
    """Buyer as first name + last initial — "Mike Hein" -> "Mike H.". Enough
    to match a box to its label, not enough to be a name on a page that gets
    printed, photographed and passed around. A single-word name is returned
    as-is; an empty name stays empty rather than becoming a stray period."""
    parts = (full or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _addr_key(o: dict) -> tuple:
    to = ship_to(o)
    addr = to.get("contactAddress") or {}
    return (to.get("fullName", ""), addr.get("addressLine1", ""), addr.get("postalCode", ""))


def _label_url(order_id: str) -> str:
    """eBay's direct 'buy this order's shipping label' redirect — the same
    URL Seller Hub lands on after a seller finds the order in the awaiting-
    shipment list and clicks Buy Label, minus that search-and-click (#162)."""
    return f"https://www.ebay.com/lbr/go?t={order_id}"


def render_html(orders: list[dict], drafts: list[dict], ledger: list[dict],
                store: str | None = None) -> str:
    """One or several orders on one page. Several orders are for the case
    eBay merges into a single shipping label (same buyer) — the seller packs
    them as one box, so the pick list should read as one, not N separate
    printouts. Each item still carries its own order id when grouped, since
    that's what ties it back to eBay's merge screen.

    `store` (#156) picks the letterhead and the account the label links
    warn about; by default it is the orders' own store tag."""
    store = _sheet_store(orders, store)
    brand = letterhead(store)
    brand_name, brand_tagline, brand_storefront = (
        brand["name"], brand["tagline"], brand["storefront"])
    hand = hand_locations_for(store)

    grouped = len(orders) > 1
    to = ship_to(orders[0])
    addr = to.get("contactAddress") or {}
    mismatch = grouped and any(_addr_key(o) != _addr_key(orders[0]) for o in orders[1:])

    ship_by = min((li.get("lineItemFulfillmentInstructions", {}).get("shipByDate") or "zz"
                   for o in orders for li in (o.get("lineItems") or [])), default="")[:10]
    payment_bit = "/".join(sorted({o.get("orderPaymentStatus", "") for o in orders}))

    item_blocks = []
    for o in orders:
        for li in (o.get("lineItems") or []):
            row = {"sku": li.get("sku") or "", "listing_id": li.get("legacyItemId", ""),
                   "title": li.get("title", "")}
            folder, _ask, _how = match_sale(row, drafts, ledger)
            hero = _hero_path(folder) if folder else None
            thumb = _thumb_uri(hero or _ebay_photo_path(row["listing_id"]))
            pic = f'<img src="{thumb}" alt="">' if thumb else '<div class="noimg">no photo</div>'
            sku_bit = f" &middot; sku {html.escape(row['sku'])}" if row["sku"] else ""
            order_bit = (f" &middot; order {html.escape(o.get('orderId', ''))}"
                         f" &middot; rec #{html.escape(str(o.get('salesRecordReference', '')))}"
                         f" &middot; {_money(li.get('lineItemCost'))}") if grouped else ""
            # FROM is a pick location — where a person walks to pull the item.
            # A hand-listed item has no local folder; its shelf, if anyone
            # recorded one, is in hand_listed_locations.csv. Otherwise drop
            # the line rather than print a placeholder explaining our own
            # internals on a sheet that gets packed with the box.
            location = (_pick_location(folder) if folder
                        else (hand.get(row["listing_id"])
                              or hand.get(row["sku"]) or ""))
            from_line = ("\n          <div class=\"from\">FROM&nbsp; "
                         f"{html.escape(location)}</div>") if location else ""
            item_blocks.append(f"""
      <div class="item">
        <div class="thumb">{pic}</div>
        <div class="details">
          <div class="qty">&times;{li.get('quantity', 1)}</div>
          <div class="title">{html.escape(li.get('title', ''))}</div>
          <div class="meta">item {html.escape(str(li.get('legacyItemId', '')))}{sku_bit}{order_bit}</div>{from_line}
        </div>
      </div>""")

    # City + state only. Enough for a picker to sanity-check the box against
    # the label eBay prints; not a street address, so the sheet stays safe to
    # print, carry around and photograph.
    city_state = ", ".join(p for p in (addr.get("city"), addr.get("stateOrProvince")) if p)
    city_state_html = (f'<div>{html.escape(city_state)}</div>' if city_state else "")

    ship_by_bit = (f" &middot; SHIP BY {html.escape(ship_by)}"
                   if ship_by and ship_by != "zz" else "")

    if grouped:
        title = f"Pick — {len(orders)} orders combined"
        heading = (f"PICK — {len(orders)} orders combined &middot; "
                   f"{html.escape(_short_name(to.get('fullName', '')))}")
        vias = sorted({(html.escape(ship_to(o).get('carrier', '')),
                         html.escape(ship_to(o).get('service', ''))) for o in orders})
        via_line = (f"VIA {vias[0][0]} {vias[0][1]}" if len(vias) == 1
                    else "VIA varies by order — check each order in Seller Hub")
        total = sum(float(((o.get("pricingSummary") or {}).get("total") or {}).get("value", 0))
                    for o in orders)
        footer_line = f"ORDER TOTAL ({len(orders)} orders combined) {_money({'value': total})}"
        warn_html = ('<div class="warn">&#9888; ship-to details differ between these orders — '
                     'verify before packing as one box.</div>' if mismatch else "")
    else:
        o = orders[0]
        title = f"Pick — {o.get('orderId', '')}"
        heading = (f"PICK — order {html.escape(o.get('orderId', ''))} &middot; "
                   f"sales record #{html.escape(str(o.get('salesRecordReference', '')))}")
        via_line = f"VIA {html.escape(to.get('carrier', ''))} {html.escape(to.get('service', ''))}"
        footer_line = f"ORDER {_money((o.get('pricingSummary') or {{}}).get('total'))} total"
        warn_html = ""

    # One "Buy label" link per order, straight to eBay's per-order label
    # flow (#162) instead of the awaiting-shipment list the seller used to
    # have to search through. Grouped orders each keep their own eBay
    # order and so each keep their own link, labelled by order id so they
    # don't get mixed up; a single order gets one plain link, as before.
    def _label_link(o: dict) -> str:
        oid = o.get("orderId", "")
        text = f"Buy label (order {html.escape(oid)}) &rarr;" if grouped else "Buy label &rarr;"
        # The warning is #156's, moved in here from the single hard-coded button
        # main replaced: the link resolves against whichever eBay account the
        # BROWSER is signed into, not the account the order came from. With two
        # stores live that is a real way to buy a label on the wrong account, and
        # now it rides every order's button rather than only the one-order case.
        warn = (f"Opens the label flow for whichever eBay account this browser is "
                f"signed into — verify it is {html.escape(account)} before "
                f"buying a label off this sheet.")
        return (f'<a href="{_label_url(oid)}" target="_blank" rel="noopener" '
                f'title="{warn}">{text}</a>')

    # Which account the label links need, in words (#156). Default store: its
    # storefront, as before. Named store: its own storefront first when it has
    # one (#178), always with its name — never the default store's.
    if stores.is_default(store):
        account = brand_storefront
    elif brand_storefront:
        account = f"{brand_storefront} (the '{store}' store's eBay account)"
    else:
        account = f"the '{store}' store's eBay account"
    labelbtn_html = " &middot; ".join(_label_link(o) for o in orders if o.get("orderId"))
    # Screen-only (hidden in print with the label buttons): the store's
    # internal name is for the packer, not for the buyer who opens the box.
    acct_html = ("" if stores.is_default(store) else
                 f'<div class="acct">Store: <b>{html.escape(store)}</b> &mdash; '
                 f'sign this browser into {html.escape(account)} before using '
                 f'Buy label; the link opens whichever eBay account is signed in.</div>')
    brand_lines = "".join(
        f'\n    <div class="{cls}">{html.escape(val)}</div>'
        for cls, val in (("nm", brand_name), ("tg", brand_tagline),
                         ("st", brand_storefront)) if val)
    # A named store with no identity configured gets a neutral sheet: no
    # masthead at all rather than an empty rule or someone else's name.
    brand_html = (f'<div class="brand">\n    <div class="hr"></div>{brand_lines}\n  </div>\n'
                  f'  <hr class="divider">' if brand_lines else "")

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<title>{html.escape(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  :root {{ --ink: #141210; --red: #a8322b; --grey: #4a443c; color-scheme: light; }}
  @page {{ size: letter; margin: .5in; }}
  /* A pick sheet is a paper object — it commits to one light look. The
     background is stated rather than inherited so the page still reads as
     paper when the viewer's browser ground is dark. */
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #111;
          background: #fff;
          max-width: 640px; margin: 24px auto; padding: 0 16px; }}
  /* Two-face system, matching the brand/ thank-you cards: Georgia carries display
     content (headings, item titles), Courier New carries utility/data
     (ids, dates, addresses, money) — same split the card uses between its
     "Thank you." headline and its store-line utility text. */
  h1 {{ font-size: 1.05rem; font-weight: 400; margin: 0 0 2px; }}
  .sub {{ font-family: 'Courier New', monospace; letter-spacing: .03em;
          color: #444; font-size: .78rem; margin-bottom: 14px; }}
  .warn {{ font-family: 'Courier New', monospace; font-size: .78rem; color: var(--red);
           border: 1px solid var(--red); padding: 6px 8px; margin-bottom: 12px; }}
  .item {{ display: flex; gap: 12px; border-top: 1px solid #999; padding: 7px 0;
           page-break-inside: avoid; }}
  .thumb img {{ width: {THUMB_PX}px; height: {THUMB_PX}px; object-fit: contain; }}
  .noimg {{ width: {THUMB_PX}px; height: {THUMB_PX}px; border: 1px dashed #999;
            display: flex; align-items: center; justify-content: center;
            color: #999; font-size: .75rem; }}
  .details {{ flex: 1; }}
  .qty {{ font-weight: bold; }}
  .title {{ font-size: 1rem; margin: 2px 0; }}
  .meta {{ font-family: 'Courier New', monospace; letter-spacing: .02em;
           color: #333; font-size: .78rem; }}
  .from {{ font-family: 'Courier New', monospace; letter-spacing: .02em;
           color: #555; font-size: .74rem; margin-top: 4px; }}
  .shipto {{ border-top: 2px solid #111; margin-top: 8px; padding-top: 10px; }}
  .shipto b {{ display: block; margin-bottom: 4px; font-family: Georgia, serif;
               font-weight: 700; letter-spacing: .26em; text-transform: uppercase;
               font-size: .8rem; color: var(--ink); }}
  .footer {{ font-family: 'Courier New', monospace; letter-spacing: .02em;
             font-size: .78rem; color: #333; margin-top: 6px; }}
  .printbtn {{ margin: 14px 0; }}
  .labelbtn {{ font-family: 'Courier New', monospace; font-size: .8rem; }}
  .acct {{ font-family: 'Courier New', monospace; font-size: .78rem; color: var(--red);
           border: 1px dashed var(--red); padding: 6px 8px; margin: 8px 0 12px; }}

  /* Store letterhead — same mark/order as the identity block on the
     brand/<store> thank-you cards (rule, name, tagline, storefront), reused
     here as a masthead instead of a card. Brand-neutral on purpose: this
     stylesheet ships inside every store's sheet (#156). */
  .brand {{ margin-bottom: 14px; }}
  .brand .hr {{ width: 2.6em; height: 2px; background: var(--red); margin-bottom: .4em; }}
  .brand .nm {{ font-size: 1.05rem; letter-spacing: .26em; text-transform: uppercase;
                font-weight: 700; color: var(--ink); }}
  .brand .tg {{ font-size: .68rem; letter-spacing: .2em; color: var(--grey);
                font-family: 'Courier New', monospace; margin-top: .3em; }}
  .brand .st {{ font-size: .68rem; letter-spacing: .04em; color: var(--red);
                font-family: 'Courier New', monospace; margin-top: .15em; }}
  .divider {{ border: none; border-top: 1px solid #ccc; margin: 0 0 14px; }}

  @media print {{
    .printbtn {{ display: none; }} .labelbtn {{ display: none; }} .acct {{ display: none; }}
    body {{ margin: 0; max-width: none; }}
  }}
</style></head>
<body>
  <div class="printbtn"><button onclick="window.print()">Print</button></div>
  <div class="labelbtn">{labelbtn_html}</div>
  {acct_html}
  {brand_html}
  <h1>{heading}</h1>
  <div class="sub">{html.escape(orders[0].get('creationDate', '')[:10])} &middot;
    {html.escape(payment_bit)}
    {ship_by_bit}</div>
  {warn_html}
  {''.join(item_blocks)}
  <div class="shipto">
    <b>BUYER</b>
    <div>{html.escape(_short_name(to.get('fullName', '')))}</div>
    {city_state_html}
  </div>
  <div class="footer">
    {via_line}<br>
    {footer_line}
  </div>
</body></html>"""


def _describe(o: dict) -> str:
    to = ship_to(o)
    a = to.get("contactAddress") or {}
    who = to.get("fullName") or (o.get("buyer") or {}).get("username") or "?"
    return (f"{o.get('orderId','?')}  {who}, {a.get('addressLine1','?')}, "
            f"{a.get('city','?')} {a.get('stateOrProvince','')} {a.get('postalCode','')}")


def assert_one_shipment(orders: list[dict]) -> None:
    """One page == one box. Orders may only be combined onto a single pick
    sheet when the buyer AND the full ship-to address are identical, which is
    the only case where eBay merges them under one shipping label.

    This is a hard stop, not a warning. A combined sheet for two destinations
    is a mis-ship: the picker packs both items into one box and one of the two
    buyers never gets their order. If the addresses differ by so much as an
    apartment number, these are separate shipments and get separate sheets."""
    if len(orders) < 2:
        return
    keys = {_shipment_key(o) for o in orders}
    if len(keys) == 1:
        return
    lines = "\n".join("    " + _describe(o) for o in orders)
    raise SystemExit(
        "[REFUSED] these orders are not one shipment - buyer and/or ship-to "
        "address differ:\n" + lines +
        "\n\n  One page == one box. Run the tool once per order id so each "
        "shipment gets its own sheet.")


def _default_out_name(orders: list[dict]) -> str:
    if len(orders) == 1:
        return f"pick_{orders[0].get('orderId', 'order')}.html".replace("/", "_")
    username = (orders[0].get("buyer") or {}).get("username")
    stem = username or "+".join(o.get("orderId", "") for o in orders)
    return f"pick_group_{stem}.html".replace("/", "_")


def publish(orders: list[dict], out_html: str, *,
            ttl_hours: float = pick_store.TTL_HOURS_DEFAULT,
            port: int = pick_store.DEFAULT_PORT) -> tuple[pick_store.Sheet, bool]:
    """Park the rendered sheet in the store and return (sheet, server_is_up).

    Re-running the tool for the same order supersedes its previous link
    rather than adding a second one (lib/pick_store.publish) — one shipment,
    one live URL, so a stale sheet can't be the one someone opens."""
    order_ids = [o.get("orderId", "") for o in orders if o.get("orderId")]
    sheet = pick_store.publish(out_html, order_ids=order_ids, ttl_hours=ttl_hours)
    return sheet, pick_store.server_is_up(port)


def _report(sheet: pick_store.Sheet, up: bool, port: int) -> None:
    print(f"[OK] {sheet.url(port)}")
    print(f"     expires {sheet.expires_at} "
          f"(revoke now: --revoke {sheet.order_ids[0] if sheet.order_ids else sheet.token})")
    if not up:
        print(f"[WARN] {pick_store.SERVE_HINT}")


def cmd_revoke(target: str) -> int:
    killed = pick_store.revoke(order_id=target) or pick_store.revoke(token=target)
    if not killed:
        print(f"nothing published for {target!r} (already expired, or never was)")
        return 1
    print(f"[OK] revoked {len(killed)} sheet(s) — those links are dead now")
    return 0


def cmd_list(port: int) -> int:
    sheets = pick_store.list_sheets()
    pick_store.purge_expired()
    if not sheets:
        print("no live pick sheets")
        return 0
    for s in sheets:
        print(f"{s.url(port)}  expires {s.expires_at}  "
              f"orders {', '.join(s.order_ids) or '?'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("order_id", nargs="*", metavar="ORDER_ID",
                    help="one order id. Several ids are combined onto ONE page only if they are the same buyer AND the same ship-to address (one box, one eBay label); anything else is refused - run the tool once per shipment.")
    ap.add_argument("--days", type=int, default=30, help="lookback window to find the order(s) (default 30)")
    ap.add_argument("--out", metavar="FILE", help="write to this local path instead of publishing a link")
    ap.add_argument("--local-only", action="store_true",
                    help="write pick_lists/pick_<id>.html and print the path, the pre-#151 behaviour (no link, no store)")
    ap.add_argument("--ttl", type=float, default=pick_store.TTL_HOURS_DEFAULT,
                    metavar="HOURS", help=f"how long the link lives (default {pick_store.TTL_HOURS_DEFAULT}h)")
    ap.add_argument("--port", type=int, default=pick_store.DEFAULT_PORT,
                    help=f"port the local app serves on (default {pick_store.DEFAULT_PORT})")
    ap.add_argument("--revoke", metavar="ORDER_ID|TOKEN",
                    help="delete a published sheet now instead of waiting for it to expire")
    ap.add_argument("--list", action="store_true", dest="do_list",
                    help="list the live published sheets (local only - no route does this)")
    stores.add_store_args(ap, help_extra="The order is looked up in this store's "
                                         "account and the sheet carries its letterhead.")
    args = ap.parse_args()
    if args.store:
        # One switch for both halves: fetch_orders() authenticates through
        # load_credentials() and the letterhead through get_storefront(), and
        # both read EBAYBIZ_STORE — so the account the orders came from and
        # the name printed above them can't disagree.
        os.environ["EBAYBIZ_STORE"] = args.store

    global EBAY_PHOTOS
    EBAY_PHOTOS = True

    if args.revoke:
        return cmd_revoke(args.revoke)
    if args.do_list:
        return cmd_list(args.port)
    if not args.order_id:
        ap.error("give at least one ORDER_ID (or --list / --revoke)")

    store = stores.resolve_store_name(args.store)
    candidates = fetch_recent(args.days, store)
    matches, missing = [], []
    for oid in args.order_id:
        found = next((o for o in candidates if oid in (o.get("orderId", ""), o.get("legacyOrderId", ""))), None)
        (matches if found else missing).append(found or oid)
    if missing:
        print(f"no order(s) {', '.join(missing)} in the last {args.days} days")
        return 1

    assert_one_shipment(matches)

    ledger = ledger_for(store)
    drafts = drafts_for(store, ledger)
    out_html = render_html(matches, drafts, ledger, store=store)

    # A link is the deliverable (#151); a local file is what --local-only /
    # --out ask for, and what a failed publish falls back to.
    want_local = args.local_only or bool(args.out)
    published = None
    if not (args.local_only or args.out):
        try:
            sheet, up = publish(matches, out_html, ttl_hours=args.ttl, port=args.port)
            published = sheet
            _report(sheet, up, args.port)
        except OSError as e:
            # The store is a directory; if it can't be written the sheet still
            # has to reach a human, so say what broke, fall back to the file,
            # and exit non-zero rather than pretend a link exists.
            print(f"[FAIL] could not publish the sheet ({e}); writing a local file instead")
            want_local = True

    if want_local:
        out_path = Path(args.out) if args.out else OUT_DIR / _default_out_name(matches)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_html, encoding="utf-8")
        print(f"[OK] wrote {out_path}")
        return 0 if (published or args.local_only or args.out) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
