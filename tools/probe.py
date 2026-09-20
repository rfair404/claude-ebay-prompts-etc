#!/usr/bin/env python3
"""probe — read-only per-image diagnostics: PIL metadata + subject-mask measurement.

Promotes two of the scratch-script patterns #74 item 4 names (a PIL
image-probe run 14x and a cv2 frame-measure run 9x, per the session-log
audit) into one `tools/` subcommand, instead of an inline `python -c`/heredoc
rewritten each time. The measurement half reuses
`lib.photo_prep.subject.mask_for()` — the same subject-detection PREP already
runs for crop/colour — rather than re-deriving a bounding box with fresh cv2
code, matching this repo's "wrap the existing pure function" convention (see
the module-mapping table in docs/webapp-architecture.md).

    python -m lib.cli probe <image> [<image> ...]
    python -m lib.cli probe <image> --json

Read-only: opens each image, prints its metadata, writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _pil_info(path: Path) -> dict:
    """Format, mode, size, DPI, and EXIF orientation — the fields the
    scratch PIL probes kept re-deriving by hand."""
    from PIL import ExifTags, Image

    orientation_tag = next(
        (k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None
    )
    with Image.open(path) as im:
        info = {
            "format": im.format,
            "mode": im.mode,
            "width": im.width,
            "height": im.height,
            "dpi": im.info.get("dpi"),
            "exif_orientation": None,
        }
        if orientation_tag is not None:
            try:
                info["exif_orientation"] = im.getexif().get(orientation_tag)
            except Exception:
                pass  # corrupt/absent EXIF block — leave None, don't fail the probe
    info["bytes"] = path.stat().st_size
    return info


def _subject_info(path: Path) -> dict | None:
    """Subject bbox/coverage via the shared PREP detector. None (not an
    error) when cv2/numpy or the image itself isn't readable — the PIL half
    above still reports."""
    try:
        import cv2

        from photo_prep.subject import mask_for
    except ImportError:
        return None
    bgr = cv2.imread(str(path))
    if bgr is None:
        return None
    sm = mask_for(bgr)
    x, y, w, h = (int(v) for v in sm.bbox)
    return {
        "source": sm.source,
        "bbox": {"x": x, "y": y, "w": w, "h": h},
        "coverage": round(float(sm.coverage), 4),
        "bbox_frac": round(float(sm.bbox_frac), 4),
        "agreement": round(float(sm.agreement), 4),
    }


def probe_one(path: Path) -> dict:
    row = {"path": str(path), "pil": _pil_info(path)}
    subject = _subject_info(path)
    if subject is not None:
        row["subject"] = subject
    return row


def _print_text(row: dict) -> None:
    p = row["pil"]
    print(
        f"{row['path']}  {p['format']} {p['mode']} {p['width']}x{p['height']}"
        f"  {p['bytes']}B  exif_orientation={p['exif_orientation']}"
    )
    s = row.get("subject")
    if s:
        b = s["bbox"]
        print(
            f"  subject[{s['source']}]: bbox=({b['x']},{b['y']},{b['w']},{b['h']})"
            f"  coverage={s['coverage']}  bbox_frac={s['bbox_frac']}"
            f"  agreement={s['agreement']}"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="probe", description=__doc__.splitlines()[0]
    )
    ap.add_argument("images", nargs="+", type=Path, help="image file(s) to inspect")
    ap.add_argument(
        "--json", action="store_true",
        help="emit one JSON object per image instead of the text summary",
    )
    args = ap.parse_args(argv)

    exit_code = 0
    for path in args.images:
        if not path.exists():
            print(f"probe: {path}: not found", file=sys.stderr)
            exit_code = 1
            continue
        try:
            row = probe_one(path)
        except Exception as exc:
            print(f"probe: {path}: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        if args.json:
            print(json.dumps(row))
        else:
            _print_text(row)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
