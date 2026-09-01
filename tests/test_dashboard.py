"""tests/test_dashboard.py — `ebz dashboard` (#31 Phase 1): the read-only
local dashboard extending tools/sales_report.py's gather()/draw() pattern to
backlog-by-stage, drafts-awaiting-review, and live/ledger drift.

Deliberately thin on the per-shoot stage logic itself (that's
lib/status.py's job and tests/test_status.py's coverage) — these tests lock
down dashboard.py's own job: finding shoot dirs, bucketing by blocking stage,
filtering the draft/ledger merge to unpublished rows, diffing the two local
CSVs, and rendering all three into HTML without ever leaking raw prose.

No network, no credentials, no real inventory/ledger — every fixture below
is synthetic, following the monkeypatch-module-constants pattern
tests/test_source_report.py and tests/test_status.py already use.
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import tools.dashboard as dash    # noqa: E402
import report as _report          # noqa: E402
import source_report as _sr       # noqa: E402
import status as _status          # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _write_ledger(path: Path, rows: list[dict]) -> None:
    fields = ["sku", "status", "title", "price", "offer_id", "listing_id", "url",
              "drafted_at", "synced_at", "published_at", "ended_at", "shipped_at",
              "updated_at"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _write_live_sheet(path: Path, rows: list[dict]) -> None:
    fields = ["sku", "title", "listing_id", "item_url", "offer_id", "live",
              "offer_status", "listing_status", "quantity", "price", "currency",
              "format", "marketplace", "condition", "category_id", "category_path",
              "category_top", "variation_count", "image_count", "aspect_type",
              "aspect_brand"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty synthetic repo, wired into dashboard.py and every module it
    delegates to (lib.status, lib.report, lib.source_report) so none of them
    touch the real, gitignored inventory/ or ledgers."""
    inv = tmp_path / "inventory"
    inv.mkdir()
    ledger = tmp_path / "listings_ledger.csv"
    live = tmp_path / "inventory_sheet.csv"

    monkeypatch.setattr(dash, "REPO", tmp_path)
    monkeypatch.setattr(dash, "INVENTORY", inv)
    monkeypatch.setattr(dash, "LEDGER", ledger)
    monkeypatch.setattr(dash, "LIVE_SHEET", live)
    monkeypatch.setattr(_status, "LEDGER", ledger)
    monkeypatch.setattr(_report, "REPO", tmp_path)
    monkeypatch.setattr(_report, "INVENTORY", inv)
    monkeypatch.setattr(_report, "LEDGER", ledger)
    monkeypatch.setattr(_sr, "REPO", tmp_path)
    monkeypatch.setattr(_sr, "INVENTORY", inv)
    return tmp_path


def _shoot(inv: Path, *parts: str) -> Path:
    d = inv.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _frame(d: Path, name: str = "a.jpg") -> None:
    (d / name).write_bytes(b"\xff\xd8")


# ---------------------------------------------------------------------------
# iter_shoot_dirs / _looks_like_shoot
# ---------------------------------------------------------------------------

def test_a_dir_with_only_a_frame_counts_as_a_shoot(repo):
    shoot = _shoot(repo / "inventory", "lot", "item-1")
    _frame(shoot)
    assert list(dash.iter_shoot_dirs()) == [shoot]


def test_a_dir_with_a_stage_output_but_no_frame_still_counts(repo):
    # e.g. frames were moved/archived after PREP but the manifest remains.
    shoot = _shoot(repo / "inventory", "lot", "item-1")
    (shoot / "identify.txt").write_text("x\n")
    assert list(dash.iter_shoot_dirs()) == [shoot]


def test_backup_and_dot_dirs_are_excluded(repo):
    inv = repo / "inventory"
    real = _shoot(inv, "lot", "item-1")
    _frame(real)
    backup = _shoot(inv, "lot", "_prepped", "item-1-bak")
    _frame(backup)
    dotdir = _shoot(inv, "lot", ".history", "item-1-old")
    _frame(dotdir)
    assert list(dash.iter_shoot_dirs()) == [real]


def test_a_plain_non_shoot_directory_is_not_yielded(repo):
    # A grouping directory (e.g. "lot/") holding shoots but no frames/outputs
    # of its own must not appear as a phantom row.
    _shoot(repo / "inventory", "lot", "item-1")   # empty leaf, no frame yet
    (repo / "inventory" / "lot" / "item-1" / "notes.txt").write_text("not a frame\n")
    assert list(dash.iter_shoot_dirs()) == []


