"""UNSKEW — square up an item that is rectangular in life but not in the frame.

A framed picture, a magazine, a book, a box, a record sleeve, a certificate: the
object is a rectangle, and the buyer knows it. When the camera was a few degrees
off square the photo shows a parallelogram, and every one of those degrees reads
to a buyer as a cheap listing before they have read a word of the description.
Nothing else in PREP fixes it — the orientation stage only turns in multiples of
90°, and the crop stage frames whatever shape it is given.

So this stage measures the item's own edges and warps the frame so they meet the
picture's edges. Two failure modes, and the guards are built around them:

  * **The item is not a rectangle.** A marble, a jug, a pile of flatware — there
    is no "square" to restore, and any quad fitted to it is noise. Gated on how
    completely the item's outline fills the smallest rotated rectangle that
    contains it (`fill`): a book measures ~0.97, an ellipse cannot exceed 0.79.
  * **The angle is the point of the photo.** A raking-light shot across a
    surface, a three-quarter view showing depth — both are deliberate, both are
    steeply skewed, and flattening either destroys the shot. Gated on magnitude:
    past ~18° of tilt or a quarter of keystone this stops reading as a mistake.

Both refusals are reported per frame on the stage sheet, never silent.

The warp is applied to the WHOLE frame, not to a cut-out of the item, so the
backdrop travels with it and the crop stage still has something to frame. The
canvas grows to hold every source pixel — deskewing never crops — and the
wedges of new canvas outside the original frame are filled by replicating the
edge. That fill only ever lands on backdrop beyond where the photograph reached;
it cannot touch the item, whose outline is strictly inside the source frame.

What this stage may NOT do: it is a rigid geometric correction, and it must stay
one. No warping the item to a "nicer" proportion, no stretching a slightly
trapezoidal frame into a square one. The destination rectangle's proportions are
measured from the item's own opposite edges, so a 12x9 painting stays 4:3.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

# How completely the item's outline must fill the smallest rotated rectangle
# that contains it before we will call it rectangular. An ellipse is 0.785 at
# any angle; a book, a frame or a magazine measures 0.95-0.99. 0.88 sits in the
# gap with room on both sides.
MIN_FILL = 0.88

# Corners must be corners. Mean absolute deviation from 90°, in degrees.
MAX_CORNER_DEV = 12.0

# Below this the frame is already square and the warp is not worth a resample.
MIN_TILT_DEG = 0.35
MIN_KEYSTONE = 0.010

# Above this the angle is deliberate, not a mistake.
MAX_TILT_DEG = 18.0
MAX_KEYSTONE = 0.25

# A correction that needs half again the canvas is not a correction.
MAX_CANVAS_GROWTH = 1.45


@dataclass
class Skew:
    """One frame's unskew decision, and the measurements behind it."""
    applied: bool
    reason: str
    quad: Optional[list] = None        # item corners TL,TR,BR,BL in upright px
    dst: Optional[list] = None         # where they go
    out_size: Optional[list] = None    # [W, H] of the warped canvas
    tilt_deg: float = 0.0              # in-plane rotation of the item's long axis
    keystone: float = 0.0              # 0..1 disagreement between opposite edges
    fill: float = 0.0                  # outline area / min-area-rect area
    corner_dev: float = 0.0            # mean |corner - 90| in degrees
    operator: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def from_dict(d: Optional[dict]) -> Skew:
    """Rebuild a Skew from a manifest record, however partial it is.

    `applied` and `reason` have no defaults on the dataclass, so a record
    carrying only one of them raised TypeError. Manifests predate several of
    these fields and get edited by hand, so a partial record is a normal input
    rather than a bug — and raising on one takes down whatever was reading it.
    """
    d = dict(d or {})
    d.pop("_operator_pad", None)
    known = {f for f in Skew.__dataclass_fields__}
    kw = {k: v for k, v in d.items() if k in known}
    kw.setdefault("applied", False)
    kw.setdefault("reason", "not planned")
    return Skew(**kw)


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Corners as TL, TR, BR, BL — the order every other function assumes."""
    pts = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float64)


def _quad_from_mask(mask: np.ndarray) -> Optional[tuple]:
    """The item's four corners, how rectangular it is, and where they came from.

    Two separate questions, and they need two different measurements:

      * **Is this a rectangle at all?** Answered against the min-area rotated
        rect, which always circumscribes the outline, so the ratio is bounded by
        1 and reads the same for every shape: a book or a frame measures 0.97+,
        an ellipse cannot exceed 0.79 whatever its angle.
      * **Where exactly are its corners?** Answered by approxPolyDP, which lands
        on the true corners when it lands at all — including the keystone that a
        rotated rect cannot see.

    The approximation is only trusted when it circumscribes as well as the rect
    does. On a round item approxPolyDP returns a quad INSCRIBED in the outline,
    which would otherwise be mistaken for a tight fit; comparing its area to the
    rect's is what catches that.
    """
    import cv2

    m = (mask > 0).astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    return _quad_from_contour(max(cnts, key=cv2.contourArea))


def _quad_from_contour(c) -> Optional[tuple]:
    """Corners, rectangularity and provenance for one outline."""
    import cv2

    area = float(cv2.contourArea(c))
    if area <= 0:
        return None

    rect = cv2.minAreaRect(c)
    rect_area = float(rect[1][0] * rect[1][1])
    if rect_area <= 0:
        return None
    fill = area / rect_area

    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    quad, source = None, "minarearect"
    for eps in (0.010, 0.015, 0.020, 0.030, 0.045, 0.060):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) != 4:
            continue
        cand = _order_quad(ap.reshape(4, 2).astype(np.float64))
        cand_area = float(cv2.contourArea(cand.astype(np.float32)))
        if cand_area >= 0.85 * rect_area:
            quad, source = cand, "approx"
        break
    if quad is None:
        quad = _order_quad(cv2.boxPoints(rect).astype(np.float64))

    return quad, fill, source


def _quad_from_edges(bgr: np.ndarray) -> list:
    """Outline candidates read off the pixels rather than the subject mask.

    The mask cannot be the only source here, and this shoot is why: a dark wood
    frame on dark cloth segmented to 54% of its own rectangle, which reads as
    "not a rectangle" when the item is nothing but rectangle. Segmentation is
    tuned to find *an item* against *a ground*; a straight edge between two
    similar tones is a different question and a cheaper one.

    So two more readings are taken from the image itself — an Otsu split, which
    is decisive whenever the item and the cloth differ in brightness at all, and
    a Canny pass, which finds the frame's own edge when they barely do. Both run
    on a downscaled copy: an edge that survives an 8x reduction is a real edge,
    and the whole pass costs milliseconds against segmentation's tens of seconds.
    """
    import cv2

    H, W = bgr.shape[:2]
    scale = 1000.0 / max(H, W)
    small = cv2.resize(bgr, None, fx=min(1.0, scale), fy=min(1.0, scale),
                       interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    inv = 1.0 / min(1.0, scale)

    # Several splits, not one. Otsu answers "item or ground" and on a framed
    # picture it answers it about the CANVAS — the bright rectangle inside a
    # dark moulding — while the rectangle we want to square is the frame's own
    # outer edge. Low quantile cuts find that edge whenever the backdrop is
    # darker than the item, which is what a cloth sweep is for. Cheap enough to
    # run all of them and let the pick decide.
    masks = []
    _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(("otsu", otsu))
    masks.append(("otsu-inv", 255 - otsu))
    for q in (12, 22, 35, 50):
        t = float(np.percentile(g, q))
        masks.append((f"q{q}", (g > t).astype(np.uint8) * 255))

    med = float(np.median(g))
    lo, hi = int(max(0, 0.66 * med)), int(min(255, 1.33 * med))
    edges = cv2.Canny(g, lo, hi)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    masks.append(("edges", cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k, iterations=2)))

    out = []
    frame_area = float(small.shape[0] * small.shape[1])
    for src, mm in masks:
        mm = cv2.morphologyEx((mm > 0).astype(np.uint8), cv2.MORPH_OPEN,
                              np.ones((7, 7), np.uint8))
        cnts, _ = cv2.findContours(mm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = float(cv2.contourArea(c))
            if not (0.10 * frame_area <= a <= 0.94 * frame_area):
                continue
            got = _quad_from_contour(c)
            if got is None:
                continue
            quad, fill, how = got
            out.append((quad * inv, fill, f"{src}/{how}"))
    return out


def _corner_deviation(q: np.ndarray) -> float:
    """Mean |interior angle - 90|, in degrees. A rectangle scores 0."""
    devs = []
    for i in range(4):
        a, b, c = q[(i - 1) % 4], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            return 90.0
        cosang = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        devs.append(abs(np.degrees(np.arccos(cosang)) - 90.0))
    return float(np.mean(devs))


def _edges(q: np.ndarray) -> tuple:
    tl, tr, br, bl = q
    return (np.linalg.norm(tr - tl), np.linalg.norm(br - bl),   # top, bottom
            np.linalg.norm(bl - tl), np.linalg.norm(br - tr))   # left, right


def _tilt_degrees(q: np.ndarray) -> float:
    """How far the item's horizontals sit off the picture's horizontal.

    Averaged over top and bottom edge, and reported signed-free: the number the
    operator cares about is "how crooked", and the direction is visible on the
    sheet.
    """
    tl, tr, br, bl = q
    angs = []
    for p, r in ((tl, tr), (bl, br)):
        v = r - p
        angs.append(np.degrees(np.arctan2(v[1], v[0])))
    a = float(np.mean(angs))
    # Fold into (-45, 45]: a portrait item's "horizontal" edge may be the short one.
    while a > 45.0:
        a -= 90.0
    while a <= -45.0:
        a += 90.0
    return abs(a)


def _keystone(q: np.ndarray) -> float:
    """Disagreement between opposite edge lengths, 0 when the quad is a
    parallelogram. This is the part a rotation cannot fix."""
    top, bot, left, right = _edges(q)
    kh = abs(top - bot) / max(top, bot, 1e-6)
    kv = abs(left - right) / max(left, right, 1e-6)
    return float(max(kh, kv))


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def _visible_corners(q: np.ndarray, shape, margin: float = 0.012) -> bool:
    """Reject an outline whose corners are the picture's own corners.

    A macro of a painted surface or a page fills the frame edge to edge: the
    only quad to be found is the photograph itself, which is square by
    definition and would warp to a no-op — or worse, to noise. If we cannot see
    at least one corner of the item, there is nothing here to square.
    """
    H, W = shape[:2]
    mx, my = margin * W, margin * H
    inside = sum(1 for x, y in q if mx < x < W - mx and my < y < H - my)
    return inside >= 1


def _inside_frame(q: np.ndarray, shape, slack: float = 0.02) -> bool:
    """Drop a quad that leaves the picture. An outline can only be the item's if
    the item was photographed; corners well outside the frame mean the reading
    chained together two unrelated edges."""
    H, W = shape[:2]
    return bool(np.all(q[:, 0] >= -slack * W) and np.all(q[:, 0] <= (1 + slack) * W)
                and np.all(q[:, 1] >= -slack * H) and np.all(q[:, 1] <= (1 + slack) * H))


def plan(bgr: np.ndarray, sm, rectangular: Optional[bool] = None) -> Skew:
    """Decide this frame's unskew, or the reason there isn't one.

    `rectangular=True` is the operator saying "this IS a rectangle" and skips
    only the shape test — every magnitude guard still applies, because "square
    it up" is not consent to warp a deliberately angled shot.
    """
    import cv2

    cands = []
    got = _quad_from_mask(sm.mask)
    if got is not None:
        cands.append((got[0], got[1], f"mask/{got[2]}"))
    cands += _quad_from_edges(bgr)
    found = bool(cands)
    cands = [c for c in cands
             if _visible_corners(c[0], bgr.shape) and _inside_frame(c[0], bgr.shape)]
    if not cands:
        return Skew(applied=False,
                    reason=("the item fills the frame — no corner of it is visible to "
                            "square against" if found else "no item outline to measure"))

    # Most rectangular wins, biggest breaks the tie.
    #
    # Getting this ordering right took a measurement. On a framed picture the
    # obvious split (Otsu) lands on the CANVAS — the bright rectangle inside the
    # moulding — and squaring the canvas leaves the frame's own outer edge
    # visibly crooked, which is worse than leaving it alone. Both rectangles are
    # in the candidate list; the outer one wins because a wooden frame against
    # cloth is the cleaner rectangle of the two (0.98 against 0.90 on this
    # shoot), not because we guessed which one mattered.
    def _score(c):
        q = c[0]
        w = max(q[:, 0]) - min(q[:, 0])
        h = max(q[:, 1]) - min(q[:, 1])
        return (round(min(c[1], 1.0), 3), float(w * h))

    quad, fill, source = max(cands, key=_score)

    dev = _corner_deviation(quad)
    tilt = _tilt_degrees(quad)
    key = _keystone(quad)
    base = dict(quad=quad.round(1).tolist(), tilt_deg=round(tilt, 2),
                keystone=round(key, 4), fill=round(fill, 3),
                corner_dev=round(dev, 2))

    if rectangular is not True:
        if fill < MIN_FILL:
            return Skew(applied=False, **base,
                        reason=f"not a rectangle (outline fills {fill:.2f} of the "
                               f"rectangle around it, needs {MIN_FILL}) — nothing to square up")
        if dev > MAX_CORNER_DEV:
            return Skew(applied=False, **base,
                        reason=f"corners are {dev:.0f}deg off square on average — "
                               f"the shape is not a rectangle seen at an angle")

    if tilt < MIN_TILT_DEG and key < MIN_KEYSTONE:
        return Skew(applied=False, **base,
                    reason=f"already square (tilt {tilt:.2f}deg, keystone {key:.3f})")
    if tilt > MAX_TILT_DEG or key > MAX_KEYSTONE:
        return Skew(applied=False, **base,
                    reason=f"shot at an angle on purpose (tilt {tilt:.1f}deg, "
                           f"keystone {key:.2f}) — squaring it would destroy the shot")

    # Destination proportions come from the item's own opposite edges, so the
    # correction restores the rectangle rather than inventing a nicer one.
    top, bot, left, right = _edges(quad)
    w = float(np.mean([top, bot]))
    h = float(np.mean([left, right]))
    if w < 8 or h < 8:
        return Skew(applied=False, **base, reason="item outline too small to square up")

    tl = quad[0]
    dst = np.array([tl, tl + (w, 0), tl + (w, h), tl + (0, h)], dtype=np.float64)
    H = cv2.getPerspectiveTransform(quad.astype(np.float32), dst.astype(np.float32))

    # Grow the canvas to hold every source pixel: a deskew must not silently
    # crop, and the crop stage runs after this one anyway.
    Hh, Ww = bgr.shape[:2]
    corners = np.array([[0, 0], [Ww, 0], [Ww, Hh], [0, Hh]], np.float64).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    x0, y0 = warped.min(0)
    x1, y1 = warped.max(0)
    out_w, out_h = int(np.ceil(x1 - x0)), int(np.ceil(y1 - y0))
    growth = (out_w * out_h) / float(Ww * Hh)
    if growth > MAX_CANVAS_GROWTH:
        return Skew(applied=False, **base,
                    reason=f"correction would grow the canvas {growth:.2f}x — too far "
                           f"out of square to fix by warping")

    shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
    dst_shifted = (dst - (x0, y0))
    return Skew(applied=True, reason=f"squared up ({source}: tilt {tilt:.2f}deg, "
                                     f"keystone {key:.3f})",
                dst=dst_shifted.round(1).tolist(), out_size=[out_w, out_h], **base)


def matrix(sk: Skew) -> Optional[np.ndarray]:
    """The homography that carries `sk.quad` onto `sk.dst`, canvas shift included."""
    import cv2
    if not (sk.applied and sk.quad and sk.dst):
        return None
    return cv2.getPerspectiveTransform(np.float32(sk.quad), np.float32(sk.dst))


def apply(bgr: np.ndarray, sk: Skew, flags: Optional[int] = None) -> np.ndarray:
    """Warp a frame by a planned unskew. Returns the input untouched if none."""
    import cv2
    H = matrix(sk)
    if H is None:
        return bgr
    w, h = sk.out_size
    return cv2.warpPerspective(
        bgr, H, (w, h),
        flags=cv2.INTER_CUBIC if flags is None else flags,
        borderMode=cv2.BORDER_REPLICATE)


def apply_mask(mask: np.ndarray, sk: Skew) -> np.ndarray:
    """Carry a subject mask through the same warp.

    Segmentation is the expensive step by a wide margin, so the mask is warped
    rather than recomputed — the same trade the orientation stage already makes
    for its 90° turns. Nearest-neighbour and a constant border keep it binary
    and keep the replicated edge from smearing the item outward.
    """
    import cv2
    H = matrix(sk)
    if H is None:
        return mask
    w, h = sk.out_size
    return cv2.warpPerspective(mask, H, (w, h), flags=cv2.INTER_NEAREST,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
