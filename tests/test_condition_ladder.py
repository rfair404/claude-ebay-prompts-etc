"""eBay's three-rung used ladder (jewelry, apparel): 2990 / 3000 / 3010.

The ladder's outer rungs have Sell API names (PRE_OWNED_EXCELLENT,
PRE_OWNED_FAIR). Before they were known, an as-is jewelry lot could only be
listed as "Pre-owned - Good" — a grade above what it was.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import list_edit  # noqa: E402
from ebay_schema import CONDITION_ENUM  # noqa: E402

# Measured on a jewelry mixed-lots category, 2026-10-08.
JEWELRY = {1000, 1500, 1750, 2990, 3000, 3010}


def test_ladder_names_are_valid_draft_conditions():
    for name in ("PRE_OWNED_EXCELLENT", "PRE_OWNED_FAIR"):
        assert name in list_edit._VALID_CONDITIONS
        assert name in CONDITION_ENUM


def test_ladder_ids_round_trip():
    assert list_edit._COND_ENUM_TO_ID["PRE_OWNED_FAIR"] == 3010
    assert list_edit._COND_ENUM_TO_ID["PRE_OWNED_EXCELLENT"] == 2990
    assert list_edit._COND_ID_TO_ENUM[3010] == "PRE_OWNED_FAIR"
    assert list_edit._COND_ID_TO_ENUM[2990] == "PRE_OWNED_EXCELLENT"


def test_fair_is_accepted_unchanged_on_the_ladder():
    assert list_edit._remap_condition_for_category("PRE_OWNED_FAIR", JEWELRY) == ("PRE_OWNED_FAIR", None)


def test_acceptable_lands_on_fair_not_good():
    enum, why = list_edit._remap_condition_for_category("USED_ACCEPTABLE", JEWELRY)
    assert enum == "PRE_OWNED_FAIR" and why


def test_good_lands_on_good():
    enum, _ = list_edit._remap_condition_for_category("USED_GOOD", JEWELRY)
    assert enum == "USED_EXCELLENT"  # id 3000, shown as "Pre-owned - Good" here


def test_old_used_family_still_prefers_generic_used():
    enum, _ = list_edit._remap_condition_for_category("USED_ACCEPTABLE", {1000, 3000, 7000})
    assert enum == "USED_EXCELLENT"


def test_for_parts_still_refused_on_the_ladder():
    try:
        list_edit._remap_condition_for_category("FOR_PARTS_OR_NOT_WORKING", JEWELRY)
    except list_edit.EbayAPIError:
        return
    raise AssertionError("7000 should have no fallback on a jewelry category")