def test_no_inventory_directory_yields_nothing(repo):
    import shutil
    shutil.rmtree(repo / "inventory")
    assert list(dash.iter_shoot_dirs()) == []


# ---------------------------------------------------------------------------
# gather_backlog
# ---------------------------------------------------------------------------

def test_gather_backlog_empty_tree(repo):
    d = dash.gather_backlog()
    assert d["rows"] == []
    assert not d["stage_counts"]
    assert d["count"] == 0


def test_gather_backlog_buckets_by_the_blocking_stage(repo):
    inv = repo / "inventory"
    fresh = _shoot(inv, "lot", "fresh")
    _frame(fresh)                                    # nothing done -> identify

    ready = _shoot(inv, "lot", "ready")
    _frame(ready)
    (ready / "identify.txt").write_text("x\n")
    prep_dir = ready / ".prep"
    prep_dir.mkdir()
    import json
    manifest = {"version": 1, "photos": {
        "a.jpg": {"orientation": {"applied": 0, "needs_ask": False, "guessed": False,
                                  "subject_angle": 0, "osd_proposal": 0},
                  "crop": {"applied": True, "box": [0, 0, 10, 10]}}},
        "approved": True, "auto": {"guessed": []}}
    (prep_dir / "prep.json").write_text(json.dumps(manifest))
    (ready / "price.txt").write_text("Max supported price: $40\n")
    (ready / "investigate.txt").write_text("fine\n")
    (ready / "draft.md").write_text('---\ntitle: "x"\nmeta:\n---\nbody\n')

    d = dash.gather_backlog()
    by_dir = {r["dir"]: r for r in d["rows"]}
    assert by_dir["inventory/lot/fresh"]["blocked_stage"] == "identify"
    assert by_dir["inventory/lot/ready"]["blocked_stage"] is None
    assert d["stage_counts"]["identify"] == 1
    assert d["stage_counts"]["ready for review"] == 1
    assert d["count"] == 2


def test_gather_backlog_rows_carry_sku_and_ledger_status(repo):
    inv = repo / "inventory"
    shoot = _shoot(inv, "lot", "item-1")
    _frame(shoot)
    (shoot / "draft.md").write_text(
        '---\ntitle: "x"\nmeta:\n  ebay_inventory_sku: "abc12345"\n---\nbody\n')
    _write_ledger(repo / "listings_ledger.csv",
                  [{"sku": "abc12345", "status": "SYNCED"}])

    d = dash.gather_backlog()
    row = d["rows"][0]
    assert row["sku"] == "abc12345"
    assert row["ledger_status"] == "SYNCED"


# ---------------------------------------------------------------------------
# gather_drafts
# ---------------------------------------------------------------------------

def test_gather_drafts_empty(repo):
    d = dash.gather_drafts()
    assert d == {"synced": [], "drafted": [], "count": 0}


def test_gather_drafts_splits_synced_from_drafted_only(repo):
    inv = repo / "inventory"
    d1 = _shoot(inv, "lot", "synced-item")
    (d1 / "draft.md").write_text(
        '---\ntitle: "Synced Item"\nprice: "10.00"\nmeta:\n'
        '  ebay_offer_id: "OFFER-1"\n---\nlong enough body text for validation.\n')
    d2 = _shoot(inv, "lot", "drafted-item")
    (d2 / "draft.md").write_text(
        '---\ntitle: "Drafted Item"\nprice: "5.00"\nmeta:\n---\nbody\n')

    d = dash.gather_drafts()
    assert d["count"] == 2
    assert [r["title"] for r in d["synced"]] == ["Synced Item"]
    assert [r["title"] for r in d["drafted"]] == ["Drafted Item"]


def test_gather_drafts_excludes_published_rows(repo):
    inv = repo / "inventory"
    d1 = _shoot(inv, "lot", "live-item")
    (d1 / "draft.md").write_text(
        '---\ntitle: "Live Item"\nprice: "10.00"\nmeta:\n'
        '  published_at: "2026-01-01T00:00:00Z"\n---\nbody\n')
    d = dash.gather_drafts()
    assert d["count"] == 0


def test_gather_drafts_sorts_by_price_descending(repo):
    inv = repo / "inventory"
    for name, price in (("cheap", "5.00"), ("pricey", "50.00"), ("mid", "20.00")):
        s = _shoot(inv, "lot", name)
        (s / "draft.md").write_text(f'---\ntitle: "{name}"\nprice: "{price}"\nmeta:\n---\nbody\n')
    d = dash.gather_drafts()
    assert [r["title"] for r in d["drafted"]] == ["pricey", "mid", "cheap"]


