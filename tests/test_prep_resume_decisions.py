#!/usr/bin/env python3
"""`--apply --resume` must re-render a frame whose DECISION changed.

The resume fingerprint was built to catch changed FILES: a replaced source, a
truncated preset, a run asking a different question. It did not catch a changed
ANSWER, and `--rotate`, `--crop` and `--detail` all rewrite the record without
touching a single byte on disk.

Measured on inventory/FREE/cats-mags/more-mags-444, frame ANMP0008.jpg: the
backdrop pass smeared the printed DESTINATION title off the cover,
`--detail ANMP0008=on` correctly wrote `color_plan.is_sweep: false`, and
`--apply --only crisp --resume` then reported "OK 5/5" and left the damaged
render in listing/ untouched. Silent, and worse than a plain stale render --
the operator had explicitly acted to fix the frame and been told it worked.

The other half is tested just as hard: a frame nobody touched must still
resume, or the fix gives back the whole point of the flag.

Run:  python tests/test_prep_resume_decisions.py
  or: pytest tests/test_prep_resume_decisions.py
"""
import copy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.photo_prep import prep as P                        # noqa: E402

# Small enough to render several times in a test run; the crop test asks for
# a bigger one because `plan_crop` refuses anything under a 1400px floor, and
# a crop that never applies cannot prove that changing it re-renders.
SMALL = (900, 1200)
CROPPABLE = (2000, 2600)


