#!/usr/bin/env python
"""Render a shoot's CURRENT state as one wide preview card, for chat.

The Frame Check page is a good document and a bad conversation. It cannot reach
the CLI, so every answer costs the operator a copy-paste, and by the time the
command runs the page they judged is stale. This is the other half: a single
image, refreshed after every change, showing exactly what the shoot looks like
RIGHT NOW with each frame numbered so an answer is one short sentence.

    "3 -> 180, 7 crop off"

Numbers are stable — they are the manifest's photo order, not a sort of
whatever happens to be on disk — so a number means the same frame across
refreshes. Every cell states its own decisions, because a picture with no
label cannot be argued with.

Sources are never modified. The card is built from the same rendered bytes
that would ship (listing/ when it exists, else the picked preset, else the
source at its recorded rotation), so what is shown is what is agreed.

    python tools/prep_card.py <shoot>            # -> <shoot>/.prep/card.jpg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CELL = 460          # picture box, square
PAD = 18
LABEL_H = 74
COLS = 4
BG = (247, 246, 243)
INK = (22, 23, 26)
MUTED = (110, 112, 119)
GREEN = (28, 122, 76)
AMBER = (180, 105, 14)
RED = (164, 75, 84)


def _font(size: int, bold: bool = False):
    for name in (("arialbd.ttf", "seguisb.ttf") if bold else ("arial.ttf", "segoeui.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _shipping_bytes(shoot: Path, name: str, rec: dict, settled: bool):
    """The pixels the operator is being asked about.

    `listing/` is only the answer once the run that produced it is approved.
    Reaching for it earlier is how this tool lied on its first outing: a shoot
    prepped months ago by the old chain still had a listing/ directory, so the
    card rendered THOSE frames — stale rotations, stale crops — under labels
    describing decisions taken since. A stale picture under a fresh label is
    worse than no picture, because it reviews as agreement.

    While a stage is still open, the truth is the SOURCE at the rotation and
    inside the crop box currently recorded. That is the decision under review —
    and after the auto first pass BOTH of those are decided, so a card that
    showed the rotation but not the crop would be reviewing half the proposal.
    """
    if settled:
        live = shoot / "listing" / name
        if live.exists():
            return Image.open(live), "listing"

    im = ImageOps.exif_transpose(Image.open(shoot / name))
    ang = int(rec.get("orientation", {}).get("subject_angle") or 0)
    if ang:
        im = im.rotate(-ang, expand=True)
    where = "source @ recorded rotation"

    crop = rec.get("crop") or {}
    box = crop.get("box")
    # The box was planned on the upright frame at full resolution, which is
    # exactly what `im` is here. Guard the bounds anyway: a manifest edited by
    # hand, or a source replaced since the plan, must degrade to the uncropped
    # frame rather than to a PIL exception in the middle of a contact sheet.
    if crop.get("applied") and box and box[2] <= im.width and box[3] <= im.height:
        im = im.crop(tuple(box))
        where = "rotated + cropped"
    return im, where


def _state_line(rec: dict, settled: set) -> tuple[str, tuple[int, int, int]]:
    o = rec.get("orientation", {})
    bits = [f"rot {o.get('applied', 0)}deg"]
    colour = GREEN

    if o.get("needs_ask"):
        bits.append("ASK")
        colour = RED
    elif o.get("osd_proposal") is not None and o.get("osd_proposal") != o.get("subject_angle"):
        bits.append(f"osd said {o['osd_proposal']}")
        colour = AMBER
    elif "needs a look" in " ".join(o.get("notes") or []):
        bits.append("unread")
        colour = AMBER

    # Only report a stage that has actually been agreed. The manifest may carry
    # crop numbers from an earlier run; printing them beside an
    # unapproved rotation describes geometry measured on pixels nobody kept.
    crop = (rec.get("crop") or {}) if {"crop", "_deciding"} & settled else {}
    if crop.get("applied"):
        bits.append("cropped")
    elif crop.get("reason"):
        r = crop["reason"]
        if crop.get("operator"):
            # Say WHOSE decision it was. An override inherited from an earlier
            # run reads exactly like one made today, and "as shot" on every
            # frame is how a stage nobody opened passes as a stage agreed.
            bits.append("as shot (held over)")
        else:
            bits.append("crop refused")

    # A shoot squared before the unskew stage was removed still replays that
    # warp, so say so — the picture on this card is not the raw frame.
    if (rec.get("unskew") or {}).get("applied"):
        bits.append(f"squared {rec['unskew'].get('tilt_deg', 0):.1f}deg (legacy)")

    return " · ".join(bits), colour


def build(shoot: Path) -> Path:
    m = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
    photos: dict = m.get("photos") or {}
    if not photos:
        raise SystemExit(f"{shoot.name}: nothing in the manifest — run --check first")

    names = list(photos)
    rows = (len(names) + COLS - 1) // COLS
    head_h = 92
    W = COLS * (CELL + PAD) + PAD
    H = head_h + rows * (CELL + LABEL_H + PAD) + PAD

    card = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(card)

    f_title = _font(34, bold=True)
    f_sub = _font(21)
    f_num = _font(30, bold=True)
    f_state = _font(20)
    f_name = _font(18)

    stages = {k: v.get("approved") for k, v in (m.get("stages") or {}).items()}
    done = [k for k, v in stages.items() if v]
    settled = set(done)
    all_settled = bool(m.get("approved"))
    pending = next((k for k, v in stages.items() if not v), None)
    if pending:
        settled.add("_deciding")          # the open stage IS what we are showing

    d.text((PAD, 20), f"{shoot.name} — {len(names)} frames", INK, font=f_title)
    sub = f"approved: {', '.join(done) if done else 'nothing yet'}"
    if pending:
        sub += f"   ·   now deciding: {pending.upper()}"
    sub += f"   ·   look: {m.get('preset') or m.get('pick') or 'not picked'}"
    d.text((PAD, 60), sub, MUTED, font=f_sub)

    for i, name in enumerate(names, start=1):
        rec = photos[name]
        col, row = (i - 1) % COLS, (i - 1) // COLS
        x = PAD + col * (CELL + PAD)
        y = head_h + row * (CELL + LABEL_H + PAD)

        im, origin = _shipping_bytes(shoot, name, rec, all_settled)
        im = im.convert("RGB")
        d.rectangle([x, y, x + CELL, y + CELL], fill=(255, 255, 255))

        im.thumbnail((CELL, CELL))
        card.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))

        # The number rides ON the picture — a caption number gets read as
        # belonging to the neighbouring cell as often as to its own.
        d.rectangle([x, y, x + 54, y + 46], fill=INK)
        d.text((x + 16, y + 8), str(i), (255, 255, 255), font=f_num)

        state, colour = _state_line(rec, settled)
        d.text((x + 2, y + CELL + 8), state, colour, font=f_state)
        tail = name if len(name) <= 22 else name[:10] + "…" + name[-10:]
        line = f"{tail}  ({origin})"
        while d.textlength(line, font=f_name) > CELL - 4 and len(origin) > 6:
            origin = origin[:-1]
            line = f"{tail}  ({origin}…)"
        d.text((x + 2, y + CELL + 36), line, MUTED, font=f_name)

    out = shoot / ".prep" / "card.jpg"
    card.save(out, quality=84, optimize=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shoot")
    a = ap.parse_args()
    p = build(Path(a.shoot))
    print(f"{p}  ({p.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
