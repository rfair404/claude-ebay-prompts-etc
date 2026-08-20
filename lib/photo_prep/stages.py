"""Staged, interactive review — orientation, then crop, then colour.

PREP used to decide all three at once and present a single before|after sheet.
That is the wrong shape for a decision the operator actually has to make: the
three corrections depend on each other in one direction only, and a sheet that
mixes them cannot be answered. A crop is only meaningful once the frame is the
right way up. A colour judgement is only meaningful on the framing that will
ship. Shown together, a bad crop and a bad rotation look the same on the page.

So the review runs in order and stops at each step:

    orientation  ->  unskew  ->  crop  ->  colour

Each stage renders ONE sheet with a row per frame and a thumbnail for every
option at that stage, so the choice is a pick rather than a description. Each
stage has its own approval, recorded separately. A stage cannot be opened until
the one before it is approved, and `listing/` is not written until all three
are — the final gate still applies on top.

Nothing here decides anything on its own. The sheets show what PREP proposes;
the operator confirms or overrides, and the manifest records which.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from . import color as colormod
from . import orientation as orientmod
from . import unskew as skewmod

STAGES = ("orientation", "unskew", "crop", "color")
STAGE_LABEL = {
    "orientation": "1 · ORIENTATION — which way is up",
    "unskew": "2 · UNSKEW — square up a rectangular item",
    "crop": "3 · CROP — framing on the squared frame",
    "color": "4 · COLOUR — backdrop and item, on the final framing",
}

CELL = 250
OK = (170, 230, 175)
PICK = (90, 220, 120)
WARN = (120, 200, 255)
DIM = (165, 165, 165)


# ---------------------------------------------------------------------------
# stage state
# ---------------------------------------------------------------------------

def stage_state(m: dict) -> dict:
    st = m.setdefault("stages", {})
    for s in STAGES:
        st.setdefault(s, {"approved": False, "approved_at": None})
    return st


def stage_blocker(m: dict, stage: str) -> Optional[str]:
    """Why this stage cannot be opened yet, or None."""
    if stage not in STAGES:
        return f"unknown stage {stage!r}; use one of {', '.join(STAGES)}"
    st = stage_state(m)
    for earlier in STAGES[:STAGES.index(stage)]:
        if not st[earlier]["approved"]:
            return (f"stage '{earlier}' is not approved yet — a {stage} decision "
                    f"made on an un-approved {earlier} is a decision about a frame "
                    f"that is going to change")
    return None


def unresolved_for(m: dict, stage: str) -> list:
    """Frames that still need an answer before this stage can be approved."""
    out = []
    for name, rec in (m.get("photos") or {}).items():
        if stage == "orientation" and rec["orientation"]["needs_ask"]:
            out.append(f"{name}: orientation unresolved")
        elif stage == "color" and not (rec.get("presets") or {}):
            out.append(f"{name}: not rendered")
    return out


# ---------------------------------------------------------------------------
# tiles
# ---------------------------------------------------------------------------

def _fit(img: np.ndarray, cell: int = CELL, bg: int = 26) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    s = cell / max(h, w)
    r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                   interpolation=cv2.INTER_AREA)
    canvas = np.full((cell, cell, 3), bg, np.uint8)
    yo, xo = (cell - r.shape[0]) // 2, (cell - r.shape[1]) // 2
    canvas[yo:yo + r.shape[0], xo:xo + r.shape[1]] = r
    return canvas


def _tile(img: np.ndarray, caption: str, chosen: bool, cell: int = CELL) -> np.ndarray:
    """One option. The chosen one is captioned in green and ruled, so the
    current decision is visible at a glance across a long sheet."""
    import cv2
    t = _fit(img, cell)
    if chosen:
        cv2.rectangle(t, (0, 0), (cell - 1, cell - 1), PICK, 3)
    bar = np.full((22, cell, 3), 16, np.uint8)
    cv2.putText(bar, caption[:34], (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                PICK if chosen else DIM, 1, cv2.LINE_AA)
    return np.vstack([bar, t])


def _row_label(width: int, text: str, colour=OK) -> np.ndarray:
    import cv2
    bar = np.full((26, width, 3), 12, np.uint8)
    cv2.putText(bar, text[:150], (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.46,
                colour, 1, cv2.LINE_AA)
    return bar


def _banner(width: int, text: str, sub: str = "") -> np.ndarray:
    import cv2
    h = 54 if sub else 38
    bar = np.full((h, width, 3), 8, np.uint8)
    cv2.putText(bar, text, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.72,
                (255, 225, 130), 2, cv2.LINE_AA)
    if sub:
        cv2.putText(bar, sub, (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.44,
                    DIM, 1, cv2.LINE_AA)
    return bar


def _stack(rows: list) -> np.ndarray:
    width = max(r.shape[1] for r in rows)
    out = []
    for r in rows:
        if r.shape[1] < width:
            pad = np.full((r.shape[0], width - r.shape[1], 3), 12, np.uint8)
            r = np.hstack([r, pad])
        out.append(r)
    return np.vstack(out)


def _write(sheet: np.ndarray, path: Path) -> Path:
    import cv2
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 87])
    if ok:
        buf.tofile(str(path))
    return path


# ---------------------------------------------------------------------------
# stage 1 — orientation
# ---------------------------------------------------------------------------

def orientation_sheet(shoot: Path, m: dict, out: Path) -> Path:
    """Row per frame, all four rotations, the applied one ruled in green.

    Every option is shown rather than just the proposal, because the answer is
    "which of these", and asking that against a single thumbnail is how a frame
    gets confirmed sideways.
    """
    from .prep import _load_bgr, _thumb

    rows = [_banner(CELL * 4 + 30, STAGE_LABEL["orientation"],
                    "green = what will ship.  Wrong? --rotate NAME=DEG (relative to the green one)")]
    for name, rec in (m.get("photos") or {}).items():
        src = shoot / name
        if not src.exists():
            continue
        o = rec["orientation"]
        cam = orientmod.rotate_bgr(_load_bgr(src), o.get("exif_angle", 0))
        cam = _thumb(cam, CELL * 2)
        applied = o.get("subject_angle", 0) % 360

        tiles = [_tile(orientmod.rotate_bgr(cam, d), f"+{d}", d == applied)
                 for d in (0, 90, 180, 270)]
        note = f"{name}   exif {o.get('exif_angle', 0)}deg + subject {applied}deg  ({o.get('source', '?')})"
        if o.get("needs_ask"):
            prop = o.get("osd_proposal")
            note += "   [NEEDS AN ANSWER" + (f"; OSD guessed {prop}" if prop else "") + "]"
        rows.append(_row_label(CELL * 4 + 30, note, WARN if o.get("needs_ask") else OK))
        rows.append(np.hstack(tiles))
    return _write(_stack(rows), out)


# ---------------------------------------------------------------------------
# stage 2 — unskew
# ---------------------------------------------------------------------------

def unskew_sheet(shoot: Path, m: dict, out: Path) -> Path:
    """Row per frame: the upright frame with the item's own quad drawn on it,
    and the squared result beside it.

    The quad is what makes this answerable. A deskew of two degrees is invisible
    as a before/after pair at thumbnail size, but a green outline that does not
    sit on the item's edges is obvious — the operator is checking the MEASUREMENT,
    not eyeballing the correction.
    """
    import cv2
    from .prep import _load_bgr, _thumb

    rows = [_banner(CELL * 3 + 20, STAGE_LABEL["unskew"],
                    "left = the edges PREP measured, right = squared.  "
                    "Override with --unskew NAME=off|on")]
    for name, rec in (m.get("photos") or {}).items():
        src = shoot / name
        if not src.exists():
            continue
        up = orientmod.rotate_bgr(_load_bgr(src), rec["orientation"]["applied"])
        sk = skewmod.from_dict(rec.get("unskew"))

        drawn = up.copy()
        if sk.quad:
            q = np.array(sk.quad, np.int32).reshape(-1, 1, 2)
            cv2.polylines(drawn, [q], True, PICK if sk.applied else WARN,
                          max(3, int(0.004 * max(up.shape[:2]))), cv2.LINE_AA)
        tiles = [_tile(_thumb(drawn, CELL * 2), "edges measured", False)]

        if sk.applied:
            tiles.append(_tile(_thumb(skewmod.apply(up, sk), CELL * 2), "squared", True))
        else:
            blank = np.full((CELL, CELL, 3), 22, np.uint8)
            cv2.putText(blank, "AS SHOT", (18, CELL // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, WARN, 2, cv2.LINE_AA)
            tiles.append(_tile(blank, "left alone", True))

        bits = f"tilt {sk.tilt_deg:.2f}deg  keystone {sk.keystone:.3f}  fill {sk.fill:.2f}"
        rows.append(_row_label(CELL * 3 + 20,
                               f"{name}   {'SQUARED' if sk.applied else 'as shot'}   "
                               f"{bits}   — {sk.reason}",
                               OK if sk.applied else WARN))
        rows.append(np.hstack(tiles))
    return _write(_stack(rows), out)


# ---------------------------------------------------------------------------
# stage 3 — crop
# ---------------------------------------------------------------------------

def crop_sheet(shoot: Path, m: dict, out: Path) -> Path:
    """Row per frame: the upright frame with the proposed box drawn on it, and
    the result beside it. A skipped crop shows the reason instead of a result,
    so 'nothing happened' is never silent."""
    import cv2
    from .prep import _thumb, prepared

    rows = [_banner(CELL * 3 + 20, STAGE_LABEL["crop"],
                    "left = box on the upright frame, right = result.  "
                    "Override with --crop NAME=off|on|pad0.20")]
    for name, rec in (m.get("photos") or {}).items():
        src = shoot / name
        if not src.exists():
            continue
        up = prepared(shoot, name, rec)
        crop = rec.get("crop") or {}
        boxed = up.copy()
        if crop.get("applied") and crop.get("box"):
            x0, y0, x1, y1 = crop["box"]
            cv2.rectangle(boxed, (x0, y0), (x1, y1), (90, 220, 120),
                          max(3, int(0.004 * max(up.shape[:2]))))
            result = up[y0:y1, x0:x1]
        else:
            result = None

        tiles = [_tile(_thumb(boxed, CELL * 2), "frame + box", False)]
        if result is not None:
            tiles.append(_tile(_thumb(result, CELL * 2), "cropped", True))
        else:
            blank = np.full((CELL, CELL, 3), 22, np.uint8)
            cv2.putText(blank, "NO CROP", (18, CELL // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, WARN, 2, cv2.LINE_AA)
            tiles.append(_tile(blank, "kept as shot", True))

        why = "" if crop.get("applied") else f"  — {crop.get('reason', 'no reason recorded')}"
        rows.append(_row_label(CELL * 3 + 20,
                               f"{name}   crop {'YES' if crop.get('applied') else 'no'}{why}",
                               OK if crop.get("applied") else WARN))
        rows.append(np.hstack(tiles))
    return _write(_stack(rows), out)


# ---------------------------------------------------------------------------
# stage 4 — colour
# ---------------------------------------------------------------------------

def color_sheet(shoot: Path, m: dict, out: Path) -> Path:
    """Row per frame: the framing that will ship, then every rendered look.
    The adopted one is ruled green."""
    from .prep import _load_bgr, _thumb, prepared

    presets = list(colormod.PRESETS)
    chosen = m.get("chosen_preset")
    width = CELL * (len(presets) + 1) + 20
    rows = [_banner(width, STAGE_LABEL["color"],
                    f"green = adopted ({chosen or 'none yet'}).  Change for the whole shoot with --pick NAME")]
    for name, rec in (m.get("photos") or {}).items():
        src = shoot / name
        if not src.exists():
            continue
        up = prepared(shoot, name, rec)
        crop = rec.get("crop") or {}
        if crop.get("applied") and crop.get("box"):
            x0, y0, x1, y1 = crop["box"]
            up = up[y0:y1, x0:x1]
        tiles = [_tile(_thumb(up, CELL * 2), "as shot", False)]
        for p in presets:
            entry = (rec.get("presets") or {}).get(p) or {}
            path = shoot / entry.get("path", "")
            if entry.get("path") and path.exists():
                tiles.append(_tile(_thumb(_load_bgr(path), CELL * 2), p, p == chosen))
            else:
                blank = np.full((CELL, CELL, 3), 22, np.uint8)
                tiles.append(_tile(blank, f"{p} (not rendered)", False))

        c = (rec.get("presets") or {}).get(chosen or "", {}).get("report") or rec.get("color") or {}
        bits = []
        if c:
            bits.append(f"bg {c.get('bg_luma_before', 0):.0f}->{c.get('bg_luma_after', 0):.0f}")
            bits.append(f"strength {c.get('strength', '?')}")
            rails = (c.get("subject_newly_clipped", 0) or 0) + (c.get("subject_newly_crushed", 0) or 0)
            if rails:
                bits.append(f"RAILS +{rails}")
        rows.append(_row_label(width, f"{name}   " + "   ".join(bits), OK))
        rows.append(np.hstack(tiles))
    return _write(_stack(rows), out)


SHEET_BUILDERS = {"orientation": orientation_sheet, "unskew": unskew_sheet,
                  "crop": crop_sheet, "color": color_sheet}
