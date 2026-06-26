#!/usr/bin/env python3
"""Regression guard for the marble_crop CLIP gate — NON-MARBLE CROPS ARE NOT
ACCEPTABLE.

This pins the two real false positives that slipped through the geometric
detector and got indexed/priced as marbles (a size-reference dime and a patch of
background fabric). If anyone weakens the gate, this test fails loudly.

Run:  python tests/test_marble_gate.py     (standalone, prints a table)
  or: pytest tests/test_marble_gate.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
FIX = Path(__file__).resolve().parent / "fixtures" / "marble_gate"

REAL = sorted(FIX.glob("real_*.jpg"))          # must PASS the gate
NOT_MARBLE = sorted(FIX.glob("notmarble_*.jpg"))  # must be REJECTED


def _verdicts():
    from PIL import Image
    from marble_crop import is_marble
    rows = []
    for p in REAL + NOT_MARBLE:
        ok, sp, sn = is_marble(Image.open(p).convert("RGB"))
        rows.append((p.name, ok, sp, sn, p in REAL))
    return rows


def test_real_marbles_pass():
    from PIL import Image
    from marble_crop import is_marble
    assert REAL, "no real_* fixtures found"
    for p in REAL:
        ok, sp, sn = is_marble(Image.open(p).convert("RGB"))
        assert ok, f"real marble {p.name} was REJECTED (s_pos={sp:.3f} s_neg={sn:.3f})"


def test_non_marbles_rejected():
    from PIL import Image
    from marble_crop import is_marble
    assert NOT_MARBLE, "no notmarble_* fixtures found"
    for p in NOT_MARBLE:
        ok, sp, sn = is_marble(Image.open(p).convert("RGB"))
        assert not ok, f"NON-MARBLE {p.name} PASSED the gate (s_pos={sp:.3f} s_neg={sn:.3f})"


def test_detect_and_crop_drops_non_marbles_by_default():
    """End-to-end: the coin/fabric, dropped into the pipeline, must not survive
    detect_and_crop's default gate. (Re-uses the fixtures as 1-marble 'frames'.)"""
    from marble_crop import detect_and_crop
    for p in NOT_MARBLE:
        kept = detect_and_crop(str(p), min_frac=0.20, max_frac=0.95, circ=0.30)
        assert kept == [] or all(False for _ in kept), \
            f"{p.name} produced {len(kept)} crop(s) through the gated pipeline"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    rows = _verdicts()
    print(f"{'fixture':28} {'s_pos':>7} {'s_neg':>7} {'diff':>7}  result")
    ok_all = True
    for name, ok, sp, sn, is_real in rows:
        want_pass = is_real
        good = (ok == want_pass)
        ok_all &= good
        tag = ("PASS" if ok else "REJECT") + ("" if good else "  <<< WRONG")
        print(f"{name:28} {sp:7.3f} {sn:7.3f} {sp - sn:7.3f}  {tag}")
    print("\n" + ("ALL GOOD — gate accepts marbles, rejects non-marbles."
                  if ok_all else "FAILURE — gate misclassified a fixture."))
    sys.exit(0 if ok_all else 1)
