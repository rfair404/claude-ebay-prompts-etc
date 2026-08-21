"""Subject segmentation — the shared mask every PREP step reads.

Crop needs to know where the item is. Colour correction needs to know where the
item ISN'T (that's the backdrop it may push around). Both used to answer the
question separately, and both answered it with the same premise: the frame
border samples the background, so the subject is whatever differs from it.

That premise is what `center_crop` documents as its own failure mode — on a
macro the subject IS the background, and a printed logo out-shouts the metal
item beside it. So this module answers the question TWICE, with two methods
that fail differently, and reports how much they agree:

  * `rembg` (u2net, a real salient-object model) — the primary. It has no
    border premise to lose, so it survives macros, busy fields and dark felt.
  * the LAB corner-distance segmentation lifted from `center_crop` — the
    fallback when rembg isn't installed, and the second opinion when it is.

Agreement (mask IoU) is the number that matters downstream. Two independent
detectors landing on the same pixels is evidence; one detector landing
somewhere confidently is a guess. PREP crops on agreement and flags
disagreement for a human instead of cropping anyway.

In-process:
    mask_for(bgr) -> SubjectMask(mask, bbox, source, agreement, coverage, …)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

_SEG_LONG = 1024      # segment on a downscaled copy — u2net is 320px internally
_REMBG_SESSION = None  # module-level cache; creating a session reloads the model


@dataclass
class SubjectMask:
    """Where the item is, and how much we believe it."""
    mask: np.ndarray          # uint8 0/255, full resolution
    bbox: tuple               # (x, y, w, h) of the union of kept components
    source: str               # "rembg+lab" | "lab"
    agreement: float          # bbox containment of the two detectors (see _containment)
    mask_iou: float           # pixel IoU of the two masks — recorded, not gated on
    coverage: float           # subject pixels / frame pixels
    bbox_frac: float          # bbox area / frame area
    border_fg: float          # fraction of the frame's border ring that reads as subject
    alt_bbox: Optional[tuple] = None   # the second opinion's bbox, for the report


# ---------------------------------------------------------------------------
# rembg (primary)
# ---------------------------------------------------------------------------

def rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except Exception:
        return False


def _rembg_mask(bgr: np.ndarray) -> Optional[np.ndarray]:
    """Salient-object alpha from u2net, thresholded to a binary mask.

    Returns None (rather than raising) when rembg is missing or the model
    can't be fetched — the caller falls back to LAB and records `source`, so a
    machine without the model still runs, just with one detector instead of two.
    """
    try:
        import cv2
        from rembg import new_session, remove
    except Exception:
        return None

    global _REMBG_SESSION
    try:
        if _REMBG_SESSION is None:
            _REMBG_SESSION = new_session("u2net")

        H, W = bgr.shape[:2]
        scale = _SEG_LONG / max(H, W)
        small = (cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                 if scale < 1 else bgr)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        alpha = remove(rgb, session=_REMBG_SESSION, only_mask=True)
        if alpha.ndim == 3:
            alpha = alpha[:, :, 0]

        # Otsu rather than a fixed cut: u2net's alpha is confident and bimodal
        # on studio shots, but its absolute level drifts with subject contrast.
        _t, m = cv2.threshold(alpha, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if m.shape[:2] != (H, W):
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
        return m
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LAB corner distance (fallback + second opinion)
# ---------------------------------------------------------------------------

def _lab_mask(bgr: np.ndarray) -> np.ndarray:
    """The `center_crop` segmentation, unchanged in behaviour.

    Imported rather than reimplemented so the two modules can't drift apart —
    if that heuristic is retuned, this second opinion moves with it.
    """
    from .center_crop import _subject_mask
    return _subject_mask(bgr)


# ---------------------------------------------------------------------------
# Combination
# ---------------------------------------------------------------------------

def _iou(a: np.ndarray, b: np.ndarray) -> float:
    ab = (a > 0)
    bb = (b > 0)
    union = int((ab | bb).sum())
    return float((ab & bb).sum()) / union if union else 0.0


def _containment(a: tuple, b: tuple) -> float:
    """Overlap of two bboxes as a fraction of the SMALLER one.

    Plain IoU is the wrong question to gate a crop on. The LAB heuristic
    routinely draws a looser box than u2net — it catches the contact shadow and
    the sweep's fold — and on a real magazine shot the two masks scored IoU
    0.3-0.4 while pointing at exactly the same object. Gating on IoU refused
    every crop in the shoot.

    What actually matters is whether the two detectors found the same THING:
    if the tighter box sits inside the looser one, one is merely generous. The
    dangerous case — u2net locking onto a printed logo while LAB holds the
    item — shows up as low containment, which this catches and IoU muddles.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    smaller = max(1, min(aw * ah, bw * bh))
    return inter / float(smaller)


def _bbox_of(mask: np.ndarray) -> tuple:
    """Union bbox of the real foreground pieces (pairs/sets stay whole)."""
    from .center_crop import _pick_blob
    x, y, w, h, _cx, _cy = _pick_blob(mask)
    return (int(x), int(y), int(w), int(h))


def describe(mask: np.ndarray, source: str, agreement: float,
             mask_iou: float, alt_bbox: Optional[tuple] = None) -> SubjectMask:
    """Re-derive the geometry of an existing mask.

    Used after a 90° rotation: segmentation is rotation-invariant, so the mask
    can be turned with the image instead of paying for a second u2net pass.
    """
    H, W = mask.shape[:2]
    bbox = _bbox_of(mask)
    b = max(2, int(round(0.04 * min(H, W))))
    ring = np.zeros((H, W), bool)
    ring[:b, :] = ring[-b:, :] = True
    ring[:, :b] = ring[:, -b:] = True
    x, y, w, h = bbox
    return SubjectMask(
        mask=mask, bbox=bbox, source=source, agreement=agreement,
        mask_iou=mask_iou, coverage=float((mask > 0).mean()),
        bbox_frac=(w * h) / float(W * H),
        border_fg=float((mask[ring] > 0).mean()), alt_bbox=alt_bbox,
    )


def mask_for(bgr: np.ndarray) -> SubjectMask:
    """Segment the subject with both detectors; report agreement."""
    H, W = bgr.shape[:2]

    primary = _rembg_mask(bgr)
    secondary = _lab_mask(bgr)

    if primary is None:
        mask, source = secondary, "lab"
        bbox, alt_bbox = _bbox_of(mask), None
        agreement, mask_iou = 1.0, 1.0
    else:
        mask, source = primary, "rembg+lab"
        bbox = _bbox_of(mask)
        alt_bbox = _bbox_of(secondary)
        agreement = _containment(bbox, alt_bbox)
        mask_iou = _iou(primary, secondary)

    b = max(2, int(round(0.04 * min(H, W))))
    ring = np.zeros((H, W), bool)
    ring[:b, :] = ring[-b:, :] = True
    ring[:, :b] = ring[:, -b:] = True

    x, y, w, h = bbox
    return SubjectMask(
        mask=mask,
        bbox=bbox,
        source=source,
        agreement=agreement,
        mask_iou=mask_iou,
        coverage=float((mask > 0).mean()),
        bbox_frac=(w * h) / float(W * H),
        border_fg=float((mask[ring] > 0).mean()),
        alt_bbox=alt_bbox,
    )
