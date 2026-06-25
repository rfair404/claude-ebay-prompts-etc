#!/usr/bin/env python3
"""Detect and crop individual marbles out of a photo (the classifier's eyes).

The classifier embeds whatever fills the frame, so a towel/caliper/multi-marble
phone shot embeds the background and mis-fires. This finds each marble (they're
circles), crops it to a tight square, and masks the surrounding towel/caliper to
white — turning one cluttered photo into N clean single-marble inputs that look
like the MCSA studio references the classifier was tuned on.

  python lib/marble_crop.py IMG [IMG ...] [--out DIR] [--no-mask]
     [--min-frac 0.05] [--max-frac 0.6] [--circ 0.78] [--max-n 60]

Writes <stem>_mNN.jpg per detected marble and prints the count + circles.
In-process: detect_and_crop(path) -> [PIL.Image] for the classifier.

Detection uses Hough gradient-alt (OpenCV's accurate circle finder); --circ is
its circularity threshold (0..1, higher = stricter/rounder). Radii are bounded
as a fraction of the image's short side so a 1-marble macro and a 7-marble row
both work. Rectangular clutter (calipers, paper labels, fingers) isn't circular,
so it's naturally rejected.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _load_bgr(path):
    import cv2
    import numpy as np
    # cv2.imread chokes on non-ASCII Windows paths; read via numpy buffer.
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"could not decode image: {path}")
    return img


_DETECT_SHORT = 700   # detect on a downscaled copy (fast + param-stable)


def _contour_circles(bgr, r_min, r_max):
    """Catch marbles Hough misses (esp. pale ones) via a foreground blob mask.

    The towel background is low-saturation and a fairly uniform brightness;
    a marble departs from it in saturation OR brightness. Mask that, clean it
    up, and fit a circle to each round-enough blob.
    """
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    border = np.concatenate([val[:5].ravel(), val[-5:].ravel(),
                             val[:, :5].ravel(), val[:, -5:].ravel()])
    bg = float(np.median(border))
    fg = ((sat > 60) | (np.abs(val.astype("int") - bg) > 30)).astype("uint8") * 255
    k = max(3, (r_min // 3) | 1)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((k, k), "uint8"))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((k, k), "uint8"))
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        (x, y), r = cv2.minEnclosingCircle(c)
        if not (r_min <= r <= r_max):
            continue
        fill = cv2.contourArea(c) / (np.pi * r * r + 1e-6)   # 1.0 = perfect disk
        if fill >= 0.70:                 # 0.70 rejects squares (a square fills ~0.64)
            out.append((float(x), float(y), float(r)))
    return out


def _interior_ok(bgr, x, y, r):
    """Reject blank paper labels and flat shadows: a real marble has internal
    texture (swirl + specular highlight) or colour; a label/shadow has neither."""
    import cv2
    import numpy as np
    H, W = bgr.shape[:2]
    rr = int(round(r * 0.8))
    x0, y0 = max(0, int(x) - rr), max(0, int(y) - rr)
    x1, y1 = min(W, int(x) + rr), min(H, int(y) + rr)
    patch = bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    sat = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[..., 1]
    return g.std() > 8 or sat.mean() > 22


def detect_marbles(bgr, *, min_frac=0.05, max_frac=0.6, circ=0.40, max_n=60):
    """-> list of (x, y, r) circles in ORIGINAL-image pixels, largest first.

    Hough (accurate on contrasting marbles) unioned with a contour/blob pass
    (recovers pale marbles that blend into a light background). Detection runs on
    a downscaled copy for speed; circles are scaled back up.
    """
    import cv2
    import numpy as np
    H, W = bgr.shape[:2]
    scale = _DETECT_SHORT / min(H, W)
    small = cv2.resize(bgr, (int(W * scale), int(H * scale))) if scale < 1 else bgr
    sh = min(small.shape[:2])
    r_min = max(6, int(min_frac * sh))
    r_max = max(r_min + 1, int(max_frac * sh))
    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 5)
    found = []
    hc = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT_ALT, dp=1.5,
                          minDist=int(r_min * 1.3), param1=300, param2=circ,
                          minRadius=r_min, maxRadius=r_max)
    if hc is not None:
        found.extend((float(x), float(y), float(r)) for x, y, r in hc[0])
    # add contour circles whose centre isn't already covered by a Hough hit
    for (x, y, r) in _contour_circles(small, r_min, r_max):
        if all((x - a) ** 2 + (y - b) ** 2 > (0.6 * max(r, rr)) ** 2
               for a, b, rr in found):
            found.append((x, y, r))
    # drop blank labels / shadows (no marble texture inside)
    found = [c for c in found if _interior_ok(small, *c)]
    inv = 1.0 / scale if scale < 1 else 1.0
    out = sorted(((x * inv, y * inv, r * inv) for x, y, r in found), key=lambda c: -c[2])
    return out[:max_n]


def crop_marble(bgr, circle, *, pad=0.15, mask_bg=True):
    """Square crop around one circle, background masked to white (-> PIL.RGB)."""
    import cv2
    import numpy as np
    from PIL import Image
    h, w = bgr.shape[:2]
    x, y, r = circle
    R = r * (1 + pad)
    x0, y0 = int(max(0, x - R)), int(max(0, y - R))
    x1, y1 = int(min(w, x + R)), int(min(h, y + R))
    crop = bgr[y0:y1, x0:x1].copy()
    if mask_bg and crop.size:
        ch, cw = crop.shape[:2]
        mask = np.zeros((ch, cw), np.uint8)
        cv2.circle(mask, (int(x - x0), int(y - y0)), int(r * 1.02), 255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(1, r * 0.03))
        white = np.full_like(crop, 255)
        a = (mask.astype("float32") / 255.0)[..., None]
        crop = (crop * a + white * (1 - a)).astype("uint8")
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def detect_and_crop(path, **kw):
    """Photo path -> [(circle, PIL.Image)] for each detected marble."""
    mask_bg = kw.pop("mask_bg", True)
    pad = kw.pop("pad", 0.15)
    bgr = _load_bgr(path)
    return [(c, crop_marble(bgr, c, pad=pad, mask_bg=mask_bg))
            for c in detect_marbles(bgr, **kw)]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default=None, help="dir for crops (default: <image_dir>/crops)")
    ap.add_argument("--no-mask", action="store_true", help="don't white-mask the background")
    ap.add_argument("--min-frac", type=float, default=0.05)
    ap.add_argument("--max-frac", type=float, default=0.6)
    ap.add_argument("--circ", type=float, default=0.40)
    ap.add_argument("--max-n", type=int, default=60)
    args = ap.parse_args()
    total = 0
    for img in args.images:
        p = Path(img)
        out = Path(args.out) if args.out else p.parent / "crops"
        out.mkdir(parents=True, exist_ok=True)
        pairs = detect_and_crop(
            p, min_frac=args.min_frac, max_frac=args.max_frac, circ=args.circ,
            max_n=args.max_n, mask_bg=not args.no_mask)
        print(f"{p.name}: {len(pairs)} marble(s)")
        for k, (c, im) in enumerate(pairs, 1):
            fn = out / f"{p.stem}_m{k:02d}.jpg"
            im.save(fn, quality=92)
            print(f"   m{k:02d}  center=({c[0]:.0f},{c[1]:.0f}) r={c[2]:.0f}  -> {fn.name}")
        total += len(pairs)
    print(f"\nTotal: {total} marble(s) from {len(args.images)} photo(s).")


if __name__ == "__main__":
    main()
