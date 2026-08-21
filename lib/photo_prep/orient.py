#!/usr/bin/env python3
"""Manual orientation corrections — the human's call, recorded so it sticks.

EXIF only ever answers "what did the camera claim". The question that decides
whether a listing photo is good is different and cannot be read from metadata:
**would a buyer see the item the right way up?** A phone held at an angle, a
book laid on its side, a box photographed from the end — all produce files that
are perfectly correct by their EXIF and still wrong on the page. Buyers
complained about exactly this.

So corrections come from a person looking at the image, and this module's job is
to make that decision durable:

* rotations are stored in `<shoot-dir>/orientation.json`, keyed by filename, as
  degrees CLOCKWISE to apply on top of the EXIF-corrected source;
* the shipped file in `no-exif/` is always REBUILT from the source — never
  rotated in place — so applying twice cannot double-rotate, and re-running
  strip_exif can no longer silently revert a human's decision;
* the source frames are never modified.

CLI:
    # inspect what is recorded
    python -m lib.photo_prep.orient <shoot-dir>

    # set by draft photo order (1-based), or by filename
    python -m lib.photo_prep.orient <shoot-dir> --set "1=180,2=180,3=cw,4=ccw"
    python -m lib.photo_prep.orient <shoot-dir> --set "IMG_1234.jpg=ccw"

    # rebuild the shipped files from source + manifest (idempotent)
    python -m lib.photo_prep.orient <shoot-dir> --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps

MANIFEST = "orientation.json"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

# accepted spellings -> degrees clockwise
_WORDS = {"cw": 90, "90": 90, "right": 90,
          "180": 180, "flip": 180, "upside": 180, "upsidedown": 180,
          "ccw": 270, "270": 270, "left": 270, "-90": 270,
          "ok": 0, "none": 0, "0": 0, "fine": 0, "keep": 0}


def parse_rotation(token: str) -> int:
    t = token.strip().lower().replace(" ", "")
    if t not in _WORDS:
        raise ValueError(f"unrecognised rotation {token!r}; use cw / ccw / 180 / ok")
    return _WORDS[t]


def draft_photo_order(shoot: Path) -> list[str]:
    """Filenames in the order the draft lists them, so '#3' means what the
    review sheet showed as #3."""
    d = shoot / "draft.md"
    if not d.exists():
        return []
    txt = d.read_text(encoding="utf-8", errors="ignore")
    return [Path(p).name for p in
            re.findall(r'^\s*-\s*"([^"]+\.(?:JPG|jpg|jpeg|png))"', txt, re.M)]


def load_manifest(shoot: Path) -> dict[str, int]:
    p = shoot / MANIFEST
    if not p.exists():
        return {}
    try:
        return {k: int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except (json.JSONDecodeError, ValueError):
        return {}


def save_manifest(shoot: Path, man: dict[str, int]) -> None:
    (shoot / MANIFEST).write_text(
        json.dumps(dict(sorted(man.items())), indent=2), encoding="utf-8")


def _source_for(shoot: Path, name: str) -> Path | None:
    """The original frame a shipped file was derived from."""
    p = shoot / name
    if p.exists():
        return p
    stem = Path(name).stem
    for cand in shoot.iterdir():
        if cand.is_file() and cand.suffix.lower() in IMG_EXTS and cand.stem == stem:
            return cand
    return None


def apply(shoot: Path, quiet: bool = False) -> tuple[int, int]:
    """Rebuild no-exif/ from source + manifest. Idempotent by construction."""
    man = load_manifest(shoot)
    out_dir = shoot / "no-exif"
    out_dir.mkdir(exist_ok=True)
    built = rotated = 0
    names = draft_photo_order(shoot) or [p.name for p in sorted(out_dir.iterdir())
                                         if p.suffix.lower() in IMG_EXTS]
    for name in names:
        src = _source_for(shoot, name)
        if src is None:
            if not quiet:
                print(f"  ! no source frame for {name} — left as is")
            continue
        deg = man.get(name, 0) % 360
        with Image.open(src) as im:
            img = ImageOps.exif_transpose(im)       # camera's own claim first
            if deg == 90:
                img = img.transpose(Image.ROTATE_270)   # PIL rotates CCW
            elif deg == 180:
                img = img.transpose(Image.ROTATE_180)
            elif deg == 270:
                img = img.transpose(Image.ROTATE_90)
            kw = {"format": "JPEG", "exif": b"", "quality": 95, "subsampling": 0} \
                if src.suffix.lower() in (".jpg", ".jpeg") else {"exif": b""}
            img.save(out_dir / name, **kw)
        built += 1
        rotated += 1 if deg else 0
        if not quiet and deg:
            print(f"  {name}: rotated {deg}deg CW")
    if not quiet:
        print(f"{built} shipped file(s) rebuilt from source; {rotated} carry a manual rotation")
    return built, rotated


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Record and apply human orientation calls.")
    ap.add_argument("shoot_dir", type=Path)
    ap.add_argument("--set", dest="sets",
                    help='comma list of "<n|filename>=<cw|ccw|180|ok>"')
    ap.add_argument("--apply", action="store_true",
                    help="rebuild no-exif/ from source + manifest")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    shoot = args.shoot_dir
    man = load_manifest(shoot)
    order = draft_photo_order(shoot)

    if args.sets:
        for item in args.sets.split(","):
            if not item.strip():
                continue
            key, _, val = item.partition("=")
            key = key.strip()
            deg = parse_rotation(val)
            if key.isdigit():
                idx = int(key) - 1
                if not (0 <= idx < len(order)):
                    raise SystemExit(f"frame #{key} is out of range (draft lists "
                                     f"{len(order)} photos)")
                name = order[idx]
            else:
                name = key
            if deg:
                man[name] = deg
            else:
                man.pop(name, None)      # 'ok' clears any previous call
            print(f"  #{key} -> {name}: {deg}deg CW")
        save_manifest(shoot, man)

    if args.sets or args.apply:
        apply(shoot, quiet=args.quiet)
    else:
        if not man:
            print(f"No manual rotations recorded in {shoot / MANIFEST}")
        for i, name in enumerate(order, 1):
            print(f"  #{i:>2}  {name}  {man.get(name, 0)}deg")


if __name__ == "__main__":
    _cli()
