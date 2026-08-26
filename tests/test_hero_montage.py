#!/usr/bin/env python3
"""The hero montage — composition, and the two guesses it is allowed to make.

The gallery frame is the one image a buyer sees before deciding to click, so
the failure modes here are expensive and quiet:

  1. A montage that CROPS. Panels are contained, never cover-cropped — a crop
     at this stage silently undoes the crop the operator approved in PREP, and
     is exactly how a disclosed defect ends up outside the gallery image.
  2. An inset dropped on top of the item. The first version pinned it
     bottom-right and it landed squarely on the second earring of a pair;
     it now picks the emptiest corner.
  3. A hero that cuts through the object. The first version of that check
     measured border contrast and read a busy tabletop as a clipped subject —
     it flagged every frame of a shoot photographed on a rug. The rule under
     test is the replacement, which is about the subject box, not the backdrop.

Run:  python tests/test_hero_montage.py
  or: pytest tests/test_hero_montage.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from PIL import Image  # noqa: E402

from photo_prep import hero_montage as hm  # noqa: E402


def _frame(tmp: Path, name: str, size=(1200, 800), bg=(250, 250, 250),
           blob=None) -> Path:
    """A synthetic frame: light backdrop, optional dark rectangle as the subject."""
    im = Image.new("RGB", size, bg)
    if blob:
        x0, y0, x1, y1 = blob
        for x in range(x0, x1):
            for y in range(y0, y1):
                im.putpixel((x, y), (30, 30, 30))
    p = tmp / name
    im.save(p, quality=92)
    return p


def test_split_keeps_both_panels_uncropped(tmp_path):
    a = _frame(tmp_path, "a.jpg", (1200, 800), blob=(400, 250, 800, 550))
    b = _frame(tmp_path, "b.jpg", (800, 1200), blob=(250, 400, 550, 800))
    out = hm.compose_split([a, b])
    assert out.size == (hm.CANVAS, hm.CANVAS)
    # a portrait and a landscape frame both fit: neither is cover-cropped, so
    # each panel keeps white margin on the axis it does not fill
    assert out.getpixel((hm.CANVAS // 4, 8))[0] > 240


def test_inset_lands_in_the_emptiest_corner():
    """Subject crowded into the bottom-right must push the inset elsewhere."""
    main = Image.new("RGB", (1000, 1000), (250, 250, 250))
    for x in range(520, 1000):
        for y in range(520, 1000):
            main.putpixel((x, y), (20, 20, 20))
    assert hm.quietest_corner(main) != "br"

    main2 = Image.new("RGB", (1000, 1000), (250, 250, 250))
    for x in range(0, 460):
        for y in range(0, 460):
            main2.putpixel((x, y), (20, 20, 20))
    assert hm.quietest_corner(main2) != "tl"


def test_inset_composes_at_canvas_size(tmp_path):
    a = _frame(tmp_path, "a.jpg", (1000, 1000), blob=(100, 100, 400, 400))
    b = _frame(tmp_path, "b.jpg", (600, 600), blob=(100, 100, 500, 500))
    out = hm.compose_inset([a, b])
    assert out.size == (hm.CANVAS, hm.CANVAS)


def test_a_montage_needs_two_frames(tmp_path):
    a = _frame(tmp_path, "a.jpg")
    try:
        hm.compose([a])
    except SystemExit:
        return
    raise AssertionError("composed a montage from a single frame")


def test_clipped_hero_rule_is_about_the_subject_box():
    # object runs off two edges -> clipped, whatever the backdrop looks like
    assert hm.hero_is_clipped(0.40, 2)
    assert hm.hero_is_clipped(0.98, 0)          # box fills the frame
    # object whole, with room around it -> fine, and a busy backdrop (which the
    # old border-contrast check tripped on) is not part of this judgement
    assert not hm.hero_is_clipped(0.49, 0)
    assert not hm.hero_is_clipped(0.30, 1)      # one edge: a bleed, not a cut


def test_looks_whole_wants_room_around_the_object():
    assert hm.looks_whole(0.49, 0)
    assert not hm.looks_whole(0.49, 1)          # touches an edge
    assert not hm.looks_whole(0.02, 0)          # a speck: too far away to be the hero
    assert not hm.looks_whole(0.95, 0)          # no breathing room


def test_hero_files_are_never_offered_as_source_frames(tmp_path):
    """`00_hero.jpg` is our own output — re-running must not montage the montage."""
    listing = tmp_path / "listing"
    listing.mkdir()
    _frame(listing, "00_hero.jpg")
    _frame(listing, "a.jpg")
    _frame(listing, "b.jpg")
    names = [p.name for p in hm.list_frames(tmp_path)]
    assert names == ["a.jpg", "b.jpg"]


if __name__ == "__main__":
    import tempfile

    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            fails += 1
            print(f"FAIL {name}: {exc}")
    sys.exit(1 if fails else 0)
