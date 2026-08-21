#!/usr/bin/env python3
"""Record orientation answers by sheet index.

Answers come back from the combined ask sheets as indices, so this maps them to
shoot+frame via index.json and applies them per shoot in one pass. Default is 0
("looked at it, upright as shown") for a whole range; exceptions are given
explicitly as IDX=DEG.

    python tools/prep_answer.py --index docs/ask/index.json --range 1-48
    python tools/prep_answer.py --index docs/ask/index.json --range 49-96 --set 55=90 61=180
"""
import argparse, json, subprocess, sys
from collections import defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--index", required=True)
ap.add_argument("--range", required=True, help="e.g. 1-48")
ap.add_argument("--set", nargs="*", default=[], help="IDX=DEG exceptions")
a = ap.parse_args()

idx = json.loads(Path(a.index).read_text(encoding="utf-8"))
lo, hi = (int(x) for x in a.range.split("-"))
exc = {}
for s in a.set:
    k, v = s.split("=")
    exc[int(k)] = int(v)

by_shoot = defaultdict(list)
for i in range(lo, hi + 1):
    e = idx.get(str(i))
    if not e:
        continue
    by_shoot[e["shoot"]].append((e["frame"], exc.get(i, 0)))

for shoot, frames in sorted(by_shoot.items()):
    cmd = [sys.executable, "-m", "lib.photo_prep.prep", shoot, "--rotate"] + \
          [f"{f}={d}" for f, d in frames]
    p = subprocess.run(cmd, capture_output=True, text=True)
    nz = [f"{f}={d}" for f, d in frames if d]
    print(f"  {shoot:52} {len(frames):3} frames" + (f"  rotated: {', '.join(nz)}" if nz else ""))
    if p.returncode:
        print(f"     FAILED: {(p.stdout+p.stderr).strip()[-160:]}")
