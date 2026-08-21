#!/usr/bin/env python3
"""Unit tests for lib/marble_crop.py geometry — the crop refiner + detector.

Uses SYNTHETIC images (a bright colored disk on a dark ground, drawn with cv2)
so detection is deterministic and needs no CLIP. The is_marble() CLIP gate is
exercised separately by test_marble_gate.py, so here we run detect_and_crop with
gate=False to keep these fast and model-free.

Run:  python tests/test_marble_crop.py
  or: pytest tests/test_marble_crop.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import numpy as np                     # noqa: E402
import cv2                             # noqa: E402
import marble_crop as MC              # noqa: E402


def _disk_bgr(w=400, h=400, cx=200, cy=200, r=80, color=(200, 60, 40)):
    """Dark ground + one bright, saturated, lightly-textured disk (BGR)."""
    rng = np.random.default_rng(0)
    img = (rng.integers(0, 12, (h, w, 3))).astype("uint8")     # near-black noise
    cv2.circle(img, (cx, cy), r, color, -1)
    # add faint interior texture so _interior_ok() sees a "marble", not a flat label
    noise = rng.integers(0, 40, (h, w, 3)).astype("uint8")
    mask = np.zeros((h, w), "uint8")
    cv2.circle(mask, (cx, cy), r, 255, -1)
    img[mask > 0] = cv2.add(img, noise)[mask > 0]
    return img


def test_refine_circle_tightens_oversized_detection():
    bgr = _disk_bgr(r=80)
    x, y, r = MC._refine_circle(bgr, 200, 200, 92)   # detection ~15% too big
    assert 0.45 * 92 <= r <= 1.15 * 92, "refined radius must stay inside the sane band"
    assert abs(r - 80) < 20, f"should shrink toward the true radius 80, got {r:.1f}"
    assert abs(x - 200) < 15 and abs(y - 200) < 15, "centre should stay on the disk"


def test_refine_circle_ignores_wild_refit_returns_original():
    bgr = _disk_bgr(r=80)
    # passing r=200 (way bigger than the true 80): a refit to ~80 is < 0.45*200,
    # so it's out of band and the ORIGINAL must be returned untouched.
    got = MC._refine_circle(bgr, 200, 200, 200)
    assert got == (200, 200, 200), f"out-of-band refit must return the original, got {got}"


def test_refine_circle_empty_window_returns_original():
    bgr = _disk_bgr()
    got = MC._refine_circle(bgr, 1000, 1000, 10)      # window off-image -> size 0
    assert got == (1000, 1000, 10)


def test_detect_and_crop_finds_the_disk_ungated():
    bgr = _disk_bgr(r=80)
    d = Path(tempfile.mkdtemp(prefix="crop_test_"))
    p = d / "disk.jpg"
    cv2.imwrite(str(p), bgr)
    pairs = MC.detect_and_crop(str(p), gate=False, refine=False)
    assert len(pairs) >= 1, "the disk should be detected"
    circle, im = pairs[0]
    assert len(circle) == 3, "each result carries an (x,y,r) circle"
    assert im.size[0] > 0 and im.size[1] > 0, "and a non-empty PIL crop"


def test_detect_and_crop_refine_path_does_not_crash():
    bgr = _disk_bgr(r=80)
    d = Path(tempfile.mkdtemp(prefix="crop_test2_"))
    p = d / "disk.jpg"
    cv2.imwrite(str(p), bgr)
    pairs = MC.detect_and_crop(str(p), gate=False, refine=True)     # refine=True default path
    assert len(pairs) >= 1


def test_detect_and_crop_blank_image_finds_nothing():
    blank = np.zeros((400, 400, 3), "uint8")           # flat black, no marble
    d = Path(tempfile.mkdtemp(prefix="crop_test3_"))
    p = d / "blank.jpg"
    cv2.imwrite(str(p), blank)
    pairs = MC.detect_and_crop(str(p), gate=False, refine=True)
    assert pairs == [], "a blank frame must yield no false-positive crops"


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
