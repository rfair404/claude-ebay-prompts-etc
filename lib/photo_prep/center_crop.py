#!/usr/bin/env python3
"""Center the item in each product photo (the pre-DRAFT crop-check).

We shoot some items deliberately off-center (better focus / lighting), but an
off-center subject looks bad in an eBay gallery — the thumbnail crops to the
frame center, not the item. This finds the item's focal point, then crops so the
item sits centered, at an eBay-friendly aspect (square by default).

Focal detection is background-contrast segmentation: studio shots sit on a
uniform field (dark felt OR a light box interior), so the *border* of the frame
samples the background color, and the subject is the largest blob of pixels that
differ from it. That one rule handles both the navy-felt and white-box shots in
this repo without a model. Rectangular clutter at the very edge (ruler, box lip
running off-frame) is trimmed by preferring the largest central blob.

That premise fails on real shoots — on a macro the subject IS the background, and
a high-contrast printed logo out-shouts the metal item beside it. When it fails
the crop doesn't degrade gracefully, it destroys the frame (zooms into the logo,
returns bare fabric, clips the legend off a hang tag). So every crop is run past
`crop_warning()` first and a crop that can't be made safely is SKIPPED — the
original passes through untouched, with the reason printed and shown on the
review sheet. Guards, all of them detection-failure tests rather than taste:

  * subject box < 2% or > 70% of the frame  — nothing found / subject fills frame
  * > 25% of the foreground falls outside the box — the blob picker discarded the
    real subject (a bracelet spanning the frame reads as "background field")
  * box aspect worse than 8:1 — that's a seam or an edge, not an item
  * > 50% of the frame border reads as foreground — the background premise is gone
  * the crop would cut ANY of the subject box (>2%) — a centering crop is allowed
    to trim background, never the item

`--force` bypasses the guards. Note the off-center *offset* is not a safety
signal: a macro whose subject fills the frame reads as maximally off-center.

  # check only — report each photo's verdict + reason, write nothing:
  python -m lib.photo_prep.center_crop <shoot-dir> --check

  # crop: write centered copies to <shoot-dir>/cropped/ + a review contact sheet:
  python -m lib.photo_prep.center_crop <shoot-dir> [--aspect 1:1] [--pad 0.12]
     [--out DIR] [--threshold 0.06] [--apply] [--force]

By default crops are written to a `cropped/` subdir (non-destructive) and a
`crop_review.jpg` contact sheet (original | centered, per photo) is written for
eyeball-before-use. `--apply` overwrites the originals in place (a `.orig/`
backup is made first); skipped frames are left untouched. Off-center is measured
as the subject centroid's distance from frame center as a fraction of the frame's
short side; `--threshold` (default 0.06 = 6%) is the flag cutoff.

In-process: focal_point(path) -> dict(cx, cy, bbox, offset, + guard metrics);
            crop_warning(fp, box) -> reason str or None;
            center_crop(path, aspect, pad) -> PIL.Image (the ORIGINAL if unsafe).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_DETECT_SHORT = 800   # segment on a downscaled copy (fast + param-stable)

# --- safety guards ---------------------------------------------------------
# Thresholds are set from the misc-08-14-a shoot, where 20/38 frames were
# destroyed: every bad frame is caught, every clean crop still ships.
MIN_SUBJECT_FRAC = 0.02   # below -> detector found nothing (blur, empty macro)
MAX_SUBJECT_FRAC = 0.70   # above -> subject fills frame; there's no bg to trim
MIN_CAPTURE = 0.75        # foreground mass that must land inside the picked box
MAX_BOX_ASPECT = 8.0      # a sliver box is a seam/edge, not an item
MAX_BORDER_FG = 0.50      # border ring is supposed to *sample the background*
MAX_SUBJECT_LOSS = 0.02   # crop may trim background, not the item itself


def _load_bgr(path):
    import cv2
    import numpy as np
    # cv2.imread chokes on non-ASCII Windows paths; read via numpy buffer.
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"could not decode image: {path}")
    return img


def _save_bgr(path, img):
    import cv2
    ext = Path(path).suffix or ".jpg"
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError(f"could not encode image: {path}")
    buf.tofile(str(path))


def _subject_mask(bgr):
    """Return a full-res boolean-ish uint8 mask of the focal subject."""
    import cv2
    import numpy as np

    H, W = bgr.shape[:2]
    scale = _DETECT_SHORT / min(H, W)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr.copy()
    h, w = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)

    # Background color = median of a thin border frame (outer ~4%).
    b = max(2, int(round(0.04 * min(h, w))))
    border = np.concatenate([
        lab[:b, :, :].reshape(-1, 3), lab[-b:, :, :].reshape(-1, 3),
        lab[:, :b, :].reshape(-1, 3), lab[:, -b:, :].reshape(-1, 3),
    ], axis=0)
    bg = np.median(border, axis=0)

    dist = np.linalg.norm(lab - bg[None, None, :], axis=2)
    dn = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    # Otsu splits "near background" from "clearly foreground".
    _t, mask = cv2.threshold(dn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Clean speckle; close gaps inside the subject.
    k = max(3, int(round(0.012 * min(h, w))) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    frac = float((mask > 0).mean())
    if frac < 0.004 or frac > 0.92:
        # Degenerate (busy bg or subject fills frame) -> fall back to a
        # center-weighted gradient saliency so we still get a focal point.
        # The result is a guess, and the guards below are what stop it shipping.
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 0.02 * min(h, w))
        mn = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _t, mask = cv2.threshold(mn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if scale < 1:
        mask = cv2.resize(mask, (W, H), interpolation=cv2.INTER_NEAREST)
    return mask


def _pick_blob(mask):
    """Union of ALL foreground pieces into one subject region.

    A pair of cufflinks / a set of items is several components; the focal region
    must span every piece, not just the biggest — else the crop keeps one and
    drops the rest. So: drop components that read as background (a blob spanning
    the frame or hugging the border at huge area), keep the real pieces (each
    >= a floor area AND a fraction of the largest piece, to shed dust/specks),
    union their bounding boxes, and take the area-weighted centroid.

    The drop rules are the risk here: a bracelet laid across the frame spans
    left-to-right and gets dropped as "background", leaving a chance highlight as
    the subject. That's what the `capture` metric measures and the guards catch.

    Returns (x, y, w, h, cx, cy) of the union in full-res pixels.
    """
    import cv2

    H, W = mask.shape[:2]
    n, labels, stats, cents = cv2.connectedComponentsWithStats((mask > 0).astype("uint8"), 8)
    cand = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        touch_l, touch_r = x <= 1, x + ww >= W - 1
        touch_t, touch_b = y <= 1, y + hh >= H - 1
        spans = (touch_l and touch_r) or (touch_t and touch_b)
        border_huge = (touch_l or touch_r or touch_t or touch_b) and area > 0.40 * W * H
        if spans or border_huge:
            continue   # background field, not the subject
        cand.append((int(x), int(y), int(ww), int(hh), int(area), float(cents[i][0]), float(cents[i][1])))
    if not cand:
        return (0, 0, W, H, W / 2.0, H / 2.0)

    max_area = max(c[4] for c in cand)
    keep = [c for c in cand if c[4] >= max(0.0015 * W * H, 0.06 * max_area)]
    if not keep:
        keep = [max(cand, key=lambda c: c[4])]

    x0 = min(c[0] for c in keep); y0 = min(c[1] for c in keep)
    x1 = max(c[0] + c[2] for c in keep); y1 = max(c[1] + c[3] for c in keep)
    tot = sum(c[4] for c in keep)
    cx = sum(c[5] * c[4] for c in keep) / tot
    cy = sum(c[6] * c[4] for c in keep) / tot
    return (x0, y0, x1 - x0, y1 - y0, cx, cy)


def _focal_from_bgr(bgr):
    """focal_point() on an already-loaded image (so callers decode once)."""
    import numpy as np

    H, W = bgr.shape[:2]
    mask = _subject_mask(bgr) > 0
    x, y, ww, hh, _cxw, _cyw = _pick_blob(mask.astype("uint8") * 255)

    # Frame on the subject's geometric center so every piece stays in-crop.
    cx, cy = x + ww / 2.0, y + hh / 2.0
    short = float(min(H, W))
    offset = (((cx - W / 2.0) ** 2 + (cy - H / 2.0) ** 2) ** 0.5) / short

    # Guard metrics — all "did detection work?", none of them about framing.
    total_fg = int(mask.sum())
    inside_fg = int(mask[y:y + hh, x:x + ww].sum())
    b = max(2, int(round(0.04 * min(H, W))))
    ring = np.zeros((H, W), bool)
    ring[:b, :] = ring[-b:, :] = True
    ring[:, :b] = ring[:, -b:] = True

    return {
        "w": W, "h": H, "bbox": (x, y, ww, hh),
        "cx": cx, "cy": cy, "offset": offset,
        "subject_frac": (ww * hh) / float(W * H),
        "capture": (inside_fg / total_fg) if total_fg else 0.0,
        "box_aspect": (ww / hh) if hh else float("inf"),
        "border_fg": float(mask[ring].mean()),
    }


def focal_point(path):
    """Locate the subject; return focal geometry, off-center metric + guard metrics."""
    return _focal_from_bgr(_load_bgr(path))


def _parse_aspect(s):
    if not s or s.lower() in ("orig", "original", "none"):
        return None
    if ":" in s:
        a, b = s.split(":")
        return float(a) / float(b)
    return float(s)


def _crop_box(fp, aspect, pad):
    """Compute a crop (x0,y0,x1,y1) centered on the focal point.

    The crop is the smallest box of the target aspect that (a) contains the
    subject bbox grown by `pad`, and (b) is centered on the subject centroid —
    then clamped inside the image, shrinking symmetrically if a clamp would
    otherwise re-decenter the subject. That shrink is what can eat the subject,
    so the result must always be run past crop_warning().
    """
    W, H = fp["w"], fp["h"]
    x, y, bw, bh = fp["bbox"]
    cx, cy = fp["cx"], fp["cy"]

    # subject extent + padding
    pw = bw * (1 + 2 * pad)
    ph = bh * (1 + 2 * pad)

    if aspect is None:
        cw, ch = pw, ph
    else:
        # grow to the target aspect around the padded subject
        if pw / ph > aspect:
            cw, ch = pw, pw / aspect
        else:
            ch, cw = ph, ph * aspect

    # Max half-extent that keeps the crop centered on (cx,cy) inside the frame.
    half_w = min(cx, W - cx)
    half_h = min(cy, H - cy)
    max_w, max_h = 2 * half_w, 2 * half_h
    if aspect is not None:
        # keep aspect while fitting inside the centered limits
        if max_w / max_h > aspect:
            max_w = max_h * aspect
        else:
            max_h = max_w / aspect
    cw = min(cw, max_w)
    ch = min(ch, max_h)

    x0 = int(round(cx - cw / 2)); x1 = int(round(cx + cw / 2))
    y0 = int(round(cy - ch / 2)); y1 = int(round(cy + ch / 2))
    x0 = max(0, x0); y0 = max(0, y0); x1 = min(W, x1); y1 = min(H, y1)
    return x0, y0, x1, y1


def subject_kept(fp, box):
    """Fraction of the detected subject box that survives inside `box`."""
    x, y, bw, bh = fp["bbox"]
    x0, y0, x1, y1 = box
    ix = max(0, min(x + bw, x1) - max(x, x0))
    iy = max(0, min(y + bh, y1) - max(y, y0))
    return (ix * iy) / float(max(1, bw * bh))


def crop_warning(fp, box):
    """Why this crop is unsafe to ship, or None if it's safe.

    Detection-failure tests first (is the subject box even believable?), then the
    one geometry test (would the crop cut the item?).
    """
    sf, cap = fp["subject_frac"], fp["capture"]
    ar = fp["box_aspect"]

    if sf < MIN_SUBJECT_FRAC:
        return f"no subject found (box is {sf:.1%} of the frame)"
    if sf > MAX_SUBJECT_FRAC:
        return f"subject fills the frame ({sf:.0%}) — nothing to re-center"
    if ar > MAX_BOX_ASPECT or ar < 1.0 / MAX_BOX_ASPECT:
        shape = f"{ar:.0f}:1" if ar >= 1 else f"1:{1 / ar:.0f}"
        return f"detected box is a sliver ({shape}) — an edge, not an item"
    if fp["border_fg"] > MAX_BORDER_FG:
        return f"no clean background ({fp['border_fg']:.0%} of the frame border reads as subject)"
    if cap < MIN_CAPTURE:
        return f"detection unreliable ({1 - cap:.0%} of the subject fell outside the box)"

    lost = 1.0 - subject_kept(fp, box)
    if lost > MAX_SUBJECT_LOSS:
        return f"crop would cut {lost:.0%} off the subject"
    return None


def center_crop(path, aspect=1.0, pad=0.12, force=False):
    """Return a PIL.Image of the centered crop — or of the ORIGINAL if unsafe."""
    from PIL import Image
    import cv2
    bgr = _load_bgr(path)
    fp = _focal_from_bgr(bgr)
    box = _crop_box(fp, aspect, pad)
    if not force and crop_warning(fp, box):
        crop = bgr
    else:
        x0, y0, x1, y1 = box
        crop = bgr[y0:y1, x0:x1]
    return Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))


def _iter_images(shoot_dir):
    for p in sorted(Path(shoot_dir).iterdir()):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            yield p


def _contact_sheet(rows, out_path):
    """rows: list of (title, before_bgr, after_bgr). Stack original|crop pairs."""
    import cv2
    import numpy as np
    cell = 320
    def fit(img):
        h, w = img.shape[:2]
        s = cell / max(h, w)
        r = cv2.resize(img, (int(w * s), int(h * s)))
        canvas = np.full((cell, cell, 3), 30, np.uint8)
        yo, xo = (cell - r.shape[0]) // 2, (cell - r.shape[1]) // 2
        canvas[yo:yo + r.shape[0], xo:xo + r.shape[1]] = r
        return canvas
    strips = []
    for title, before, after in rows:
        pair = np.hstack([fit(before), np.full((cell, 6, 3), 60, np.uint8), fit(after)])
        bar = np.full((26, pair.shape[1], 3), 20, np.uint8)
        cv2.putText(bar, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
        strips.append(np.vstack([bar, pair]))
    sheet = np.vstack(strips) if strips else np.zeros((10, 10, 3), np.uint8)
    _save_bgr(out_path, sheet)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Center the item in each product photo (pre-DRAFT crop-check).")
    ap.add_argument("shoot_dir", help="folder of photos for one item")
    ap.add_argument("--check", action="store_true", help="report per-photo verdicts only; write nothing")
    ap.add_argument("--apply", action="store_true", help="overwrite originals in place (backs up to .orig/ first)")
    ap.add_argument("--out", default=None, help="output dir (default <shoot-dir>/cropped)")
    ap.add_argument("--aspect", default="1:1", help="target aspect W:H (default 1:1; 'orig' to keep)")
    ap.add_argument("--pad", type=float, default=0.12, help="margin around subject (fraction, default 0.12)")
    ap.add_argument("--threshold", type=float, default=0.06, help="off-center flag cutoff (frac of short side)")
    ap.add_argument("--force", action="store_true", help="ignore the safety guards and crop anyway")
    args = ap.parse_args(argv)

    shoot = Path(args.shoot_dir)
    if not shoot.is_dir():
        ap.error(f"not a directory: {shoot}")
    aspect = _parse_aspect(args.aspect)
    imgs = list(_iter_images(shoot))
    if not imgs:
        print(f"no images in {shoot}")
        return 1

    if args.check:
        skipped = 0
        for p in imgs:
            fp = focal_point(p)
            why = crop_warning(fp, _crop_box(fp, aspect, args.pad))
            if why:
                skipped += 1
                print(f"  {p.name:16} SKIP  {why}")
            else:
                tag = "OFF-CENTER" if fp["offset"] > args.threshold else "centered"
                print(f"  {p.name:16} crop  offset={fp['offset']*100:5.1f}%  {tag}")
        print(f"{len(imgs) - skipped}/{len(imgs)} photos can be safely cropped "
              f"({skipped} would be skipped, originals kept)")
        return 0

    out_dir = Path(args.out) if args.out else shoot / "cropped"
    if not args.apply:
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        (shoot / ".orig").mkdir(exist_ok=True)

    rows, cropped, skipped = [], 0, 0
    for p in imgs:
        before = _load_bgr(p)
        fp = _focal_from_bgr(before)
        box = _crop_box(fp, aspect, args.pad)
        why = None if args.force else crop_warning(fp, box)

        if why:
            skipped += 1
            after = before
            print(f"  {p.name:16} SKIPPED  {why}")
            # Pass the original through byte-for-byte: no re-encode, and under
            # --apply don't touch the file (or its backup) at all.
            if not args.apply:
                shutil.copyfile(p, out_dir / p.name)
        else:
            cropped += 1
            x0, y0, x1, y1 = box
            after = before[y0:y1, x0:x1]
            if args.apply:
                _save_bgr(shoot / ".orig" / p.name, before)
                _save_bgr(p, after)
            else:
                _save_bgr(out_dir / p.name, after)

        label = f"{p.name}  SKIPPED: {why}" if why else f"{p.name}  off={fp['offset']*100:.0f}% cropped"
        rows.append((label, before, after))

    # On --apply the shoot dir IS the listing-photo dir, so keep the review
    # sheet OUT of it (Windows glob is case-insensitive → crop_review.jpg would
    # otherwise be picked up as a listing photo). Stash it in .orig/.
    sheet = (shoot / ".orig" / "crop_review.jpg") if args.apply else (out_dir / "crop_review.jpg")
    _contact_sheet(rows, sheet)
    where = "in place (originals -> .orig/)" if args.apply else str(out_dir)
    print(f"{len(imgs)} photos -> {where}")
    print(f"  {cropped} centered, {skipped} skipped (original kept — see reasons above)")
    print(f"  review: {sheet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
