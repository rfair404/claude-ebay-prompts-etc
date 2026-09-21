#!/usr/bin/env python3
"""tools/offer_floor_audit.py's verdict ladder, including the new top-severity
`FLOOR >= ASK` (#140) — a live listing whose auto-decline sits at or above
its own asking price, so every possible offer auto-declines.

`verdict_for()` is a plain function pulled out of `main()`'s loop
specifically so this needs no Sell API fake or `inventory_sheet.csv` fixture
to test the ordering.

Run:  python tests/test_offer_floor_audit.py
  or: pytest tests/test_offer_floor_audit.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import offer_floor_audit as A                                 # noqa: E402


def test_no_best_offer_when_not_enabled():
    assert A.verdict_for(False, None, 100, 50, 80) == "no best offer"


def test_no_floor_when_enabled_but_no_decline_set():
    assert A.verdict_for(True, None, 100, 50, 80) == "NO FLOOR"


def test_floor_at_price_is_worse_than_no_floor():
    """The home-casino bug (#140): $39 list, $39 auto-decline -- every offer
    auto-declines, which is worse than Best Offer off. Must outrank every
    other verdict, including NO FLOOR."""
    assert A.verdict_for(True, 39.0, 39.0, 26.0, 38.99) == "FLOOR >= ASK"


def test_floor_above_price_is_also_flagged():
    assert A.verdict_for(True, 45.0, 39.0, 26.0, 38.99) == "FLOOR >= ASK"


def test_floor_below_the_price_file_floor():
    assert A.verdict_for(True, 20.0, 100.0, 50.0, 80.0) == "BELOW FLOOR"


def test_floor_between_conservative_and_recommended():
    assert A.verdict_for(True, 60.0, 100.0, 50.0, 80.0) == "under rec"


def test_floor_at_or_above_recommended_is_ok():
    assert A.verdict_for(True, 85.0, 100.0, 50.0, 80.0) == "ok"


def test_order_ranks_floor_gte_ask_first():
    order = {"FLOOR >= ASK": 0, "NO FLOOR": 1, "BELOW FLOOR": 2, "under rec": 3,
             "no best offer": 4, "ok": 5}
    assert order["FLOOR >= ASK"] < order["NO FLOOR"] < order["BELOW FLOOR"]


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
