"""Orientation resolution for PREP — the step that is not allowed to guess.

There are TWO independent rotations in a listing photo, and conflating them is
what made the old handling wrong:

  * **Camera rotation** — how the phone or body was held. The EXIF Orientation
    tag reports it, and `exif_transpose` bakes it. Objective, and solved.
  * **Subject rotation** — how the item was laid down in the frame. A magazine
    on a bedspread can be square to the world and still 90° to the camera.
    **No metadata knows this.** The first real shoot this module ran on had
    EXIF=3 on all four frames, baked correctly, and all four items still lying
    sideways.

`applied` is the sum of the two. The camera half comes from EXIF; the subject
half comes from, in order:

  1. `osd`    — Tesseract page-orientation, run on the CROPPED SUBJECT (not the
                whole frame; a magazine cover in a 4096px bedspread shot reads
                as "too few characters"). Objective, and it covers a large share
                of this inventory: catalogs, magazines, boxes, cartons, labels.
                Below `OSD_MIN_CONF` it counts as no answer.
  2. `vision` — someone looked and said so, either through PREP's own
                `--rotate` or through the sibling `orient.py` tool's
                `orientation.json`. A recorded 0 is a real answer — "confirmed
                upright" — not the absence of one.
  3. `ask`    — nobody has answered. The frame keeps its camera rotation only,
                is marked unresolved, and CANNOT pass the gate.

The rule that makes this trustworthy is the last one. Nothing here infers
"probably upright" from an aspect ratio and ships it. Round items with no
defined upright are resolved the same way as everything else — by someone
looking once and confirming 0.

Angle convention throughout: degrees CLOCKWISE to apply to the stored pixels to
make them upright. 0 / 90 / 180 / 270 only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

# Tesseract's own orientation confidence. MEASURED, not chosen: see
# tools/osd_audit.py, which scores every OSD reading in inventory/ against the
# human look recorded beside it (73 labelled frames).
#
# At the old floor of 1.5, OSD agreed with the recorded look 49% of the time --
# a coin toss, on the one signal this module calls objective. The failure is not
# spread evenly, which is what makes it fixable: agreement runs 21% below 2.0 and
# 12% in the 2.0-3.0 band, then 80% above 4.0 and 86% above 6.0. The old floor
# admitted the 2-3 band, which is the single worst bucket in the corpus and
# performs worse than chance.
#
#   floor   answers kept   agreement   wrong answers taken
#    1.5         100%          49%            37
#    3.0          58%          74%            11
#    4.0          42%          84%             5
#    6.0          29%          86%             3
#
# 4.0 is the knee. It gives up 58% of the readings to remove 32 of 37 wrong
# ones, and giving them up is cheap: a frame with no OSD answer is not guessed
# at, it becomes an ASK and goes to the operator on the rotation sheet, which
# the stage is built around anyway. A wrong answer is the expensive outcome --
# it is applied silently and ships a sideways photo in a headless run.
#
# This is the question issue #21 said to answer before refactoring. The answer
# is not a broken angle convention: that was tested at all four rotations and is
# correct, and EXIF is not double-applied either. OSD was simply being believed
# far below the confidence at which it is worth believing.
OSD_MIN_CONF = 4.0
# …and it must also have recognised a writing system. A textless macro of a cast
# iron key came back "180 degrees, confidence 1.51, script confidence 0.1" and
# was silently flipped backwards relative to every other frame in its shoot.
# Orientation confidence alone says "these marks point that way"; script
# confidence is what says "these are marks of a language I know". Without the
# second, tesseract is pattern-matching rust.
OSD_MIN_SCRIPT = 0.3
# Long sides tried in order, first confident answer wins. Body copy in a
# magazine ad is small enough that 2400 reads it as noise and 3600 reads it as
# 270° at confidence 3.5 — measured on the esquire-gentleman shoot. Escalation
# costs a second OSD pass only on frames the first pass failed to resolve.
OSD_LADDER = (2400, 3600)

# EXIF Orientation tag -> rotation CW. The mirrored values (2,4,5,7) are rare
# from real cameras; we record them but only ever apply the rotation, because
# un-mirroring a photo that was never mirrored is a new way to be wrong.
EXIF_ROT = {1: 0, 2: 0, 3: 180, 4: 180, 5: 90, 6: 90, 7: 270, 8: 270}


@dataclass
class OrientVerdict:
    """Everything known about one frame's orientation, and what was done."""
    name: str
    exif_tag: Optional[int] = None
    exif_angle: int = 0          # camera half — from the EXIF tag
    osd_angle: Optional[int] = None
    osd_conf: float = 0.0
    osd_note: str = ""
    vision_angle: Optional[int] = None
    subject_angle: int = 0       # subject half — from OSD or a look
    applied: int = 0             # (exif + subject) % 360
    source: str = "none"         # exif | exif+osd | exif+vision | unresolved
    needs_ask: bool = False
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def rotate_bgr(arr: np.ndarray, deg_cw: int) -> np.ndarray:
    """Lossless 90-step rotation. Works on colour frames and on masks."""
    import cv2
    deg_cw %= 360
    if deg_cw == 0:
        return arr
    if deg_cw == 90:
        return cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
    if deg_cw == 180:
        return cv2.rotate(arr, cv2.ROTATE_180)
    if deg_cw == 270:
        return cv2.rotate(arr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"rotation must be a 90 step, got {deg_cw}")


