"""tests/test_tool_lot.py — tools/live/tool_lot.py, one tool-sale lot per run.

The feed frame is a temp JPEG, the claude call and PREP are fake subprocesses,
`list_edit.py --sync` is faked, and `--record` / `--validate` run for real on a
temp ledger ($EBAYBIZ_LISTINGS_LEDGER) and a temp sale folder. Nothing here
touches a camera, the network or eBay."""
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "live"))
sys.path.insert(0, str(ROOT / "lib"))

import list_edit as L  # noqa: E402
import tool_lot as T  # noqa: E402
from draft_io import parse_draft  # noqa: E402

IDENT = {"lot": "Spring Clamps, LED Flashlight & Aviation Snips",
         "items": ["2 spring clamps, black with orange tips", "LED flashlight, black",
                   "Aviation snips, yellow and black handles"],
         "type": "Tool Lot", "brand": "", "color": "Black", "condition": "USED_GOOD",
         "condition_notes": "Used shop tools with normal wear.",
         "disclosures": ["Flashlight not tested."], "size": "medium",
         "all_visible": True, "in_focus": True, "well_lit": True, "rig_in_frame": True}

REAL_RUN = subprocess.run


def frame(path: Path, size=(640, 426)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def fake_run(reply: str, calls: list):
    """subprocess.run for the tool: claude answers `reply`, PREP and
    `list_edit.py --sync` succeed without doing anything, and the other
    list_edit.py calls (--record, --validate) run for real."""
    def run(cmd, **kw):
        calls.append(cmd)
        if "lib.photo_prep.prep" in cmd:
            return ok()
        if any(str(c).endswith("list_edit.py") for c in cmd):
            if "--sync" in cmd:
                return ok("[OK] created eBay DRAFT offer 123 (UNPUBLISHED)\n")
            return REAL_RUN(cmd, **kw)
        out = json.dumps({"type": "result", "is_error": False, "result": reply})
        return ok(out + "\n")
    return run


@pytest.fixture
def sale(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(tmp_path / "ledger.csv"))
    monkeypatch.setattr(T, "claude_bin", lambda: "/bin/claude")
    monkeypatch.setattr(T, "main_dir", lambda start=ROOT: ROOT)  # this checkout's list_edit.py
    snap = frame(tmp_path / "d5100_latest.jpg")
    monkeypatch.setattr(T, "feed_frames", lambda: [snap])
    return tmp_path / "tool-sale-2026-10"


def ledger_rows(tmp_path):
    with open(tmp_path / "ledger.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_tool(monkeypatch, sale, *argv, reply=None, calls=None):
    calls = [] if calls is None else calls
    reply = json.dumps(IDENT) if reply is None else reply
    monkeypatch.setattr(T.subprocess, "run", fake_run(reply, calls))
    try:
        return T.main(["--sale-dir", str(sale), *argv])
    finally:
        monkeypatch.setattr(T.subprocess, "run", REAL_RUN)


def prep_calls(calls):
    return [c[c.index("lib.photo_prep.prep") + 2:] for c in calls if "lib.photo_prep.prep" in c]


# ------------------------------------------------------------ the whole run

def test_run_drafts_records_and_numbers_the_next_lot(sale, tmp_path, monkeypatch, capsys):
    assert run_tool(monkeypatch, sale, "--no-sync") == 0
    assert run_tool(monkeypatch, sale, "--no-sync") == 0
    out = capsys.readouterr().out
    for stage in ("grab", "identify", "draft", "record", "prep", "sync", "total"):
        assert stage in out

    lot = sale / "tool-lot-2"
    d = parse_draft(lot / "draft.md")
    assert d.get("title") == "TOOL LOT #2 Spring Clamps, LED Flashlight & Aviation Snips"
    assert d.get("store") == "default"
    assert d.get("condition") == "USED_GOOD"  # list_edit preflight maps it to the category at sync
    assert d.get("price") == "1.00"
    assert d.get("shipping.free_shipping") is False
    assert d.get("shipping.fulfillment_policy_id") == T.LIVE_SMALL
    assert "Flashlight not tested." in d.get("condition_description")
    assert "- LED flashlight, black" in d.body
    assert (lot / "price.txt").read_text().startswith(
        'OPERATOR PRICE: Russell: "$1 start" for tool-sale Live lots (2026-10-25)')
    # the source frame is kept as shot, at the lot's root, for PREP
    assert Image.open(lot / T.PHOTO).size == (640, 426)
    assert not (lot / "listing").exists()

    rows = ledger_rows(tmp_path)
    assert [r["status"] for r in rows] == ["DRAFTED", "DRAFTED"]
    assert {r["price"] for r in rows} == {"1.00"}
    assert d.get("meta.ebay_inventory_sku") in {r["sku"] for r in rows}


def test_the_draft_passes_every_sync_check(sale, monkeypatch):
    """Price gate included: price.txt carries Russell's OPERATOR PRICE line."""
    run_tool(monkeypatch, sale, "--no-sync")
    assert L.validate_draft_for_sync(sale / "tool-lot-1" / "draft.md") == []


def test_a_clean_lot_is_prepped_approved_and_synced(sale, monkeypatch, capsys):
    calls = []
    assert run_tool(monkeypatch, sale, calls=calls) == 0
    steps = prep_calls(calls)
    assert steps[:2] == [["--auto"], ["--set-rotate", "D5_0001.jpg=90"]]
    assert ["--crop", "D5_0001.jpg=off"] in steps and ["--pick", "crisp"] in steps
    # the stages are signed off, listing/ is rendered again, then the stamp
    stage_at = steps.index(["--approve-stage", "color"])
    assert ["--apply"] in steps[stage_at:]
    assert steps[-1] == ["--approve"]
    sync = [c for c in calls if "--sync" in c]
    assert len(sync) == 1 and not {"--publish", "--list", "--confirm"} & set(sync[0])
    assert "[OK] TOOL LOT #1: eBay draft (unpublished offer) at $1.00" in capsys.readouterr().out


def test_a_doubtful_frame_is_held_for_the_art_director(sale, monkeypatch, capsys):
    calls = []
    rc = run_tool(monkeypatch, sale, reply=json.dumps({**IDENT, "in_focus": False}), calls=calls)
    assert rc == 1
    flat = [a for step in prep_calls(calls) for a in step]
    assert not [a for a in flat if a.startswith("--approve")]
    assert not [c for c in calls if "--sync" in c]
    notes = parse_draft(sale / "tool-lot-1" / "draft.md").get("meta.notes")
    assert "PREP HOLD for the art director: the frame is out of focus" in notes
    assert "HELD for the art director" in capsys.readouterr().out


def test_a_hand_made_lot_is_adopted_as_its_number_and_recorded(sale, tmp_path, monkeypatch):
    run_tool(monkeypatch, sale, "--no-sync")                 # makes tool-lot-1
    one = sale / "tool-lot-1" / "draft.md"
    one.write_text("\n".join(ln for ln in one.read_text().splitlines()
                             if "ebay_inventory_sku" not in ln) + "\n")
    os.remove(tmp_path / "ledger.csv")                        # as if made by hand, never recorded

    run_tool(monkeypatch, sale, "--no-sync")
    assert (sale / "tool-lot-2").is_dir() and not (sale / "tool-lot-3").exists()
    titles = sorted(r["title"] for r in ledger_rows(tmp_path))
    assert titles[0].startswith("TOOL LOT #1") and titles[1].startswith("TOOL LOT #2")
    assert parse_draft(one).get("meta.ebay_inventory_sku")


def test_stale_frame_is_refused_and_makes_no_lot(sale, tmp_path, monkeypatch, capsys):
    snap = tmp_path / "d5100_latest.jpg"
    old = time.time() - 30
    os.utime(snap, (old, old))
    assert run_tool(monkeypatch, sale) == 2
    assert "D5 feed is down" in capsys.readouterr().err
    assert T.lot_numbers(sale) == []


def test_failed_identify_still_drafts_records_and_holds_the_lot(sale, tmp_path, monkeypatch):
    calls = []
    assert run_tool(monkeypatch, sale, reply="I can't tell.", calls=calls) == 1
    d = parse_draft(sale / "tool-lot-1" / "draft.md")
    assert d.get("title") == "TOOL LOT #1"
    assert "IDENTIFY FAILED" in d.get("meta.notes")
    assert ledger_rows(tmp_path)[0]["status"] == "DRAFTED"
    assert not [c for c in calls if "--sync" in c]


# ------------------------------------------------------------ the parts

def test_sync_runs_in_the_main_checkout_and_never_publishes(tmp_path, monkeypatch):
    main = tmp_path / "main"
    monkeypatch.setattr(T, "main_dir", lambda start=ROOT: main)
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return ok("[OK] created eBay DRAFT offer 123\n")
    synced, line = T.sync(tmp_path, approved=True, run=run)
    assert synced and line.startswith("[OK]")
    assert [c[-2] for c, _ in seen] == ["--validate", "--sync"]
    for cmd, kw in seen:
        assert cmd[1] == str(main / "lib" / "list_edit.py") and kw["cwd"] == main
        assert not {"--publish", "--list", "--confirm"} & set(cmd)


def test_sync_waits_for_approved_photos(tmp_path):
    seen = []
    synced, line = T.sync(tmp_path, approved=False, run=lambda cmd, **kw: seen.append(cmd) or ok())
    assert not synced and "held for the art director" in line
    assert [c[-2] for c in seen] == ["--validate"]


def test_sync_failure_is_one_line(tmp_path):
    err = ("Traceback...\n[X] EbayAuthError: Sync needs user-context OAuth. "
           "Run `python list_edit.py --setup-check`.\n")

    def run(cmd, **kw):
        if "--sync" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr=err)
        return ok("[OK] sync-ready.\n")
    synced, line = T.sync(tmp_path, approved=True, run=run)
    assert not synced and "\n" not in line and "user-context OAuth" in line


def test_validate_blocker_is_named(tmp_path):
    out = "[X] draft.md has 2 issue(s):\n  - title: required, empty\n  - photos: none listed\n"
    synced, line = T.sync(tmp_path, approved=True,
                          run=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=out, stderr=""))
    assert not synced and "title: required, empty (+1 more)" in line


