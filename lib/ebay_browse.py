#!/usr/bin/env python3
"""eBay **Browse API** reader — other sellers' ACTIVE listings, direct via API.

Uses the app-context OAuth token from ebay_client.py (app_id + cert_id, no user
consent) to call the Buy > Browse API. Lets you pull any seller's currently
**active** listings — title, asking price, condition, image, URL — without
Chrome or Apify.

  IMPORTANT — what this can and cannot do (verified 2026-06-22, production):
  • ACTIVE listings by seller  -> YES (this module; Browse item_summary/search
    with filter=sellers:{username}).
  • SOLD/completed by seller    -> NO via official API. The only API with sold
    data is Marketplace Insights (buy/marketplace_insights/v1/item_sales/search),
    a Limited-Release API not granted to this keyset (returns HTTP 404). That is
    why realized-price comps come from Apify (lib/apify_ebay.py / ebay_visual.py),
    which is keyword-based and has no seller filter. A specific seller's SOLD
    history is not reachable by API — only their ACTIVE listings are.

So treat prices here as **asking**, not realized. Useful for watching what a
known-good seller currently offers (and how they price/title/condition-grade),
not as sold comps.

CLI:
    python ebay_browse.py seller <username> [--q marble] [--category 233]
                                            [--max 200] [--json out.json]
    python ebay_browse.py search "<keywords>" [--category 233] [--max 100] [--json out.json]

Browse search requires at least a `q` OR `category_ids`; a seller filter alone is
not enough, so `seller` defaults to the Marbles category (233) when no q/category
is given. Output records are normalised and include `listingStatus: "active"`.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebay_client as ec  # noqa: E402

BROWSE_SEARCH = "/buy/browse/v1/item_summary/search"
PAGE_LIMIT = 200            # Browse API max page size
# Browse rejects a seller filter alone (HTTP 400 — needs q OR category_ids). For
# "a seller's whole store" we default to the Toys & Hobbies PARENT category,
# which captures marble/toy subcategories (verified: dusar-8's marbles surface
# under 220, not the narrower 233). Pass --category for sellers outside toys.
DEFAULT_SELLER_CATEGORY = "220"   # Toys & Hobbies (EBAY_US top-level)


def _normalize(it: dict) -> dict:
    pr = it.get("price") or {}
    img = (it.get("image") or {}).get("imageUrl")
    if not img:
        thumbs = it.get("thumbnailImages") or []
        img = thumbs[0].get("imageUrl") if thumbs else None
    val = pr.get("value")
    try:
        val = float(val) if val is not None else None
    except (TypeError, ValueError):
        pass
    seller = it.get("seller") or {}
    return {
        "itemId": it.get("itemId"),
        "title": it.get("title"),
        "askingPrice": val,
        "priceStr": f"{pr.get('value')} {pr.get('currency')}".strip() if pr else None,
        "currency": pr.get("currency"),
        "condition": it.get("condition"),
        "conditionId": it.get("conditionId"),
        "thumbnail": img,                      # named to match ebay_visual ingest
        "url": (it.get("itemWebUrl") or "").split("?")[0] or None,
        "seller": seller.get("username"),
        "sellerFeedbackPct": seller.get("feedbackPercentage"),
        "sellerFeedbackScore": seller.get("feedbackScore"),
        "buyingOptions": it.get("buyingOptions"),
        "listingStatus": "active",             # NB: asking price, not sold
    }


def search(q: str | None = None, *, seller: str | None = None,
           category_ids: str | None = None, max_items: int = 200,
           marketplace: str = "EBAY_US", creds=None) -> list[dict]:
    """Page Browse item_summary/search and return normalised active listings.

    Provide at least one of `q` or `category_ids` (eBay requires it). `seller`
    adds filter=sellers:{seller}.
    """
    if not q and not category_ids:
        raise ValueError("Browse search needs a `q` or `category_ids`.")
    out: list[dict] = []
    offset = 0
    while len(out) < max_items:
        query = {"limit": min(PAGE_LIMIT, max_items - len(out)), "offset": offset}
        if q:
            query["q"] = q
        if category_ids:
            query["category_ids"] = category_ids
        if seller:
            query["filter"] = f"sellers:{{{seller}}}"
        data = ec.api_get(BROWSE_SEARCH, query=query, marketplace=marketplace, creds=creds)
        batch = data.get("itemSummaries") or []
        out.extend(_normalize(it) for it in batch)
        total = data.get("total") or 0
        offset += len(batch)
        if not batch or offset >= total:
            break
    return out[:max_items]


def seller_items(seller: str, *, q: str | None = None,
                 category_ids: str | None = None, max_items: int = 200,
                 creds=None) -> list[dict]:
    """A seller's ACTIVE listings. Defaults to the Toys & Hobbies category if no q/cat."""
    if not q and not category_ids:
        category_ids = DEFAULT_SELLER_CATEGORY
    return search(q=q, seller=seller, category_ids=category_ids,
                  max_items=max_items, creds=creds)


