#!/usr/bin/env python3
"""Print-friendly pick list for one order, or several combined — open it, hit
print, done.

    python tools/pick_list_html.py <order-id> [order-id ...]
        -> pick_lists/pick_<id>.html, or pick_lists/pick_group_<buyer>.html
           for more than one order (e.g. orders eBay will merge into a single
           shipping label — pack them as one box, so the pick list reads as
           one page, not N separate printouts)

Same data as `python -m lib.cli pick-list`, rendered as a page instead of
terminal text, with a small grayscale thumbnail of the item's hero photo so
picking off a shelf doesn't require re-reading the title. Deliberately
low-res/low-quality/grayscale — this is a pick sheet, not a photo proof, and
should not burn a color cartridge printing it.

Buyer name and street address are on this page. Like tools/pick_list.py, the
output goes to pick_lists/ (gitignored) only — never an artifact, never
committed, never anywhere that leaves the machine.

No seller financials on this page — deliberately. This sheet can end up seen
by the buyer during packing (dropped in the box by mistake, photographed,
whatever), so it shows only the order total, which the buyer already knows
from their own receipt. tools/pick_list.py's terminal output is seller-only
and does show buyer-paid-shipping / net payout; do not port those figures
here. See GH #84.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                 # noqa: E402
from PIL import Image                                              # noqa: E402

from pick_list import _money, ship_to                              # noqa: E402
from sync_actuals import fetch_orders, load_listings_ledger, match_sale, scan_drafts  # noqa: E402

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


def _pick_location(folder: str) -> str:
    """Where a person actually goes to pull this — not the full repo path.

    Walks up from the item's folder to the nearest ancestor holding a
    context.txt (the estate/lot marker, e.g. inventory/ESTATES/SCJ/context.txt)
    and returns just that directory's name, e.g. "SCJ". Falls back to the
    item folder's own name if no ancestor has one."""
    if not folder:
        return ""
    d = (ROOT / folder).resolve()
    root = ROOT.resolve()
    cur = d
    while root in cur.parents or cur == root:
        if (cur / "context.txt").exists():
            return cur.name
        if cur == root:
            break
        cur = cur.parent
    return d.name


def _addr_key(o: dict) -> tuple:
    to = ship_to(o)
    addr = to.get("contactAddress") or {}
    return (to.get("fullName", ""), addr.get("addressLine1", ""), addr.get("postalCode", ""))


