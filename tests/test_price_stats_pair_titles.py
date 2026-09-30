#!/usr/bin/env python3
"""unit_match `--unit pair` on truncated titles and multi-pair sets.

Written after the COV-929 10k CZ earrings run (2026-09-29; fixture is that
run's saved Stage B capture, trimmed). Every title in the capture was cut at
70 chars, so the earring noun often arrived as "Earrin" / "Ear" / "Ea" and
the filter:

  * dropped 12 ordinary single pairs as "not a single pair" (the mm/gram
    size tokens next to them looked like the cause but were not), and
  * kept "2 Pair Sets" / "3 Pair Sets" listings, then chose the $73.47
    three-pair set as the Push-high ceiling.

Correct cohort: n=29, median $49.99 (the tool gave n=21, median $48.99).

Run:  python tests/test_price_stats_pair_titles.py
  or: pytest tests/test_price_stats_pair_titles.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import price_stats  # noqa: E402

BM = (Path(__file__).resolve().parent / "fixtures" / "price_stats_pair_10k"
      / "best_match.json")

# Single pairs with a size token: full titles, as eBay lists them.
SINGLE_PAIRS_FULL = [
    "10K Solid Yellow Gold 6mm Round Cut Clear Crystal CZ Solitaire Hook Earrings",
    "10K .97 Gram Solid Yellow Gold 6mm Round Blue Crystal CZ Solitaire Earrings",
    "10K Solid Yellow Gold 5mm Heart Clear Crystal CZ Solitaire Hook Earrings",
    "10K .78 Gram 5mm Fine Solid Yellow Gold Princess Solitaire Clear CZ Earrings",
]
# ...and as this capture delivered them (cut at 70 chars).
SINGLE_PAIRS_TRUNCATED = [
    "10K Solid Yellow Gold 6mm Round Cut Clear Crystal CZ Solitaire Hook Ea",
    "10K .97 Gram Solid Yellow Gold 6mm Round Blue Crystal CZ Solitaire Ear",
    "10K Solid Yellow Gold 5mm Heart Clear Crystal CZ Solitaire Hook Earrin",
    "10K .78 Gram 5mm Fine Solid Yellow Gold Princess Solitaire Clear CZ Ea",
    "1 Ct 10k Yellow Gold Princess Cut Cubic Zirconia Square Solitaire Stud",
]
MULTI_PAIRS = [
    "New 10K 3 Pair Sets Solid White Gold 5mm Round Clear Crystal CZ Earrin",
    "10K 2 Pair Sets Fine Solid Yellow Gold 3mm Round Clear Crystal CZ Earr",
    "New 10K 2 Pair Sets Solid White Gold 5mm Round Clear Crystal CZ Earrin",
    "10K Yellow Gold CZ Stud Earrings Lot of 3 Pairs 2mm 3mm Round Solitair",
]
SINGLE_EARRING = ("Single 14K SOLID GOLD Solitaire Stud Earring 10K Back Round "
                  "CZ ~6.5mm")


def _report():
    return price_stats.price_from_runs(
        best_match_json=BM, unit_type="pair", condition="used",
        price_field="total", require_tokens=["10k"],
    )


def test_mm_and_gram_sizes_are_not_quantities():
    for t in SINGLE_PAIRS_FULL:
        assert price_stats.looks_pair_unit(t), t


def test_truncated_paired_noun_still_names_a_pair():
    for t in SINGLE_PAIRS_TRUNCATED:
        assert price_stats.looks_pair_unit(t), t
    # A short title ending in a noun fragment was not truncated.
    assert not price_stats.names_paired_item("Vintage Brass Ear")


def test_counted_pairs_are_not_one_pair():
    for t in MULTI_PAIRS:
        assert not price_stats.looks_pair_unit(t), t
    assert not price_stats.looks_pair_unit("Two Pairs Sterling Hoop Earrings")


def test_single_earring_is_not_a_pair():
    assert not price_stats.looks_pair_unit(SINGLE_EARRING)
    assert not price_stats.looks_pair_unit("Sterling Silver 1 Earring Replacement")
    # "Earrings Set" is still one pair.
    assert price_stats.looks_pair_unit("Sterling Silver Hoop Earrings Set")


def test_cohort_matches_the_hand_count():
    r = _report()
    d = r["distribution"]
    assert d["n"] == 29, d
    assert d["median"] == 49.99, d
    kept = {c["title"] for c in r["kept_comps"]}
    for t in MULTI_PAIRS + [SINGLE_EARRING[:70]]:
        assert t not in kept, t
    for t in SINGLE_PAIRS_TRUNCATED:
        assert t in kept, t


def test_multi_pair_set_is_not_the_ceiling():
    ceiling = _report().get("ceiling_comp") or {}
    assert "pair sets" not in str(ceiling.get("title", "")).lower(), ceiling
    assert ceiling.get("price") != 73.47, ceiling


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
