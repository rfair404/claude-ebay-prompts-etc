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
