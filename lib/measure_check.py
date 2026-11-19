"""Jewelry size and weight are measured by Russell, never estimated.

A 1" miss on a chain or ring is a return. Every jewelry item in identify.txt
must carry `Measured size:` and `Measured weight:` lines holding a number and a
unit that Russell gave; anything else (blank, unknown, `~`, estimate, range,
ASK) blocks the IDENTIFY gate until he answers. An item that already has a
draft is past IDENTIFY and isn't re-checked.

    python -m lib.measure_check <shoot-dir>

Item blocks start with `--- Item N ---`. An item is jewelry when its Category
line names a jewelry noun as a whole word. Strong nouns (necklace, bracelet,
earring...) always count; weak ones that also name household things (ring,
chain, pin, charm) count only when the Category names nothing non-jewelry
(pliers, mug, glass, napkin ring...). Named household phrases never count.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

DRAFTS = ("draft.md", "draft_group.md")  # same as lot_check.DRAFTS

_STRONG = re.compile(
    r"\b(?:jewel(?:ry|lery|s)?|necklaces?|pendants?|bracelets?|bangles?|earrings?|"
    r"brooch(?:es)?|cuff ?links?|lockets?|anklets?|chokers?|tiaras?|watch fobs?)\b", re.I)
_WEAK = re.compile(r"\b(?:rings?|chains?|pins?|charms?)\b", re.I)
# Household phrases that use a jewelry word; never jewelry.
_NOT_PHRASE = re.compile(
    r"\b(?:napkin|curtain|shower|key|piston|o|split|drawer|door|teething|onion)[- ]?rings?\b|"
    r"\bring binders?\b|\bchain ?saws?\b|\b(?:bowling|rolling|clothes|safety|cotter)"
    r" pins?\b|\bpendant (?:lights?|lamps?|fixtures?)\b", re.I)
# Non-jewelry nouns; with one present a weak word (ring, chain...) doesn't count.
_NOT_JEWELRY = re.compile(
    r"\b(?:pliers?|mugs?|cups?|glass(?:es|ware)?|tumblers?|steins?|bowls?|plates?|"
    r"tools?|hardware|curtains?|napkins?|keys?|hooks?|dish(?:es)?|trays?|"
    r"binders?|toys?|games?)\b", re.I)


def _is_jewelry(category: str) -> bool:
    cat = _NOT_PHRASE.sub(" ", category)
    if _STRONG.search(cat):
        return True
    return bool(_WEAK.search(cat)) and not _NOT_JEWELRY.search(cat)


_ITEM_SPLIT = re.compile(r"^\s*-{2,}\s*Item\s+(\S+?)\s*-{2,}\s*$", re.I | re.M)
_FIELD = r"^\s*[-*]?\s*\**{}\**\s*:\s*\**\s*(.*?)\s*$"
_SIZE_UNIT = r"(?:in(?:ch(?:es)?)?|\"|mm|cm)"
_RING_SIZE = re.compile(r"(?:US\s*)?size\s*\d+(?:\.\d+)?(?:\s*1/2)?", re.I)
_WT_UNIT = r"(?:g|gr|gram|grams|dwt|oz|ozt|lb|lbs)"
_NUM = r"\d+(?:\.\d+)?(?:\s*/\s*\d+)?"
_BAD = re.compile(r"[~≈]|estimat|approx|unknown|\bask\b|tbd|\?|\bn/?a\b|\bor\b|\d\s*[–-]\s*\d", re.I)


def _field(block: str, name: str) -> str:
    m = re.search(_FIELD.format(re.escape(name)), block, re.I | re.M)
    return m.group(1) if m else ""


def _measured(val: str, unit: str) -> bool:
    if not val or _BAD.search(val):
        return False
    if unit == _SIZE_UNIT and _RING_SIZE.search(val):  # ring: "size 7", "US size 7 1/2"
        return True
    return bool(
        re.search(rf"{_NUM}\s*(?:x|×)?\s*{unit}\b|{_NUM}\s*{unit}(?![a-z])", val, re.I))


def problems(shoot: Path) -> list[str]:
    f = Path(shoot) / "identify.txt"
    if not f.is_file() or any((Path(shoot) / n).is_file() for n in DRAFTS):
        return []
    parts = _ITEM_SPLIT.split(f.read_text(encoding="utf-8", errors="replace"))
    out = []
    for num, block in zip(parts[1::2], parts[2::2]):
        if not _is_jewelry(_field(block, "Category")):
            continue
        for label, unit in (("Measured size", _SIZE_UNIT), ("Measured weight", _WT_UNIT)):
            if not _measured(_field(block, label), unit):
                out.append(f"Item {num} is jewelry: ask Russell for its {label.lower()} "
                           f"(a real number + unit, never an estimate) and write `{label}:`.")
    return out


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    errs = problems(Path(argv[0])) if argv else ["usage: measure_check <shoot-dir>"]
    for e in errs:
        print(f"MEASURE CHECK: {e}")
    if not errs:
        print("measure check: ok")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
