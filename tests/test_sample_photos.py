#!/usr/bin/env python3
"""Regression tests for tests/fixtures/photos — the tracked sample shoot (#97).

`/inventory/` is gitignored and `*.jpg` is excluded repo-wide, so a cloud agent
or a fresh clone has no photos to run on. These ten frames are the carve-out.
Two things have to stay true or they stop being useful:

  1. They stay SMALL and SAFE to have in a public repo — under 150 KB each, no
     GPS, no MakerNote. A well-meaning "let's use the full-res originals" is
     the failure mode this locks out.
  2. They keep producing the SPREAD they were chosen for — eight frames that
     crop, two that hit a center_crop guard. A set where everything passes
     proves nothing about the guards.

Companion to test_center_crop.py, which draws synthetic frames with cv2. That
one pins the guard *logic*; this one pins the guards against real photographs,
which is what actually broke in misc-08-14-a.

Run:  python tests/test_sample_photos.py
  or: pytest tests/test_sample_photos.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402
from lib.photo_prep import center_crop as CC                # noqa: E402

PHOTOS = ROOT / "tests" / "fixtures" / "photos"

MAX_BYTES = 150 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_EDGE = 1200

# (subject_frac, offset, border_fg) as measured, in percent, and whether the
# frame is safe to crop. Recorded from the fixtures themselves — see README.md.
# Tolerance is generous enough for a cv2 point release and tight enough that a
# real change in the detector shows up.
TOL = 3.0
EXPECTED = {
    "ring-01-full-plain-ground.jpg":     (9.6,  2.1,  0.0, True),
    "ring-02-macro-bezel.jpg":           (3.7,  3.7,  0.0, True),
    "ring-03-macro-bezel-tight.jpg":     (9.8,  7.2,  0.0, True),
    "ring-04-on-finger-small.jpg":       (17.0, 7.5, 29.8, False),
    "ring-05-on-finger.jpg":             (46.1, 6.9, 21.8, False),
    "ring-06-macro-shank-offcentre.jpg": (13.5, 16.7, 0.0, True),
    "ring-07-macro-shank-near-dup.jpg":  (13.9, 13.8, 0.0, True),
    "ring-08-profile-cool-cast.jpg":     (7.0, 10.3,  0.0, True),
    "ring-09-full-cool-cast.jpg":        (4.5,  7.2,  0.0, True),
    "ring-10-exif-rotated.jpg":          (9.6,  2.1,  0.0, True),
}

# EXIF Orientation each frame must carry. ring-10 is the derived sideways copy.
EXPECTED_ORIENTATION = {name: (6 if name == "ring-10-exif-rotated.jpg" else 1)
                        for name in EXPECTED}

GPS_IFD = 0x8825
MAKER_NOTE = 0x927C


def _files():
    return sorted(PHOTOS.glob("*.jpg"))


def _focal(path):
    fp = CC.focal_point(path)
    why = CC.crop_warning(fp, CC._crop_box(fp, 1.0, 0.12))
    return fp, why


def _ahash(path, n=16):
    im = Image.open(path).convert("L").resize((n, n), Image.LANCZOS)
    a = np.asarray(im, dtype=float)
    return a > a.mean()


# --- 1. the set is present, small, and safe to publish -----------------------

def test_the_set_is_complete():
    """Every documented frame is on disk, and nothing undocumented crept in."""
    assert PHOTOS.is_dir(), f"missing fixture directory: {PHOTOS}"
    assert (PHOTOS / "README.md").is_file(), "the set must document itself"
    assert {p.name for p in _files()} == set(EXPECTED), (
        "fixture set changed — update EXPECTED and README.md in the same commit"
    )


def test_frames_stay_small():
    """Under 150 KB each and ~1.3 MB for the set. This is a public repo."""
    total = 0
    for p in _files():
        n = p.stat().st_size
        total += n
        assert n <= MAX_BYTES, f"{p.name} is {n / 1024:.0f} KB (cap {MAX_BYTES / 1024:.0f} KB)"
        w, h = Image.open(p).size
        assert max(w, h) <= MAX_EDGE, f"{p.name} is {w}x{h} (cap {MAX_EDGE} px long edge)"
    assert total <= MAX_TOTAL, f"set is {total / 1024:.0f} KB (cap {MAX_TOTAL / 1024:.0f} KB)"


def test_no_location_or_camera_serial_metadata():
    """No GPS and no MakerNote — the two tags that leak more than they look.

    The Nikon MakerNote carries the shutter count and body-specific fields; GPS
    would carry where we shoot. Neither belongs in a public repo, and neither is
    read by anything in the pipeline.
    """
    for p in _files():
        ex = Image.open(p).getexif()
        assert GPS_IFD not in ex, f"{p.name} carries a GPS IFD"
        assert MAKER_NOTE not in ex, f"{p.name} carries a MakerNote"


# --- 2. they keep producing the spread they were chosen for ------------------

def test_crop_verdicts_hold():
    """Eight frames crop, two hit a guard — with the numbers behind each."""
    for p in _files():
        subj, off, border, croppable = EXPECTED[p.name]
        fp, why = _focal(p)
        assert (why is None) == croppable, (
            f"{p.name}: expected {'crop' if croppable else 'SKIP'}, got "
            f"{'crop' if why is None else 'SKIP: ' + why}"
        )
        for label, got, want in (("subject_frac", fp["subject_frac"] * 100, subj),
                                 ("offset", fp["offset"] * 100, off),
                                 ("border_fg", fp["border_fg"] * 100, border)):
            assert abs(got - want) <= TOL, (
                f"{p.name}: {label} moved {want:.1f} -> {got:.1f}"
            )


def test_both_guard_frames_are_kept_whole():
    """A skipped frame must come back as the ORIGINAL, not a mangled crop."""
    for name in ("ring-04-on-finger-small.jpg", "ring-05-on-finger.jpg"):
        p = PHOTOS / name
        src = Image.open(p)
        out = CC.center_crop(p)                    # force=False: guards apply
        assert out.size == src.size, (
            f"{name}: guard fired but the frame was cropped anyway "
            f"({src.size} -> {out.size})"
        )


def test_rotated_frame_is_the_same_scene():
    """ring-10 is ring-01 stored sideways: same subject, same numbers.

    This is what makes it useful for the orientation path — a bug there shows up
    as a difference from ring-01, not as someone's judgement about a photo.
    """
    upright = Image.open(PHOTOS / "ring-01-full-plain-ground.jpg")
    rotated = Image.open(PHOTOS / "ring-10-exif-rotated.jpg")
    assert rotated.size == (upright.size[1], upright.size[0]), (
        "ring-10 should be ring-01's frame with the axes swapped"
    )
    a, _ = _focal(PHOTOS / "ring-01-full-plain-ground.jpg")
    b, _ = _focal(PHOTOS / "ring-10-exif-rotated.jpg")
    assert abs(a["offset"] - b["offset"]) * 100 <= TOL
    assert abs(a["subject_frac"] - b["subject_frac"]) * 100 <= TOL


def test_exif_orientation_tags():
    """Nine frames upright, one carrying a real Orientation 6."""
    for p in _files():
        got = Image.open(p).getexif().get(274)
        assert got == EXPECTED_ORIENTATION[p.name], (
            f"{p.name}: EXIF Orientation {got}, expected "
            f"{EXPECTED_ORIENTATION[p.name]}"
        )


def test_near_duplicate_pair_is_the_closest_pair():
    """ring-06/ring-07 must stay the tightest pair, or near-dup work has no case."""
    files = _files()
    hashes = {p.name: _ahash(p) for p in files}
    dists = {}
    for i, a in enumerate(files):
        for b in files[i + 1:]:
            dists[(a.name, b.name)] = int((hashes[a.name] != hashes[b.name]).sum())

    # ring-01 vs ring-10 is the same frame rotated; not a "pair" in this sense.
    dists.pop(("ring-01-full-plain-ground.jpg", "ring-10-exif-rotated.jpg"), None)
    pair = ("ring-06-macro-shank-offcentre.jpg", "ring-07-macro-shank-near-dup.jpg")
    closest = min(dists, key=dists.get)
    assert closest == pair, (
        f"closest pair is now {closest} at {dists[closest]}/256, "
        f"not {pair} at {dists[pair]}/256"
    )


def test_the_cool_cast_frames_are_actually_cool():
    """ring-08/ring-09 carry a real blue cast; ring-01 is the neutral reference."""
    def cast(name):
        r, _, b = Image.open(PHOTOS / name).convert("RGB").resize((64, 64)).split()
        return np.asarray(b).mean() - np.asarray(r).mean()

    neutral = cast("ring-01-full-plain-ground.jpg")
    assert abs(neutral) < 5, f"reference frame is no longer neutral (B-R {neutral:+.1f})"
    for name in ("ring-08-profile-cool-cast.jpg", "ring-09-full-cool-cast.jpg"):
        assert cast(name) - neutral > 10, f"{name} has lost its cast"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:                          # noqa: PERF203
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