def seller_active(seller: str, *, q: str | None = None,
                  category_ids: str | None = None, sample: int = 200,
                  creds=None) -> tuple[int, list[dict]]:
    """One Browse call: a seller's EXACT active-listing `total` in a category +
    a normalised price sample (up to `sample`). Cheap enough to fan across many
    sellers to map a category's competitive landscape."""
    if not q and not category_ids:
        category_ids = DEFAULT_SELLER_CATEGORY
    query = {"limit": min(PAGE_LIMIT, sample), "filter": f"sellers:{{{seller}}}"}
    if q:
        query["q"] = q
    if category_ids:
        query["category_ids"] = category_ids
    data = ec.api_get(BROWSE_SEARCH, query=query, marketplace="EBAY_US", creds=creds)
    total = int(data.get("total") or 0)
    recs = [_normalize(it) for it in (data.get("itemSummaries") or [])]
    return total, recs


def top_sellers_active(q: str | None = None, *, category_ids: str | None = None,
                       sample: int = 200, top_n: int = 5,
                       marketplace: str = "EBAY_US", creds=None) -> list[dict]:
    """Active listings from the top `top_n` sellers in a category/query sample.

    One `search()` pull (up to `sample` active listings) stands in for "the
    competing stores in this niche" — Browse has no seller-ranking endpoint,
    so listing count within the sample is the proxy for who's moving volume
    here. Returns the normalised listings (each carries `seller` +
    `askingPrice`) belonging to just those top sellers — feed this straight
    into `price_stats.competitor_charm_pattern` (GH #82) to learn the
    charm-pricing convention the sellers actually winning this niche use,
    rather than defaulting to a generic charm price.
    """
    listings = search(q=q, category_ids=category_ids, max_items=sample,
                      marketplace=marketplace, creds=creds)
    counts = Counter(r["seller"] for r in listings if r.get("seller"))
    top = {name for name, _ in counts.most_common(top_n)}
    return [r for r in listings if r.get("seller") in top]


def _print(records: list[dict], header: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"\n{header} — {len(records)} active listing(s):\n")
    for r in records:
        price = f"${r['askingPrice']:.2f}" if isinstance(r.get("askingPrice"), float) else (r.get("priceStr") or "?")
        opt = ",".join(r.get("buyingOptions") or [])
        print(f"  {price:>9}  [{r.get('condition')}/{opt}]  {(r.get('title') or '')[:58]}")
        print(f"     {r.get('url')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seller", help="a seller's ACTIVE listings (asking prices)")
    p.add_argument("username")
    p.add_argument("--q", default=None, help="optional keyword filter")
    p.add_argument("--category", default=None, help="category_ids (default 233 Marbles)")
    p.add_argument("--max", type=int, default=200)
    p.add_argument("--json", default=None)

    p = sub.add_parser("search", help="general Browse keyword search (active listings)")
    p.add_argument("query")
    p.add_argument("--seller", default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--max", type=int, default=100)
    p.add_argument("--json", default=None)

    p = sub.add_parser("top-sellers", help="active listings from the top N sellers in a "
                                           "category/query sample (GH #82 — competitor charm pricing)")
    p.add_argument("query", nargs="?", default=None, help="optional keyword filter")
    p.add_argument("--category", default=None, help="category_ids (required if no query)")
    p.add_argument("--sample", type=int, default=200, help="active listings to sample")
    p.add_argument("--top", type=int, default=5, help="how many top sellers to keep")
    p.add_argument("--json", default=None)

    args = ap.parse_args()
    try:
        if args.cmd == "seller":
            recs = seller_items(args.username, q=args.q, category_ids=args.category, max_items=args.max)
            _print(recs, f"Seller {args.username} (active)")
        elif args.cmd == "top-sellers":
            recs = top_sellers_active(q=args.query, category_ids=args.category,
                                      sample=args.sample, top_n=args.top)
            _print(recs, f"Top {args.top} sellers")
        else:
            recs = search(q=args.query, seller=args.seller, category_ids=args.category, max_items=args.max)
            _print(recs, f"Search {args.query!r}")
        if args.json:
            Path(args.json).write_text(json.dumps(recs, indent=2), encoding="utf-8")
            print(f"\nWrote {len(recs)} records -> {args.json}")
    except (ec.EbayAuthError, ec.EbayAPIError, ValueError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
