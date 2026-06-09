"""Diagnostic — process a single photo with verbose output.

Not part of the public CLI. A workshop tool for tuning trim_whitespace
on individual images before committing parameters to the batch pass.

Usage:
    python _inspect_one.py <image-path> [--fuzziness F] [--margin-pct P]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image
import pillow_heif

from trim_whitespace import (
    _pil_lab_to_cie,
    _sample_background_color,
    _find_subject_bbox,
    _apply_margin,
    _save_cropped,
    DEFAULT_FUZZINESS,
    DEFAULT_MARGIN_PCT,
    CORNER_PATCH_PCT,
)

pillow_heif.register_heif_opener()


def inspect(src: Path, fuzziness: float, margin_pct: float) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"=== inspecting {src.name} ===")

    with Image.open(src) as img_in:
        src_qtables = getattr(img_in, "quantization", None)
        arr_rgb = np.array(img_in.convert("RGB"))
        arr_lab_pil = np.array(img_in.convert("LAB"))

    h, w = arr_rgb.shape[:2]
    print(f"size:        {w} x {h}  ({w*h:,} pixels)")
    print(f"qtables:     {'present' if src_qtables else 'missing'}")

    # Corner RGB and LAB
    ph = max(1, int(h * CORNER_PATCH_PCT))
    pw = max(1, int(w * CORNER_PATCH_PCT))
    arr_lab_cie = _pil_lab_to_cie(arr_lab_pil)

    print(f"corner patch size: {pw} x {ph} (5% of W and H)")
    for name, sl in [
        ("TL", (slice(None, ph), slice(None, pw))),
        ("TR", (slice(None, ph), slice(-pw, None))),
        ("BL", (slice(-ph, None), slice(None, pw))),
        ("BR", (slice(-ph, None), slice(-pw, None))),
    ]:
        rgb_mean = arr_rgb[sl].reshape(-1, 3).mean(axis=0)
        lab_mean = arr_lab_cie[sl].reshape(-1, 3).mean(axis=0)
        rgb_std = arr_rgb[sl].reshape(-1, 3).std(axis=0)
        print(f"  {name} corner: "
              f"RGB ({rgb_mean[0]:5.1f}, {rgb_mean[1]:5.1f}, {rgb_mean[2]:5.1f}) "
              f"+/- ({rgb_std[0]:4.1f}, {rgb_std[1]:4.1f}, {rgb_std[2]:4.1f})   "
              f"LAB (L={lab_mean[0]:5.1f}  a={lab_mean[1]:5.1f}  b={lab_mean[2]:5.1f})")

    bg_color, corner_note = _sample_background_color(arr_lab_cie)
    print()
    print(f"sampled bg (CIE LAB): L={bg_color[0]:.1f}  a={bg_color[1]:.1f}  b={bg_color[2]:.1f}")
    print(f"corner decision:      {corner_note}")

    # Distance distribution
    diff = arr_lab_cie - bg_color
    delta_e = np.sqrt(np.sum(diff * diff, axis=2))
    print()
    print(f"delta-E from background (whole image):")
    print(f"  min={delta_e.min():.1f}  median={np.median(delta_e):.1f}  "
          f"mean={delta_e.mean():.1f}  p95={np.percentile(delta_e, 95):.1f}  "
          f"max={delta_e.max():.1f}")

    print()
    print(f"mask coverage (= 'subject') at multiple thresholds:")
    for fz in [3, 5, 8, 12, 15, 20, 30, 50]:
        mask = delta_e > fz
        coverage = mask.mean() * 100
        marker = "  <-- current" if abs(fz - fuzziness) < 0.01 else ""
        print(f"  fuzziness={fz:3d}: {coverage:5.1f}% subject{marker}")

    # Bbox at current fuzziness
    bbox = _find_subject_bbox(arr_lab_cie, bg_color, fuzziness)
    if bbox is None:
        print()
        print("RESULT: no subject pixels at current fuzziness -> would passthrough")
        return

    left, top, right, bottom = bbox
    print()
    print(f"raw bbox (no margin):  L={left}  T={top}  R={right}  B={bottom}")
    print(f"   dimensions:         {right-left} x {bottom-top}")
    print(f"   area kept:          {((right-left)*(bottom-top))/(w*h)*100:.1f}%")

    bbox_m = _apply_margin(bbox, (w, h), margin_pct)
    left_m, top_m, right_m, bottom_m = bbox_m
    print(f"with {margin_pct*100:.1f}% margin:  L={left_m}  T={top_m}  R={right_m}  B={bottom_m}")
    print(f"   dimensions:         {right_m-left_m} x {bottom_m-top_m}")
    print(f"   area kept:          {((right_m-left_m)*(bottom_m-top_m))/(w*h)*100:.1f}%")

    # Save the crop next to the source as <name>.inspect.jpg
    cropped_arr = arr_rgb[top_m:bottom_m, left_m:right_m]
    cropped_img = Image.fromarray(cropped_arr)
    dst = src.with_name(src.stem + f".trim-fz{int(fuzziness)}-m{int(margin_pct*100)}.jpg")
    _save_cropped(cropped_img, dst, ".jpg", src_qtables)
    print()
    print(f"saved crop -> {dst}")
    print(f"   (compare against original {src.name})")


def _cli() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("image", type=Path)
    p.add_argument("--fuzziness", type=float, default=DEFAULT_FUZZINESS)
    p.add_argument("--margin-pct", type=float, default=DEFAULT_MARGIN_PCT)
    a = p.parse_args()
    inspect(a.image, a.fuzziness, a.margin_pct)


if __name__ == "__main__":
    _cli()
