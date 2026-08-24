#!/usr/bin/env python3
"""Score tesseract's OSD against the human looks recorded beside it.

Issue #21 asked one question before any refactor: why was OSD confidently wrong
on five catalog frames? This answers it with the corpus rather than the anecdote.

THE LABELS. Every manifest that carries both an `osd_angle` and a `vision_angle`
for the same frame is a labelled example: OSD guessed, and then a person looked
and recorded what they saw. Where the two differ, OSD was wrong -- the recorded
look outranks it by policy (`orientation.resolve`), so a disagreement is not a
tie, it is a correction.

THE CONFOUND, STATED UP FRONT. A recorded look is not perfect ground truth.
Issue #21 also documents a relative-vs-absolute `--rotate` bug that moved some
frames more than once, so a handful of these `vision_angle`s are themselves
corrupted. That noise is real and it is why the headline agreement rate should
be read as approximate. It cannot, however, manufacture the monotonic
relationship between confidence and agreement that this tool reports, which is
the part the threshold rests on.

Run:  python tools/osd_audit.py
      python tools/osd_audit.py --root inventory --bands
"""
import argparse
import glob
import json
import os
import re
import sys

BANDS_ORIENT = [0, 2, 3, 4, 6, 999]
BANDS_SCRIPT = [0, 1, 2, 3, 5, 999]


def collect(root: str) -> list:
    """(script_conf, orient_conf, agreed, shoot, frame) per labelled frame."""
    rows = []
    pattern = os.path.join(root, "**", ".prep", "prep.json")
    for f in glob.glob(pattern, recursive=True):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        for name, rec in (d.get("photos") or {}).items():
            o = rec.get("orientation") or {}
            osd, vis = o.get("osd_angle"), o.get("vision_angle")
            if osd is None or vis is None:
                continue
            m = re.search(r"script conf ([0-9.]+)", o.get("osd_note") or "")
            if not m:
                continue
            rows.append((float(m.group(1)), float(o.get("osd_conf") or 0.0),
                         osd == vis, os.path.dirname(os.path.dirname(f)), name))
    return rows


def _rate(sel) -> float:
    return 100.0 * sum(r[2] for r in sel) / len(sel) if sel else 0.0


def bands(rows, idx, edges, label):
    print(f"\nagreement by {label}")
    for lo, hi in zip(edges, edges[1:]):
        sel = [r for r in rows if lo <= r[idx] < hi]
        if sel:
            print(f"  {lo:4.1f}-{hi:5.1f}  n={len(sel):-3d}  agree {_rate(sel):3.0f}%")


def sweep(rows, idx, label, current):
    print(f"\n{label} threshold sweep (current floor {current})")
    print("  thresh  kept  %kept  agree   wrong answers still taken")
    for t in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0):
        sel = [r for r in rows if r[idx] >= t]
        if not sel:
            continue
        wrong = len(sel) - sum(r[2] for r in sel)
        print(f"   {t:4.1f}   {len(sel):3d}   {100*len(sel)/len(rows):3.0f}%   "
              f"{_rate(sel):3.0f}%    {wrong}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="inventory")
    ap.add_argument("--bands", action="store_true", help="also print per-band tables")
    ap.add_argument("--list-wrong", action="store_true",
                    help="every frame where OSD disagreed with the recorded look")
    a = ap.parse_args(argv)

    rows = collect(a.root)
    if not rows:
        print(f"no labelled frames under {a.root!r} "
              "(a frame needs both an osd_angle and a vision_angle)")
        return 1

    print(f"labelled frames (OSD answered AND a look was recorded): {len(rows)}")
    print(f"OSD agreed with the look: {_rate(rows):.0f}%")

    if a.bands:
        bands(rows, 1, BANDS_ORIENT, "orientation confidence")
        bands(rows, 0, BANDS_SCRIPT, "script confidence")

    sweep(rows, 1, "orientation confidence", 1.5)

    if a.list_wrong:
        print("\nframes where OSD disagreed with the recorded look")
        for r in sorted(r for r in rows if not r[2]):
            print(f"  orient={r[1]:5.2f} script={r[0]:4.2f}  {r[3]} / {r[4]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
