"""ledger_reconcile.compute_drift — the money-safety rules, off the network.

`compute_drift` holds the actual reconciliation decisions (drift, SOLD
protection, blanked-field retention); this locks each one down with synthetic
ledger/truth data so a future edit can't silently change what gets
overwritten. No pytest fixtures — runs under tests/run_all.py too.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "lib"))

import ledger_reconcile as lr                                     # noqa: E402


def _row(**kw) -> dict:
    row = {"sku": "", "status": "", "title": "", "price": "", "offer_id": "",
           "listing_id": "", "url": ""}
    row.update(kw)
    return row


def _truth(**kw) -> dict:
    t = {"sku": "", "status": "", "price": "", "offer_id": "",
         "listing_id": "", "url": ""}
    t.update(kw)
    return t


def test_no_drift_when_ledger_matches_ebay():
    by_sku = {"a": _row(sku="a", status="PUBLISHED", price="10.00")}
    truth = {"a": _truth(sku="a", status="PUBLISHED", price="10.0")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["drift"] == []


def test_price_formatting_difference_is_not_drift():
    # 99.0 and 99.00 are the same price; only a real numeric difference is drift.
    by_sku = {"a": _row(sku="a", status="PUBLISHED", price="99.0")}
    truth = {"a": _truth(sku="a", status="PUBLISHED", price="99.00")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["drift"] == []


def test_real_price_change_is_drift():
    by_sku = {"a": _row(sku="a", status="PUBLISHED", price="99.00")}
    truth = {"a": _truth(sku="a", status="PUBLISHED", price="85.00")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["drift"] == [("a", "price", "99.00", "85.00")]


def test_status_drift_reported():
    by_sku = {"a": _row(sku="a", status="SYNCED")}
    truth = {"a": _truth(sku="a", status="PUBLISHED")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert ("a", "status", "SYNCED", "PUBLISHED") in r["drift"]


def test_sold_with_order_is_protected_over_synced():
    # This is the 42-sale regression: a SOLD row backed by a real order must
    # never flip to SYNCED just because the Inventory API doesn't know about sales.
    by_sku = {"a": _row(sku="a", status="SOLD", price="50.00")}
    truth = {"a": _truth(sku="a", status="SYNCED", price="50.00")}
    r = lr.compute_drift(by_sku, truth, sold={"a"})
    assert r["drift"] == []
    assert r["protected"] == 1
    assert r["unbacked"] == []


def test_sold_without_order_is_kept_but_flagged_for_review():
    by_sku = {"a": _row(sku="a", status="SOLD", price="50.00")}
    truth = {"a": _truth(sku="a", status="SYNCED", price="50.00")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["drift"] == []          # still not overwritten
    assert r["protected"] == 0       # not corroborated, so not counted as protected
    assert r["unbacked"] == ["a"]


def test_relist_active_outranks_sold():
    # A genuine relist beats even a corroborated SOLD.
    by_sku = {"a": _row(sku="a", status="SOLD", price="50.00")}
    truth = {"a": _truth(sku="a", status="PUBLISHED", price="55.00")}
    r = lr.compute_drift(by_sku, truth, sold={"a"})
    assert ("a", "status", "SOLD", "PUBLISHED") in r["drift"]


def test_ebay_blank_field_keeps_local_value():
    by_sku = {"a": _row(sku="a", status="ENDED", price="20.00")}
    truth = {"a": _truth(sku="a", status="ENDED", price="")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["blanked"] == [("a", "price", "20.00")]
    assert r["drift"] == []


def test_live_sku_with_no_ledger_row_is_missing():
    by_sku = {}
    truth = {"a": _truth(sku="a", status="PUBLISHED", price="10.00")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["missing"] == [truth["a"]]


def test_ledger_row_ebay_does_not_know_is_orphan():
    by_sku = {"a": _row(sku="a", status="DRAFTED")}
    truth = {}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["orphan"] == ["a"]


def test_drafted_never_synced_row_is_not_never_listed():
    # An inventory item with no offer at all is a legitimate unsynced draft,
    # not a "no offer" anomaly.
    by_sku = {"a": _row(sku="a", status="DRAFTED")}
    truth = {"a": None}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["never_listed"] == []


def test_inventory_item_with_no_offer_and_nondraft_status_is_flagged():
    by_sku = {"a": _row(sku="a", status="SYNCED")}
    truth = {"a": None}
    r = lr.compute_drift(by_sku, truth, sold=set())
    assert r["never_listed"] == [("a", "SYNCED")]


def test_write_report_round_trips_json():
    by_sku = {"a": _row(sku="a", status="SYNCED")}
    truth = {"a": _truth(sku="a", status="PUBLISHED")}
    r = lr.compute_drift(by_sku, truth, sold=set())
    out = REPO / "tests" / "_scratch_ledger_reconcile_report.json"
    try:
        lr.write_report(out, by_sku, r)
        import json
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["drift"] == [{"sku": "a", "field": "status",
                                 "ledger": "SYNCED", "ebay": "PUBLISHED"}]
    finally:
        out.unlink(missing_ok=True)
