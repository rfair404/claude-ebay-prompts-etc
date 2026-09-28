#!/usr/bin/env python3
"""lib/vinted_client.py — signing, credentials, and the write guardrail, offline.

The two things that must hold before this client ever meets a real account:
the signature matches Vinted's documented recipe byte for byte, and no
account-changing call leaves the machine without confirm=True. Every HTTP
call is faked by patching urllib.request.urlopen.

Run:  python tests/test_vinted_client.py
  or: pytest tests/test_vinted_client.py
"""
import hashlib
import hmac
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import vinted_client as V                                              # noqa: E402
import config as CFG                                                   # noqa: E402
from config import ConfigError                                         # noqa: E402

CREDS = V.VintedCredentials(environment="sandbox", access_key="AK123",
                            signing_key="SK456", store="default")


class _Resp:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Fake:
    """Records every request; answers from a script (last entry repeats)."""
    def __init__(self, *script):
        self.script = list(script) or [b"{}"]
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return _Resp(item if isinstance(item, bytes) else json.dumps(item).encode())


def _patch(fake):
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    return lambda: setattr(urllib.request, "urlopen", orig)


def _patch_config(cfg: dict):
    orig = CFG.load_config
    CFG.load_config = lambda reload=False: cfg
    V.load_config = CFG.load_config
    saved = {k: os.environ.pop(k, None)
             for k in (V.TOKEN_ENV_VAR, V.ENVIRONMENT_ENV_VAR, "EBAYBIZ_STORE")}

    def undo():
        CFG.load_config = orig
        V.load_config = orig
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
    return undo


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def test_sign_matches_documented_recipe():
    body = '{"event_types":["CREATE_ITEM_SUCCESS"],"url":"https://example.com"}'
    got = V.sign("SK456", "AK123", "post", "/api/v1/webhooks?foo=bar", body,
                 timestamp=1704067200)
    payload = f"1704067200.POST./api/v1/webhooks?foo=bar.AK123.{body}"
    want = hmac.new(b"SK456", payload.encode(), hashlib.sha256).hexdigest()
    assert got == f"t=1704067200,v1={want}"


def test_sign_empty_body_ends_with_trailing_dot():
    got = V.sign("SK456", "AK123", "GET", "/api/v1/orders", "", timestamp=1)
    want = hmac.new(b"SK456", b"1.GET./api/v1/orders.AK123.", hashlib.sha256).hexdigest()
    assert got == f"t=1,v1={want}"


def test_request_signs_exact_bytes_sent_including_query():
    fake = _Fake({"items": []})
    undo = _patch(fake)
    try:
        V.list_items(after_item_id="abc", limit=5, creds=CREDS)
        V.validate_items([{"title": "Ünïcode tïtle"}], creds=CREDS)
    finally:
        undo()
    get, post = fake.calls
    assert get.full_url == ("https://pro-public-sandbox.svc.vinted.com"
                            "/api/v1/items?after_item_id=abc&limit=5")
    for req in (get, post):
        h = {k.lower(): v for k, v in req.header_items()}
        assert h["x-vpi-access-key"] == "AK123"
        t = h["x-vpi-hmac-sha256"].split(",")[0][2:]
        path = req.full_url.split(".com", 1)[1]
        body = (req.data or b"").decode("utf-8")
        assert h["x-vpi-hmac-sha256"] == V.sign("SK456", "AK123", req.get_method(),
                                                path, body, timestamp=int(t))
    assert json.loads(post.data) == {"items": [{"title": "Ünïcode tïtle"}]}


def test_none_query_params_are_dropped():
    fake = _Fake({"orders": []})
    undo = _patch(fake)
    try:
        V.list_orders(creds=CREDS)
        V.list_orders(after_id=42, creds=CREDS)
    finally:
        undo()
    assert fake.calls[0].full_url.endswith("/api/v1/orders")
    assert fake.calls[1].full_url.endswith("/api/v1/orders?after-id=42")


def test_verify_webhook():
    body = b'{"event":"ITEM_SOLD"}'
    sig = hmac.new(b"WHK", b"1000." + body, hashlib.sha256).hexdigest()
    hdr = f"t=1000,v1={sig}"
    assert V.verify_webhook("WHK", hdr, body, now=1010)
    assert not V.verify_webhook("WHK", hdr, body + b" ", now=1010)
    assert not V.verify_webhook("other", hdr, body, now=1010)
    assert not V.verify_webhook("WHK", hdr, body, now=5000)          # replay window
    assert V.verify_webhook("WHK", hdr, body, tolerance_s=None, now=5000)
    assert not V.verify_webhook("WHK", "garbage", body)


# ---------------------------------------------------------------------------
# Guardrail — no account-changing request without confirm=True
# ---------------------------------------------------------------------------

