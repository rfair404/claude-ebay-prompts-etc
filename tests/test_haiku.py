#!/usr/bin/env python3
"""tools/haiku.py — pick-sheet haiku (GH #161).

Every line in the curated pools is checked against the module's own
syllable heuristic so the 5-7-5 shape can't silently drift as lines are
added or edited, and the generator is checked for determinism (same order
id -> same haiku, so re-rendering a pick sheet doesn't reshuffle the poem)
and for never leaking the buyer's name (which the generator never even
receives).

Run:  python tests/test_haiku.py
  or: pytest tests/test_haiku.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import haiku                                                       # noqa: E402


def test_line1_pools_are_five_syllables():
    for category, lines in haiku.LINE1_BY_CATEGORY.items():
        for line in lines:
            assert haiku.line_syllables(line) == 5, (category, line)


def test_line2_pools_are_seven_syllables():
    for region, lines in haiku.LINE2_BY_REGION.items():
        for line in lines:
            assert haiku.line_syllables(line) == 7, (region, line)


def test_line3_pool_is_five_syllables():
    for line in haiku.LINE3_CLOSING:
        assert haiku.line_syllables(line) == 5, line


def test_generate_haiku_returns_three_lines():
    lines = haiku.generate_haiku("03-11111-22222", "Pokemon TCG Booster Box", "OH")
    assert len(lines) == 3
    assert all(isinstance(line, str) and line for line in lines)


def test_generate_haiku_is_deterministic_per_order():
    a = haiku.generate_haiku("03-11111-22222", "Pokemon TCG Booster Box", "OH")
    b = haiku.generate_haiku("03-11111-22222", "Pokemon TCG Booster Box", "OH")
    assert a == b


def test_generate_haiku_varies_by_order():
    a = haiku.generate_haiku("03-11111-11111", "A Board Game", "OH")
    b = haiku.generate_haiku("03-22222-22222", "A Board Game", "OH")
    assert a != b


def test_generate_haiku_never_contains_buyer_name():
    lines = haiku.generate_haiku("03-11111-22222", "Vintage Funko Pop Figure", "CA")
    text = " ".join(lines).lower()
    for forbidden in ("jamie", "buyer", "mike", "springfield"):
        assert forbidden not in text


def test_category_detection():
    assert haiku._category_for_title("Pokemon TCG Booster Box") == "cards"
    assert haiku._category_for_title("Funko Pop Vinyl Figure") == "toys"
    assert haiku._category_for_title("USB Charger Cable") == "electronics"
    assert haiku._category_for_title("Vintage Comic Book") == "books"
    assert haiku._category_for_title("Wooden Jigsaw Puzzle") == "games"
    assert haiku._category_for_title("McCoy Beehive Mixing Bowl") == "general"


def test_region_detection():
    assert haiku._region_for_state("NY") == "northeast"
    assert haiku._region_for_state("OH") == "midwest"
    assert haiku._region_for_state("TX") == "south"
    assert haiku._region_for_state("CA") == "west"
    assert haiku._region_for_state("") == "other"
    assert haiku._region_for_state("zz") == "other"


def test_generate_haiku_handles_missing_inputs():
    lines = haiku.generate_haiku("", "", "")
    assert len(lines) == 3


if __name__ == "__main__":
    import inspect

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
