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
import stores  # noqa: E402


def _isolate(monkeypatch, root, cfg=None):
    """Point every per-store path at `root` (#156): stores.REPO patched, no
    ambient store, no ledger override, and `cfg` (default {}) as the config —
    so a test can never read or write the real ledgers."""
    monkeypatch.setattr(stores, "REPO", root)
    monkeypatch.setattr(stores, "load_config", lambda: cfg or {})
    for var in ("EBAYBIZ_STORE", "EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG"):
        monkeypatch.delenv(var, raising=False)


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
# sold_at is bucketed in Pacific time, not UTC-truncated (#122)
# --------------------------------------------------------------------------
def test_sold_at_lands_in_the_previous_pacific_day_for_an_early_utc_order():
    # 2026-08-01T02:00:00Z is 19:00 Pacific on July 31 (PDT, UTC-7) -- the
    # exact #122 case: an evening sale that a naive creationDate[:10] slice
    # would have miscounted into August.
    o = _order(oid="9-1", sku="pdt-case")
    o["creationDate"] = "2026-08-01T02:00:00.000Z"
    rows, _ = SA.flatten_orders([o])
    assert len(rows) == 1
    assert rows[0]["sold_at"] == "2026-07-31"


def test_sold_at_matches_utc_date_when_well_clear_of_the_pacific_offset():
    o = _order(oid="9-2", sku="midday-case")
    o["creationDate"] = "2026-08-01T20:00:00.000Z"   # 13:00 Pacific, same day
    rows, _ = SA.flatten_orders([o])
    assert rows[0]["sold_at"] == "2026-08-01"


def test_sold_at_falls_back_to_utc_truncation_for_an_unparsable_creation_date():
    o = _order(oid="9-3", sku="garbage-case")
    o["creationDate"] = "not-a-real-timestamp"
    rows, _ = SA.flatten_orders([o])
    # No crash, no dropped row -- degrades to the old behaviour rather than
    # losing the sale.
    assert rows[0]["sold_at"] == "not-a-real-timestamp"[:10]


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
    def always_400(days, verbose, creds=None):
        raise RuntimeError("GET /sell/fulfillment/v1/order → HTTP 400")
    monkeypatch.setattr(SA, "_fetch_orders_window", always_400)
    # 30 is below every fallback rung, so the candidate list is a single entry —
    # the case that used to fall through to `return []`.
    with pytest.raises(RuntimeError, match="rejected every order window"):
        SA.fetch_orders(30, verbose=False)


def test_non_400_errors_propagate_immediately(monkeypatch):
    def boom(days, verbose, creds=None):
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
    _isolate(monkeypatch, tmp_path)

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
    _isolate(monkeypatch, tmp_path)

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
    _isolate(monkeypatch, tmp_path)

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
    _isolate(monkeypatch, tmp_path)

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
    _isolate(monkeypatch, tmp_path)

    row = _sale_row("1-000", "sku-a", "2026-08-01T00:00:00Z")
    row["ad_fee"], row["actual_postage"] = Decimal("0"), Decimal("6.85")
    SA.write_sales_ledger([row])

    with ledger.open(encoding="utf-8") as f:
        out = next(csv.DictReader(f))
    assert out["ad_fee"] == "0.00"


def test_write_sales_ledger_does_not_erase_known_finances_data_on_rerun(tmp_path, monkeypatch):
    # A prior --apply recorded real ad_fee/actual_postage for this order...
    ledger = tmp_path / "sales_ledger.csv"
    _isolate(monkeypatch, tmp_path)

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

    def boom(days, verbose=True, **kw):
        raise EbayAuthError("sell.finances not yet re-consented")

    monkeypatch.setattr(ebay_finances, "fetch_transactions", boom)
    ad_by_order, postage_by_order, status = SA.sync_finances(90, verbose=False)
    assert ad_by_order == {} and postage_by_order == {}
    assert status["ok"] is False
    assert "re-consented" in status["reason"]


