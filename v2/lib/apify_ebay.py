"""
Apify eBay sold-listings client.

Production replacement for the Claude-in-Chrome browser navigation
path used today by PRICE's Source B (direct eBay sold-listings browse).

Calls the caffein.dev/ebay-sold-listings Apify Actor (or any
schema-compatible alternative configured via apify.ebay_actor) to
retrieve sold-listing comps. Returns clean structured CompRecord
objects ready for PRICE's classification logic.

Actor schema reference (caffein.dev/ebay-sold-listings):
    Input: keywords[], count, daysToScrape, sortOrder, ebaySite,
           minPrice, maxPrice, itemLocation, itemCondition,
           categoryId, subcategoryId
    Output: itemId, url, title, condition, conditionId, endedAt,
            soldPrice (string), soldCurrency, shippingPrice (string),
            shippingCurrency, shippingType, totalPrice (string),
            sellerUsername, sellerPositivePercent, sellerFeedbackScore,
            scrapedAt
    Docs: https://apify.com/caffein.dev/ebay-sold-listings

Configuration:
    API token + Actor selection loaded via `config.py` from
    ~/.ebaybiz/config.yaml (or %APPDATA%\\ebaybiz\\config.yaml on
    Windows). Environment variables (APIFY_API_TOKEN, APIFY_EBAY_ACTOR)
    override the config file if set. See config.example.yaml.

Programmatic usage:
    from apify_ebay import search_ebay_sold
    comps = search_ebay_sold("vintage polo ralph lauren on safari catalog")
    for c in comps:
        print(c.title, c.sold_price, c.url)

CLI usage (manual testing):
    python apify_ebay.py "vintage polo ralph lauren on safari catalog"
    python apify_ebay.py "..." --count 50 --days 90 --json
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional, Union

try:
    from apify_client import ApifyClient
except ImportError as e:
    raise ImportError(
        "apify-client is required. Install with: pip install apify-client"
    ) from e

from config import ConfigError, get_apify_actor, get_apify_token


# ---------------------------------------------------------------------------
# Defaults (matched to PRICE's usage patterns)
# ---------------------------------------------------------------------------

DEFAULT_COUNT = 30                 # max results per keyword
DEFAULT_DAYS_TO_SCRAPE = 90        # PRICE's anchor window
DEFAULT_SORT_ORDER = "pricePlusPostageHighest"  # matches our _sop=3 convention
DEFAULT_EBAY_SITE = "ebay.com"
DEFAULT_ITEM_CONDITION = "any"
DEFAULT_ITEM_LOCATION = "default"
DEFAULT_RUN_TIMEOUT_SEC = 180

# Allowed values per the caffein.dev Actor docs
VALID_SORT_ORDERS = {
    "endedRecently",
    "timeNewlyListed",
    "pricePlusPostageLowest",
    "pricePlusPostageHighest",
    "distanceNearest",
}
VALID_ITEM_CONDITIONS = {"any", "new", "used"}
VALID_ITEM_LOCATIONS = {"default", "domestic", "worldwide"}
VALID_EBAY_SITES = {
    "ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr",
    "ebay.it", "ebay.es", "ebay.ca", "ebay.com.au",
}

MAX_KEYWORDS = 6  # Actor accepts up to 6 keywords per run


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CompRecord:
    """One sold-listing comp record from eBay.

    Field shape designed for PRICE's classifier. Fields not present in
    the Actor output are set to None; bo_accepted is always False for
    the caffein.dev/ebay-sold-listings Actor (it doesn't expose the
    Best Offer Accepted flag).
    """
    title: str
    sold_price: float           # USD-equivalent, parsed from soldPrice string
    url: str                    # direct link to the eBay listing
    sold_date: Optional[str] = None       # ISO 8601 from endedAt
    condition: Optional[str] = None       # localized eBay condition label
    condition_id: Optional[int] = None    # numeric eBay condition ID (1000, 3000, ...)
    seller_username: Optional[str] = None
    seller_feedback_score: Optional[int] = None
    seller_feedback_pct: Optional[float] = None
    shipping_cost: Optional[float] = None
    shipping_type: Optional[str] = None   # free / paid / pickup / unknown
    sold_currency: Optional[str] = None
    total_price: Optional[float] = None
    item_id: Optional[str] = None
    keyword_tag: Optional[str] = None     # which input keyword this matched
    bo_accepted: bool = False             # NOT exposed by caffein.dev actor — always False
    raw: dict = field(default_factory=dict, repr=False)


class ApifyError(RuntimeError):
    """Raised when the Apify run fails, times out, or returns no usable data."""


# ---------------------------------------------------------------------------
# URL construction (debug / display helper — NOT used by the Actor)
# ---------------------------------------------------------------------------

def build_sold_search_url(query: str, sort_highest_price: bool = True) -> str:
    """Build the equivalent eBay sold-listings search URL for a query.

    NOT passed to the Apify Actor (which builds its own search from
    keywords + filters). Kept as a utility for "show the user what
    eBay search this maps to" output in human-readable reports.
    """
    params = {
        "_nkw": query,
        "LH_Sold": "1",
        "LH_Complete": "1",
    }
    if sort_highest_price:
        params["_sop"] = "3"
    return "https://www.ebay.com/sch/i.html?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# Apify client
# ---------------------------------------------------------------------------

def _client() -> ApifyClient:
    try:
        token = get_apify_token()
    except ConfigError as e:
        raise ApifyError(str(e)) from e
    return ApifyClient(token)


# ---------------------------------------------------------------------------
# Value parsing (defensive — actor returns strings for prices)
# ---------------------------------------------------------------------------

def _parse_price(raw: Any) -> Optional[float]:
    """Extract a float price from a string / number / dict.

    The caffein.dev actor returns prices as strings ("215", "6.20")
    or null. This helper also handles dicts and other formats for
    portability across Actor variants.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        for key in ("value", "amount", "soldPrice", "currentPrice", "price"):
            if key in raw:
                parsed = _parse_price(raw[key])
                if parsed is not None:
                    return parsed
        return None
    if isinstance(raw, str):
        s = raw.replace("US", "").replace("$", "").replace(",", "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)", s)
        if match:
            return float(match.group(1))
    return None


