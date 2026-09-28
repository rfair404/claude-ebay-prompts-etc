#!/usr/bin/env python3
"""Count every offer and live listing on the account. Read-only.

A before/after invariant for any batch write: the totals must not increase. A
new listing is exactly what an accidental publish looks like, and the only way
to see one is to count them from eBay rather than from our own ledger.

    python tools/count_listings.py [out.json] [--store NAME]

Per store (#156): the count is of ONE account, so `--store` picks it and the
default output is per store (.listing_count.json / .listing_count-<store>.json)
— a before/after pair taken against two different accounts is meaningless.
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "lib"))
import list_edit as L                                        # noqa: E402
import stores                                                # noqa: E402


def snapshot(creds):
    offers, listings, skus = [], set(), set()
    limit, offset = 100, 0
    while True:
        d = L.api_send("GET", f"/sell/inventory/v1/inventory_item?limit={limit}&offset={offset}",
                       creds=creds)
        items = d.get("inventoryItems") or []
        for it in items:
            skus.add(it.get("sku"))
        if len(items) < limit:
            break
        offset += limit
    for sku in sorted(s for s in skus if s):
        try:
            st = L.offer_sellable_state(sku, creds)
        except Exception:
            continue
        if st["offer_id"]:
            offers.append((sku, st["offer_id"], st["status"], st["listing_id"]))
        if st["listing_id"]:
            listings.add(st["listing_id"])
    return dict(skus=len(skus), offers=len(offers), listings=len(listings),
                listing_ids=sorted(listings), offer_rows=offers)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", default=None)
    stores.add_store_args(ap)
    a = ap.parse_args()
    store = stores.resolve_store_name(a.store)
    if not stores.is_default(store):
        print(f"store: {store}")
    out = (Path(a.out) if a.out else Path(".listing_count.json")
           if stores.is_default(store) else stores.store_file(".listing_count.json", store))
    snap = snapshot(L.load_credentials(store=store))
    out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    print(f"SKUs={snap['skus']}  offers={snap['offers']}  distinct listing ids={snap['listings']}")
    print(f"wrote {out}")