def _scene(seed=0, size=SMALL):
    """A flat backdrop with one rectangular item on it -- enough for the
    colour pass to read a sweep and have something to neutralise."""
    h, w = size
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 225, np.uint8)
    img = np.clip(img.astype(np.int16)
                  + rng.normal(0, 3, img.shape).astype(np.int16),
                  0, 255).astype(np.uint8)
    img[h // 3:2 * h // 3, w // 3:2 * w // 3] = (70, 110, 190)
    return img


def _shoot(tmp: Path, n=2, size=SMALL) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cv2.imencode(".jpg", _scene(seed=i, size=size))[1].tofile(
            str(tmp / f"IMG_{i}.jpg"))
    return tmp


def _rendered(shoot: Path, **kw):
    """run_apply, reporting which frames actually went through the renderer."""
    seen = []
    orig = P._save_bgr

    def spy(path, bgr, quality=94):
        seen.append(Path(path).stem)
        return orig(path, bgr, quality=quality)

    with patch.object(P, "_save_bgr", side_effect=spy):
        P.run_apply(shoot, quiet=True, **kw)
    return seen


def _preset_sha(shoot: Path, name: str, preset: str = "crisp") -> str:
    return P.load_manifest(shoot)["photos"][name]["presets"][preset]["sha256"]


def _prepped(td: Path, n=2, size=SMALL) -> Path:
    shoot = _shoot(td / "s", n=n, size=size)
    P.run_auto(shoot, "1:1", P.DEFAULT_PAD, "gentle", quiet=True)
    P.run_approve_auto(shoot)
    return shoot


# ---------------------------------------------------------------------------
# the reported bug, end to end
# ---------------------------------------------------------------------------

def test_flipping_detail_forces_a_rerender_of_that_frame_under_resume():
    """The more-mags-444 case: `--detail NAME=on` turns the backdrop pass off,
    so the pixels on disk stop being an answer to anything. The frame must
    re-render, and its preset sha256 must move."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td))
        P.run_apply(shoot, quiet=True, only=("crisp",))
        before = _preset_sha(shoot, "IMG_0.jpg")

        P.run_set_detail(shoot, ["IMG_0=on"])
        cp = P.load_manifest(shoot)["photos"]["IMG_0.jpg"]["color_plan"]
        assert cp["is_sweep"] is False, "sanity: --detail=on records is_sweep false"

        seen = _rendered(shoot, resume=True, only=("crisp",))

        assert "IMG_0" in seen, (
            "a frame whose backdrop decision changed must re-render under "
            "--resume -- the source and the preset file are both untouched, "
            "so nothing else in the fingerprint can catch this")
        assert _preset_sha(shoot, "IMG_0.jpg") != before, (
            "the re-render must produce different pixels -- a resumed 'OK' "
            "over the old bytes is the exact failure being guarded against")


def test_a_frame_nobody_touched_still_resumes():
    """Per-frame, not a blanket invalidation. Re-rendering the whole shoot
    because one frame's flag moved gives back all the time --resume exists to
    save, on a batch big enough to be killed by a timeout in the first place."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td))
        P.run_apply(shoot, quiet=True, only=("crisp",))
        untouched = _preset_sha(shoot, "IMG_1.jpg")

        P.run_set_detail(shoot, ["IMG_0=on"])
        seen = _rendered(shoot, resume=True, only=("crisp",))

        assert "IMG_1" not in seen, (
            "the frame whose decisions did not move must still be skipped")
        assert _preset_sha(shoot, "IMG_1.jpg") == untouched


def test_rotating_a_frame_forces_a_rerender_under_resume():
    """The same hole, reached through `--rotate`. `_invalidate_from` clears the
    stage approvals but leaves the rendered presets in place, so before this
    fingerprint a resumed apply shipped the old rotation."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td), n=1)
        P.run_apply(shoot, quiet=True, only=("crisp",))
        before = _preset_sha(shoot, "IMG_0.jpg")

        P.run_rotate(shoot, ["IMG_0=90"])
        seen = _rendered(shoot, resume=True, only=("crisp",))

        assert "IMG_0" in seen, "a recorded rotation must re-render the frame"
        assert _preset_sha(shoot, "IMG_0.jpg") != before


def test_a_crop_override_forces_a_rerender_under_resume():
    """And through `--crop NAME=off`, which changes the box the pixels are
    rendered from without touching a file either."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td), n=1, size=CROPPABLE)
        P.run_apply(shoot, quiet=True, only=("crisp",))
        before = _preset_sha(shoot, "IMG_0.jpg")
        assert P.load_manifest(shoot)["photos"]["IMG_0.jpg"]["crop"]["applied"], (
            "sanity: this scene crops, so turning the crop off is a real change")

        P.run_set_crop(shoot, ["IMG_0=off"])
        seen = _rendered(shoot, resume=True, only=("crisp",))

        assert "IMG_0" in seen, "an operator crop override must re-render"
        assert _preset_sha(shoot, "IMG_0.jpg") != before


# ---------------------------------------------------------------------------
# the fingerprint itself
# ---------------------------------------------------------------------------

def _rec() -> dict:
    return {
        "orientation": {"applied": 90, "needs_ask": False, "osd_conf": 6.2},
        "unskew": {"applied": False, "operator": False},
        "crop": {"applied": True, "box": [10, 20, 900, 910], "operator": False},
        "color_plan": {"is_sweep": True, "bg_class_effective": "light",
                       "bg_luma": 211.4},
    }


def test_render_hash_moves_for_every_decision_that_moves_pixels():
    base = P._frame_render_hash(_rec())
    assert len(base) == 16
    assert base == P._frame_render_hash(_rec()), "must be deterministic"

    for (section, field), val in [(("orientation", "applied"), 180),
                                  (("unskew", "applied"), True),
                                  (("crop", "applied"), False),
                                  (("crop", "box"), [0, 0, 100, 100]),
                                  (("color_plan", "is_sweep"), False),
                                  (("color_plan", "bg_class_effective"), "other")]:
        rec = _rec()
        rec[section][field] = val
        assert P._frame_render_hash(rec) != base, f"{section}.{field} must move the hash"


def test_render_hash_ignores_measurements_that_do_not_move_pixels():
    """A fingerprint that fires on a re-measured backdrop luma or an OSD
    confidence re-renders everything every time, and then gets switched off."""
    base = P._frame_render_hash(_rec())
    noisy = _rec()
    noisy["orientation"]["osd_conf"] = 1.1
    noisy["color_plan"]["bg_luma"] = 12.0
    noisy["crop"]["reason"] = "operator: keep as shot"
    noisy["status"] = "PICK"
    assert P._frame_render_hash(noisy) == base


def test_a_record_from_before_render_hash_existed_is_never_resumable():
    """Nothing on disk says what decisions an old render was made from, so the
    only safe reading is 'unknown' -- it re-renders once, and is stamped."""
    with tempfile.TemporaryDirectory() as td:
        shoot = Path(td) / "s"
        (shoot / ".prep" / "presets" / "crisp").mkdir(parents=True)
        (shoot / "IMG_0.jpg").write_bytes(b"src")
        render = shoot / ".prep" / "presets" / "crisp" / "IMG_0.jpg"
        render.write_bytes(b"render")
        rec = dict(_rec(),
                   src_sha256=P._sha256(shoot / "IMG_0.jpg"),
                   presets={"crisp": {"path": ".prep/presets/crisp/IMG_0.jpg",
                                      "sha256": P._sha256(render)}})

        assert not P._frame_is_resumable(shoot, "IMG_0.jpg", rec, ("crisp",)), (
            "a record with no render_hash must not be treated as resumable")

        stamped = copy.deepcopy(rec)
        stamped["render_hash"] = P._frame_render_hash(stamped)
        assert P._frame_is_resumable(shoot, "IMG_0.jpg", stamped, ("crisp",)), (
            "sanity: the same record resumes once the hash is recorded")


def test_apply_stamps_a_render_hash_on_every_frame_it_renders():
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td))
        P.run_apply(shoot, quiet=True, only=("crisp",))
        for name, rec in P.load_manifest(shoot)["photos"].items():
            assert rec.get("render_hash") == P._frame_render_hash(rec), name


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                               # noqa: BLE001
            bad += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
