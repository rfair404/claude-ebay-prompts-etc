#!/usr/bin/env python3
"""lib/easypost_client.py — quote + buy, tested offline (GH #80).

Buying a label spends real money, so this module's own tests are the first
line of defense that the confirm gate actually holds: buy_label() must
never touch the purchase endpoint (POST /shipments/{id}/buy) unless called
with confirm=True. Every test here asserts on the exact set of HTTP calls
made, the same way tests/test_ebay_client.py and tests/test_list_edit.py
verify their money-moving paths.

All HTTP is faked by patching urllib.request.urlopen; no network, no real
API key needed to run these.

Run:  python tests/test_easypost_client.py
  or: pytest tests/test_easypost_client.py
"""
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

import easypost_client as EP                                            # noqa: E402
from easypost_client import (                                          # noqa: E402
    Address,
    BuyResult,
    EasyPostAPIError,
    EasyPostAuthError,
    Parcel,
    Rate,
    api_send,
    buy_label,
    get_api_key,
    get_rates,
)
import config as CFG                                                   # noqa: E402
from config import ConfigError                                         # noqa: E402


TO_ADDR = Address(name="Jane Buyer", street1="1 Main St", city="Springfield",
                  state="IL", zip="62704")
FROM_ADDR = Address(name="My Store", street1="9 Ship St", city="Elgin",
                    state="IL", zip="60120")
PARCEL = Parcel(weight_oz=24, length_in=10, width_in=8, height_in=4)


# ---------------------------------------------------------------------------
# Fakes — same shape as tests/test_ebay_client.py's _Fake/_FakeResponse
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, raw=None):
        self._raw = raw if raw is not None else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=b'{"error":{"message":"boom"}}'):
    return urllib.error.HTTPError("https://x", code, f"HTTP {code}", None, io.BytesIO(body))