def test_writes_are_dry_runs_without_confirm():
    fake = _Fake()
    undo = _patch(fake)
    try:
        results = [
            V.create_items([{"title": "x"}], creds=CREDS),
            V.update_items([{"item_id": "u1"}], creds=CREDS),
            V.delete_items(["u1"], creds=CREDS),
            V.cancel_order("9", "buyer asked", creds=CREDS),
            V.relist_orders([9], creds=CREDS),
            V.create_webhook("https://h", ["ITEM_SOLD"], creds=CREDS),
            V.delete_webhook("w1", creds=CREDS),
        ]
    finally:
        undo()
    assert fake.calls == []
    assert all(isinstance(r, V.DryRun) for r in results)
    assert results[2].body == {"item_ids": ["u1"]}
    assert results[3].body == {"cancellation_reason_explanation": "buyer asked"}


def test_create_forces_draft_unless_publish():
    dry = V.create_items([{"title": "x", "is_draft": False}], creds=CREDS)
    assert dry.body["items"][0]["is_draft"] is True
    dry = V.create_items([{"title": "x"}], publish=True, creds=CREDS)
    assert "is_draft" not in dry.body["items"][0]


def test_confirmed_create_posts_once_even_on_network_error():
    err = urllib.error.URLError("reset")
    fake = _Fake(err)
    undo = _patch(fake)
    try:
        try:
            V.create_items([{"title": "x"}], confirm=True, creds=CREDS)
            raise AssertionError("expected VintedAPIError")
        except V.VintedAPIError:
            pass
    finally:
        undo()
    assert len(fake.calls) == 1                       # never double-creates
    assert fake.calls[0].get_method() == "POST"
    assert json.loads(fake.calls[0].data)["items"][0]["is_draft"] is True


def test_batch_cap_and_empty():
    for bad in ([], [{}] * 101):
        try:
            V.create_items(bad, creds=CREDS)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_cancel_requires_short_reason():
    for bad in ("", "x" * 101):
        try:
            V.cancel_order("1", bad, creds=CREDS)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_unknown_webhook_event_rejected():
    try:
        V.create_webhook("https://h", ["NOT_AN_EVENT"], creds=CREDS)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_dev_trigger_refuses_production():
    prod = V.VintedCredentials("production", "AK", "SK")
    fake = _Fake()
    undo = _patch(fake)
    try:
        try:
            V.dev_trigger_item_sold("i1", creds=prod)
            raise AssertionError("expected VintedAPIError")
        except V.VintedAPIError:
            pass
    finally:
        undo()
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Transport errors
# ---------------------------------------------------------------------------

def _http(code):
    return urllib.error.HTTPError("https://x", code, "err", None, io.BytesIO(b'{"e":1}'))


def test_401_is_auth_error_no_retry():
    fake = _Fake(_http(401))
    undo = _patch(fake)
    try:
        try:
            V.get_ontologies(creds=CREDS)
            raise AssertionError("expected VintedAuthError")
        except V.VintedAuthError:
            pass
    finally:
        undo()
    assert len(fake.calls) == 1


def test_get_retries_5xx_then_succeeds():
    fake = _Fake(_http(503), {"ok": True})
    undo = _patch(fake)
    orig_sleep = V.time.sleep
    V.time.sleep = lambda s: None
    try:
        assert V.get_ontologies(creds=CREDS) == {"ok": True}
    finally:
        V.time.sleep = orig_sleep
        undo()
    assert len(fake.calls) == 2


def test_label_returns_raw_bytes():
    fake = _Fake(b"%PDF-1.4 ...")
    undo = _patch(fake)
    try:
        assert V.shipment_label_pdf("7", creds=CREDS) == b"%PDF-1.4 ..."
    finally:
        undo()
    assert fake.calls[0].full_url.endswith("/api/v1/orders/7/shipment-label")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_credentials_default_store_sandbox_default():
    undo = _patch_config({"vinted": {"sandbox": {"token": "ak , sk"}}})
    try:
        c = V.load_credentials()
    finally:
        undo()
    assert (c.environment, c.access_key, c.signing_key, c.store) == \
        ("sandbox", "ak", "sk", "default")
    assert "sk" not in repr(c)


def test_credentials_named_store_and_production():
    undo = _patch_config({"vinted": {"stores": {"junk": {
        "environment": "production", "production": {"token": "a,b"}}}}})
    try:
        c = V.load_credentials("junk")
        assert c.base_url == "https://pro.svc.vinted.com" and c.store == "junk"
        try:
            V.load_credentials("nope")
            raise AssertionError("expected ConfigError")
        except ConfigError as e:
            assert "junk" in str(e)
    finally:
        undo()


def test_credentials_env_override_and_errors():
    undo = _patch_config({})
    try:
        try:
            V.load_credentials()
            raise AssertionError("expected ConfigError")
        except ConfigError as e:
            assert "pro-portal" in str(e)
        os.environ[V.TOKEN_ENV_VAR] = "x,y"
        assert V.load_credentials().access_key == "x"
        os.environ[V.TOKEN_ENV_VAR] = "no-comma"
        try:
            V.load_credentials()
            raise AssertionError("expected ConfigError")
        except ConfigError:
            pass
        os.environ[V.ENVIRONMENT_ENV_VAR] = "live"
        try:
            V.load_credentials()
            raise AssertionError("expected ConfigError")
        except ConfigError:
            pass
    finally:
        undo()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:                                     # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if fails else 0)
