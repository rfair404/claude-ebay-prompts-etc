#!/usr/bin/env python3
"""tools/ledger_reconcile.py — _protected() guards SOLD and SHIPPED rows.

_protected is a pure function (row/truth/field/sold -> bool); no HTTP, no
filesystem. This exists specifically to lock down GH #32's fix: a row
advanced to SHIPPED by tools/pick_list.py --record-tracking must survive a
routine `ledger_reconcile.py --apply` run the same way a SOLD row already
does, since eBay's own status inference (_status_for) never returns SOLD or
SHIPPED — only the Inventory API's PUBLISHED/SYNCED/ENDED.

Run:  python tests/test_ledger_reconcile.py
  or: pytest tests/test_ledger_reconcile.py
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

ledger_reconcile = pytest.importorskip(
    "ledger_reconcile", reason="ledger_reconcile imports ebay_client (config module)")


def test_shipped_row_protected_when_ebay_shows_synced():
    row = {"status": "SHIPPED"}
    truth = {"status": "SYNCED"}
    assert ledger_reconcile._protected(row, truth, "status", set()) is True


def test_sold_row_still_protected_same_as_before():
    row = {"status": "SOLD"}
    truth = {"status": "SYNCED"}
    assert ledger_reconcile._protected(row, truth, "status", set()) is True


def test_shipped_row_not_protected_when_ebay_shows_published_relist():
    # A genuine relist (ACTIVE again) outranks SHIPPED the same way it already
    # outranks SOLD — the item is back on eBay, so the local post-sale status
    # is stale.
    row = {"status": "SHIPPED"}
    truth = {"status": "PUBLISHED"}
    assert ledger_reconcile._protected(row, truth, "status", set()) is False


def test_shipped_only_protects_the_status_field():
    row = {"status": "SHIPPED"}
    truth = {"status": "SYNCED"}
    assert ledger_reconcile._protected(row, truth, "price", set()) is False


def test_ordinary_status_unprotected():
    row = {"status": "SYNCED"}
    truth = {"status": "PUBLISHED"}
    assert ledger_reconcile._protected(row, truth, "status", set()) is False


def test_fields_list_carries_shipped_at_column():
    # tools/pick_list.py's advance_ledger_for_order writes status=SHIPPED via
    # lib/list_edit.py's upsert_listing, which stamps a shipped_at timestamp
    # into that column. If ledger_reconcile.FIELDS doesn't also carry it,
    # `--apply` rewrites the CSV and silently drops the column.
    assert "shipped_at" in ledger_reconcile.FIELDS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
