#!/usr/bin/env python3
"""lib/ebay_client.py — the money path, tested offline (GH #30).

Every listing create/update/publish and every ledger reconcile goes through
this module, and until now none of it had tests: token caching, the retry
policy, and — most important — how a non-2xx surfaces. The live 400s we've
debugged (UPC checksum, missing Model aspect, transient republish 400) were
all diagnosed from EbayAPIError.body, so that body surviving intact is a
contract, not a nicety.

All HTTP is faked by patching urllib.request.urlopen; no network, no creds.

Run:  python tests/test_ebay_client.py
  or: pytest tests/test_ebay_client.py
"""
import contextlib
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

import ebay_client  # noqa: E402
from ebay_client import (  # noqa: E402
    EbayAPIError,
    EbayAuthError,
    EbayCredentials,
    api_get,
    api_send,
    get_app_access_token,
    get_user_access_token,
    load_credentials,
)

CREDS = EbayCredentials(environment="sandbox", app_id="app-x", cert_id="cert-x",
                        user_refresh_token="refresh-x")
APP_ONLY = EbayCredentials(environment="sandbox", app_id="app-x", cert_id="cert-x")
NO_CREDS = EbayCredentials(environment="sandbox")


class _FakeResponse:
    def __init__(self, payload, raw=None):
        self._raw = raw if raw is not None else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=b'{"errors":[{"message":"boom"}]}'):
    return urllib.error.HTTPError("https://x", code, f"HTTP {code}",
                                  None, io.BytesIO(body))


