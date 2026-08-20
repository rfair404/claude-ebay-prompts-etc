#!/usr/bin/env python3
"""Regression tests for the PREP stage — orientation, crop geometry, colour, gate.

Every case here locks down a failure this stage was built to stop, most of them
observed on real shoots while building it:

  * an EXIF tag that is correct and an item still lying sideways in the frame
    (esquire-gentleman: all four frames);
  * an unresolved frame quietly shipping as "probably upright";
  * a crop that refuses because the item sits near a frame edge;
  * a colour pass that destroys subject detail to make the backdrop pretty;
  * a detail macro treated as if it had a studio backdrop behind it;
  * photos reaching eBay without a human ever looking at them.

Synthetic images throughout (drawn with cv2), matching the house style in
test_center_crop.py: deterministic, fast, no fixtures to carry, and no rembg
download needed — the colour tests pass an explicit mask.

Run:  python tests/test_prep.py
  or: pytest tests/test_prep.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                        # noqa: E402
import cv2                                                # noqa: E402

from lib.photo_prep import color as C                     # noqa: E402
from lib.photo_prep import orientation as O               # noqa: E402
from lib.photo_prep import prep as P                      # noqa: E402

W, H = 1200, 900
NO_OSD = (None, 0.0, "no text")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _printed_surface(seed=0):
    """A macro of a printed/patterned surface — detail at a real scale.

    Deliberately NOT per-pixel noise. The backdrop statistics are measured on a
    downscaled copy (analyze is a hot path), which averages single-pixel noise
    away; structured marks a few pixels across survive that, and are what an
    actual macro of a label, a stamp or printed card looks like.
    """
    rng = np.random.default_rng(seed)
    blocks = rng.integers(40, 240, (H // 8, W // 8, 3), dtype=np.uint8)
    return cv2.resize(blocks, (W, H), interpolation=cv2.INTER_NEAREST)


def _scene(bg=235, subject=90, box=(400, 300, 400, 300), noise=3, seed=0):
    """A flat backdrop with one rectangular item on it, plus its mask."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), bg, np.uint8)
    img = np.clip(img.astype(np.int16) +
                  rng.normal(0, noise, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
    x, y, w, h = box
    img[y:y + h, x:x + w] = subject
    mask = np.zeros((H, W), np.uint8)
    mask[y:y + h, x:x + w] = 255
    return img, mask


# ---------------------------------------------------------------------------
# orientation — the camera half and the subject half are separate questions
# ---------------------------------------------------------------------------

def test_rotate_is_lossless_and_reversible():
    img, _ = _scene()
    back = O.rotate_bgr(O.rotate_bgr(img, 90), 270)
    assert np.array_equal(img, back), "90 CW then 90 CCW must be the identity"
    assert O.rotate_bgr(img, 90).shape[:2] == (W, H), "a 90 turn swaps the axes"


def test_exif_and_subject_angles_compose():
    """EXIF=3 (180) plus a subject lying at 270 must apply 90, not 180 or 270.

    This is the esquire-gentleman case exactly: the tag was right, was baked
    correctly, and every item was still on its side.
    """
    v = O.resolve("f.jpg", exif_tag=3, osd=(270, 4.0, "ok"))
    assert v.exif_angle == 180
    assert v.subject_angle == 270
    assert v.applied == 90
    assert not v.needs_ask


def test_unresolved_subject_is_asked_never_assumed():
    """No tag, no readable text, nobody looked -> ASK, and no subject rotation."""
    v = O.resolve("f.jpg", exif_tag=None, osd=NO_OSD)
    assert v.needs_ask, "an unread frame must not pass as upright"
    assert v.subject_angle == 0, "an unread frame must not be turned on a hunch"
    assert v.source == "unresolved"


def test_exif_still_applies_while_subject_is_unresolved():
    """The camera half is independently true — a pending ASK must not lose it."""
    v = O.resolve("f.jpg", exif_tag=6, osd=NO_OSD)
    assert v.exif_angle == 90 and v.applied == 90
    assert v.needs_ask


def test_a_recorded_look_outranks_osd():
    v = O.resolve("f.jpg", exif_tag=None, osd=(90, 9.9, "confident"), vision=270)
    assert v.subject_angle == 270 and not v.needs_ask
    assert any("look wins" in n for n in v.notes), "the disagreement must be recorded"


def test_confirmed_upright_is_a_real_answer():
    """A recorded 0 means 'looked at it, it is fine' — not 'no answer yet'."""
    v = O.resolve("f.jpg", exif_tag=None, osd=NO_OSD, vision=0)
    assert not v.needs_ask and v.applied == 0


def test_low_confidence_osd_is_not_an_answer():
    v = O.resolve("f.jpg", exif_tag=None, osd=(90, 0.4, "below confidence"))
    assert v.needs_ask, "a shaky OSD reading must not rotate anything"


def test_osd_needs_a_recognised_script_not_just_confidence():
    """A textless cast-iron key came back '180 deg, conf 1.51, script conf 0.1'
    and was silently flipped backwards relative to every other frame in its
    shoot. Orientation confidence says 'these marks point that way'; script
    confidence is what says 'these are marks of a language I know'."""
    assert O.OSD_MIN_SCRIPT > 0, "the script bar must exist"
    # The filter lives in the reader, so assert on its contract: a reading
    # without a recognised script is reported as NO answer.
    assert O.OSD_MIN_CONF <= 1.51, "fixture assumes 1.51 clears the confidence bar"
    v = O.resolve("f.jpg", exif_tag=None, osd=(None, 1.51, "did not recognise a script"))
    assert v.needs_ask and v.subject_angle == 0


# ---------------------------------------------------------------------------
# crop geometry
# ---------------------------------------------------------------------------

def _fp(bbox, w=W, h=H):
    x, y, bw, bh = bbox
    return {"w": w, "h": h, "bbox": bbox, "cx": x + bw / 2, "cy": y + bh / 2,
            "offset": 0.0, "subject_frac": (bw * bh) / (w * h), "capture": 1.0,
            "box_aspect": bw / bh, "border_fg": 0.0}


def test_crop_contains_the_subject_even_against_an_edge():
    """Containment beats centring.

    The inherited crop insisted on centring exactly on the subject and shrank
    the box when that ran off an edge — which ate the item, tripped the guard,
    and refused the crop. 3 of 4 frames in the first real shoot got no crop at
    all because of it.
    """
    bbox = (60, 40, 500, 700)          # hard against the top-left corner
    x0, y0, x1, y1 = P._fit_box(_fp(bbox), aspect=1.0, pad=0.12)
    assert x0 <= bbox[0] and y0 <= bbox[1]
    assert x1 >= bbox[0] + bbox[2] and y1 >= bbox[1] + bbox[3]
    assert abs((x1 - x0) - (y1 - y0)) <= 1, "1:1 was requested"


def test_crop_stays_inside_the_frame():
    x0, y0, x1, y1 = P._fit_box(_fp((900, 600, 280, 280)), aspect=1.0, pad=0.2)
    assert 0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H


def test_detail_frame_is_not_cropped():
    """A macro has no studio behind it, so there is no crop to make.

    On the coke-tray shoot this rule is what stopped a condition macro being
    zoomed into the printed woman's face and a serial-stamp macro into the
    number — both deliberately framed shots.
    """
    textured = _printed_surface(seed=1)
    mask = np.zeros((H, W), np.uint8)
    mask[300:600, 400:800] = 255
    stats = C.analyze(textured, mask)
    assert not stats.is_sweep

    class _SM:
        bbox, source, agreement, mask_iou = (400, 300, 400, 300), "rembg+lab", 1.0, 1.0
    sm = _SM(); sm.mask = mask; sm.border_fg = 0.0
    plan = P.plan_crop(textured, sm, 1.0, 0.12, stats)
    assert not plan["applied"] and "no studio backdrop" in plan["reason"]


# ---------------------------------------------------------------------------
# colour
# ---------------------------------------------------------------------------

def test_white_backdrop_is_lifted_and_the_item_is_not():
    img, mask = _scene(bg=180, subject=90)
    out, rep = C.correct(img, mask, pop="gentle")
    assert rep["bg_luma_after"] >= 240, f"backdrop only reached {rep['bg_luma_after']}"
    assert rep["strength"] == 1.0, "a clean scene should need no back-off"
    subj_before = img[350:550, 450:750].astype(float).mean()
    subj_after = out[350:550, 450:750].astype(float).mean()
    assert abs(subj_after - subj_before) < 25, "the item must not ride the backdrop up"


def test_dark_backdrop_is_deepened():
    img, mask = _scene(bg=70, subject=200)
    _out, rep = C.correct(img, mask, pop="gentle")
    assert rep["bg_luma_after"] <= 30, f"backdrop only reached {rep['bg_luma_after']}"


def test_correction_never_creates_rails_on_the_item():
    """The whole point of the verify loop, and of the structural clamp.

    A +15% white-balance gain on a specular highlight, or unsharp's undershoot
    beside high-contrast text, will otherwise push item pixels to 0 or 255 —
    measured at 49k pixels on one real frame.
    """
    for bg, subj in ((180, 250), (70, 245), (200, 6), (60, 3)):
        img, mask = _scene(bg=bg, subject=subj, noise=6)
        _out, rep = C.correct(img, mask, pop="strong")
        assert rep["subject_newly_clipped"] == 0, f"clipped the item (bg={bg}, subj={subj})"
        assert rep["subject_newly_crushed"] == 0, f"crushed the item (bg={bg}, subj={subj})"


def test_no_backdrop_move_without_a_sweep():
    """A textured surface is the item, not a backdrop — leave its tone alone.

    Lifting aged tan paper toward white is cosmetically nicer and a
    misrepresentation of the goods.
    """
    img = _printed_surface(seed=2)
    mask = np.zeros((H, W), np.uint8)
    mask[300:600, 400:800] = 255
    _out, rep = C.correct(img, mask, pop="gentle")
    assert rep["curve"] == "none"
    assert abs(rep["bg_luma_after"] - rep["bg_luma_before"]) < 8


def test_white_balance_cannot_tint_the_item():
    """Gains are clamped, so a strongly coloured ground can't recolour the goods."""
    img, mask = _scene(bg=200)
    img[:, :, 0] = np.clip(img[:, :, 0].astype(int) - 90, 0, 255)   # heavy blue cast
    _out, rep = C.correct(img, mask, pop="off")
    assert all(0.85 - 1e-6 <= g <= 1.15 + 1e-6 for g in rep["wb_gains"]), rep["wb_gains"]


def test_pop_off_leaves_the_item_alone():
    img, mask = _scene(bg=185, subject=120)
    out, _rep = C.correct(img, mask, pop="off")
    d = abs(out[350:550, 450:750].astype(float).mean()
            - img[350:550, 450:750].astype(float).mean())
    assert d < 6, f"item moved {d:.1f} levels with the subject pass off"


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def _shoot(tmp: Path, n=2):
    tmp.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img, _ = _scene(seed=i)
        cv2.imencode(".jpg", img)[1].tofile(str(tmp / f"IMG_{i}.jpg"))
    return tmp


def test_gate_blocks_an_unprepped_shoot():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s")
        try:
            P.assert_approved(shoot)
            raise AssertionError("unprepped photos must not clear the gate")
        except P.PrepGateError as e:
            assert "not been prepped" in str(e)


def test_gate_blocks_prepped_but_unapproved():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s")
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"src_sha256": "x", "output": "listing/IMG_0.jpg",
                                     "out_sha256": "y",
                                     "orientation": {"needs_ask": False}}}
        P.save_manifest(shoot, m)
        try:
            P.assert_approved(shoot)
            raise AssertionError("rendering is not approval")
        except P.PrepGateError as e:
            assert "not approved" in str(e)


