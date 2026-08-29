"""Regression tests for tools/ledger_reconcile.py's SOLD/SHIPPED-vs-eBay precedence.

The rule this module enforces is "the API is truth" — except for one carved-out
case: a ledger row marked SOLD (or, since GH #32, SHIPPED — one step past SOLD)
outranks the Inventory API reporting SYNCED, because the Inventory API has no
concept of a sale (that's the Fulfillment API, reached through
sales_ledger.csv). The first cut of this file got that wrong and would have
flipped 42 real sales back to SYNCED. These tests lock the carve-out down:
SOLD/SHIPPED survive eBay's SYNCED, but not a real relist.

Run:  pytest tests/test_ledger_reconcile.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from tools.ledger_reconcile import FIELDS, _protected, _sold_skus  # noqa: E402


def _row(status="SOLD"):
    return {"sku": "abc123", "status": status}


def _truth(status):
    return {"sku": "abc123", "status": status}


# --------------------------------------------------------------------------
# _protected — the field must be "status", and only "status"
# --------------------------------------------------------------------------
def test_non_status_fields_are_never_protected():
    # Price/offer_id/etc. always defer to eBay, even on a SOLD row — the
    # carve-out is narrowly about status, not "SOLD rows are untouchable".
    assert _protected(_row(), _truth("SYNCED"), "price", sold={"abc123"}) is False
    assert _protected(_row(), _truth("SYNCED"), "offer_id", sold={"abc123"}) is False


def test_non_sold_rows_are_never_protected():
    for status in ("DRAFTED", "SYNCED", "PUBLISHED", "ENDED", ""):
        assert _protected(_row(status), _truth("SYNCED"), "status", sold=set()) is False


# --------------------------------------------------------------------------
# the actual carve-out: SOLD beats eBay's SYNCED
# --------------------------------------------------------------------------
def test_sold_row_survives_ebay_reporting_synced():
    # This is the 42-sales bug: eBay's Inventory API has no sale concept, so
    # an unpublished-but-sold offer reads SYNCED there. That must not
    # overwrite a real SOLD row.
    assert _protected(_row("SOLD"), _truth("SYNCED"), "status", sold={"abc123"}) is True


def test_sold_row_survives_ebay_reporting_ended():
    assert _protected(_row("SOLD"), _truth("ENDED"), "status", sold={"abc123"}) is True


def test_sold_row_survives_even_when_uncorroborated_by_an_order():
    # Deliberately not gated on the `sold` set: an offline sale (mall case,
    # direct buyer) the Fulfillment API never saw is more likely than a
    # mistake, and silently clearing SOLD would resurrect it as listable
    # stock. main() surfaces this case under REVIEW instead of correcting it.
    assert _protected(_row("SOLD"), _truth("SYNCED"), "status", sold=set()) is True


def test_a_real_relist_outranks_sold():
    # eBay showing the SKU ACTIVE again (a genuine relist) is the one thing
    # that beats a local SOLD row.
    assert _protected(_row("SOLD"), _truth("PUBLISHED"), "status", sold={"abc123"}) is False


# --------------------------------------------------------------------------
# GH #32: SHIPPED gets the same carve-out as SOLD (it's one step past it,
# not a different track) — tools/pick_list.py --record-tracking advances a
# row to SHIPPED, and eBay's own status inference (_status_for) never
# returns SOLD or SHIPPED, only PUBLISHED/SYNCED/ENDED.
# --------------------------------------------------------------------------
def test_shipped_row_survives_ebay_reporting_synced():
    assert _protected(_row("SHIPPED"), _truth("SYNCED"), "status", sold=set()) is True


def test_shipped_row_only_protects_the_status_field():
    assert _protected(_row("SHIPPED"), _truth("SYNCED"), "price", sold=set()) is False


def test_a_real_relist_outranks_shipped():
    assert _protected(_row("SHIPPED"), _truth("PUBLISHED"), "status", sold=set()) is False


def test_fields_list_carries_shipped_at_column():
    # advance_ledger_for_order writes status=SHIPPED via lib/list_edit.py's
    # upsert_listing, which stamps a shipped_at timestamp into that column.
    # If FIELDS doesn't also carry it, `--apply` rewrites the CSV and
    # silently drops the column.
    assert "shipped_at" in FIELDS


# --------------------------------------------------------------------------
# _sold_skus — reads sales_ledger.csv, tolerates it being absent
# --------------------------------------------------------------------------
def test_sold_skus_reads_the_sales_ledger(tmp_path, monkeypatch):
    import tools.ledger_reconcile as LR
    sales = tmp_path / "sales_ledger.csv"
    sales.write_text("order_id,sku\n1-000,sku-a\n2-000,sku-b\n", encoding="utf-8")
    monkeypatch.setattr(LR, "SALES", sales)
    assert LR._sold_skus() == {"sku-a", "sku-b"}


def test_sold_skus_empty_when_no_sales_ledger(tmp_path, monkeypatch):
    import tools.ledger_reconcile as LR
    monkeypatch.setattr(LR, "SALES", tmp_path / "does_not_exist.csv")
    assert LR._sold_skus() == set()
