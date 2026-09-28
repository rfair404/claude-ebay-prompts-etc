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
    # Every per-store file (sales ledger, live sheet, ads JSON, finances
    # status, dashboard) resolves through stores.paths() at call time (#156).
    _isolate(monkeypatch, tmp_path)
    # gather() -> band_stats() -> price_vs_actual.gather(), a sibling tool
    # that reads price.txt under its own REPO; not #119's concern, but it
    # must not blow up gather() in an empty tmp_path.
    import price_vs_actual as pva
    monkeypatch.setattr(pva, "REPO", tmp_path)
    return tmp_path


THREE_STORES = {"ebay": {"stores": {"junk": {}, "outlet": {}}}}


def _isolate(monkeypatch, root, cfg=None):
    import stores
    monkeypatch.setattr(stores, "REPO", root)
    monkeypatch.setattr(stores, "load_config", lambda: cfg or {})
    for var in ("EBAYBIZ_STORE", "EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG"):
        monkeypatch.delenv(var, raising=False)


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
    (fixture_repo / "reports" / "finances_sync_status.json").write_text(json.dumps({
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


def test_ad_fee_only_coverage_not_gated_on_postage(fixture_repo):
    # ad_fee known, postage NOT known yet — the promoted-panel ad-spend
    # figure must still show (the panel is about ads, not postage), even
    # though the combined "net after ads & postage" headline correctly
    # stays hidden until both are known.
    _write_sales(fixture_repo, [
        _sale("1", gross=100, fee=13, net=87, ad_fee="4.00", actual_postage=""),
    ])
    d = sr.gather(365)
    assert d["fin_covered_n"] == 0             # combined (both-known) unaffected
    assert d["net_after_ads_postage"] is None
    assert d["fin_ad_fee_only_total"] == pytest.approx(4.00)
    assert d["fin_ad_covered_n"] == 1


# --------------------------------------------------------------------------
# #119 — the qualifier (sourced from finances_sync_status.json, which this
# module does not control the contents of) must be HTML-escaped before it
# reaches the rendered page, the same way _stat() already escapes its own
# subtitle text.
# --------------------------------------------------------------------------
def test_draw_escapes_the_qualifier_reason_in_the_promoted_panel_note(fixture_repo):
    _write_sales(fixture_repo, [_sale("1", gross=100, fee=13, net=87)])  # no #119 coverage
    (fixture_repo / "reports" / "finances_sync_status.json").write_text(json.dumps({
        "ok": False,
        "reason": '<script>alert(1)</script> & "quoted"',
        "other_fee_labels": {},
    }), encoding="utf-8")

    d = sr.gather(365)
    html_out = sr.draw(d)
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_draw_escapes_the_qualifier_in_the_headline_net_stat_too(fixture_repo):
    _write_sales(fixture_repo, [_sale("1", gross=100, fee=13, net=87)])
    (fixture_repo / "reports" / "finances_sync_status.json").write_text(json.dumps({
        "ok": False,
        "reason": "<b>unsafe</b>",
        "other_fee_labels": {},
    }), encoding="utf-8")

    d = sr.gather(365)
    html_out = sr.draw(d)
    assert "<b>unsafe</b>" not in html_out
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in html_out


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


# --------------------------------------------------------------------------
# #156 — one dashboard per store; the default store's page is unchanged.
# --------------------------------------------------------------------------
def _write_store_sales(root, store, rows):
    name = "sales_ledger.csv" if store == "default" else f"sales_ledger-{store}.csv"
    with (root / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_default_store_dashboard_keeps_its_name_and_header(fixture_repo):
    _write_sales(fixture_repo, [_sale("1", gross=100, fee=13, net=87)])
    assert sr.main(["--no-sync"]) == 0
    page = (fixture_repo / "reports" / "sales_dashboard.html").read_text(encoding="utf-8")
    assert "<title>Sales Dashboard</title>" in page
    assert '<p class="eyebrow">ebaybiz · sales</p>' in page


def test_a_named_store_dashboard_reads_only_its_own_ledger(fixture_repo, monkeypatch):
    _isolate(monkeypatch, fixture_repo, THREE_STORES)
    _write_store_sales(fixture_repo, "default", [_sale("1", gross=100, fee=13, net=87)])
    _write_store_sales(fixture_repo, "junk", [_sale("2", gross=9, fee=1, net=8)])
    d = sr.gather(365, "junk")
    assert d["count"] == 1 and d["gross"] == pytest.approx(9.0)
    assert d["store"] == "junk"
    page = sr.draw(d)
    assert "<title>Sales Dashboard [junk]</title>" in page and "junk store" in page


def test_all_stores_draws_one_dashboard_per_store_three_stores(fixture_repo, monkeypatch):
    _isolate(monkeypatch, fixture_repo, THREE_STORES)
    for i, store in enumerate(("default", "junk", "outlet"), start=1):
        _write_store_sales(fixture_repo, store, [_sale(str(i), gross=10 * i, fee=1, net=9)] * i)
    assert sr.main(["--no-sync", "--all-stores"]) == 0
    reports = fixture_repo / "reports"
    for i, (store, fname) in enumerate((("default", "sales_dashboard.html"),
                                        ("junk", "sales_dashboard-junk.html"),
                                        ("outlet", "sales_dashboard-outlet.html")), start=1):
        page = (reports / fname).read_text(encoding="utf-8")
        assert f"{i} sold line items" in page, store     # its own sales, never pooled


def test_sync_fetches_every_input_from_the_named_store(fixture_repo, monkeypatch):
    calls, pulled = [], []
    monkeypatch.setattr(sr, "_run", lambda label, args: calls.append(args) or True)
    monkeypatch.setattr(sr, "pull_ads", lambda store=None: pulled.append(store) or
                        {"campaigns": [], "ads": []})
    sr.sync(365, "outlet")
    assert calls[0] == ["lib/sync_actuals.py", "--days", "365", "--apply", "--store", "outlet"]
    assert calls[1] == ["tools/ebay_sheet.py", "--csv", "inventory_sheet-outlet.csv",
                        "--json", "inventory_sheet-outlet.json", "--store", "outlet"]
    assert pulled == ["outlet"]

    calls.clear()
    sr.sync(365, "default")
    # the default store keeps the historic file names
    assert calls[1] == ["tools/ebay_sheet.py", "--csv", "inventory_sheet.csv",
                        "--json", "inventory_sheet.json", "--store", "default"]


def test_price_vs_actual_reads_one_stores_sales(fixture_repo, monkeypatch):
    import price_vs_actual as pva
    _isolate(monkeypatch, fixture_repo, THREE_STORES)
    shoot = fixture_repo / "inventory" / "lot-1"
    shoot.mkdir(parents=True)
    (shoot / "price.txt").write_text("Conservative: $10\nRecommended: $20\nPush-high: $30\n",
                                     encoding="utf-8")
    row = {**_sale("9", gross=25, fee=3, net=22), "shoot_dir": "inventory/lot-1"}
    _write_store_sales(fixture_repo, "junk", [row])
    assert [r["sold"] for r in pva.gather("junk")] == [25.0]
    assert pva.gather("default") == []            # no default ledger: nothing, no crash
    assert pva.gather("outlet") == []
