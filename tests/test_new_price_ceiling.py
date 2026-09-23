#!/usr/bin/env python3
"""lib/price_stats.apply_new_price_ceiling — the `below_new` posture (GH #147).

Ceiling-first pricing treats the sold-comp ceiling as the market's top. That
is true for collectables, where nobody can make another one, and false for
anything still manufactured: the buyer's alternative is the retail box, with
a warranty and a return window. A junk store deals mostly in the second kind,
so its tiers get capped against NEW delivered.

The case worth protecting is the red flag. If sold comps sit AT or ABOVE the
new price, something upstream is wrong — wrong item, stale comps, or a bad
new-price reference. Silently capping would produce a plausible number and
bury the fault, so the flag is asserted here.

Run:  python tests/test_new_price_ceiling.py
  or: pytest tests/test_new_price_ceiling.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from price_stats import NEW_CEILING_PCT, apply_new_price_ceiling  # noqa: E402


def _tiers(cons, rec, high):
    return {
        "conservative": {"price": cons, "basis": "25th pct"},
        "recommended": {"price": rec, "basis": "median"},
        "push_high": {"price": high, "basis": "vetted ceiling"},
    }


# ---------------------------------------------------------------------------
# No new supply → the comp math is untouched.
# ---------------------------------------------------------------------------

def test_no_new_reference_leaves_tiers_alone():
    """Discontinued goods have no retail alternative — ceiling-first stands."""
    t = _tiers(20.0, 30.0, 45.0)
    out = apply_new_price_ceiling(t, None)
    assert out["tiers"] == t
    assert out["cap"] is None
    assert "no new-price reference" in out["notes"][0]


def test_zero_or_negative_new_price_is_treated_as_no_reference():
    out = apply_new_price_ceiling(_tiers(20.0, 30.0, 45.0), 0)
    assert out["cap"] is None


# ---------------------------------------------------------------------------
# The cap bites only where it must.
# ---------------------------------------------------------------------------

def test_tiers_under_the_cap_are_not_raised():
    """A cap is a ceiling, never a target — cheap comps stay cheap."""
    out = apply_new_price_ceiling(_tiers(10.0, 15.0, 20.0), 100.0)
    assert out["cap"] == 75.0
    assert [t["price"] for t in out["tiers"].values()] == [10.0, 15.0, 20.0]
    assert out["notes"] == []


def test_only_the_offending_tier_is_capped():
    # cap = 75. push_high 90 is over; the rest are not.
    out = apply_new_price_ceiling(_tiers(40.0, 60.0, 90.0), 100.0)
    assert out["tiers"]["conservative"]["price"] == 40.0
    assert out["tiers"]["recommended"]["price"] == 60.0
    assert out["tiers"]["push_high"]["price"] == 75.0


def test_capped_tier_keeps_its_original_number_for_the_record():
    out = apply_new_price_ceiling(_tiers(40.0, 60.0, 90.0), 100.0)
    assert out["tiers"]["push_high"]["uncapped_price"] == 90.0
    assert "was $90.0" in out["tiers"]["push_high"]["basis"]


def test_the_cap_is_explained_in_the_notes():
    """A different number with no stated reason is how a report loses trust."""
    out = apply_new_price_ceiling(_tiers(40.0, 60.0, 90.0), 100.0)
    assert any("push_high: $90.0 -> $75.0" in n for n in out["notes"])
    assert any("buyable new at $100.0 delivered" in n for n in out["notes"])


def test_pct_is_overridable_per_storefront():
    out = apply_new_price_ceiling(_tiers(40.0, 60.0, 90.0), 100.0, pct=0.5)
    assert out["cap"] == 50.0
    assert out["tiers"]["recommended"]["price"] == 50.0


def test_default_pct_is_the_documented_three_quarters():
    assert NEW_CEILING_PCT == 0.75


# ---------------------------------------------------------------------------
# The red flag: used does not outsell new.
# ---------------------------------------------------------------------------

def test_comps_at_or_above_new_raise_the_flag():
    out = apply_new_price_ceiling(_tiers(60.0, 90.0, 120.0), 100.0)
    assert out["above_new"] is True
    assert any("RED FLAG" in n for n in out["notes"])


def test_the_flag_fires_at_exactly_the_new_price_too():
    out = apply_new_price_ceiling(_tiers(60.0, 80.0, 100.0), 100.0)
    assert out["above_new"] is True


def test_normal_comps_do_not_raise_the_flag():
    out = apply_new_price_ceiling(_tiers(40.0, 60.0, 90.0), 100.0)
    assert out["above_new"] is False
    assert not any("RED FLAG" in n for n in out["notes"])


def test_the_flag_names_both_numbers_being_compared():
    """An operator has to be able to tell WHICH side is wrong."""
    out = apply_new_price_ceiling(_tiers(60.0, 90.0, 120.0), 100.0)
    flag = next(n for n in out["notes"] if "RED FLAG" in n)
    assert "$120.0" in flag and "$100.0" in flag


def test_flagged_tiers_are_still_capped():
    """The flag is a warning, not a bypass — the number stays defensible."""
    out = apply_new_price_ceiling(_tiers(60.0, 90.0, 120.0), 100.0)
    assert out["tiers"]["push_high"]["price"] == 75.0


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
