#!/usr/bin/env python3
"""lib/temp_storage.py — hand-rolled SigV4 R2 client (GH #151).

All HTTP is faked by patching urllib.request.urlopen, same convention as
tests/test_easypost_client.py; no network and no real R2 account needed to
run these. Credentials are faked by monkeypatching config.get_r2_credentials
directly rather than env vars, so tests never depend on (or leak into) real
config.yaml / environment state.

Run:  python tests/test_temp_storage.py
  or: pytest tests/test_temp_storage.py
"""
import io
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import temp_storage as TS                                              # noqa: E402
from temp_storage import R2Client, R2Error                             # noqa: E402

CREDS = dict(account_id="acct123", access_key_id="AKIDEXAMPLE",
            secret_access_key="secretkey123", bucket="pick-sheets-bucket")


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Fake:
    """Scripted stand-in for urllib.request.urlopen — records every Request
    it was called with so a test can assert on method/URL/headers."""

    def __init__(self, *script):
        self.script = list(script) or [_FakeResponse()]
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, Exception):
            raise step
        return step


def _http_error(code, body=b"nope"):
    return urllib.error.HTTPError("https://x", code, f"HTTP {code}", None, io.BytesIO(body))


# --------------------------------------------------------------------------- #
# generate_key — unguessable, no order id, no sequence (GH #151's floor)
# --------------------------------------------------------------------------- #
def test_generate_key_has_128_bits_of_hex_entropy_and_no_order_id():
    key = TS.generate_key()
    assert key.startswith("pick-sheets/")
    assert key.endswith(".html")
    token = key[len("pick-sheets/"):-len(".html")]
    assert len(token) == 32  # 16 bytes of secrets.token_hex == 32 hex chars == 128 bits
    int(token, 16)  # raises ValueError if it isn't hex
    assert TS.generate_key() != TS.generate_key()  # not sequential / reused


# --------------------------------------------------------------------------- #
# presigned_get_url — https, time-limited, no secret key in the URL itself
# --------------------------------------------------------------------------- #
def test_presigned_get_url_is_https_scoped_to_bucket_and_key_and_ttl():
    c = R2Client(**CREDS)
    url = c.presigned_get_url("pick-sheets/abc123.html", 3600)
    assert url.startswith("https://acct123.r2.cloudflarestorage.com/pick-sheets-bucket/pick-sheets/abc123.html?")
    assert "X-Amz-Expires=3600" in url
    assert "X-Amz-Signature=" in url
    assert CREDS["secret_access_key"] not in url


def test_presigned_get_url_clamps_ttl_to_seven_days():
    c = R2Client(**CREDS)
    url = c.presigned_get_url("k.html", 999_999_999)
    assert "X-Amz-Expires=604800" in url  # AWS SigV4's own 7-day presign ceiling


# --------------------------------------------------------------------------- #
# put_object / delete_object — signed requests, error handling
# --------------------------------------------------------------------------- #
def test_put_object_sends_a_signed_put_with_content_type():
    c = R2Client(**CREDS)
    fake = _Fake(_FakeResponse())
    old = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        c.put_object("pick-sheets/x.html", b"<html>hi</html>", "text/html; charset=utf-8")
    finally:
        urllib.request.urlopen = old
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req.get_method() == "PUT"
    assert req.full_url == "https://acct123.r2.cloudflarestorage.com/pick-sheets-bucket/pick-sheets/x.html"
    assert req.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert req.headers["Content-type"] == "text/html; charset=utf-8"
    assert req.data == b"<html>hi</html>"


def test_put_object_raises_r2error_on_http_error():
    c = R2Client(**CREDS)
    old = urllib.request.urlopen
    urllib.request.urlopen = _Fake(_http_error(403, b"AccessDenied"))
    try:
        with pytest.raises(R2Error) as exc_info:
            c.put_object("k.html", b"data", "text/html")
    finally:
        urllib.request.urlopen = old
    assert exc_info.value.status == 403


def test_delete_object_treats_404_as_success_not_an_error():
    c = R2Client(**CREDS)
    old = urllib.request.urlopen
    urllib.request.urlopen = _Fake(_http_error(404))
    try:
        c.delete_object("already-gone.html")  # must not raise
    finally:
        urllib.request.urlopen = old


def test_delete_object_still_raises_on_a_real_failure():
    c = R2Client(**CREDS)
    old = urllib.request.urlopen
    urllib.request.urlopen = _Fake(_http_error(500, b"InternalError"))
    try:
        with pytest.raises(R2Error):
            c.delete_object("k.html")
    finally:
        urllib.request.urlopen = old


# --------------------------------------------------------------------------- #
# upload_pick_sheet / revoke_key — the convenience wrappers pick_list_html
# actually calls; "not configured" must be a quiet None, never an exception.
# --------------------------------------------------------------------------- #
def test_upload_pick_sheet_returns_not_configured_without_credentials():
    old = TS.get_r2_credentials
    TS.get_r2_credentials = lambda: None
    try:
        result = TS.upload_pick_sheet("<html></html>", 48)
    finally:
        TS.get_r2_credentials = old
    assert result == {"configured": False}


def test_upload_pick_sheet_uploads_once_and_returns_a_live_link():
    old_creds, old_open = TS.get_r2_credentials, urllib.request.urlopen
    TS.get_r2_credentials = lambda: dict(CREDS)
    urllib.request.urlopen = _Fake(_FakeResponse())
    try:
        result = TS.upload_pick_sheet("<html>pick sheet</html>", 48)
    finally:
        TS.get_r2_credentials = old_creds
        urllib.request.urlopen = old_open
    assert result["configured"] is True
    assert result["url"].startswith("https://")
    assert result["key"].startswith("pick-sheets/")
    assert result["expires_at"]


def test_upload_pick_sheet_propagates_r2error_on_failed_upload():
    old_creds, old_open = TS.get_r2_credentials, urllib.request.urlopen
    TS.get_r2_credentials = lambda: dict(CREDS)
    urllib.request.urlopen = _Fake(_http_error(500))
    try:
        with pytest.raises(R2Error):
            TS.upload_pick_sheet("<html></html>", 48)
    finally:
        TS.get_r2_credentials = old_creds
        urllib.request.urlopen = old_open


def test_revoke_key_raises_when_not_configured():
    old = TS.get_r2_credentials
    TS.get_r2_credentials = lambda: None
    try:
        with pytest.raises(R2Error):
            TS.revoke_key("pick-sheets/x.html")
    finally:
        TS.get_r2_credentials = old


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
