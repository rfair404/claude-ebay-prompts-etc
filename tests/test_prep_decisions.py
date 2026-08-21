#!/usr/bin/env python3
"""The decision record — issue #21, stage 1.

Every test here locks down one half of the same promise: **an approval attaches
to the decisions it was given.** Change a decision and the approval stops
matching, automatically, with nobody remembering to call an invalidator.

The other half matters just as much and is easier to lose: the record must NOT
move for things that do not change the rendered output. An invalidator that
fires on a tesseract upgrade, a re-measured backdrop luma or a timestamp gets
switched off, and then it protects nothing.

Run:  python tests/test_prep_decisions.py
  or: pytest tests/test_prep_decisions.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.photo_prep import decisions as D                  # noqa: E402
from lib.photo_prep import stages as S                     # noqa: E402


def _manifest() -> dict:
    """A two-frame shoot with every stage signed off."""
    m = {
        "settings": {"aspect": "1:1", "pad": 0.12, "pop": "gentle",
                     "subject": "auto", "category": "default"},
        "chosen_preset": "studio",
        "preset_picked_by_operator": True,
        "photos": {
            "a.jpg": {
                "orientation": {"applied": 90, "source": "exif+osd",
                                "needs_ask": False, "osd_conf": 6.2,
                                "osd_note": "OSD conf 6.2 @2400px"},
                "unskew": {"applied": False, "operator": False},
                "crop": {"applied": True, "box": [10, 20, 900, 910],
                         "operator": False},
                "color": {"bg_luma_before": 200.0},
            },
            "b.jpg": {
                "orientation": {"applied": 0, "source": "exif+vision",
                                "needs_ask": False, "osd_conf": 0.4},
                "unskew": {"applied": True, "angle": 1.23456,
                           "quad": [[0, 0], [10, 0], [10, 10], [0, 10]],
                           "operator": True},
                "crop": {"applied": False, "operator": False},
            },
        },
    }
    m["stages"] = {}
    for st in S.STAGES:
        m["stages"][st] = dict(approved=True, approved_at="t", **D.stamp(m, st))
    return m


# ---------------------------------------------------------------------------
# the record is pure data
# ---------------------------------------------------------------------------

def test_the_record_carries_no_file_paths():
    """#21's first requirement, and the one a refactor drifts away from.

    A record that mentions where the shoot lives cannot be compared across a
    copy, a move, or a machine — and the whole point is that it is the half that
    does NOT depend on files.
    """
    rec = D.record_for(_manifest())
    blob = D.canonical(rec)
    for probe in ("/", "\\", ".prep", "listing", "presets", "sha256"):
        assert probe not in blob, f"{probe!r} leaked into the decision record"
    # Frame identity is a basename, which is identity rather than a location.
    assert set(rec["frames"]) == {"a.jpg", "b.jpg"}
    for frame in rec["frames"].values():
        assert "path" not in frame and "output" not in frame


def test_the_digest_is_stable_across_key_order():
    """Same decisions, different dict order — the digest cannot notice.

    It is compared against a value stored days earlier by a different process,
    so anything order-dependent makes it useless.
    """
    m1 = _manifest()
    m2 = _manifest()
    m2["photos"] = {k: m2["photos"][k] for k in reversed(list(m2["photos"]))}
    m2["settings"] = {k: m2["settings"][k] for k in reversed(list(m2["settings"]))}
    assert D.digest_for(m1) == D.digest_for(m2)


def test_a_reserialised_manifest_digests_the_same():
    """The manifest round-trips through JSON on every command."""
    m = _manifest()
    before = D.digest_for(m)
    after = D.digest_for(json.loads(json.dumps(m)))
    assert before == after


# ---------------------------------------------------------------------------
# what moves it, and what must not
# ---------------------------------------------------------------------------

def test_a_changed_rotation_moves_the_digest():
    m = _manifest()
    before = D.digest_for(m)
    m["photos"]["a.jpg"]["orientation"]["applied"] = 180
    assert D.digest_for(m) != before


def test_a_changed_crop_box_moves_the_digest():
    m = _manifest()
    before = D.digest_for(m)
    m["photos"]["a.jpg"]["crop"]["box"] = [11, 20, 900, 910]
    assert D.digest_for(m) != before


def test_who_decided_is_part_of_the_decision():
    """An identical box a human set and one the planner proposed are different
    decisions, because re-planning may overwrite one and not the other."""
    m = _manifest()
    before = D.digest_for(m)
    m["photos"]["a.jpg"]["crop"]["operator"] = True      # same box, human's now
    assert D.digest_for(m) != before, "provenance was dropped from the record"


def test_an_auto_default_and_a_deliberate_pick_are_different_decisions():
    """The auto-pick race: --pick reported success while a background --apply
    re-adopted the default. Same preset name, different decision — and only one
    of them survives the next --apply."""
    m = _manifest()
    before = D.digest_for(m)
    m["preset_picked_by_operator"] = False               # same preset, auto now
    assert D.digest_for(m) != before


def test_evidence_does_not_move_the_digest():
    """THE OTHER HALF OF THE PROMISE.

    Confidence scores, OSD notes and backdrop statistics explain a decision;
    they are not one. If a tesseract upgrade that re-reads the same rotation at
    a different confidence invalidated sign-offs, the invalidation would be
    noise, and an invalidator that fires on nothing gets switched off.
    """
    m = _manifest()
    before = D.digest_for(m)
    m["photos"]["a.jpg"]["orientation"]["osd_conf"] = 11.4
    m["photos"]["a.jpg"]["orientation"]["osd_note"] = "re-read after an upgrade"
    m["photos"]["a.jpg"]["color"]["bg_luma_before"] = 137.2
    assert D.digest_for(m) == before, "evidence leaked into the decision record"


def test_pad_and_aspect_do_not_move_the_digest():
    """They are inputs to planning a box; the box itself is already recorded.
    Changing the pad after an operator fixed a box changes nothing that renders."""
    m = _manifest()
    before = D.digest_for(m)
    m["settings"]["pad"] = 0.30
    m["settings"]["aspect"] = "4:3"
    assert D.digest_for(m) == before


def test_the_detector_choice_does_move_the_digest():
    """`subject` decides which mask everything downstream is measured against,
    so it reaches the rendered pixels and belongs in the record."""
    m = _manifest()
    before = D.digest_for(m)
    m["settings"]["subject"] = "paper"
    assert D.digest_for(m) != before


# ---------------------------------------------------------------------------
# stage scoping
# ---------------------------------------------------------------------------

def test_a_stage_digest_covers_only_what_that_stage_depends_on():
    """Approving orientation is what LICENSES the geometry to be planned, so
    hashing the whole record into that approval would invalidate it the instant
    it was granted, by the very planning it authorised."""
    m = _manifest()
    before = D.digest_for(m, "orientation")
    m["photos"]["a.jpg"]["crop"]["box"] = [0, 0, 500, 500]
    m["photos"]["b.jpg"]["unskew"]["angle"] = 9.9
    assert D.digest_for(m, "orientation") == before
    assert D.digest_for(m, "crop") != before


def test_picking_a_look_does_not_invalidate_the_crop():
    """Backwards otherwise: the crop is what the look is chosen ON TOP OF, and
    every preset renders from the same box."""
    m = _manifest()
    before = D.digest_for(m, "crop")
    m["chosen_preset"] = "asshot"
    assert D.digest_for(m, "crop") == before
    assert "look" not in D.record_for(m, "crop")
    assert "look" in D.record_for(m, "color")


def test_a_rotation_invalidates_every_stage_built_on_it():
    """The cascade is the point. A crop decided on an orientation that has since
    been edited is a decision about a frame that already changed."""
    m = _manifest()
    assert D.stale_stages(m, S.STAGES) == []
    m["photos"]["a.jpg"]["orientation"]["applied"] = 270
    stale = dict(D.stale_stages(m, S.STAGES))
    assert set(stale) == set(S.STAGES), f"the cascade stopped early: {sorted(stale)}"
    assert "a.jpg" in stale["crop"][0] and "270" in stale["crop"][0]


def test_staleness_says_what_changed():
    """'approval is stale' with no reason is the message that gets worked
    around; naming the frame and the move is the one that gets fixed."""
    m = _manifest()
    m["photos"]["b.jpg"]["crop"] = {"applied": True, "box": [1, 2, 3, 4],
                                    "operator": True}
    (_stage, why), = [r for r in D.stale_stages(m, ["crop"])]
    assert any("b.jpg" in w and "crop" in w for w in why), why


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def test_the_stage_gate_refuses_to_build_on_stale_decisions():
    m = _manifest()
    assert S.stage_blocker(m, "crop") is None
    m["photos"]["a.jpg"]["orientation"]["applied"] = 180
    blocked = S.stage_blocker(m, "crop")
    assert blocked and "decisions have changed" in blocked, blocked
    # It still reads as approved in the manifest -- that is exactly the state
    # that used to pass, and the reason the check cannot rely on the flag.
    assert m["stages"]["orientation"]["approved"] is True


def test_manifests_written_before_hashes_existed_are_not_all_stale():
    """~150 manifests predate this. Invalidating every one of them at once
    would train everyone to ignore the message, which costs more than it buys —
    they re-stamp on their next approval."""
    m = _manifest()
    for st in S.STAGES:
        m["stages"][st].pop("decision_hash", None)
        m["stages"][st].pop("decisions", None)
    assert D.stale_stages(m, S.STAGES) == []
    assert S.stage_blocker(m, "crop") is None


def test_an_unapproved_stage_is_never_reported_stale():
    """Staleness is a property of a sign-off. Without one there is nothing to
    go stale, and saying otherwise would bury the real ones."""
    m = _manifest()
    m["stages"]["crop"]["approved"] = False
    m["photos"]["a.jpg"]["crop"]["box"] = [0, 0, 10, 10]
    assert "crop" not in dict(D.stale_stages(m, ["crop"]))


def test_an_unknown_stage_is_refused():
    try:
        D.record_for(_manifest(), "colour")     # the British spelling, and wrong
    except ValueError as e:
        assert "color" in str(e)
    else:
        raise AssertionError("an unknown stage was accepted")


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
