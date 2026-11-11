#!/usr/bin/env python3
"""unit_match `--unit pair` keeps two-item "sets" and "pair lots".

Written after C093 (Knickerbocker Raggedy Ann & Andy dolls, 2026-11-10;
fixture is that item's eBay sold comps). The filter dropped 5 of 10 genuine
pairs because their titles said "SET" or "Pair Lot", including the top comp
($42.95 "All Original SET"), so the tool's median fell to $20.50 and the
price-researcher had to place tiers by hand.

Run:  python tests/test_price_stats_pair_sets.py
  or: pytest tests/test_price_stats_pair_sets.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import price_stats  # noqa: E402

BM = (Path(__file__).resolve().parent / "fixtures" / "price_stats_pair_c093"
      / "best_match.json")


def _report():
    return price_stats.price_from_runs(
        best_match_json=BM, unit_type="pair", condition="used",
        price_field="sold",
    )


def test_every_c093_pair_survives_unit_match():
    dist = _report()["distribution"]
    assert dist["n"] == 10, dist
    assert dist["max"] == 42.95, dist


def test_two_item_set_wording_is_a_pair():
    for title in (
        "Vintage Raggedy Ann & Andy Dolls 1970's Knickerbocker 15\" - All Original SET",
        "Knickerbocker Raggedy Ann & Andy 15in Cloth Doll Pair Lot 1970s Taiwan",
        "Vintage Raggedy Ann and Andy Doll Set 14\" Tall Knickerbocker",
        "Lot of 2 Raggedy Ann & Andy Dolls",
        "Set of 2 Brass Candlesticks",
    ):
        assert price_stats.looks_pair_unit(title), title


def test_true_multi_item_lots_still_drop():
    for title in (
        "Lot of 3 Raggedy Ann & Andy Dolls",
        "Raggedy Ann & Andy 3 Pc Doll Set",
        "Raggedy Ann & Andy Dolls Sets",
        "Raggedy Ann & Andy Doll Collection",
        "Lot of 4 Vintage Clip Earrings",
        "Raggedy Ann Doll Set",  # one name + "set": not shown to be two items
    ):
        assert not price_stats.looks_pair_unit(title), title


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
