"""Jewelry size and weight are measured by Russell, never estimated.

A 1" miss on a chain or ring is a return. Every jewelry item in identify.txt
must carry `Measured size:` and `Measured weight:` lines holding a number and a
unit that Russell gave; anything else (blank, unknown, `~`, estimate, range,
ASK) blocks the IDENTIFY gate until he answers.

    python -m lib.measure_check <shoot-dir>

Item blocks start with `--- Item N ---`. An item is jewelry when its Category
or Type line names a jewelry word.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

_JEWELRY = re.compile(
    r"jewel|necklace|chain|pendant|bracelet|bangle|earring|brooch|\bpin\b|\bring\b|"
    r"charm|cufflink|locket|anklet|choker|tiara|watch fob", re.I)
_ITEM_SPLIT = re.compile(r"^\s*-{2,}\s*Item\s+(\S+?)\s*-{2,}\s*$", re.I | re.M)
_FIELD = r"^\s*[-*]?\s*\**{}\**\s*:\s*\**\s*(.*?)\s*$"
_SIZE_UNIT = r"(?:in(?:ch(?:es)?)?|\"|mm|cm)"
_WT_UNIT = r"(?:g|gr|gram|grams|dwt|oz|ozt|lb|lbs)"
_NUM = r"\d+(?:\.\d+)?(?:\s*/\s*\d+)?"
_BAD = re.compile(r"[~≈]|estimat|approx|unknown|\bask\b|tbd|\?|\bn/?a\b|\bor\b|\d\s*[–-]\s*\d", re.I)


def _field(block: str, name: str) -> str:
    m = re.search(_FIELD.format(re.escape(name)), block, re.I | re.M)
    return m.group(1) if m else ""


def _measured(val: str, unit: str) -> bool:
    return bool(val) and not _BAD.search(val) and bool(
        re.search(rf"{_NUM}\s*(?:x|×)?\s*{unit}\b|{_NUM}\s*{unit}(?![a-z])", val, re.I))


def problems(shoot: Path) -> list[str]:
    f = Path(shoot) / "identify.txt"
    if not f.is_file():
        return []
    parts = _ITEM_SPLIT.split(f.read_text(encoding="utf-8", errors="replace"))
    out = []
    for num, block in zip(parts[1::2], parts[2::2]):
        if not _JEWELRY.search(_field(block, "Category") + " " + _field(block, "Type")):
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
