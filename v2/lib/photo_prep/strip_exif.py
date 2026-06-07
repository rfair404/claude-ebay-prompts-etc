"""Bulk EXIF strip — sub-step 1 of PHOTO PREP.

Strips EXIF metadata from every JPG/HEIC image in a shoot directory.
Writes the stripped copies to `<shoot-dir>/no-exif/`; originals are
untouched.

Why strip EXIF (and not auto-orient first): on the user's camera, the
stored pixels are already upright but the EXIF Orientation tag is a
false claim — viewers that honor it display the image rotated. Removing
the tag makes viewers fall back to the stored pixel orientation, which
is correct. No pixel rotation is performed by this script.

CLI:
    python strip_exif.py <shoot-dir> [--workers N] [--quiet]
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
import pillow_heif

# Register pillow-heif's opener so PIL.Image.open handles .heic/.heif.
pillow_heif.register_heif_opener()


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif"}
DEFAULT_WORKER_CAP = 4


@dataclass
class FileResult:
    """Per-file outcome of the strip pass."""
    source: Path
    output: Path
    skipped: bool          # True when output existed and was up-to-date
    stripped_bytes: int    # size of the EXIF block removed (0 if none)
    error: str | None      # populated if save failed


def _output_path(src: Path, out_dir: Path) -> Path:
    """Resolve the matching output path for a source file."""
    return out_dir / src.name


def _pil_format_for(src: Path) -> str:
    """Pick the PIL save-format string based on file extension."""
    ext = src.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "JPEG"
    if ext in (".heic", ".heif"):
        return "HEIF"
    raise ValueError(f"Unsupported extension for {src.name}")


def _strip_one(args: tuple[Path, Path]) -> FileResult:
    """Worker function — strip EXIF from one file.

    Pure function so it can be pickled and run in a multiprocessing.Pool.
    """
    src, out_dir = args
    dst = _output_path(src, out_dir)

    # Idempotency check: skip if output exists and is at least as new as source.
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return FileResult(src, dst, skipped=True, stripped_bytes=0, error=None)

    try:
        with Image.open(src) as img:
            # Measure the original EXIF size so we can report what we stripped.
            original_exif = img.info.get("exif", b"")
            stripped_bytes = len(original_exif) if original_exif else 0

            fmt = _pil_format_for(src)
            save_kwargs: dict = {"format": fmt, "exif": b""}
            if fmt == "JPEG":
                # quality='keep' re-encodes at the source's exact quality
                # and quantization tables; subsampling='keep' preserves
                # chroma layout. Together they make the re-save effectively
                # lossless for JPGs.
                save_kwargs["quality"] = "keep"
                save_kwargs["subsampling"] = "keep"

            img.save(dst, **save_kwargs)

        return FileResult(src, dst, skipped=False, stripped_bytes=stripped_bytes, error=None)

    except Exception as e:
        return FileResult(src, dst, skipped=False, stripped_bytes=0, error=str(e))


def find_images(shoot_dir: Path) -> list[Path]:
    """List supported image files at the top level of shoot_dir.

    Does not recurse into subdirectories (notably, skips the no-exif/
    output folder if it already exists).
    """
    return sorted(
        p for p in shoot_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def strip_directory(
    shoot_dir: Path,
    workers: int | None = None,
    quiet: bool = False,
) -> list[FileResult]:
    """Run the EXIF-strip pass across every supported image in shoot_dir.

    Returns the per-file results so callers can introspect.
    """
    if not shoot_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {shoot_dir}")

    sources = find_images(shoot_dir)
    if not sources:
        if not quiet:
            print(f"No supported images in {shoot_dir} (looking for: "
                  f"{', '.join(sorted(SUPPORTED_EXTENSIONS))})")
        return []

    out_dir = shoot_dir / "no-exif"
    out_dir.mkdir(exist_ok=True)

    if workers is None:
        workers = min(os.cpu_count() or 1, DEFAULT_WORKER_CAP)
    workers = max(1, workers)

    work_items = [(src, out_dir) for src in sources]

    started_at = time.monotonic()
    results: list[FileResult] = []

    # multiprocessing.Pool for parallel encode-bound work. Single-worker
    # falls through to a plain map to keep stack traces clean and avoid
    # the Pool-startup overhead for tiny shoots.
    if workers == 1:
        for i, item in enumerate(work_items, 1):
            r = _strip_one(item)
            results.append(r)
            if not quiet:
                _print_progress(i, len(work_items), r)
    else:
        with multiprocessing.Pool(processes=workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_strip_one, work_items), 1):
                results.append(r)
                if not quiet:
                    _print_progress(i, len(work_items), r)

    elapsed = time.monotonic() - started_at

    # Summary always prints; --quiet only suppresses per-file progress lines.
    _print_summary(results, out_dir, elapsed)

    return results


def _print_progress(idx: int, total: int, r: FileResult) -> None:
    """One-line progress for a finished file."""
    if r.error:
        print(f"[{idx}/{total}] {r.source.name} -> ERROR: {r.error}")
    elif r.skipped:
        print(f"[{idx}/{total}] {r.source.name} -> no-exif/{r.output.name} (skipped, already up-to-date)")
    else:
        size_note = f"{r.stripped_bytes} bytes stripped" if r.stripped_bytes else "no EXIF found"
        print(f"[{idx}/{total}] {r.source.name} -> no-exif/{r.output.name} ({size_note})")


def _print_summary(results: list[FileResult], out_dir: Path, elapsed: float) -> None:
    """End-of-run rollup."""
    stripped = sum(1 for r in results if not r.skipped and not r.error)
    skipped  = sum(1 for r in results if r.skipped)
    errored  = sum(1 for r in results if r.error)
    total_bytes_stripped = sum(r.stripped_bytes for r in results if not r.error)

    parts = [f"Stripped {stripped}"]
    if skipped:
        parts.append(f"skipped {skipped}")
    if errored:
        parts.append(f"FAILED {errored}")
    summary = ", ".join(parts)

    print()
    print(f"Done. {summary} in {elapsed:.1f}s "
          f"({total_bytes_stripped} bytes of EXIF removed total). "
          f"Output: {out_dir}")


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pylint: disable=broad-except
        pass

    parser = argparse.ArgumentParser(
        description="Strip EXIF from every JPG/HEIC in a shoot directory.",
    )
    parser.add_argument("shoot_dir", type=Path,
                        help="Directory containing the source images.")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"Parallel worker count (default min(cpu_count, "
                             f"{DEFAULT_WORKER_CAP})).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-file progress; only print final summary.")
    args = parser.parse_args()

    results = strip_directory(
        shoot_dir=args.shoot_dir,
        workers=args.workers,
        quiet=args.quiet,
    )

    # Non-zero exit if any file failed, so shell pipelines can detect it.
    failures = [r for r in results if r.error]
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    _cli()
