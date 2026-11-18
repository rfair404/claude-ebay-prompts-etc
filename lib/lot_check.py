"""IDENTIFY lot-attribution check: every item belongs to exactly one lot, the
one its folder says, and identify.txt must name it.

The lot is read from where the item sits under inventory/ (same rules as
tools/report_lot.py): walking up from the item, the nearest context.txt with
`part_of: <lot>` names that lot; one with `client:` is a consignment lot of its
own; plain buckets roll up to the outermost bucket. identify.txt carries a
`Lot:` line in its SHOOT SUMMARY header (before the first `--- Item` line);
a `Lot:` further down is item prose and is neither read nor overwritten. A missing line, a line that disagrees with
the folder, or an item under no context.txt at all blocks IDENTIFY, so a
ledger row can never inherit a wrong lot.

    python -m lib.lot_check <item-dir>            # verify, exit 1 on a problem
    python -m lib.lot_check <item-dir> --stamp    # write the folder's lot into identify.txt

Shoots outside inventory/ (scratch dirs) have no lot and pass.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "inventory"

_KEY = re.compile(r"^\s*(client|part_of)\s*:\s*(.*?)\s*$", re.I)
_LOT_LINE = re.compile(r"^\s*Lot\s*:\s*(.*?)\s*$", re.I | re.M)
_FIRST_ITEM = re.compile(r"^\s*-{2,}\s*Item\b", re.I | re.M)


def _split_header(text: str) -> tuple[str, str]:
    """(SHOOT SUMMARY header, rest): the header ends at the first `--- Item`."""
    m = _FIRST_ITEM.search(text)
    return (text[:m.start()], text[m.start():]) if m else (text, "")


def _keys(ctx: Path) -> dict:
    out: dict = {}
    for line in ctx.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _KEY.match(line)
        if m and m.group(2) and m.group(1).lower() not in out:
            out[m.group(1).lower()] = m.group(2).strip().strip("/")
    return out


def _label(d: Path, inv: Path) -> str:
    return d.resolve().relative_to(inv.resolve()).as_posix()


def resolve_lot(item_dir: Path, inventory: Optional[Path] = None) -> Optional[str]:
    """The lot an item folder belongs to, or None when no context.txt claims it.
    Raises ValueError when the item is not under the inventory root."""
    inv = Path(inventory or INVENTORY).resolve()
    cur = Path(item_dir).resolve()
    if cur != inv and inv not in cur.parents:
        raise ValueError(f"{item_dir} is not under {inv}")
    outer: Optional[Path] = None
    while True:
        ctx = cur / "context.txt"
        if ctx.is_file():
            k = _keys(ctx)
            if k.get("part_of"):
                return k["part_of"]
            if k.get("client"):
                return _label(cur, inv)
            outer = cur
        if cur == inv:
            break
        cur = cur.parent
    return _label(outer, inv) if outer else None


def recorded_lot(shoot: Path) -> Optional[str]:
    f = Path(shoot) / "identify.txt"
    if not f.is_file():
        return None
    header, _ = _split_header(f.read_text(encoding="utf-8", errors="replace"))
    m = _LOT_LINE.search(header)
    return m.group(1).strip().strip("/") if m and m.group(1) else None


def problems(shoot: Path, inventory: Optional[Path] = None) -> list[str]:
    """Reasons identify.txt's lot attribution can't be trusted; [] when clean
    or when the shoot isn't under inventory/."""
    try:
        want = resolve_lot(shoot, inventory)
    except ValueError:
        return []
    if want is None:
        return ["no context.txt above this item names its lot; add one (or "
                "`part_of: <lot>`) before IDENTIFY."]
    have = recorded_lot(shoot)
    if have is None:
        return [f"identify.txt has no `Lot:` line; its folder says {want}. "
                f"Run `python -m lib.lot_check {shoot} --stamp`."]
    if have != want:
        return [f"identify.txt says Lot: {have} but the folder belongs to {want}. "
                "Fix the file or move the item; never let the ledger inherit it."]
    return []


def stamp(shoot: Path, inventory: Optional[Path] = None) -> str:
    """Write (or correct) the `Lot:` line from the folder; returns the lot."""
    want = resolve_lot(shoot, inventory)
    if want is None:
        raise SystemExit("no context.txt names this item's lot; add one first.")
    f = Path(shoot) / "identify.txt"
    header, rest = _split_header(f.read_text(encoding="utf-8"))
    line = f"Lot:              {want}"
    if _LOT_LINE.search(header):
        header = _LOT_LINE.sub(lambda _m: line, header, count=1)
    elif "SHOOT SUMMARY\n" in header:
        header = header.replace("SHOOT SUMMARY\n", f"SHOOT SUMMARY\n{line}\n", 1)
    else:
        header = f"{line}\n{header}"
    f.write_text(header + rest, encoding="utf-8")
    return want


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("item")
    ap.add_argument("--stamp", action="store_true")
    a = ap.parse_args(argv)
    item = Path(a.item)
    if not item.is_absolute() and not item.exists():
        item = INVENTORY / item
    if a.stamp:
        print(f"Lot: {stamp(item)}")
        return 0
    errs = problems(item)
    for e in errs:
        print(f"LOT CHECK: {e}")
    if not errs:
        print("lot check: ok")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
