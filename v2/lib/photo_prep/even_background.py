"""Even out the near-white backdrop around the subject.

Sub-step of PHOTO PREP. Detects background regions using the same
corner-sampling + LAB-distance logic as `trim_whitespace`, then replaces
background pixels with the median corner color so the backdrop becomes
uniform. Subject pixels are untouched. An anti-aliased mask keeps the
boundary natural-looking.

The backdrop is kept (not removed) — its color stays close to the
original near-white. What goes away is the uneven lighting, shadows, and
sensor noise that the algorithm detects as background. Result: a
listing-ready uniform backdrop without doing a hard cut-out.

CLI:
    python even_background.py <input-dir> [--fuzziness F] [--edge-band E]
                                          [--workers N] [--quiet]
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
import pillow_heif

from trim_whitespace import (
    CORNER_PATCH_PCT,
    DEFAULT_FUZZINESS,
    DEFAULT_WORKER_CAP,
    MAD_OUTLIER_FACTOR,
    SUPPORTED_EXTENSIONS,
    _pil_lab_to_cie,
    _save_cropped,
)

pillow_heif.register_heif_opener()


@dataclass
class FileResult:
    source: Path
    output: Path
    status: str               # "evened" | "error"
    bg_rgb: tuple[int, int, int]
    subject_pct: float        # approx % of image kept as subject (alpha sum / pixel count)
    corner_note: str
    fuzziness: float          # the threshold actually used
    error: str | None


def _sample_background(arr_rgb: np.ndarray, arr_lab_cie: np.ndarray) -> tuple[np.ndarray, np.ndarray, str]:
    """Sample the four corners and return:
      - bg_rgb  (float32, shape (3,))  — for compositing
      - bg_lab  (float32, shape (3,))  — for distance math
      - corner_note (str)              — which corners were used / rejected
    Outlier-rejects corners using the same MAD test as trim_whitespace.
    """
    h, w = arr_rgb.shape[:2]
    ph = max(1, int(h * CORNER_PATCH_PCT))
    pw = max(1, int(w * CORNER_PATCH_PCT))

    corner_slices = {
        "TL": (slice(None, ph), slice(None, pw)),
        "TR": (slice(None, ph), slice(-pw, None)),
        "BL": (slice(-ph, None), slice(None, pw)),
        "BR": (slice(-ph, None), slice(-pw, None)),
    }

    lab_medians = {k: np.median(arr_lab_cie[sl].reshape(-1, 3), axis=0)
                   for k, sl in corner_slices.items()}
    rgb_medians = {k: np.median(arr_rgb[sl].reshape(-1, 3), axis=0)
                   for k, sl in corner_slices.items()}

    lab_arr = np.stack(list(lab_medians.values()))
    grand_lab = np.median(lab_arr, axis=0)
    deviations = np.linalg.norm(lab_arr - grand_lab, axis=1)
    mad = np.median(deviations)
    threshold = max(MAD_OUTLIER_FACTOR * mad, 5.0)
    keep_mask = deviations <= threshold

    keys = list(lab_medians.keys())
    if np.sum(keep_mask) >= 2:
        accepted = [k for k, ok in zip(keys, keep_mask) if ok]
        rejected = [k for k, ok in zip(keys, keep_mask) if not ok]
        bg_lab = np.median(np.stack([lab_medians[k] for k in accepted]), axis=0)
        bg_rgb = np.median(np.stack([rgb_medians[k] for k in accepted]), axis=0)
        note = (f"corners used: {','.join(accepted)} (rejected {','.join(rejected)})"
                if rejected else "all 4 corners agree")
    else:
        bg_lab = grand_lab
        bg_rgb = np.median(np.stack(list(rgb_medians.values())), axis=0)
        note = "corner disagreement; using all-corner median"

    return bg_rgb.astype(np.float32), bg_lab.astype(np.float32), note


def _even_one(args: tuple[Path, Path, float, float | None]) -> FileResult:
    src, out_dir, fuzziness, edge_band = args
    dst = out_dir / src.name

    try:
        with Image.open(src) as img_in:
            src_qtables = getattr(img_in, "quantization", None)
            arr_rgb = np.array(img_in.convert("RGB"))
            arr_lab_pil = np.array(img_in.convert("LAB"))

        arr_lab_cie = _pil_lab_to_cie(arr_lab_pil)
        bg_rgb, bg_lab, corner_note = _sample_background(arr_rgb, arr_lab_cie)

        # Per-pixel LAB distance from background
        diff = arr_lab_cie - bg_lab
        delta_e = np.sqrt(np.sum(diff * diff, axis=2))

        # Soft mask: 0 at ΔE <= fuzziness (pure bg), 1 at ΔE >= fuzziness + band (pure subject).
        # Linear ramp in between. Default band = fuzziness, so the ramp runs fz → 2*fz.
        band = float(edge_band) if edge_band is not None else float(fuzziness)
        ramp_lo = fuzziness
        ramp_hi = fuzziness + band
        soft_mask = np.clip(
            (delta_e - ramp_lo) / max(ramp_hi - ramp_lo, 1e-6),
            0.0, 1.0,
        )

        # Composite: subject pixels keep their original color; bg pixels become bg_rgb
        # (smoothly blended via the soft mask). Done in float, clipped, cast back.
        soft_mask_3 = soft_mask[..., None].astype(np.float32)
        arr_rgb_f = arr_rgb.astype(np.float32)
        bg_broadcast = bg_rgb.reshape(1, 1, 3)
        result = soft_mask_3 * arr_rgb_f + (1.0 - soft_mask_3) * bg_broadcast
        result_u8 = np.clip(result, 0, 255).astype(np.uint8)

        out_img = Image.fromarray(result_u8)
        _save_cropped(out_img, dst, src.suffix.lower(), src_qtables)

        return FileResult(
            source=src,
            output=dst,
            status="evened",
            bg_rgb=tuple(int(round(v)) for v in bg_rgb.tolist()),
            subject_pct=float(soft_mask.mean()) * 100.0,
            corner_note=corner_note,
            fuzziness=float(fuzziness),
            error=None,
        )

    except Exception as e:
        return FileResult(
            source=src,
            output=dst,
            status="error",
            bg_rgb=(0, 0, 0),
            subject_pct=0.0,
            corner_note="",
            fuzziness=float(fuzziness),
            error=str(e),
        )


def find_images(in_dir: Path) -> list[Path]:
    return sorted(
        p for p in in_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def even_directory(
    in_dir: Path,
    workers: int | None = None,
    fuzziness: float = DEFAULT_FUZZINESS,
    edge_band: float | None = None,
    quiet: bool = False,
) -> list[FileResult]:
    if not in_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {in_dir}")

    sources = find_images(in_dir)
    if not sources:
        if not quiet:
            print(f"No supported images in {in_dir}")
        return []

    out_dir = in_dir / "evened"
    out_dir.mkdir(exist_ok=True)

    if workers is None:
        workers = min(os.cpu_count() or 1, DEFAULT_WORKER_CAP)
    workers = max(1, workers)

    work_items = [(src, out_dir, fuzziness, edge_band) for src in sources]

    started = time.monotonic()
    results: list[FileResult] = []

    if workers == 1:
        for i, item in enumerate(work_items, 1):
            r = _even_one(item)
            results.append(r)
            if not quiet:
                _print_progress(i, len(work_items), r)
    else:
        with multiprocessing.Pool(processes=workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_even_one, work_items), 1):
                results.append(r)
                if not quiet:
                    _print_progress(i, len(work_items), r)

    elapsed = time.monotonic() - started
    _print_summary(results, out_dir, elapsed)
    return results


def _print_progress(idx: int, total: int, r: FileResult) -> None:
    if r.error:
        print(f"[{idx}/{total}] {r.source.name}: ERROR: {r.error}")
        return
    br, bg_, bb = r.bg_rgb
    print(f"[{idx}/{total}] {r.source.name}: "
          f"bg=RGB({br},{bg_},{bb})  "
          f"subject ~{r.subject_pct:.0f}%  "
          f"(fz={r.fuzziness:.0f}; {r.corner_note})")


def _print_summary(results: list[FileResult], out_dir: Path, elapsed: float) -> None:
    ok = sum(1 for r in results if r.status == "evened")
    err = sum(1 for r in results if r.status == "error")
    parts = [f"Evened {ok}"]
    if err:
        parts.append(f"FAILED {err}")
    print()
    print(f"Done. {', '.join(parts)} in {elapsed:.1f}s. Output: {out_dir}")


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pylint: disable=broad-except
        pass

    p = argparse.ArgumentParser(
        description="Even out the near-white backdrop around the subject "
                    "(detect via corner-sampling + LAB distance, replace with median corner color)."
    )
    p.add_argument("in_dir", type=Path,
                   help="Directory of input images.")
    p.add_argument("--fuzziness", type=float, default=DEFAULT_FUZZINESS,
                   help=f"Delta-E threshold for subject detection (default {DEFAULT_FUZZINESS}). "
                        "Use the same value that produced a good crop in trim_whitespace.")
    p.add_argument("--edge-band", type=float, default=None,
                   help="Soft-edge width in Delta-E units. Default = fuzziness "
                        "(transition ramps from fz to 2*fz).")
    p.add_argument("--workers", type=int, default=None,
                   help=f"Parallel workers (default min(cpu_count, {DEFAULT_WORKER_CAP})).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-file progress; keep summary.")
    args = p.parse_args()

    results = even_directory(
        in_dir=args.in_dir,
        workers=args.workers,
        fuzziness=args.fuzziness,
        edge_band=args.edge_band,
        quiet=args.quiet,
    )
    sys.exit(1 if any(r.status == "error" for r in results) else 0)


if __name__ == "__main__":
    _cli()
