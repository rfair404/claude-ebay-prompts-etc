"""tests/test_tool_lot.py — tools/live/tool_lot.py, one tool-sale lot per run.

The feed frame is a temp JPEG, the claude call is a fake subprocess, the
ledger is a temp file ($EBAYBIZ_LISTINGS_LEDGER) and the sale folder is a
temp dir. Nothing here touches a camera, the network or eBay."""
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
         "disclosures": ["Flashlight not tested."], "size": "medium"}


def frame(path: Path, size=(640, 426)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def fake_claude(reply: str, rc=0, calls=None):
    """A subprocess.run stand-in for the claude CLI: stream-json out."""
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        out = json.dumps({"type": "result", "is_error": False, "result": reply})
        return SimpleNamespace(returncode=rc, stdout=out + "\n", stderr="")
    return run


@pytest.fixture
def sale(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(tmp_path / "ledger.csv"))
    monkeypatch.setattr(T, "claude_bin", lambda: "/bin/claude")
    snap = frame(tmp_path / "d5100_latest.jpg")
    monkeypatch.setattr(T, "feed_frames", lambda: [snap])
    return tmp_path / "tool-sale-2026-10"


def ledger_rows(tmp_path):
    with open(tmp_path / "ledger.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_tool(monkeypatch, sale, *argv, reply=json.dumps(IDENT)):
    real = subprocess.run
    monkeypatch.setattr(T.subprocess, "run", fake_claude(reply))
    try:
        return T.main(["--sale-dir", str(sale), *argv])
    finally:
        monkeypatch.setattr(T.subprocess, "run", real)


# ------------------------------------------------------------ the whole run

def test_run_drafts_records_and_numbers_the_next_lot(sale, tmp_path, monkeypatch, capsys):
    assert run_tool(monkeypatch, sale, "--no-sync") == 0
    assert run_tool(monkeypatch, sale, "--no-sync") == 0
    out = capsys.readouterr().out
    for stage in ("grab", "identify", "draft", "record", "sync", "total"):
        assert stage in out

    d = parse_draft(sale / "tool-lot-2" / "draft.md")
    assert d.get("title") == "TOOL LOT #2 Spring Clamps, LED Flashlight & Aviation Snips"
    assert d.get("store") == "default"
    assert d.get("condition") == "USED_GOOD"
    assert d.get("shipping.free_shipping") is False
    assert d.get("shipping.fulfillment_policy_id") == T.LIVE_SMALL
    assert T.PLACEHOLDER_NOTE in d.get("meta.notes")
    assert "Flashlight not tested." in d.get("condition_description")
    assert "- LED flashlight, black" in d.body
    # the frame came in on its side and was turned upright
    assert Image.open(sale / "tool-lot-2" / T.PHOTO).size == (426, 640)

    rows = ledger_rows(tmp_path)
    assert [r["status"] for r in rows] == ["DRAFTED", "DRAFTED"]
    assert {r["title"] for r in rows} == {
        "TOOL LOT #1 Spring Clamps, LED Flashlight & Aviation Snips",
        "TOOL LOT #2 Spring Clamps, LED Flashlight & Aviation Snips"}
    assert d.get("meta.ebay_inventory_sku") in {r["sku"] for r in rows}
    assert not L.validate_draft_for_sync(d.path) == []  # unpriced: the gates hold it


def test_the_draft_passes_every_sync_check_but_the_price_gate(sale, monkeypatch):
    run_tool(monkeypatch, sale, "--no-sync")
    issues = L.validate_draft_for_sync(sale / "tool-lot-1" / "draft.md")
    assert issues and all(i.startswith("price:") for i in issues), issues


def test_a_hand_made_lot_is_adopted_as_its_number_and_recorded(sale, tmp_path, monkeypatch):
    run_tool(monkeypatch, sale, "--no-sync")                 # makes tool-lot-1
    one = sale / "tool-lot-1" / "draft.md"
    text = one.read_text().replace("    ebay_inventory_sku:", "    x_was_sku:")
    one.write_text("\n".join(ln for ln in text.splitlines() if "ebay_inventory_sku" not in ln) + "\n")
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


def test_failed_identify_still_drafts_and_records_the_lot(sale, tmp_path, monkeypatch, capsys):
    rc = run_tool(monkeypatch, sale, "--no-sync", reply="I can't tell.")
    assert rc == 1
    d = parse_draft(sale / "tool-lot-1" / "draft.md")
    assert d.get("title") == "TOOL LOT #1"
    assert "IDENTIFY FAILED" in d.get("meta.notes")
    assert ledger_rows(tmp_path)[0]["status"] == "DRAFTED"


def test_sync_step_reports_the_price_gate_without_calling_ebay(sale, monkeypatch, capsys):
    calls = []
    real = subprocess.run
    monkeypatch.setattr(T.subprocess, "run", fake_claude(json.dumps(IDENT), calls=calls))
    try:
        assert T.main(["--sale-dir", str(sale)]) == 0
    finally:
        monkeypatch.setattr(T.subprocess, "run", real)
    out = capsys.readouterr().out
    assert "waiting on PRICE" in out
    assert all("list_edit.py" not in " ".join(c[0]) for c in calls)  # only the claude call ran


def test_sync_runs_list_edit_sync_once_the_gates_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "validate_draft_for_sync", lambda p: [])
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return SimpleNamespace(returncode=0, stdout="[OK] created eBay DRAFT offer 123\n", stderr="")
    ok, line = T.sync(tmp_path, run=run)
    assert ok and line.startswith("[OK]")
    assert seen[0][-2:] == ["--sync", str(tmp_path)]
    assert not {"--publish", "--list", "--confirm"} & set(seen[0])


def test_sync_failure_is_one_line(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "validate_draft_for_sync", lambda p: [])
    err = ("Traceback...\n[FAIL] eBay store 'junk' is not configured in config.yaml. "
           "Configured stores: (none configured)\n")
    ok, line = T.sync(tmp_path, run=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr=err))
    assert not ok and "\n" not in line and "not configured" in line