class _Fake:
    """Scripted stand-in for urllib.request.urlopen. Each entry in `script`
    is a _FakeResponse to return or an exception to raise; requests beyond
    the script repeat the last entry."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, Exception):
            raise step
        return step


def _patched(fake, fn):
    """Run fn with urlopen faked, retry sleeps zeroed, and token caches
    cleared — before AND after, so no test leaks a cached token."""
    real_open, real_sleep = urllib.request.urlopen, ebay_client.time.sleep
    urllib.request.urlopen = fake
    ebay_client.time.sleep = lambda s: None
    _reset_caches()
    try:
        return fn()
    finally:
        urllib.request.urlopen, ebay_client.time.sleep = real_open, real_sleep
        _reset_caches()


def _reset_caches():
    ebay_client._app_cache.token = None
    ebay_client._app_cache.expires_at = 0.0
    ebay_client._user_cache.token = None
    ebay_client._user_cache.expires_at = 0.0
    ebay_client._user_scopes = ebay_client.USER_SCOPES_SELL


def _token_response(token="tok-1", ttl=7200):
    return _FakeResponse({"access_token": token, "expires_in": ttl})


# ---------------------------------------------------------------------------
# App token
# ---------------------------------------------------------------------------

def test_app_token_missing_creds_raises_auth_error():
    try:
        get_app_access_token(NO_CREDS)
        raise AssertionError("expected EbayAuthError")
    except EbayAuthError as e:
        assert "app_id" in str(e)


def test_app_token_is_cached_across_calls():
    fake = _Fake(_token_response("tok-app"))

    def go():
        assert get_app_access_token(APP_ONLY) == "tok-app"
        assert get_app_access_token(APP_ONLY) == "tok-app"
        return len(fake.requests)

    assert _patched(fake, go) == 1  # second call served from cache


def test_app_token_force_refresh_refetches():
    fake = _Fake(_token_response("tok-1"))

    def go():
        get_app_access_token(APP_ONLY)
        fake.script = [_token_response("tok-2")]
        assert get_app_access_token(APP_ONLY, force_refresh=True) == "tok-2"
        return len(fake.requests)

    assert _patched(fake, go) == 2


def test_app_token_near_expiry_refetches():
    # A token with 30s left is inside the 60s guard band — must refetch.
    fake = _Fake(_token_response("tok-short", ttl=30))

    def go():
        get_app_access_token(APP_ONLY)
        fake.script = [_token_response("tok-fresh")]
        assert get_app_access_token(APP_ONLY) == "tok-fresh"
        return len(fake.requests)

    assert _patched(fake, go) == 2


def test_app_token_http_error_surfaces_ebay_body():
    fake = _Fake(_http_error(401, b'{"error":"invalid_client"}'))

    def go():
        try:
            get_app_access_token(APP_ONLY)
            raise AssertionError("expected EbayAuthError")
        except EbayAuthError as e:
            assert "invalid_client" in str(e)
            assert "401" in str(e)

    _patched(fake, go)


# ---------------------------------------------------------------------------
# User token
# ---------------------------------------------------------------------------

def test_user_token_missing_refresh_token_raises_with_recapture_steps():
    try:
        get_user_access_token(APP_ONLY)
        raise AssertionError("expected EbayAuthError")
    except EbayAuthError as e:
        assert "user_refresh_token" in str(e)


def test_user_token_cached_and_grant_is_refresh_token():
    fake = _Fake(_token_response("tok-user"))

    def go():
        assert get_user_access_token(CREDS) == "tok-user"
        assert get_user_access_token(CREDS) == "tok-user"
        assert len(fake.requests) == 1
        body = fake.requests[0].data.decode()
        assert "grant_type=refresh_token" in body
        assert "refresh-x" in body

    _patched(fake, go)


def test_user_token_expired_refresh_token_says_recapture():
    fake = _Fake(_http_error(400, b'{"error":"invalid_grant"}'))

    def go():
        try:
            get_user_access_token(CREDS)
            raise AssertionError("expected EbayAuthError")
        except EbayAuthError as e:
            assert "re-capture" in str(e)

    _patched(fake, go)


# ---------------------------------------------------------------------------
# api_send — error surfacing + retry policy
# ---------------------------------------------------------------------------

def test_api_send_400_preserves_status_and_ebay_body():
    # The live-seen publish 400s (UPC checksum, Model aspect) are diagnosed
    # from this body — it must arrive verbatim, not summarized.
    ebay_body = b'{"errors":[{"errorId":25002,"message":"Invalid UPC"}]}'
    fake = _Fake(_token_response(), _http_error(400, ebay_body))

    def go():
        try:
            api_send("POST", "/sell/inventory/v1/offer", {"sku": "x"}, creds=CREDS)
            raise AssertionError("expected EbayAPIError")
        except EbayAPIError as e:
            assert e.status == 400
            assert "Invalid UPC" in e.body
            assert len(fake.requests) == 2  # token + one attempt, no retry on 4xx

    _patched(fake, go)


def test_api_send_5xx_on_post_does_not_retry():
    # A POST create must never double-fire on a transient 500.
    fake = _Fake(_token_response(), _http_error(500))

    def go():
        try:
            api_send("POST", "/sell/inventory/v1/offer", {"sku": "x"}, creds=CREDS)
            raise AssertionError("expected EbayAPIError")
        except EbayAPIError as e:
            assert e.status == 500
            assert len(fake.requests) == 2  # token + exactly ONE attempt

    _patched(fake, go)


def test_api_send_5xx_on_put_retries_then_succeeds():
    fake = _Fake(_token_response(), _http_error(503), _http_error(503),
                 _FakeResponse({"ok": True}))

    def go():
        out = api_send("PUT", "/sell/inventory/v1/inventory_item/SKU1",
                       {"a": 1}, creds=CREDS)
        assert out == {"ok": True}
        assert len(fake.requests) == 4  # token + 3 attempts

    _patched(fake, go)


def test_api_send_network_error_retries_even_for_post():
    # URLError = the request likely never reached eBay, so POST may retry.
    fake = _Fake(_token_response(), urllib.error.URLError("reset"),
                 _FakeResponse({"ok": True}))

    def go():
        out = api_send("POST", "/sell/inventory/v1/offer", {"sku": "x"}, creds=CREDS)
        assert out == {"ok": True}
        assert len(fake.requests) == 3

    _patched(fake, go)


def test_api_send_empty_2xx_body_returns_empty_dict():
    fake = _Fake(_token_response(), _FakeResponse(None, raw=b""))

    def go():
        assert api_send("DELETE", "/sell/inventory/v1/offer/123", creds=CREDS) == {}

    _patched(fake, go)


def test_api_send_sets_auth_marketplace_and_language_headers():
    fake = _Fake(_token_response("tok-user"), _FakeResponse({"ok": True}))

    def go():
        api_send("PUT", "sell/inventory/v1/inventory_item/SKU1", {"a": 1},
                 creds=CREDS)
        req = fake.requests[-1]
        assert req.get_header("Authorization") == "Bearer tok-user"
        assert req.get_header("X-ebay-c-marketplace-id") == "EBAY_US"
        assert req.get_header("Content-language") == "en-US"
        assert req.full_url.endswith("/sell/inventory/v1/inventory_item/SKU1")

    _patched(fake, go)


# ---------------------------------------------------------------------------
# api_get
# ---------------------------------------------------------------------------

def test_api_get_encodes_query_and_returns_json():
    fake = _Fake(_token_response(), _FakeResponse({"total": 3}))

    def go():
        out = api_get("/buy/browse/v1/item_summary/search",
                      query={"q": "rogers bros", "limit": 5}, creds=APP_ONLY)
        assert out == {"total": 3}
        assert "q=rogers+bros" in fake.requests[-1].full_url

    _patched(fake, go)


def test_api_get_http_error_becomes_api_error_with_body():
    fake = _Fake(_token_response(), _http_error(404, b'{"errors":[{"errorId":11001}]}'))

    def go():
        try:
            api_get("/buy/browse/v1/item/nope", creds=APP_ONLY)
            raise AssertionError("expected EbayAPIError")
        except EbayAPIError as e:
            assert e.status == 404
            assert "11001" in e.body

    _patched(fake, go)


# ---------------------------------------------------------------------------
# load_credentials(store=...) — multi-store profiles (GH #147)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _config(data):
    """Monkeypatch ebay_client.load_config for the duration of the block."""
    real = ebay_client.load_config
    ebay_client.load_config = lambda: data
    try:
        yield
    finally:
        ebay_client.load_config = real


def test_load_credentials_default_store_reads_legacy_single_account_shape():
    with _config({"ebay": {"environment": "sandbox",
                            "sandbox": {"app_id": "main-app", "cert_id": "main-cert"}}}):
        creds = load_credentials()
        assert creds.store == "default"
        assert creds.environment == "sandbox"
        assert creds.app_id == "main-app"
        assert creds.cert_id == "main-cert"


def test_load_credentials_named_store_reads_its_own_flat_block():
    with _config({
        "ebay": {
            "environment": "sandbox",
            "sandbox": {"app_id": "main-app"},
            "stores": {
                "junk": {"environment": "production", "app_id": "junk-app",
                         "cert_id": "junk-cert", "user_refresh_token": "junk-refresh"},
            },
        }
    }):
        creds = load_credentials(store="junk")
        assert creds.store == "junk"
        assert creds.environment == "production"       # a store's own environment, not the default's
        assert creds.app_id == "junk-app"
        assert creds.cert_id == "junk-cert"
        assert creds.user_refresh_token == "junk-refresh"
        # the default store's credentials are untouched
        default_creds = load_credentials(store="default")
        assert default_creds.app_id == "main-app"


def test_load_credentials_unknown_store_raises_auth_error_listing_known_stores():
    with _config({"ebay": {"stores": {"junk": {"app_id": "x"}}}}):
        try:
            load_credentials(store="nope")
            raise AssertionError("expected EbayAuthError")
        except EbayAuthError as e:
            assert "nope" in str(e)
            assert "junk" in str(e)


def test_load_credentials_store_precedence_env_var_over_config_default():
    with _config({
        "ebay": {"environment": "sandbox", "active_store": "default",
                 "stores": {"junk": {"app_id": "junk-app"}}}
    }):
        old = os.environ.get("EBAYBIZ_STORE")
        os.environ["EBAYBIZ_STORE"] = "junk"
        try:
            assert load_credentials().store == "junk"
        finally:
            if old is None:
                os.environ.pop("EBAYBIZ_STORE", None)
            else:
                os.environ["EBAYBIZ_STORE"] = old


def test_load_credentials_explicit_store_arg_wins_over_env_var():
    with _config({"ebay": {"environment": "sandbox", "stores": {"junk": {"app_id": "junk-app"}}}}):
        old = os.environ.get("EBAYBIZ_STORE")
        os.environ["EBAYBIZ_STORE"] = "junk"
        try:
            assert load_credentials(store="default").store == "default"
        finally:
            if old is None:
                os.environ.pop("EBAYBIZ_STORE", None)
            else:
                os.environ["EBAYBIZ_STORE"] = old


def test_list_stores_returns_configured_store_names():
    with _config({"ebay": {"stores": {"junk": {}, "vintage": {}}}}):
        assert ebay_client.list_stores() == ["junk", "vintage"]
    with _config({"ebay": {}}):
        assert ebay_client.list_stores() == []


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)


# ---------------------------------------------------------------------------
# A refresh_token consented before sell.finances existed (#126): the refresh
# must fall back to the core scopes, not break every Sell-API call.
# ---------------------------------------------------------------------------

def test_user_token_falls_back_to_core_scopes_on_invalid_scope():
    fake = _Fake(_http_error(400, b'{"error":"invalid_scope"}'), _token_response("tok-core"))

    def go():
        assert get_user_access_token(CREDS) == "tok-core"
        assert len(fake.requests) == 2
        first, second = (r.data.decode() for r in fake.requests)
        assert "sell.finances" in first
        assert "sell.finances" not in second
        assert "sell.fulfillment" in second
        # Remembered: the next forced refresh goes straight to the core set.
        get_user_access_token(CREDS, force_refresh=True)
        assert len(fake.requests) == 3
        assert "sell.finances" not in fake.requests[2].data.decode()

    _patched(fake, go)


def test_user_token_invalid_scope_on_core_set_still_raises():
    fake = _Fake(_http_error(400, b'{"error":"invalid_scope"}'),
                 _http_error(400, b'{"error":"invalid_scope"}'))

    def go():
        try:
            get_user_access_token(CREDS)
            raise AssertionError("expected EbayAuthError")
        except EbayAuthError as e:
            assert "invalid_scope" in str(e)
        assert len(fake.requests) == 2      # full set, then core set, then stop

    _patched(fake, go)


# ---------------------------------------------------------------------------
# Host routing: the Finances API lives on apiz.* (CREDS here is sandbox).
# ---------------------------------------------------------------------------

def test_finances_paths_go_to_the_apiz_host():
    fake = _Fake(_token_response("tok-user"), _FakeResponse({"total": 1}))

    def go():
        ebay_client.api_send("GET", "/sell/finances/v1/transaction?limit=1", creds=CREDS)
        assert fake.requests[-1].full_url.startswith("https://apiz.sandbox.ebay.com/sell/finances/")

    _patched(fake, go)


def test_other_sell_paths_stay_on_the_api_host():
    fake = _Fake(_token_response("tok-user"), _FakeResponse({"total": 1}))

    def go():
        ebay_client.api_send("GET", "/sell/fulfillment/v1/order?limit=1", creds=CREDS)
        assert fake.requests[-1].full_url.startswith("https://api.sandbox.ebay.com/sell/fulfillment/")

    _patched(fake, go)
