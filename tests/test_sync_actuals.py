#!/usr/bin/env python3
"""Regression tests for lib/sync_actuals.py — the money math.

These lock down four ways the actuals could be wrong, all of them found by
review against live order data rather than imagined:

  * a fully REFUNDED order that was never cancelled still counted as revenue
    (measured: one $80 sale, totalDueSeller -$0.40, in the reported gross);
  * gross added shipping to a line total that may already contain it;
  * a failed fetch returned [] and read as "nothing sold";
  * two identically-titled listings both claimed the same shoot folder.

Order dicts are hand-built in the shape the Fulfillment API actually returns
(verified against live payloads), so no network and no credentials.

Run:  pytest tests/test_sync_actuals.py
"""
import csv
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

SA = pytest.importorskip(
    "sync_actuals", reason="sync_actuals imports ebay_client (config module)")


def _money(v):
    return {"value": str(v), "currency": "USD"}


def _order(*, item=80.0, ship=0.0, fee=11.93, refund=None, cancel="NONE_REQUESTED",
           oid="1-2-3", sku="abc123", total=None, due=None):
    """One single-line order in the live API's shape."""
    o = {
        "orderId": oid,
        "creationDate": "2026-06-08T19:00:00.000Z",
        "cancelStatus": {"cancelState": cancel},
        "totalMarketplaceFee": _money(fee),
        "lineItems": [{
            "legacyItemId": "206000000001",
            "sku": sku,
            "title": "McCoy Beehive Mixing Bowls Set of 4",
            "quantity": 1,
            "soldFormat": "FIXED_PRICE",
            "lineItemCost": _money(item),
            "total": _money(item if total is None else total),
            "deliveryCost": {"shippingCost": _money(ship)},
        }],
    }
    # totalDueSeller is what eBay says the seller actually keeps. On every clean
    # order on this account it equals item - fee; on a fully unwound one it
    # collapses to about zero regardless of how big the refund looks.
    pay = {"totalDueSeller": _money(round(item + ship - fee, 2) if due is None else due)}
    if refund is not None:
        pay["refunds"] = [{"amount": _money(refund), "refundStatus": "REFUNDED"}]
    o["paymentSummary"] = pay
    return o


# --------------------------------------------------------------------------
# refunds
# --------------------------------------------------------------------------
def test_fully_refunded_order_is_not_revenue_even_when_not_cancelled():
    # The exact live case: cancelState NONE_REQUESTED, $68.47 refunded on an $80
    # sale, totalDueSeller -$0.40. Note the refund is only 86% of the sale, so a
    # refund-ratio threshold would have kept it — totalDueSeller is the signal.
    rows, excluded = SA.flatten_orders(
        [_order(item=80.0, fee=11.93, refund=68.47, due=-0.40)])
    assert rows == [], "a fully refunded sale must not count as revenue"
    assert excluded["refunded"] == 1




def test_cancelled_order_is_excluded_and_counted_separately():
    rows, excluded = SA.flatten_orders([_order(cancel="CANCELED")])
    assert rows == []
    assert excluded["cancelled"] == 1 and excluded["refunded"] == 0


def test_partial_refund_is_subtracted_not_dropped():
    # $20 goodwill refund on a $100 sale: still a sale, but $20 lighter. A real
    # order in this state reports totalDueSeller 65 (100 - 15 fee - 20 refund).
    rows, excluded = SA.flatten_orders(
        [_order(item=100.0, fee=15.0, refund=20.0, due=65.0)])
    assert len(rows) == 1, "a partial refund is still a sale"
    r = rows[0]
    assert r["refunded"] == Decimal("20.00")
    assert r["gross"] == Decimal("80.00"), "gross must be net of the refund"
    assert r["net_before_postage"] == Decimal("65.00"), (
        "totalDueSeller is authoritative — it already nets refunds and fee credits")
    assert excluded["partial_refund"] == 1


def test_clean_order_survives_untouched():
    rows, excluded = SA.flatten_orders([_order(item=115.0, fee=17.65)])
    assert len(rows) == 1
    assert rows[0]["gross"] == Decimal("115.00")
    assert rows[0]["net_before_postage"] == Decimal("97.35")
    assert not any(excluded.values())


