"""
price_stats — distribution-based PRICE tier engine (PRICE strategy v2).

Turns one or two saved Apify run JSON files into a defensible price: it
characterizes the *cleaned sold distribution* and places three tiers on it,
instead of hand-picking a few comps. See docs/price-strategy-v2.md.

The shift: from "pick the strongest comps" to "characterize the sold
distribution, then place tiers on it." This module owns the deterministic,
reproducible half — the filtering and the statistics — so PRICE's tiers
trace to a sample + percentiles, not a vibe. The reasoning half (exact-match
judgement, ceiling vetting prose, authenticity notes) stays in the prompt.

Inputs (saved by apify_ebay.save_run_json):
    --best-match   <run.json>   representative set (sort=best_match)
    --price-high   <run.json>   ceiling/outlier set (sort=price_high)
At least one is required; both is the v2 design (dual query per unit).

Pipeline (mirrors docs/price-strategy-v2.md "Normalize before any stats"):
    1. Flag (NOT strip) the price_high first row — often out of order; cause
       unconfirmed, so it's kept and surfaced for the human to judge.
    2. Unit match (biggest lever): pricing a `single`/`duplicate` excludes
       multi-item listings (lot/set/bundle/collection/N pcs/multiple years);
       pricing a `lot`/`set`/`pair` keeps the matching unit.
    3. Same-item core tokens: require the brand/type tokens from IDENTIFY in
       the comp title (drops loose keyword matches).
    4. Condition cohort: bucket New vs Used; price within the like-condition
       cohort (pool Used grades only when that cohort is thin).
    5. Exclusions: single-bid auctions, sub-threshold-feedback sellers.

Stats come off the cleaned best_match (representative) set; the price_high
set supplies the vetted ceiling. Every drop is logged, never silent.

Tier defaults (configurable constants — the proposal's open decisions):
    Conservative = 25th pct · Recommended = median · Push-high = vetted
    price_high ceiling, else 90th pct. Thin (n < 3) → caller falls back to
    the closest-comp method; this module says so rather than inventing
    percentiles off 2 points.

Standard library only (json, statistics) — runs in minimal/sandboxed envs.

CLI:
    python price_stats.py --best-match bm.json --price-high ph.json \
        --unit single --require-tokens needlepoint eagle --condition used
    python price_stats.py --best-match bm.json --json     # machine-readable

Programmatic:
    from price_stats import price_from_runs
    report = price_from_runs(best_match_json="bm.json", unit_type="single",
                             require_tokens=["needlepoint"], condition="used")
    print(report["tiers"]["recommended"])
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

# ---------------------------------------------------------------------------
# Tunable policy (the proposal's "Open decisions" — change here, one place)
# ---------------------------------------------------------------------------

CONSERVATIVE_PCT = 25       # no-objection floor percentile
RECOMMENDED_PCT = 50        # working-price percentile (median)
PUSH_HIGH_FALLBACK_PCT = 90  # ceiling when no price_high comp vets out

THIN_N = 3                  # n < THIN_N like-condition comps → fall back
GOOD_N = 8                  # n >= GOOD_N (and tight IQR) → confidence "good"
TIGHT_DISPERSION = 0.50     # IQR/median at/below this is "tight"

# A price_high item above OUTLIER_MULT × median that isn't clearly the same
# item/condition is an excluded outlier (bundle/mislisting/different model),
# not a ceiling comp. Logged, never silently dropped.
OUTLIER_MULT = 2.5

MIN_SELLER_FEEDBACK = 50    # below this feedback count → Tier-C exclusion

# Tokens that mark a listing as multi-item (a "lot"), used by the unit filter.
_MULTI_ITEM_WORDS = {
    "lot", "lots", "set", "sets", "bundle", "bundles", "collection",
    "complete", "pair", "pairs", "group", "grouping", "joblot",
}
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# A digit that touches a quote/inch mark (7" x 6") or a length unit (6 in) is
# a physical DIMENSION, not a quantity. Used to stop the x-multiplier count
# patterns from false-positiving on eBay size strings.
_DIM_AFTER = r'(?:["″′\']|\s*(?:in|inch|inches|cm|mm|ft|feet|foot)\b)'
# "5 pcs", "lot of 3", "x12", "12 pieces/issues/cards/vols". The x-multiplier
# forms ("x12" / "12x") only count when the number is NOT a dimension, so
# 7" x 6" / 13" X 10" stay single-item (see looks_multi_item doctests).
_COUNT_RE = re.compile(
    r'\b(?:'
    r'lot of\s*\d+'
    r'|set of\s*\d+'
    r'|\d+\s*(?:pcs?|pieces?|issues?|cards?|vol(?:ume)?s?|pc)'
    r'|x\s*\d+(?!' + _DIM_AFTER + r')'
    r'|(?<!["″′\'])\d+\s*x'
    r')\b',
    re.IGNORECASE,
)

# unit_type → does this unit WANT multi-item listings?
_MULTI_UNITS = {"lot", "set", "pair"}
_SINGLE_UNITS = {"single", "duplicate"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Comp:
    """A normalized comp, plus the bookkeeping the filters annotate."""
    title: str
    price: float                  # the price the stats run on (see price_field)
    sold_price: float
    total_price: Optional[float]
    url: str
    condition: Optional[str] = None
    sold_date: Optional[str] = None
    listing_type: Optional[str] = None
    bids_count: Optional[int] = None
    seller_feedback_score: Optional[int] = None
    source: str = "best_match"    # which run sort this came from
    is_row0: bool = False         # flagged first price_high row (kept, surfaced)
    drop_reason: Optional[str] = None  # set when a filter excludes it
    cohort: Optional[str] = None  # "new" / "used" bucket

    @property
    def dropped(self) -> bool:
        return self.drop_reason is not None


@dataclass
class FilterLog:
    """One filter's effect — what it removed and why (audit trail)."""
    name: str
    removed: list = field(default_factory=list)   # (title, price, reason)

    def add(self, comp: "Comp", reason: str) -> None:
        comp.drop_reason = reason
        self.removed.append((comp.title, comp.price, reason))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _condition_cohort(condition: Optional[str]) -> str:
    """Bucket an eBay condition label into 'new' / 'used' / 'unknown'."""
    if not condition:
        return "unknown"
    c = condition.lower()
    if "new" in c or "sealed" in c or "nwt" in c or "mint" in c:
        return "new"
    if any(w in c for w in ("used", "pre-owned", "preowned", "very good",
                            "good", "acceptable", "refurb", "open box",
                            "parts", "not work")):
        return "used"
    return "unknown"