class _Fake:
    """Scripted stand-in for urllib.request.urlopen. Requests beyond the
    script repeat the last scripted entry."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, Exception):
            raise step
        return step


def _patched(fake, fn, *, api_key="test-key-123"):
    """Run fn with urlopen faked, retry sleeps zeroed, and the API key
    forced via env var so no real config.yaml can leak in or out."""
    real_open, real_sleep = urllib.request.urlopen, EP.time.sleep
    prev_env = os.environ.get("EASYPOST_API_KEY")
    urllib.request.urlopen = fake
    EP.time.sleep = lambda s: None
    if api_key is not None:
        os.environ["EASYPOST_API_KEY"] = api_key
    else:
        os.environ.pop("EASYPOST_API_KEY", None)
    try:
        return fn()
    finally:
        urllib.request.urlopen, EP.time.sleep = real_open, real_sleep
        if prev_env is None:
            os.environ.pop("EASYPOST_API_KEY", None)
        else:
            os.environ["EASYPOST_API_KEY"] = prev_env


def _shipment_response(shipment_id="shp_1", rates=None):
    return _FakeResponse({
        "id": shipment_id,
        "rates": rates if rates is not None else [
            {"id": "rate_usps", "carrier": "USPS", "service": "Priority",
             "rate": "8.45", "currency": "USD", "delivery_days": 2},
            {"id": "rate_ups", "carrier": "UPS", "service": "Ground",
             "rate": "11.20", "currency": "USD", "delivery_days": 3},
        ],
    })


def _buy_response(tracking="TRK123456789", carrier="USPS", service="Priority",
                  rate_id="rate_usps", price="8.45"):
    return _FakeResponse({
        "id": "shp_1",
        "tracking_code": tracking,
        "selected_rate": {"id": rate_id, "carrier": carrier, "service": service,
                          "rate": price, "currency": "USD"},
        "postage_label": {"id": "pl_1",
                          "label_url": "https://easypost-labels.example/pl_1.png"},
    })


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def test_get_api_key_missing_everywhere_raises_config_error():
    # easypost_client.get_api_key() delegates to config.get_easypost_key();
    # patch the name as bound inside config's own namespace, not
    # easypost_client's, so the delegation is actually exercised.
    prev_env = os.environ.pop("EASYPOST_API_KEY", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    try:
        try:
            get_api_key()
            raise AssertionError("expected ConfigError")
        except ConfigError as e:
            assert "EASYPOST_API_KEY" in str(e)
            assert "easypost" in str(e)
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["EASYPOST_API_KEY"] = prev_env


def test_get_api_key_prefers_env_var_over_config_file():
    prev_env = os.environ.get("EASYPOST_API_KEY")
    os.environ["EASYPOST_API_KEY"] = "from-env"
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {"easypost": {"api_key": "from-config"}}
    try:
        assert get_api_key() == "from-env"
    finally:
        CFG.load_config = real_load
        if prev_env is None:
            os.environ.pop("EASYPOST_API_KEY", None)
        else:
            os.environ["EASYPOST_API_KEY"] = prev_env


def test_get_api_key_falls_back_to_config_file():
    prev_env = os.environ.pop("EASYPOST_API_KEY", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {"easypost": {"api_key": "from-config"}}
    try:
        assert get_api_key() == "from-config"
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["EASYPOST_API_KEY"] = prev_env


def test_get_api_key_delegates_to_config_get_easypost_key():
    """The Copilot-review fix: easypost_client no longer has its own copy
    of the precedence/error-message logic — it must call straight through
    to config.get_easypost_key() (bound in easypost_client's own namespace
    via `from config import get_easypost_key`)."""
    real = EP.get_easypost_key
    EP.get_easypost_key = lambda: "delegated-value"
    try:
        assert get_api_key() == "delegated-value"
    finally:
        EP.get_easypost_key = real


# ---------------------------------------------------------------------------
# api_send — auth header + retry policy (mirrors ebay_client's contract)
# ---------------------------------------------------------------------------

def test_api_send_sends_basic_auth_with_key_as_username():
    fake = _Fake(_FakeResponse({"ok": True}))

    def go():
        out = api_send("GET", "/shipments/shp_1")
        assert out == {"ok": True}
        auth = fake.requests[0].get_header("Authorization")
        import base64
        assert auth.startswith("Basic ")
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode()
        assert decoded == "test-key-123:"

    _patched(fake, go)


def test_api_send_401_raises_easypost_auth_error():
    fake = _Fake(_http_error(401, b'{"error":{"message":"invalid API key"}}'))

    def go():
        try:
            api_send("GET", "/shipments/shp_1")
            raise AssertionError("expected EasyPostAuthError")
        except EasyPostAuthError as e:
            assert "invalid API key" in str(e)

    _patched(fake, go)


def test_api_send_400_preserves_status_and_body():
    fake = _Fake(_http_error(422, b'{"error":{"message":"bad zip"}}'))

    def go():
        try:
            api_send("POST", "/shipments", {"shipment": {}})
            raise AssertionError("expected EasyPostAPIError")
        except EasyPostAPIError as e:
            assert e.status == 422
            assert "bad zip" in e.body
            assert len(fake.requests) == 1  # no retry on 4xx

    _patched(fake, go)


def test_api_send_5xx_on_post_does_not_retry():
    fake = _Fake(_http_error(500))

    def go():
        try:
            api_send("POST", "/shipments/shp_1/buy", {"rate": {"id": "r"}})
            raise AssertionError("expected EasyPostAPIError")
        except EasyPostAPIError as e:
            assert e.status == 500
            assert len(fake.requests) == 1  # a purchase call must never double-fire

    _patched(fake, go)


def test_api_send_5xx_on_get_retries_then_succeeds():
    fake = _Fake(_http_error(503), _http_error(503), _FakeResponse({"ok": True}))

    def go():
        out = api_send("GET", "/shipments/shp_1")
        assert out == {"ok": True}
        assert len(fake.requests) == 3

    _patched(fake, go)


def test_api_send_network_error_retries_even_for_post():
    fake = _Fake(urllib.error.URLError("reset"), _FakeResponse({"ok": True}))

    def go():
        out = api_send("POST", "/shipments", {"shipment": {}})
        assert out == {"ok": True}
        assert len(fake.requests) == 2

    _patched(fake, go)


# ---------------------------------------------------------------------------
# get_rates — the free path
# ---------------------------------------------------------------------------

def test_get_rates_parses_and_sorts_cheapest_first():
    fake = _Fake(_shipment_response())

    def go():
        shipment_id, rates = get_rates(TO_ADDR, FROM_ADDR, PARCEL)
        assert shipment_id == "shp_1"
        assert [r.carrier for r in rates] == ["USPS", "UPS"]  # 8.45 < 11.20
        assert rates[0].rate == 8.45
        assert rates[0].id == "rate_usps"
        # exactly one call — quoting never calls the purchase endpoint
        assert len(fake.requests) == 1
        assert fake.requests[0].full_url.endswith("/shipments")
        assert fake.requests[0].get_method() == "POST"

    _patched(fake, go)


def test_get_rates_sends_addresses_and_parcel_in_body():
    fake = _Fake(_shipment_response())

    def go():
        get_rates(TO_ADDR, FROM_ADDR, PARCEL)
        body = json.loads(fake.requests[0].data.decode())
        shipment = body["shipment"]
        assert shipment["to_address"]["zip"] == "62704"
        assert shipment["from_address"]["city"] == "Elgin"
        assert shipment["parcel"]["weight"] == 24

    _patched(fake, go)


def test_get_rates_skips_malformed_rate_entries():
    fake = _Fake(_shipment_response(rates=[
        {"id": "rate_bad", "carrier": "UPS", "service": "Ground", "rate": "not-a-number"},
        {"id": "rate_ok", "carrier": "USPS", "service": "Priority", "rate": "9.00"},
    ]))

    def go():
        _, rates = get_rates(TO_ADDR, FROM_ADDR, PARCEL)
        assert [r.id for r in rates] == ["rate_ok"]

    _patched(fake, go)


def test_get_rates_no_rates_returns_empty_list():
    fake = _Fake(_shipment_response(rates=[]))

    def go():
        shipment_id, rates = get_rates(TO_ADDR, FROM_ADDR, PARCEL)
        assert shipment_id == "shp_1"
        assert rates == []

    _patched(fake, go)


# ---------------------------------------------------------------------------
# buy_label — the ONE money-spending path; the confirm gate is load-bearing
# ---------------------------------------------------------------------------

CHEAP_RATE = Rate(id="rate_usps", carrier="USPS", service="Priority", rate=8.45,
                  currency="USD", delivery_days=2, shipment_id="shp_1")


def test_buy_label_without_confirm_is_a_pure_dry_run_no_http_call():
    fake = _Fake()  # scripted with NOTHING — any HTTP call fails the test

    def go():
        result = buy_label("shp_1", CHEAP_RATE, confirm=False)
        assert isinstance(result, BuyResult)
        assert result.dry_run is True
        assert result.shipment_id == "shp_1"
        assert result.rate_id == "rate_usps"
        assert result.carrier == "USPS"
        assert result.price == 8.45
        assert result.tracking_code is None
        assert result.label_url is None
        assert fake.requests == []  # the load-bearing assertion: no call made

    _patched(fake, go)


def test_buy_label_default_confirm_is_false():
    # Calling positionally/without the kwarg must default to the safe path.
    fake = _Fake()

    def go():
        result = buy_label("shp_1", CHEAP_RATE)
        assert result.dry_run is True
        assert fake.requests == []

    _patched(fake, go)


def test_buy_label_with_confirm_calls_the_buy_endpoint_and_returns_tracking():
    fake = _Fake(_buy_response())

    def go():
        result = buy_label("shp_1", CHEAP_RATE, confirm=True)
        assert result.dry_run is False
        assert result.tracking_code == "TRK123456789"
        assert result.carrier == "USPS"
        assert result.service == "Priority"
        assert result.label_url == "https://easypost-labels.example/pl_1.png"
        assert result.price == 8.45
        assert len(fake.requests) == 1
        req = fake.requests[0]
        assert req.full_url.endswith("/shipments/shp_1/buy")
        assert req.get_method() == "POST"
        body = json.loads(req.data.decode())
        assert body == {"rate": {"id": "rate_usps"}}

    _patched(fake, go)


def test_buy_label_confirmed_5xx_does_not_retry_and_surfaces_error():
    # A purchase POST must never silently retry — could double-buy a label.
    fake = _Fake(_http_error(500, b'{"error":{"message":"carrier timeout"}}'))

    def go():
        try:
            buy_label("shp_1", CHEAP_RATE, confirm=True)
            raise AssertionError("expected EasyPostAPIError")
        except EasyPostAPIError as e:
            assert e.status == 500
            assert "carrier timeout" in e.body
            assert len(fake.requests) == 1

    _patched(fake, go)


def test_buy_label_missing_api_key_raises_before_any_call():
    fake = _Fake()

    def go():
        try:
            buy_label("shp_1", CHEAP_RATE, confirm=True)
            raise AssertionError("expected ConfigError")
        except ConfigError:
            assert fake.requests == []

    _patched(fake, go, api_key=None)


# ---------------------------------------------------------------------------
# tools/ship_quote.py CLI — partial-dimension validation (Copilot review fix)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(ROOT / "tools"))
import ship_quote                                                     # noqa: E402

_BASE_ARGV = [
    "ship_quote.py",
    "--to-name", "Jane Buyer", "--to-street1", "1 Main St",
    "--to-city", "Springfield", "--to-state", "IL", "--to-zip", "62704",
    "--from-name", "My Store", "--from-street1", "9 Ship St",
    "--from-city", "Elgin", "--from-state", "IL", "--from-zip", "60120",
    "--weight-oz", "24",
]


def _run_ship_quote_cli(extra_argv, fake=None):
    real_argv = sys.argv
    real_get_rates = ship_quote.get_rates
    sys.argv = _BASE_ARGV + extra_argv
    if fake is not None:
        ship_quote.get_rates = fake
    try:
        return ship_quote.main()
    finally:
        sys.argv = real_argv
        ship_quote.get_rates = real_get_rates


def test_ship_quote_rejects_a_partial_dimension_set():
    calls = []
    fake = lambda *a, **kw: calls.append((a, kw)) or ("shp_1", [])  # noqa: E731
    rc = _run_ship_quote_cli(["--length-in", "10"], fake=fake)
    assert rc == 1
    assert calls == [], "get_rates must not be called — dims would be silently dropped"


def test_ship_quote_accepts_all_three_dimensions():
    calls = []
    fake = lambda *a, **kw: calls.append((a, kw)) or ("shp_1", [])  # noqa: E731
    rc = _run_ship_quote_cli(
        ["--length-in", "10", "--width-in", "8", "--height-in", "4"], fake=fake)
    assert rc == 0
    assert len(calls) == 1


def test_ship_quote_accepts_no_dimensions():
    calls = []
    fake = lambda *a, **kw: calls.append((a, kw)) or ("shp_1", [])  # noqa: E731
    rc = _run_ship_quote_cli([], fake=fake)
    assert rc == 0
    assert len(calls) == 1


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