def test_approval_goes_stale_when_a_file_changes():
    """What was approved must be what is uploaded, byte for byte."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        out_dir = shoot / "listing"
        out_dir.mkdir()
        img, _ = _scene(seed=9)
        cv2.imencode(".jpg", img)[1].tofile(str(out_dir / "IMG_0.jpg"))

        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {
            "src_sha256": P._sha256(shoot / "IMG_0.jpg"),
            "output": "listing/IMG_0.jpg",
            "out_sha256": P._sha256(out_dir / "IMG_0.jpg"),
            "orientation": {"needs_ask": False}}}
        m["approved"] = True
        m["approved_at"] = P._now()
        P.save_manifest(shoot, m)
        P.assert_approved(shoot)                       # clean: passes

        other, _ = _scene(bg=40, seed=3)               # someone re-renders it
        cv2.imencode(".jpg", other)[1].tofile(str(out_dir / "IMG_0.jpg"))
        try:
            P.assert_approved(shoot)
            raise AssertionError("a changed file must void the approval")
        except P.PrepGateError as e:
            assert "stale" in str(e)


def test_cannot_approve_while_a_frame_is_unresolved():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"src_sha256": "x", "output": "listing/IMG_0.jpg",
                                     "out_sha256": "y",
                                     "orientation": {"needs_ask": True}}}
        P.save_manifest(shoot, m)
        try:
            P.run_approve(shoot)
            raise AssertionError("an ASK frame must block approval")
        except SystemExit as e:
            assert "unresolved" in str(e)


def test_recorded_rotation_is_relative_to_what_was_shown():
    """`--rotate` answers the sheet in front of you, and voids any approval."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {
            "orientation": {"exif_angle": 180, "subject_angle": 0, "needs_ask": True},
            "crop": {"applied": False}}}
        m["approved"] = True
        P.save_manifest(shoot, m)

        m = P.run_rotate(shoot, ["IMG_0.jpg=270"])
        o = m["photos"]["IMG_0.jpg"]["orientation"]
        assert o["subject_angle"] == 270 and o["applied"] == 90
        assert not o["needs_ask"]
        assert not m["approved"], "changing a rotation must void the approval"

        m = P.run_rotate(shoot, ["IMG_0.jpg=90"])      # a second correction stacks
        assert m["photos"]["IMG_0.jpg"]["orientation"]["subject_angle"] == 0