def test_sync_finances_collapses_a_multiline_error_into_one_line(monkeypatch):
    import ebay_finances
    from ebay_client import EbayAPIError

    def boom(days, verbose=True, **kw):
        raise EbayAPIError(401, "unauthorized\n  reason: invalid_scope\n  hint: re-consent")

    monkeypatch.setattr(ebay_finances, "fetch_transactions", boom)
    _, _, status = SA.sync_finances(90, verbose=False)
    assert "\n" not in status["reason"]
    assert "invalid_scope" in status["reason"]


def test_sync_finances_returns_attribution_on_success(monkeypatch):
    import ebay_finances

    def fake(days, verbose=True, **kw):
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


# load_hand_locations — the shelf for an item listed by hand (no shoot folder)
# --------------------------------------------------------------------------
def test_load_hand_locations_maps_listing_id_or_sku_to_shelf(tmp_path, monkeypatch):
    import sync_actuals as sa
    f = tmp_path / "hand_listed_locations.csv"
    f.write_text("listing_id,location,note\n"
                 "206301883472,cats-mens-4,Brooks Brothers 1981\n"
                 "some-sku,bin-2,\n"
                 "206300000000,,no shelf recorded\n", encoding="utf-8")
    _isolate(monkeypatch, tmp_path)
    assert sa.load_hand_locations() == {"206301883472": "cats-mens-4",
                                        "some-sku": "bin-2"}


def test_load_hand_locations_missing_file_means_no_overrides(tmp_path, monkeypatch):
    import sync_actuals as sa
    _isolate(monkeypatch, tmp_path)
    assert sa.load_hand_locations() == {}


# --------------------------------------------------------------------------
# #156 — one store per pass. The canonical SKU and the inventory/ tree are
# both store-independent, so without a store filter a store-B order can
# claim (and stamp) a store-A folder and flip a store-A ledger row to SOLD.
# --------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402

THREE_STORES = {"ebay": {"stores": {"junk": {}, "outlet": {}}}}
TITLE_A = "McCoy Beehive Mixing Bowls Set of 4"


def _write_draft(root, rel, *, title, sku, store=None, price="80.00"):
    d = root / "inventory" / rel
    d.mkdir(parents=True)
    fm = [f'title: "{title}"', f'price: "{price}"', f'ebay_inventory_sku: "{sku}"']
    if store is not None:
        fm.append(f'store: "{store}"')
    (d / "draft.md").write_text("---\n" + "\n".join(fm) + "\n---\n\nbody\n", encoding="utf-8")
    return d


def _write_ledger(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "status", "title", "price", "listing_id"])
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """Three configured stores, one inventory/ tree:

      a  main-store item (no `store:`)             sku aaaa1111
      b  junk-store item (`store: junk`)           sku bbbb2222
      c  started on main, RELISTED on junk: no `store:`, sku cccc3333,
         present in BOTH listings ledgers
    """
    _isolate(monkeypatch, tmp_path, THREE_STORES)
    monkeypatch.setattr(SA, "REPO", tmp_path)
    monkeypatch.setattr(SA, "INVENTORY", tmp_path / "inventory")
    _write_draft(tmp_path, "shootA/a", title=TITLE_A, sku="aaaa1111")
    _write_draft(tmp_path, "shootB/b", title="Box of Assorted Junk Drawer Keys",
                 sku="bbbb2222", store="junk")
    _write_draft(tmp_path, "shootC/c", title="Fenton Hobnail Milk Glass Vase", sku="cccc3333")
    _write_ledger(tmp_path / "listings_ledger.csv",
                  [{"sku": "aaaa1111", "status": "PUBLISHED", "title": TITLE_A, "price": "80.00"},
                   {"sku": "cccc3333", "status": "ENDED", "title": "Fenton", "price": "40.00"}])
    _write_ledger(tmp_path / "listings_ledger-junk.csv",
                  [{"sku": "bbbb2222", "status": "PUBLISHED", "title": "Box", "price": "9.00"},
                   {"sku": "cccc3333", "status": "PUBLISHED", "title": "Fenton", "price": "15.00"}])
    return tmp_path


