#!/usr/bin/env python3
"""The sheet-protect mask for flat printed goods (lib/photo_prep/subject.py).

Locks the more-mags-444 failure: the contrast detectors find INK, not paper, so
a catalog cover masked 11% of the frame when the cover was 30% of it. Every
pixel the mask missed went to the backdrop pass, and the frames shipped with
the title blurred off the cover while one dark figure stayed sharp.

Run:  python tests/test_subject_sheet_mask.py
  or: pytest tests/test_subject_sheet_mask.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                    # noqa: E402
from lib.photo_prep import subject as S               # noqa: E402


class _SM:
    """Just the two fields sheet_mask reads."""
    def __init__(self, shape, bbox):
        self.mask = np.zeros(shape, np.uint8)
        self.bbox = bbox


def test_sheet_mask_is_the_whole_box():
    sm = _SM((600, 800), (100, 50, 300, 400))
    out = S.sheet_mask(sm)
    assert out.shape == (600, 800)
    assert (out > 0).sum() == 300 * 400, "the box must be filled solid"
    assert (out[50:450, 100:400] > 0).all(), "every pixel inside the box is protected"
    assert not (out[:50, :] > 0).any(), "nothing above the box"


def test_sheet_mask_protects_what_the_ink_mask_missed():
    """The ANMP0008 shape: ink masked, paper not.

    The point of the mask is the DIFFERENCE — the title, the logo and the pale
    margins that the detector never called subject.
    """
    sm = _SM((600, 800), (100, 50, 300, 400))
    sm.mask[200:400, 150:300] = 255          # the one dark figure the detector found
    ink = (sm.mask > 0).sum()
    sheet = (S.sheet_mask(sm) > 0).sum()
    assert sheet > 3 * ink, (
        f"sheet must cover far more than the ink it found ({sheet} vs {ink})")


def test_sheet_mask_never_reaches_outside_the_sheet():
    """The failure direction has to be 'backdrop left untidy', never 'item blurred'."""
    sm = _SM((600, 800), (100, 50, 300, 400))
    out = S.sheet_mask(sm)
    assert (out[:, :100] == 0).all() and (out[:, 400:] == 0).all(), (
        "the sweep outside the sheet must stay available to the backdrop pass")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:                              # noqa: BLE001
                fails += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if fails else 0)