def test_shoot_level_backdrop_unification():
    """One physical backdrop, one treatment — exposure drift must not split it.

    The marble shoot metered the same navy felt from 49 to 104 depending on how
    each marble was lit, and the frames either side of the absolute cutoff got
    opposite treatment in a single listing.
    """
    photos = {f"a{i}.jpg": {"color_plan": {"bg_luma": lum, "bg_iqr": 25.0,
                                           "bg_rough": 12.0,
                                           "bg_class": cls, "is_sweep": cls != "other",
                                           "bg_class_effective": cls}}
              for i, (lum, cls) in enumerate(
                  [(49, "dark"), (55, "dark"), (69, "dark"), (72, "dark"),
                   (92, "other"), (104, "other")])}
    info = P._unify_backdrop(photos, quiet=True)
    assert info and info["class"] == "dark"
    for n in ("a4.jpg", "a5.jpg"):
        assert photos[n]["color_plan"]["bg_class_effective"] == "dark", n
        assert photos[n]["color_plan"]["is_sweep"]


def test_textured_frames_are_never_promoted_to_a_backdrop():
    """A macro must not be dragged into the shoot's backdrop treatment.

    `far` is a different surface by tone; `panel` is a contact sheet sitting at
    the shoot's own tone — only its roughness gives it away.
    """
    def cp(lum, iqr, rough, cls):
        return {"color_plan": {"bg_luma": lum, "bg_iqr": iqr, "bg_rough": rough,
                               "bg_class": cls, "is_sweep": cls != "other" and iqr <= 35,
                               "bg_class_effective": cls}}

    photos = {"hero1.jpg": cp(60, 20, 12, "dark"), "hero2.jpg": cp(66, 22, 12, "dark"),
              "hero3.jpg": cp(58, 19, 11, "dark"),
              "far.jpg": cp(150, 88, 20, "other"),      # different surface entirely
              "panel.jpg": cp(61, 130, 77, "dark")}     # contact sheet, shoot's own tone
    P._unify_backdrop(photos, quiet=True)
    for n in ("far.jpg", "panel.jpg"):
        assert not photos[n]["color_plan"]["is_sweep"], n
        assert photos[n]["color_plan"]["bg_class_effective"] != "dark" or n == "panel.jpg"


def test_a_coloured_backdrop_is_not_used_as_a_grey_card():
    """The brass hound dog, caught on a live listing.

    Balancing against navy felt pushed the whole frame warm and rendered plain
    brass as polished gold — on a listing whose own title says brass. The ±15%
    clamp bounded the size of that error without preventing it: every frame
    simply pinned to the rail. The reference itself has to be rejected.
    """
    img, mask = _scene(bg=70, subject=150)
    img[:, :, 0] = np.clip(img[:, :, 0].astype(int) + 40, 0, 255)   # navy backdrop
    img[300:600, 400:800] = (110, 150, 185)                          # warm brass item
    mask[:] = 0
    mask[300:600, 400:800] = 255

    _out, rep = C.correct(img, mask, pop="off")
    assert rep["wb_gains"] == [1.0, 1.0, 1.0], f"balanced against a coloured cloth: {rep}"
    assert "coloured" in rep["wb_note"]


def test_a_neutral_backdrop_is_still_used_for_white_balance():
    """The guard must not disable white balance on a genuine grey/white sweep."""
    img, mask = _scene(bg=200, subject=120)
    img[:, :, 0] = np.clip(img[:, :, 0].astype(int) - 12, 0, 255)   # mild warm cast
    _out, rep = C.correct(img, mask, pop="off")
    assert rep["wb_gains"] != [1.0, 1.0, 1.0], "a real cast should still be corrected"


# ---------------------------------------------------------------------------
# presets — diffuse background, sharpen foreground
# ---------------------------------------------------------------------------

