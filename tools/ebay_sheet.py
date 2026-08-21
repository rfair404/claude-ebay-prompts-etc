#!/usr/bin/env python3
"""Build an inventory sheet from the eBay APIs ALONE.

Every earlier view of this inventory was a join between eBay and the local
`inventory/` tree, and the local half could not be trusted: SKUs live in
draft.md files that go stale when a shoot directory is moved (the
frankie-roys-things -> FR rename put six shoots out of reach), drafts carry
null offer ids for listings that are demonstrably live, and one audit queue
silently omitted five published j-crew catalogs because only one sibling had
ever been seeded. Category, too, was being read out of a local `category_path`
string and matched with a word regex, which counted a glass PAPERweight as
printed media.

So this file reads nothing from disk. Three eBay sources, all authoritative:

  * Sell Inventory API, inventory items  — sku, title, condition, aspects,
    quantity, image count
  * Sell Inventory API, offers           — offer id, status, listing id and
    listing status, price, format, categoryId
  * Commerce Taxonomy API, category tree — categoryId -> full category path.
    Fetched once as the whole US tree (~4MB, 17k categories) rather than one
    call per category: the per-category subtree endpoint returns the leaf name
    with no ancestors, so it cannot build a path. NOTE this needs an APPLICATION
    token; with a user token the Taxonomy API answers 403.

`variation_count` is how many SKUs share a listing id. A CHOICE listing is ONE
listing to a buyer and one row in eBay's own seller view, so any count of
"listings" must group on listing_id — counting SKUs reports 161 where the
truthful answer is 139.

    python tools/ebay_sheet.py                 # -> inventory_sheet.csv + .json
"""
from __future__ import annotations
import argparse, csv, json, sys, urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from ebay_client import (load_credentials, iter_inventory_items,          # noqa: E402
                         get_offers_for_sku, get_app_access_token, EbayAPIError)

TREE_CACHE = REPO / ".category_paths.json"

COLUMNS = ["sku", "title", "listing_id", "item_url", "offer_id", "live",
           "offer_status", "listing_status", "quantity", "price", "currency",
           "format", "marketplace", "condition", "category_id", "category_path",
           "category_top", "variation_count", "image_count", "aspect_type",
           "aspect_brand"]


def category_paths(refresh: bool = False) -> dict:
    """categoryId -> 'Books & Magazines > Catalogs' for the whole US tree."""
    if TREE_CACHE.exists() and not refresh:
        d = json.loads(TREE_CACHE.read_text(encoding="utf-8"))
        if d:
            return d
    tok = get_app_access_token(load_credentials())
    req = urllib.request.Request(
        "https://api.ebay.com/commerce/taxonomy/v1/category_tree/0",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
    tree = json.loads(urllib.request.urlopen(req, timeout=300).read())
    paths: dict[str, str] = {}

    def walk(node, trail):
        c = node.get("category", {})
        cid, nm = c.get("categoryId"), c.get("categoryName")
        t = trail + [nm] if nm else trail
        if cid:
            paths[cid] = " > ".join(t[1:]) if len(t) > 1 else (nm or "")
        for ch in node.get("childCategoryTreeNodes") or []:
            walk(ch, t)

    walk(tree["rootCategoryNode"], [])
    TREE_CACHE.write_text(json.dumps(paths), encoding="utf-8")
    return paths


def build() -> list[dict]:
    creds = load_credentials()
    paths = category_paths()
    items = iter_inventory_items(creds=creds)
    print(f"inventory items from eBay: {len(items)}")

    rows = []
    for n, it in enumerate(items, 1):
        sku = it.get("sku")
        prod = it.get("product") or {}
        asp = prod.get("aspects") or {}
        avail = ((it.get("availability") or {}).get("shipToLocationAvailability") or {})
        try:
            offers = get_offers_for_sku(sku, creds=creds)
        except EbayAPIError:
            offers = []
        o = offers[0] if offers else {}
        lst = o.get("listing") or {}
        cid = o.get("categoryId")
        path = paths.get(cid or "", "")
        qty = o.get("availableQuantity")
        qty = int(qty) if qty is not None else avail.get("quantity")
        status = (o.get("status") or "").upper()
        lstatus = (lst.get("listingStatus") or "").upper()
        live = status == "PUBLISHED" and lstatus in ("", "ACTIVE") and (qty or 0) > 0
        lid = lst.get("listingId")
        rows.append({
            "sku": sku,
            "title": prod.get("title") or "",
            "listing_id": lid or "",
            "item_url": f"https://www.ebay.com/itm/{lid}" if lid else "",
            "offer_id": o.get("offerId") or "",
            "live": "yes" if live else "no",
            "offer_status": status or "NO_OFFER",
            "listing_status": lstatus,
            "quantity": qty if qty is not None else "",
            "price": ((o.get("pricingSummary") or {}).get("price") or {}).get("value") or "",
            "currency": ((o.get("pricingSummary") or {}).get("price") or {}).get("currency") or "",
            "format": o.get("format") or "",
            "marketplace": o.get("marketplaceId") or "",
            "condition": it.get("condition") or "",
            "category_id": cid or "",
            "category_path": path,
            "category_top": path.split(" > ")[0] if path else "",
            "image_count": len(prod.get("imageUrls") or []),
            "aspect_type": "; ".join(asp.get("Type") or []),
            "aspect_brand": "; ".join(asp.get("Brand") or []),
        })
        if n % 25 == 0:
            print(f"  ...{n}/{len(items)}"); sys.stdout.flush()

    seen = Counter(r["listing_id"] for r in rows if r["listing_id"])
    for r in rows:
        r["variation_count"] = seen.get(r["listing_id"], 1) if r["listing_id"] else 1
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="inventory_sheet.csv")
    ap.add_argument("--json", default="inventory_sheet.json")
    a = ap.parse_args()

    rows = build()
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")

    live = [r for r in rows if r["live"] == "yes"]
    listings = {r["listing_id"] for r in live if r["listing_id"]}
    print(f"\n{'='*60}")
    print(f"SKUs (inventory items) : {len(rows)}   live: {len(live)}")
    print(f"LIVE LISTINGS          : {len(listings)}  (CHOICE grouped)")
    print(f"\n{'top-level category':40} listings")
    seen_l, top = set(), Counter()
    for r in live:
        if r["listing_id"] in seen_l:
            continue
        seen_l.add(r["listing_id"])
        top[r["category_top"] or "(none)"] += 1
    for k, v in top.most_common():
        print(f"  {k:38} {v:4}")
    print(f"\nwrote {a.csv} and {a.json}")


if __name__ == "__main__":
    main()
