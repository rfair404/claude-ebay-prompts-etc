"""tests/test_status.py — `ebz status` (#61/#62): one read pass over a shoot
dir instead of the ls/cat/grep sequence an operator ran by hand (measured:
2,638 such calls, 13.4h across 114 sessions — #61).

Deliberately thin: the per-stage "is this done" logic already has one
definition, in lib/single_pass.py's STAGE_CHECK, and these tests don't
re-litigate it — they lock down status.py's own job (frame count, sku/ledger
lookup, and assembling the one-line next_action).
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import status as st  # noqa: E402


def _clean_prep_manifest(names=("a.jpg",)) -> dict:
    photos = {n: {"orientation": {"applied": 0, "needs_ask": False, "guessed": False,
                                   "subject_angle": 0, "osd_proposal": 0},
                  "crop": {"applied": True, "box": [0, 0, 10, 10]}}
              for n in names}
    return {"version": 1, "photos": photos, "approved": True, "auto": {"guessed": []}}


def _clean_shoot(tmp_path: Path, sku: str = "") -> Path:
    shoot = tmp_path / "item"
    shoot.mkdir()
    (shoot / "identify.txt").write_text("SHOOT SUMMARY\n", encoding="utf-8")
    prep_dir = shoot / ".prep"
    prep_dir.mkdir()
    (prep_dir / "prep.json").write_text(json.dumps(_clean_prep_manifest()), encoding="utf-8")
    (shoot / "price.txt").write_text("Max supported price: $40\n", encoding="utf-8")
    (shoot / "investigate.txt").write_text("fine.\n", encoding="utf-8")
    sku_line = f'  ebay_inventory_sku: "{sku}"\n' if sku else ""
    (shoot / "draft.md").write_text(
        f'---\ntitle: "x"\nmeta:\n{sku_line}---\nbody\n', encoding="utf-8")
    (shoot / "a.jpg").write_bytes(b"\xff\xd8")
    (shoot / "b.JPG").write_bytes(b"\xff\xd8")
    (shoot / "notes.txt").write_text("not a frame\n", encoding="utf-8")
    return shoot


# --------------------------------------------------------------------------
# frame count — images only, case-insensitive extension
# --------------------------------------------------------------------------
def test_frame_count_ignores_non_image_files(tmp_path):
    shoot = _clean_shoot(tmp_path)
    assert st._frame_count(shoot) == 2, "a.jpg + b.JPG, not notes.txt or the .prep/ dir"


# --------------------------------------------------------------------------
# next_action — the first stage with something pending, in STAGE_ORDER
# --------------------------------------------------------------------------
def test_empty_shoot_names_identify_as_next_action(tmp_path):
    shoot = tmp_path / "empty"
    shoot.mkdir()
    state = st.gather(shoot)
    assert state["next_action"].startswith("identify:")
    assert state["stages"]["identify"]["pending"]
    assert state["stages"]["identify"]["file"] is None


def test_clean_shoot_reports_ready_for_review(tmp_path):
    shoot = _clean_shoot(tmp_path)
    state = st.gather(shoot)
    assert state["next_action"] == "all stages clear — ready for REVIEW"
    for stage in ("identify", "prep", "price", "investigate", "draft"):
        assert state["stages"][stage]["pending"] == [], stage
        assert state["stages"][stage]["file"] is not None, stage


def test_next_action_stops_at_the_first_unresolved_stage(tmp_path):
    # identify + a NOT-approved prep manifest: prep should be the blocker,
    # not price/investigate/draft even though those files don't exist either.
    shoot = tmp_path / "item"
    shoot.mkdir()
    (shoot / "identify.txt").write_text("x\n", encoding="utf-8")
    prep_dir = shoot / ".prep"
    prep_dir.mkdir()
    manifest = _clean_prep_manifest()
    manifest["approved"] = False
    (prep_dir / "prep.json").write_text(json.dumps(manifest), encoding="utf-8")

    state = st.gather(shoot)
    assert state["next_action"].startswith("prep:")
    assert state["stages"]["price"]["pending"], "price hasn't run either, but isn't the blocker reported"


# --------------------------------------------------------------------------
# sku / ledger lookup
# --------------------------------------------------------------------------
def test_sku_and_ledger_row_are_read_when_present(tmp_path, monkeypatch):
    ledger = tmp_path / "listings_ledger.csv"
    with ledger.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "status", "listing_id"])
        w.writerow(["SKU-999", "SYNCED", "L12345"])
    monkeypatch.setattr(st, "LEDGER", ledger)

    shoot = _clean_shoot(tmp_path, sku="SKU-999")
    state = st.gather(shoot)
    assert state["sku"] == "SKU-999"
    assert state["ledger_status"] == "SYNCED"
    assert state["listing_id"] == "L12345"


def test_no_sku_means_no_ledger_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "LEDGER", tmp_path / "listings_ledger.csv")
    shoot = _clean_shoot(tmp_path)  # no sku in draft.md
    state = st.gather(shoot)
    assert state["sku"] is None
    assert state["ledger_status"] is None


# --------------------------------------------------------------------------
# summary() — human-readable text mirrors gather()'s state
# --------------------------------------------------------------------------
def test_summary_marks_done_pending_and_not_started():
    state = {
        "shoot": "inventory/x", "frames": 3,
        "stages": {
            "identify": {"file": "identify.txt", "pending": []},
            "prep": {"file": ".prep/prep.json", "pending": ["not approved"]},
            "price": {"file": None, "pending": ["price.txt not written yet"]},
            "investigate": {"file": None, "pending": ["investigate.txt not written yet"]},
            "draft": {"file": None, "pending": ["draft.md not written yet"]},
        },
        "sku": None, "ledger_status": None, "listing_id": None,
        "next_action": "prep: not approved",
    }
    out = st.summary(state)
    assert "✓ identify" in out
    assert "⚠ prep" in out
    assert "· price" in out
    assert out.endswith("→ prep: not approved")