# --------------------------------------------------------------------------
# shipping basis — must not double-count
# --------------------------------------------------------------------------
def test_buyer_paid_shipping_is_counted_exactly_once():
    # Every order on the account is free-shipping, so this path had no live
    # coverage: build the case explicitly. total==item is what the API returns.
    rows, _ = SA.flatten_orders([_order(item=40.0, ship=10.0, fee=8.0)])
    r = rows[0]
    assert r["item_price"] == Decimal("40.00")
    assert r["buyer_shipping"] == Decimal("10.00")
    assert r["gross"] == Decimal("50.00"), "gross is item + shipping, counted once"


def test_line_total_disagreeing_with_item_cost_is_surfaced():
    # tax or a promotion moved `total` away from lineItemCost — don't absorb it
    _, excluded = SA.flatten_orders([_order(item=40.0, total=44.0)])
    assert excluded["total_mismatch"] == 1


def test_fee_is_split_across_lines_and_sums_back_to_the_whole():
    o = _order(item=30.0, fee=10.0)
    o["lineItems"].append(dict(o["lineItems"][0], sku="d2", lineItemCost=_money(70.0),
                               total=_money(70.0), legacyItemId="206000000002"))
    rows, _ = SA.flatten_orders([o])
    assert [r["ebay_fee"] for r in rows] == [Decimal("3.00"), Decimal("7.00")]
    assert sum(r["ebay_fee"] for r in rows) == Decimal("10.00")


# --------------------------------------------------------------------------
# a failed fetch must never look like an empty window
# --------------------------------------------------------------------------
def test_exhausted_windows_raise_rather_than_reporting_no_sales(monkeypatch):
    def always_400(days, verbose):
        raise RuntimeError("GET /sell/fulfillment/v1/order → HTTP 400")
    monkeypatch.setattr(SA, "_fetch_orders_window", always_400)
    # 30 is below every fallback rung, so the candidate list is a single entry —
    # the case that used to fall through to `return []`.
    with pytest.raises(RuntimeError, match="rejected every order window"):
        SA.fetch_orders(30, verbose=False)


def test_non_400_errors_propagate_immediately(monkeypatch):
    def boom(days, verbose):
        raise RuntimeError("HTTP 401 unauthorized")
    monkeypatch.setattr(SA, "_fetch_orders_window", boom)
    with pytest.raises(RuntimeError, match="401"):
        SA.fetch_orders(365, verbose=False)


# --------------------------------------------------------------------------
# ambiguous title matches must not silently claim a folder
# --------------------------------------------------------------------------
def _draft(dirname, title):
    return {"dir": dirname, "title": title, "price": "29.95", "sku": "", "listing_id": ""}


def test_identical_titles_do_not_claim_a_folder():
    # Two live listings on this account share a byte-identical title.
    title = "Vintage Anson Tie Bar NOS in Original Box Silver Tone"
    drafts = [_draft("inventory/anson-a", title), _draft("inventory/anson-b", title)]
    row = {"sku": "", "listing_id": "", "title": title}
    shoot, ask, how = SA.match_sale(row, drafts, [])
    assert shoot == "", "a tie must not hand the sale to an arbitrary folder"
    assert how.startswith("ambiguous")


def test_a_clear_title_winner_still_matches():
    drafts = [_draft("inventory/dulcimer", "McSpadden T34-W Mountain Dulcimer 1984 Walnut"),
              _draft("inventory/coke-tray", "Coca-Cola 75th Anniversary Tray 1975 Atlanta")]
    row = {"sku": "", "listing_id": "",
           "title": "McSpadden T34-W Mountain Dulcimer 1984 Walnut Scroll Headstock w/ Case"}
    shoot, ask, how = SA.match_sale(row, drafts, [])
    assert shoot == "inventory/dulcimer" and how.startswith("title~")


