#!/usr/bin/env python3
"""tg7_triage — move a camera import's frames to where they belong, and log it.

tools/tg7_import.sh drops each TG-7 import's new JPGs into a staging lot
inventory/tg7-<date>-<time>/ and starts the inventory-manager on it
unattended ("Camera import triage" in .claude/agents/inventory-manager.md).
This is the one shell command that run has: it moves frames out of the lot
into item folders, records frames that stay in the lot, and prints the line
the import's finish pop-up shows. Every frame it handles gets a row in the
lot's triage.csv (frame, kind, dest, note).

    python tools/tg7_triage.py move inventory/tg7-20261024-1412 inventory/bin-07/03-tin-car \\
        P1010004.JPG P1010005.JPG --kind item --note "bin-07/catalog.csv#03"
    python tools/tg7_triage.py keep inventory/tg7-20261024-1412 P1010001.JPG --kind overview
    python tools/tg7_triage.py summary inventory/tg7-20261024-1412
    python tools/tg7_triage.py add inventory/tg7-20261024-1412/catalog.csv \
        --description "tin toy car, red" --photos "P1010004 P1010005" \
        --hint "possible duplicate of K01-C042"

`add` is the one way a new catalog row is made (lib/dupe_guard.py). When one
row on record has the same item folder, or a frame no other row has, the
object is that row: its frames go onto it and no row is made. Otherwise the
row (and its folder with intake.txt) is made, with a POSSIBLE_DUPE line in
dupe_queue.csv when a row reads alike. `--new "<reason>"` never attaches;
`--attach <key>` attaches to the row named.

It never deletes or overwrites a file, and it moves only within inventory/.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import Counter
from pathlib import Path

KINDS = ("overview", "item", "lot", "split", "other")
LOG = "triage.csv"
SUMMARY = "TRIAGE.txt"
FIELDS = ("frame", "kind", "dest", "note")


def _lot(path: str) -> Path:
    lot = Path(path).resolve()
    if not lot.is_dir() or lot.parent.name != "inventory":
        sys.exit(f"not a lot folder directly under inventory/: {path}")
    return lot


def _frames(lot: Path, names: list[str]) -> list[Path]:
    out = []
    for name in dict.fromkeys(names):
        f = lot / name
        if Path(name).name != name or not f.is_file():
            sys.exit(f"no frame {name!r} in {lot.name}/")
        out.append(f)
    return out


def _dest(lot: Path, path: str) -> Path:
    """An item folder somewhere under the same inventory/, never the lot itself,
    inventory/ itself or a _private folder (inventory/_live, _consign)."""
    inv = lot.parent
    dest = Path(path).resolve()
    try:
        rel = dest.relative_to(inv)
    except ValueError:
        sys.exit(f"destination is outside inventory/: {path}")
    if not rel.parts or rel.parts[0].startswith("_") or dest == lot:
        sys.exit(f"not an item folder: {path}")
    if dest.exists() and not dest.is_dir():
        sys.exit(f"destination is a file: {path}")
    return dest


def _log(lot: Path, rows: list[dict]) -> None:
    log = lot / LOG
    new = not log.exists()
    with log.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def _rel(lot: Path, p: Path) -> str:
    return p.relative_to(lot.parent.parent).as_posix()


def move(lot: Path, dest: Path, frames: list[Path], kind: str, note: str) -> None:
    taken = [f.name for f in frames if (dest / f.name).exists()]
    if taken:
        sys.exit(f"already in {_rel(lot, dest)}/: {', '.join(taken)} (nothing moved)")
    dest.mkdir(parents=True, exist_ok=True)
    for f in frames:
        shutil.move(str(f), str(dest / f.name))
    _log(lot, [{"frame": f.name, "kind": kind, "dest": _rel(lot, dest), "note": note}
               for f in frames])
    print(f"moved {len(frames)} frame(s) -> {_rel(lot, dest)}/")


def keep(lot: Path, frames: list[Path], kind: str, note: str) -> None:
    _log(lot, [{"frame": f.name, "kind": kind, "dest": _rel(lot, lot), "note": note}
               for f in frames])
    print(f"kept {len(frames)} frame(s) in {lot.name}/ as {kind}")


def logged(lot: Path) -> dict[str, dict]:
    """The last row for each frame in the lot's triage.csv."""
    log = lot / LOG
    if not log.exists():
        return {}
    with log.open(newline="", encoding="utf-8") as fh:
        return {r["frame"]: r for r in csv.DictReader(fh)}


