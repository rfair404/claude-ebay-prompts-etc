#!/usr/bin/env python3
"""Find photos that shipped with the colour drained out of them.

Written after a measured failure. On the christmas-train shoot the segmenter
handed a red-painted car to the backdrop, and the backdrop pass — doing exactly
what it is supposed to do — drove it toward grey: 38% of that frame's red
saturation, gone, on a file that was already approved and would have shipped.

That bug (`_protect_objects` testing only luma, never chroma) was fixed in
lib/photo_prep/color.py. It was not new, so anything rendered before the fix
could carry the same damage. This measures every shipped file against its own
source and reports where the colour went.

Method, and its one honest limitation:

  * rebuild what the source looked like at the moment colour ran — the recorded
    orientation, then the recorded crop box — so the shipped file and the source
    line up pixel for pixel. A frame whose geometry does not reconcile is
    reported as SKIPPED, never silently passed.
  * pick the pixels that had real colour to lose (chroma above the same bar the
    fix uses) FROM THE SOURCE, so the selection cannot be biased by whatever the
    render did.
  * compare mean saturation over exactly those pixels.

The limitation: saturation naturally RISES a little under punch and studio (they
add saturation on purpose), so a small positive delta is expected and healthy.
Only a fall is evidence, which is why the bar is one-sided.

Usage:
    python tools/prep_saturation_audit.py [--root inventory] [--drop 15]
                                          [--json out.json] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2                                                   # noqa: E402
import numpy as np                                           # noqa: E402

from lib.photo_prep import color as colormod                 # noqa: E402
from lib.photo_prep import orientation as orientmod          # noqa: E402

VIVID = colormod.CHROMA_OBJECT_MIN     # the same bar the fix uses
MIN_VIVID_PIXELS = 5000                # below this the frame has no colour at stake


def _source_at_colour_time(shoot: Path, name: str, rec: dict):
    """The frame as the colour pass saw it: oriented, then cropped."""
    src = shoot / name
    if not src.exists():
        return None
    img = cv2.imread(str(src))
    if img is None:
        return None
    img = orientmod.rotate_bgr(img, rec.get("orientation", {}).get("applied", 0))
    crop = rec.get("crop") or {}
    if crop.get("applied") and crop.get("box"):
        x0, y0, x1, y1 = crop["box"]
        img = img[y0:y1, x0:x1]
    return img


def audit_frame(shoot: Path, name: str, rec: dict) -> dict | None:
    out_rel = rec.get("output")
    if not out_rel:
        return None
    shipped = cv2.imread(str(shoot / out_rel))
    base = _source_at_colour_time(shoot, name, rec)
    if shipped is None or base is None:
        return dict(frame=name, status="SKIPPED", why="source or output unreadable")
    if shipped.shape[:2] != base.shape[:2]:
        return dict(frame=name, status="SKIPPED",
                    why=f"geometry does not reconcile {base.shape[:2]} vs {shipped.shape[:2]}")

    b = base.astype(np.int16)
    vivid = (b.max(axis=2) - b.min(axis=2)) > VIVID
    n = int(vivid.sum())
    if n < MIN_VIVID_PIXELS:
        return dict(frame=name, status="NO_COLOUR_AT_STAKE", vivid_px=n)

    s_before = float(cv2.cvtColor(base, cv2.COLOR_BGR2HSV)[vivid][:, 1].mean())
    s_after = float(cv2.cvtColor(shipped, cv2.COLOR_BGR2HSV)[vivid][:, 1].mean())
    return dict(frame=name, status="MEASURED", vivid_px=n,
                sat_before=round(s_before, 1), sat_after=round(s_after, 1),
                delta_pct=round((s_after - s_before) / s_before * 100, 1))


def audit_shoot(manifest: Path, drop: float) -> dict:
    shoot = manifest.parent.parent
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        return dict(shoot=shoot.as_posix(), error=str(e), frames=[])

    frames = []
    for name, rec in (m.get("photos") or {}).items():
        r = audit_frame(shoot, name, rec)
        if r:
            frames.append(r)

    measured = [f for f in frames if f["status"] == "MEASURED"]
    hits = [f for f in measured if f["delta_pct"] <= -drop]
    return dict(
        shoot=shoot.as_posix(),
        preset=m.get("chosen_preset"),
        frames=frames,
        n_measured=len(measured),
        n_skipped=sum(1 for f in frames if f["status"] == "SKIPPED"),
        worst=min((f["delta_pct"] for f in measured), default=None),
        hits=hits,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT / "inventory")
    ap.add_argument("--drop", type=float, default=15.0,
                    help="flag a frame whose vivid pixels lost this %% of saturation")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    manifests = sorted(a.root.rglob(".prep/prep.json"))
    if a.limit:
        manifests = manifests[:a.limit]
    print(f"auditing {len(manifests)} shoots, flagging a fall of {a.drop:.0f}% or more\n")

    results, flagged = [], []
    for i, mf in enumerate(manifests, 1):
        r = audit_shoot(mf, a.drop)
        results.append(r)
        mark = ""
        if r.get("hits"):
            flagged.append(r)
            mark = f"  <-- {len(r['hits'])} frame(s) drained"
        print(f"[{i:3}/{len(manifests)}] {Path(r['shoot']).name:38} "
              f"{r['n_measured']:3} measured  worst "
              f"{r['worst'] if r['worst'] is not None else 'n/a':>7}%{mark}", flush=True)

    print(f"\n{'=' * 72}")
    print(f"shoots audited        : {len(results)}")
    print(f"frames measured       : {sum(r['n_measured'] for r in results)}")
    print(f"frames skipped        : {sum(r['n_skipped'] for r in results)}")
    print(f"SHOOTS WITH DAMAGE    : {len(flagged)}")
    if flagged:
        print(f"\nworst first:")
        for r in sorted(flagged, key=lambda x: x["worst"]):
            print(f"  {Path(r['shoot']).name:38} preset={r['preset'] or '?':8} "
                  f"worst {r['worst']:6.1f}%  ({len(r['hits'])} frames)")
            for f in sorted(r["hits"], key=lambda x: x["delta_pct"])[:4]:
                print(f"      {f['frame']:20} {f['sat_before']:6.1f} -> "
                      f"{f['sat_after']:6.1f}  ({f['delta_pct']:+.1f}%)")

    if a.json:
        a.json.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"\nfull results: {a.json}")


if __name__ == "__main__":
    main()
