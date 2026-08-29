#!/usr/bin/env python3
"""Competitor charm-price convention (GH #82).

`charm_price_share` (lib/comps_core.py, folded into price_stats' run meta) is
a currency-leak guard over OUR OWN sold comps — it says charm pricing is
common in general, nothing about what a specific niche's competing stores are
doing right now. `competitor_charm_pattern` (lib/price_stats.py) buckets
ending-price patterns across the top competing sellers' ACTIVE listings (from
lib/ebay_browse.py) and, when there's a clear majority, prefers that ending
for the `recommended` tier instead of the generic charm default.

Two things have to hold:
  1. A real majority (>=60%, >=3 sellers, >=5 listings — the issue's own bar)
     is preferred over the generic default, and the nudge is traceable
     (`pre_charm_price` survives alongside the adjusted price).
  2. Anything short of that — too few competitors, or no majority ending —
     changes NOTHING: `recommended` is exactly today's median, and
     `charm_price_share`-only behavior (no competitor data at all) is
     unaffected.

Run:  python tests/test_competitor_charm_pricing.py
  or: pytest tests/test_competitor_charm_pricing.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import price_stats  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "price_stats_pair"
BM = FIX / "best_match.json"
PH = FIX / "price_high.json"


def _report(**kw):
    return price_stats.price_from_runs(
        best_match_json=BM, price_high_json=PH,
        unit_type="pair", condition="used", price_field="total", **kw,
    )


def _listings(pairs):
    """[(seller, price), ...] -> the ebay_browse.search() normalised shape."""
    return [{"seller": s, "askingPrice": p} for s, p in pairs]


# ---------------------------------------------------------------------------
# competitor_charm_pattern() — pure bucketing logic
# ---------------------------------------------------------------------------

def test_charm_ending_buckets():
    assert price_stats.charm_ending(34.99) == ".99"
    assert price_stats.charm_ending(20.00) == ".00"
    assert price_stats.charm_ending(18.97) == ".97"
    assert price_stats.charm_ending(9.95) == ".95"
    assert price_stats.charm_ending(18.50) == "other"


def test_apply_charm_ending_stays_within_a_dollar():
    assert price_stats.apply_charm_ending(34.50, ".99") == 34.99
    assert price_stats.apply_charm_ending(34.50, ".00") == 34.0
    assert price_stats.apply_charm_ending(19.60, ".99") == 19.99
    # unknown pattern is a no-op (just rounds)
    assert price_stats.apply_charm_ending(34.503, "other") == 34.5


def test_majority_pattern_found_and_returned():
    listings = _listings([
        ("alice", 12.99), ("bob", 22.99), ("carol", 8.99),
        ("dave", 15.99), ("erin", 30.00), ("frank", 40.99),
        ("gail", 5.99),
    ])
    r = price_stats.competitor_charm_pattern(listings)
    assert r["pattern"] == ".99"
    assert r["n_competitors"] == 7
    assert r["n_listings"] == 7
    assert r["share"] == round(6 / 7, 3)
    assert r["reason"] is None


def test_no_majority_falls_back():
    # Enough sellers/listings, but no single ending clears 60%.
    listings = _listings([
        ("alice", 12.99), ("bob", 22.00), ("carol", 8.97),
        ("dave", 15.95), ("erin", 30.99), ("frank", 40.00),
    ])
    r = price_stats.competitor_charm_pattern(listings)
    assert r["pattern"] is None
    assert r["n_competitors"] == 6
    assert "no majority" in r["reason"]


def test_too_few_sellers_falls_back_even_with_unanimous_price():
    # 2 sellers unanimously at .99 is NOT a decisive competitor signal —
    # the issue explicitly calls out guarding against exactly this.
    listings = _listings([("alice", 12.99), ("alice", 13.99), ("bob", 9.99)])
    r = price_stats.competitor_charm_pattern(listings)
    assert r["pattern"] is None
    assert r["n_competitors"] == 2
    assert "too few competitors" in r["reason"]


def test_too_few_listings_falls_back_even_with_enough_sellers():
    listings = _listings([("a", 9.99), ("b", 8.99), ("c", 7.99)])
    r = price_stats.competitor_charm_pattern(listings, min_listings=5)
    assert r["pattern"] is None
    assert "too few competitors" in r["reason"]


def test_accepts_sellerName_and_price_keys_too():
    # ebay_browse's shape uses seller/askingPrice; other saved shapes may use
    # sellerName/price (e.g. Apify-style comps) — both must compose.
    listings = [
        {"sellerName": "alice", "price": 12.99},
        {"sellerName": "bob", "price": 8.99},
        {"sellerName": "carol", "price": 22.99},
        {"sellerName": "dave", "price": 5.99},
        {"sellerName": "erin", "price": 30.99},
    ]
    r = price_stats.competitor_charm_pattern(listings)
    assert r["pattern"] == ".99"
    assert r["n_competitors"] == 5


def test_junk_prices_are_skipped_not_fatal():
    listings = _listings([("a", 9.99), ("b", 8.99), ("c", 7.99),
                          ("d", 6.99), ("e", None)])
    listings.append({"seller": "f", "askingPrice": "not-a-price"})
    r = price_stats.competitor_charm_pattern(listings, min_listings=4, min_sellers=4)
    assert r["n_listings"] == 4
    assert r["pattern"] == ".99"


# ---------------------------------------------------------------------------
# price_from_runs() — wired into the price-proposal logic
# ---------------------------------------------------------------------------

def test_recommended_is_nudged_when_a_majority_is_found():
    baseline = _report()["tiers"]["recommended"]["price"]  # 35.95 (whole-ish)
    listings = _listings([
        ("alice", 30.00), ("bob", 40.00), ("carol", 50.00),
        ("dave", 20.00), ("erin", 60.00), ("frank", 12.99),
    ])
    r = _report(competitor_listings=listings)
    cc = r["competitor_charm_pattern"]
    assert cc["pattern"] == ".00"
    rec = r["tiers"]["recommended"]
    assert price_stats.charm_ending(rec["price"]) == ".00"
    assert rec["price"] != baseline
    assert rec["pre_charm_price"] == baseline
    assert ".00" in rec["basis"]
    # the nudge never crosses the conservative floor
    assert rec["price"] >= r["tiers"]["conservative"]["price"]
    # other tiers are untouched by the nudge
    assert "pre_charm_price" not in r["tiers"]["conservative"]
    assert "pre_charm_price" not in r["tiers"]["push_high"]


def test_recommended_unchanged_when_no_majority():
    baseline = _report()
    listings = _listings([
        ("alice", 12.99), ("bob", 22.00), ("carol", 8.97),
        ("dave", 15.95), ("erin", 30.99), ("frank", 40.00),
    ])
    r = _report(competitor_listings=listings)
    assert r["competitor_charm_pattern"]["pattern"] is None
    assert r["tiers"]["recommended"] == baseline["tiers"]["recommended"]
    assert "pre_charm_price" not in r["tiers"]["recommended"]


def test_recommended_unchanged_when_too_few_competitors():
    baseline = _report()
    listings = _listings([("alice", 12.99), ("bob", 13.99)])
    r = _report(competitor_listings=listings)
    assert r["competitor_charm_pattern"]["pattern"] is None
    assert "too few competitors" in r["competitor_charm_pattern"]["reason"]
    assert r["tiers"]["recommended"] == baseline["tiers"]["recommended"]


def test_charm_price_share_only_behavior_is_unchanged_with_no_competitor_data():
    """The regression the issue calls out explicitly: our OWN charm_price_share
    (surfaced via run meta) keeps working exactly as before when this feature
    finds no competitor data at all — nothing here should touch it."""
    r = _report()
    assert r["runs"][0]["charm_price_share"] == 0.8
    cc = r["competitor_charm_pattern"]
    assert cc["pattern"] is None
    assert cc["n_competitors"] == 0
    assert cc["reason"] == "no competitor active-listing data supplied"
    assert r["tiers"]["recommended"]["price"] == 35.95
    assert "pre_charm_price" not in r["tiers"]["recommended"]


def test_competitor_charm_pattern_surfaced_even_when_thin():
    # Thin (n < THIN_N) sold-comp distribution still short-circuits to
    # tiers=None, but the competitor signal is independent of sold comps and
    # should still be computed and surfaced.
    listings = _listings([
        ("alice", 30.00), ("bob", 40.00), ("carol", 50.00), ("dave", 20.00),
        ("erin", 60.00),
    ])
    r = price_stats.price_from_runs(
        best_match_json=BM, unit_type="pair", condition="used",
        price_field="total", require_tokens=["nonexistent-token-xyz"],
        competitor_listings=listings,
    )
    assert r["confidence"] == "thin"
    assert r["tiers"] is None
    assert r["competitor_charm_pattern"]["pattern"] == ".00"


def test_competitor_active_json_loader():
    import json
    import tempfile
    listings = _listings([
        ("alice", 30.00), ("bob", 40.00), ("carol", 50.00),
        ("dave", 20.00), ("erin", 60.00),
    ])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "competitors.json"
        p.write_text(json.dumps({"listings": listings}), encoding="utf-8")
        r = _report(competitor_active_json=p)
    assert r["competitor_charm_pattern"]["pattern"] == ".00"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