def exif_orientation(path: Path) -> Optional[int]:
    """The raw Orientation tag (274), or None when absent/unreadable."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            tag = im.getexif().get(274)
        return int(tag) if tag else None
    except Exception:
        return None


def recorded_looks(shoot: Path) -> dict:
    """Human rotation calls from the sibling `orient.py` tool's manifest.

    That tool exists to let a person say "frame 3 is on its side" and have it
    stick. Its answers are exactly the `vision` input this resolver wants, so
    PREP reads them rather than asking the same question twice — a rotation
    already recorded there does not come back as an ASK here.
    """
    p = Path(shoot) / "orientation.json"
    if not p.exists():
        return {}
    try:
        import json
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): int(v) % 360 for k, v in raw.items()}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Tesseract OSD
# ---------------------------------------------------------------------------

_TESSERACT_HINTS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _ensure_tesseract() -> bool:
    """Point pytesseract at the binary, PATH or not.

    The Windows installer does not add itself to PATH, so a working install
    otherwise reports as "not installed" and every frame silently loses its one
    objective content signal. Check the known install locations before believing
    it is missing.
    """
    try:
        import pytesseract
    except Exception:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        pass
    for hint in _TESSERACT_HINTS:
        if Path(hint).exists():
            pytesseract.pytesseract.tesseract_cmd = hint
            try:
                pytesseract.get_tesseract_version()
                return True
            except Exception:
                continue
    return False


def osd_available() -> bool:
    return _ensure_tesseract()


def osd_angle(bgr: np.ndarray) -> tuple[Optional[int], float, str]:
    """Page-orientation from printed text, escalating scale until it answers.

    Returns the first confident reading from `OSD_LADDER`, else the last
    (unconfident) note so the review sheet can show what tesseract actually
    thought rather than a bare "no answer".
    """
    last = (None, 0.0, "not attempted")
    for long_side in OSD_LADDER:
        last = _osd_once(bgr, long_side)
        if last[0] is not None:
            return last
    return last


def _osd_once(bgr: np.ndarray, long_side: int) -> tuple[Optional[int], float, str]:
    """One OSD pass at a given scale. Feed it the CROPPED SUBJECT.

    Sizing is not a detail here. A magazine cover inside a 4096px bedspread
    shot, downscaled whole to fit OSD, puts the cover type at a few pixels a
    glyph and tesseract answers "Too few characters" — which reads as "this item
    has no text" when in fact the text was thrown away before it was asked.
    Cropping to the subject first and scaling that UP to ~`OSD_LONG` is the
    difference between an objective answer and a shrug: on the first real shoot
    it turned one frame from unresolved into a confident 270°.

    Returns (angle_cw, confidence, note). Angle is None whenever tesseract is
    absent, finds too little text, or answers below `OSD_MIN_CONF` — all three
    are "no answer", never "probably upright". The note is carried onto the
    review sheet so a low-confidence reading is visible rather than silent.
    """
    if not _ensure_tesseract():
        return None, 0.0, "tesseract not installed"
    try:
        import cv2
        import pytesseract
        from pytesseract import Output
    except Exception:
        return None, 0.0, "pytesseract not installed"

    try:
        H, W = bgr.shape[:2]
        scale = long_side / max(H, W)
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=interp)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        # --dpi silences tesseract's "Invalid resolution 0 dpi" guess, which
        # otherwise lands in the note and reads like a failure.
        osd = pytesseract.image_to_osd(gray, output_type=Output.DICT,
                                       config="--psm 0 --dpi 300")
        rotate = int(osd.get("rotate", 0)) % 360
        conf = float(osd.get("orientation_conf", 0.0))
        script_conf = float(osd.get("script_conf", 0.0))

        if rotate not in (0, 90, 180, 270):
            return None, conf, f"non-90 OSD answer ({rotate}) @{long_side}px"
        if conf < OSD_MIN_CONF:
            return None, conf, f"OSD below confidence ({conf:.1f} < {OSD_MIN_CONF}) @{long_side}px"
        if script_conf < OSD_MIN_SCRIPT:
            return None, conf, (f"OSD read {rotate}° at conf {conf:.1f} but did not "
                                f"recognise a script ({script_conf:.1f}) — not text")
        return rotate, conf, f"OSD conf {conf:.1f} @{long_side}px, script conf {script_conf:.1f}"
    except Exception as e:
        # "Too few characters" is the normal answer for a marble on felt, not an
        # error — it just means this frame has no text to read.
        msg = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
        return None, 0.0, f"no OSD ({msg[:70]})"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def resolve(name: str,
            exif_tag: Optional[int],
            osd: tuple[Optional[int], float, str],
            vision: Optional[int] = None) -> OrientVerdict:
    """Compose the camera half and the subject half into one applied angle.

    `osd` is measured on the subject AFTER the EXIF bake, so it reports the
    subject half directly. `vision` is a recorded look, also expressed as the
    subject half, and it outranks OSD — a person who looked at the frame beats
    an inference from glyph shapes, including when they disagree.
    """
    v = OrientVerdict(name=name, exif_tag=exif_tag)
    osd_a, osd_c, osd_n = osd
    v.osd_angle, v.osd_conf, v.osd_note = osd_a, osd_c, osd_n
    v.vision_angle = vision

    # Re-apply the confidence bar here as well as in the reader. `osd_angle`
    # already filters, but this function is the one thing standing between a
    # number and a rotation applied to a listing photo, and it should not depend
    # on every caller having filtered correctly first.
    if osd_a is not None and osd_c < OSD_MIN_CONF:
        osd_a = None
        v.notes.append(f"OSD reading of {v.osd_angle}° ignored (confidence {osd_c:.1f})")

    if exif_tag is not None:
        v.exif_angle = EXIF_ROT.get(exif_tag, 0)
        if exif_tag in (2, 4, 5, 7):
            v.notes.append(f"EXIF {exif_tag} claims a mirror; rotation applied, mirror ignored")

    if vision is not None:
        v.subject_angle = vision % 360
        v.source = "exif+vision" if v.exif_angle else "vision"
        if osd_a is not None and osd_a != v.subject_angle:
            v.notes.append(f"OSD read {osd_a}°; the recorded look says {v.subject_angle}° — look wins")
    elif osd_a is not None:
        v.subject_angle = osd_a
        v.source = "exif+osd" if v.exif_angle else "osd"
    else:
        # No objective read of the subject and nobody has looked. The camera
        # half is still applied (it is independently true), but the frame is
        # unresolved: "landscape photos are usually upright" is exactly the
        # reasoning that shipped 66 sideways listings, and it buys nothing.
        v.subject_angle = 0
        v.source = "exif" if v.exif_angle else "unresolved"
        v.needs_ask = True
        v.notes.append("subject orientation unread (no text to read) — needs a look")

    v.applied = (v.exif_angle + v.subject_angle) % 360
    return v


# ---------------------------------------------------------------------------
# Panels for the human / model call
# ---------------------------------------------------------------------------

def four_way_panel(bgr: np.ndarray, out_path: Path, label: str, cell: int = 300) -> None:
    """Write a 0/90/180/270 panel so 'which way is up' is a pick, not a description."""
    import cv2
    tiles = []
    for deg in (0, 90, 180, 270):
        r = rotate_bgr(bgr, deg)
        h, w = r.shape[:2]
        s = cell / max(h, w)
        r = cv2.resize(r, (max(1, int(w * s)), max(1, int(h * s))))
        canvas = np.full((cell, cell, 3), 30, np.uint8)
        yo, xo = (cell - r.shape[0]) // 2, (cell - r.shape[1]) // 2
        canvas[yo:yo + r.shape[0], xo:xo + r.shape[1]] = r
        bar = np.full((24, cell, 3), 20, np.uint8)
        cv2.putText(bar, f"+{deg} deg", (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (235, 235, 235), 1, cv2.LINE_AA)
        tiles.append(np.vstack([bar, canvas]))
    row = np.hstack(tiles)
    head = np.full((26, row.shape[1], 3), 15, np.uint8)
    cv2.putText(head, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 220, 120), 1, cv2.LINE_AA)
    sheet = np.vstack([head, row])
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        buf.tofile(str(out_path))