# ------------------------------------------------------------ the parts

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
    calls = []
    got = T.identify(Image.new("RGB", (10, 10)), "m", run=fake_claude(json.dumps(IDENT), calls=calls))
    assert got["lot"] == IDENT["lot"]
    cmd, kw = calls[0]
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--model") + 1] == "m"
    sent = json.loads(kw["input"])["message"]["content"]
    assert sent[0]["type"] == "image" and sent[0]["source"]["media_type"] == "image/jpeg"


def test_large_lots_take_the_large_live_policy(tmp_path):
    s = dict(T.DEFAULTS)
    text = T.build_draft(3, tmp_path / "tool-lot-3", {**IDENT, "size": "heavy"}, s)
    (tmp_path / "draft.md").write_text(text)
    d = parse_draft(tmp_path / "draft.md")
    assert d.get("shipping.fulfillment_policy_id") == T.LIVE_LARGE
    assert d.get("shipping.weight.major_lb") == 20


def test_store_flag_is_saved_for_the_rest_of_the_sale(tmp_path):
    sale = tmp_path / "s"
    assert T.settings(sale, {"store": None})["store"] == "default"
    T.settings(sale, {"store": "junk"})
    assert T.settings(sale, {"store": None})["store"] == "junk"
    assert json.loads((sale / "sale.json").read_text())["rotate"] == -90


# ------------------------------------------------------------ list_edit: per-draft policy

class _Creds:
    has_user = True
    store = "default"
    environment = "sandbox"


def test_a_draft_named_fulfillment_policy_wins_over_the_default(monkeypatch):
    from draft_io import Draft
    monkeypatch.setattr(L, "get_fulfillment_policies", lambda creds=None: [
        {"fulfillmentPolicyId": T.LIVE_SMALL, "name": "LIVE Small",
         "shippingOptions": [{"shippingServices": [{"shippingServiceCode": "USPSGround"}]}]}])
    draft = Draft(path=Path("inventory/x/draft.md"), body="",
                  frontmatter={"title": "TOOL LOT #1 Clamps",
                               "shipping": {"fulfillment_policy_id": T.LIVE_SMALL}})
    chosen, msgs = L._resolve_shipping_policy(draft, {"fulfillment": "default-fid"}, _Creds(), strict=True)
    assert chosen == T.LIVE_SMALL
    assert "draft's own policy" in msgs[0]