def _fuzzy_scene(bg=55, cast=35, subject=(80, 120, 170), seed=0):
    """Dark cloth with lint on it, a colour cast, and a warm item."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), bg, np.uint8)
    img[:, :, 0] = np.clip(img[:, :, 0].astype(int) + cast, 0, 255)   # navy cloth
    for _ in range(400):                                              # fuzz
        y, x = int(rng.integers(0, H - 3)), int(rng.integers(0, W - 4))
        img[y:y + 2, x:x + 3] = 190
    img[300:600, 400:800] = subject
    mask = np.zeros((H, W), np.uint8)
    mask[300:600, 400:800] = 255
    return img, mask


def test_every_preset_keeps_the_item_off_the_rails():
    img, mask = _fuzzy_scene()
    for name in C.PRESETS:
        _out, rep = C.correct(img, mask, preset=name)
        assert rep["subject_newly_clipped"] == 0, name
        assert rep["subject_newly_crushed"] == 0, name


def test_every_preset_blurs_backdrop_fuzz_in_proportion_to_its_strength():
    """The ask: fuzz on the black cloth should go, without touching the item.

    The bar scales with the look, because a preset that declares itself a
    fraction of studio (`k`) is supposed to do that fraction as much — holding
    a 10% look to the full-strength bar would only assert that 10% is not 10%.

    So: a full-strength preset must take the fuzz under a third, and a reduced
    one must still visibly cut it, by at least half of what its strength claims.
    No preset may ever ADD fuzz, at any strength.
    """
    img, mask = _fuzzy_scene()
    before = float(img[:200, :200].astype(float).std())
    for name, cfg in C.PRESETS.items():
        if float(cfg.get("k", 1.0)) == 0.0:
            continue          # a zero-strength look is a deliberate passthrough
        out, _ = C.correct(img, mask, preset=name)
        after = float(out[:200, :200].astype(float).std())
        k = float(cfg.get("k", 1.0))
        bar = before / 3 if k >= 1.0 else before * (1.0 - 0.5 * k)
        assert after <= before, f"{name} ADDED fuzz: {after:.1f} vs {before:.1f}"
        assert after < bar, (f"{name} (k={k}) left the fuzz: {after:.1f} vs "
                             f"{before:.1f}, bar {bar:.1f}")


def test_half_is_studio_with_every_move_halved():
    """`half` must stay derived from studio, not drift into its own numbers."""
    studio, half = C.PRESETS["studio"], C.PRESETS["half"]
    assert half.get("k") == 0.5
    for knob in ("pop", "bg_neutralize", "bg_diffuse", "sharpen", "wb"):
        assert half[knob] == studio[knob], f"half drifted from studio on {knob}"


def test_reduced_strength_looks_stay_derived_from_studio():
    """`half` and `tenth` are studio at a multiplier, not their own numbers.

    The whole point of `k` is that one edit to studio carries to both. A knob
    copied by hand drifts, and the ladder the operator picked off stops meaning
    what it meant.
    """
    studio = C.PRESETS["studio"]
    for name, k in (("half", 0.5), ("tenth", 0.10)):
        look = C.PRESETS[name]
        assert look.get("k") == k, name
        for knob in ("pop", "bg_neutralize", "bg_diffuse", "sharpen", "wb"):
            assert look[knob] == studio[knob], f"{name} drifted from studio on {knob}"


def test_asshot_changes_nothing():
    """k=0 has to mean untouched, not merely gentle.

    It is the answer when a shoot's mask cannot be trusted — a fairy doll whose
    magenta wings fell outside it and were neutralised to grey on a live
    listing. A passthrough that still nudges the pixels would not be one.
    """
    img, mask = _scene(bg=40, subject=(30, 60, 210))       # a vivid BLUE subject
    out, rep = C.correct(img, mask, preset="asshot")
    assert np.array_equal(out, img), "asshot altered the pixels"
    assert rep["strength"] == 0.0

    # Channel order, explicitly. The passthrough short-circuit writes into an
    # RGB-space buffer that the caller flips back to BGR; handing it the BGR
    # array instead passes any "did the pixels move" check and ships the frame
    # with red and blue swapped. A grey test fixture cannot see that.
    b, g, r = out[:, :, 0].mean(), out[:, :, 1].mean(), out[:, :, 2].mean()
    assert r > b, f"red and blue look swapped: B={b:.0f} G={g:.0f} R={r:.0f}"


def test_a_weaker_preset_moves_the_backdrop_less():
    """Ordering is the whole point: half < studio <= punch on backdrop travel."""
    img, mask = _fuzzy_scene()
    start = float(img[:200, :200].astype(float).mean())
    moved = {}
    for name in ("tenth", "half", "studio", "punch"):
        out, _ = C.correct(img, mask, preset=name)
        moved[name] = abs(float(out[:200, :200].astype(float).mean()) - start)
    assert moved["tenth"] < moved["half"] < moved["studio"], moved
    assert moved["studio"] <= moved["punch"] + 1e-6, moved


def test_a_new_item_defaults_to_crisp_whatever_the_backdrop():
    """`crisp` is the only look that cannot misrepresent the goods.

    It cleans the backdrop at full strength and leaves the item's colour exactly
    as the camera recorded it. Adopted as the default for new items after an
    audit of published photos found item colour destroyed on 14 frames — a
    fairy doll's magenta wings rendered grey among them — every one a mask
    failure feeding a correction that was behaving correctly on a wrong premise.
    """
    for bg in ("dark", "light", "other", None):
        assert C.default_preset_for(bg, new_item=True) == "crisp", bg


def test_an_existing_listing_keeps_its_old_default():
    """Re-rendering a live listing into a different look changes pictures a
    buyer may already have seen. Only `--pick` may do that, one at a time."""
    assert C.default_preset_for("dark", new_item=False) == "punch"
    assert C.default_preset_for("light", new_item=False) == "studio"
    assert C.default_preset_for(None, new_item=False) in C.PRESETS
    # the warm-metal exception still applies to existing shoots
    assert C.default_preset_for("dark", warm_subject=True, new_item=False) == "crisp"


def test_studio_neutralises_the_cloth_but_not_the_item():
    """Navy reads black; brass stays brass.

    This is the safe half of the correction that turned the hound dog gold —
    the difference is that it runs through the mask.
    """
    img, mask = _fuzzy_scene(subject=(80, 120, 170))
    out, _rep = C.correct(img, mask, preset="studio")
    bg = out[:150, :150].reshape(-1, 3).astype(float).mean(axis=0)
    assert bg.max() - bg.min() < 4, f"backdrop still coloured: {bg}"

    item_in = img[350:550, 450:750].reshape(-1, 3).astype(float).mean(axis=0)
    item_out = out[350:550, 450:750].reshape(-1, 3).astype(float).mean(axis=0)
    warmth_in = float(item_in[2] - item_in[0])      # R-B in BGR order
    warmth_out = float(item_out[2] - item_out[0])
    assert abs(warmth_out - warmth_in) < 12, \
        f"the item's colour moved: R-B {warmth_in:.0f} -> {warmth_out:.0f}"


def test_no_backdrop_operations_on_a_detail_frame():
    """A macro's 'background' is the item's own surface — never blur it."""
    img = _printed_surface(seed=4)
    mask = np.zeros((H, W), np.uint8)
    mask[300:600, 400:800] = 255
    out, rep = C.correct(img, mask, preset="punch")
    assert rep["bg_diffuse"] == 0.0 and rep["bg_neutralize"] == 0.0
    assert float(out[:200, :200].astype(float).std()) > 20, "it smoothed a detail frame"


def test_a_ruler_in_the_backdrop_is_not_blurred_away():
    """Caught on the brass-dog sheet.

    Segmentation returns THE subject, so a ruler laid alongside for scale falls
    outside the mask and was treated as cloth: desaturated to grey and blurred.
    Ruler shots are a standing convention here — that is measurement evidence
    being destroyed to tidy a backdrop.
    """
    img, mask = _fuzzy_scene()
    cv2.rectangle(img, (150, 700), (1050, 790), (150, 200, 225), -1)   # wooden rule
    for x in range(170, 1040, 30):
        cv2.line(img, (x, 700), (x, 740), (40, 40, 40), 2)             # tick marks

    out, _rep = C.correct(img, mask, preset="studio")

    def stats(im, y0, y1, x0, x1):
        r = im[y0:y1, x0:x1].reshape(-1, 3).astype(float)
        return r.mean(axis=0), float(r.std())

    r_in, rd_in = stats(img, 700, 790, 150, 1050)
    r_out, rd_out = stats(out, 700, 790, 150, 1050)
    assert abs(float(r_out[0] - r_in[0])) < 12, "the ruler was recoloured"
    assert rd_out > rd_in * 0.75, f"the ruler's tick marks were blurred: {rd_in:.0f} -> {rd_out:.0f}"

    _c_in, cd_in = stats(img, 50, 200, 50, 250)
    _c_out, cd_out = stats(out, 50, 200, 50, 250)
    assert cd_out < cd_in / 3, "the actual cloth should still have been diffused"


def test_a_failed_mask_disables_every_backdrop_operation():
    """One live frame reported a subject box of 0.1% of the frame.

    With no usable mask, "backdrop" means the whole picture, and re-toning,
    neutralising and blurring all of it is the worst thing this module could do.
    """
    img, _ = _fuzzy_scene()
    empty = np.zeros((H, W), np.uint8)
    out, rep = C.correct(img, empty, preset="punch")
    assert rep["mask_failed"] is True
    assert rep["curve"] == "none"
    assert rep["bg_diffuse"] == 0.0 and rep["bg_neutralize"] == 0.0
    assert float(np.abs(out.astype(float) - img.astype(float)).mean()) < 12


def test_a_lone_osd_rotation_is_downgraded_to_ask():
    """Measured twice on real shoots, once at conf 3.2 with script conf 4.3 on a
    frame containing no text at all — tesseract read fabric fuzz as writing.
    No confidence threshold separates that from a true reading, so a rotation no
    other frame in the shoot agrees with has to be looked at."""
    def rec(angle, source):
        return {"orientation": {"subject_angle": angle, "exif_angle": 0,
                                "applied": angle, "source": source,
                                "needs_ask": False, "notes": []}}
    photos = {"a.jpg": rec(180, "osd"), "b.jpg": rec(0, "unresolved"),
              "c.jpg": rec(0, "unresolved")}
    P._corroborate_orientation(photos, quiet=True)
    o = photos["a.jpg"]["orientation"]
    assert o["needs_ask"] and o["subject_angle"] == 0 and o["applied"] == 0
    assert o["osd_proposal"] == 180, "the proposal must survive for whoever looks"


def test_the_rotation_sheet_cannot_disagree_with_the_manifest():
    """Caught mid-batch on a real shoot.

    The sheet was built from the in-loop verdict objects, and corroboration
    mutates the manifest afterwards — so a downgraded OSD reading was RENDERED
    at 180 and labelled `180deg osd` while the manifest said applied=0, ASK.
    Whoever judges that sheet is answering a question about an image that will
    never ship, and `--rotate` is relative to what the sheet showed. Silent, and
    exactly the class of bug this stage exists to stop.
    """
    photos = {
        "a.jpg": {"orientation": {"subject_angle": 0, "applied": 0, "exif_angle": 0,
                                  "source": "unresolved", "needs_ask": True,
                                  "osd_proposal": 180, "notes": []}},
        "b.jpg": {"orientation": {"subject_angle": 90, "applied": 90, "exif_angle": 0,
                                  "source": "exif+vision", "needs_ask": False,
                                  "notes": []}},
    }
    base, _ = _scene(bg=200, subject=60, box=(100, 100, 300, 200))
    thumbs = [("a.jpg", base), ("b.jpg", base)]

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "rotation_sheet.jpg"
        P._rotation_sheet(thumbs, photos, out, cell=120, cols=2)
        assert out.exists() and out.stat().st_size > 0

    # The un-corroborated frame must be drawn at the manifest's angle (0), not
    # at the proposal, and the label must say why it is being asked about.
    drawn = P.orientmod.rotate_bgr(base, photos["a.jpg"]["orientation"]["subject_angle"])
    assert np.array_equal(drawn, base), "ASK frame must render un-rotated"
    turned = P.orientmod.rotate_bgr(base, photos["b.jpg"]["orientation"]["subject_angle"])
    assert turned.shape[:2] == base.shape[:2][::-1], "resolved frame renders turned"


def test_two_agreeing_osd_readings_are_kept():
    """The esquire shoot: two frames independently read 270 and both were right."""
    def rec(angle, source):
        return {"orientation": {"subject_angle": angle, "exif_angle": 180,
                                "applied": (180 + angle) % 360, "source": source,
                                "needs_ask": False, "notes": []}}
    photos = {"a.jpg": rec(270, "exif+osd"), "b.jpg": rec(270, "exif+osd")}
    P._corroborate_orientation(photos, quiet=True)
    for n in photos:
        o = photos[n]["orientation"]
        assert not o["needs_ask"] and o["subject_angle"] == 270, n


def test_pick_is_required_before_approval():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"orientation": {"needs_ask": False},
                                     "out_sha256": "x", "output": "listing/IMG_0.jpg",
                                     "presets": {"studio": {}}}}
        P.save_manifest(shoot, m)
        try:
            P.run_approve(shoot)
            raise AssertionError("approving without a chosen look must fail")
        except SystemExit as e:
            # The staged review now blocks first — you cannot reach the preset
            # question until orientation, crop and colour are each signed off.
            assert "not approved yet" in str(e) or "no preset picked" in str(e)


def test_pick_copies_the_chosen_look_and_voids_approval():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        pdir = shoot / ".prep" / "presets" / "studio"
        pdir.mkdir(parents=True)
        img, _ = _scene(bg=30, seed=5)
        cv2.imencode(".jpg", img)[1].tofile(str(pdir / "IMG_0.jpg"))

        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {
            "orientation": {"needs_ask": False, "applied": 0, "source": "vision"},
            "src_sha256": P._sha256(shoot / "IMG_0.jpg"),
            "crop": {"applied": False, "reason": ""},
            "presets": {"studio": {"path": ".prep/presets/studio/IMG_0.jpg",
                                   "sha256": P._sha256(pdir / "IMG_0.jpg"),
                                   "report": {}}}}}
        m["approved"] = True
        P.save_manifest(shoot, m)

        m = P.run_pick(shoot, "studio", quiet=True)
        assert m["chosen_preset"] == "studio"
        assert (shoot / "listing" / "IMG_0.jpg").exists()
        assert not m["approved"], "a new look must void the old approval"
        rec = m["photos"]["IMG_0.jpg"]
        assert rec["out_sha256"] == P._sha256(shoot / "listing" / "IMG_0.jpg")


def test_pick_rejects_an_unknown_preset():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"orientation": {"needs_ask": False}, "presets": {}}}
        P.save_manifest(shoot, m)
        try:
            P.run_pick(shoot, "cinematic", quiet=True)
            raise AssertionError("unknown preset must be rejected")
        except SystemExit as e:
            assert "unknown preset" in str(e)


# ---------------------------------------------------------------------------
# integration: the code-level gate, and the two orientation manifests
# ---------------------------------------------------------------------------

def test_upload_refuses_photos_that_did_not_come_through_prep():
    """The gate has to live where photos actually leave the machine.

    A prompt instruction is not a control — the sideways-photo incident happened
    with the rules already written down.
    """
    sys.path.insert(0, str(ROOT / "lib"))
    from list_edit import _assert_photos_cleared

    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        legacy = shoot / "no-exif"
        legacy.mkdir()
        img, _ = _scene(seed=7)
        cv2.imencode(".jpg", img)[1].tofile(str(legacy / "IMG_0.jpg"))
        try:
            _assert_photos_cleared([legacy / "IMG_0.jpg"])
            raise AssertionError("legacy no-exif/ photos must not upload")
        except SystemExit as e:
            assert "PREP GATE" in str(e)
            # the message must name the SHOOT, not the subdirectory
            assert "no-exif:" not in str(e), f"points at the wrong dir: {e}"

    _assert_photos_cleared([])          # nothing to upload is not a failure


def test_recorded_rotations_mirror_into_orientation_json():
    """One fact, two files, is how they come to disagree.

    `orientation.json` (written by the sibling orient.py tool) and prep.json
    record the same quantity — degrees CW on the subject after the EXIF bake —
    and PREP already READS the former. Writing back closes the loop.
    """
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=2)
        m = P.load_manifest(shoot)
        m["photos"] = {
            "IMG_0.jpg": {"orientation": {"exif_angle": 0, "subject_angle": 0,
                                          "needs_ask": True}, "crop": {"applied": False}},
            "IMG_1.jpg": {"orientation": {"exif_angle": 0, "subject_angle": 0,
                                          "needs_ask": True}, "crop": {"applied": False}},
        }
        P.save_manifest(shoot, m)

        P.run_rotate(shoot, ["IMG_0.jpg=90", "IMG_1.jpg=0"])
        written = json.loads((shoot / "orientation.json").read_text(encoding="utf-8"))
        assert written == {"IMG_0.jpg": 90}, written

        # …and PREP reads that file back as an answer, so the call is not
        # asked about twice.
        assert O.recorded_looks(shoot) == {"IMG_0.jpg": 90}


def test_repoint_matches_the_three_live_naming_conventions():
    """9 of 33 drafts in the first fan-out could not be repointed.

    Draft photo lists carry the naming of whatever workflow produced them, and
    three conventions are live at once. All three name the same source frame.
    """
    photos = {"P8140022.JPG": {}, "ZZ150038.JPG": {}, "DSC_0050.JPG": {}}
    cases = [
        ("DSC_0050.JPG", "DSC_0050.JPG"),                        # source frame
        ("listing-photos/01_P8140022.jpg", "P8140022.JPG"),      # index-prefixed
        ("no-exif/ZZ150038r.JPG", "ZZ150038.JPG"),               # orient.py 'r'
        ("listing/DSC_0050.jpg", "DSC_0050.JPG"),                # PREP's own output
        ("something-else.JPG", None),                            # genuinely absent
    ]
    for entry, want in cases:
        assert P.match_prepped(entry, photos) == want, entry


def test_repoint_handles_both_yaml_list_styles_and_keeps_order():
    """Entry one is the eBay gallery image; reordering changes a live listing."""
    import re
    for style, text in [
        ("block", 'title: x\nphotos:\n  - "b.JPG"\n  - "a.JPG"\n\nnext: y\n'),
        ("flow",  'title: x\nphotos: ["b.JPG", "a.JPG"]\n\nnext: y\n'),
    ]:
        with tempfile.TemporaryDirectory() as td:
            shoot = Path(td) / "s"
            shoot.mkdir()
            (shoot / "draft.md").write_text(text, encoding="utf-8")
            m = P.load_manifest(shoot)
            m["chosen_preset"] = "punch"
            m["photos"] = {"a.JPG": {"output": "listing/a.jpg"},
                           "b.JPG": {"output": "listing/b.jpg"}}
            P.save_manifest(shoot, m)

            mapping = P.run_repoint_draft(shoot, apply=True)
            assert [o for _e, o, _c, _q in mapping] == ["listing/b.jpg", "listing/a.jpg"], style
            out = (shoot / "draft.md").read_text(encoding="utf-8")
            assert re.search(r'listing/b\.jpg.*listing/a\.jpg', out, re.S), style
            assert "next: y" in out and "title: x" in out, f"{style}: clobbered the draft"


def test_repoint_preserves_per_frame_comments():
    """Block entries carry annotations documenting what each frame IS —
    `- "P6150001.JPG"   # hero - full-form ornament, front`. They are the only
    record of why the photo order is what it is. A rewrite that drops them
    silently destroys that, and the entries also failed to parse at all."""
    with tempfile.TemporaryDirectory() as td:
        shoot = Path(td) / "s"
        shoot.mkdir()
        (shoot / "draft.md").write_text(
            'photos:\n'
            '  - "a.JPG"      # hero - front\n'
            '  - "b.JPG"      # maker mark\n'
            '\nnext: y\n', encoding="utf-8")
        m = P.load_manifest(shoot)
        m["chosen_preset"] = "punch"
        m["photos"] = {"a.JPG": {"output": "listing/a.jpg"},
                       "b.JPG": {"output": "listing/b.jpg"}}
        P.save_manifest(shoot, m)
        P.run_repoint_draft(shoot, apply=True)
        out = (shoot / "draft.md").read_text(encoding="utf-8")
        assert "# hero - front" in out and "# maker mark" in out, out
        assert "listing/a.jpg" in out and "listing/b.jpg" in out
        assert "next: y" in out


def test_prep_phase_prompt_exists_and_is_wired():
    """PREP is only a phase if the runbook and DRAFT actually point at it."""
    prompt = ROOT / "prompts" / "prep.md"
    assert prompt.exists(), "prompts/prep.md missing"
    run = (ROOT / "RUN.md").read_text(encoding="utf-8")
    assert "prompts/prep.md" in run, "RUN.md does not reference the PREP prompt"
    draft = (ROOT / "prompts" / "draft.md").read_text(encoding="utf-8")
    assert "--repoint-draft" in draft, "DRAFT does not point photos: at listing/"


# ---------------------------------------------------------------------------
# staged review — orientation, then crop, then colour
# ---------------------------------------------------------------------------

def test_stages_run_in_order_and_cannot_be_skipped():
    """A crop judged on a frame that is still going to be rotated is a
    judgement about an image that will not exist."""
    from lib.photo_prep import stages as S
    m = {"photos": {}}
    assert S.stage_blocker(m, "orientation") is None
    assert "orientation" in S.stage_blocker(m, "crop")
    assert S.stage_blocker(m, "color")
    S.stage_state(m)["orientation"]["approved"] = True
    assert "unskew" in S.stage_blocker(m, "crop")
    S.stage_state(m)["unskew"]["approved"] = True
    assert S.stage_blocker(m, "crop") is None
    assert "crop" in S.stage_blocker(m, "color")


def test_approving_a_stage_invalidates_the_later_ones():
    """Revisiting orientation must not leave a crop sign-off standing."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"orientation": {"needs_ask": False},
                                     "crop": {"applied": False}}}
        P.save_manifest(shoot, m)
        P.run_approve_stage(shoot, "orientation")
        P.run_approve_stage(shoot, "unskew")
        P.run_approve_stage(shoot, "crop")
        m = P.load_manifest(shoot)
        assert m["stages"]["crop"]["approved"]
        P.run_approve_stage(shoot, "orientation")      # revisit the first
        m = P.load_manifest(shoot)
        assert not m["stages"]["crop"]["approved"], "later stages must reset"


