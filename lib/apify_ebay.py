"""
Apify eBay sold-listings client.

PRICE's default Stage B comp source (un-gated direct eBay sold-listings).
The Claude-in-Chrome browse path is the optional Stage C fallback, used
only when confidence is low or this backend is unavailable.

Backend Actor: **automation-lab/ebay-sold-scraper** (configurable via
`apify.ebay_actor`). Chosen because it pins a **US residential proxy**, so
eBay returns prices in USD natively — avoiding the silent foreign-currency
leak that actors without proxy control suffer (e.g. caffein.dev/ebay-sold-
listings returned BRL/CZK prices mislabeled as USD, inflating values ~5x).

Actor schema reference (automation-lab/ebay-sold-scraper):
    Input:  searchQueries[], maxListingsPerSearch, maxSearchPages, sort,
            listingType, condition[], minPrice, maxPrice, maxRequestRetries
    Output: itemId, title, soldPrice (number, USD), soldPriceString,
            soldDate ("May 12, 2026"), condition, listingType, bidsCount,
            shippingCost ("+$108.12 delivery"), sellerName,
            sellerFeedbackPercent ("100%"), sellerFeedbackCount ("2K"),
            thumbnail, url, scrapedAt

Defense in depth: even with a US proxy, every run is checked by a
provider-agnostic **currency-leak validator** (`_check_currency_leak`).
Genuine USD eBay sold prices are overwhelmingly charm-priced
(.99/.95/.00/.50); an FX leak multiplies every price by one rate and
destroys that structure. If the charm-price share collapses, the run is
flagged (and, by default, raises CurrencyLeakError so PRICE falls back to
Stage C instead of anchoring on corrupt data). This does NOT trust the
actor's currency label, which can lie.

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
    python apify_ebay.py "..." --max 50 --sort price_high --json
    python apify_ebay.py "..." --save-dir inventory/my-shoot   # save JSON beside price.txt

Result persistence: every call saves its results to JSON (run metadata,
normalized comps, raw dataset) for audit/cache. Default location is
$APIFY_RUNS_DIR or <repo>/apify_runs/; override with --save-dir (or the
search_ebay_sold(save_dir=...) arg), or disable with --no-save.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

# NOTE: no third-party dependency. The Apify backend is reached over its
# plain HTTPS REST API using only the Python standard library (urllib), so
# this runs in minimal/sandboxed environments where `pip install` is blocked
# (e.g. a Cowork tab whose proxy 403s PyPI). The `apify-client` package is
# NOT required.

from config import ConfigError, get_apify_actor, get_apify_token

APIFY_API_BASE = "https://api.apify.com/v2"


# ---------------------------------------------------------------------------
# Defaults (matched to PRICE's usage patterns) — automation-lab schema
# ---------------------------------------------------------------------------

DEFAULT_MAX_LISTINGS = 30          # maxListingsPerSearch (PRICE comp-list size)
DEFAULT_MAX_PAGES = 3              # maxSearchPages (60 listings/page)
DEFAULT_SORT = "price_high"        # surface the ceiling first (≈ old _sop=3)
DEFAULT_LISTING_TYPE = "all"
DEFAULT_MAX_RETRIES = 5
DEFAULT_RUN_TIMEOUT_SEC = 300

VALID_SORTS = {"best_match", "newly_listed", "price_low", "price_high"}
VALID_LISTING_TYPES = {"all", "auction", "buy_it_now"}

# Short tag per `sort`, used in the run's completion status message shown in
# the Apify Console. PRICE's dual query uses best_match + price_high.
SORT_STATUS_TAG = {
    "best_match": "best",
    "price_high": "sold_highest",
    "price_low": "sold_lowest",
    "newly_listed": "newest",
}

# Records the most recent run's provenance so the CLI (and PRICE's research
# log) can cite concrete proof a query hit the Apify backend: the run id,
# actor, and comp count. Populated by search_ebay_sold() even when the run
# is later flagged for a currency leak.
LAST_RUN: dict = {}

# ---------------------------------------------------------------------------
# Currency-leak validator (provider-agnostic; does NOT trust currency labels)
# ---------------------------------------------------------------------------

# Cents endings that dominate genuine USD eBay prices (charm pricing).
_CHARM_CENTS = {0, 49, 50, 95, 98, 99}
# Need at least this many priced comps to judge the distribution.
_MIN_SAMPLES_FOR_LEAK_CHECK = 5
# Below this charm share, suspect a currency leak (clean US runs are ~0.5+).
_CHARM_LEAK_THRESHOLD = 0.30
# Approximate USD cross-rates of currencies eBay commonly localizes into.
# Used ONLY to diagnose/repair a detected leak (find the divisor that
# restores charm structure). Rates need only be close; the charm check is
# what confirms the match.
_FX_RATES = {
    "BRL": 5.2, "CZK": 23.0, "MXN": 17.0, "INR": 83.0, "ZAR": 18.5,
    "EUR": 0.92, "GBP": 0.79, "CAD": 1.37, "AUD": 1.52, "JPY": 157.0,
    "PHP": 57.0, "PLN": 4.0, "SEK": 10.5,
}


class ApifyError(RuntimeError):
    """Raised when the Apify run fails, times out, or returns no usable data."""


class CurrencyLeakError(ApifyError):
    """Raised when a run's prices look FX-converted (not genuine USD).

    Carries the detected diagnosis so a caller can decide to repair or fall
    back to another source (Stage C / Chrome).
    """

    def __init__(self, message: str, *, charm_share: float,
                 guessed_currency: Optional[str] = None,
                 guessed_factor: Optional[float] = None):
        super().__init__(message)
        self.charm_share = charm_share
        self.guessed_currency = guessed_currency
        self.guessed_factor = guessed_factor


def _charm_share(prices: list[float]) -> float:
    """Fraction of prices whose cents land on a charm-price ending."""
    if not prices:
        return 0.0
    hits = 0
    for p in prices:
        cents = round((p - int(p)) * 100)
        if cents in _CHARM_CENTS:
            hits += 1
    return hits / len(prices)


def _guess_leak(prices: list[float]) -> tuple[Optional[str], Optional[float], float]:
    """Find the FX divisor that best restores charm structure.

    For each candidate currency we refine the divisor within a ±4% band (the
    exact rate eBay localized at drifts from a table value, and charm
    restoration is sensitive to that precision), and keep the global best.

    Returns (currency, factor, repaired_charm_share). currency/factor are
    None if no candidate clearly beats the leaked distribution.
    """
    best_cur, best_factor, best_share = None, None, _charm_share(prices)
    for cur, rate in _FX_RATES.items():
        steps = 81  # ±4% scanned at ~0.1% resolution
        for i in range(steps):
            factor = rate * (0.96 + 0.08 * i / (steps - 1))
            share = _charm_share([round(p / factor, 2) for p in prices])
            if share > best_share:
                best_cur, best_factor, best_share = cur, round(factor, 4), share
    return best_cur, best_factor, best_share


def _check_currency_leak(comps: list["CompRecord"], on_leak: str) -> list["CompRecord"]:
    """Validate (and optionally repair) a run against currency leaks.

    on_leak: "raise" (default) -> CurrencyLeakError; "repair" -> divide by
    the detected FX factor and tag; "ignore" -> return as-is.
    """
    priced = [c for c in comps if c.sold_price and c.sold_price > 0]
    if len(priced) < _MIN_SAMPLES_FOR_LEAK_CHECK:
        return comps  # too few to judge — don't false-positive on thin queries
    share = _charm_share([c.sold_price for c in priced])
    if share >= _CHARM_LEAK_THRESHOLD:
        return comps  # looks like genuine USD

    cur, factor, repaired_share = _guess_leak([c.sold_price for c in priced])
    diag = (f"charm-price share {share:.0%} (<{_CHARM_LEAK_THRESHOLD:.0%}); "
            f"prices look FX-converted, not USD")
    if cur:
        diag += f" — best fit {cur} (÷{factor}, restores charm to {repaired_share:.0%})"

    if on_leak == "ignore":
        return comps
    if on_leak == "repair":
        if cur and factor and repaired_share >= 0.5:
            for c in comps:
                if c.sold_price:
                    c.sold_price = round(c.sold_price / factor, 2)
                if c.shipping_cost:
                    c.shipping_cost = round(c.shipping_cost / factor, 2)
                if c.total_price:
                    c.total_price = round(c.total_price / factor, 2)
                c.sold_currency = f"USD (repaired from {cur})"
            return comps
        raise CurrencyLeakError(
            f"currency leak detected and not confidently repairable: {diag}",
            charm_share=share, guessed_currency=cur, guessed_factor=factor)
    # default: raise
    raise CurrencyLeakError(
        f"currency leak detected: {diag}. Refusing to return corrupt prices "
        f"(set on_currency_leak='repair' to auto-correct, or fall back to "
        f"Stage C / Chrome).",
        charm_share=share, guessed_currency=cur, guessed_factor=factor)


# ---------------------------------------------------------------------------
# URL construction (debug / display helper — NOT used by the Actor)
# ---------------------------------------------------------------------------

def build_sold_search_url(query: str, sort_highest_price: bool = True) -> str:
    """Build the equivalent eBay sold-listings search URL for a query.

    NOT passed to the Apify Actor (which builds its own search). Kept as a
    utility for "show the user what eBay search this maps to" output.
    """
    params = {"_nkw": query, "LH_Sold": "1", "LH_Complete": "1"}
    if sort_highest_price:
        params["_sop"] = "3"
    return "https://www.ebay.com/sch/i.html?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# Apify REST transport (stdlib only — no apify-client needed)
# ---------------------------------------------------------------------------

def _api_request(method: str, path: str, token: str,
                 body: Optional[dict] = None, timeout: int = 60) -> Any:
    """One Apify REST call. Returns the decoded `data` payload (or raw JSON)."""
    url = f"{APIFY_API_BASE}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
        raise ApifyError(f"Apify API {method} {path} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ApifyError(
            f"Apify API unreachable ({method} {path}): {e.reason}. "
            f"If this is a sandbox, its proxy may be blocking api.apify.com."
        ) from e
    if not raw:
        return None
    parsed = json.loads(raw)
    return parsed.get("data", parsed) if isinstance(parsed, dict) else parsed


def _run_actor(actor: str, run_input: dict, timeout_sec: int) -> tuple[list[dict], str]:
    """Start an actor run over REST, poll to completion, return (items, run_id).

    Pure stdlib. `actor` may be 'user/name' (converted to 'user~name' for the
    API path). The run id is returned as verifiable proof the query executed.
    """
    try:
        token = get_apify_token()
    except ConfigError as e:
        raise ApifyError(str(e)) from e

    actor_path = actor.replace("/", "~")
    run = _api_request(
        "POST", f"acts/{actor_path}/runs?timeout={int(timeout_sec)}",
        token, body=run_input, timeout=60,
    )
    run_id = (run or {}).get("id")
    if not run_id:
        raise ApifyError("Apify run start returned no run id")

    deadline = time.monotonic() + timeout_sec
    status = (run or {}).get("status")
    dataset_id = (run or {}).get("defaultDatasetId")
    while status in ("READY", "RUNNING"):
        if time.monotonic() > deadline:
            raise ApifyError(f"Apify run {run_id} timed out after {timeout_sec}s (status={status})")
        time.sleep(2.5)
        run = _api_request("GET", f"actor-runs/{run_id}", token, timeout=30)
        status = (run or {}).get("status")
        dataset_id = (run or {}).get("defaultDatasetId") or dataset_id

    if status != "SUCCEEDED":
        raise ApifyError(f"Apify run {run_id} did not succeed (status={status})")
    if not dataset_id:
        raise ApifyError(f"Apify run {run_id} has no defaultDatasetId")

    items = _api_request("GET", f"datasets/{dataset_id}/items?clean=true&format=json",
                         token, timeout=60)
    return (items or []), run_id


# ---------------------------------------------------------------------------
# Run status message (Console label — cosmetic, best-effort)
# ---------------------------------------------------------------------------

def build_status_message(sort: str, sku: Optional[str] = None,
                         title: Optional[str] = None,
                         query: Optional[str] = None,
                         title_chars: Optional[int] = None,
                         query_chars: int = 48) -> str:
    """Compose the run's completion status message for the Apify Console.

    This is what makes runs diagnosable from the Console's runs LIST (the
    status-message column) without opening each one. Format:
    ``[<tag>] <sku> <title>`` — e.g. ``[best] 588a1313 Vintage Indonesian mask``
    (tag = 'best' for best_match, 'sold_highest' for price_high). sku/title
    are optional; whatever is supplied is appended, in that order. The full
    title is passed through — the Apify Console truncates it in the runs-list
    column. (Pass ``title_chars`` to cap it explicitly.)

    When NEITHER sku nor title is given (e.g. PRICE runs before a SKU
    exists), the search query is appended instead so the run is never
    anonymous: ``[best] vintage Indonesian Balinese carved wood mask``.
    """
    tag = SORT_STATUS_TAG.get(sort, sort)
    parts = [f"[{tag}]"]
    if sku:
        parts.append(str(sku))
    if title:
        parts.append(str(title)[:title_chars] if title_chars else str(title))
    if not sku and not title and query:
        parts.append(str(query)[:query_chars])
    return " ".join(parts)


def set_run_status_message(run_id: str, message: str,
                           token: Optional[str] = None, *,
                           terminal: bool = True, timeout: int = 30) -> None:
    """Set a run's status message via `PUT /v2/actor-runs/{runId}`.

    The message is cosmetic (Apify Console only), so callers treat this as
    best-effort and never let a failure break the actual search. Apify can
    reject a terminal-flagged update on an already-finished run
    (`cannot-set-is-status-message-terminal`); we retry once without the
    terminal flag before giving up.
    """
    if token is None:
        token = get_apify_token()
    body: dict[str, Any] = {"runId": run_id, "statusMessage": message}
    if terminal:
        body["isStatusMessageTerminal"] = True
    try:
        _api_request("PUT", f"actor-runs/{run_id}", token, body=body, timeout=timeout)
    except ApifyError:
        if not terminal:
            raise
        _api_request("PUT", f"actor-runs/{run_id}", token,
                     body={"runId": run_id, "statusMessage": message}, timeout=timeout)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CompRecord:
    """One sold-listing comp record from eBay.

    Field shape designed for PRICE's classifier. Prices are USD (the backend
    pins a US proxy; the currency-leak validator guards the assumption).
    Fields the Actor doesn't expose are left None.
    """
    title: str
    sold_price: float                     # USD, parsed from soldPrice
    url: str                              # direct link to the eBay listing
    sold_date: Optional[str] = None       # ISO 8601 (parsed from "May 12, 2026")
    condition: Optional[str] = None       # localized eBay condition label
    condition_id: Optional[int] = None    # not exposed by this Actor (None)
    seller_username: Optional[str] = None
    seller_feedback_score: Optional[int] = None   # parsed from "2K" / "532"
    seller_feedback_pct: Optional[float] = None   # parsed from "100%"
    shipping_cost: Optional[float] = None
    shipping_type: Optional[str] = None   # "free" when shipping is free
    sold_currency: Optional[str] = "USD"
    total_price: Optional[float] = None   # sold_price + shipping_cost
    item_id: Optional[str] = None
    listing_type: Optional[str] = None    # "Buy It Now" / "Auction"
    bids_count: Optional[int] = None      # for Tier-C single-bid exclusion
    keyword_tag: Optional[str] = None     # which input keyword this matched
    bo_accepted: bool = False             # not exposed by this Actor
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Value parsing (defensive — Actor returns mixed string/number formats)
# ---------------------------------------------------------------------------

def _parse_price(raw: Any) -> Optional[float]:
    """Extract a float from a number / money string ('$450.00', '+$12 ship')."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        for key in ("value", "amount", "soldPrice", "price"):
            if key in raw:
                parsed = _parse_price(raw[key])
                if parsed is not None:
                    return parsed
        return None
    if isinstance(raw, str):
        if "free" in raw.lower():
            return 0.0
        m = re.search(r"(\d+(?:[\d,]*\.\d+|\d*))", raw.replace(",", ""))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _parse_int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        m = re.search(r"\d+", raw)
        return int(m.group(0)) if m else None
    return None