def summary(lot: Path) -> str:
    """The pop-up line: the agent's first TRIAGE.txt line, else counts by kind,
    plus how many frames in the lot were never sorted."""
    rows = logged(lot)
    unsorted = sorted(f.name for f in lot.iterdir()
                      if f.is_file() and f.suffix.upper() == ".JPG" and f.name not in rows)
    line = ""
    s = lot / SUMMARY
    if s.exists():
        line = next((ln.strip() for ln in s.read_text(encoding="utf-8").splitlines()
                     if ln.strip()), "")
    if not line and rows:
        n = Counter(r["kind"] for r in rows.values())
        line = f"{len(rows)} frame(s) sorted: " + ", ".join(
            f"{n[k]} {k}" for k in KINDS if n[k])
    if not line:
        line = "nothing sorted"
    if unsorted:
        line += f" · {len(unsorted)} frame(s) unsorted"
    return line


def add(a: argparse.Namespace) -> None:
    # Imported here so move/keep/summary need nothing beyond this file.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import dupe_guard  # one object, one record

    cat = Path(a.catalog).resolve()
    if cat.name != dupe_guard.CATALOG or "inventory" not in cat.parts[:-1]:
        sys.exit(f"not a catalog.csv under inventory/: {a.catalog}")
    inv = Path(*cat.parts[:len(cat.parts) - cat.parts[::-1].index("inventory")])
    scope = dupe_guard.scope_files(inv, cat)
    cand = {k: v for k, v in {
        "description": a.description, "bin": a.bin, "qty": a.qty, "marks": a.marks,
        "condition": a.condition, "photos": a.photos, "inventory_folder": a.folder,
        "why": a.why, "hint": a.hint}.items() if v}
    out = dupe_guard.add_catalog_row(cat, cand, scope, force_new=a.new,
                                     attach_to=a.attach, make_folder=not a.no_folder)
    best = out.decision.best
    if out.action in ("attached", "unchanged"):
        print(f"ATTACH {out.key}" + (" (nothing new)" if out.action == "unchanged" else
                                     ": frames added, re-run IDENTIFY")
              + (f" · {best.label()}" if best else ""))
    else:
        print(f"NEW {out.key}" + (f" -> {dupe_guard.rel(out.path)}/" if out.path else "")
              + (" (--new)" if a.new else ""))
        if out.decision.action == "possible" and not a.new:
            print(f"POSSIBLE_DUPE in {cat.parent.name}/{dupe_guard.QUEUE}: "
                  + "; ".join(m.label() for m in out.decision.matches[:3]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("move", help="move frames from the lot into an item folder")
    m.add_argument("lot")
    m.add_argument("dest")
    k = sub.add_parser("keep", help="log frames that stay in the lot (overview, split, other)")
    k.add_argument("lot")
    for p in (m, k):
        p.add_argument("frames", nargs="+")
        p.add_argument("--kind", required=True, choices=KINDS)
        p.add_argument("--note", default="")
    s = sub.add_parser("summary", help="print the finish pop-up line")
    s.add_argument("lot")
    n = sub.add_parser("add", help="add a catalog row, unless the object is already "
                                   "on record (then its frames go onto that row)")
    n.add_argument("catalog", help="the bin's catalog.csv (made if missing)")
    n.add_argument("--description", required=True)
    for f in ("bin", "qty", "marks", "condition", "photos", "why"):
        n.add_argument(f"--{f}", default="")
    n.add_argument("--folder", default="", help="its item folder, when it has one")
    n.add_argument("--hint", default="", help="triage's 'possible duplicate of ...' note")
    g = n.add_mutually_exclusive_group()
    g.add_argument("--new", default="", metavar="REASON",
                   help="add the row anyway; the reason goes in its `why`")
    g.add_argument("--attach", default="", metavar="KEY",
                   help="attach to this row (K01-C008, <lot>/catalog.csv#03)")
    n.add_argument("--no-folder", action="store_true",
                   help="no item folder (a consigned bin: folders come at the shoot)")
    a = ap.parse_args(argv)

    if a.cmd == "add":
        add(a)
        return 0
    lot = _lot(a.lot)
    if a.cmd == "summary":
        print(summary(lot))
    elif a.cmd == "move":
        move(lot, _dest(lot, a.dest), _frames(lot, a.frames), a.kind, a.note)
    else:
        keep(lot, _frames(lot, a.frames), a.kind, a.note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
