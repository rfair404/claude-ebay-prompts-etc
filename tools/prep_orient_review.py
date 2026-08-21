#!/usr/bin/env python3
"""Render every frame at all four turns, for a model to read and decide.

Orientation stopped being a good use of the operator's attention. It is the one
stage where the answer is usually knowable from the picture — a text baseline, a
masthead, which way an object sits — and the detector that was supposed to
supply it is not reliable enough to trust unattended (on one catalog shoot OSD
read the same wrong angle on five frames at confidence up to 12.2, with
recognised script, all corroborating each other).

So the model looks instead. This renders what it needs to look at: a row per
frame, all four turns side by side, big enough to read printed body copy, with
the current call marked. The model reads it, applies what it is confident about
with `--set-rotate`, and surfaces only the frames it is not sure about.

The bar is deliberately high. See prompts/prep.md — a frame is auto-applied only
on a legible, positive signal (readable text baseline, a masthead, an
unambiguous upright), corroborated by the rest of the shoot where the frames are
alike. Anything resting on "it looks a bit more natural" goes to the operator.

Usage:
    python tools/prep_orient_review.py <shoot> [--per-sheet 4] [--cell 460]
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

from lib.photo_prep import orientation as orientmod          # noqa: E402

TURNS = (0, 90, 180, 270)
INK = (240, 240, 240)
PICK = (90, 220, 120)
GROUND = 24


def _cell(img, cell: int, label: str, chosen: bool):
    h, w = img.shape[:2]
    s = cell / max(h, w)
    rz = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                    interpolation=cv2.INTER_AREA)
    pad = np.full((cell, cell, 3), GROUND, np.uint8)
    y, x = (cell - rz.shape[0]) // 2, (cell - rz.shape[1]) // 2
    pad[y:y + rz.shape[0], x:x + rz.shape[1]] = rz
    if chosen:
        cv2.rectangle(pad, (1, 1), (cell - 2, cell - 2), PICK, 3)
    cap = np.full((26, cell, 3), GROUND, np.uint8)
    cv2.putText(cap, label, (7, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                PICK if chosen else INK, 1, cv2.LINE_AA)
    return np.vstack([cap, pad])


def build(shoot: Path, per_sheet: int, cell: int) -> list[Path]:
    m = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
    out_dir = shoot / ".prep"
    rows, names = [], []

    for name, rec in (m.get("photos") or {}).items():
        src = shoot / name
        if not src.exists():
            continue
        o = rec["orientation"]
        # The camera half is objective; the four options are the SUBJECT turns
        # on top of it, which is the only thing anyone is being asked to judge.
        cam = orientmod.rotate_bgr(cv2.imread(str(src)), o.get("exif_angle", 0))
        cells = []
        for t in TURNS:
            applied = ((o.get("exif_angle", 0) or 0) + t) % 360
            cells.append(_cell(orientmod.rotate_bgr(cam, t), cell,
                               f"subj +{t}  (applied {applied})",
                               t == (o.get("subject_angle") or 0)))
        head = np.full((26, cell * 4, 3), GROUND, np.uint8)
        osd = (f"OSD {o['osd_angle']} conf {o['osd_conf']:.1f}"
               if o.get("osd_angle") is not None else "OSD: no reading")
        cv2.putText(head, f"{name}   [{osd}]", (7, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, INK, 1, cv2.LINE_AA)
        rows.append(np.vstack([head, np.hstack(cells)]))
        names.append(name)

    written = []
    for i in range(0, len(rows), per_sheet):
        chunk = rows[i:i + per_sheet]
        p = out_dir / f"orient_review_{i // per_sheet + 1}.jpg"
        cv2.imwrite(str(p), np.vstack(chunk), [cv2.IMWRITE_JPEG_QUALITY, 92])
        written.append(p)
        print(f"{p}   frames {i + 1}-{i + len(chunk)} of {len(rows)}")
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("shoot", type=Path)
    ap.add_argument("--per-sheet", type=int, default=4)
    ap.add_argument("--cell", type=int, default=460)
    a = ap.parse_args()
    build(a.shoot, a.per_sheet, a.cell)


if __name__ == "__main__":
    main()
