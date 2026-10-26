"""tools/photo_sheet.py — the /yn photo sheet: frames resolve from an item
folder by id, land left to right in the order given, and the sheet refuses
anything but 1-3 frames. Images are made in tmp_path."""
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import photo_sheet as PS  # noqa: E402

RED, BLUE = (220, 20, 20), (20, 20, 220)


def _img(path: Path, colour, size=(300, 200)) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


def test_frames_land_in_order_with_the_header_above(tmp_path):
    item = tmp_path / "item"
    item.mkdir()
    _img(item / "P1010123.JPG", RED)
    _img(item / "P1010131.jpg", BLUE)
    out = tmp_path / "sheet.jpg"

    PS.main(["--title", "Pfaff 463 sewing machine", "--price", "$375",
             "--dir", str(item), "--out", str(out),
             "P1010123=front", "P1010131=badge"])

    with Image.open(out) as sheet:
        assert sheet.size == (PS.PAD + 2 * (PS.CELL + PS.PAD),
                              PS.HEAD_H + PS.CELL + PS.LABEL_H + PS.PAD)
        mid_y = PS.HEAD_H + PS.CELL // 2
        left = sheet.getpixel((PS.PAD + PS.CELL // 2, mid_y))
        right = sheet.getpixel((2 * PS.PAD + PS.CELL + PS.CELL // 2, mid_y))
        assert left[0] > 180 and left[2] < 80      # frame 1 is the red one
        assert right[2] > 180 and right[0] < 80    # frame 2 is the blue one
        # the header band carries text (title and price), not just background
        header = sheet.crop((0, 0, sheet.width, PS.HEAD_H)).convert("L")
        assert header.getextrema()[0] < 120


def test_portrait_frame_is_fitted_not_cropped(tmp_path):
    tall = _img(tmp_path / "tall.png", RED, size=(200, 800))
    out = PS.build([(tall, "")], "Lamp", out=tmp_path / "s.jpg")
    with Image.open(out) as sheet:
        top = sheet.getpixel((PS.PAD + PS.CELL // 2, PS.HEAD_H + 4))
        side = sheet.getpixel((PS.PAD + 4, PS.HEAD_H + PS.CELL // 2))
    assert top[0] > 180 and top[1] < 80            # image reaches the top edge
    assert side[0] < 255 and abs(side[0] - side[2]) < 20   # side is background


def test_resolve_prefers_exact_path_then_id(tmp_path):
    exact = _img(tmp_path / "a.jpg", RED)
    assert PS.resolve(str(exact), None) == exact
    assert PS.resolve("a", tmp_path) == exact


def test_ambiguous_or_missing_frame_stops(tmp_path):
    _img(tmp_path / "a.jpg", RED)
    _img(tmp_path / "a.png", BLUE)
    with pytest.raises(SystemExit, match="matches 2 files"):
        PS.resolve("a", tmp_path)
    with pytest.raises(SystemExit, match="not found"):
        PS.resolve("zzz", tmp_path)


@pytest.mark.parametrize("n", [0, 4])
def test_sheet_takes_one_to_three_frames(tmp_path, n):
    f = _img(tmp_path / "a.jpg", RED)
    with pytest.raises(SystemExit, match="1 to 3"):
        PS.build([(f, "")] * n, "x", out=tmp_path / "s.jpg")


def test_default_output_is_a_temp_file_named_for_the_item(tmp_path, monkeypatch):
    monkeypatch.setattr(PS.tempfile, "gettempdir", lambda: str(tmp_path))
    f = _img(tmp_path / "a.jpg", RED)
    out = PS.build([(f, "")], "Gold Cross Pendant, 12K GF")
    assert out == tmp_path / "yn" / "gold-cross-pendant-12k-gf.jpg"
    assert out.is_file()
