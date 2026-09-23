#!/usr/bin/env python3
"""tools/pick_list_html.py — link-not-file pick sheets (GH #151).

Covers exactly the acceptance list from the issue: the upload is called once
per shipment, a mismatched-shipment refusal short-circuits before any upload
is attempted, --poll's state file is reused (no re-upload) across a second
poll while a link is still live, and an upload failure falls back to the
local file with a non-zero exit instead of silently succeeding.

R2 itself is never touched — lib/temp_storage.upload_pick_sheet /
revoke_key are monkeypatched directly, the same "no network" convention as
tests/test_pick_list.py's _Patched helper for eBay's API.

Run:  python tests/test_pick_list_html.py
  or: pytest tests/test_pick_list_html.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

plh = pytest.importorskip(
    "pick_list_html", reason="pick_list_html imports numpy/PIL for the thumbnail")
from temp_storage import R2Error                                       # noqa: E402


class _Patched:
    """Set module attributes for the duration of a `with`, restoring after —
    same shape as tests/test_pick_list.py's _Patched."""

    def __init__(self, targets: dict):
        self._targets = targets
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


def _money(v):
    return {"value": str(v), "currency": "USD"}


def _order(*, oid="03-11111-22222", fullname="Jamie Buyer",
           addr1="1 Test Way", city="Springfield", state="OH", zipc="45501"):
    return {
        "orderId": oid,
        "legacyOrderId": oid.replace("-", ""),
        "salesRecordReference": "1",
        "creationDate": "2026-08-20T12:00:00.000Z",
        "orderPaymentStatus": "PAID",
        "lineItems": [{
            "lineItemId": "11500017010", "legacyItemId": "206000000001",
            "sku": "abc123def4", "title": "McCoy Beehive Mixing Bowl",
            "quantity": 1, "lineItemCost": _money(24.99),
            "lineItemFulfillmentInstructions": {"shipByDate": "2026-08-24T00:00:00.000Z"},
        }],
        "fulfillmentStartInstructions": [{
            "maxEstimatedDeliveryDate": "2026-08-29T00:00:00.000Z",
            "shippingStep": {
                "shippingCarrierCode": "USPS", "shippingServiceCode": "USPSGround",
                "shipTo": {"fullName": fullname, "contactAddress": {
                    "addressLine1": addr1, "city": city,
                    "stateOrProvince": state, "postalCode": zipc, "countryCode": "US"}},
            },
        }],
        "pricingSummary": {"total": _money(24.99), "deliveryCost": _money(0)},
        "paymentSummary": {"totalDueSeller": _money(21.99)},
    }


def _tmp_out_path():
    d = Path(tempfile.mkdtemp(prefix="pick_list_html_test_"))
    return d, d / "pick_lists" / "pick_test.html"


# --------------------------------------------------------------------------- #
# render_html — the new posture's page-level guarantee
# --------------------------------------------------------------------------- #
def test_render_html_carries_a_noindex_meta_tag():
    out = plh.render_html([_order()], [], [])
    assert '<meta name="robots" content="noindex, nofollow">' in out


# --------------------------------------------------------------------------- #
# render_and_ship — upload called once per shipment; local file always
# written as a byproduct regardless of upload outcome
# --------------------------------------------------------------------------- #
def test_render_and_ship_uploads_exactly_once_and_returns_the_link():
    tmp, out_path = _tmp_out_path()
    calls = []

    def fake_upload(html_text, ttl_hours):
        calls.append((html_text, ttl_hours))
        return {"configured": True, "url": "https://example.r2.dev/pick-sheets/x.html",
                "key": "pick-sheets/x.html", "expires_at": "2026-08-22T00:00:00+00:00"}

    try:
        with _Patched({(plh, "upload_pick_sheet"): fake_upload}):
            result = plh.render_and_ship([_order()], [], [], out_path, 48)
        assert len(calls) == 1
        assert calls[0][1] == 48
        assert result["configured"] is True
        assert result["url"] == "https://example.r2.dev/pick-sheets/x.html"
        assert out_path.exists()
        assert "Jamie Buyer" in out_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_render_and_ship_falls_back_to_local_file_on_upload_error():
    tmp, out_path = _tmp_out_path()

    def fake_upload(html_text, ttl_hours):
        raise R2Error("R2 PUT ... unreachable: [Errno -2] Name or service not known")

    try:
        with _Patched({(plh, "upload_pick_sheet"): fake_upload}):
            result = plh.render_and_ship([_order()], [], [], out_path, 48)
        assert "error" in result
        assert out_path.exists()  # local fallback still written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_render_and_ship_reports_not_configured_without_raising():
    tmp, out_path = _tmp_out_path()

    def fake_upload(html_text, ttl_hours):
        return {"configured": False}

    try:
        with _Patched({(plh, "upload_pick_sheet"): fake_upload}):
            result = plh.render_and_ship([_order()], [], [], out_path, 48)
        assert result == {"configured": False}
        assert out_path.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# main() — mismatched shipment refuses BEFORE any upload is attempted