def _parse_feedback_count(raw: Any) -> Optional[int]:
    """eBay feedback counts: '2K' -> 2000, '17.8K' -> 17800, '532' -> 532."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([KkMm]?)", s)
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


def _parse_pct(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = re.search(r"([\d.]+)", str(raw))
    return float(m.group(1)) if m else None


def _parse_sold_date(raw: Any) -> Optional[str]:
    """'May 12, 2026' / 'Sold May 12, 2026' -> '2026-05-12' (ISO date)."""
    if not raw:
        return None
    s = str(raw).strip()
    s = re.sub(r"^Sold\s+", "", s, flags=re.IGNORECASE)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s  # leave as-is if an unexpected format slips through


# ---------------------------------------------------------------------------
# Output mapping (automation-lab/ebay-sold-scraper schema)
# ---------------------------------------------------------------------------

def _map_to_comp_record(item: dict, keyword_tag: Optional[str]) -> Optional[CompRecord]:
    """Convert a raw Actor item dict to a CompRecord, or None if unusable."""
    title = item.get("title")
    if not title:
        return None

    sold_price = _parse_price(item.get("soldPrice"))
    if sold_price is None:
        sold_price = _parse_price(item.get("soldPriceString"))
    if sold_price is None:
        return None

    url = item.get("url")
    if not url:
        return None

    ship_raw = item.get("shippingCost")
    shipping_cost = _parse_price(ship_raw)
    shipping_type = "free" if (isinstance(ship_raw, str) and "free" in ship_raw.lower()) else None
    total_price = sold_price + (shipping_cost or 0.0)

    return CompRecord(
        title=str(title),
        sold_price=sold_price,
        url=str(url),
        sold_date=_parse_sold_date(item.get("soldDate")),
        condition=item.get("condition"),
        condition_id=None,
        seller_username=item.get("sellerName"),
        seller_feedback_score=_parse_feedback_count(item.get("sellerFeedbackCount")),
        seller_feedback_pct=_parse_pct(item.get("sellerFeedbackPercent")),
        shipping_cost=shipping_cost,
        shipping_type=shipping_type,
        sold_currency="USD",
        total_price=round(total_price, 2),
        item_id=str(item.get("itemId")) if item.get("itemId") is not None else None,
        listing_type=item.get("listingType"),
        bids_count=_parse_int(item.get("bidsCount")),
        keyword_tag=keyword_tag,
        bo_accepted=False,
        raw=item,
    )


# ---------------------------------------------------------------------------
# Result persistence (every Apify call is saved as JSON — audit trail / cache)
# ---------------------------------------------------------------------------

def _comp_to_dict(c: "CompRecord") -> dict:
    return {
        "title": c.title, "sold_price": c.sold_price, "sold_currency": c.sold_currency,
        "sold_date": c.sold_date, "condition": c.condition, "listing_type": c.listing_type,
        "bids_count": c.bids_count, "shipping_cost": c.shipping_cost,
        "total_price": c.total_price, "seller_username": c.seller_username,
        "seller_feedback_score": c.seller_feedback_score,
        "seller_feedback_pct": c.seller_feedback_pct,
        "item_id": c.item_id, "keyword_tag": c.keyword_tag, "url": c.url,
    }


def _default_runs_dir() -> Path:
    """Where run JSON is saved by default: $APIFY_RUNS_DIR or <repo>/apify_runs."""
    env = os.environ.get("APIFY_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "apify_runs"


def save_run_json(run_id: str, actor: str, queries: list[str],
                  raw_items: list[dict], comps: list["CompRecord"],
                  save_dir: Optional[Union[str, Path]] = None) -> str:
    """Persist one Apify call's results to a JSON file; return the path.

    Stores run metadata, the normalized comps, and the raw dataset items, so
    a run can be audited or re-read later without re-querying (and re-paying).
    """
    base = Path(save_dir) if save_dir else _default_runs_dir()
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = base / f"apify_run_{ts}_{run_id}.json"
    prices = [c.sold_price for c in comps if c.sold_price]
    charm = _charm_share(prices) if prices else None
    payload = {
        "run_id": run_id,
        "actor": actor,
        "queries": queries,
        "saved_at_utc": ts,
        "n_comps": len(comps),
        "charm_price_share": round(charm, 3) if charm is not None else None,
        "currency_leak_suspected": bool(
            len(prices) >= _MIN_SAMPLES_FOR_LEAK_CHECK
            and charm is not None and charm < _CHARM_LEAK_THRESHOLD
        ),
        "comps": [_comp_to_dict(c) for c in comps],
        "raw_items": raw_items,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def search_ebay_sold(
    query: Union[str, list[str]],
    *,
    max_listings: int = DEFAULT_MAX_LISTINGS,
    max_pages: int = DEFAULT_MAX_PAGES,
    sort: str = DEFAULT_SORT,
    listing_type: str = DEFAULT_LISTING_TYPE,
    condition: Optional[list[str]] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    actor_id: Optional[str] = None,
    timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC,
    on_currency_leak: str = "raise",
    save: bool = True,
    save_dir: Optional[Union[str, Path]] = None,
    sku: Optional[str] = None,
    title: Optional[str] = None,
    status_label: Optional[str] = None,
    set_status: bool = True,
) -> list[CompRecord]:
    """Run the eBay sold-listings Actor and return USD comps.

    Args:
        query: one search keyword string OR a list of keywords (each runs as
               a separate search). With a single keyword, every returned
               comp is tagged with it via `keyword_tag`.
        max_listings: max results per keyword (maxListingsPerSearch).
        max_pages: max search pages per keyword (60 listings/page).
        sort: one of best_match / newly_listed / price_low / price_high.
        listing_type: all / auction / buy_it_now.
        condition: optional list of eBay condition filters (e.g. ["used"]).
        min_price, max_price: optional USD price-range filters.
        max_retries: per-request retries before skipping (anti-bot).
        actor_id: override the Apify Actor ID (default: from config).
        timeout_sec: max time to wait for the Apify run.
        on_currency_leak: "raise" (default) | "repair" | "ignore" — how to
            handle a run whose prices fail the charm-price USD check.
        sku, title: optional item identifiers used to build the run's
            completion status message (Apify Console label); the full title
            is passed through (the Console truncates it in the runs list).
        status_label: override the full status-message text (ignores sku/title).
        set_status: post a completion status message labeling the run in the
            Apify Console runs list (default True). Falls back to the query
            when sku/title are absent, so every run is labeled. Best-effort.

    Returns:
        List of CompRecord objects (USD), ordered as the Actor returns them.

    Raises:
        CurrencyLeakError: prices look FX-converted (and on_currency_leak
            is not "repair"/"ignore").
        ApifyError: auth failure, run failure, timeout, or invalid config.
        ValueError: on invalid argument values.
    """
    keywords = [query] if isinstance(query, str) else list(query)
    if not keywords or not all(isinstance(k, str) and k.strip() for k in keywords):
        raise ValueError("query must be a non-empty string or list of non-empty strings")

    if sort not in VALID_SORTS:
        raise ValueError(f"sort must be one of {sorted(VALID_SORTS)}; got {sort!r}")
    if listing_type not in VALID_LISTING_TYPES:
        raise ValueError(
            f"listing_type must be one of {sorted(VALID_LISTING_TYPES)}; got {listing_type!r}")
    if max_listings < 1:
        raise ValueError(f"max_listings must be >= 1; got {max_listings}")
    if max_pages < 1:
        raise ValueError(f"max_pages must be >= 1; got {max_pages}")
    if on_currency_leak not in ("raise", "repair", "ignore"):
        raise ValueError("on_currency_leak must be 'raise', 'repair', or 'ignore'")

    actor = actor_id or get_apify_actor()

    run_input: dict[str, Any] = {
        "searchQueries": keywords,
        "maxListingsPerSearch": max_listings,
        "maxSearchPages": max_pages,
        "sort": sort,
        "listingType": listing_type,
        "maxRequestRetries": max_retries,
    }
    if condition:
        run_input["condition"] = condition
    if min_price is not None:
        run_input["minPrice"] = min_price
    if max_price is not None:
        run_input["maxPrice"] = max_price

    # Execute over the Apify REST API (stdlib only — no apify-client).
    raw_items, run_id = _run_actor(actor, run_input, timeout_sec)

    single_tag = keywords[0] if len(keywords) == 1 else None
    comps: list[CompRecord] = []
    for item in raw_items:
        comp = _map_to_comp_record(item, single_tag)
        if comp is not None:
            comps.append(comp)

    # Provenance for PRICE's research log (set before leak check so a flagged
    # run still records that it reached the backend).
    LAST_RUN.clear()
    LAST_RUN.update(run_id=run_id, actor=actor, queries=keywords, n_comps=len(comps))

    # Persist results to JSON BEFORE the leak check, so even a leak-flagged
    # run leaves an auditable record (and a cache to avoid re-querying).
    if save:
        try:
            LAST_RUN["saved_path"] = save_run_json(run_id, actor, keywords, raw_items, comps, save_dir)
        except OSError as e:
            LAST_RUN["save_error"] = str(e)

    # Post a completion status message to the run so it's identifiable in the
    # Console runs LIST (the status-message column) — the whole point: diagnose
    # past runs without a custom UI. Format "[best|sold_highest] <sku>
    # <title>", falling back to the query when sku/title aren't supplied,
    # so NO run is anonymous. Best-effort — a status failure never breaks the
    # returned comps.
    if set_status:
        msg = status_label or build_status_message(
            sort, sku, title, query=" | ".join(keywords))
        LAST_RUN["status_message"] = msg
        try:
            set_run_status_message(run_id, msg)
            LAST_RUN["status_message_posted"] = True
        except (ApifyError, ConfigError) as e:
            LAST_RUN["status_message_posted"] = False
            LAST_RUN["status_message_error"] = str(e)

    return _check_currency_leak(comps, on_currency_leak)


# ---------------------------------------------------------------------------
# CLI entry point (for manual testing)
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import json
    import sys

    # Avoid UnicodeEncodeError on Windows (cp1252) when comp titles contain
    # non-cp1252 characters (e.g. emoji); same approach as lib/list_edit.py.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Apify eBay sold-listings search (automation-lab/ebay-sold-scraper)"
    )
    parser.add_argument("query", nargs="*",
                        help="One or more search keywords (each runs a separate search). "
                             "Optional with --ingest.")
    parser.add_argument("--ingest", metavar="RAW_JSON",
                        help="Skip the live Actor call: read raw scraper items from a "
                             "JSON file (the Apify MCP tool's result — a dict with an "
                             "'items' list, or a bare list of items) and write the same "
                             "saved-run JSON that a live run produces (normalized comps "
                             "with total_price + charm/currency check). Use on the MCP "
                             "path so no JSON is hand-authored.")
    parser.add_argument("--run-id", dest="run_id",
                        help="Run id to stamp on an --ingest save (default: the file's "
                             "runId/run_id, else 'ingested').")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_LISTINGS, dest="max_listings",
                        help=f"Max results per keyword (default: {DEFAULT_MAX_LISTINGS})")
    parser.add_argument("--pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max search pages per keyword (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--sort", default=DEFAULT_SORT, choices=sorted(VALID_SORTS),
                        help=f"Sort order (default: {DEFAULT_SORT})")
    parser.add_argument("--listing-type", default=DEFAULT_LISTING_TYPE,
                        choices=sorted(VALID_LISTING_TYPES),
                        help=f"Listing type filter (default: {DEFAULT_LISTING_TYPE})")
    parser.add_argument("--condition", nargs="*", help="Condition filter(s), e.g. used new")
    parser.add_argument("--min-price", type=float, help="Minimum sold price (USD)")
    parser.add_argument("--max-price", type=float, help="Maximum sold price (USD)")
    parser.add_argument("--actor", help="Override the Apify Actor ID")
    parser.add_argument("--timeout", type=int, default=DEFAULT_RUN_TIMEOUT_SEC,
                        help=f"Max seconds for the Apify run (default: {DEFAULT_RUN_TIMEOUT_SEC})")
    parser.add_argument("--on-leak", default="raise", choices=["raise", "repair", "ignore"],
                        help="How to handle a detected currency leak (default: raise)")
    parser.add_argument("--save-dir", help="Directory to save the run JSON in "
                        "(default: $APIFY_RUNS_DIR or <repo>/apify_runs). "
                        "Tip: pass the shoot dir to save alongside price.txt.")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not save the run results to JSON")
    parser.add_argument("--sku", help="SKU for the run's completion status message "
                        "(Console label: '[best|sold_highest] <sku> <title>')")
    parser.add_argument("--title", help="Item title for the status message "
                        "(full title; the Apify Console truncates it in the runs list)")
    parser.add_argument("--status-label", dest="status_label",
                        help="Override the full status-message text")
    parser.add_argument("--no-status", action="store_true",
                        help="Do not post a completion status message to the run")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of human-readable list")
    args = parser.parse_args()

    # --- Ingest path: normalize raw MCP items into a saved run JSON, no live call ---
    if args.ingest:
        try:
            raw = json.loads(Path(args.ingest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: cannot read --ingest file: {e}", file=sys.stderr)
            sys.exit(1)
        if isinstance(raw, dict):
            items = raw.get("items") or raw.get("raw_items") or raw.get("comps") or []
            run_id = args.run_id or raw.get("runId") or raw.get("run_id") or "ingested"
            file_queries = raw.get("queries") or raw.get("searchQueries")
        else:
            items = raw
            run_id = args.run_id or "ingested"
            file_queries = None
        actor = args.actor or "automation-lab/ebay-sold-scraper"
        queries = args.query or file_queries or []
        comps = [c for c in (_map_to_comp_record(it, None) for it in items) if c]
        if not comps:
            print(f"ERROR: no usable comps in {args.ingest} "
                  f"(expected a dict with an 'items' list, or a bare list of scraper items)",
                  file=sys.stderr)
            sys.exit(1)
        prices = [c.sold_price for c in comps if c.sold_price]
        charm = _charm_share(prices) if prices else None
        leak = bool(len(prices) >= _MIN_SAMPLES_FOR_LEAK_CHECK
                    and charm is not None and charm < _CHARM_LEAK_THRESHOLD)
        if args.no_save:
            print(json.dumps([_comp_to_dict(c) for c in comps], indent=2, default=str))
            return
        path = save_run_json(run_id, actor, queries, items, comps, save_dir=args.save_dir)
        print(f"Ingested {len(comps)} comp(s) from {args.ingest} (no live Apify call)")
        print(f"Apify run: {run_id}  (actor {actor})  — ingested")
        print(f"Saved results: {path}")
        if charm is not None:
            note = ("  -> CURRENCY LEAK SUSPECTED: prices don't cluster on USD charm "
                    "endings; verify or prefer Chrome (Stage C)" if leak
                    else "  (USD charm pattern OK)")
            print(f"Charm-price share: {charm:.0%}{note}")
        return

    if not args.query:
        parser.error("a search query is required unless --ingest is given")

    try:
        comps = search_ebay_sold(
            args.query if len(args.query) > 1 else args.query[0],
            max_listings=args.max_listings,
            max_pages=args.pages,
            sort=args.sort,
            listing_type=args.listing_type,
            condition=args.condition,
            min_price=args.min_price,
            max_price=args.max_price,
            actor_id=args.actor,
            timeout_sec=args.timeout,
            on_currency_leak=args.on_leak,
            save=not args.no_save,
            save_dir=args.save_dir,
            sku=args.sku,
            title=args.title,
            status_label=args.status_label,
            set_status=not args.no_status,
        )
    except CurrencyLeakError as e:
        if LAST_RUN.get("run_id"):
            print(f"Apify run: {LAST_RUN['run_id']}  (actor {LAST_RUN.get('actor')})  — FLAGGED currency leak")
        if LAST_RUN.get("saved_path"):
            print(f"Saved results: {LAST_RUN['saved_path']}")
        print(f"CURRENCY LEAK: {e}", file=sys.stderr)
        print("  -> prices NOT returned. Re-run with --on-leak repair to auto-correct, "
              "or use the Chrome (Stage C) path.", file=sys.stderr)
        sys.exit(2)
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
                    "listing_type": c.listing_type,
                    "bids_count": c.bids_count,
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
    if LAST_RUN.get("run_id"):
        print(f"Apify run: {LAST_RUN['run_id']}  (actor {LAST_RUN.get('actor')})  "
              f"— proof this query hit the Apify backend")
    if LAST_RUN.get("status_message"):
        posted = LAST_RUN.get("status_message_posted")
        state = "posted" if posted else f"NOT posted ({LAST_RUN.get('status_message_error', 'unknown')})"
        print(f"Run status message: {LAST_RUN['status_message']!r} — {state}")
    if LAST_RUN.get("saved_path"):
        print(f"Saved results: {LAST_RUN['saved_path']}")
    print(f"Equivalent eBay search: {build_sold_search_url(args.query[0])}")
    print()
    for i, c in enumerate(comps, 1):
        tag = f" [{c.keyword_tag}]" if c.keyword_tag and len(args.query) > 1 else ""
        try:
            print(f"[{i:2d}] {c.sold_currency} {c.sold_price:.2f}{tag} - {c.title}")
        except UnicodeEncodeError:
            safe = c.title.encode("ascii", "replace").decode("ascii")
            print(f"[{i:2d}] {c.sold_currency} {c.sold_price:.2f}{tag} - {safe}")
        if c.sold_date:
            print(f"     Sold: {c.sold_date}")
        if c.condition:
            lt = f", {c.listing_type}" if c.listing_type else ""
            bids = f", {c.bids_count} bids" if c.bids_count else ""
            print(f"     Condition: {c.condition}{lt}{bids}")
        if c.seller_username:
            fb_parts = []
            if c.seller_feedback_score is not None:
                fb_parts.append(f"feedback {c.seller_feedback_score}")
            if c.seller_feedback_pct is not None:
                fb_parts.append(f"{c.seller_feedback_pct}% positive")
            fb = f" ({', '.join(fb_parts)})" if fb_parts else ""
            print(f"     Seller: {c.seller_username}{fb}")
        if c.shipping_cost is not None:
            print(f"     Shipping: +${c.shipping_cost:.2f}" + (" (free)" if c.shipping_type == "free" else ""))
        print(f"     URL: {c.url}")
        print()


if __name__ == "__main__":
    _cli()
