#!/usr/bin/env python3
"""Count ACTIVE eBay listings matching a search query. Read-only.

Market-wide sizing, not account state: `tools/count_listings.py` counts OUR
offers/listings via Sell Inventory; this counts everybody's via Browse.

Browse's `item_summary/search` returns a `total` field alongside the page of
results, so one call with limit=1 is enough — we throw the item away and keep
the number. Uses lib/ebay_client for auth (app-context client_credentials),
so it honours config.yaml's `ebay.environment` and needs no extra credentials.

    python tools/search_count.py "nintendo switch oled"
    python tools/search_count.py "marbles" --category 220
    python tools/search_count.py "vintage pyrex" --filter "buyingOptions:{FIXED_PRICE}"
    python tools/search_count.py "akro agate" --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
import ebay_client as ec  # noqa: E402

BROWSE_SEARCH = "/buy/browse/v1/item_summary/search"


def count(q: str | None = None, *, category_ids: str | None = None,
          filter_: str | None = None, marketplace: str = "EBAY_US",
          creds=None) -> tuple[int, dict | None]:
    """Return (total_active_listings, first_item_or_None).

    Browse requires at least one of `q` / `category_ids` (400 otherwise) —
    same constraint ebay_browse.search enforces.
    """
    if not q and not category_ids:
        raise ValueError("Browse search needs a `q` or `category_ids`.")
    query: dict[str, str | int] = {"limit": 1}
    if q:
        query["q"] = q
    if category_ids:
        query["category_ids"] = category_ids
    if filter_:
        query["filter"] = filter_
    data = ec.api_get(BROWSE_SEARCH, query=query, marketplace=marketplace, creds=creds)
    items = data.get("itemSummaries") or []
    return int(data.get("total") or 0), (items[0] if items else None)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", nargs="?", default=None, help="keywords")
    p.add_argument("--category", default=None, help="category id(s), e.g. 220")
    p.add_argument("--filter", dest="filter_", default=None,
                   help="raw Browse filter, e.g. 'buyingOptions:{FIXED_PRICE},price:[10..100],priceCurrency:USD'")
    p.add_argument("--marketplace", default="EBAY_US")
    p.add_argument("--json", action="store_true")
    p.add_argument("--sample", action="store_true", help="also show one matching item")
    a = p.parse_args()

    creds = ec.load_credentials()
    total, item = count(a.query, category_ids=a.category, filter_=a.filter_,
                        marketplace=a.marketplace, creds=creds)

    if a.json:
        print(json.dumps({"query": a.query, "category": a.category,
                          "filter": a.filter_, "marketplace": a.marketplace,
                          "environment": creds.environment, "total": total}))
        return

    label = a.query or f"category {a.category}"
    print(f'"{label}" [{a.marketplace} / {creds.environment}] -> {total:,} active listings')
    if a.sample and item:
        pr = item.get("price") or {}
        print(f'  e.g. {item.get("title")} - {pr.get("value")} {pr.get("currency")}')
        print(f'       {(item.get("itemWebUrl") or "").split("?")[0]}')


if __name__ == "__main__":
    main()
