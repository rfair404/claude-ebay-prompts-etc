#!/usr/bin/env python3
"""Second pass: was the colour lost from the ITEM, or from the backdrop?

The sweep in prep_saturation_audit.py measures every strongly coloured pixel in
a frame, which is the right net to cast first — it needs no segmentation and so
can run over every shoot cheaply. But it cannot tell two very different things
apart:

  * a mask failure handed part of the ITEM to the backdrop pass, which
    neutralised it. That is damage, and it is what this audit exists to find.
  * the BACKDROP itself had a colour cast — tungsten light on a white sweep,
    a blue-grey felt — and the correction removed it. That is the correction
    doing its job, and it will read as a huge "loss" because the cast pixels
    were strongly coloured before and are neutral after.

The first pass flags both. This one separates them, by re-segmenting each
flagged frame and measuring the loss only over pixels INSIDE the subject mask.
Segmentation is the expensive step, which is why it runs on the flagged frames
alone rather than all ~2000.

A frame that loses colour inside the mask is damage. A frame that loses it only
outside is a cast being removed, and should be left alone.

Usage:
    python tools/prep_saturation_verify.py [--audit .saturation_audit.json]
                                           [--drop 15] [--json out.json]
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
from lib.photo_prep import subject as subjectmod             # noqa: E402

VIVID = colormod.CHROMA_OBJECT_MIN
MIN_PIXELS = 2000


def verify_frame(shoot: Path, name: str, rec: dict) -> dict:
    src = shoot / name
    out = shoot / (rec.get("output") or "")
    base = cv2.imread(str(src))
    shipped = cv2.imread(str(out))
    if base is None or shipped is None:
        return dict(frame=name, verdict="UNREADABLE")

    base = orientmod.rotate_bgr(base, rec.get("orientation", {}).get("applied", 0))
    crop = rec.get("crop") or {}
    if crop.get("applied") and crop.get("box"):
        x0, y0, x1, y1 = crop["box"]
        base = base[y0:y1, x0:x1]
    if base.shape[:2] != shipped.shape[:2]:
        return dict(frame=name, verdict="GEOMETRY_MISMATCH")

    mask = subjectmod.mask_for(base).mask
    b = base.astype(np.int16)
    vivid = (b.max(axis=2) - b.min(axis=2)) > VIVID

    sat_b = cv2.cvtColor(base, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
    sat_a = cv2.cvtColor(shipped, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)

    def delta(sel):
        if int(sel.sum()) < MIN_PIXELS:
            return None, 0
        before, after = float(sat_b[sel].mean()), float(sat_a[sel].mean())
        return round((after - before) / max(before, 1e-6) * 100, 1), int(sel.sum())

    on_item, n_item = delta(vivid & (mask > 0))
    off_item, n_bg = delta(vivid & (mask == 0))

    if on_item is None:
        verdict = "NO_ITEM_COLOUR"        # the coloured pixels are all backdrop
    elif on_item <= -15:
        verdict = "ITEM_DAMAGED"
    elif off_item is not None and off_item <= -15:
        verdict = "BACKDROP_CAST_REMOVED"
    else:
        verdict = "OK"

    return dict(frame=name, verdict=verdict,
                item_delta_pct=on_item, item_px=n_item,
                backdrop_delta_pct=off_item, backdrop_px=n_bg,
                mask_coverage=round(float((mask > 0).mean()), 3))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", type=Path, default=ROOT / ".saturation_audit.json")
    ap.add_argument("--drop", type=float, default=15.0)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    audit = json.loads(a.audit.read_text(encoding="utf-8"))
    jobs = [(r["shoot"], f["frame"]) for r in audit
            for f in r.get("hits", []) if f["delta_pct"] <= -a.drop]
    print(f"re-checking {len(jobs)} flagged frames against the subject mask\n")

    results, damaged = [], []
    for i, (shoot_s, frame) in enumerate(jobs, 1):
        shoot = Path(shoot_s)
        m = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
        rec = (m.get("photos") or {}).get(frame)
        if not rec:
            continue
        r = verify_frame(shoot, frame, rec)
        r["shoot"] = shoot_s
        r["preset"] = m.get("chosen_preset")
        results.append(r)
        if r["verdict"] == "ITEM_DAMAGED":
            damaged.append(r)
        print(f"[{i:3}/{len(jobs)}] {shoot.name:30} {frame:18} "
              f"item {str(r.get('item_delta_pct')):>7}%  "
              f"bg {str(r.get('backdrop_delta_pct')):>7}%  {r['verdict']}", flush=True)

    print(f"\n{'=' * 74}")
    from collections import Counter
    for v, n in Counter(r["verdict"] for r in results).most_common():
        print(f"  {v:24} {n}")

    if damaged:
        shoots = sorted({r["shoot"] for r in damaged})
        print(f"\nITEM COLOUR DAMAGED — {len(damaged)} frames across {len(shoots)} shoots:")
        for s in shoots:
            fr = [r for r in damaged if r["shoot"] == s]
            print(f"  {Path(s).name:30} preset={fr[0]['preset'] or '?':8} "
                  f"{len(fr)} frame(s)")
            for r in sorted(fr, key=lambda x: x["item_delta_pct"])[:3]:
                print(f"      {r['frame']:18} item {r['item_delta_pct']:+6.1f}%  "
                      f"mask covers {r['mask_coverage']:.0%}")

    if a.json:
        a.json.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"\nfull results: {a.json}")


if __name__ == "__main__":
    main()
