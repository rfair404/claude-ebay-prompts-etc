#!/usr/bin/env python3
"""The unit_match filter for `--unit pair`.

Written after a live inversion on the ej-08-23 goldstone screw-back earrings
(fixtures here are that shoot's two saved Apify runs). The filter decided
"is this a pair?" off the literal token "pair", so it:

  * dropped all four earring-PAIR comps ("...Screw Back Earrings" never says
    the word "pair"), and
  * kept the one comp that is a necklace + earrings SET at $80.70.

Exactly backwards, and it collapsed the cohort to n=1. The fix decides the
unit off the item NOUN: an inherently-paired noun IS a pair, and a second item
class in the title (necklace + earrings) is not.

Run:  python tests/test_price_stats_unit.py
  or: pytest tests/test_price_stats_unit.py
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

NECKLACE_SET = ("Vintage AMCO 1/20 12K Gold Filled Goldstone Necklace "
                "Screw Back Earrings Set")


def _report(unit):
    return price_stats.price_from_runs(
        best_match_json=BM, price_high_json=PH,
        unit_type=unit, condition="used", price_field="total",
    )


def test_paired_noun_is_a_pair_without_the_word():
    assert price_stats.looks_pair_unit(
        "Vintage 12K Gold Filled Blue Goldstone Screw Back Earrings Oval")
    assert price_stats.looks_pair_unit("Sterling Silver Cuff Links Monogrammed")
    assert price_stats.looks_pair_unit("Pair Antique Brass Candlesticks")


def test_second_item_class_is_not_a_pair():
    assert not price_stats.looks_pair_unit(NECKLACE_SET)
    assert not price_stats.looks_pair_unit("Lot of 4 Vintage Clip Earrings")
    assert not price_stats.looks_pair_unit("Vintage Sterling Silver Brooch")


def test_pair_cohort_matches_the_single_cohort_on_this_shoot():
    """The regression: pair used to give n=1 (the $80.70 set) here."""
    pair = _report("pair")["distribution"]
    single = _report("single")["distribution"]
    assert pair["n"] == 3, pair
    assert pair["median"] == single["median"] == 35.95


def test_the_necklace_set_is_the_comp_that_gets_dropped():
    pair = _report("pair")
    log = [f for f in pair["filter_log"] if f["filter"] == "unit_match"][0]
    assert log["removed_count"] == 1, log
    assert log["removed"][0]["title"] == NECKLACE_SET
    # ...and every kept comp is a plain earring pair.
    assert len(pair["kept_comps"]) == 3
    for c in pair["kept_comps"]:
        assert "necklace" not in c["title"].lower()


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