# --------------------------------------------------------------------------
# write_sales_ledger must merge, never overwrite — a narrow --days window
# used to erase every older sale outright (measured: a plain rewrite with
# the default --days 90 would have dropped anything sold before that).
# --------------------------------------------------------------------------
def _sale_row(order_id, sku, sold_at, item_price="10.00"):
    return {"order_id": order_id, "sold_at": sold_at, "listing_id": f"L{sku}",
            "sku": sku, "title": f"item {sku}", "quantity": "1",
            "sold_format": "FIXED_PRICE", "item_price": Decimal(item_price),
            "buyer_shipping": Decimal("0.00"), "refunded": Decimal("0.00"),
            "gross": Decimal(item_price), "ebay_fee": Decimal("1.00"),
            "net_before_postage": Decimal("9.00"), "listed_price": "10.00",
            "pct_of_ask": "100%", "shoot_dir": "", "matched_by": "sku"}


def test_write_sales_ledger_preserves_rows_outside_the_fetch_window(tmp_path, monkeypatch):
    ledger = tmp_path / "sales_ledger.csv"
    monkeypatch.setattr(SA, "SALES_LEDGER", ledger)

    # An old sale, written in a prior --apply, is already on disk...
    SA.write_sales_ledger([_sale_row("1-000", "old-sku", "2025-01-01T00:00:00Z")])
    # ...then a new --apply with a narrow window only fetches a recent sale.
    SA.write_sales_ledger([_sale_row("2-000", "new-sku", "2026-08-01T00:00:00Z")])

    with ledger.open(encoding="utf-8") as f:
        rows = {r["sku"]: r for r in csv.DictReader(f)}
    assert set(rows) == {"old-sku", "new-sku"}, \
        "the old sale must survive a later --apply that never re-fetched it"