def test_a_stage_cannot_be_approved_while_frames_are_outstanding():
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"orientation": {"needs_ask": True},
                                     "crop": {"applied": False}}}
        P.save_manifest(shoot, m)
        try:
            P.run_approve_stage(shoot, "orientation")
            raise AssertionError("an unresolved frame must block the stage")
        except SystemExit as e:
            assert "outstanding" in str(e)


def test_a_vivid_object_in_the_backdrop_is_not_neutralised():
    """The christmas-train failure, in miniature.

    On ZZ170057 the segmenter kept the reindeer and handed the entire red car
    body to the backdrop. Neutralising duly drove it toward grey and 38% of the
    red's saturation went with it — measured, not guessed. `_protect_objects`
    could not save it because it only tested LUMA, and the car sits at roughly
    the brightness of the cloth.

    Cloth is nearly colourless; paint is not. So a large, strongly coloured
    region outside the mask is an object, whatever its brightness.
    """
    img, _ = _scene(bg=60, subject=60, noise=2)            # item luma == backdrop luma
    img[300:700, 350:850] = (40, 45, 200)                  # a vivid red block, BGR
    mask = np.zeros((H, W), np.uint8)                      # mask misses it entirely

    out, _rep = C.correct(img, mask, sweep=True, bg_class="dark", preset="crisp")

    def sat(im):
        return float(cv2.cvtColor(im[320:680, 370:830], cv2.COLOR_BGR2HSV)[:, :, 1].mean())

    before, after = sat(img), sat(out)
    assert after > before * 0.9, (
        f"the red block was neutralised: saturation {before:.0f} -> {after:.0f}")


