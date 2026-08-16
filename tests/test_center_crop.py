#!/usr/bin/env python3
"""Regression tests for lib/photo_prep/center_crop.py — the pre-DRAFT crop guards.

These lock down the misc-08-14-a failure modes, where 20/38 frames shipped
destroyed: a high-contrast printed logo hijacked the detector and cropped the
actual tie bar out of frame, and macros (subject filling the frame) collapsed to
bare background or clipped the legend off a hang tag. The tool must now refuse
those crops and pass the original through instead.

Synthetic images (drawn with cv2) rather than the real JPGs, matching the house
style in test_marble_crop.py: deterministic, fast, no fixtures to carry.

Run:  python tests/test_center_crop.py
  or: pytest tests/test_center_crop.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                        # noqa: E402
import cv2                                                # noqa: E402
from lib.photo_prep import center_crop as CC              # noqa: E402

W, H = 1200, 900


def _felt(w=W, h=H, level=28):
    """Uniform dark studio ground with a little sensor noise."""
    rng = np.random.default_rng(0)
    return (rng.integers(level - 6, level + 6, (h, w, 3))).astype("uint8")


def _write(bgr):
    """Save to a temp JPG (the API takes paths) and return it."""
    d = Path(tempfile.mkdtemp(prefix="center_crop_test_"))
    p = d / "frame.jpg"
    cv2.imwrite(str(p), bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return p


def _plan(path, aspect=1.0, pad=0.12):
    fp = CC.focal_point(path)
    return fp, CC.crop_warning(fp, CC._crop_box(fp, aspect, pad))


# --- failure mode 1: high-contrast printed logo beside the actual item -------

def _logo_beside_item():
    """A small metal bar bottom-left; a big bright box with dark print top-right.

    This is the anson-clips shape: the printed logo is the highest-contrast thing
    in the frame, so a crop centered on the detected region drops the bar.
    """
    img = _felt()
    cv2.rectangle(img, (700, 90), (1150, 330), (238, 240, 242), -1)      # cream box
    cv2.putText(img, "ANSON", (735, 250), cv2.FONT_HERSHEY_TRIPLEX, 3.2,
                (40, 40, 190), 12, cv2.LINE_AA)                          # red print
    cv2.rectangle(img, (90, 610), (330, 680), (150, 150, 155), -1)       # the tie bar
    return img


def test_logo_beside_item_is_not_cropped():
    p = _write(_logo_beside_item())
    fp, why = _plan(p)
    assert why is not None, "a crop that drops the item must be refused"
    # and the refusal must actually pass the original through
    out = np.asarray(CC.center_crop(p))
    assert out.shape[:2] == (H, W), f"skipped frame must ship the original, got {out.shape[:2]}"


def test_logo_beside_item_would_have_cut_the_item():
    """Guard the *reason*: the naive crop really does discard the item."""
    p = _write(_logo_beside_item())
    fp = CC.focal_point(p)
    box = CC._crop_box(fp, 1.0, 0.12)
    assert CC.subject_kept(fp, box) < 0.95, (
        "fixture no longer reproduces the bug — the naive crop keeps the whole subject")


# --- failure mode 2: subject fills the frame (macro) ------------------------

def test_subject_filling_frame_is_not_cropped():
    img = _felt()
    cv2.rectangle(img, (20, 15), (W - 20, H - 15), (170, 165, 160), -1)   # >90% of frame
    p = _write(img)
    fp, why = _plan(p)
    assert why is not None, "a macro with no background to trim must be refused"
    assert "fills" in why or "background" in why, f"unexpected reason: {why}"
    out = np.asarray(CC.center_crop(p))
    assert out.shape[:2] == (H, W), "macro must ship the original"


def test_uniform_blurred_frame_is_not_cropped():
    """Out-of-focus macro: nothing in the frame reads as a separable subject."""
    p = _write(cv2.GaussianBlur(_felt(level=120), (0, 0), 40))
    fp, why = _plan(p)
    assert why is not None, "a frame with no detectable subject must be refused"
    out = np.asarray(CC.center_crop(p))
    assert out.shape[:2] == (H, W), "blurred frame must ship the original"


def test_speck_reads_as_no_subject():
    """A lone speck on an empty field is detector noise, not an item."""
    img = _felt()
    cv2.circle(img, (700, 400), 18, (210, 210, 215), -1)
    p = _write(img)
    fp, why = _plan(p)
    assert fp["subject_frac"] < CC.MIN_SUBJECT_FRAC
    assert why is not None and "no subject" in why, f"unexpected reason: {why}"


# --- failure mode 3: detector locks onto a fragment of the subject ----------

def test_partial_detection_is_not_cropped():
    """The tara-clasp shape: an item laid across the frame + one bright piece.

    A subject spanning the frame edge-to-edge is dropped by _pick_blob as a
    "background field", leaving a chance highlight as the apparent subject — so
    a crop centered on it cuts the rest of the item away. The `capture` metric
    (how much foreground the picked box actually holds) is what catches this.
    """
    img = _felt()
    cv2.rectangle(img, (0, 250), (W, 360), (110, 110, 115), -1)        # chain, edge to edge
    cv2.rectangle(img, (740, 600), (990, 850), (245, 245, 250), -1)    # bright clasp
    p = _write(img)
    fp, why = _plan(p)
    assert fp["capture"] < CC.MIN_CAPTURE, "fixture should hide most of the foreground"
    assert why is not None and "unreliable" in why, f"unexpected reason: {why}"
    out = np.asarray(CC.center_crop(p))
    assert out.shape[:2] == (H, W), "partial detection must ship the original"


# --- the tool must still do its job on a good frame -------------------------

def test_offcenter_subject_still_gets_cropped():
    """Positive control: guards must not turn the tool into a no-op."""
    img = _felt()
    cv2.rectangle(img, (140, 170), (440, 430), (150, 150, 155), -1)   # ~7% of frame, up-left
    p = _write(img)
    fp, why = _plan(p)
    assert why is None, f"a clean off-center subject must still crop, got: {why}"
    assert fp["offset"] > 0.06, "fixture should read as off-center"

    out = np.asarray(CC.center_crop(p))
    oh, ow = out.shape[:2]
    assert (oh, ow) != (H, W), "off-center subject should have been re-cropped"
    assert abs(ow / oh - 1.0) < 0.02, f"default aspect is 1:1, got {ow}x{oh}"
    # the whole subject survives, and it now sits centered
    box = CC._crop_box(fp, 1.0, 0.12)
    assert CC.subject_kept(fp, box) > 0.99, "the crop must keep the entire subject"
    cx = fp["cx"] - box[0]
    assert abs(cx / ow - 0.5) < 0.05, "subject should end up near the crop's center"


# --- geometry unit ----------------------------------------------------------

def test_subject_kept_measures_overlap():
    fp = {"bbox": (100, 100, 200, 200)}
    assert CC.subject_kept(fp, (100, 100, 300, 300)) == 1.0        # fully inside
    assert CC.subject_kept(fp, (200, 100, 300, 300)) == 0.5        # right half only
    assert CC.subject_kept(fp, (0, 0, 50, 50)) == 0.0              # disjoint


def test_force_bypasses_the_guards():
    p = _write(_logo_beside_item())
    forced = np.asarray(CC.center_crop(p, force=True))
    assert forced.shape[:2] != (H, W), "--force must crop even when unsafe"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:                                  # noqa: BLE001
                fails += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if fails else 0)
