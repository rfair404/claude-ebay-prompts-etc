#!/usr/bin/env python3
"""Draft scanners ignore inventory/_backups/ and <shoot>/.history/.

A bulk rewrite snapshots the tree to inventory/_backups/<run>/ first. A
draft.md copied there carries the same SKU and listing id as the real one,
so a scanner that walks into it sees two drafts for one item and can match
a sale (or a rebuild, or a report row) to the backup folder.

Run:  pytest tests/test_draft_backups.py
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import draft_io                                              # noqa: E402
import draft_rebuild as R                                    # noqa: E402
import report                                                # noqa: E402
import sync_actuals as SA                                    # noqa: E402

DRAFT = """---
title: "Brass Key"
price: "40.00"
ebay_inventory_sku: "ab12cd34"
ebay_listing_id: "880000000001"
---
Body.
"""


@pytest.fixture
def inv(tmp_path, monkeypatch):
    """inventory/ with one real item and two backup copies of it. The
    `_backups` copy sorts first, so a scanner that keeps the first match
    per SKU picks the backup unless it skips it."""
    inv = tmp_path / "inventory"
    for rel in ("_backups/run-1/key", "shoot/key", "shoot/.history"):
        (inv / rel).mkdir(parents=True)
    (inv / "_backups/run-1/key/draft.md").write_text(DRAFT, encoding="utf-8")
    (inv / "_backups/run-1/key/RECOVERED.md").write_text(
        "- sku: ab12cd34\n", encoding="utf-8")
    (inv / "shoot/.history/draft.md").write_text(DRAFT, encoding="utf-8")
    (inv / "shoot/key/draft.md").write_text(DRAFT, encoding="utf-8")
    for mod in (SA, report):
        monkeypatch.setattr(mod, "REPO", tmp_path)
        monkeypatch.setattr(mod, "INVENTORY", inv)
    monkeypatch.setattr(R, "REPO", tmp_path)
    return inv


def test_iter_drafts_skips_backups_and_history(inv):
    assert draft_io.iter_drafts(inv) == [inv / "shoot/key/draft.md"]


def test_iter_drafts_rooted_inside_a_backup_still_finds_it(inv):
    run = inv / "_backups/run-1"
    assert draft_io.iter_drafts(run) == [run / "key/draft.md"]


def test_sync_actuals_scan_drafts_ignores_backup(inv):
    assert [d["dir"] for d in SA.scan_drafts()] == ["inventory/shoot/key"]


def test_sync_actuals_scan_recovered_ignores_backup(inv):
    assert SA.scan_recovered() == []


def test_draft_rebuild_existing_drafts_ignores_backup(inv):
    assert R._existing_drafts() == {"ab12cd34": inv / "shoot/key/draft.md"}


def test_report_scan_drafts_ignores_backup(inv):
    assert [r["path"] for r in report._scan_drafts()] == [
        "inventory/shoot/key/draft.md"]
