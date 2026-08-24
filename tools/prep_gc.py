#!/usr/bin/env python3
"""Sweep the regenerable byproducts PREP leaves behind, across every shoot.

`python -m lib.photo_prep.prep <shoot> --gc` is the per-shoot form. This is the
same thing over the whole tree, which is how the debt actually got here: nobody
was going to run it 250 times by hand, so it never ran at all.

Two classes, and they are NOT the same risk:

  UNREFERENCED   preset renders no manifest entry points at. A leak — narrowing
                 `--only` or the shoot's category on a re-run strands whatever
                 the earlier, wider pass wrote. Unreadable by any code path.
                 `--apply` now sweeps these itself; this catches the backlog.

  SUPERSEDED     the looks that lost the pick, and `.prep/ask/` panels whose
                 frames have all been answered. Real artifacts of a real
                 decision, so they only go once the shoot is APPROVED — before
                 that the unchosen looks ARE the gate.

`inventory/` is gitignored. There is no undo, so this prints and stops unless
you pass --force.

    python tools/prep_gc.py                     # what would go, tree-wide
    python tools/prep_gc.py --unreferenced-only # just the leak, safe anywhere
    python tools/prep_gc.py --force             # do it
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from photo_prep.prep import (load_manifest, _dir_bytes, _mb,          # noqa: E402
                             _sweep_unreferenced_presets)

MB = 1048576


def _unreferenced(shoot: Path, m: dict) -> tuple[int, list[Path]]:
    """What _sweep_unreferenced_presets WOULD take, without taking it."""
    root = shoot / ".prep" / "presets"
    if not root.is_dir():
        return 0, []
    named, refs = set(), set()
    for rec in (m.get("photos") or {}).values():
        for pname, entry in (rec.get("presets") or {}).items():
            named.add(pname)
            refs.add((shoot / entry["path"]).resolve())
    total, hits = 0, []
    for pdir in sorted(root.iterdir()):
        if not pdir.is_dir():
            continue
        if pdir.name not in named:
            total += _dir_bytes(pdir); hits.append(pdir); continue
        for f in pdir.rglob("*"):
            if f.is_file() and f.resolve() not in refs:
                total += f.stat().st_size; hits.append(f)
    return total, hits


def _superseded(shoot: Path, m: dict) -> tuple[int, list[tuple[Path, str]]]:
    """Only meaningful for an approved shoot; returns (0, []) otherwise."""
    if not m.get("approved"):
        return 0, []
    chosen = m.get("chosen_preset")
    out, total = [], 0
    root = shoot / ".prep" / "presets"
    if root.is_dir():
        for pdir in sorted(root.iterdir()):
            if pdir.is_dir() and pdir.name != chosen:
                total += _dir_bytes(pdir)
                out.append((pdir, f"unchosen look (kept: {chosen})"))
    ask = shoot / ".prep" / "ask"
    if ask.is_dir() and not any((r.get("orientation") or {}).get("needs_ask")
                                for r in (m.get("photos") or {}).values()):
        total += _dir_bytes(ask)
        out.append((ask, "ask panels, all answered"))
    return total, out


def main() -> int:
    import shutil
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(REPO / "inventory"))
    ap.add_argument("--force", action="store_true", help="actually delete (no undo)")
    ap.add_argument("--unreferenced-only", action="store_true", dest="unref_only",
                    help="skip the approved-shoot sweep; take only the pure leak")
    ap.add_argument("--quiet", action="store_true", help="totals only")
    a = ap.parse_args()

    root = Path(a.root)
    leak = sup = 0
    n_leak = n_sup = n_shoots = 0
    skipped_unapproved = 0

    for pj in sorted(root.rglob(".prep/prep.json")):
        shoot = pj.parent.parent
        try:
            m = load_manifest(shoot)
        except (OSError, json.JSONDecodeError):
            print(f"  ! unreadable manifest: {pj}")
            continue
        if not m.get("photos"):
            continue
        n_shoots += 1

        ub, uhits = _unreferenced(shoot, m)
        if ub:
            leak += ub; n_leak += 1
            if not a.quiet:
                print(f"{shoot.relative_to(root)}: leak {_mb(ub):>10}  "
                      f"({len(uhits)} path(s) no manifest names)")
            if a.force:
                _sweep_unreferenced_presets(shoot, m, quiet=True)

        if a.unref_only:
            continue
        sb, shits = _superseded(shoot, m)
        if not m.get("approved"):
            skipped_unapproved += 1
        if sb:
            sup += sb; n_sup += 1
            if not a.quiet:
                print(f"{shoot.relative_to(root)}: superseded {_mb(sb):>10}")
                for d, why in shits:
                    print(f"    {'rm' if a.force else 'would rm'} "
                          f"{str(d.relative_to(shoot)):24} {_mb(_dir_bytes(d)):>10}  {why}")
            if a.force:
                for d, _ in shits:
                    shutil.rmtree(d)

    print()
    print(f"shoots with a manifest        : {n_shoots}")
    print(f"unreferenced (leak)           : {n_leak:4} shoots  {leak / MB:8.1f} MB")
    if not a.unref_only:
        print(f"superseded in approved shoots : {n_sup:4} shoots  {sup / MB:8.1f} MB")
        print(f"held back, shoot not approved : {skipped_unapproved:4} shoots")
    print(f"{'FREED' if a.force else 'WOULD FREE'}                    : "
          f"{(leak + sup) / MB:8.1f} MB   (regenerate any of it with `--apply`)")
    if not a.force:
        print("\ndry run — add --force to actually remove")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