# Titles of comps that asked for the delivered basis but had no shipping figure,
# so fell back to item-only. Module-level because _load_comps is called once per
# run-sort and the warning belongs to the whole run.
_DELIVERED_FALLBACKS: list[str] = []


def _load_comps(json_path: Union[str, Path], source: str,
                price_field: str) -> list["Comp"]:
    """Read a saved Apify run JSON into Comp objects.

    `source` labels the run sort (best_match / price_high). The first comp of
    a price_high run is flagged is_row0 (often out of order — kept, surfaced).
    """
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    comps: list[Comp] = []
    for i, c in enumerate(data.get("comps", [])):
        sold = c.get("sold_price")
        if sold is None:
            continue
        total = c.get("total_price")
        # A delivered-basis run that quietly substitutes the item-only price is
        # the worst kind of wrong: the header still says DELIVERED, but every
        # comp missing shipping drags the median down by its postage, and our
        # free-shipping list price is set against that median. The substitution
        # still happens (dropping the comp entirely would be worse on a thin
        # cohort) but it is COUNTED, and the caller shouts about it.
        fell_back = price_field == "total" and total is None
        if fell_back:
            _DELIVERED_FALLBACKS.append(str(c.get("title", ""))[:60])
        price = total if (price_field == "total" and total is not None) else sold
        comps.append(Comp(
            title=str(c.get("title", "")),
            price=float(price),
            sold_price=float(sold),
            total_price=float(total) if total is not None else None,
            url=str(c.get("url", "")),
            condition=c.get("condition"),
            sold_date=c.get("sold_date"),
            listing_type=c.get("listing_type"),
            bids_count=c.get("bids_count"),
            seller_feedback_score=c.get("seller_feedback_score"),
            source=source,
            is_row0=(source == "price_high" and i == 0),
            cohort=_condition_cohort(c.get("condition")),
        ))
    return comps


def _run_meta(json_path: Union[str, Path]) -> dict:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return {
        "run_id": data.get("run_id"),
        "queries": data.get("queries"),
        "charm_price_share": data.get("charm_price_share"),
        "currency_leak_suspected": data.get("currency_leak_suspected"),
        "path": str(json_path),
    }


# ---------------------------------------------------------------------------
# Filters (each annotates comps in place and returns a FilterLog)
# ---------------------------------------------------------------------------

