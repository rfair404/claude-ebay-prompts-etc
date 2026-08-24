"""UNSKEW — RETIRED. Replay only, for shoots that were squared before it went.

The stage measured a rectangular item's own edges and warped the frame so they
met the picture's. It was removed from the pipeline in 2026-08: it cost a
segmentation, a quad fit and a full-frame resample on every frame, and it
damaged more photos than it saved — a quad fitted to a mat, a mount or a soft
shadow squares up the wrong rectangle, and nobody can see two degrees of tilt in
a listing thumbnail anyway. Nothing plans an unskew now.

What survives is the ability to REPRODUCE one. 180 frames across 46 shoots were
published with a warp already applied and recorded in their manifest; re-running
PREP on one of those must return the pixels that are live, not a different
picture. So `apply`/`apply_mask` still honour a recorded decision, and
`from_dict` still reads one. There is no `plan` — a shoot without a recorded
unskew will never acquire one.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np


@dataclass
class Skew:
    """One frame's recorded unskew decision, and the measurements behind it."""
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
