"""ebaybiz — shared comp record + persistence (backend-neutral).

Extracted from `apify_ebay.py` on 2026-08-15 when Stage B moved off Apify to
the logged-in browser. Everything here is about WHAT a comp is and HOW it is
stored; nothing here knows or cares where the comp came from.

`price_stats.py`, `comps_csv.py`, `seller_intel.py` and `ebay_visual.py` all
read the JSON written by `save_run_json()`. That format is UNCHANGED from the
Apify era on purpose, so every downstream consumer kept working when the
backend was swapped.

File naming stays `apify_run_<ts>_<id>.json` for the same reason: 293 existing
run files and the glob patterns that find them predate the switch. The name is
historical, not a claim about the source — the `source` field says where a run
actually came from.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

# --- USD sanity check ------------------------------------------------------
# Genuine USD eBay prices cluster hard on charm endings (.99/.95/.00/.50).
# A run whose prices DON'T cluster there is usually FX-converted from another
# currency and mislabeled as USD. Kept from the Apify era: a browser session
# pinned to ebay.com is far less likely to leak, but the check is free.
_CHARM_ENDINGS = (99, 95, 0, 50, 98, 97, 49, 89, 79, 25, 75)
_CHARM_LEAK_THRESHOLD = 0.30
_MIN_SAMPLES_FOR_LEAK_CHECK = 6


@dataclass
class CompRecord:
    """One sold-listing comp.

    Fields a given backend can't supply are left None. The browser backend
    fills every field below EXCEPT `condition`/`condition_id` (eBay's card
    layout doesn't surface condition on sold results).
    """
    title: str
    sold_price: float                     # USD
    url: str                              # direct link to the eBay listing
    sold_date: Optional[str] = None       # ISO 8601
    condition: Optional[str] = None
    condition_id: Optional[int] = None
    seller_username: Optional[str] = None
    seller_feedback_score: Optional[int] = None
    seller_feedback_pct: Optional[float] = None
    shipping_cost: Optional[float] = None
    shipping_type: Optional[str] = None   # "free" when shipping is free
    sold_currency: Optional[str] = "USD"
    total_price: Optional[float] = None   # sold_price + shipping_cost
    item_id: Optional[str] = None
    listing_type: Optional[str] = None
    bids_count: Optional[int] = None
    thumbnail: Optional[str] = None
    keyword_tag: Optional[str] = None     # which query this matched
    bo_accepted: bool = False             # sold via an ACCEPTED Best Offer
    raw: dict = field(default_factory=dict, repr=False)


def parse_feedback_count(raw: Any) -> Optional[int]:
    """'1.8K' -> 1800, '5.1K' -> 5100, '532' -> 532, '1.2M' -> 1200000."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*([KM]?)$", s, re.I)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    suffix = m.group(2).upper()
    if suffix == "K":
        val *= 1_000
    elif suffix == "M":
        val *= 1_000_000
    return int(round(val))


def parse_money(raw: Any) -> Optional[float]:
    """First $-amount in a string -> float. '$39.96$49.95' -> 39.96."""
    if raw is None:
        return None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", str(raw).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_sold_date(raw: Any) -> Optional[str]:
    """'Jun 10, 2026' / 'Sold Jun 10, 2026' -> '2026-06-10'."""
    if not raw:
        return None
    s = re.sub(r"^\s*Sold\s+", "", str(raw)).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def charm_share(prices: list[float]) -> Optional[float]:
    """Fraction of prices ending on a USD charm value."""
    vals = [p for p in prices if p]
    if not vals:
        return None
    hits = sum(1 for p in vals if int(round((p - int(p)) * 100)) in _CHARM_ENDINGS)
    return hits / len(vals)


def comp_to_dict(c: CompRecord) -> dict:
    """Serialized shape consumed by price_stats / comps_csv / ebay_visual."""
    return {
        "title": c.title, "sold_price": c.sold_price, "sold_currency": c.sold_currency,
        "sold_date": c.sold_date, "condition": c.condition, "listing_type": c.listing_type,
        "bids_count": c.bids_count, "shipping_cost": c.shipping_cost,
        "total_price": c.total_price, "seller_username": c.seller_username,
        "seller_feedback_score": c.seller_feedback_score,
        "seller_feedback_pct": c.seller_feedback_pct,
        "item_id": c.item_id, "thumbnail": c.thumbnail,
        "keyword_tag": c.keyword_tag, "url": c.url,
        "bo_accepted": c.bo_accepted,
    }


def default_runs_dir() -> Path:
    """Default save location: $COMPS_RUNS_DIR / $APIFY_RUNS_DIR / <repo>/apify_runs."""
    env = os.environ.get("COMPS_RUNS_DIR") or os.environ.get("APIFY_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "apify_runs"


def save_run_json(run_id: str, source: str, queries: list[str],
                  raw_items: list[dict], comps: list[CompRecord],
                  save_dir: Optional[Union[str, Path]] = None) -> str:
    """Persist one comp pull to JSON; return the path.

    `source` records the backend ("browser:ebay-sold(price_high)", or an Apify
    actor id historically). Written as BOTH `source` and `actor` so older
    readers that look for `actor` keep working.
    """
    base = Path(save_dir) if save_dir else default_runs_dir()
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = base / f"apify_run_{ts}_{run_id}.json"
    prices = [c.sold_price for c in comps if c.sold_price]
    charm = charm_share(prices) if prices else None
    payload = {
        "run_id": run_id,
        "source": source,
        "actor": source,           # back-compat alias for pre-2026-08-15 readers
        "queries": queries,
        "saved_at_utc": ts,
        "n_comps": len(comps),
        "charm_price_share": round(charm, 3) if charm is not None else None,
        "currency_leak_suspected": bool(
            len(prices) >= _MIN_SAMPLES_FOR_LEAK_CHECK
            and charm is not None and charm < _CHARM_LEAK_THRESHOLD
        ),
        "comps": [comp_to_dict(c) for c in comps],
        "raw_items": raw_items,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)