def test_the_chroma_bar_sits_between_cloth_and_paint():
    """Measured on real frames: true backdrop reaches chroma 24 at p99, painted
    red starts at 54. The bar has to sit in that gap with margin on both sides —
    too low and felt gets protected from its own blur, too high and paint is
    treated as cloth again."""
    assert 24 < C.CHROMA_OBJECT_MIN < 54


def test_a_redacted_frame_replaces_its_original():
    """The un-redacted frame must never reach the shipping directory.

    A mailing label gets redacted to `<stem>_REDACTED.jpg` alongside the
    original. PREP was preparing BOTH and writing both into listing/ — the
    directory DRAFT reads and the uploader ships from. The draft happened to
    point at the redacted one, so nothing leaked; but one lexicographic photo
    picker or one re-run of an older draft and a customer's home address goes
    public. The redacted copy replaces its original; it is not an extra frame.
    """
    with tempfile.TemporaryDirectory() as td:
        shoot = Path(td) / "s"
        shoot.mkdir()
        img = np.full((60, 80, 3), 200, np.uint8)
        for n in ("cover.jpg", "label.jpg", "label_REDACTED.jpg", "back.jpg"):
            cv2.imwrite(str(shoot / n), img)

        got = {p.name for p in P.find_images(shoot)}
        assert "label.jpg" not in got, f"the un-redacted original survived: {got}"
        assert "label_REDACTED.jpg" in got, got
        assert {"cover.jpg", "back.jpg"} <= got, got


