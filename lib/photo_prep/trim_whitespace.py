"""Trim near-white background from product photos.

Sub-step of PHOTO PREP. For each image in the input directory:

  1. Sample the four corners (5% patches) and median them to find the
     actual background color — adapts to off-white / warm-room lighting
     instead of assuming pure #fff.
  2. Outlier-reject any corner whose color disagrees with the others
     (e.g. subject extending into a corner, or one corner in shadow).
  3. Convert image and background sample to CIE LAB.
  4. For every pixel, compute LAB Delta-E from the sampled background.
     Pixels beyond the fuzziness threshold are "subject."
  5. Find the subject bounding box, add a small margin, crop the
     original RGB image to that bbox, save.

Outputs go to `<input-dir>/trimmed/` — originals untouched.

Safety bailouts (the file is copied through untouched, flagged in the
report):
  - Kept area >95% — subject fills the frame, nothing meaningful to trim
  - Kept area <5%  — corner-sampled threshold likely failed; refuse to
    ruin the file

CLI:
    python trim_whitespace.py <input-dir> [--margin-pct P] [--fuzziness F]
                                          [--workers N] [--quiet]
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif"}
DEFAULT_WORKER_CAP = 4
DEFAULT_FUZZINESS = 12.0      # CIE Delta-E76 threshold
DEFAULT_MARGIN_PCT = 0.03     # 3% of min(width, height)
CORNER_PATCH_PCT = 0.05       # 5% of width/height per corner
KEEP_AREA_FLOOR = 0.05        # bail if cropped area < 5% of original
KEEP_AREA_CEILING = 0.95      # bail if cropped area > 95% of original
MAD_OUTLIER_FACTOR = 2.5      # corner-rejection threshold (Iglewicz-Hoaglin)


@dataclass
class FileResult:
    source: Path
    output: Path
    status: str           # "trimmed", "passthrough", "skipped", "error"
    original_size: tuple[int, int]   # (w, h)
    final_size: tuple[int, int]      # (w, h)
    area_kept_pct: float  # 0-100
    note: str             # human-readable detail
    error: str | None


def _pil_lab_to_cie(arr_pil_lab: np.ndarray) -> np.ndarray:
    """Convert Pillow's LAB encoding (uint8 per channel) to CIE LAB.

    Pillow's encoding (verified empirically against known sRGB values):
      - L: scaled to [0, 255] (not CIE's [0, 100]).
      - a, b: signed int8 reinterpreted as uint8 — neutral = 0, positive
        values stored directly (0..127), negative values wrap into
        128..255. So uint8 200 means CIE a = -56.

    Verified reference values:
      pure white  (255,255,255) -> PIL LAB (255,   0,   0)
      pure red    (255,  0,  0) -> PIL LAB (138,  81,  70)   [CIE ~(53, 80, 67)]
      pure black  (  0,  0,  0) -> PIL LAB (  0,   0,   0)
      mid gray    (128,128,128) -> PIL LAB (137,   0,   0)
    """
    out = arr_pil_lab.astype(np.float32)
    out[..., 0] *= (100.0 / 255.0)
    # a, b: reinterpret as signed (subtract 256 where value >= 128).
    out[..., 1] = np.where(out[..., 1] >= 128, out[..., 1] - 256, out[..., 1])
    out[..., 2] = np.where(out[..., 2] >= 128, out[..., 2] - 256, out[..., 2])
    return out


def _sample_background_color(arr_lab_cie: np.ndarray) -> tuple[np.ndarray, str]:
    """Return median background color from outlier-rejected corner samples.

    Returns (bg_color_lab, note) where note describes which corners were
    used / rejected so the caller can include it in the report.
    """
    h, w = arr_lab_cie.shape[:2]
    ph = max(1, int(h * CORNER_PATCH_PCT))
    pw = max(1, int(w * CORNER_PATCH_PCT))
    corners = {
        "TL": arr_lab_cie[:ph, :pw],
        "TR": arr_lab_cie[:ph, -pw:],
        "BL": arr_lab_cie[-ph:, :pw],
        "BR": arr_lab_cie[-ph:, -pw:],
    }
    # Per-corner median color
    medians = {k: np.median(v.reshape(-1, 3), axis=0) for k, v in corners.items()}
    medians_arr = np.stack(list(medians.values()))

    # MAD-based outlier rejection
    grand_median = np.median(medians_arr, axis=0)
    deviations = np.linalg.norm(medians_arr - grand_median, axis=1)
    mad = np.median(deviations)
    threshold = max(MAD_OUTLIER_FACTOR * mad, 5.0)  # 5.0 floor for very-uniform cases
    keep_mask = deviations <= threshold

    if np.sum(keep_mask) >= 2:
        bg = np.median(medians_arr[keep_mask], axis=0)
        kept = [k for k, ok in zip(medians.keys(), keep_mask) if ok]
        rejected = [k for k, ok in zip(medians.keys(), keep_mask) if not ok]
        if rejected:
            note = f"corners used: {','.join(kept)} (rejected {','.join(rejected)})"
        else:
            note = "all 4 corners agree"
    else:
        # Too many outliers — fall back to grand median across all four
        bg = grand_median
        note = "corner disagreement; using all-corner median"

    return bg, note


def _find_subject_bbox(
    arr_lab_cie: np.ndarray,
    bg_lab: np.ndarray,
    fuzziness: float,
) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) of subject pixels, or None if empty."""
    diff = arr_lab_cie - bg_lab
    delta_e = np.sqrt(np.sum(diff * diff, axis=2))
    mask = delta_e > fuzziness  # True = subject

    if not mask.any():
        return None

    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    top = int(np.argmax(rows))
    bottom = int(len(rows) - np.argmax(rows[::-1]))
    left = int(np.argmax(cols))
    right = int(len(cols) - np.argmax(cols[::-1]))
    return left, top, right, bottom


def _apply_margin(
    bbox: tuple[int, int, int, int],
    img_size: tuple[int, int],
    margin_pct: float,
) -> tuple[int, int, int, int]:
    """Expand bbox by margin (clamped to image bounds)."""
    left, top, right, bottom = bbox
    w, h = img_size
    margin = int(min(w, h) * margin_pct)
    return (
        max(0, left - margin),
        max(0, top - margin),
        min(w, right + margin),
        min(h, bottom + margin),
    )


def _save_cropped(
    img_rgb: Image.Image,
    dst: Path,
    ext: str,
    src_qtables: dict | None,
) -> None:
    """Save a cropped Image, matching the source JPEG's quantization tables
    where possible so re-encode quality matches the source."""
    if ext in (".jpg", ".jpeg"):
        kwargs: dict = {"format": "JPEG"}
        if src_qtables:
            # qtables= takes the same dict shape JpegImageFile.quantization
            # exposes (channel_idx -> 64 ints). Mirrors source quality.
            kwargs["qtables"] = src_qtables
        else:
            kwargs["quality"] = 92  # fallback when source had no tables
        img_rgb.save(dst, **kwargs)
    elif ext in (".heic", ".heif"):
        img_rgb.save(dst, format="HEIF", quality=92)
    else:
        # Defensive: SUPPORTED_EXTENSIONS already filtered, but be explicit
        img_rgb.save(dst)


def _trim_one(args: tuple[Path, Path, float, float]) -> FileResult:
    """Worker: trim one image."""
    src, out_dir, fuzziness, margin_pct = args
    dst = out_dir / src.name

    try:
        # Open once, extract source metadata, pull pixels into numpy.
        # Doing all I/O inside the `with` block makes the source file
        # handle close cleanly before we do any expensive numpy work.
        with Image.open(src) as img_in:
            src_qtables = getattr(img_in, "quantization", None)
            arr_rgb = np.array(img_in.convert("RGB"))
            arr_lab_pil = np.array(img_in.convert("LAB"))

        arr_lab_cie = _pil_lab_to_cie(arr_lab_pil)
        bg_color, corner_note = _sample_background_color(arr_lab_cie)
        bbox = _find_subject_bbox(arr_lab_cie, bg_color, fuzziness)

        original_h, original_w = arr_rgb.shape[:2]
        original_size = (original_w, original_h)
        original_area = original_w * original_h

        if bbox is None:
            return _passthrough(
                src, dst, original_size,
                note=f"no subject detected ({corner_note})",
            )

        bbox_margined = _apply_margin(bbox, original_size, margin_pct)
        left, top, right, bottom = bbox_margined
        cropped_w = right - left
        cropped_h = bottom - top
        cropped_area = cropped_w * cropped_h
        area_kept = cropped_area / original_area

        if area_kept >= KEEP_AREA_CEILING:
            return _passthrough(
                src, dst, original_size,
                note=f"subject fills frame ({area_kept*100:.0f}% kept; {corner_note})",
            )
        if area_kept <= KEEP_AREA_FLOOR:
            return _passthrough(
                src, dst, original_size,
                note=f"crop suspicious ({area_kept*100:.1f}% kept; {corner_note})",
            )

        # Crop directly on the numpy array (faster than PIL.crop on the Image).
        cropped_arr = arr_rgb[top:bottom, left:right]
        cropped_img = Image.fromarray(cropped_arr)
        _save_cropped(cropped_img, dst, src.suffix.lower(), src_qtables)

        return FileResult(
            source=src,
            output=dst,
            status="trimmed",
            original_size=original_size,
            final_size=(cropped_w, cropped_h),
            area_kept_pct=area_kept * 100,
            note=corner_note,
            error=None,
        )

    except Exception as e:
        return FileResult(
            source=src,
            output=dst,
            status="error",
            original_size=(0, 0),
            final_size=(0, 0),
            area_kept_pct=0.0,
            note="",
            error=str(e),
        )


def _passthrough(
    src: Path,
    dst: Path,
    size: tuple[int, int],
    note: str,
) -> FileResult:
    """Copy the original through byte-identical. No re-encode, no quality loss."""
    shutil.copy2(src, dst)
    return FileResult(
        source=src,
        output=dst,
        status="passthrough",
        original_size=size,
        final_size=size,
        area_kept_pct=100.0,
        note=note,
        error=None,
    )


def find_images(in_dir: Path) -> list[Path]:
    return sorted(
        p for p in in_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def trim_directory(
    in_dir: Path,
    workers: int | None = None,
    fuzziness: float = DEFAULT_FUZZINESS,
    margin_pct: float = DEFAULT_MARGIN_PCT,
    quiet: bool = False,
) -> list[FileResult]:
    if not in_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {in_dir}")

    sources = find_images(in_dir)
    if not sources:
        if not quiet:
            print(f"No supported images in {in_dir}")
        return []

    out_dir = in_dir / "trimmed"
    out_dir.mkdir(exist_ok=True)

    if workers is None:
        workers = min(os.cpu_count() or 1, DEFAULT_WORKER_CAP)
    workers = max(1, workers)

    work_items = [(src, out_dir, fuzziness, margin_pct) for src in sources]

    started_at = time.monotonic()
    results: list[FileResult] = []

    if workers == 1:
        for i, item in enumerate(work_items, 1):
            r = _trim_one(item)
            results.append(r)
            if not quiet:
                _print_progress(i, len(work_items), r)
    else:
        with multiprocessing.Pool(processes=workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_trim_one, work_items), 1):
                results.append(r)
                if not quiet:
                    _print_progress(i, len(work_items), r)

    elapsed = time.monotonic() - started_at
    _print_summary(results, out_dir, elapsed)
    return results


def _print_progress(idx: int, total: int, r: FileResult) -> None:
    name = r.source.name
    if r.error:
        print(f"[{idx}/{total}] {name} -> ERROR: {r.error}")
    elif r.status == "trimmed":
        ow, oh = r.original_size
        fw, fh = r.final_size
        print(f"[{idx}/{total}] {name}: {ow}x{oh} -> {fw}x{fh}"
              f"  ({r.area_kept_pct:.0f}% kept; {r.note})")
    elif r.status == "passthrough":
        print(f"[{idx}/{total}] {name}: passed through  ({r.note})")
    else:
        print(f"[{idx}/{total}] {name}: {r.status}")


def _print_summary(results: list[FileResult], out_dir: Path, elapsed: float) -> None:
    trimmed = sum(1 for r in results if r.status == "trimmed")
    passed  = sum(1 for r in results if r.status == "passthrough")
    errored = sum(1 for r in results if r.status == "error")

    print()
    parts = [f"Trimmed {trimmed}"]
    if passed:
        parts.append(f"passed through {passed}")
    if errored:
        parts.append(f"FAILED {errored}")
    print(f"Done. {', '.join(parts)} in {elapsed:.1f}s. Output: {out_dir}")

    if passed:
        print()
        print("Passed-through files (no trim applied):")
        for r in results:
            if r.status == "passthrough":
                print(f"  - {r.source.name}: {r.note}")


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pylint: disable=broad-except
        pass

    parser = argparse.ArgumentParser(
        description="Trim near-white background from product photos.",
    )
    parser.add_argument("in_dir", type=Path,
                        help="Directory containing source images.")
    parser.add_argument("--fuzziness", type=float, default=DEFAULT_FUZZINESS,
                        help=f"CIE Delta-E76 threshold (default {DEFAULT_FUZZINESS}). "
                             "Lower = stricter / tighter crop.")
    parser.add_argument("--margin-pct", type=float, default=DEFAULT_MARGIN_PCT,
                        help=f"Margin around subject as fraction of min(W,H) "
                             f"(default {DEFAULT_MARGIN_PCT}).")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"Parallel workers (default min(cpu_count, "
                             f"{DEFAULT_WORKER_CAP})).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-file progress; keep summary.")
    args = parser.parse_args()

    results = trim_directory(
        in_dir=args.in_dir,
        workers=args.workers,
        fuzziness=args.fuzziness,
        margin_pct=args.margin_pct,
        quiet=args.quiet,
    )

    failures = [r for r in results if r.status == "error"]
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    _cli()
