#!/usr/bin/env python3
"""Every override flag in one `prep` call must land in prep.json.

`--crop`, `--detail` and `--set-rotate` each returned from `main()` on their
own, so `--crop X=off --detail Y=on` recorded the crop, dropped the detail and
exited 0. The operator was told it worked; the frame shipped with the backdrop
pass the detail flag was there to switch off.

These drive `main()` with real argv against copies of tests/fixtures/photos
and read the decisions back from the item's .prep/prep.json.

Run:  pytest tests/test_prep_override_flags.py
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.photo_prep import prep as P                        # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "photos"
A = "ring-01-full-plain-ground"
B = "ring-02-macro-bezel"


@pytest.fixture
def shoot(tmp_path):
    """Two fixture frames in a temp item folder, through `--check`. The
    fixtures themselves are never written to."""
    d = tmp_path / "item"
    d.mkdir()
    for stem in (A, B):
        shutil.copy2(FIXTURES / f"{stem}.jpg", d / f"{stem}.jpg")
    assert P.main([str(d), "--check", "--quiet"]) == 0
    return d


def _photos(shoot: Path) -> dict:
    return json.loads((shoot / ".prep" / "prep.json").read_text())["photos"]


def test_crop_and_detail_in_one_call_both_land(shoot):
    assert P.main([str(shoot), "--crop", f"{A}=off", "--detail", f"{B}=on"]) == 0

    photos = _photos(shoot)
    crop = photos[f"{A}.jpg"]["crop"]
    assert crop["operator"] is True and crop["applied"] is False
    assert photos[f"{B}.jpg"]["color_plan"]["is_sweep"] is False
    assert photos[f"{B}.jpg"]["color_plan"]["operator"] is True


def test_set_rotate_with_crop_and_detail_all_land(shoot):
    rc = P.main([str(shoot), "--set-rotate", f"{A}=90",
                 "--crop", f"{B}=off", "--detail", f"{A}=on"])
    assert rc == 0

    photos = _photos(shoot)
    assert photos[f"{A}.jpg"]["orientation"]["subject_angle"] == 90
    assert photos[f"{B}.jpg"]["crop"]["operator"] is True
    assert photos[f"{B}.jpg"]["crop"]["applied"] is False
    assert photos[f"{A}.jpg"]["color_plan"]["is_sweep"] is False


def test_order_on_the_command_line_does_not_matter(shoot):
    assert P.main([str(shoot), "--detail", f"{A}=on", "--crop", f"{B}=off"]) == 0

    photos = _photos(shoot)
    assert photos[f"{A}.jpg"]["color_plan"]["is_sweep"] is False
    assert photos[f"{B}.jpg"]["crop"]["operator"] is True


def test_combined_overrides_drop_both_stage_signoffs(shoot):
    """Both stages the overrides touch lose their approval, not just the first."""
    m = P.load_manifest(shoot)
    for st in ("crop", "color"):
        P.stagemod.stage_state(m)[st] = {"approved": True, "approved_at": "x"}
    m["approved"] = True
    P.save_manifest(shoot, m)

    assert P.main([str(shoot), "--crop", f"{A}=off", "--detail", f"{B}=on"]) == 0

    m = P.load_manifest(shoot)
    assert P.stagemod.stage_state(m)["crop"]["approved"] is False
    assert P.stagemod.stage_state(m)["color"]["approved"] is False
    assert m["approved"] is False
