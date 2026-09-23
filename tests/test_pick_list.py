#!/usr/bin/env python3
"""tools/pick_list.py — poll/print idempotency + record-tracking (GH #32).

All eBay HTTP is faked by patching module-level functions (fetch_open /
api_send / fetch_order), the same "no network, no credentials" convention as
tests/test_ebay_client.py. Filesystem side effects (the print-state ledger,
the rendered pick_lists/ output, listings_ledger.csv) are redirected to a
scratch tempdir for every test — nothing here touches the repo's real state
or writes buyer PII anywhere tracked.

Run:  python tests/test_pick_list.py
  or: pytest tests/test_pick_list.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

pick_list = pytest.importorskip(
    "pick_list", reason="pick_list imports ebay_client (config module)")
from ebay_client import EbayAPIError                        # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures (hand-built, no pytest fixture args — every test below is a plain
# no-arg function so it also runs under tests/run_all.py)
# --------------------------------------------------------------------------- #
def _money(v):
    return {"value": str(v), "currency": "USD"}


def _order(*, oid="03-11111-22222", sku="abc123def4", item_id="206000000001",
           title="McCoy Beehive Mixing Bowl", qty=1, price=24.99,
           line_item_id="11500017010", fullname="Jamie Buyer"):
    return {
        "orderId": oid,
        "legacyOrderId": oid.replace("-", ""),
        "salesRecordReference": "1",
        "creationDate": "2026-08-20T12:00:00.000Z",
        "orderFulfillmentStatus": "NOT_STARTED",
        "orderPaymentStatus": "PAID",
        "lineItems": [{
            "lineItemId": line_item_id,
            "legacyItemId": item_id,
            "sku": sku,
            "title": title,
            "quantity": qty,
            "lineItemCost": _money(price),
            "lineItemFulfillmentInstructions": {"shipByDate": "2026-08-24T00:00:00.000Z"},
        }],
        "fulfillmentStartInstructions": [{
            "maxEstimatedDeliveryDate": "2026-08-29T00:00:00.000Z",
            "shippingStep": {
                "shippingCarrierCode": "USPS",
                "shippingServiceCode": "USPSGround",
                "shipTo": {
                    "fullName": fullname,
                    "contactAddress": {
                        "addressLine1": "1 Test Way",
                        "city": "Springfield", "stateOrProvince": "OH",
                        "postalCode": "45501", "countryCode": "US",
                    },
                },
            },
        }],
        "pricingSummary": {"total": _money(price), "deliveryCost": _money(0)},
        "paymentSummary": {"totalDueSeller": _money(price - 3)},
    }


class _Patched:
    """Set module attributes for the duration of a `with`, restoring after —
    same manual-patch shape as tests/test_ebay_client.py's _patched().

    Usage: `with _Patched({(pick_list, "api_send"): fake}):` — a plain dict,
    not **kwargs, because the keys are (module, name) tuples."""

    def __init__(self, targets: dict):
        self._targets = targets  # {(module, name): new_value}
        self._saved = {}

    def __enter__(self):
        for (mod, name), val in self._targets.items():
            self._saved[(mod, name)] = getattr(mod, name)
            setattr(mod, name, val)
        return self

    def __exit__(self, *exc):
        for (mod, name), val in self._saved.items():
            setattr(mod, name, val)
        return False


def _scratch_dirs():
    """A fresh tempdir; returns (out_dir, state_file) inside it."""
    d = Path(tempfile.mkdtemp(prefix="pick_list_test_"))
    return d / "pick_lists", d / ".pick_list_state.json"


# --------------------------------------------------------------------------- #
# fetch_open — the one-brace-group gotcha (GH #32's "known gotcha")
# --------------------------------------------------------------------------- #
def test_open_filter_is_one_brace_group_not_two_calls():
    # eBay 400s if NOT_STARTED and IN_PROGRESS are requested as separate
    # filters; the fix is one orderfulfillmentstatus with both states in a
    # single {a|b} group. Lock the literal down so nobody "simplifies" it back
    # into two calls.
    assert pick_list.OPEN_FILTER == "orderfulfillmentstatus:%7BNOT_STARTED%7CIN_PROGRESS%7D"


def test_fetch_open_pages_until_total_reached():
    calls = []

    def fake_api_send(method, path, creds=None, marketplace=None, body=None):
        calls.append(path)
        offset = int(path.split("offset=")[1].split("&")[0])
        # fetch_open always asks for limit=50 and steps offset by 50 regardless
        # of how many an actual batch holds, so `total` has to exceed 50 to
        # force a second page even though each fake batch is tiny.
        if offset == 0:
            return {"total": 51, "orders": [_order(oid="a"), _order(oid="b")]}
        return {"total": 51, "orders": [_order(oid="c")]}

    with _Patched({(pick_list, "api_send"): fake_api_send}):
        orders = pick_list.fetch_open()
    assert [o["orderId"] for o in orders] == ["a", "b", "c"]
    assert len(calls) == 2
    for c in calls:
        assert pick_list.OPEN_FILTER in c


# --------------------------------------------------------------------------- #
# render — sanity on the fields the issue calls out (item, qty, listing id,
# sku, ship-to, carrier/service, ship-by, total/net)
# --------------------------------------------------------------------------- #
def test_render_carries_the_fields_the_pick_list_promises():
    text = pick_list.render(_order(), [], [])
    assert "206000000001" in text          # listing id
    assert "abc123def4" in text            # sku
    assert "Jamie Buyer" in text           # ship-to name
    assert "USPS USPSGround" in text       # carrier/service
    assert "2026-08-24" in text            # ship-by
    assert "$24.99" in text                # order total
    assert "$21.99" in text                # net (totalDueSeller)


# --------------------------------------------------------------------------- #
# poll_and_print — idempotent printing
# --------------------------------------------------------------------------- #
def test_poll_and_print_writes_new_orders_and_skips_reprints():
    out_dir, _ = _scratch_dirs()
    orders = [_order(oid="new-1"), _order(oid="new-2", sku="zzz999")]
    try:
        with _Patched({(pick_list, "scan_drafts"): lambda: [],
                          (pick_list, "load_listings_ledger"): lambda: []}):
            new_ids, skipped_ids, state, unconfirmed = pick_list.poll_and_print(
                out_dir=out_dir, state={"printed": {}, "shipped": {}},
                fetch=lambda: orders, publish=False)
        assert sorted(new_ids) == ["new-1", "new-2"]
        assert skipped_ids == []
        assert unconfirmed == []
        assert set(state["printed"]) == {"new-1", "new-2"}
        files = sorted(p.name for p in out_dir.glob("*.txt"))
        assert files == ["pick_new-1.txt", "pick_new-2.txt"]
        # buyer PII made it into the file, never into anything else this test
        # touches (stdout is not asserted on — see the module docstring).
        assert "Jamie Buyer" in (out_dir / "pick_new-1.txt").read_text()

        # Poll again with the SAME state (as a real second poll would reuse
        # it) — nothing new should render.
        with _Patched({(pick_list, "scan_drafts"): lambda: [],
                          (pick_list, "load_listings_ledger"): lambda: []}):
            new_ids2, skipped_ids2, state2, _ = pick_list.poll_and_print(
                out_dir=out_dir, state=state, fetch=lambda: orders, publish=False)
        assert new_ids2 == []
        assert sorted(skipped_ids2) == ["new-1", "new-2"]
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_poll_and_print_reprint_forces_one_order_through():
    out_dir, _ = _scratch_dirs()
    orders = [_order(oid="dup-1")]
    state = {"printed": {"dup-1": {"printed_at": "x", "file": "y"}}, "shipped": {}}
    try:
        with _Patched({(pick_list, "scan_drafts"): lambda: [],
                          (pick_list, "load_listings_ledger"): lambda: []}):
            new_ids, skipped_ids, _, unconfirmed = pick_list.poll_and_print(
                out_dir=out_dir, state=state, fetch=lambda: orders, publish=False, reprint="dup-1")
        assert new_ids == ["dup-1"]
        assert skipped_ids == []
        assert unconfirmed == []
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_poll_and_print_do_print_failure_never_raises():
    out_dir, _ = _scratch_dirs()
    orders = [_order(oid="print-1")]

    def boom(_path):
        raise OSError("no printer configured")

    try:
        with _Patched({(pick_list, "scan_drafts"): lambda: [],
                          (pick_list, "load_listings_ledger"): lambda: [],
                          (pick_list, "_send_to_printer"): boom}):
            new_ids, _, _, unconfirmed = pick_list.poll_and_print(
                out_dir=out_dir, state={"printed": {}, "shipped": {}},
                fetch=lambda: orders, publish=False, do_print=True)
        assert new_ids == ["print-1"]  # the file still got written
        assert unconfirmed == ["print-1"]  # printing failed, but poll didn't raise
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


# --------------------------------------------------------------------------- #
# fetch_order / build_shipping_fulfillment_body / record_tracking
# --------------------------------------------------------------------------- #
def test_fetch_order_returns_none_on_404():
    def raise_404(method, path, creds=None, marketplace=None, body=None):
        raise EbayAPIError(404, "not found", '{"errors":[]}')

    with _Patched({(pick_list, "api_send"): raise_404}):
        assert pick_list.fetch_order("does-not-exist") is None


def test_fetch_order_reraises_non_404():
    def raise_500(method, path, creds=None, marketplace=None, body=None):
        raise EbayAPIError(500, "boom", "{}")

    with _Patched({(pick_list, "api_send"): raise_500}):
        try:
            pick_list.fetch_order("x")
            raise AssertionError("expected EbayAPIError")
        except EbayAPIError as e:
            assert e.status == 500


def test_build_shipping_fulfillment_body_defaults_to_all_line_items():
    o = _order()
    o["lineItems"].append({**o["lineItems"][0], "lineItemId": "second-li", "sku": "other"})
    body = pick_list.build_shipping_fulfillment_body(o, "USPS", "9400111899560000000000")
    assert body["shippingCarrierCode"] == "USPS"
    assert body["trackingNumber"] == "9400111899560000000000"
    assert {li["lineItemId"] for li in body["lineItems"]} == {"11500017010", "second-li"}
    assert body["shippedDate"].endswith("Z")


def test_build_shipping_fulfillment_body_filters_to_requested_line_items():
    o = _order()
    o["lineItems"].append({**o["lineItems"][0], "lineItemId": "second-li"})
    body = pick_list.build_shipping_fulfillment_body(o, "UPS", "1Z999", ["second-li"])
    assert [li["lineItemId"] for li in body["lineItems"]] == ["second-li"]


def test_record_tracking_posts_the_built_body_and_returns_order_plus_response():
    o = _order()
    posted = {}

    def fake_fetch_order(order_id):
        assert order_id == o["orderId"]
        return o

    def fake_api_send(method, path, creds=None, marketplace=None, body=None):
        assert method == "POST"
        assert path == f"/sell/fulfillment/v1/order/{o['orderId']}/shipping_fulfillment"
        posted["body"] = body
        return {"fulfillmentId": "fid-1"}

    with _Patched({(pick_list, "fetch_order"): fake_fetch_order,
                      (pick_list, "api_send"): fake_api_send}):
        result = pick_list.record_tracking(o["orderId"], "USPS", "TRACK123")
    assert posted["body"]["trackingNumber"] == "TRACK123"
    assert result["response"]["fulfillmentId"] == "fid-1"
    assert result["order"] is o


def test_record_tracking_raises_on_unknown_order():
    with _Patched({(pick_list, "fetch_order"): lambda order_id: None}):
        try:
            pick_list.record_tracking("ghost", "USPS", "T1")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


# --------------------------------------------------------------------------- #
# advance_ledger_for_order — real listings_ledger.csv write, via the same
# EBAYBIZ_LISTINGS_LEDGER override list_edit.upsert_listing already honours
# (lib/list_edit.py's own pattern for advancing status, e.g. from
# sync_actuals.mark_sold_in_ledger's SOLD transition).
# --------------------------------------------------------------------------- #
def test_advance_ledger_for_order_marks_every_sku_shipped():
    o = _order(sku="ledger-sku-1")
    o["lineItems"].append({**o["lineItems"][0], "sku": "", "lineItemId": "no-sku-li"})
    tmp = Path(tempfile.mkdtemp(prefix="pick_list_ledger_"))
    ledger_path = tmp / "listings_ledger.csv"
    old_env = os.environ.get("EBAYBIZ_LISTINGS_LEDGER")
    os.environ["EBAYBIZ_LISTINGS_LEDGER"] = str(ledger_path)
    try:
        n = pick_list.advance_ledger_for_order(o)
        assert n == 1  # the line item with no SKU is skipped, not counted
        rows = list(__import__("csv").DictReader(ledger_path.open(newline="", encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["sku"] == "ledger-sku-1"
        assert rows[0]["status"] == "SHIPPED"
        assert rows[0]["listing_id"] == "206000000001"
        assert rows[0]["shipped_at"]  # advance stamped a timestamp, not just status
    finally:
        if old_env is None:
            os.environ.pop("EBAYBIZ_LISTINGS_LEDGER", None)
        else:
            os.environ["EBAYBIZ_LISTINGS_LEDGER"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# buy_shipping_label — must stay a hard stop, never a live purchase path
# --------------------------------------------------------------------------- #
def test_buy_shipping_label_is_not_implemented():
    try:
        pick_list.buy_shipping_label()
        raise AssertionError("label purchase must not be reachable")
    except NotImplementedError as e:
        assert "confirmation" in str(e)


# --------------------------------------------------------------------------- #
# cmd_record_tracking — dry-run vs --confirm gate
# --------------------------------------------------------------------------- #
class _Args:
    def __init__(self, **kw):
        self.record_tracking = kw.get("record_tracking")
        self.carrier = kw.get("carrier")
        self.tracking_number = kw.get("tracking_number")
        self.confirm = kw.get("confirm", False)


def test_cmd_record_tracking_dry_run_never_posts_or_advances_ledger():
    o = _order()
    posted = []
    advanced = []

    with _Patched({
        (pick_list, "fetch_order"): lambda order_id: o,
        (pick_list, "api_send"): lambda *a, **k: posted.append(1) or {},
        (pick_list, "advance_ledger_for_order"): lambda order: advanced.append(1) or 1,
    }):
        rc = pick_list.cmd_record_tracking(
            _Args(record_tracking=o["orderId"], carrier="USPS",
                 tracking_number="T1", confirm=False))
    assert rc == 0
    assert posted == []
    assert advanced == []


def test_cmd_record_tracking_confirm_posts_and_advances_ledger():
    o = _order()
    posted = []
    advanced = []
    _, state_file = _scratch_dirs()

    with _Patched({
        (pick_list, "fetch_order"): lambda order_id: o,
        (pick_list, "api_send"): lambda *a, **k: posted.append(1) or {"fulfillmentId": "f1"},
        (pick_list, "advance_ledger_for_order"): lambda order: advanced.append(1) or 1,
        (pick_list, "STATE_FILE"): state_file,
    }):
        rc = pick_list.cmd_record_tracking(
            _Args(record_tracking=o["orderId"], carrier="USPS",
                 tracking_number="T1", confirm=True))
    try:
        assert rc == 0
        assert posted == [1]
        assert advanced == [1]
        assert state_file.exists()
    finally:
        shutil.rmtree(state_file.parent, ignore_errors=True)


def test_cmd_record_tracking_missing_carrier_is_rejected_before_any_call():
    calls = []
    with _Patched({(pick_list, "fetch_order"): lambda order_id: calls.append(1)}):
        rc = pick_list.cmd_record_tracking(
            _Args(record_tracking="x", carrier=None, tracking_number="T1", confirm=True))
    assert rc == 2
    assert calls == []


def test_cmd_record_tracking_confirm_reuses_record_tracking_not_a_second_post():
    """cmd_record_tracking must go through record_tracking() for the real write
    (not re-fetch/re-POST inline) — one call to fetch_order, one POST, no
    drift between what --record-tracking does and what record_tracking() is
    tested to do."""
    o = _order()
    fetch_calls = []
    post_calls = []

    def fake_fetch_order(order_id):
        fetch_calls.append(order_id)
        return o

    def fake_api_send(method, path, creds=None, marketplace=None, body=None):
        post_calls.append(path)
        return {"fulfillmentId": "f1"}

    _, state_file = _scratch_dirs()
    try:
        with _Patched({
            (pick_list, "fetch_order"): fake_fetch_order,
            (pick_list, "api_send"): fake_api_send,
            (pick_list, "advance_ledger_for_order"): lambda order: 1,
            (pick_list, "STATE_FILE"): state_file,
        }):
            rc = pick_list.cmd_record_tracking(
                _Args(record_tracking=o["orderId"], carrier="USPS",
                     tracking_number="T1", confirm=True))
        assert rc == 0
        assert len(fetch_calls) == 1  # not fetched twice (once for the dry-run body, once again for the real write)
        assert len(post_calls) == 1
    finally:
        shutil.rmtree(state_file.parent, ignore_errors=True)


def test_cmd_record_tracking_confirm_surfaces_api_error_instead_of_raising():
    o = _order()

    def raise_400(method, path, creds=None, marketplace=None, body=None):
        raise EbayAPIError(400, "bad tracking number", '{"errors":[]}')

    with _Patched({
        (pick_list, "fetch_order"): lambda order_id: o,
        (pick_list, "api_send"): raise_400,
    }):
        rc = pick_list.cmd_record_tracking(
            _Args(record_tracking=o["orderId"], carrier="USPS",
                 tracking_number="T1", confirm=True))
    assert rc == 1  # reported, not a raw traceback


# --------------------------------------------------------------------------- #
# main() — --poll and --record-tracking are separate modes
# --------------------------------------------------------------------------- #
def test_main_rejects_poll_and_record_tracking_together():
    old_argv = sys.argv
    sys.argv = ["pick_list.py", "--poll", "--record-tracking", "x",
               "--carrier", "USPS", "--tracking-number", "T1"]
    try:
        try:
            pick_list.main()
            raise AssertionError("expected argparse to reject the combination")
        except SystemExit as e:
            assert e.code == 2
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------- #
# --poll publishes a LINK per shipment (#151)
#
# pick_list_html.render_html is faked throughout: it reads hero photos out of
# inventory/ and pulls in PIL/numpy, none of which this file has any business
# touching. What is under test is the wiring — that a poll publishes, that a
# re-poll reuses, that an expired link is replaced, and that the seller-only
# .txt never becomes a URL.
# --------------------------------------------------------------------------- #
def _pick_store_at(tmp: Path):
    """pick_store bound to a throwaway directory, so no test can publish into
    (or purge) the real pick_lists/.served/."""
    import pick_store
    store = tmp / "served"

    class _Bound:
        TTL_HOURS_DEFAULT = pick_store.TTL_HOURS_DEFAULT
        SERVE_HINT = pick_store.SERVE_HINT
        store_dir = store

        @staticmethod
        def publish(html, order_ids=None, ttl_hours=pick_store.TTL_HOURS_DEFAULT):
            return pick_store.publish(html, order_ids=order_ids,
                                      ttl_hours=ttl_hours, store_dir=store)

        @staticmethod
        def live_sheet_for(order_ids):
            return pick_store.live_sheet_for(order_ids, store_dir=store)

        @staticmethod
        def fetch(token):
            return pick_store.fetch(token, store_dir=store)

        @staticmethod
        def server_is_up(*a, **k):
            return True

    return _Bound


class _FakeModules:
    """Put fake `pick_store` / `pick_list_html` in sys.modules for the
    duration of a `with` — poll_and_print imports both lazily, by name."""

    def __init__(self, **mods):
        self._mods = mods
        self._saved = {}

    def __enter__(self):
        for name, mod in self._mods.items():
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = mod
        return self

    def __exit__(self, *exc):
        for name, old in self._saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        return False


class _FakeHtml:
    rendered = []

    @classmethod
    def render_html(cls, orders, drafts, ledger):
        cls.rendered.append([o.get("orderId") for o in orders])
        who = orders[0]["fulfillmentStartInstructions"][0]["shippingStep"]["shipTo"]["fullName"]
        return f"<html><body>{who}</body></html>"


def _no_inventory():
    """The two lookups poll_and_print makes into local inventory/ledger."""
    return _Patched({(pick_list, "scan_drafts"): lambda: [],
                     (pick_list, "load_listings_ledger"): lambda: []})


def test_poll_publishes_a_link_per_new_order():
    out_dir, _ = _scratch_dirs()
    store = _pick_store_at(out_dir.parent)
    _FakeHtml.rendered = []
    orders = [_order(oid="link-1")]
    try:
        with _no_inventory():
            with _FakeModules(pick_store=store, pick_list_html=_FakeHtml):
                new_ids, _, state, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state={"printed": {}, "shipped": {}},
                    fetch=lambda: orders)
        entry = state["printed"]["link-1"]
        assert entry["url"].startswith("http://127.0.0.1:8770/pick/")
        assert entry["token"] and entry["expires_at"]
        assert _FakeHtml.rendered == [["link-1"]]
        # the link really resolves to this buyer's sheet
        html, status = store.fetch(entry["token"])
        assert status == "ok" and "Jamie Buyer" in html
        assert new_ids == ["link-1"]
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_second_poll_hands_back_the_same_link_without_republishing():
    out_dir, _ = _scratch_dirs()
    store = _pick_store_at(out_dir.parent)
    _FakeHtml.rendered = []
    orders = [_order(oid="link-2")]
    try:
        with _no_inventory():
            with _FakeModules(pick_store=store, pick_list_html=_FakeHtml):
                _, _, state, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state={"printed": {}, "shipped": {}},
                    fetch=lambda: orders)
                first = dict(state["printed"]["link-2"])
                new_ids2, skipped2, state2, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state=state, fetch=lambda: orders)
        assert new_ids2 == [] and skipped2 == ["link-2"]
        assert state2["printed"]["link-2"]["token"] == first["token"]
        assert state2["printed"]["link-2"]["url"] == first["url"]
        assert _FakeHtml.rendered == [["link-2"]]      # rendered once, not twice
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_an_expired_link_is_republished_even_though_the_order_was_printed():
    out_dir, _ = _scratch_dirs()
    store = _pick_store_at(out_dir.parent)
    _FakeHtml.rendered = []
    orders = [_order(oid="link-3")]
    try:
        with _no_inventory():
            with _FakeModules(pick_store=store, pick_list_html=_FakeHtml):
                # first poll publishes something already past its TTL
                _, _, state, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state={"printed": {}, "shipped": {}},
                    fetch=lambda: orders, ttl_hours=-1)
                dead_token = state["printed"]["link-3"]["token"]
                _, skipped, state2, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state=state, fetch=lambda: orders)
        assert skipped == ["link-3"]                   # not re-printed
        fresh = state2["printed"]["link-3"]["token"]
        assert fresh != dead_token                     # but re-published
        assert store.fetch(fresh)[1] == "ok"
        assert len(_FakeHtml.rendered) == 2
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_no_links_mode_publishes_nothing():
    out_dir, _ = _scratch_dirs()
    store = _pick_store_at(out_dir.parent)
    orders = [_order(oid="link-4")]
    try:
        with _no_inventory():
            with _FakeModules(pick_store=store, pick_list_html=_FakeHtml):
                _, _, state, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state={"printed": {}, "shipped": {}},
                    fetch=lambda: orders, publish=False)
        assert "url" not in state["printed"]["link-4"]
        assert not (out_dir.parent / "served").exists()
        assert (out_dir / "pick_link-4.txt").exists()   # the local sheet still lands
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_a_publish_failure_never_breaks_the_poll():
    out_dir, _ = _scratch_dirs()
    orders = [_order(oid="link-5")]

    class _Boom:
        TTL_HOURS_DEFAULT = 48

        @staticmethod
        def publish(*a, **k):
            raise RuntimeError("store is on fire")

        @staticmethod
        def live_sheet_for(order_ids):
            return None

    try:
        with _no_inventory():
            with _FakeModules(pick_store=_Boom, pick_list_html=_FakeHtml):
                new_ids, _, state, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state={"printed": {}, "shipped": {}},
                    fetch=lambda: orders)
        assert new_ids == ["link-5"]
        assert "url" not in state["printed"]["link-5"]   # no stale/fake link recorded
        assert (out_dir / "pick_link-5.txt").exists()    # the fallback is the file
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_the_published_sheet_is_the_buyer_safe_one_not_the_seller_txt():
    # tools/pick_list.py's render() shows buyer-paid shipping and net payout;
    # the HTML sheet does not. Only the latter may ever get a URL (#84/#151).
    out_dir, _ = _scratch_dirs()
    store = _pick_store_at(out_dir.parent)
    orders = [_order(oid="link-6")]
    try:
        with _no_inventory():
            with _FakeModules(pick_store=store, pick_list_html=_FakeHtml):
                _, _, state, _ = pick_list.poll_and_print(
                    out_dir=out_dir, state={"printed": {}, "shipped": {}},
                    fetch=lambda: orders)
        served = store.fetch(state["printed"]["link-6"]["token"])[0]
        local_txt = (out_dir / "pick_link-6.txt").read_text(encoding="utf-8")
        assert "you keep" in local_txt.lower()     # seller copy shows the payout
        assert "buyer paid" in local_txt.lower()   # ...and what shipping earned
        assert "you keep" not in served.lower()    # the link shows neither
        assert "buyer paid" not in served.lower()
    finally:
        shutil.rmtree(out_dir.parent, ignore_errors=True)