def looks_multi_item(title: str) -> bool:
    """True if a title reads as a multi-item listing (lot/set/bundle/N pcs).

    The single-item biggest-lever filter: a `single` priced against lots is
    the classic skew the proposal targets (m-agni $10-$506 → $10-$144).

    Counts ("Lot of 5", "x12", "12x", "3 pcs") flag as multi-item; physical
    dimensions ('7" x 6"', '13" X 10"', '8 inches') do NOT — a digit touching
    a quote/inch mark or length unit is a measurement, not a quantity.

    >>> looks_multi_item('Lot of 5 Vintage Postcards')
    True
    >>> looks_multi_item('Set of 3 Brass Candlesticks')
    True
    >>> looks_multi_item('Magic The Gathering Cards x12')
    True
    >>> looks_multi_item('12x Vintage Brass Buttons')
    True
    >>> looks_multi_item('RARE Vintage Balinese Topeng Mask 7\" x 6\" Needs Repair')
    False
    >>> looks_multi_item('Antique Wood Panel 13\" X 10\" Hand Carved')
    False
    >>> looks_multi_item('Sterling Silver Spoon 8 inches Long')
    False
    """
    t = title.lower()
    words = set(re.findall(r"[a-z]+", t))
    if words & _MULTI_ITEM_WORDS:
        return True
    if _COUNT_RE.search(title):
        return True
    # Multiple DISTINCT years in one title → almost always a multi-year lot.
    years = set(_YEAR_RE.findall(title))
    if len(years) > 1:
        return True
    return False


def filter_unit(comps: list["Comp"], unit_type: str) -> FilterLog:
    """Keep only comps whose selling-unit matches the item being priced."""
    log = FilterLog("unit_match")
    unit = (unit_type or "single").lower()
    for c in comps:
        if c.dropped:
            continue
        multi = looks_multi_item(c.title)
        if unit in _SINGLE_UNITS and multi:
            log.add(c, f"multi-item listing; pricing a {unit}")
        elif unit in _MULTI_UNITS and not multi:
            log.add(c, f"single-item listing; pricing a {unit}")
    return log


def filter_tokens(comps: list["Comp"], require_tokens: list[str]) -> FilterLog:
    """Require every core token (brand/type from IDENTIFY) in the comp title."""
    log = FilterLog("same_item_tokens")
    tokens = [t.lower() for t in (require_tokens or []) if t.strip()]
    if not tokens:
        return log
    for c in comps:
        if c.dropped:
            continue
        title = c.title.lower()
        missing = [t for t in tokens if t not in title]
        if missing:
            log.add(c, f"title missing core token(s): {', '.join(missing)}")
    return log


def filter_condition(comps: list["Comp"], target: Optional[str],
                     pool_used_when_thin: bool = True) -> tuple[FilterLog, str]:
    """Keep comps in the item's condition cohort. Returns (log, cohort_used).

    If the strict target cohort is thin (< THIN_N), and pool_used_when_thin,
    Used grades pool together rather than starve the sample.
    """
    log = FilterLog("condition_cohort")
    if not target:
        return log, "any"
    target_cohort = _condition_cohort(target)
    if target_cohort == "unknown":
        return log, "any"

    live = [c for c in comps if not c.dropped]
    in_cohort = [c for c in live if c.cohort == target_cohort]
    # Thin strict cohort + Used target → pool all Used grades (already same
    # bucket here, so "pool" mainly means: don't also drop unknown-condition).
    pooled = (pool_used_when_thin and target_cohort == "used"
              and len(in_cohort) < THIN_N)
    cohort_used = f"{target_cohort} (pooled w/ unknown — thin)" if pooled else target_cohort
    for c in live:
        if c.cohort == target_cohort:
            continue
        if pooled and c.cohort == "unknown":
            continue
        log.add(c, f"condition '{c.condition or '?'}' not in {target_cohort} cohort")
    return log, cohort_used