def _parse_int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _parse_float(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Output mapping (caffein.dev/ebay-sold-listings schema)
# ---------------------------------------------------------------------------

def _map_to_comp_record(item: dict) -> Optional[CompRecord]:
    """Convert a raw Apify item dict to a CompRecord, or None if unusable."""
    title = item.get("title")
    if not title:
        return None

    sold_price = _parse_price(item.get("soldPrice"))
    if sold_price is None:
        return None

    url = item.get("url")
    if not url:
        return None

    return CompRecord(
        title=str(title),
        sold_price=sold_price,
        url=str(url),
        sold_date=item.get("endedAt"),
        condition=item.get("condition"),
        condition_id=_parse_int(item.get("conditionId")),
        seller_username=item.get("sellerUsername"),
        seller_feedback_score=_parse_int(item.get("sellerFeedbackScore")),
        seller_feedback_pct=_parse_float(item.get("sellerPositivePercent")),
        shipping_cost=_parse_price(item.get("shippingPrice")),
        shipping_type=item.get("shippingType"),
        sold_currency=item.get("soldCurrency"),
        total_price=_parse_price(item.get("totalPrice")),
        item_id=item.get("itemId"),
        keyword_tag=item.get("keyword"),  # may be set by the actor to tag multi-keyword runs
        bo_accepted=False,                # not exposed by this Actor
        raw=item,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def search_ebay_sold(
    query: Union[str, list[str]],
    *,
    count: int = DEFAULT_COUNT,
    days_to_scrape: int = DEFAULT_DAYS_TO_SCRAPE,
    sort_order: str = DEFAULT_SORT_ORDER,
    ebay_site: str = DEFAULT_EBAY_SITE,
    item_condition: str = DEFAULT_ITEM_CONDITION,
    item_location: str = DEFAULT_ITEM_LOCATION,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    category_id: Optional[str] = None,
    subcategory_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC,
) -> list[CompRecord]:
    """Run the caffein.dev/ebay-sold-listings Apify Actor and return comps.

    Args:
        query: a single search keyword string OR a list of up to 6
               keywords (each runs as a separate search; results are
               tagged with the keyword that matched them via the
               `keyword_tag` field on the returned CompRecord).
        count: max results PER KEYWORD (Actor default is 100; we
               default to 30 to match PRICE's typical comp-list size).
        days_to_scrape: how many days back to search (1–90).
        sort_order: one of `endedRecently`, `timeNewlyListed`,
                    `pricePlusPostageLowest`, `pricePlusPostageHighest`,
                    `distanceNearest`. Default `pricePlusPostageHighest`
                    matches PRICE's _sop=3 convention.
        ebay_site: e.g. `ebay.com`, `ebay.co.uk`, `ebay.de`, ...
        item_condition: one of `any`, `new`, `used`.
        item_location: one of `default`, `domestic`, `worldwide`.
        min_price, max_price: optional price range filters.
        category_id, subcategory_id: optional eBay category filters.
        actor_id: override the Apify Actor ID
                  (default: from config; built-in fallback caffein.dev/ebay-sold-listings).
        timeout_sec: max time to wait for the Apify run.

    Returns:
        List of CompRecord objects, ordered as Apify returns them.

    Raises:
        ApifyError: on auth failure, run failure, timeout, or invalid
                    config. ValueError on invalid argument values.
    """
    # Normalize and validate keywords
    keywords = [query] if isinstance(query, str) else list(query)
    if not keywords or not all(isinstance(k, str) and k.strip() for k in keywords):
        raise ValueError("query must be a non-empty string or list of non-empty strings")
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError(
            f"caffein.dev/ebay-sold-listings accepts up to {MAX_KEYWORDS} keywords; "
            f"got {len(keywords)}"
        )

    # Validate enums
    if sort_order not in VALID_SORT_ORDERS:
        raise ValueError(
            f"sort_order must be one of {sorted(VALID_SORT_ORDERS)}; got {sort_order!r}"
        )
    if item_condition not in VALID_ITEM_CONDITIONS:
        raise ValueError(
            f"item_condition must be one of {sorted(VALID_ITEM_CONDITIONS)}; "
            f"got {item_condition!r}"
        )
    if item_location not in VALID_ITEM_LOCATIONS:
        raise ValueError(
            f"item_location must be one of {sorted(VALID_ITEM_LOCATIONS)}; "
            f"got {item_location!r}"
        )
    if ebay_site not in VALID_EBAY_SITES:
        raise ValueError(
            f"ebay_site must be one of {sorted(VALID_EBAY_SITES)}; got {ebay_site!r}"
        )
    if not (1 <= days_to_scrape <= 90):
        raise ValueError(f"days_to_scrape must be 1–90; got {days_to_scrape}")
    if count < 1:
        raise ValueError(f"count must be >= 1; got {count}")

    actor = actor_id or get_apify_actor()

    run_input: dict[str, Any] = {
        "keywords": keywords,
        "count": count,
        "daysToScrape": days_to_scrape,
        "sortOrder": sort_order,
        "ebaySite": ebay_site,
        "itemCondition": item_condition,
        "itemLocation": item_location,
    }
    if min_price is not None:
        run_input["minPrice"] = min_price
    if max_price is not None:
        run_input["maxPrice"] = max_price
    if category_id is not None:
        run_input["categoryId"] = category_id
    if subcategory_id is not None:
        run_input["subcategoryId"] = subcategory_id

    client = _client()

    try:
        run = client.actor(actor).call(
            run_input=run_input,
            run_timeout=timedelta(seconds=timeout_sec),
        )
    except Exception as e:
        raise ApifyError(f"Apify run failed: {e}") from e

    if run is None:
        raise ApifyError("Apify .call() returned no run object")

    # The SDK can return either a Pydantic Run model (newer versions) or
    # a plain dict (older versions). Normalize to a dict.
    if hasattr(run, "model_dump"):
        run_dict: dict = run.model_dump()
    elif isinstance(run, dict):
        run_dict = run
    else:
        # Fallback: try attribute access on a duck-typed object
        run_dict = {
            "status": getattr(run, "status", None),
            "id": getattr(run, "id", None),
            "defaultDatasetId": (
                getattr(run, "default_dataset_id", None)
                or getattr(run, "defaultDatasetId", None)
            ),
        }

    status = run_dict.get("status")
    if status != "SUCCEEDED":
        raise ApifyError(
            f"Apify run did not succeed (status={status}, run id={run_dict.get('id')})"
        )

    # Pydantic models from apify-client use snake_case attribute names but
    # the JSON-compatible model_dump() preserves the original casing... or
    # not, depending on SDK version. Try both.
    dataset_id = (
        run_dict.get("defaultDatasetId")
        or run_dict.get("default_dataset_id")
    )
    if not dataset_id:
        raise ApifyError("Apify run has no defaultDatasetId")

    raw_items = list(client.dataset(dataset_id).iterate_items())

    comps: list[CompRecord] = []
    for item in raw_items:
        comp = _map_to_comp_record(item)
        if comp is not None:
            comps.append(comp)

    return comps


# ---------------------------------------------------------------------------
# CLI entry point (for manual testing)
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Apify eBay sold-listings search (caffein.dev/ebay-sold-listings)"
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="One or more search keywords (each runs as a separate search; up to 6)",
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Max results per keyword (default: {DEFAULT_COUNT})")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS_TO_SCRAPE,
                        help=f"Days back to search 1-90 (default: {DEFAULT_DAYS_TO_SCRAPE})")
    parser.add_argument("--sort", default=DEFAULT_SORT_ORDER,
                        choices=sorted(VALID_SORT_ORDERS),
                        help=f"Sort order (default: {DEFAULT_SORT_ORDER})")
    parser.add_argument("--site", default=DEFAULT_EBAY_SITE,
                        choices=sorted(VALID_EBAY_SITES),
                        help=f"eBay marketplace (default: {DEFAULT_EBAY_SITE})")
    parser.add_argument("--condition", default=DEFAULT_ITEM_CONDITION,
                        choices=sorted(VALID_ITEM_CONDITIONS),
                        help=f"Item condition filter (default: {DEFAULT_ITEM_CONDITION})")
    parser.add_argument("--location", default=DEFAULT_ITEM_LOCATION,
                        choices=sorted(VALID_ITEM_LOCATIONS),
                        help=f"Item location filter (default: {DEFAULT_ITEM_LOCATION})")
    parser.add_argument("--min-price", type=float, help="Minimum price filter")
    parser.add_argument("--max-price", type=float, help="Maximum price filter")
    parser.add_argument("--category", help="Category ID filter (default: all)")
    parser.add_argument("--subcategory", help="Subcategory ID filter (overrides --category)")
    parser.add_argument("--actor", help="Override the Apify Actor ID")
    parser.add_argument("--timeout", type=int, default=DEFAULT_RUN_TIMEOUT_SEC,
                        help=f"Max seconds for the Apify run (default: {DEFAULT_RUN_TIMEOUT_SEC})")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of human-readable list")
    args = parser.parse_args()

    try:
        comps = search_ebay_sold(
            args.query if len(args.query) > 1 else args.query[0],
            count=args.count,
            days_to_scrape=args.days,
            sort_order=args.sort,
            ebay_site=args.site,
            item_condition=args.condition,
            item_location=args.location,
            min_price=args.min_price,
            max_price=args.max_price,
            category_id=args.category,
            subcategory_id=args.subcategory,
            actor_id=args.actor,
            timeout_sec=args.timeout,
        )
    except (ApifyError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(
            [
                {
                    "title": c.title,
                    "sold_price": c.sold_price,
                    "sold_currency": c.sold_currency,
                    "sold_date": c.sold_date,
                    "condition": c.condition,
                    "condition_id": c.condition_id,
                    "url": c.url,
                    "item_id": c.item_id,
                    "seller_username": c.seller_username,
                    "seller_feedback_score": c.seller_feedback_score,
                    "seller_feedback_pct": c.seller_feedback_pct,
                    "shipping_cost": c.shipping_cost,
                    "shipping_type": c.shipping_type,
                    "total_price": c.total_price,
                    "keyword_tag": c.keyword_tag,
                }
                for c in comps
            ],
            indent=2,
        ))
        return

    query_display = " | ".join(args.query)
    print(f"Found {len(comps)} comp(s) for: {query_display}")
    print(f"Equivalent eBay search: {build_sold_search_url(args.query[0])}")
    print()
    for i, c in enumerate(comps, 1):
        tag = f" [{c.keyword_tag}]" if c.keyword_tag and len(args.query) > 1 else ""
        currency = c.sold_currency or "USD"
        print(f"[{i:2d}] {currency} {c.sold_price:.2f}{tag} - {c.title}")
        if c.sold_date:
            print(f"     Sold: {c.sold_date}")
        if c.condition:
            cond_id = f" ({c.condition_id})" if c.condition_id else ""
            print(f"     Condition: {c.condition}{cond_id}")
        if c.seller_username:
            fb_parts = []
            if c.seller_feedback_score is not None:
                fb_parts.append(f"feedback {c.seller_feedback_score}")
            if c.seller_feedback_pct is not None:
                fb_parts.append(f"{c.seller_feedback_pct}% positive")
            fb = f" ({', '.join(fb_parts)})" if fb_parts else ""
            print(f"     Seller: {c.seller_username}{fb}")
        if c.shipping_cost is not None or c.shipping_type:
            ship_parts = []
            if c.shipping_cost is not None:
                ship_parts.append(f"+${c.shipping_cost:.2f}")
            if c.shipping_type:
                ship_parts.append(f"({c.shipping_type})")
            print(f"     Shipping: {' '.join(ship_parts)}")
        print(f"     URL: {c.url}")
        print()


if __name__ == "__main__":
    _cli()
