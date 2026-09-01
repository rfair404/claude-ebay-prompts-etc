#!/usr/bin/env python3
"""Regression tests for lib/ebay_finances.py — the /sell/finances/v1/transaction
reader for #119 (route B: real ad fees + actual postage).

These lock down the traps the issue called out by name, each found by
review rather than live testing (no eBay credentials in this environment):

  * ad-fee attribution must not require `soldViaAdCampaign` — a cost-per-click
    ad bills whether or not eBay ends up crediting the sale to the campaign;
  * `SHIPPING_LABEL` transactions must reach the right place, keyed by order;
  * a fully-refunded order dropped from sales_ledger.csv must still surface
    its sunk ad-fee/postage cost as a loss, never silently disappear;
  * nothing PII-shaped (buyerInfo, raw transactionMemo text) may ever appear
    in a returned record.

Transaction dicts are hand-built in the shape the Finances API is documented
to return (transactionType / transactionDate / amount / orderId /
orderLineItems / feeType), built defensively per the module's own docstring
since this repo has no live account to verify the exact shape against.

Run:  pytest tests/test_ebay_finances.py
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

EF = pytest.importorskip(
    "ebay_finances", reason="ebay_finances imports ebay_client (config module)")


def _money(v):
    return {"value": str(v), "currency": "USD"}


def _ad_fee_txn(order_id="1-2-3", sku="abc123", amount="-2.50",
               date="2026-06-08T19:00:00.000Z", fee_type="AD_FEE"):
    """A NON_SALE_CHARGE for promoted-listing spend. Deliberately carries NO
    `soldViaAdCampaign` anywhere — that field lives on Fulfillment-API order
    line items, not here, and this transaction shape must still attribute
    correctly without it (the #119 "ad cost != ad attribution" trap)."""
    return {
        "transactionId": "txn-ad-1",
        "transactionType": "NON_SALE_CHARGE",
        "transactionDate": date,
        "amount": _money(amount),
        "feeType": fee_type,
        "orderId": order_id,
        "orderLineItems": [{"lineItemId": "li-1", "sku": sku}],
        "buyerInfo": {"buyerUsername": "definitely_pii_do_not_leak"},
        "transactionMemo": "Promoted Listings fee for buyer John Q Buyer",
    }


def _other_fee_txn(order_id="1-2-3", amount="-0.30",
                   date="2026-06-08T19:00:00.000Z", fee_type="INTERNATIONAL_FEE"):
    return {
        "transactionId": "txn-other-1",
        "transactionType": "NON_SALE_CHARGE",
        "transactionDate": date,
        "amount": _money(amount),
        "feeType": fee_type,
        "orderId": order_id,
        "orderLineItems": [{"lineItemId": "li-1", "sku": "abc123"}],
    }


def _shipping_label_txn(order_id="1-2-3", amount="-6.85",
                        date="2026-06-09T12:00:00.000Z"):
    return {
        "transactionId": "txn-ship-1",
        "transactionType": "SHIPPING_LABEL",
        "transactionDate": date,
        "amount": _money(amount),
        "orderId": order_id,
        "shippingLabel": {"trackingNumber": "9400111899223197428187"},
    }


def _sale_txn(order_id="1-2-3", amount="80.00", date="2026-06-08T19:00:00.000Z"):
    """A SALE transaction — out of scope for this reader (Fulfillment API
    already carries the sale); must be skipped, not mis-parsed as a fee."""
    return {
        "transactionId": "txn-sale-1",
        "transactionType": "SALE",
        "transactionDate": date,
        "amount": _money(amount),
        "orderId": order_id,
        "buyerInfo": {"buyerUsername": "definitely_pii_do_not_leak"},
    }


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def test_parses_ad_fee_other_fee_postage_and_skips_sale():
    parsed = EF.parse_transactions([
        _ad_fee_txn(), _other_fee_txn(), _shipping_label_txn(), _sale_txn(),
    ])
    assert len(parsed["fees"]) == 2
    assert len(parsed["postage"]) == 1
    ad = [f for f in parsed["fees"] if f.fee_type == "AD"]
    other = [f for f in parsed["fees"] if f.fee_type == "OTHER"]
    assert len(ad) == 1 and ad[0].amount == Decimal("-2.50")
    assert len(other) == 1 and other[0].amount == Decimal("-0.30")
    assert parsed["postage"][0].amount == Decimal("-6.85")
    assert parsed["postage"][0].order_id == "1-2-3"
    assert parsed["other_fee_labels"]["INTERNATIONAL_FEE"] == 1


def test_ad_fee_detection_falls_back_to_memo_when_feetype_absent():
    txn = _ad_fee_txn(fee_type="")
    txn["transactionMemo"] = "This is your Promoted Listings ad fee"
    parsed = EF.parse_transactions([txn])
    assert parsed["fees"][0].fee_type == "AD"


def test_unrecognized_non_sale_charge_is_filed_as_other_not_ad():
    parsed = EF.parse_transactions([_other_fee_txn(fee_type="REGULATORY_OPERATING_FEE")])
    assert parsed["fees"][0].fee_type == "OTHER"
    assert parsed["other_fee_labels"]["REGULATORY_OPERATING_FEE"] == 1


def test_fee_transaction_split_evenly_across_multi_sku_order():
    txn = _ad_fee_txn(amount="-9.00")
    txn["orderLineItems"] = [{"sku": "sku-a"}, {"sku": "sku-b"}, {"sku": "sku-c"}]
    parsed = EF.parse_transactions([txn])
    assert len(parsed["fees"]) == 3
    assert {f.sku for f in parsed["fees"]} == {"sku-a", "sku-b", "sku-c"}
    assert sum((f.amount for f in parsed["fees"]), Decimal(0)) == Decimal("-9.00")


def test_date_is_pacific_bucketed_not_raw_utc():
    # 2026-06-09T02:00:00Z is 2026-06-08 19:00 Pacific (PDT, UTC-7) — the #122
    # convention this module is required to use (see module docstring).
    txn = _ad_fee_txn(date="2026-06-09T02:00:00.000Z")
    parsed = EF.parse_transactions([txn])
    assert parsed["fees"][0].date == "2026-06-08"


# --------------------------------------------------------------------------
# attribution — must not require soldViaAdCampaign
# --------------------------------------------------------------------------
def test_ad_fee_attributed_by_order_without_any_ad_campaign_flag():
    # Note: none of the fixtures anywhere in this file carry soldViaAdCampaign
    # — that field simply does not exist on a Finances-API transaction. If
    # attribution worked, it worked without it.
    parsed = EF.parse_transactions([_ad_fee_txn(order_id="9-9-9", amount="-3.10")])
    by_order = EF.attribute_fees_by_order(parsed["fees"])
    assert by_order == {"9-9-9": Decimal("-3.10")}


def test_ad_fee_attributed_by_sku_too():
    parsed = EF.parse_transactions([_ad_fee_txn(sku="the-sku", amount="-3.10")])
    by_sku = EF.attribute_fees_by_sku(parsed["fees"])
    assert by_sku == {"the-sku": Decimal("-3.10")}


def test_ad_only_attribution_excludes_other_fees_by_default():
    parsed = EF.parse_transactions([_ad_fee_txn(amount="-2.00"),
                                    _other_fee_txn(amount="-0.50")])
    assert EF.attribute_fees_by_order(parsed["fees"]) == {"1-2-3": Decimal("-2.00")}
    assert EF.attribute_fees_by_order(parsed["fees"], ad_only=False) == \
        {"1-2-3": Decimal("-2.50")}


def test_multiple_ad_fee_transactions_on_one_order_sum():
    parsed = EF.parse_transactions([
        _ad_fee_txn(order_id="1-2-3", amount="-2.00"),
        _ad_fee_txn(order_id="1-2-3", amount="-1.25"),
    ])
    assert EF.attribute_fees_by_order(parsed["fees"]) == {"1-2-3": Decimal("-3.25")}


# --------------------------------------------------------------------------
# postage — "sold" vs "bought" totals
# --------------------------------------------------------------------------
def test_postage_reaches_attribute_postage_by_order():
    parsed = EF.parse_transactions([_shipping_label_txn(order_id="5-5-5", amount="-7.20")])
    assert EF.attribute_postage_by_order(parsed["postage"]) == {"5-5-5": Decimal("-7.20")}


def test_total_postage_bought_sums_regardless_of_order_match():
    parsed = EF.parse_transactions([
        _shipping_label_txn(order_id="1-1-1", amount="-5.00"),
        _shipping_label_txn(order_id="2-2-2", amount="-6.50"),
    ])
    assert EF.total_postage_bought(parsed["postage"]) == Decimal("-11.50")
    # the "sold" (by-order) and "bought" (window total) figures are DIFFERENT
    # concepts and must not collapse to the same number by construction:
    by_order = EF.attribute_postage_by_order(parsed["postage"])
    assert sum(by_order.values(), Decimal(0)) == EF.total_postage_bought(parsed["postage"])
    assert set(by_order) == {"1-1-1", "2-2-2"}


# --------------------------------------------------------------------------
# refunds are not zero — an unwound order still shows its sunk cost
# --------------------------------------------------------------------------
def test_unwound_order_loss_is_not_dropped():
    # Order 1-2-3 was fully refunded and excluded from sales_ledger.csv
    # entirely (sync_actuals.flatten_orders) — but the ad fee was only
    # partly credited and the label was already bought.
    parsed = EF.parse_transactions([
        _ad_fee_txn(order_id="1-2-3", amount="-2.50"),
        _shipping_label_txn(order_id="1-2-3", amount="-6.85"),
    ])
    ad_by_order = EF.attribute_fees_by_order(parsed["fees"])
    post_by_order = EF.attribute_postage_by_order(parsed["postage"])
    losses = EF.unwound_order_losses(["1-2-3"], ad_by_order, post_by_order)
    assert len(losses) == 1
    loss = losses[0]
    assert loss["order_id"] == "1-2-3"
    # positive magnitudes — money actually lost, matching sales_ledger.csv's
    # own positive-cost convention (see module docstring "Sign convention")
    assert loss["ad_fee"] == Decimal("2.50")
    assert loss["actual_postage"] == Decimal("6.85")
    assert loss["loss"] == Decimal("9.35")


def test_unwound_order_with_no_fee_or_postage_is_not_reported():
    losses = EF.unwound_order_losses(["no-charges-here"], {}, {})
    assert losses == []


def test_unwound_order_losses_deduplicates_repeated_order_ids():
    parsed = EF.parse_transactions([_ad_fee_txn(order_id="1-2-3", amount="-2.50")])
    ad_by_order = EF.attribute_fees_by_order(parsed["fees"])
    losses = EF.unwound_order_losses(["1-2-3", "1-2-3"], ad_by_order, {})
    assert len(losses) == 1


# --------------------------------------------------------------------------
# PII — nothing from buyerInfo / transactionMemo reaches a returned record
# --------------------------------------------------------------------------
def test_no_pii_field_in_parsed_output():
    parsed = EF.parse_transactions([
        _ad_fee_txn(), _other_fee_txn(), _shipping_label_txn(), _sale_txn(),
    ])
    blob = repr(parsed["fees"]) + repr(parsed["postage"]) + repr(parsed["other_fee_labels"])
    assert "definitely_pii_do_not_leak" not in blob
    assert "John Q Buyer" not in blob
    assert "buyerInfo" not in blob
    assert "9400111899223197428187" not in blob  # tracking number


def test_no_pii_field_in_unwound_losses_output():
    parsed = EF.parse_transactions([
        _ad_fee_txn(order_id="1-2-3"), _shipping_label_txn(order_id="1-2-3"),
    ])
    ad_by_order = EF.attribute_fees_by_order(parsed["fees"])
    post_by_order = EF.attribute_postage_by_order(parsed["postage"])
    losses = EF.unwound_order_losses(["1-2-3"], ad_by_order, post_by_order)
    blob = repr(losses)
    assert "definitely_pii_do_not_leak" not in blob
    assert set(losses[0]) == {"order_id", "ad_fee", "actual_postage", "loss"}


def test_feeline_and_postageline_have_no_pii_shaped_fields():
    # Fields are an explicit allowlist — this pins the dataclass shape so a
    # future edit can't quietly add buyerInfo/memo/tracking back in.
    assert set(EF.FeeLine.__dataclass_fields__) == {"order_id", "sku", "fee_type",
                                                     "amount", "date"}
    assert set(EF.PostageLine.__dataclass_fields__) == {"order_id", "amount", "date"}


# --------------------------------------------------------------------------
# fetch — windowing mirrors sync_actuals.fetch_orders
# --------------------------------------------------------------------------
def test_fetch_transactions_narrows_window_on_400(monkeypatch):
    calls = []

    def fake(start, end, verbose):
        calls.append((start, end))
        if len(calls) < 2:
            raise RuntimeError("GET /sell/finances/v1/transaction → HTTP 400")
        return [_ad_fee_txn()]

    monkeypatch.setattr(EF, "_fetch_transactions_window", fake)
    txns = EF.fetch_transactions(730, verbose=False)
    assert txns == [_ad_fee_txn()]
    assert len(calls) == 2  # 730 rejected, next candidate (540) accepted


def test_fetch_transactions_exhausted_windows_raise(monkeypatch):
    def always_400(start, end, verbose):
        raise RuntimeError("GET /sell/finances/v1/transaction → HTTP 400")

    monkeypatch.setattr(EF, "_fetch_transactions_window", always_400)
    with pytest.raises(RuntimeError, match="rejected every transaction window"):
        EF.fetch_transactions(30, verbose=False)


def test_fetch_transactions_non_400_error_propagates_immediately(monkeypatch):
    def boom(start, end, verbose):
        raise RuntimeError("HTTP 403 forbidden — insufficient scope")

    monkeypatch.setattr(EF, "_fetch_transactions_window", boom)
    with pytest.raises(RuntimeError, match="403"):
        EF.fetch_transactions(365, verbose=False)


def test_fetch_transactions_window_keeps_paging_past_a_server_side_page_cap(monkeypatch):
    # A server that always caps a page below the requested `limit` (e.g. 100
    # when limit=200) makes every page look "short" — stopping on that alone
    # would silently drop every transaction past page one.
    all_txns = [_ad_fee_txn(order_id=f"o-{i}") for i in range(250)]
    pages = [all_txns[0:100], all_txns[100:200], all_txns[200:250]]
    calls = []

    def fake_api_send(method, path, creds=None, marketplace=None):
        calls.append(path)
        return {"transactions": pages[len(calls) - 1], "total": 250}

    monkeypatch.setattr(EF, "api_send", fake_api_send)
    from datetime import datetime, timezone
    out = EF._fetch_transactions_window(
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc),
        verbose=False)
    assert len(out) == 250
    assert len(calls) == 3


def test_fetch_transactions_does_not_retry_the_same_window_twice(monkeypatch):
    # days=365 collides with one of the hard-coded fallback candidates — a
    # naive filter would list 365 twice, retrying an identical rejected
    # request before actually narrowing to 180.
    calls = []

    def always_400(start, end, verbose):
        calls.append(start)
        raise RuntimeError("GET /sell/finances/v1/transaction → HTTP 400")

    monkeypatch.setattr(EF, "_fetch_transactions_window", always_400)
    with pytest.raises(RuntimeError, match="rejected every transaction window"):
        EF.fetch_transactions(365, verbose=False)
    assert len(calls) == 3  # 365, 180, 90 — not 365, 365, 180, 90