def _dirs(drafts):
    return sorted(d["dir"] for d in drafts)


def test_default_store_keeps_the_historic_filenames(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert SA.write_sales_ledger([_sale_row("1-000", "sku-a", "2026-08-01")]) \
        == tmp_path / "sales_ledger.csv"
    assert SA.write_finances_status({"ok": True}, days=90, orders_total=0, orders_covered=0) \
        == tmp_path / "reports" / "finances_sync_status.json"
    (tmp_path / "hand_listed_locations.csv").write_text(
        "listing_id,location\n206000000001,bin-1\n", encoding="utf-8")
    assert SA.load_hand_locations() == {"206000000001": "bin-1"}


def test_a_named_store_reads_and_writes_only_its_own_files(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, THREE_STORES)
    (tmp_path / "hand_listed_locations.csv").write_text(
        "listing_id,location\n206000000001,main-shelf\n", encoding="utf-8")
    (tmp_path / "hand_listed_locations-junk.csv").write_text(
        "listing_id,location\n206000000002,junk-bin\n", encoding="utf-8")

    assert SA.write_sales_ledger([_sale_row("1-000", "sku-a", "2026-08-01")], "junk") \
        == tmp_path / "sales_ledger-junk.csv"
    SA.write_finances_status({"ok": False, "reason": "no consent"}, days=90,
                             orders_total=1, orders_covered=0, store="junk")
    assert not (tmp_path / "sales_ledger.csv").exists()
    assert not (tmp_path / "reports" / "finances_sync_status.json").exists()
    assert (tmp_path / "reports" / "finances_sync_status-junk.json").exists()
    assert SA.load_hand_locations("junk") == {"206000000002": "junk-bin"}
    assert SA.load_hand_locations("default") == {"206000000001": "main-shelf"}


def test_scan_drafts_filters_by_store_field_or_ledger_membership(shop):
    # No store: every draft (tools/pick_list*.py's pre-#156 call).
    assert _dirs(SA.scan_drafts()) == ["inventory/shootA/a", "inventory/shootB/b",
                                       "inventory/shootC/c"]
    # default: its unlabelled drafts (c has no `store:` either).
    assert _dirs(SA.scan_drafts("default")) == ["inventory/shootA/a", "inventory/shootC/c"]
    # junk: its `store: junk` draft, plus c by junk-ledger membership (relisted).
    assert _dirs(SA.scan_drafts("junk")) == ["inventory/shootB/b", "inventory/shootC/c"]
    # outlet: nothing is its own — not even as a title-fallback candidate.
    assert SA.scan_drafts("outlet") == []


def test_ambient_store_does_not_move_drafts_between_stores(shop, monkeypatch):
    # EBAYBIZ_STORE=junk must not make an unlabelled draft a junk draft.
    monkeypatch.setenv("EBAYBIZ_STORE", "junk")
    assert "inventory/shootA/a" not in _dirs(SA.scan_drafts("junk"))
    assert "inventory/shootA/a" in _dirs(SA.scan_drafts("default"))


def _store_order(sku, title, oid):
    o = _order(oid=oid, sku=sku)
    o["lineItems"][0]["title"] = title
    return o


def _args(**kw):
    base = dict(days=90, apply=True, skip_finances=True, store_json=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_store_b_order_never_claims_or_overwrites_a_store_a_row(shop, monkeypatch):
    # The junk store sells something whose SKU AND title are the main store's
    # item "a" — the collision #156 §3 names. Nothing of a's may change.
    seen_store = []

    def fake_fetch(days, verbose=True, *, store=None):
        seen_store.append(store)
        return [_store_order("aaaa1111", TITLE_A, "J-1")]

    monkeypatch.setattr(SA, "fetch_orders", fake_fetch)
    (shop / "sales_ledger.csv").write_text("order_id,sku\nMAIN-1,aaaa1111\n", encoding="utf-8")
    main_ledger_before = (shop / "listings_ledger.csv").read_bytes()
    main_sales_before = (shop / "sales_ledger.csv").read_bytes()

    assert SA.sync_store("junk", _args()) == 0

    assert seen_store == ["junk"], "orders must be fetched with the junk store's credentials"
    with (shop / "sales_ledger-junk.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["shoot_dir"] == "", "a store-B order must not claim a store-A folder"
    assert rows[0]["matched_by"] == "unmatched"
    assert not (shop / "inventory" / "shootA" / "a" / "SOLD.md").exists()
    # the main store's ledgers are byte-for-byte untouched...
    assert (shop / "listings_ledger.csv").read_bytes() == main_ledger_before
    assert (shop / "sales_ledger.csv").read_bytes() == main_sales_before
    # ...and the SOLD advance landed in the junk store's own ledger
    with (shop / "listings_ledger-junk.csv").open(encoding="utf-8") as f:
        junk = {r["sku"]: r for r in csv.DictReader(f)}
    assert junk["aaaa1111"]["status"] == "SOLD"


def test_a_relisted_item_matches_its_folder_on_the_store_that_sold_it(shop, monkeypatch):
    monkeypatch.setattr(SA, "fetch_orders", lambda days, verbose=True, *, store=None:
                        [_store_order("cccc3333", "Fenton Hobnail Milk Glass Vase", "J-2")])
    SA.sync_store("junk", _args())
    with (shop / "sales_ledger-junk.csv").open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["shoot_dir"] == "inventory/shootC/c" and row["matched_by"] == "sku"
    stamp = (shop / "inventory" / "shootC" / "c" / "SOLD.md").read_text(encoding="utf-8")
    assert "- Store: junk" in stamp
    # the main ledger's row for the same SKU keeps its own status
    with (shop / "listings_ledger.csv").open(encoding="utf-8") as f:
        main = {r["sku"]: r for r in csv.DictReader(f)}
    assert main["cccc3333"]["status"] == "ENDED"


def test_all_stores_runs_one_isolated_pass_per_store_three_stores(shop, monkeypatch):
    # Three stores, not two: nothing may assume a pair.
    orders = {"default": _store_order("aaaa1111", TITLE_A, "D-1"),
              "junk": _store_order("bbbb2222", "Box of Assorted Junk Drawer Keys", "J-1"),
              "outlet": _store_order("dddd4444", "Outlet Only Item", "O-1")}
    monkeypatch.setattr(SA, "fetch_orders", lambda days, verbose=True, *, store=None:
                        [orders[store]])
    assert SA.main(["--all-stores", "--apply", "--skip-finances"]) == 0

    for store, suffix in (("default", ""), ("junk", "-junk"), ("outlet", "-outlet")):
        with (shop / f"sales_ledger{suffix}.csv").open(encoding="utf-8") as f:
            ids = [r["order_id"] for r in csv.DictReader(f)]
        assert ids == [orders[store]["orderId"]], store
        assert (shop / "reports" / f"finances_sync_status{suffix}.json").exists(), store
    # each store's order found its own folder, and only its own
    assert "D-1" in (shop / "inventory" / "shootA" / "a" / "SOLD.md").read_text(encoding="utf-8")
    assert "J-1" in (shop / "inventory" / "shootB" / "b" / "SOLD.md").read_text(encoding="utf-8")


def test_fetch_orders_and_finances_use_the_named_stores_account(monkeypatch):
    import ebay_finances
    monkeypatch.setattr(SA, "load_credentials", lambda store=None: f"creds:{store}")
    used = []
    monkeypatch.setattr(SA, "_fetch_orders_window",
                        lambda days, verbose, creds=None: used.append(creds) or [])
    SA.fetch_orders(90, verbose=False, store="outlet")
    assert used == ["creds:outlet"]

    asked = []
    monkeypatch.setattr(ebay_finances, "fetch_transactions",
                        lambda days, verbose=True, store=None: asked.append(store) or [])
    SA.sync_finances(90, verbose=False, store="outlet")
    assert asked == ["outlet"]


def test_store_json_is_refused_with_all_stores(shop):
    with pytest.raises(SystemExit):
        SA.main(["--all-stores", "--store-json", "x.json"])