def test_redaction_guard_reaches_the_fallback_directories():
    """no-exif/ holds copies of the same frames — the guard must cover them too."""
    with tempfile.TemporaryDirectory() as td:
        shoot = Path(td) / "s"
        (shoot / "no-exif").mkdir(parents=True)
        img = np.full((60, 80, 3), 200, np.uint8)
        cv2.imwrite(str(shoot / "label_REDACTED.jpg"), img)
        cv2.imwrite(str(shoot / "no-exif" / "label.jpg"), img)

        got = {p.name for p in P.find_images(shoot)}
        assert got == {"label_REDACTED.jpg"}, got


def test_set_rotate_is_absolute_and_rotate_is_relative():
    """A generated command must be safe to paste twice.

    --rotate is relative on purpose: a human answering a contact sheet should
    not do arithmetic against a rotation they cannot see. But the review page
    generates its command from the angle it displays, and a relative command
    run twice moves the frame twice — it took a catalog spread from applied 0
    to 270 on the second paste of an identical line.
    """
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"orientation": {"exif_angle": 90, "subject_angle": 0,
                                                     "applied": 90, "needs_ask": True}}}
        P.save_manifest(shoot, m)

        P.run_rotate(shoot, ["IMG_0.jpg=270"])                      # relative
        assert P.load_manifest(shoot)["photos"]["IMG_0.jpg"]["orientation"]["subject_angle"] == 270
        P.run_rotate(shoot, ["IMG_0.jpg=270"])                      # relative again: moves
        assert P.load_manifest(shoot)["photos"]["IMG_0.jpg"]["orientation"]["subject_angle"] == 180

        for _ in range(3):                                          # absolute: settles
            P.run_rotate(shoot, ["IMG_0.jpg=270"], absolute=True)
            o = P.load_manifest(shoot)["photos"]["IMG_0.jpg"]["orientation"]
            assert o["subject_angle"] == 270, o
            assert o["applied"] == 0, o                             # 90 exif + 270 subject


def test_recording_a_rotation_invalidates_the_approvals():
    """A changed DECISION is as stale as a changed file.

    paul-fredrick sat at approved=true with all four stages signed off while six
    frames carried a rotation the shipping files had never been rendered with —
    the manifest said 270 degrees and listing/ still held the portrait frame.
    Nothing caught it, because no file had changed.
    """
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=1)
        m = P.load_manifest(shoot)
        m["photos"] = {"IMG_0.jpg": {"orientation": {"exif_angle": 0, "subject_angle": 0,
                                                     "applied": 0, "needs_ask": False}}}
        m["approved"] = True
        m["approved_at"] = "2026-08-19T00:00:00+00:00"
        from lib.photo_prep import stages as S
        for st in S.STAGES:
            S.stage_state(m)[st] = {"approved": True, "approved_at": "x"}
        P.save_manifest(shoot, m)

        P.run_rotate(shoot, ["IMG_0.jpg=90"], absolute=True)

        m = P.load_manifest(shoot)
        assert m["approved"] is False, "the publish stamp survived a rotation change"
        assert m["approved_at"] is None
        for st in S.STAGES:
            assert not m["stages"][st]["approved"], f"{st} stayed approved"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                              # noqa: BLE001
            bad += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)


def test_warm_metal_on_dark_cloth_defaults_to_crisp():
    """The keys-shoot rule. Brass on navy is where white balance does damage:
    the cloth's chroma sits on WB_MAX_CHROMA, so the gain fires on some frames
    and not others, and what it lifts is green — the opposite of brass. Measured
    on goodwill/keys: punch moved the item's mean colour by 21/255 and reversed
    its channel order on the worst frame. A cool or neutral item on the same
    cloth is unaffected and keeps punch."""
    img, mask = _fuzzy_scene(subject=(80, 120, 170))        # BGR: warm brass
    warm = C.subject_warmth(img, mask)
    assert warm["warm"] is True, warm
    assert warm["r_minus_b"] >= C.WARM_SUBJECT_MIN_RB, warm
    assert C.default_preset_for("dark", warm_subject=True) == "crisp"

    cool, _ = _fuzzy_scene(subject=(170, 120, 80))[0], None  # BGR: cool blue item
    assert C.subject_warmth(cool, mask)["warm"] is False
    assert C.default_preset_for("dark", warm_subject=False) == "punch"
    # A warm item on a white sweep is NOT the failing case — white balance off a
    # real grey sweep is the correction working as intended.
    assert C.default_preset_for("light", warm_subject=True) == "studio"


