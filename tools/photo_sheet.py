#!/usr/bin/env python
"""One labelled photo sheet for a yes/no question, for chat (`/yn`).

Russell answers a question about an item faster when he sees it first. This
puts one to three frames side by side under a header with the item's name and
its price (or estimate), numbers each frame and writes a JPEG to a temp dir.
The session sends that file and then asks the one question.

    python tools/photo_sheet.py --title "Pfaff 463 sewing machine" \\
        --price '$375' --dir inventory/<item> P1010123=front P1010131=badge

A frame is a path, or a name or id inside --dir (`P1010123` finds
`P1010123.JPG`). `=label` is optional; the file name is the default label.
Sources are never modified. Prints the sheet's path.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CELL = 520          # picture box, square
PAD = 18
HEAD_H = 92
LABEL_H = 46
MAX_FRAMES = 3
EXTS = (".jpg", ".jpeg", ".png", ".webp")
BG = (247, 246, 243)
INK = (22, 23, 26)
MUTED = (110, 112, 119)
GREEN = (28, 122, 76)


def _font(size: int, bold: bool = False):
    names = (("DejaVuSans-Bold.ttf", "arialbd.ttf") if bold
             else ("DejaVuSans.ttf", "arial.ttf"))
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def resolve(frame: str, base: Path | None) -> Path:
    """A frame argument -> an image file. Exact path first, then a name in
    `base`, then `<id>.<ext>` in `base` (any case)."""
    p = Path(frame)
    if p.is_file():
        return p
    if base is not None:
        if (base / frame).is_file():
            return base / frame
        hits = [f for f in base.iterdir()
                if f.is_file() and f.stem.lower() == p.name.lower()
                and f.suffix.lower() in EXTS]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SystemExit(f"frame {frame!r} matches {len(hits)} files in {base}: "
                             + ", ".join(sorted(h.name for h in hits)))
    raise SystemExit(f"frame {frame!r} not found" + (f" in {base}" if base else ""))


def parse(arg: str) -> tuple[str, str]:
    frame, _, label = arg.partition("=")
    return frame, label.strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "sheet"


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text + "..."


def build(frames: list[tuple[Path, str]], title: str, price: str = "",
          out: Path | None = None) -> Path:
    if not 1 <= len(frames) <= MAX_FRAMES:
        raise SystemExit(f"give 1 to {MAX_FRAMES} frames, got {len(frames)}")
    width = PAD + len(frames) * (CELL + PAD)
    height = HEAD_H + CELL + LABEL_H + PAD
    sheet = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(sheet)

    price_font, title_font, label_font = _font(44, True), _font(34, True), _font(26)
    price_w = draw.textlength(price, font=price_font) if price else 0
    draw.text((width - PAD - price_w, HEAD_H // 2), price, font=price_font,
              fill=GREEN, anchor="lm")
    title_w = width - 3 * PAD - price_w
    draw.text((PAD, HEAD_H // 2), _fit(draw, title, title_font, title_w),
              font=title_font, fill=INK, anchor="lm")

    for i, (path, label) in enumerate(frames):
        x = PAD + i * (CELL + PAD)
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((CELL, CELL))
            sheet.paste(im, (x + (CELL - im.width) // 2, HEAD_H + (CELL - im.height) // 2))
        text = _fit(draw, f"{i + 1}  {label or path.stem}", label_font, CELL)
        draw.text((x, HEAD_H + CELL + LABEL_H // 2), text, font=label_font,
                  fill=MUTED, anchor="lm")

    if out is None:
        out = Path(tempfile.gettempdir()) / "yn" / f"{_slug(title)}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=88)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("frames", nargs="+", metavar="FRAME[=LABEL]")
    ap.add_argument("--title", required=True, help="the item's name, as Russell knows it")
    ap.add_argument("--price", default="", help="ask or estimate, shown in the header")
    ap.add_argument("--dir", type=Path, help="item folder that frame names resolve in")
    ap.add_argument("--out", type=Path, help="default: <tmp>/yn/<title>.jpg")
    a = ap.parse_args(argv)
    frames = [(resolve(f, a.dir), label) for f, label in map(parse, a.frames)]
    print(build(frames, a.title, a.price, a.out))


if __name__ == "__main__":
    sys.exit(main())