def filter_exclusions(comps: list["Comp"]) -> FilterLog:
    """Tier-C exclusions: single-bid auctions, sub-threshold-feedback sellers."""
    log = FilterLog("exclusions")
    for c in comps:
        if c.dropped:
            continue
        lt = (c.listing_type or "").lower()
        if "auction" in lt and c.bids_count is not None and c.bids_count <= 1:
            log.add(c, "single-bid auction (one bidder set the price)")
        elif (c.seller_feedback_score is not None
              and c.seller_feedback_score < MIN_SELLER_FEEDBACK):
            log.add(c, f"seller feedback {c.seller_feedback_score} < {MIN_SELLER_FEEDBACK}")
    return log


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in 0..100) on pre-sorted values."""
    if not sorted_vals:
        raise ValueError("percentile of empty data")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def distribution(prices: list[float]) -> dict:
    """Core distribution stats for a cleaned price set."""
    vals = sorted(prices)
    n = len(vals)
    if n == 0:
        return {"n": 0}
    med = statistics.median(vals)
    p25 = percentile(vals, 25)
    p75 = percentile(vals, 75)
    iqr = p75 - p25
    return {
        "n": n,
        "min": round(vals[0], 2),
        "max": round(vals[-1], 2),
        "median": round(med, 2),
        "p25": round(p25, 2),
        "p75": round(p75, 2),
        "iqr": round(iqr, 2),
        # Dispersion: IQR/median (robust). Higher = wider/less confident.
        "dispersion": round(iqr / med, 3) if med else None,
        # 1.5×IQR fence — anything outside is an outlier to vet.
        "fence_low": round(p25 - 1.5 * iqr, 2),
        "fence_high": round(p75 + 1.5 * iqr, 2),
    }


def _confidence(n: int, dist: dict) -> str:
    if n < THIN_N:
        return "thin"
    disp = dist.get("dispersion")
    if n >= GOOD_N and disp is not None and disp <= TIGHT_DISPERSION:
        return "good"
    return "partial"


# ---------------------------------------------------------------------------
# Ceiling vetting (price_high extremes vs the representative median)
# ---------------------------------------------------------------------------

def vet_ceiling(price_high_kept: list["Comp"], median: float) -> tuple[Optional["Comp"], bool, list[dict]]:
    """Pick the highest surviving price_high comp as the ceiling candidate.

    The unit/token/condition filters already ran on these, so a survivor is a
    plausible same-item top sale. The MODULE does not judge "clearly
    comparable" (that's the prompt's call) — it surfaces the highest survivor
    as the ceiling candidate and, when that candidate exceeds OUTLIER_MULT ×
    median, flags it `needs_vetting` so the prompt confirms it's the same
    item/condition (else treats it as a bundle/mislisting and falls back to
    the percentile). Every above-multiple survivor is returned in
    `to_vet` — logged, never silently dropped.

    Returns (ceiling_candidate, needs_vetting, to_vet).
    """
    to_vet: list[dict] = []
    survivors = sorted(price_high_kept, key=lambda x: x.price, reverse=True)
    for c in survivors:
        if median and c.price > OUTLIER_MULT * median:
            to_vet.append({
                "title": c.title, "price": c.price, "url": c.url,
                "ratio": round(c.price / median, 2) if median else None,
                "reason": (f">{OUTLIER_MULT}× median (${median}) — confirm same "
                           f"item/condition; else a bundle/mislisting/different model"),
                "is_row0": c.is_row0,
            })
    ceiling = survivors[0] if survivors else None
    needs_vetting = bool(ceiling and median and ceiling.price > OUTLIER_MULT * median)
    return ceiling, needs_vetting, to_vet


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def price_from_runs(
    *,
    best_match_json: Optional[Union[str, Path]] = None,
    price_high_json: Optional[Union[str, Path]] = None,
    unit_type: str = "single",
    require_tokens: Optional[list[str]] = None,
    condition: Optional[str] = None,
    price_field: str = "sold",
    pool_used_when_thin: bool = True,
) -> dict:
    """Run the full distribution-based pricing pipeline. Returns a report dict.

    The report carries: run provenance, the filter log (every drop + reason),
    the cleaned distribution stats, the vetted ceiling + excluded outliers,
    the three tiers (each with its basis), and a confidence label. When the
    cleaned representative set is thin (n < THIN_N) the report sets
    tiers=None and confidence='thin' — the caller falls back to the
    closest-comp method rather than trust percentiles off too few points.
    """
    if not best_match_json and not price_high_json:
        raise ValueError("provide at least one of best_match_json / price_high_json")

    runs: list[dict] = []
    bm: list[Comp] = []
    ph: list[Comp] = []
    if best_match_json:
        bm = _load_comps(best_match_json, "best_match", price_field)
        runs.append({"sort": "best_match", **_run_meta(best_match_json)})
    if price_high_json:
        ph = _load_comps(price_high_json, "price_high", price_field)
        runs.append({"sort": "price_high", **_run_meta(price_high_json)})

    # If only price_high is available (observed: best_match can be empty while
    # price_high returns data), use it as the representative set too — flagged.
    rep = bm if bm else ph
    rep_source = "best_match" if bm else "price_high"

    all_for_filter = rep  # filters annotate the representative set
    logs: list[FilterLog] = []
    logs.append(filter_unit(all_for_filter, unit_type))
    logs.append(filter_tokens(all_for_filter, require_tokens or []))
    cond_log, cohort_used = filter_condition(all_for_filter, condition, pool_used_when_thin)
    logs.append(cond_log)
    logs.append(filter_exclusions(all_for_filter))

    kept = [c for c in all_for_filter if not c.dropped]
    row0_flags = [
        {"title": c.title, "price": c.price, "url": c.url}
        for c in (ph or rep) if c.is_row0
    ]

    dist = distribution([c.price for c in kept])
    n = dist.get("n", 0)
    conf = _confidence(n, dist)

    report: dict[str, Any] = {
        "runs": runs,
        "unit_type": unit_type,
        "require_tokens": require_tokens or [],
        "condition_target": condition,
        "condition_cohort_used": cohort_used,
        "price_field": price_field,
        "representative_source": rep_source,
        "n_raw": len(rep),
        "n_kept": n,
        "filter_log": [
            {"filter": lg.name, "removed_count": len(lg.removed),
             "removed": [{"title": t, "price": p, "reason": r} for t, p, r in lg.removed]}
            for lg in logs
        ],
        "row0_flags": row0_flags,
        "distribution": dist,
        "confidence": conf,
        "kept_comps": [
            {"title": c.title, "price": c.price, "url": c.url,
             "condition": c.condition, "sold_date": c.sold_date,
             "source": c.source}
            for c in sorted(kept, key=lambda x: x.price)
        ],
    }

    if conf == "thin":
        report["tiers"] = None
        report["thin_fallback"] = (
            f"n={n} (< {THIN_N}) after filters — too few to trust percentiles. "
            f"Fall back to the closest-comp / era-peer method; widen the bracket; "
            f"flag rarity. Do NOT compute tiers off {n} point(s)."
        )
        return report

    median = dist["median"]
    # Vet the ceiling from the price_high set (filtered the same way), else the
    # representative set's own high side.
    ph_pool = ph if ph else rep
    # Apply the same unit/token/condition gate to the ceiling candidates.
    filter_unit(ph_pool, unit_type)
    filter_tokens(ph_pool, require_tokens or [])
    filter_condition(ph_pool, condition, pool_used_when_thin)
    filter_exclusions(ph_pool)
    ph_kept = [c for c in ph_pool if not c.dropped]
    ceiling, needs_vetting, to_vet = vet_ceiling(ph_kept, median)

    sorted_kept = sorted(c.price for c in kept)
    p90 = percentile(sorted_kept, PUSH_HIGH_FALLBACK_PCT)
    push_high = ceiling.price if ceiling else p90
    if ceiling and needs_vetting:
        ceiling_basis = (
            f"ceiling candidate from price_high (VET — confirm same item/cond, "
            f"else use ${round(p90, 2)}): \"{ceiling.title[:55]}\" ({ceiling.url})")
    elif ceiling:
        ceiling_basis = f"vetted ceiling comp from price_high: \"{ceiling.title[:55]}\" ({ceiling.url})"
    else:
        ceiling_basis = f"no price_high comp survived filters → {PUSH_HIGH_FALLBACK_PCT}th percentile fallback"
    report["tiers"] = {
        "conservative": {
            "price": round(percentile(sorted_kept, CONSERVATIVE_PCT), 2),
            "basis": f"{CONSERVATIVE_PCT}th percentile of like-condition sold (no-objection floor)",
        },
        "recommended": {
            "price": round(percentile(sorted_kept, RECOMMENDED_PCT), 2),
            "basis": "median of like-condition comparable sold (working price)",
        },
        "push_high": {
            "price": round(push_high, 2),
            "basis": ceiling_basis,
            "needs_vetting": needs_vetting,
            "fallback_if_unvetted": round(p90, 2) if needs_vetting else None,
        },
    }
    report["ceiling_comp"] = (
        {"title": ceiling.title, "price": ceiling.price, "url": ceiling.url,
         "needs_vetting": needs_vetting}
        if ceiling else None
    )
    report["ceiling_candidates_to_vet"] = to_vet
    return report


# ---------------------------------------------------------------------------
# Human-readable rendering
# ---------------------------------------------------------------------------

def render(report: dict) -> str:
    """Render a report dict as the text block PRICE folds into price.txt."""
    out: list[str] = []
    d = report["distribution"]
    runs = " + ".join(
        f"{r['sort']}={r.get('run_id', '?')}" for r in report["runs"]
    )
    out.append(f"Distribution ({report['representative_source']} set, "
               f"condition={report['condition_cohort_used']}, "
               f"unit={report['unit_type']}):")
    if d.get("n"):
        out.append(f"  n={d['n']}  median=${d['median']}  "
                   f"IQR=${d['p25']}–${d['p75']}  min/max=${d['min']}/${d['max']}  "
                   f"dispersion={d.get('dispersion')}")
    out.append(f"  raw={report['n_raw']} → kept={report['n_kept']}  "
               f"runs: {runs}")
    out.append(f"  Confidence: {report['confidence']}")

    if report["confidence"] == "thin":
        out.append("")
        out.append("  THIN: " + report["thin_fallback"])
        return "\n".join(out)

    t = report["tiers"]
    out.append("")
    out.append("Tiers (distribution-based):")
    out.append(f"  Conservative  ${t['conservative']['price']}  — {t['conservative']['basis']}")
    out.append(f"  Recommended   ${t['recommended']['price']}  — {t['recommended']['basis']}")
    out.append(f"  Push-high     ${t['push_high']['price']}  — {t['push_high']['basis']}")

    if report.get("row0_flags"):
        out.append("")
        out.append("  Row-0 flag (price_high first row — often out of order; KEPT, judge it):")
        for r in report["row0_flags"]:
            out.append(f"    • ${r['price']} — \"{r['title'][:60]}\" — {r['url']}")

    if report.get("ceiling_candidates_to_vet"):
        out.append("")
        out.append("  Ceiling candidates to VET (high vs median — confirm same item/cond):")
        for o in report["ceiling_candidates_to_vet"]:
            out.append(f"    • ${o['price']} (×{o.get('ratio')}) — \"{o['title'][:60]}\" "
                       f"— {o['reason']} — {o['url']}")

    dropped = [(lg["filter"], r) for lg in report["filter_log"] for r in lg["removed"]]
    if dropped:
        out.append("")
        out.append(f"  Filters removed {len(dropped)} comp(s):")
        for filt, r in dropped:
            out.append(f"    • [{filt}] ${r['price']} — \"{r['title'][:50]}\" — {r['reason']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(
        description="Distribution-based PRICE tiers from saved Apify run JSON")
    ap.add_argument("--best-match", help="Saved Apify run JSON (sort=best_match)")
    ap.add_argument("--price-high", help="Saved Apify run JSON (sort=price_high)")
    ap.add_argument("--unit", default="single",
                    help="unit_type being priced (single/pair/set/lot/duplicate)")
    ap.add_argument("--require-tokens", nargs="*", default=[],
                    help="Core brand/type tokens that MUST appear in a comp title")
    ap.add_argument("--condition", help="Item condition cohort to price within (e.g. used / new)")
    ap.add_argument("--price-field", default="sold", choices=["sold", "total"],
                    help="Price the stats run on: item sold price (default) or sold+shipping")
    ap.add_argument("--no-pool-used", action="store_true",
                    help="Do NOT pool Used grades when the strict cohort is thin")
    ap.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = ap.parse_args()

    if not args.best_match and not args.price_high:
        ap.error("provide --best-match and/or --price-high")

    try:
        report = price_from_runs(
            best_match_json=args.best_match,
            price_high_json=args.price_high,
            unit_type=args.unit,
            require_tokens=args.require_tokens,
            condition=args.condition,
            price_field=args.price_field,
            pool_used_when_thin=not args.no_pool_used,
        )
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        report["delivered_fallbacks"] = list(_DELIVERED_FALLBACKS)
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
        if _DELIVERED_FALLBACKS:
            n = len(_DELIVERED_FALLBACKS)
            print(f"\n⚠ DELIVERED BASIS INCOMPLETE — {n} comp(s) had no shipping figure "
                  f"and were counted at their ITEM-ONLY price:", file=sys.stderr)
            for t in _DELIVERED_FALLBACKS[:5]:
                print(f"    • {t}", file=sys.stderr)
            if n > 5:
                print(f"    … and {n - 5} more", file=sys.stderr)
            print("  The tiers above are therefore a MIXED basis and read LOW by roughly "
                  "the missing postage.\n  Usual cause: eBay changed the delivery wording "
                  "the extractor matches — check lib/ebay_sold_browse.py --js.",
                  file=sys.stderr)


if __name__ == "__main__":
    _cli()