def test_crisp_keeps_the_cameras_colour_on_the_item():
    """`crisp` gives up exactly one thing — the global white-balance gain — and
    that is what protects the item's hue. It must land closer to the camera file
    than punch does, and must not reorder the channels."""
    img, mask = _fuzzy_scene(subject=(80, 120, 170))
    src = img[mask > 0].reshape(-1, 3).mean(axis=0)          # BGR
    out_crisp, rep = C.correct(img, mask, bg_class="dark", sweep=True, preset="crisp")
    out_punch, _ = C.correct(img, mask, bg_class="dark", sweep=True, preset="punch")
    assert "off" in rep["wb_note"], rep["wb_note"]

    got = out_crisp[mask > 0].reshape(-1, 3).mean(axis=0)
    hit = out_punch[mask > 0].reshape(-1, 3).mean(axis=0)
    # R-B is the warmth of the metal; crisp must preserve it better than punch.
    assert abs((got[2] - got[0]) - (src[2] - src[0])) <= abs((hit[2] - hit[0]) - (src[2] - src[0]))
    assert np.argsort(got).tolist() == np.argsort(src).tolist(), (src, got)


# ---------------------------------------------------------------------------
# unskew — square up a rectangle, refuse everything that is not one
# ---------------------------------------------------------------------------

from lib.photo_prep import unskew as U                     # noqa: E402


class _M:
    """The two fields unskew.plan reads off a SubjectMask."""
    def __init__(self, mask):
        self.mask = mask


def _quad_scene(quad, bg=30, subject=200):
    """A filled quadrilateral on a flat backdrop, plus its mask."""
    img = np.full((H, W, 3), bg, np.uint8)
    mask = np.zeros((H, W), np.uint8)
    q = np.array(quad, np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(img, [q], (subject, subject, subject))
    cv2.fillPoly(mask, [q], 255)
    return img, mask


def _tilted_rect(cx, cy, w, h, deg):
    a = np.radians(deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    pts = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    return (pts @ R.T + (cx, cy)).tolist()


def test_unskew_squares_a_tilted_frame():
    """A rectangle rotated 4 degrees is the whole point of the stage."""
    img, mask = _quad_scene(_tilted_rect(600, 450, 620, 460, 4.0))
    sk = U.plan(img, _M(mask))
    assert sk.applied, sk.reason
    assert 3.0 <= sk.tilt_deg <= 5.0, sk.tilt_deg
    assert sk.fill > U.MIN_FILL, sk.fill

    out = U.apply(img, sk)
    after = U.plan(out, _M(U.apply_mask(mask, sk)))
    assert not after.applied and "already square" in after.reason, after.reason


def test_unskew_leaves_a_frame_that_is_already_square():
    img, mask = _quad_scene([[300, 250], [900, 250], [900, 700], [300, 700]])
    sk = U.plan(img, _M(mask))
    assert not sk.applied and "already square" in sk.reason, sk.reason
    assert np.array_equal(U.apply(img, sk), img), "a no-op must not resample"


def test_unskew_refuses_a_round_item():
    """A marble has no square to restore; a quad fitted to it is noise.

    This is the guard that keeps the stage out of the way of every non-flat
    item in the inventory.
    """
    img = np.full((H, W, 3), 30, np.uint8)
    mask = np.zeros((H, W), np.uint8)
    cv2.ellipse(img, (600, 450), (260, 210), 12, 0, 360, (200, 200, 200), -1)
    cv2.ellipse(mask, (600, 450), (260, 210), 12, 0, 360, 255, -1)
    sk = U.plan(img, _M(mask))
    assert not sk.applied, sk.reason
    assert "not a rectangle" in sk.reason, sk.reason
    assert sk.fill < U.MIN_FILL, sk.fill


def test_unskew_refuses_a_deliberately_angled_shot():
    """Steep keystone is a three-quarter view, not a mistake. Squaring it would
    throw away the shot the photographer meant to take."""
    img, mask = _quad_scene([[250, 260], [980, 150], [980, 760], [250, 640]])
    sk = U.plan(img, _M(mask))
    assert not sk.applied, sk.reason
    assert "on purpose" in sk.reason or "not a rectangle" in sk.reason, sk.reason


def test_unskew_keeps_the_items_own_proportions():
    """The destination rectangle is measured from the item's opposite edges, so
    a 4:3 painting comes out 4:3 — the correction restores a rectangle, it does
    not invent a nicer one."""
    img, mask = _quad_scene(_tilted_rect(600, 450, 640, 480, 3.0))
    sk = U.plan(img, _M(mask))
    assert sk.applied, sk.reason
    (x0, y0), (x1, _), (_, y2), _ = sk.dst
    assert abs(((x1 - x0) / (y2 - y0)) - 4 / 3) < 0.03, sk.dst


def test_unskew_never_crops():
    """The canvas grows to hold every source pixel. A stage that silently ate
    the corners of a frame would be indistinguishable from a bad crop."""
    img, mask = _quad_scene(_tilted_rect(600, 450, 620, 460, 5.0))
    sk = U.plan(img, _M(mask))
    assert sk.applied, sk.reason
    out = U.apply(img, sk)
    assert out.shape[0] >= H and out.shape[1] >= W, out.shape
    assert (out.shape[0] * out.shape[1]) / (H * W) <= U.MAX_CANVAS_GROWTH


def test_unskew_operator_on_waives_only_the_shape_test():
    """`--unskew NAME=on` says "this is a rectangle" — a mount, a mat or a
    shadow can easily cost a real frame its shape score. It must not become a
    licence to flatten a shot that was angled on purpose."""
    # A tilted rectangle with a bite out of one side: unambiguously tilted,
    # but too ragged to pass the shape test on its own.
    img, mask = _quad_scene(_tilted_rect(600, 450, 620, 460, 5.0))
    notch = np.array([[600, 200], [960, 250], [900, 700]], np.int32)
    cv2.fillPoly(img, [notch], (30, 30, 30))
    cv2.fillPoly(mask, [notch], 0)

    free = U.plan(img, _M(mask))
    assert not free.applied and "not a rectangle" in free.reason, free.reason
    forced = U.plan(img, _M(mask), rectangular=True)
    assert forced.applied, forced.reason
    assert 4.0 <= forced.tilt_deg <= 6.0, forced.tilt_deg

    steep, smask = _quad_scene([[250, 260], [980, 150], [980, 760], [250, 640]])
    over = U.plan(steep, _M(smask), rectangular=True)
    assert not over.applied, "a forced unskew must still obey the magnitude guards"


def test_unskew_runs_before_crop_in_the_stage_order():
    """Order is a dependency, not a preference: a crop box measured on skewed
    pixels describes a frame that is about to change shape."""
    from lib.photo_prep import stages as S
    assert S.STAGES == ("orientation", "unskew", "crop", "color")
    assert S.STAGES.index("unskew") < S.STAGES.index("crop")
    assert S.stage_blocker({}, "crop"), "crop must not open before unskew is approved"