# --------------------------------------------------------------------------- #
def test_refusal_short_circuits_before_any_upload():
    upload_calls = []

    def fake_upload(html_text, ttl_hours):
        upload_calls.append(1)
        return {"configured": False}

    a = _order(oid="a-1", fullname="Jamie Buyer", addr1="1 Test Way")
    b = _order(oid="a-2", fullname="Someone Else", addr1="99 Other Ave")

    old_argv = sys.argv
    sys.argv = ["pick_list_html.py", "a-1", "a-2"]
    try:
        with _Patched({
            (plh, "upload_pick_sheet"): fake_upload,
            (plh, "fetch_orders"): lambda days, verbose=False: [a, b],
            (plh, "scan_drafts"): lambda: [],
            (plh, "load_listings_ledger"): lambda: [],
        }):
            try:
                plh.main()
                raise AssertionError("expected assert_one_shipment to refuse")
            except SystemExit as e:
                assert "REFUSED" in str(e)
    finally:
        sys.argv = old_argv
    assert upload_calls == []  # no half-uploaded object left behind


# --------------------------------------------------------------------------- #
# --poll — state-file reuse (idempotent) vs. --reprint / expiry forcing a
# fresh upload
# --------------------------------------------------------------------------- #
class _PollArgs:
    def __init__(self, ttl=48, reprint=None):
        self.ttl = ttl
        self.reprint = reprint