def test_record_runs_in_the_main_checkout(tmp_path, monkeypatch):
    main = tmp_path / "main"
    monkeypatch.setattr(T, "main_dir", lambda start=ROOT: main)
    lot = tmp_path / "sale" / "tool-lot-1"
    lot.mkdir(parents=True)
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return ok("[OK] recorded DRAFTED — sku 0123abcd\n")
    assert T.record(lot.parent, lot, run=run) == ("0123abcd", [])
    cmd, kw = seen[0]
    assert cmd[1] == str(main / "lib" / "list_edit.py") and kw["cwd"] == main
    assert cmd[-2:] == ["--record", str(lot)]


def test_main_dir_from_a_worktree_is_the_main_checkout(tmp_path):
    main, wt = tmp_path / "repo", tmp_path / "wt"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(main)]
    REAL_RUN(["git", "init", "-q", str(main)], check=True)
    REAL_RUN([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    REAL_RUN([*git, "worktree", "add", "-q", str(wt)], check=True)
    assert T.main_dir(wt) == main.resolve()
    assert T.main_dir(main) == main.resolve()


def test_main_python_prefers_the_main_checkouts_venv(tmp_path):
    assert T.main_python(tmp_path) == sys.executable
    venv = tmp_path / ".venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.write_text("")
    assert T.main_python(tmp_path) == str(venv)


def test_prep_stops_on_a_failed_step(tmp_path):
    def run(cmd, **kw):
        return SimpleNamespace(returncode=1 if "--apply" in cmd else 0, stdout="",
                               stderr="[X] segmentation failed\n")
    approved, line = T.prep(tmp_path, 90, approve=True, run=run)
    assert not approved and "--apply failed" in line


def test_hold_reasons_need_every_check_true():
    assert T.hold_reasons(IDENT) == []
    assert T.hold_reasons({**IDENT, "rig_in_frame": False}) == [T.CHECKS["rig_in_frame"]]
    assert T.hold_reasons({k: v for k, v in IDENT.items() if k != "well_lit"}) == [T.CHECKS["well_lit"]]
    assert T.hold_reasons(None)


def test_upright_turns_the_frame_clockwise():
    im = Image.new("RGB", (4, 2), "black")
    im.putpixel((0, 0), (255, 0, 0))                          # top-left
    up = T.upright(im, 90)
    assert up.size == (2, 4) and up.getpixel((1, 0)) == (255, 0, 0)  # now top-right
    assert T.upright(im, 0) is im


def test_two_claims_at_once_get_two_numbers(tmp_path):
    sale = tmp_path / "s"
    (sale / "tool-lot-1").mkdir(parents=True)
    a, b = T.claim_lot(sale), T.claim_lot(sale)
    assert (a[0], b[0]) == (2, 3)


def test_claim_skips_a_number_taken_between_scan_and_mkdir(tmp_path, monkeypatch):
    sale = tmp_path / "s"
    (sale / "tool-lot-1").mkdir(parents=True)
    monkeypatch.setattr(T, "lot_numbers", lambda s: [])     # a stale scan: says nothing exists
    assert T.claim_lot(sale)[0] == 2


def test_title_is_cut_at_a_word_within_80():
    t = T.title_for(12, "Spring Clamps, LED Flashlight, Pick Tool, Aviation Snips, Pliers, Hex Keys and Wrenches")
    assert len(t) <= 80 and t.startswith("TOOL LOT #12 ") and not t.endswith(",")


def test_parse_identify_reads_a_fenced_reply():
    assert T.parse_identify('```json\n{"lot": "Clamps"}\n```')["lot"] == "Clamps"
    with pytest.raises(T.LotError):
        T.parse_identify('{"lot": ""}')


def test_identify_sends_the_frame_inline_with_no_tools(monkeypatch):
    monkeypatch.setattr(T, "claude_bin", lambda: "/bin/claude")
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return ok(json.dumps({"type": "result", "is_error": False, "result": json.dumps(IDENT)}) + "\n")
    got = T.identify(Image.new("RGB", (10, 10)), "m", run=run)
    assert got["lot"] == IDENT["lot"]
    cmd, kw = seen[0]
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--model") + 1] == "m"
    sent = json.loads(kw["input"])["message"]["content"]
    assert sent[0]["type"] == "image" and sent[0]["source"]["media_type"] == "image/jpeg"


def test_large_lots_take_the_large_live_policy(tmp_path):
    text = T.build_draft(3, tmp_path / "tool-lot-3", {**IDENT, "size": "heavy"}, dict(T.DEFAULTS))
    (tmp_path / "draft.md").write_text(text)
    d = parse_draft(tmp_path / "draft.md")
    assert d.get("shipping.fulfillment_policy_id") == T.LIVE_LARGE
    assert d.get("shipping.weight.major_lb") == 20


def test_for_parts_stays_for_parts(tmp_path):
    text = T.build_draft(4, tmp_path / "tool-lot-4",
                         {**IDENT, "condition": "FOR_PARTS_OR_NOT_WORKING"}, dict(T.DEFAULTS))
    (tmp_path / "draft.md").write_text(text)
    assert parse_draft(tmp_path / "draft.md").get("condition") == "FOR_PARTS_OR_NOT_WORKING"


def test_settings_are_saved_for_the_rest_of_the_sale(tmp_path):
    sale = tmp_path / "s"
    assert T.settings(sale, {"store": None})["store"] == "default"
    T.settings(sale, {"store": "junk", "start_price": "2"})
    s = T.settings(sale, {"store": None})
    assert s["store"] == "junk" and s["start_price"] == "2.00" and s["rotate"] == 90
    assert T.operator_price(s).startswith('OPERATOR PRICE: Russell: "$2 start"')



def test_a_failed_sync_is_a_failed_run(sale, monkeypatch, capsys):
    calls = []
    inner = fake_run(json.dumps(IDENT), calls)

    def run(cmd, **kw):
        if "--sync" in cmd:
            calls.append(cmd)
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="[X] EbayAuthError: Sync needs user-context OAuth.\n")
        return inner(cmd, **kw)
    monkeypatch.setattr(T.subprocess, "run", run)
    try:
        rc = T.main(["--sale-dir", str(sale)])
    finally:
        monkeypatch.setattr(T.subprocess, "run", REAL_RUN)
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL] TOOL LOT #1: sync failed, recorded" in out and "[OK]" not in out


def test_model_flag_reaches_the_claude_call_and_is_saved(sale, monkeypatch):
    calls = []
    run_tool(monkeypatch, sale, "--no-sync", "--model", "claude-opus-5-5", calls=calls)
    claude = next(c for c in calls if "-p" in c)
    assert claude[claude.index("--model") + 1] == "claude-opus-5-5"
    assert json.loads((sale / "sale.json").read_text())["model"] == "claude-opus-5-5"