def test_write_sales_ledger_updates_a_row_thats_refetched(tmp_path, monkeypatch):
    ledger = tmp_path / "sales_ledger.csv"
    monkeypatch.setattr(SA, "SALES_LEDGER", ledger)

    SA.write_sales_ledger([_sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z", "10.00")])
    # Same order+sku re-fetched later (e.g. a refund posted since) — must
    # replace the row in place, not duplicate it.
    SA.write_sales_ledger([_sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z", "8.00")])

    with ledger.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["item_price"] == "8.00"


# --------------------------------------------------------------------------
# #119 (route B, sell.finances) — ad_fee / actual_postage columns
#
# Blank, never 0.00, whenever a row hasn't been matched against the
# Finances API yet — 0.00 would claim "no ad spend, no postage" (a real,
# checked fact), which is a different statement from "not read yet".
# --------------------------------------------------------------------------
def test_write_sales_ledger_leaves_ad_fee_and_postage_blank_when_absent(tmp_path, monkeypatch):
    ledger = tmp_path / "sales_ledger.csv"
    monkeypatch.setattr(SA, "SALES_LEDGER", ledger)

    # _sale_row (existing fixture, predates #119) carries no ad_fee/
    # actual_postage keys at all — the exact shape sync_actuals produces
    # before #119's finances merge runs, or when it's skipped.
    SA.write_sales_ledger([_sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z")])

    with ledger.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["ad_fee"] == ""
    assert row["actual_postage"] == ""


def test_write_sales_ledger_formats_known_ad_fee_and_postage(tmp_path, monkeypatch):
    ledger = tmp_path / "sales_ledger.csv"
    monkeypatch.setattr(SA, "SALES_LEDGER", ledger)

    row = _sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z")
    row["ad_fee"], row["actual_postage"] = Decimal("2.50"), Decimal("6.85")
    SA.write_sales_ledger([row])

    with ledger.open(encoding="utf-8") as f:
        out = next(csv.DictReader(f))
    assert out["ad_fee"] == "2.50"
    assert out["actual_postage"] == "6.85"


def test_write_sales_ledger_treats_zero_ad_fee_as_a_real_known_value(tmp_path, monkeypatch):
    # Decimal("0") is a legitimate READ result (an order with no ad spend at
    # all) and must be written as "0.00", distinct from None -> "".
    ledger = tmp_path / "sales_ledger.csv"
    monkeypatch.setattr(SA, "SALES_LEDGER", ledger)

    row = _sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z")
    row["ad_fee"], row["actual_postage"] = Decimal("0"), Decimal("6.85")
    SA.write_sales_ledger([row])

    with ledger.open(encoding="utf-8") as f:
        out = next(csv.DictReader(f))
    assert out["ad_fee"] == "0.00"


def test_write_sales_ledger_does_not_erase_known_finances_data_on_rerun(tmp_path, monkeypatch):
    # A prior --apply recorded real ad_fee/actual_postage for this order...
    ledger = tmp_path / "sales_ledger.csv"
    monkeypatch.setattr(SA, "SALES_LEDGER", ledger)

    row = _sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z")
    row["ad_fee"], row["actual_postage"] = Decimal("2.50"), Decimal("6.85")
    SA.write_sales_ledger([row])

    # ...then a rerun re-merges the SAME order but without a fresh Finances
    # read this time (--skip-finances, or the Finances API call degraded) —
    # the fetched row has ad_fee/actual_postage back to None. The prior
    # known values must survive, not be blanked out.
    rerun_row = _sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z")
    SA.write_sales_ledger([rerun_row])

    with ledger.open(encoding="utf-8") as f:
        out = next(csv.DictReader(f))
    assert out["ad_fee"] == "2.50"
    assert out["actual_postage"] == "6.85"


# --------------------------------------------------------------------------
# #119 — flatten_orders tracks WHICH orders it excluded, so a caller can
# check them against the Finances API's fee/postage-by-order maps (the
# "refunds are not zero" trap: an unwound order can still owe a real loss).
# --------------------------------------------------------------------------
def test_flatten_orders_reports_refunded_order_ids():
    _, excluded = SA.flatten_orders(
        [_order(item=80.0, fee=11.93, refund=68.47, due=-0.40, oid="ref-order-1")])
    assert excluded["refunded_order_ids"] == ["ref-order-1"]
    assert excluded["cancelled_order_ids"] == []


def test_flatten_orders_reports_cancelled_order_ids():
    _, excluded = SA.flatten_orders([_order(cancel="CANCELED", oid="cxl-order-1")])
    assert excluded["cancelled_order_ids"] == ["cxl-order-1"]
    assert excluded["refunded_order_ids"] == []


# --------------------------------------------------------------------------
# #119 — allocate_order_totals: order-level ad_fee/actual_postage totals
# must land on flatten_orders()'s one-row-per-line-item rows WITHOUT
# double-counting a multi-line order, and must resolve "no matching
# transaction" to a known $0.00 (not permanently blank) for ad_fee once a
# Finances read has actually succeeded this run — but never for postage.
# --------------------------------------------------------------------------
def _line(item_price, buyer_shipping="0.00"):
    return {"item_price": Decimal(item_price), "buyer_shipping": Decimal(buyer_shipping)}


def test_allocate_splits_order_total_across_lines_without_double_counting():
    # A 3-line-item order: the SAME $9.00 ad-fee total must not land whole on
    # every row — summed back over the rows it must equal $9.00, not $27.00.
    lines = [_line("30.00"), _line("50.00"), _line("20.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {"o-1": Decimal("-9.00")}, "ad_fee")
    assert [ln["ad_fee"] for ln in lines] == [
        Decimal("2.70"), Decimal("4.50"), Decimal("1.80")]
    assert sum((ln["ad_fee"] for ln in lines), Decimal(0)) == Decimal("9.00"), \
        "shares must sum back to the order total, not multiply it by row count"


def test_allocate_splits_by_item_price_plus_shipping_share():
    lines = [_line("40.00", "10.00"), _line("50.00", "0.00")]  # bases: 50 / 50
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {"o-1": Decimal("-10.00")}, "actual_postage")
    assert [ln["actual_postage"] for ln in lines] == [Decimal("5.00"), Decimal("5.00")]


def test_allocate_folds_rounding_remainder_into_last_line():
    # $1.00 across 3 lines of equal basis: 0.33/0.33/0.34, not a drifted total.
    lines = [_line("10.00"), _line("10.00"), _line("10.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {"o-1": Decimal("-1.00")}, "ad_fee")
    assert sum((ln["ad_fee"] for ln in lines), Decimal(0)) == Decimal("1.00")
    assert lines[-1]["ad_fee"] != lines[0]["ad_fee"]  # remainder landed on the last line


def test_allocate_splits_evenly_when_every_line_has_a_zero_basis():
    # A giveaway bundle: item_price + buyer_shipping is 0 on every line, so
    # there is no meaningful share — must split evenly, not dump the whole
    # total onto one arbitrary (e.g. the last) line.
    lines = [_line("0.00"), _line("0.00"), _line("0.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {"o-1": Decimal("-3.00")}, "ad_fee")
    assert [ln["ad_fee"] for ln in lines] == [Decimal("1.00")] * 3


def test_allocate_leaves_field_none_when_order_total_unknown_by_default():
    lines = [_line("10.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {}, "actual_postage")
    assert lines[0]["actual_postage"] is None


def test_allocate_absence_is_zero_resolves_missing_ad_fee_to_known_zero():
    # An order with no AD-classified fee transaction at all (no key in
    # totals_by_order) is a real $0.00 ad spend once absence_is_zero is on
    # (only safe when the Finances sync succeeded this run) — never left
    # blank forever, which would keep coverage from ever reaching 100% for
    # a window containing an unpromoted sale.
    lines = [_line("10.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {}, "ad_fee", absence_is_zero=True)
    assert lines[0]["ad_fee"] == Decimal("0.00")
    assert lines[0]["ad_fee"] is not None


def test_allocate_postage_never_treats_absence_as_zero_even_if_called_with_the_flag():
    # Guard against a future call site accidentally passing absence_is_zero
    # for postage: the function itself still only zero-fills the field it
    # was told to, so this pins that ad_fee/actual_postage are independent
    # calls — sync_actuals.main() must call postage without the flag.
    lines = [_line("10.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {}, "actual_postage", absence_is_zero=False)
    assert lines[0]["actual_postage"] is None


def test_allocate_known_order_total_overrides_absence_is_zero():
    # absence_is_zero only fires when the order has NO key at all — an order
    # that DOES have a (possibly zero) known total still gets that real value.
    lines = [_line("10.00")]
    order_lines = {"o-1": lines}
    SA.allocate_order_totals(order_lines, {"o-1": Decimal("-2.00")}, "ad_fee",
                             absence_is_zero=True)
    assert lines[0]["ad_fee"] == Decimal("2.00")


# --------------------------------------------------------------------------
# #119 — sync_finances degrades to an empty read + a reason, never raises,
# so one degraded source (scope not yet re-consented) can't take down a
# whole --apply run the way it would if this propagated.
# --------------------------------------------------------------------------
def test_sync_finances_degrades_on_auth_error(monkeypatch):
    import ebay_finances
    from ebay_client import EbayAuthError

    def boom(days, verbose=True):
        raise EbayAuthError("sell.finances not yet re-consented")

    monkeypatch.setattr(ebay_finances, "fetch_transactions", boom)
    ad_by_order, postage_by_order, status = SA.sync_finances(90, verbose=False)
    assert ad_by_order == {} and postage_by_order == {}
    assert status["ok"] is False
    assert "re-consented" in status["reason"]


def test_sync_finances_collapses_a_multiline_error_into_one_line(monkeypatch):
    import ebay_finances
    from ebay_client import EbayAPIError

    def boom(days, verbose=True):
        raise EbayAPIError(401, "unauthorized\n  reason: invalid_scope\n  hint: re-consent")

    monkeypatch.setattr(ebay_finances, "fetch_transactions", boom)
    _, _, status = SA.sync_finances(90, verbose=False)
    assert "\n" not in status["reason"]
    assert "invalid_scope" in status["reason"]


def test_sync_finances_returns_attribution_on_success(monkeypatch):
    import ebay_finances

    def fake(days, verbose=True):
        return [{
            "transactionType": "NON_SALE_CHARGE",
            "transactionDate": "2026-06-08T19:00:00.000Z",
            "amount": {"value": "-2.50", "currency": "USD"},
            "feeType": "AD_FEE",
            "orderId": "1-2-3",
            "orderLineItems": [{"sku": "abc123"}],
        }]

    monkeypatch.setattr(ebay_finances, "fetch_transactions", fake)
    ad_by_order, postage_by_order, status = SA.sync_finances(90, verbose=False)
    assert ad_by_order == {"1-2-3": Decimal("-2.50")}
    assert status["ok"] is True and status["reason"] is None
