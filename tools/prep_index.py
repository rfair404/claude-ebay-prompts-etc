#!/usr/bin/env python3
"""Build one review artifact for a whole batch of PREP'd shoots.

Reviewing a fan-out one shoot at a time does not work: 33 shoots is 33 files to
open, and the thing you actually want to answer — "is this look right, and did
anything go obviously wrong anywhere" — is a comparison across them. So this
renders ONE index sheet, a row per shoot, hero frame before beside after, with
the numbers that decide whether to look closer.

The per-shoot `prep_review.jpg` is still where you go when a row looks wrong;
this is the map, not the territory.

    python tools/prep_index.py --queue .prep_queue.txt --out docs/prep_batch.jpg
    python tools/prep_index.py --queue .prep_queue.txt --md docs/prep_batch.md
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

from lib.photo_prep.prep import _load_bgr, load_manifest     # noqa: E402

CELL = 300


def _fit(img: np.ndarray, cell: int = CELL) -> np.ndarray:
    h, w = img.shape[:2]
    s = cell / max(h, w)
    r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    canvas = np.full((cell, cell, 3), 26, np.uint8)
    yo, xo = (cell - r.shape[0]) // 2, (cell - r.shape[1]) // 2
    canvas[yo:yo + r.shape[0], xo:xo + r.shape[1]] = r
    return canvas


def _bar(text: str, width: int, colour=(230, 230, 230), h: int = 28,
         scale: float = 0.48) -> np.ndarray:
    bar = np.full((h, width, 3), 14, np.uint8)
    cv2.putText(bar, text[:200], (8, h - 9), cv2.FONT_HERSHEY_SIMPLEX, scale,
                colour, 1, cv2.LINE_AA)
    return bar


def summarise(shoot: Path) -> dict | None:
    m = load_manifest(shoot)
    photos = m.get("photos") or {}
    if not photos:
        return None
    asks = [n for n, r in photos.items() if r["orientation"]["needs_ask"]]
    rotated = [n for n, r in photos.items() if r["orientation"]["applied"]]
    cropped = sum(1 for r in photos.values() if r.get("crop", {}).get("applied"))
    moved, held = [], 0
    for r in photos.values():
        c = r.get("color") or {}
        if not c:
            continue
        if c.get("curve", "none") != "none":
            moved.append((c["bg_luma_before"], c["bg_luma_after"]))
        if c.get("strength", 1.0) < 1.0:
            held += 1
    rails = sum((r.get("color") or {}).get("subject_newly_clipped", 0)
                + (r.get("color") or {}).get("subject_newly_crushed", 0)
                for r in photos.values())
    return {
        "shoot": shoot, "n": len(photos), "asks": asks, "rotated": len(rotated),
        "cropped": cropped, "toned": len(moved), "held_back": held, "rails": rails,
        "preset": m.get("chosen_preset"), "source": m.get("preset_source"),
        "approved": bool(m.get("approved")),
        "bg": (f"{np.mean([a for a, _ in moved]):.0f}->{np.mean([b for _, b in moved]):.0f}"
               if moved else "-"),
    }


def hero_pair(shoot: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """First frame that was actually rendered, before and after."""
    m = load_manifest(shoot)
    for name, rec in (m.get("photos") or {}).items():
        out = rec.get("output") or (rec.get("presets", {}).get(m.get("chosen_preset") or "", {})
                                    .get("path"))
        src = shoot / name
        if out and (shoot / out).exists() and src.exists():
            return _load_bgr(src), _load_bgr(shoot / out)
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", default=".prep_queue.txt")
    ap.add_argument("--out", default="docs/prep_batch.jpg")
    ap.add_argument("--md", default="docs/prep_batch.md")
    args = ap.parse_args()

    shoots = [Path(l.strip()) for l in open(args.queue, encoding="utf-8") if l.strip()]
    rows, strips = [], []

    for shoot in shoots:
        s = summarise(shoot)
        if not s:
            print(f"  (no manifest) {shoot}")
            continue
        rows.append(s)
        pair = hero_pair(shoot)
        if pair is None:
            continue
        before, after = pair
        flag = "ASK" if s["asks"] else ("HELD" if s["held_back"] else "ok")
        colour = ((120, 200, 255) if s["asks"]
                  else (170, 220, 255) if s["held_back"] else (180, 235, 190))
        label = (f"{shoot.as_posix():44} {s['n']:2}f  rot {s['rotated']}  crop {s['cropped']}  "
                 f"bg {s['bg']}  {s['preset'] or '-'}  [{flag}"
                 + (f" {len(s['asks'])}" if s["asks"] else "") + "]")
        pair_img = np.hstack([_fit(before), np.full((CELL, 5, 3), 60, np.uint8), _fit(after)])
        strips.append(np.vstack([_bar(label, pair_img.shape[1], colour), pair_img]))

    if strips:
        sheet = np.vstack(strips)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 86])
        if ok:
            buf.tofile(str(out))
            print(f"index sheet -> {out}  ({len(strips)} shoots)")

    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# PREP batch review", "",
                 "| shoot | frames | ASK | rotated | cropped | backdrop | rails | preset | approved |",
                 "|---|---|---|---|---|---|---|---|---|"]
        for s in rows:
            lines.append(
                f"| `{s['shoot'].as_posix()}` | {s['n']} | {len(s['asks'])} | {s['rotated']} | "
                f"{s['cropped']} | {s['bg']} | {s['rails']} | {s['preset'] or '-'} | "
                f"{'yes' if s['approved'] else 'no'} |")
        blocked = [s for s in rows if s["asks"]]
        lines += ["", f"**{len(rows)} shoots · {sum(s['n'] for s in rows)} frames.**"]
        if blocked:
            lines += ["", "Blocked on an orientation answer (cannot be approved):", ""]
            lines += [f"- `{s['shoot'].as_posix()}` — {', '.join(s['asks'])}" for s in blocked]
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"summary     -> {md}")

    print(f"\n{len(rows)} shoots · {sum(s['n'] for s in rows)} frames · "
          f"{sum(len(s['asks']) for s in rows)} awaiting orientation · "
          f"{sum(s['rails'] for s in rows)} rail hits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
