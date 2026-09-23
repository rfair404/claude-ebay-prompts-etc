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
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import config as CFG  # noqa: E402
import ebay_client  # noqa: E402
from ebay_client import (  # noqa: E402
    EbayAPIError,
    EbayAuthError,
    EbayCredentials,
    api_get,
    api_send,
    get_app_access_token,
    get_user_access_token,
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
    # Per-store dicts (GH #147) — clearing them is equivalent to the old
    # single-slot reset, and also covers every store a test may have used.
    ebay_client._app_caches.clear()
    ebay_client._user_caches.clear()
    ebay_client._user_scopes_by_store.clear()


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


# ---------------------------------------------------------------------------
# Multi-store credentials (GH #147) — load_credentials(store=...) resolution
# and per-store token cache isolation.
# ---------------------------------------------------------------------------

_MULTI_STORE_CONFIG = {
    "ebay": {
        "environment": "sandbox",
        "sandbox": {"app_id": "default-app", "cert_id": "default-cert",
                    "user_refresh_token": "default-refresh"},
        "stores": {
            "junk": {
                "environment": "sandbox",
                "sandbox": {"app_id": "junk-app", "cert_id": "junk-cert",
                           "user_refresh_token": "junk-refresh"},
            },
        },
    },
}


def _with_multi_store_config(fn):
    real_load = ebay_client.load_config
    ebay_client.load_config = lambda: _MULTI_STORE_CONFIG
    try:
        return fn()
    finally:
        ebay_client.load_config = real_load


def test_load_credentials_default_store_is_unchanged():
    def go():
        creds = ebay_client.load_credentials()
        assert creds.store == "default"
        assert creds.app_id == "default-app"

    _with_multi_store_config(go)


def test_load_credentials_reads_a_named_store():
    def go():
        creds = ebay_client.load_credentials("junk")
        assert creds.store == "junk"
        assert creds.app_id == "junk-app"
        assert creds.cert_id == "junk-cert"
        assert creds.user_refresh_token == "junk-refresh"

    _with_multi_store_config(go)


def test_load_credentials_explicit_default_matches_omitted():
    def go():
        assert ebay_client.load_credentials("default") == ebay_client.load_credentials()

    _with_multi_store_config(go)


def test_load_credentials_unknown_store_raises_and_names_available():
    def go():
        try:
            ebay_client.load_credentials("nope")
            raise AssertionError("expected EbayAuthError")
        except EbayAuthError as e:
            assert "nope" in str(e)
            assert "junk" in str(e)   # lists what IS configured

    _with_multi_store_config(go)


def test_app_token_cache_is_isolated_per_store():
    # Two stores, two distinct tokens — fetching the default store's token
    # must never serve (or clobber) the junk store's, and vice versa.
    fake = _Fake(_token_response("tok-default"), _token_response("tok-junk"))
    default_creds = EbayCredentials(environment="sandbox", app_id="a", cert_id="c",
                                    store="default")
    junk_creds = EbayCredentials(environment="sandbox", app_id="a2", cert_id="c2",
                                 store="junk")

    def go():
        assert get_app_access_token(default_creds) == "tok-default"
        assert get_app_access_token(junk_creds) == "tok-junk"
        # Both re-served from their own cache — no third HTTP call.
        assert get_app_access_token(default_creds) == "tok-default"
        assert get_app_access_token(junk_creds) == "tok-junk"
        assert len(fake.requests) == 2

    _patched(fake, go)


def test_user_token_cache_is_isolated_per_store():
    fake = _Fake(_token_response("utok-default"), _token_response("utok-junk"))
    default_creds = EbayCredentials(environment="sandbox", app_id="a", cert_id="c",
                                    user_refresh_token="r1", store="default")
    junk_creds = EbayCredentials(environment="sandbox", app_id="a2", cert_id="c2",
                                 user_refresh_token="r2", store="junk")

    def go():
        assert get_user_access_token(default_creds) == "utok-default"
        assert get_user_access_token(junk_creds) == "utok-junk"
        assert get_user_access_token(default_creds) == "utok-default"
        assert len(fake.requests) == 2

    _patched(fake, go)


def test_user_token_scope_narrowing_does_not_leak_across_stores():
    # The junk store's refresh_token predates sell.finances (invalid_scope);
    # the default store's must keep using the full scope set afterward.
    fake = _Fake(
        _http_error(400, b'{"error":"invalid_scope"}'), _token_response("tok-junk-core"),
        _token_response("tok-default-full"),
    )
    default_creds = EbayCredentials(environment="sandbox", app_id="a", cert_id="c",
                                    user_refresh_token="r1", store="default")
    junk_creds = EbayCredentials(environment="sandbox", app_id="a2", cert_id="c2",
                                 user_refresh_token="r2", store="junk")

    def go():
        assert get_user_access_token(junk_creds) == "tok-junk-core"
        assert get_user_access_token(default_creds) == "tok-default-full"
        assert "sell.finances" not in fake.requests[1].data.decode()  # junk, narrowed
        assert "sell.finances" in fake.requests[2].data.decode()      # default, still full

    _patched(fake, go)


def test_cli_check_reports_the_selected_store():
    # ebay_client.py --check prints `store:` so a --store typo is visible
    # immediately rather than silently checking the wrong (default) account.
    # No app_id/cert_id on this store on purpose — has_app is False, so
    # --check never takes the app-token network branch.
    config = {"ebay": {"environment": "sandbox",
                       "stores": {"readonly": {"environment": "sandbox"}}}}
    import argparse

    def go():
        parser = argparse.ArgumentParser()
        for name, kw in (
            ("--check", dict(action="store_true")), ("--schema", dict(default=None)),
            ("--category-tree-id", dict(action="store_true")),
            ("--category-suggestions", dict(default=None)),
            ("--category-aspects", dict(default=None)),
            ("--user-consent-url", dict(action="store_true")),
            ("--exchange-code", dict(default=None)),
            ("--marketplace", dict(default=ebay_client.DEFAULT_MARKETPLACE)),
            ("--tree-id", dict(default=None)), ("--json", dict(action="store_true")),
            ("--store", dict(default="readonly")),
        ):
            parser.add_argument(name, **kw)
        args = parser.parse_args(["--check"])
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        with redirect_stdout(buf):
            ebay_client._cli_run(parser, args)
        assert "store:              readonly" in buf.getvalue()

    real_load = ebay_client.load_config
    ebay_client.load_config = lambda: config
    try:
        go()
    finally:
        ebay_client.load_config = real_load


# ---------------------------------------------------------------------------
# config.get_ebay_credentials(store=...) — the soft/best-effort introspection
# accessor (GH #147), separate from ebay_client.load_credentials() above.
# Reads FLAT fields only (no sandbox/production nesting — see its own
# docstring), so this needs its own config shape, not _MULTI_STORE_CONFIG,
# and patches CFG.load_config directly (config.py calls its own module-level
# load_config(), not ebay_client's — patching the latter wouldn't reach it).
# ---------------------------------------------------------------------------

_FLAT_MULTI_STORE_CONFIG = {
    "ebay": {
        "app_id": "default-app",
        "user_refresh_token": "default-refresh",
        "stores": {
            "junk": {"app_id": "junk-app", "user_refresh_token": "junk-refresh"},
        },
    },
}


def _with_flat_multi_store_config(fn):
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: _FLAT_MULTI_STORE_CONFIG
    try:
        return fn()
    finally:
        CFG.load_config = real_load


def test_get_ebay_credentials_default_store_unchanged():
    def go():
        creds = CFG.get_ebay_credentials()
        assert creds["app_id"] == "default-app"

    _with_flat_multi_store_config(go)


def test_get_ebay_credentials_reads_a_named_store():
    def go():
        creds = CFG.get_ebay_credentials("junk")
        assert creds["app_id"] == "junk-app"
        assert creds["user_refresh_token"] == "junk-refresh"

    _with_flat_multi_store_config(go)


def test_get_ebay_credentials_unknown_store_is_soft_empty_not_an_error():
    # Documented as best-effort/soft (unlike load_credentials(), which
    # raises) — a typo here must not silently leak the default store's
    # values, but it also must not crash a CLI --show.
    def go():
        creds = CFG.get_ebay_credentials("nope")
        assert creds["app_id"] is None

    _with_flat_multi_store_config(go)


def test_resolve_ebay_section_unknown_store_lists_available_names():
    def go():
        try:
            ebay_client._resolve_ebay_section(ebay_client.load_config(), "ghost")
            raise AssertionError("expected EbayAuthError")
        except EbayAuthError as e:
            assert "ghost" in str(e)
            assert "junk" in str(e)

    _with_multi_store_config(go)
