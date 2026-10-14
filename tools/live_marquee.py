#!/usr/bin/env python3
"""LIVE MARQUEE — pick a show's items and render its 1050x1575 promo image.

  --pick   sweeps the seller's live listings in one category (Browse API,
           read-only), keeps the oldest that pass the filters, downloads their
           first photos, and writes the item list into marquee.json.
  (always) renders marquee.html and marquee.png from marquee.json.

Everything lands in inventory/_live/<show>/, next to tools/live_lineup.py's
lineup. marquee.json is the place to fix labels, swap heroes or change copy, and
per item to pick another of the listing's photos ("photo_url") or tighten
one ("focus", "zoom"); then run again without --pick to re-render. --pick
rebuilds only the item list and keeps those edits for items still on it.
A photo is downloaded again when the listing's photo URL changes.

    python tools/live_marquee.py --show jewelry-oct-14 --pick --limit 25 \\
        --min-price 25 --max-price 100 --skip-seasonal \\
        --exclude 206418132464 --include 206418132358 --start "2026-10-14 20:00"
    python tools/live_marquee.py --show jewelry-oct-14          # re-render

--start is Eastern unless it carries an offset. The tool prints the same
moment in GMT, which is what eBay's show scheduler asked for (2026-10-13).

It never changes a listing. The marquee advertises $1 starts and free
shipping; making the listings match is a separate step.

The PNG needs a Chromium-family browser on PATH (chromium, google-chrome);
without one the HTML is still written. Run from the main checkout, or set
EBAYBIZ_CONFIG, so the store's credentials resolve.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import live_marquee as LM                                              # noqa: E402
import live_show as LS                                                 # noqa: E402
import stores                                                          # noqa: E402

BROWSE_SEARCH = "/buy/browse/v1/item_summary/search"
BROWSE_ITEM = "/buy/browse/v1/item/get_item_by_legacy_id"
JEWELRY_AND_WATCHES = "281"
IMG_DIR = "marquee_img"
BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


def sweep(seller: str, category: str, api_get) -> list[dict]:
    """Every live listing of `seller` in `category`, as LM.summary_record()s."""
    out, off = [], 0
    while True:
        r = api_get(BROWSE_SEARCH, query={"category_ids": category,
                                          "filter": f"sellers:{{{seller}}}",
                                          "limit": 200, "offset": off})
        items = r.get("itemSummaries") or []
        out += [LM.summary_record(it) for it in items]
        off += len(items)
        if not items or off >= int(r.get("total") or 0):
            return out


def fetch_one(item_id: str, api_get) -> dict:
    return LM.summary_record(api_get(BROWSE_ITEM, query={"legacy_item_id": item_id}))


def download_photos(items: list[dict], dest: Path) -> tuple[list[str], list[str]]:
    """Each item's photo at 800px: its "photo_url" if set, else its first. A photo already on disk is kept
    only while the listing's photo URL is the one it came from (sources.json):
    a re-shot listing gets a new URL, and the marquee should follow it.
    Returns (ids fetched, ids with no photo URL)."""
    dest.mkdir(parents=True, exist_ok=True)
    src_path = dest / "sources.json"
    try:
        sources = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sources = {}
    fetched, missing = [], []
    for it in items:
        iid, url = it["item_id"], it.get("photo_url") or it.get("image")
        path = dest / f"{iid}.jpg"
        if not url:
            if not path.exists():
                missing.append(iid)
            continue
        if path.exists() and sources.get(iid) == url:
            continue
        big = re.sub(r"s-l\d+\.(jpg|jpeg|webp|png)", "s-l800.jpg", url)
        with urllib.request.urlopen(big, timeout=30) as resp:
            path.write_bytes(resp.read())
        sources[iid] = url
        fetched.append(iid)
    LS._atomic_write(src_path, json.dumps(sources, indent=1) + "\n")
    return fetched, missing


def render_png(html_path: Path, png_path: Path) -> bool:
    exe = next((shutil.which(b) for b in BROWSERS if shutil.which(b)), None)
    if not exe:
        return False
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                        f"--user-data-dir={profile}", "--force-device-scale-factor=1",
                        f"--window-size={LM.WIDTH},{LM.HEIGHT}",
                        f"--screenshot={png_path}", html_path.resolve().as_uri()],
                       check=True, capture_output=True, timeout=120)
    return png_path.exists()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    stores.add_store_args(ap)
    ap.add_argument("--show", required=True, help="show name (folder under inventory/_live/)")
    ap.add_argument("--pick", action="store_true",
                    help="re-query eBay and rebuild the item list")
    ap.add_argument("--seller", default="popsgames",
                    help="eBay username to sweep (default popsgames)")
    ap.add_argument("--category", default=JEWELRY_AND_WATCHES,
                    help="category id to sweep (default 281, Jewelry & Watches)")
    ap.add_argument("--limit", type=int, default=25, help="items on the marquee (default 25)")
    ap.add_argument("--min-price", type=float, help="skip listings priced under this")
    ap.add_argument("--max-price", type=float, help="skip listings priced over this")
    ap.add_argument("--skip-seasonal", action="store_true",
                    help="skip Christmas/holiday pieces")
    ap.add_argument("--exclude", nargs="+", default=[], metavar="ITEM_ID")
    ap.add_argument("--include", nargs="+", default=[], metavar="ITEM_ID",
                    help="listings outside --category to consider too (still filtered by price)")
    ap.add_argument("--start", help='show start, e.g. "2026-10-14 20:00" (Eastern)')
    args = ap.parse_args(argv)

    try:
        d = LS.show_dir(args.show)
    except ValueError as exc:
        raise SystemExit(f"[X] {exc}")
    spec_path = d / "marquee.json"
    spec = (json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists()
            else LM.default_spec(args.show))

    if args.start:
        spec["start"] = LM.parse_start(args.start).isoformat()

    if args.pick:
        from ebay_client import api_get, load_credentials
        creds = load_credentials(store=stores.resolve_store_name(args.store))
        get = lambda path, **k: api_get(path, creds=creds, marketplace="EBAY_US", **k)  # noqa: E731
        records = sweep(args.seller, args.category, get)
        seen = {r["item_id"] for r in records}
        records += [fetch_one(i, get) for i in args.include if i not in seen]
        picked, dropped = LM.select_items(
            records, limit=args.limit, min_price=args.min_price, max_price=args.max_price,
            skip_seasonal=args.skip_seasonal, exclude=frozenset(args.exclude),
            include=frozenset(args.include))
        LM.set_items(spec, picked)
        print(f"  {len(records)} live listings swept; dropped "
              + ", ".join(f"{n} {why}" for why, n in dropped.items() if n))
        for n, it in enumerate(picked, 1):
            hero = "*" if it["item_id"] in spec["heroes"] else " "
            print(f"  {n:>2}{hero} {it['created'][:10]}  ${it['price']:>6.2f}  "
                  f"{it['item_id']}  {it['title'][:60]}")

    if not spec.get("items"):
        raise SystemExit("[X] marquee.json has no items yet — run with --pick")
    for problem in LM.check_spec(spec):
        print(f"[!] {problem}")

    LS._atomic_write(spec_path, json.dumps(spec, indent=1) + "\n")
    fetched, missing = download_photos(spec["items"], d / IMG_DIR)
    if fetched:
        print(f"  fetched {len(fetched)} photo(s): {' '.join(fetched)}")
    for iid in missing:
        print(f"[!] {iid} has no photo URL")
    html_path, png_path = d / "marquee.html", d / "marquee.png"
    LS._atomic_write(html_path, LM.render(spec, IMG_DIR))

    print(f"\nSpec:  {spec_path.resolve()}")
    if render_png(html_path, png_path):
        print(f"Image: {png_path.resolve()}")
    else:
        print(f"[!] no Chromium on PATH — open {html_path.resolve()} and screenshot "
              f"it at {LM.WIDTH}x{LM.HEIGHT}")
    if spec.get("start"):
        when = LM.when_lines(LM.parse_start(spec["start"]))
        print(f"Start: {when['footer']}  =  {when['gmt']} (eBay's show form)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