def test_gather_drafts_attaches_blocking_issues_for_an_incomplete_draft(repo):
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "incomplete")
    (s / "draft.md").write_text('---\ntitle: ""\nmeta:\n---\n\n')
    d = dash.gather_drafts()
    issues = d["drafted"][0]["blocking_issues"]
    assert any("title" in i for i in issues)


def test_gather_drafts_redacts_voice_check_snippet_but_names_the_field(repo):
    # check_voice() quotes up to 90 chars of the buyer-facing body verbatim
    # (lib/voice_check.py) — the dashboard must keep the "which field" signal
    # without repeating that quoted text (still only structured/aggregated
    # fields, per the PII/business-data policy this repo already follows).
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "camera-confession")
    (s / "draft.md").write_text(
        '---\ntitle: "Public Title"\nprice: "10.00"\nmeta:\n---\n'
        'The back is not shown in this listing, buyer beware of that spot.\n')
    d = dash.gather_drafts()
    issues = d["drafted"][0]["blocking_issues"]
    voice_issues = [i for i in issues if i.startswith("voice (block):")]
    assert voice_issues, "expected the in-hand voice check to flag this body text"
    for i in voice_issues:
        assert "not shown in this listing" not in i
        assert i.startswith("voice (block): body —")


def test_gather_drafts_skips_validation_for_group_drafts(repo):
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "group-item")
    (s / "draft_group.md").write_text('---\ntitle: "Group"\nprice: "10.00"\nmeta:\n---\nbody\n')
    d = dash.gather_drafts()
    assert d["drafted"][0]["blocking_issues"] == []


# ---------------------------------------------------------------------------
# gather_drift
# ---------------------------------------------------------------------------

def test_gather_drift_no_files_present(repo):
    d = dash.gather_drift()
    assert d == {"rows": [], "have_live_snapshot": False, "have_ledger": False, "count": 0}


def test_gather_drift_flags_price_and_title_mismatch(repo):
    _write_live_sheet(repo / "inventory_sheet.csv", [
        {"sku": "abc12345", "title": "Live Title", "listing_id": "111",
         "live": "yes", "price": "24.99"},
    ])
    _write_ledger(repo / "listings_ledger.csv", [
        {"sku": "abc12345", "title": "Ledger Title", "price": "19.99", "status": "PUBLISHED"},
    ])
    d = dash.gather_drift()
    assert d["count"] == 1
    issues = d["rows"][0]["issues"]
    assert any("price" in i for i in issues)
    assert any("title differs" in i for i in issues)


def test_gather_drift_matching_rows_produce_no_issue(repo):
    _write_live_sheet(repo / "inventory_sheet.csv", [
        {"sku": "abc12345", "title": "Same Title", "listing_id": "111",
         "live": "yes", "price": "24.99"},
    ])
    _write_ledger(repo / "listings_ledger.csv", [
        {"sku": "abc12345", "title": "Same Title", "price": "24.99", "status": "PUBLISHED"},
    ])
    d = dash.gather_drift()
    assert d["count"] == 0


def test_gather_drift_flags_live_sku_missing_from_ledger(repo):
    _write_live_sheet(repo / "inventory_sheet.csv", [
        {"sku": "orphan-sku", "title": "No Ledger Row", "listing_id": "222",
         "live": "yes", "price": "9.99"},
    ])
    d = dash.gather_drift()
    assert d["count"] == 1
    assert "no listings_ledger.csv row" in d["rows"][0]["issues"][0]


def test_gather_drift_flags_ledger_published_but_not_live(repo):
    _write_live_sheet(repo / "inventory_sheet.csv", [])
    _write_ledger(repo / "listings_ledger.csv", [
        {"sku": "gone-sku", "title": "Ghost", "price": "9.99", "status": "PUBLISHED"},
    ])
    d = dash.gather_drift()
    assert d["count"] == 1
    assert "not in the current live sheet" in d["rows"][0]["issues"][0]


def test_gather_drift_dedupes_choice_variations_by_listing_id(repo):
    # Two SKUs sharing one listing_id (a CHOICE group) must not double-count.
    _write_live_sheet(repo / "inventory_sheet.csv", [
        {"sku": "var-1", "title": "Choice Group", "listing_id": "333",
         "live": "yes", "price": "10.00"},
        {"sku": "var-2", "title": "Choice Group", "listing_id": "333",
         "live": "yes", "price": "10.00"},
    ])
    d = dash.gather_drift()
    assert d["count"] == 1     # only the first variation is evaluated


