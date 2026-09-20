#!/usr/bin/env python3
"""Tests for tools/probe.py — the PIL-probe + subject-measure promotion (#74 item 4).

Synthetic images, matching the house style in test_center_crop.py: no fixtures
to carry, deterministic, fast.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                 # noqa: E402
import cv2                         # noqa: E402
from PIL import Image              # noqa: E402

import probe                       # noqa: E402

W, H = 800, 600


def _felt(level=28):
    rng = np.random.default_rng(0)
    return (rng.integers(level - 6, level + 6, (H, W, 3))).astype("uint8")


def _write_jpg(bgr, tag=""):
    d = Path(tempfile.mkdtemp(prefix="probe_test_"))
    p = d / f"frame{tag}.jpg"
    cv2.imwrite(str(p), bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return p


def _item_frame():
    img = _felt()
    cv2.rectangle(img, (250, 180), (550, 420), (150, 150, 155), -1)  # the item
    return img


def test_pil_info_reports_size_and_format():
    p = _write_jpg(_felt())
    info = probe._pil_info(p)
    assert info["format"] == "JPEG"
    assert info["width"] == W
    assert info["height"] == H
    assert info["bytes"] == p.stat().st_size


def test_pil_info_reads_exif_orientation():
    d = Path(tempfile.mkdtemp(prefix="probe_test_"))
    p = d / "exif.jpg"
    im = Image.new("RGB", (40, 30), (10, 20, 30))
    exif = im.getexif()
    exif[274] = 6  # Orientation tag: rotated 90 CW
    im.save(p, exif=exif)
    info = probe._pil_info(p)
    assert info["exif_orientation"] == 6


def test_subject_info_measures_the_drawn_rectangle():
    p = _write_jpg(_item_frame())
    subject = probe._subject_info(p)
    assert subject is not None
    b = subject["bbox"]
    # The rectangle is 300x240 in an 800x600 frame -- bbox should land close,
    # never the whole frame (that would mean detection collapsed to "everything").
    assert 0 < b["w"] < W
    assert 0 < b["h"] < H
    assert 0 < subject["coverage"] <= 1
    assert 0 < subject["bbox_frac"] <= 1


def test_probe_one_combines_pil_and_subject():
    p = _write_jpg(_item_frame())
    row = probe.probe_one(p)
    assert row["path"] == str(p)
    assert "pil" in row
    assert "subject" in row


def test_missing_file_is_reported_not_raised():
    r = subprocess.run(
        [sys.executable, "-m", "lib.cli", "probe", "/no/such/file.jpg"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_json_output_is_one_object_per_line():
    p1 = _write_jpg(_felt(), tag="1")
    p2 = _write_jpg(_item_frame(), tag="2")
    r = subprocess.run(
        [sys.executable, "-m", "lib.cli", "probe", "--json", str(p1), str(p2)],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    rows = [json.loads(ln) for ln in lines]
    assert rows[0]["path"] == str(p1)
    assert rows[1]["path"] == str(p2)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