def render_html(orders: list[dict], drafts: list[dict], ledger: list[dict]) -> str:
    """One or several orders on one page. Several orders are for the case
    eBay merges into a single shipping label (same buyer) — the seller packs
    them as one box, so the pick list should read as one, not N separate
    printouts. Each item still carries its own order id when grouped, since
    that's what ties it back to eBay's merge screen."""
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
            thumb = _thumb_uri(_hero_path(folder)) if folder else None
            pic = f'<img src="{thumb}" alt="">' if thumb else '<div class="noimg">no photo</div>'
            sku_bit = f" &middot; sku {html.escape(row['sku'])}" if row["sku"] else ""
            order_bit = (f" &middot; order {html.escape(o.get('orderId', ''))}"
                         f" &middot; rec #{html.escape(str(o.get('salesRecordReference', '')))}"
                         f" &middot; {_money(li.get('lineItemCost'))}") if grouped else ""
            location = (_pick_location(folder) if folder
                       else "(no local folder — listed by hand)")
            item_blocks.append(f"""
      <div class="item">
        <div class="thumb">{pic}</div>
        <div class="details">
          <div class="qty">&times;{li.get('quantity', 1)}</div>
          <div class="title">{html.escape(li.get('title', ''))}</div>
          <div class="meta">item {html.escape(str(li.get('legacyItemId', '')))}{sku_bit}{order_bit}</div>
          <div class="from">FROM&nbsp; {html.escape(location)}</div>
        </div>
      </div>""")

    addr_lines = "".join(f"<div>{html.escape(line)}</div>" for line in
                          (addr.get("addressLine1"), addr.get("addressLine2")) if line)
    ship_by_bit = (f" &middot; SHIP BY {html.escape(ship_by)}"
                   if ship_by and ship_by != "zz" else "")

    if grouped:
        title = f"Pick — {len(orders)} orders combined"
        heading = (f"PICK — {len(orders)} orders combined &middot; "
                   f"{html.escape(to.get('fullName', ''))}")
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

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  :root {{ --ink: #141210; --red: #a8322b; --grey: #4a443c; }}
  @page {{ size: letter; margin: .5in; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; color: #111;
          max-width: 640px; margin: 24px auto; padding: 0 16px; }}
  /* Two-face system, matching brand/pops-games: Georgia carries display
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

  /* Pop's Games letterhead — same mark/order as the identity block on the
     brand/pops-games thank-you cards (rule, name, tagline, store), reused
     here as a masthead instead of a card. */
  .brand {{ margin-bottom: 14px; }}
  .brand .hr {{ width: 2.6em; height: 2px; background: var(--red); margin-bottom: .4em; }}
  .brand .nm {{ font-size: 1.05rem; letter-spacing: .26em; text-transform: uppercase;
                font-weight: 700; color: var(--ink); }}
  .brand .tg {{ font-size: .68rem; letter-spacing: .2em; color: var(--grey);
                font-family: 'Courier New', monospace; margin-top: .3em; }}
  .brand .st {{ font-size: .68rem; letter-spacing: .04em; color: var(--red);
                font-family: 'Courier New', monospace; margin-top: .15em; }}
  .divider {{ border: none; border-top: 1px solid #ccc; margin: 0 0 14px; }}

  @media print {{ .printbtn {{ display: none; }} .labelbtn {{ display: none; }} body {{ margin: 0; max-width: none; }} }}
</style></head>
<body>
  <div class="printbtn"><button onclick="window.print()">Print</button></div>
  <div class="labelbtn"><a href="https://www.ebay.com/sh/ord/?filter=status:AWAITING_SHIPMENT" target="_blank" rel="noopener">Buy label &rarr;</a></div>
  <div class="brand">
    <div class="hr"></div>
    <div class="nm">POP'S GAMES</div>
    <div class="tg">BUY &middot; SELL &middot; TRADE</div>
    <div class="st">ebay.com/usr/popsgames</div>
  </div>
  <hr class="divider">
  <h1>{heading}</h1>
  <div class="sub">{html.escape(orders[0].get('creationDate', '')[:10])} &middot;
    {html.escape(payment_bit)}
    {ship_by_bit}</div>
  {warn_html}
  {''.join(item_blocks)}
  <div class="shipto">
    <b>SHIP TO</b>
    <div>{html.escape(to.get('fullName', ''))}</div>
    {addr_lines}
    <div>{html.escape(addr.get('city', ''))}, {html.escape(addr.get('stateOrProvince', ''))}
      {html.escape(addr.get('postalCode', ''))} {html.escape(addr.get('countryCode', ''))}</div>
  </div>
  <div class="footer">
    {via_line}<br>
    {footer_line}
  </div>
</body></html>"""


def _default_out_name(orders: list[dict]) -> str:
    if len(orders) == 1:
        return f"pick_{orders[0].get('orderId', 'order')}.html".replace("/", "_")
    username = (orders[0].get("buyer") or {}).get("username")
    stem = username or "+".join(o.get("orderId", "") for o in orders)
    return f"pick_group_{stem}.html".replace("/", "_")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("order_id", nargs="+", metavar="ORDER_ID",
                    help="one order id, or several to combine onto one page — e.g. orders "
                         "eBay will merge into a single shipping label for the same buyer")
    ap.add_argument("--days", type=int, default=30, help="lookback window to find the order(s) (default 30)")
    ap.add_argument("--out", metavar="FILE", help="output path (default pick_lists/pick_<id>.html)")
    args = ap.parse_args()

    candidates = fetch_orders(args.days, verbose=False)
    matches, missing = [], []
    for oid in args.order_id:
        found = next((o for o in candidates if oid in (o.get("orderId", ""), o.get("legacyOrderId", ""))), None)
        (matches if found else missing).append(found or oid)
    if missing:
        print(f"no order(s) {', '.join(missing)} in the last {args.days} days")
        return 1

    drafts, ledger = scan_drafts(), load_listings_ledger()
    out_html = render_html(matches, drafts, ledger)

    out_path = Path(args.out) if args.out else OUT_DIR / _default_out_name(matches)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_html, encoding="utf-8")
    print(f"[OK] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