def test_gather_drift_ignores_non_live_rows(repo):
    _write_live_sheet(repo / "inventory_sheet.csv", [
        {"sku": "ended-sku", "title": "Ended", "listing_id": "444",
         "live": "no", "price": "1.00"},
    ])
    d = dash.gather_drift()
    assert d["count"] == 0


# ---------------------------------------------------------------------------
# draw() — combined render, including the empty/no-data case
# ---------------------------------------------------------------------------

def test_draw_renders_all_three_sections_on_an_empty_repo(repo):
    d = dash.gather()
    out = dash.draw(d)
    assert "<title>Dashboard</title>" in out
    assert "Backlog by stage" in out
    assert "Drafts awaiting review" in out
    assert "Live listing vs. ledger drift" in out
    assert "No shoot directories found" in out
    assert "Nothing drafted or synced" in out
    assert "not found" in out            # missing-snapshot note in the drift section


def test_draw_renders_populated_data_without_raising(repo):
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "item-1")
    _frame(s)
    (s / "draft.md").write_text(
        '---\ntitle: "Widget"\nprice: "15.00"\nmeta:\n  ebay_inventory_sku: "abc12345"\n---\n'
        'body text long enough.\n')
    _write_ledger(repo / "listings_ledger.csv", [
        {"sku": "abc12345", "title": "Widget", "price": "15.00", "status": "DRAFTED"},
    ])
    _write_live_sheet(repo / "inventory_sheet.csv", [
        {"sku": "abc12345", "title": "Widget Live", "listing_id": "555",
         "live": "yes", "price": "18.00"},
    ])
    d = dash.gather()
    out = dash.draw(d)
    assert "Widget" in out
    assert "abc12345" in out
    assert "555" in out


def test_dashboard_never_writes_any_tracked_file(repo, monkeypatch):
    """Read-only guarantee: gather()/draw() must not create or modify the
    ledger, live sheet, or anything under inventory/."""
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "item-1")
    _frame(s)
    (s / "draft.md").write_text('---\ntitle: "x"\nprice: "1"\nmeta:\n---\nbody\n')
    ledger = repo / "listings_ledger.csv"
    _write_ledger(ledger, [{"sku": "s1", "title": "x", "price": "1", "status": "DRAFTED"}])
    before = ledger.read_bytes()
    before_tree = sorted(p.relative_to(inv).as_posix() for p in inv.rglob("*") if p.is_file())

    dash.draw(dash.gather())

    assert ledger.read_bytes() == before
    after_tree = sorted(p.relative_to(inv).as_posix() for p in inv.rglob("*") if p.is_file())
    assert after_tree == before_tree


# ---------------------------------------------------------------------------
# PII: only structured fields ever reach the HTML, never raw context.txt prose
# ---------------------------------------------------------------------------

def test_context_txt_prose_never_appears_in_the_rendered_page(repo):
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "item-1")
    _frame(s)
    secret_prose = "Bought from Jane Doe at 123 Maple St, paid $575 cash, her phone is 555-0100."
    (inv / "lot" / "context.txt").write_text(secret_prose)
    (s / "draft.md").write_text(
        '---\ntitle: "Public Title"\nprice: "10.00"\nmeta:\n---\n'
        'A normal public-facing listing description body, long enough.\n')
    out = dash.draw(dash.gather())
    assert secret_prose not in out
    assert "Jane Doe" not in out
    assert "555-0100" not in out


def test_draft_body_text_never_appears_only_structured_fields_do(repo):
    inv = repo / "inventory"
    s = _shoot(inv, "lot", "item-1")
    _frame(s)
    (s / "draft.md").write_text(
        '---\ntitle: "Public Title"\nprice: "10.00"\nmeta:\n---\n'
        'This buyer-facing body must never leak into the dashboard rows verbatim '
        'because the dashboard only shows structured fields, not descriptions.\n')
    out = dash.draw(dash.gather())
    assert "Public Title" in out                       # structured field: fine
    assert "buyer-facing body must never leak" not in out


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def test_dashboard_registered_in_cli_commands():
    from lib.cli import COMMANDS
    assert "dashboard" in COMMANDS
    mod, desc = COMMANDS["dashboard"]
    assert mod == "tools.dashboard"
    assert desc


def test_dashboard_cli_help_does_not_error():
    import subprocess
    r = subprocess.run([sys.executable, "-m", "lib.cli", "dashboard", "--help"],
                       cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "--out" in r.stdout
