"""
Apify eBay sold-listings client.

PRICE's default Stage B comp source (un-gated direct eBay sold-listings).
The Claude-in-Chrome browse path is the optional Stage C fallback, used
only when confidence is low or this backend is unavailable.

Backend Actor (PRIMARY): **cirkit/ebay-product-scraper** (configurable via
`apify.ebay_actor`). Chosen because it (a) returns eBay **SOLD/completed**
listings (confirmed sale prices, not asking), (b) is searchable **two ways**
via `sortBy` — **best_match** and **price_high_low** (price high->low) — which
is exactly PRICE's dual query (relevant cohort + ceiling), and (c) pins a
**US residential proxy by default**, so eBay returns prices in USD natively —
avoiding the silent foreign-currency leak that actors without proxy control
suffer (e.g. caffein.dev/ebay-sold-listings returned BRL/CZK prices mislabeled
as USD, inflating values ~5x). It also price-band-partitions past the 600/query
cap. Prior primaries (automation-lab/ebay-sold-scraper, then the caffein.dev
leak) are retained only as documented history; the input/output ADAPTERS for
automation-lab + khadinakbar remain for the fallback path (_actor_family).

  NOTE (validate cirkit output keys once): cirkit does not publish its output
  field names in its schema, so `_map_cirkit_item` probes common key variants.
  On the first healthy live run, confirm the mapped fields (sold_price, url,
  sold_date, shipping) are populated and tighten the key list if needed.

**Silent-block fallback.** eBay periodically blocks the primary actor's
proxy, which then returns 0 items with a SUCCEEDED status — indistinguishable
from a thin market. When the primary returns 0 comps, this module AUTOMATICALLY
retries once with a fallback actor (default `khadinakbar/ebay-sold-comps-
analytics-scraper`, a different proxy that currently gets through) and maps its
schema to CompRecord. The fallback preserves the dual-query distribution +
delivered basis + condition/unit filters; it degrades the single-bid and
seller-feedback filters (it doesn't expose bids/feedback). Configure via
`APIFY_EBAY_FALLBACK_ACTOR` (or disable with ""/none / `--no-fallback`). Full
write-up + durable-fix options: docs/pricing-backend-issues.md.

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

from config import (ConfigError, get_apify_actor, get_apify_enabled,
                    get_apify_token)

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

# Fallback actor — used AUTOMATICALLY when the primary actor returns 0 comps.
# The primary (automation-lab) fails SILENTLY (SUCCEEDED, 0 items) when eBay
# blocks its proxy, which masquerades as a "thin market". khadinakbar uses a
# different actor/proxy that gets through, and preserves the price-strategy
# core: it supports best_match + price_desc sorts (dual-query distribution),
# returns a NUMERIC shippingCost (delivered basis), condition, and listing
# type. It does NOT expose bids-count or seller-feedback, so the single-bid
# and low-feedback drop filters degrade to no-ops on fallback comps (no
# currently-unblocked actor exposes those). Override APIFY_EBAY_FALLBACK_ACTOR;
# set to "" / "none" to disable the fallback.
DEFAULT_FALLBACK_ACTOR = "khadinakbar/ebay-sold-comps-analytics-scraper"

# --- Health gate (control query) -------------------------------------------
# A 0-comp result is ambiguous: genuinely-thin market vs silently-blocked
# backend. To tell them apart we re-query with a CONTROL: a search that always
# has thousands of sold comps. If the control comes back empty too, the backend
# is blocked, not the market thin. Override via APIFY_EBAY_CONTROL_QUERY.
DEFAULT_CONTROL_QUERY = "Nike Air Force 1"
# Comps the control must return for the backend to count as healthy. Low enough
# to tolerate a partial page, high enough that 1-2 stragglers don't pass it.
CONTROL_MIN_COMPS = 5
# The probe is a cheap 1-page run — it exists to prove reachability, not to
# collect data.
CONTROL_MAX_LISTINGS = 10


def control_query() -> str:
    """The control search used by the health gate (env-overridable)."""
    return (os.environ.get("APIFY_EBAY_CONTROL_QUERY") or DEFAULT_CONTROL_QUERY).strip() \
        or DEFAULT_CONTROL_QUERY

# khadinakbar sortBy  <-  our canonical sort
_KHAD_SORT = {
    "best_match": "best_match", "price_high": "price_desc",
    "price_low": "price_asc", "newly_listed": "newest",
}
# khadinakbar single-value condition  <-  our condition tokens
_KHAD_CONDITION = {"new": "new", "used": "used",
                   "refurbished": "refurbished", "open_box": "open_box"}


def _fallback_actor() -> Optional[str]:
    """Resolve the fallback actor (env override; "" / "none" disables it)."""
    val = os.environ.get("APIFY_EBAY_FALLBACK_ACTOR", DEFAULT_FALLBACK_ACTOR)
    val = (val or "").strip()
    if not val or val.lower() == "none":
        return None
    return val


def _actor_family(actor: str) -> str:
    """Which input/output adapter an actor uses. Default: automation-lab schema."""
    a = (actor or "").lower()
    if "cirkit" in a:
        return "cirkit"
    if "khadinakbar" in a:
        return "khadinakbar"
    return "automation-lab"


# cirkit/ebay-product-scraper sortBy  <-  our canonical sort. cirkit is the
# PRIMARY actor (see config default): eBay SOLD/completed listings, US
# residential proxy by default (USD prices natively), multi-query, and it
# exposes exactly the two sorts PRICE's dual query needs — best_match and
# price_high_low (price high->low, the ceiling-first sort).
_CIRKIT_SORT = {
    "best_match": "best_match", "price_high": "price_high_low",
    "price_low": "price_low_high", "newly_listed": "recently_listed",
}
# cirkit single-value condition  <-  our condition tokens (cirkit enum:
# any/new/open_box/refurbished/used/parts).
_CIRKIT_CONDITION = {"new": "new", "used": "used", "refurbished": "refurbished",
                     "open_box": "open_box", "for_parts": "parts"}
# cirkit listingType  <-  our listing_type (cirkit enum: any/auction/buy_it_now).
_CIRKIT_LISTING_TYPE = {"all": "any", "auction": "auction", "buy_it_now": "buy_it_now"}

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


class ApifyDisabledError(ApifyError):
    """Raised when Apify is switched off and something tried to call it.

    Stage B moved to the browser path (lib/ebay_sold_browse.py) on
    2026-08-15. This exists so a leftover call site fails LOUDLY and for free,
    rather than quietly spending money on a backend we no longer trust.
    """


def _require_apify_enabled() -> None:
    if not get_apify_enabled():
        raise ApifyDisabledError(
            "Apify is DISABLED (config apify.enabled=false). Stage B comps now come "
            "from the logged-in browser: build URLs with "
            "`python lib/ebay_sold_browse.py \"<query>\" --urls`, capture the results "
            "page, then `--parse` it. Set apify.enabled: true (or APIFY_ENABLED=1) "
            "only if you deliberately want the actor path back.")


class BackendBlockedError(ApifyError):
    """Raised when a 0-comp result is NOT a thin market but a blocked backend.

    This is the whole point of the health gate. eBay periodically blocks the
    actors' proxies and serves them an empty/challenge page; the actor parses
    zero listings and exits SUCCEEDED with 0 items. An empty comp list is
    therefore ambiguous: it means EITHER "nothing like this has sold" (a real,
    priceable signal) OR "we are flying blind" (not priceable at all).

    When a query returns 0 comps, `search_ebay_sold` re-probes with a control
    query known to have thousands of sold comps. Control returns data => the
    empty result is a genuine THIN market. Control ALSO returns 0 => every
    backend is blocked, and this is raised instead of handing back an empty
    list that reads as thin.

    Never downgrade this to "thin" — pricing off a blocked backend is how an
    item gets listed under its market (see docs/pricing-backend-issues.md).
    """

    def __init__(self, message: str, *, health: dict):
        super().__init__(message)
        self.health = health


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
    thumbnail: Optional[str] = None       # listing image (for the visual library)
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
    m_iso = re.match(r"(\d{4}-\d{2}-\d{2})", s)   # ISO 8601 (e.g. khadinakbar "2026-07-28T00:00:00.000Z")
    if m_iso:
        return m_iso.group(1)
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
        thumbnail=item.get("thumbnail"),
        keyword_tag=keyword_tag,
        bo_accepted=False,
        raw=item,
    )


# ---------------------------------------------------------------------------
# Output mapping (cirkit/ebay-product-scraper — PRIMARY)
# ---------------------------------------------------------------------------

def _first(item: dict, *keys: str) -> Any:
    """First non-empty value among the given keys (schema-tolerant)."""
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


def _num_from(v: Any) -> Any:
    """Pull a numeric/string amount out of a value that may be a nested
    {value|amount|usd, currency} object (cirkit reports 'numeric prices with
    currency and USD approximations')."""
    if isinstance(v, dict):
        return v.get("usd") or v.get("value") or v.get("amount") or v.get("convertedValue")
    return v


def _map_cirkit_item(item: dict, keyword_tag: Optional[str]) -> Optional[CompRecord]:
    """Map a cirkit/ebay-product-scraper SOLD item to CompRecord.

    cirkit's exact output field NAMES are not published in its schema (README
    only describes them), so this mapper is deliberately schema-tolerant: it
    probes the common key variants for each field. VALIDATE the key names on
    the first live run (`python apify_ebay.py "<q>" --json`) and tighten if a
    field comes back None when the raw item clearly has it. Prices are pinned
    USD via the US residential proxy in the input builder.
    """
    title = _first(item, "title", "name")
    if not title:
        return None

    price_raw = _num_from(_first(item, "soldPriceUsd", "priceUsd", "usdPrice",
                                 "soldPrice", "salePrice", "price", "priceValue"))
    sold_price = _parse_price(price_raw)
    if sold_price is None:
        return None

    url = _first(item, "itemUrl", "url", "link", "itemWebUrl", "permalink")
    if not url:
        return None

    currency = _first(item, "currency", "priceCurrency") or "USD"

    ship_raw = _first(item, "shippingCost", "shipping", "shippingPrice", "deliveryCost")
    ship_num = _num_from(ship_raw)
    shipping_cost = _parse_price(ship_num)
    stype = _first(item, "shippingType", "shippingOption")
    is_free = bool((isinstance(ship_raw, str) and "free" in ship_raw.lower())
                   or (stype and "free" in str(stype).lower()))
    if shipping_cost is None and is_free:
        shipping_cost = 0.0
    shipping_type = "free" if is_free else (str(stype) if stype else None)
    total_price = sold_price + (shipping_cost or 0.0)

    seller = _first(item, "sellerName", "sellerUsername", "seller")
    if isinstance(seller, dict):
        seller = seller.get("username") or seller.get("name")
    fb_score = _first(item, "sellerFeedbackScore", "feedbackScore", "sellerFeedbackCount")
    fb_pct = _first(item, "sellerFeedbackPercent", "feedbackPercent", "sellerPositivePercent")
    iid = _first(item, "itemId", "id", "legacyItemId")

    return CompRecord(
        title=str(title),
        sold_price=sold_price,
        url=str(url),
        sold_date=_parse_sold_date(_first(item, "soldDate", "dateSold", "saleDate",
                                          "endDate", "endedDate")),
        condition=_first(item, "condition", "conditionName"),
        condition_id=None,
        seller_username=str(seller) if seller else None,
        seller_feedback_score=_parse_feedback_count(fb_score) if fb_score is not None else None,
        seller_feedback_pct=_parse_pct(fb_pct),
        shipping_cost=shipping_cost,
        shipping_type=shipping_type,
        sold_currency=str(currency),
        total_price=round(total_price, 2),
        item_id=str(iid) if iid is not None else None,
        listing_type=_first(item, "listingType", "buyingFormat", "format"),
        bids_count=_parse_int(_first(item, "bidsCount", "bids", "bidCount")),
        thumbnail=_first(item, "image", "thumbnail", "imageUrl", "galleryUrl", "primaryImage"),
        keyword_tag=keyword_tag,
        bo_accepted=False,
        raw=item,
    )


# ---------------------------------------------------------------------------
# Output mapping (khadinakbar/ebay-sold-comps-analytics-scraper — fallback)
# ---------------------------------------------------------------------------

def _map_khadinakbar_item(item: dict, keyword_tag: Optional[str]) -> Optional[CompRecord]:
    """Map a khadinakbar item to CompRecord. Fields it doesn't expose
    (seller feedback, bids) are left None — the corresponding price_stats
    drop filters then become no-ops on these comps (documented tradeoff)."""
    title = item.get("title")
    if not title:
        return None
    sold_price = _parse_price(item.get("soldPrice"))
    if sold_price is None:
        return None
    url = item.get("itemUrl") or item.get("permalink")
    if not url:
        return None

    shipping_cost = _parse_price(item.get("shippingCost"))
    stype = item.get("shippingType")
    is_free = bool(stype and "free" in str(stype).lower())
    shipping_type = "free" if is_free else (str(stype) if stype else None)
    if shipping_cost is None and is_free:
        shipping_cost = 0.0
    total_price = sold_price + (shipping_cost or 0.0)

    return CompRecord(
        title=str(title),
        sold_price=sold_price,
        url=str(url),
        sold_date=_parse_sold_date(item.get("soldDate")),
        condition=item.get("condition"),
        condition_id=None,
        seller_username=item.get("sellerUsername"),
        seller_feedback_score=None,   # khadinakbar does not expose feedback score
        seller_feedback_pct=None,
        shipping_cost=shipping_cost,
        shipping_type=shipping_type,
        sold_currency=item.get("currency") or "USD",
        total_price=round(total_price, 2),
        item_id=str(item.get("itemId")) if item.get("itemId") is not None else None,
        listing_type=item.get("listingType"),
        bids_count=None,              # khadinakbar does not expose bids count
        thumbnail=item.get("imageUrl") or item.get("thumbnail"),
        keyword_tag=keyword_tag,
        bo_accepted=False,
        raw=item,
    )


def _map_item(actor: str, item: dict, keyword_tag: Optional[str]) -> Optional[CompRecord]:
    """Dispatch an actor's raw item to the right output mapper."""
    fam = _actor_family(actor)
    if fam == "cirkit":
        return _map_cirkit_item(item, keyword_tag)
    if fam == "khadinakbar":
        return _map_khadinakbar_item(item, keyword_tag)
    return _map_to_comp_record(item, keyword_tag)


# ---------------------------------------------------------------------------
# Input building (per-actor schema)
# ---------------------------------------------------------------------------

def _build_run_input(actor: str, keywords: list[str], *, max_listings: int,
                     max_pages: int, sort: str, listing_type: str,
                     condition: Optional[list[str]], min_price: Optional[float],
                     max_price: Optional[float], max_retries: int) -> dict:
    """Build the Actor input for the given actor's schema."""
    if _actor_family(actor) == "cirkit":
        inp: dict[str, Any] = {
            "searchQueries": keywords,             # cirkit takes an array of queries
            "mode": "sold",                        # SOLD/completed listings only
            "sortBy": _CIRKIT_SORT.get(sort, "best_match"),
            "maxResultsPerQuery": max(1, min(600, int(max_listings))),
            "listingType": _CIRKIT_LISTING_TYPE.get(listing_type, "any"),
            # US residential proxy REQUIRED (eBay blocks datacenter IPs) — this
            # also pins prices to USD, avoiding the caffein.dev FX leak.
            "proxyConfiguration": {
                "useApifyProxy": True,
                "apifyProxyGroups": ["RESIDENTIAL"],
                "apifyProxyCountry": "US",
            },
        }
        # cirkit condition is a single enum; take the first mappable token.
        if condition:
            for c in condition:
                if c in _CIRKIT_CONDITION:
                    inp["condition"] = _CIRKIT_CONDITION[c]
                    break
        if min_price is not None:
            inp["priceMin"] = int(min_price)
        if max_price is not None:
            inp["priceMax"] = int(max_price)
        return inp

    if _actor_family(actor) == "khadinakbar":
        inp: dict[str, Any] = {
            "searchQuery": keywords[0],          # khadinakbar takes ONE query
            "marketplace": "ebay.com",
            "maxItems": max(1, min(1000, int(max_listings))),
            "sortBy": _KHAD_SORT.get(sort, "best_match"),
            "includeReport": False,              # raw rows only; price_stats does stats
        }
        if condition:
            for c in condition:
                if c in _KHAD_CONDITION:
                    inp["condition"] = _KHAD_CONDITION[c]
                    break
        if min_price is not None:
            inp["minPrice"] = int(min_price)
        if max_price is not None:
            inp["maxPrice"] = int(max_price)
        return inp

    # automation-lab (default) schema
    inp = {
        "searchQueries": keywords,
        "maxListingsPerSearch": max_listings,
        "maxSearchPages": max_pages,
        "sort": sort,
        "listingType": listing_type,
        "maxRequestRetries": max_retries,
    }
    if condition:
        inp["condition"] = condition
    if min_price is not None:
        inp["minPrice"] = min_price
    if max_price is not None:
        inp["maxPrice"] = max_price
    return inp


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
        "item_id": c.item_id, "thumbnail": c.thumbnail,
        "keyword_tag": c.keyword_tag, "url": c.url,
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

def _run_control_probe(actor: str, timeout_sec: int) -> tuple[int, Optional[str]]:
    """Run the control query against one actor. Returns (n_comps, run_id).

    Deliberately cheap: one page, `best_match` (the sort least likely to be
    filtered), no condition/price filters — anything that could legitimately
    empty the result set is left off, so an empty return means "blocked".
    """
    run_input = _build_run_input(
        actor, [control_query()], max_listings=CONTROL_MAX_LISTINGS, max_pages=1,
        sort="best_match", listing_type=DEFAULT_LISTING_TYPE, condition=None,
        min_price=None, max_price=None, max_retries=DEFAULT_MAX_RETRIES)
    raw, run_id = _run_actor(actor, run_input, timeout_sec)
    mapped = [c for c in (_map_item(actor, it, None) for it in raw) if c is not None]
    return len(mapped), run_id


def check_backend_health(
    actor_id: Optional[str] = None,
    *,
    timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC,
    include_fallback: bool = True,
) -> dict:
    """Probe the Stage B backend(s) with the control query.

    Answers one question: can we reach eBay sold data AT ALL right now? Tries
    the primary, then (unless disabled) the fallback, and stops at the first
    healthy one — a single reachable backend is enough to trust a 0-comp
    result as a genuinely thin market.

    Returns a dict: {healthy, control_query, probes: [{actor, n_comps,
    run_id, healthy, error}]}. Never raises for a blocked backend — a failed
    probe IS the answer; per-actor errors are captured in `probes`.
    """
    _require_apify_enabled()
    primary = actor_id or get_apify_actor()
    actors = [primary]
    if include_fallback:
        fb = _fallback_actor()
        if fb and _actor_family(fb) != _actor_family(primary):
            actors.append(fb)

    probes: list[dict] = []
    healthy = False
    for act in actors:
        try:
            n_comps, run_id = _run_control_probe(act, timeout_sec)
            error = None
        except (ApifyError, ValueError, ConfigError) as e:
            n_comps, run_id, error = 0, None, str(e)
        ok = n_comps >= CONTROL_MIN_COMPS
        probes.append({"actor": act, "n_comps": n_comps, "run_id": run_id,
                       "healthy": ok, "error": error})
        if ok:
            healthy = True
            break  # one reachable backend is all the gate needs

    return {"healthy": healthy, "control_query": control_query(),
            "min_comps": CONTROL_MIN_COMPS, "probes": probes}


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
    use_fallback: bool = True,
    health_gate: bool = True,
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
        health_gate: when the search returns 0 comps, re-probe with the
            control query to tell a THIN market from a BLOCKED backend, and
            raise BackendBlockedError for the latter (default True). Costs one
            extra cheap run, and ONLY on an empty result — the happy path is
            unaffected. Disable only when you genuinely want an unverified
            empty list back.

    Returns:
        List of CompRecord objects (USD), ordered as the Actor returns them.
        An EMPTY list means a verified-thin market (the backend was probed and
        answered) unless health_gate=False, in which case it is unverified —
        check LAST_RUN["verdict"].

    Raises:
        CurrencyLeakError: prices look FX-converted (and on_currency_leak
            is not "repair"/"ignore").
        BackendBlockedError: 0 comps AND the control query also came back
            empty — every backend is blocked, so nothing here is priceable.
        ApifyError: auth failure, run failure, timeout, or invalid config.
        ValueError: on invalid argument values.
    """
    _require_apify_enabled()
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
    single_tag = keywords[0] if len(keywords) == 1 else None

    def _run_and_map(act: str) -> tuple[list[dict], str, list[CompRecord]]:
        run_input = _build_run_input(
            act, keywords, max_listings=max_listings, max_pages=max_pages,
            sort=sort, listing_type=listing_type, condition=condition,
            min_price=min_price, max_price=max_price, max_retries=max_retries)
        raw, rid = _run_actor(act, run_input, timeout_sec)
        mapped = [c for c in (_map_item(act, it, single_tag) for it in raw) if c is not None]
        return raw, rid, mapped

    # Execute over the Apify REST API (stdlib only — no apify-client).
    fallback_used: Optional[str] = None
    primary_error: Optional[str] = None
    try:
        raw_items, run_id, comps = _run_and_map(actor)
    except ApifyError as e:
        # Primary actor FAILED/timed out (a hard error, distinct from the
        # silent-0 block). Fall through to the fallback below rather than
        # aborting the whole search.
        raw_items, run_id, comps, primary_error = [], None, [], str(e)

    # Fallback: triggers on BOTH failure modes — a hard primary error (above)
    # OR 0 comps with a SUCCEEDED status (eBay blocked the primary's proxy;
    # masquerades as a thin market). If the caller didn't pin a specific actor,
    # retry once with the fallback actor (different proxy) before giving up.
    if not comps and actor_id is None and use_fallback:
        fb = _fallback_actor()
        if fb and _actor_family(fb) != _actor_family(actor):
            try:
                fb_raw, fb_run_id, fb_comps = _run_and_map(fb)
                raw_items, run_id, comps, actor = fb_raw, fb_run_id, fb_comps, fb
                primary_error = None  # fallback reached the backend; primary error no longer fatal
                if fb_comps:
                    fallback_used = fb
            except (ApifyError, ValueError):
                pass  # keep primary_error; re-raised below if nothing ran

    # If neither primary nor fallback produced a usable run, surface the error.
    if run_id is None:
        raise ApifyError(primary_error or "Apify run failed and no fallback produced results")

    # Provenance for PRICE's research log (set before leak check so a flagged
    # run still records that it reached the backend).
    LAST_RUN.clear()
    LAST_RUN.update(run_id=run_id, actor=actor, queries=keywords, n_comps=len(comps))
    if fallback_used:
        LAST_RUN["fallback_actor"] = fallback_used
        LAST_RUN["fallback_reason"] = "primary actor returned 0 comps (silent eBay block); used fallback"

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

    # --- Health gate ---------------------------------------------------
    # Comps in hand => nothing to disambiguate. Zero comps => the result is
    # ambiguous (thin market vs blocked backend) and MUST be resolved before
    # it reaches PRICE, because the two demand opposite responses: a thin
    # market is a real signal to price on, a blocked backend is no data at
    # all. Runs after save/status so a blocked run still leaves an audit
    # trail. NOTE: this gates total blocks only — a PARTIAL block that
    # returns a few junk comps still passes (see docs/pricing-backend-issues.md).
    if comps:
        LAST_RUN["verdict"] = "OK"
    elif not health_gate:
        LAST_RUN["verdict"] = "UNVERIFIED_EMPTY"
    else:
        health = check_backend_health(
            actor_id, timeout_sec=timeout_sec,
            include_fallback=(actor_id is None and use_fallback))
        LAST_RUN["health"] = health
        if health["healthy"]:
            LAST_RUN["verdict"] = "THIN"
        else:
            LAST_RUN["verdict"] = "BLOCKED"
            tried = ", ".join(
                f"{p['actor']} ({p['error'] or str(p['n_comps']) + ' comps'})"
                for p in health["probes"])
            raise BackendBlockedError(
                f"Stage B backend is BLOCKED, not thin: the query returned 0 comps and "
                f"the control query {control_query()!r} also returned fewer than "
                f"{CONTROL_MIN_COMPS} comps [tried: {tried}]. This is NOT evidence of a "
                f"thin market — treat Stage B as UNAVAILABLE and escalate to another "
                f"source (Terapeak / Chrome logged-in / local comp cache) before pricing.",
                health=health)

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
    parser.add_argument("--control", action="store_true",
                        help="Health check only: run the control query "
                             f"({DEFAULT_CONTROL_QUERY!r}, override with "
                             "APIFY_EBAY_CONTROL_QUERY) against the primary and "
                             "fallback actors and report whether Stage B can reach "
                             "eBay sold data at all. Exit 0 = healthy, 3 = BLOCKED. "
                             "Run this before trusting any 0-comp result.")
    parser.add_argument("--no-health-gate", action="store_true",
                        help="Do NOT re-probe with the control query when a search "
                             "returns 0 comps. Returns an unverified empty list "
                             "instead of raising on a blocked backend — you almost "
                             "never want this.")
    parser.add_argument("--no-fallback", action="store_true",
                        help="Disable the automatic fallback actor "
                             f"({DEFAULT_FALLBACK_ACTOR}) used when the primary "
                             "returns 0 comps (silent eBay block)")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of human-readable list")
    args = parser.parse_args()

    # --- Control path: is Stage B reachable at all? No search, no comps ---
    if args.control:
        try:
            health = check_backend_health(args.actor, timeout_sec=args.timeout,
                                          include_fallback=not args.no_fallback)
        except ApifyDisabledError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(health, indent=2))
        else:
            print(f"Stage B health check — control query: {health['control_query']!r} "
                  f"(healthy = >= {health['min_comps']} comps)")
            for p in health["probes"]:
                mark = "OK  " if p["healthy"] else "FAIL"
                detail = p["error"] or f"{p['n_comps']} comps"
                rid = f"  run {p['run_id']}" if p["run_id"] else ""
                print(f"  [{mark}] {p['actor']}: {detail}{rid}")
            if health["healthy"]:
                print("\nHEALTHY — Stage B can reach eBay sold data. A 0-comp search "
                      "result right now is a genuinely THIN market.")
            else:
                print("\nBLOCKED — every backend returned an empty control. Stage B is "
                      "UNAVAILABLE, NOT thin.\n  -> Do not price off Stage B. Escalate to "
                      "Terapeak / Chrome logged-in / the local comp cache.")
        sys.exit(0 if health["healthy"] else 3)

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
            use_fallback=not args.no_fallback,
            health_gate=not args.no_health_gate,
        )
    except BackendBlockedError as e:
        # MUST precede ApifyError (subclass). Exit 3 is distinct from a generic
        # failure (1) and a currency leak (2) so callers can branch on it.
        if LAST_RUN.get("run_id"):
            print(f"Apify run: {LAST_RUN['run_id']}  (actor {LAST_RUN.get('actor')})  — 0 comps")
        if LAST_RUN.get("saved_path"):
            print(f"Saved results: {LAST_RUN['saved_path']}")
        print(f"BACKEND BLOCKED: {e}", file=sys.stderr)
        for p in e.health.get("probes", []):
            print(f"  control probe {p['actor']}: "
                  f"{p['error'] or str(p['n_comps']) + ' comps'}", file=sys.stderr)
        sys.exit(3)
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
                    "thumbnail": c.thumbnail,
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
    if LAST_RUN.get("fallback_actor"):
        print(f"  -> FALLBACK USED: primary actor returned 0 comps (silent eBay block); "
              f"served by {LAST_RUN['fallback_actor']}")
    verdict = LAST_RUN.get("verdict")
    if verdict == "THIN":
        health = LAST_RUN.get("health") or {}
        print(f"  -> VERDICT: THIN (verified) — 0 comps, but the control query "
              f"{health.get('control_query')!r} returned data, so the backend is "
              f"healthy and this market really is empty. Safe to treat as thin.")
    elif verdict == "UNVERIFIED_EMPTY":
        print("  -> VERDICT: UNVERIFIED EMPTY — health gate disabled (--no-health-gate). "
              "0 comps here could be a thin market OR a blocked backend. Do not price "
              "on this without running --control.")
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