def test_poll_skips_an_order_whose_link_is_still_live():
    o = _order(oid="live-1")
    upload_calls = []

    def fake_upload(html_text, ttl_hours):
        upload_calls.append(1)
        return {"configured": True, "url": "https://x/should-not-be-called",
                "key": "pick-sheets/should-not-be-called.html",
                "expires_at": "2099-01-01T00:00:00+00:00"}

    state = {"html": {"live-1": {"url": "https://x/live", "key": "pick-sheets/live.html",
                                 "expires_at": "2099-01-01T00:00:00+00:00"}}}
    tmp, _ = _tmp_out_path()
    try:
        with _Patched({
            (plh.pick_list, "fetch_open"): lambda: [o],
            (plh, "scan_drafts"): lambda: [],
            (plh, "load_listings_ledger"): lambda: [],
            (plh.pick_list, "_load_state"): lambda: state,
            (plh.pick_list, "_save_state"): lambda s: None,
            (plh, "OUT_DIR"): tmp / "pick_lists",
            (plh, "upload_pick_sheet"): fake_upload,
        }):
            rc = plh.cmd_poll(_PollArgs())
        assert rc == 0
        assert upload_calls == []  # still live -- never re-uploaded
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_poll_uploads_a_brand_new_order_and_records_its_link():
    o = _order(oid="new-1")
    upload_calls = []

    def fake_upload(html_text, ttl_hours):
        upload_calls.append(1)
        return {"configured": True, "url": "https://x/new-1", "key": "pick-sheets/new-1.html",
                "expires_at": "2099-01-01T00:00:00+00:00"}

    state = {"html": {}}
    saved = {}
    tmp, _ = _tmp_out_path()
    try:
        with _Patched({
            (plh.pick_list, "fetch_open"): lambda: [o],
            (plh, "scan_drafts"): lambda: [],
            (plh, "load_listings_ledger"): lambda: [],
            (plh.pick_list, "_load_state"): lambda: state,
            (plh.pick_list, "_save_state"): lambda s: saved.update(s),
            (plh, "OUT_DIR"): tmp / "pick_lists",
            (plh, "upload_pick_sheet"): fake_upload,
        }):
            rc = plh.cmd_poll(_PollArgs())
        assert rc == 0
        assert len(upload_calls) == 1
        assert saved["html"]["new-1"]["url"] == "https://x/new-1"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_poll_reprint_forces_a_fresh_upload_and_revokes_the_old_object():
    o = _order(oid="dup-1")
    upload_calls, revoke_calls = [], []

    def fake_upload(html_text, ttl_hours):
        upload_calls.append(1)
        return {"configured": True, "url": "https://x/dup-1-v2", "key": "pick-sheets/dup-1-v2.html",
                "expires_at": "2099-01-01T00:00:00+00:00"}

    def fake_revoke(key):
        revoke_calls.append(key)

    state = {"html": {"dup-1": {"url": "https://x/dup-1-v1", "key": "pick-sheets/dup-1-v1.html",
                                "expires_at": "2099-01-01T00:00:00+00:00"}}}
    tmp, _ = _tmp_out_path()
    try:
        with _Patched({
            (plh.pick_list, "fetch_open"): lambda: [o],
            (plh, "scan_drafts"): lambda: [],
            (plh, "load_listings_ledger"): lambda: [],
            (plh.pick_list, "_load_state"): lambda: state,
            (plh.pick_list, "_save_state"): lambda s: None,
            (plh, "OUT_DIR"): tmp / "pick_lists",
            (plh, "upload_pick_sheet"): fake_upload,
            (plh, "revoke_key"): fake_revoke,
        }):
            rc = plh.cmd_poll(_PollArgs(reprint="dup-1"))
        assert rc == 0
        assert len(upload_calls) == 1
        assert revoke_calls == ["pick-sheets/dup-1-v1.html"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_poll_upload_failure_is_reported_and_does_not_stop_the_batch():
    o1, o2 = _order(oid="fail-1"), _order(oid="ok-2")
    calls = {"n": 0}

    def scripted_upload(html_text, ttl_hours):
        calls["n"] += 1
        if calls["n"] == 1:
            raise R2Error("upload boom")
        return {"configured": True, "url": "https://x/ok-2", "key": "pick-sheets/ok-2.html",
                "expires_at": "2099-01-01T00:00:00+00:00"}

    state = {"html": {}}
    saved = {}
    tmp, _ = _tmp_out_path()
    try:
        with _Patched({
            (plh.pick_list, "fetch_open"): lambda: [o1, o2],
            (plh, "scan_drafts"): lambda: [],
            (plh, "load_listings_ledger"): lambda: [],
            (plh.pick_list, "_load_state"): lambda: state,
            (plh.pick_list, "_save_state"): lambda s: saved.update(s),
            (plh, "OUT_DIR"): tmp / "pick_lists",
            (plh, "upload_pick_sheet"): scripted_upload,
        }):
            rc = plh.cmd_poll(_PollArgs())
        assert rc == 1  # a failure happened -- must not report success
        assert "fail-1" not in saved.get("html", {})
        assert saved["html"]["ok-2"]["url"] == "https://x/ok-2"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# --revoke
# --------------------------------------------------------------------------- #
def test_cmd_revoke_deletes_and_forgets_the_order():
    state = {"html": {"rev-1": {"url": "https://x/rev-1", "key": "pick-sheets/rev-1.html",
                                "expires_at": "2099-01-01T00:00:00+00:00"}}}
    saved = {}
    revoke_calls = []

    with _Patched({
        (plh.pick_list, "_load_state"): lambda: state,
        (plh.pick_list, "_save_state"): lambda s: saved.update(s),
        (plh, "revoke_key"): lambda key: revoke_calls.append(key),
    }):
        rc = plh.cmd_revoke("rev-1")
    assert rc == 0
    assert revoke_calls == ["pick-sheets/rev-1.html"]
    assert "rev-1" not in saved["html"]


def test_cmd_revoke_reports_when_nothing_is_on_record():
    with _Patched({(plh.pick_list, "_load_state"): lambda: {"html": {}}}):
        rc = plh.cmd_revoke("ghost")
    assert rc == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
