#!/usr/bin/env python3
"""Pull LIVE inventory from eBay and reconcile it against the local shoots.

Written after the j-crew gap. The retouch audit worked off a tracker whose queue
had been seeded by hand, and five published j-crew catalogs were never in it —
only one sibling had been added, so the other five sat outside the audit
entirely while carrying drained `studio` renders. The lesson is that the local
tracker cannot tell you what is live; only eBay can. So this starts from eBay's
side and asks what we hold locally for each of them, never the reverse.

For every inventory item on the account it records the offer's status, listing
id, price, category, and the local shoot that owns the SKU (matched from
draft.md), plus the preset that shoot last rendered. Output is a JSON file and
a printed summary by category.

    python tools/inventory_sync.py                    # pull + summarise
    python tools/inventory_sync.py --json out.json
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from ebay_client import (load_credentials, iter_inventory_items,      # noqa: E402
                         get_offers_for_sku, EbayAPIError)

SKU_RE = re.compile(r'ebay_inventory_sku:\s*"?([0-9a-fA-F]{6,})')
PATH_RE = re.compile(r'category_path:\s*"([^"]*)"')

# Printed paper. These are the shoots the asshot rule in prompts/prep.md covers,
# and the class where the crop detector also misbehaves (it locks onto whatever
# is highest-contrast on the page — a redaction box, a logo panel).
MEDIA_WORDS = re.compile(r"book|magazine|catalog|paper|comic|newspaper|"
                         r"periodical|brochure|pamphlet|manual|zine", re.I)


def local_index() -> dict:
    """sku -> {shoot, preset, category_path, photos} from every draft.md on disk."""
    idx = {}
    for dm in (REPO / "inventory").rglob("draft.md"):
        try:
            t = dm.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = SKU_RE.search(t)
        if not m:
            continue
        shoot = dm.parent
        preset = None
        pj = shoot / ".prep" / "prep.json"
        if pj.exists():
            try:
                preset = json.loads(pj.read_text(encoding="utf-8")).get("chosen_preset")
            except (OSError, ValueError):
                pass
        cp = PATH_RE.search(t)
        idx[m.group(1)] = {
            "shoot": str(shoot.relative_to(REPO)).replace("\\", "/"),
            "preset": preset,
            "category_path": cp.group(1) if cp else None,
            "listing_photos": len(list((shoot / "listing").glob("*.jpg")))
                              if (shoot / "listing").exists() else 0,
        }
    return idx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=".inventory_live.json")
    a = ap.parse_args()

    creds = load_credentials()
    loc = local_index()
    print(f"local drafts with a SKU: {len(loc)}")

    rows = []
    items = iter_inventory_items(creds=creds)
    print(f"inventory items on eBay: {len(items)}")
    for n, it in enumerate(items, 1):
        sku = it.get("sku")
        title = str((it.get("product") or {}).get("title") or "")
        nimg = len((it.get("product") or {}).get("imageUrls") or [])
        try:
            offers = get_offers_for_sku(sku, creds=creds)
        except EbayAPIError:
            offers = []
        o = offers[0] if offers else {}
        listing = o.get("listing") or {}
        l = loc.get(sku, {})
        rows.append({
            "sku": sku, "title": title, "live_photos": nimg,
            "offer_id": o.get("offerId"),
            "status": o.get("status"),
            "listing_status": listing.get("listingStatus"),
            "listing_id": listing.get("listingId"),
            "quantity": o.get("availableQuantity"),
            "category_id": o.get("categoryId"),
            "price": ((o.get("pricingSummary") or {}).get("price") or {}).get("value"),
            "shoot": l.get("shoot"), "preset": l.get("preset"),
            "category_path": l.get("category_path"),
            "listing_photos": l.get("listing_photos"),
        })
        if n % 25 == 0:
            print(f"  ...{n}/{len(items)}")
            sys.stdout.flush()

    Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")

    live = [r for r in rows if r["status"] == "PUBLISHED"
            and (r["listing_status"] or "ACTIVE").upper() == "ACTIVE"
            and (r["quantity"] or 0) > 0]

    def is_media(r):
        return bool(MEDIA_WORDS.search((r.get("category_path") or "") + " " + (r.get("title") or "")))

    media = [r for r in live if is_media(r)]

    print(f"\n{'='*66}")
    print(f"inventory items      : {len(rows)}")
    print(f"LIVE (active, qty>0) : {len(live)}")
    print(f"ended / sold / other : {len(rows) - len(live)}")
    print(f"\nLIVE printed media (books/magazines/catalogs/paper): {len(media)}")

    by = Counter((r.get("category_path") or "(no local category_path)") for r in media)
    print(f"\n  {'category path':52} count")
    for k, v in by.most_common():
        print(f"  {k[:52]:52} {v:5}")

    pres = Counter((r.get("preset") or "(none recorded)") for r in media)
    print(f"\n  preset now on those media shoots:")
    for k, v in pres.most_common():
        print(f"    {k:24} {v:5}")

    orphan = [r for r in live if not r.get("shoot")]
    print(f"\nLIVE listings with NO local shoot matched: {len(orphan)}")
    for r in orphan[:15]:
        print(f"    {r['sku']}  {r['title'][:58]}")
    print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
