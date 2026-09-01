#!/usr/bin/env python3
"""Regression tests for tools/sales_report.py's #119 (route B, sell.finances)
wiring — the "before ads & postage" qualifier from #115 must come off only
once every sold row in the window actually carries real ad-fee AND postage
figures, and must say why in one line while it's still up.

Run:  pytest tests/test_sales_report.py
"""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import sales_report as sr  # noqa: E402

_FIELDS = [
    "order_id", "sold_at", "listing_id", "sku", "title", "quantity", "sold_format",
    "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "listed_price", "pct_of_ask", "shoot_dir", "matched_by",
    "ad_fee", "actual_postage",
]


def _sale(order_id, *, gross, fee, net, ad_fee="", actual_postage=""):
    return {
        "order_id": order_id, "sold_at": "2026-07-01T18:00:00Z",
        "listing_id": f"L{order_id}", "sku": f"s{order_id}", "title": f"t{order_id}",
        "quantity": "1", "sold_format": "FIXED_PRICE", "item_price": str(gross),
        "buyer_shipping": "0", "refunded": "0", "gross": str(gross), "ebay_fee": str(fee),
        "net_before_postage": str(net), "listed_price": str(gross), "pct_of_ask": "100",
        "shoot_dir": "", "matched_by": "listing_id",
        "ad_fee": ad_fee, "actual_postage": actual_postage,
    }


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(sr, "REPO", tmp_path)
    monkeypatch.setattr(sr, "REPORTS", reports)
    monkeypatch.setattr(sr, "ADS_JSON", reports / "ebay_ads.json")
    monkeypatch.setattr(sr, "FINANCES_STATUS_JSON", reports / "finances_sync_status.json")
    # gather() -> band_stats() -> price_vs_actual.gather(), a sibling tool
    # with its own REPO-derived sales_ledger.csv path; not #119's concern,
    # but it must not blow up gather() in an empty tmp_path.
    import price_vs_actual as pva
    monkeypatch.setattr(pva, "REPO", tmp_path)
    return tmp_path


def _write_sales(tmp_path, rows):
    sales = tmp_path / "sales_ledger.csv"
    with sales.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_no_finances_columns_at_all_keeps_the_115_qualifier(fixture_repo):
    # Pre-#119 shape: no ad_fee/actual_postage in the CSV whatsoever.
    fields = [f for f in _FIELDS if f not in ("ad_fee", "actual_postage")]
    sales = fixture_repo / "sales_ledger.csv"
    with sales.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        row = _sale("1", gross=100, fee=13, net=87)
        w.writerow({k: v for k, v in row.items() if k in fields})

    d = sr.gather(365)
    assert d["net_after_ads_postage"] is None
    qualifier, why = d["fin_qualifier"]
    assert qualifier is not None
    assert "predates #119" in why


def test_columns_present_but_status_json_missing_says_not_read_yet(fixture_repo):
    # Post-#119 ledger shape (columns present, just blank) but
    # finances_sync_status.json doesn't exist yet — a different situation
    # from the ledger predating #119 entirely, tested above.
    _write_sales(fixture_repo, [_sale("1", gross=100, fee=13, net=87)])

    d = sr.gather(365)
    qualifier, why = d["fin_qualifier"]
    assert qualifier is not None
    assert "predates #119" not in why
    assert "has not read the Finances API yet" in why


def test_full_finances_coverage_drops_the_qualifier(fixture_repo):
    _write_sales(fixture_repo, [
        _sale("1", gross=100, fee=13, net=87, ad_fee="4.00", actual_postage="6.50"),
        _sale("2", gross=50, fee=7, net=43, ad_fee="0.00", actual_postage="5.00"),
    ])
    d = sr.gather(365)
    assert d["fin_covered_n"] == 2
    assert d["net_after_ads_postage"] == pytest.approx((87 + 43) - 4.00 - 11.50)
    qualifier, _why = d["fin_qualifier"]
    assert qualifier is None


def test_partial_finances_coverage_keeps_qualifier_and_says_how_many(fixture_repo):
    _write_sales(fixture_repo, [
        _sale("1", gross=100, fee=13, net=87, ad_fee="4.00", actual_postage="6.50"),
        _sale("2", gross=50, fee=7, net=43),   # not read yet — blank
    ])
    d = sr.gather(365)
    assert d["net_after_ads_postage"] is None
    qualifier, _why = d["fin_qualifier"]
    assert "1 of 2" in qualifier


def test_finances_status_reason_surfaces_in_the_qualifier(fixture_repo):
    _write_sales(fixture_repo, [_sale("1", gross=100, fee=13, net=87)])
    sr.FINANCES_STATUS_JSON.write_text(json.dumps({
        "ok": False,
        "reason": "sell.finances not yet re-consented",
        "other_fee_labels": {},
    }), encoding="utf-8")

    d = sr.gather(365)
    qualifier, why = d["fin_qualifier"]
    assert "sell.finances not yet re-consented" in why
    assert why in qualifier


def test_zero_ad_fee_is_a_real_known_value_not_missing(fixture_repo):
    # ad_fee "0.00" (a real read: no ad spend on this order) must count as
    # KNOWN, distinct from a blank column.
    _write_sales(fixture_repo, [
        _sale("1", gross=100, fee=13, net=87, ad_fee="0.00", actual_postage="6.50"),
    ])
    d = sr.gather(365)
    assert d["fin_covered_n"] == 1
    assert d["net_after_ads_postage"] == pytest.approx(87 - 0.00 - 6.50)


def test_ad_fee_attribution_does_not_look_at_ad_campaign_flag(fixture_repo):
    # gather() must merge ad_fee/actual_postage purely from the CSV columns —
    # nothing here reads or requires an ad-campaign flag on the sale itself
    # (the #119 "ad cost != ad attribution" trap, at the report layer).
    _write_sales(fixture_repo, [
        _sale("1", gross=100, fee=13, net=87, ad_fee="4.00", actual_postage="6.50"),
    ])
    d = sr.gather(365)
    row = d["sales"][0]
    assert row["ad"] is None            # no ad-campaign match at all
    assert row["ad_fee"] == pytest.approx(4.00)   # ad fee still known and counted
