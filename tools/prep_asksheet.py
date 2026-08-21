#!/usr/bin/env python3
"""Combine every unresolved frame across MANY shoots into a few big sheets.

Orientation is the one step that cannot be parallelised away — somebody has to
look. But looking does not have to mean opening 97 files: at one sheet per
shoot the reading itself becomes the bottleneck, and the answer to "is this
upright" needs a legible thumbnail, not a full frame.

So this tiles every ASK frame in the queue into a handful of sheets, each tile
labelled `<index> shoot/frame`, and writes an index JSON mapping those indices
back to shoot + filename. Answers come back as indices, which is what makes
recording them a single pass instead of 97.

    python tools/prep_asksheet.py --queue Q --out docs/ask --per-sheet 48
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                          # noqa: E402
import cv2                                                  # noqa: E402

from lib.photo_prep.prep import _load_bgr, _thumb, load_manifest   # noqa: E402
from lib.photo_prep import orientation as O                        # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out", default="docs/ask")
    ap.add_argument("--per-sheet", type=int, default=48)
    ap.add_argument("--cell", type=int, default=260)
    ap.add_argument("--cols", type=int, default=6)
    args = ap.parse_args()

    shoots = [Path(l.strip()) for l in
              Path(args.queue).read_text(encoding="utf-8").splitlines() if l.strip()]

    items = []
    for s in shoots:
        m = load_manifest(s)
        for name, rec in (m.get("photos") or {}).items():
            if rec["orientation"]["needs_ask"] and (s / name).exists():
                items.append((s, name, rec["orientation"]))
    if not items:
        print("nothing awaiting an orientation answer")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    index, sheet_no, cell, cols = {}, 0, args.cell, args.cols

    for start in range(0, len(items), args.per_sheet):
        chunk = items[start:start + args.per_sheet]
        sheet_no += 1
        tiles = []
        for j, (s, name, o) in enumerate(chunk):
            idx = start + j + 1
            index[str(idx)] = {"shoot": s.as_posix(), "frame": name}
            img = O.rotate_bgr(_load_bgr(s / name), o.get("exif_angle", 0))
            img = O.rotate_bgr(_thumb(img, cell * 2), o.get("subject_angle", 0))
            h, w = img.shape[:2]
            sc = cell / max(h, w)
            r = cv2.resize(img, (max(1, int(w * sc)), max(1, int(h * sc))),
                           interpolation=cv2.INTER_AREA)
            canvas = np.full((cell, cell, 3), 26, np.uint8)
            yo, xo = (cell - r.shape[0]) // 2, (cell - r.shape[1]) // 2
            canvas[yo:yo + r.shape[0], xo:xo + r.shape[1]] = r
            bar = np.full((30, cell, 3), 12, np.uint8)
            prop = o.get("osd_proposal")
            cv2.putText(bar, f"{idx}", (5, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (0, 220, 255), 1, cv2.LINE_AA)
            cv2.putText(bar, f"{s.name[:22]}/{Path(name).stem[-10:]}"
                        + (f" osd?{prop}" if prop else ""),
                        (26, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.34,
                        (215, 215, 215), 1, cv2.LINE_AA)
            tiles.append(np.vstack([bar, canvas]))

        rows = []
        for i in range(0, len(tiles), cols):
            row = tiles[i:i + cols]
            while len(row) < cols:
                row.append(np.full_like(tiles[0], 15))
            rows.append(np.hstack(row))
        sheet = np.vstack(rows)
        p = out_dir / f"ask_{sheet_no:02d}.jpg"
        ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 84])
        if ok:
            buf.tofile(str(p))
        print(f"  {p}  frames {start+1}-{start+len(chunk)}")

    (out_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"\n{len(items)} unresolved frames across {len({i[0] for i in items})} shoots "
          f"-> {sheet_no} sheets + index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
