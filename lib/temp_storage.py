"""Cloudflare R2 client for ephemeral, link-only object storage (GH #151).

Built for one job: `tools/pick_list_html.py` needs to hand a rendered pick
sheet to a human as an `https://` link instead of a local file path, without
turning R2 into a public file host. Every property that makes that safe for
buyer PII comes from how presigned URLs work, not from bucket configuration:

  - Unguessable  -- the object key is 128 bits of `secrets.token_hex`, not an
    order number or anything sequential (`generate_key()`).
  - Expiring by default -- a SigV4 presigned GET URL stops authenticating the
    instant its `X-Amz-Expires` window closes. No lifecycle rule, no cron, no
    server-side deletion required for the *link* to die on schedule; the R2
    object itself is a separate concern, cleaned up by `revoke_key()` (early,
    explicit) or left to accumulate (best-effort revoke only -- see
    `upload_pick_sheet`'s docstring for what "expiring by default" does and
    does not cover).
  - No listing, no indexing -- there is no bucket-public-access, no R2
    "custom domain" / static site hosting anywhere in this client. The only
    way to reach an object is a presigned URL bearing its own signature; no
    one can enumerate or browse the bucket. The page itself also carries a
    `noindex` meta tag (see `tools/pick_list_html.py:render_html`).
  - TLS only -- every URL this module builds is `https://`.

Credentials (account id, access key id, secret access key, bucket) come from
`config.get_r2_credentials()`, same precedence contract as every other key in
this repo (env var > config.yaml > ...) with one deliberate difference: a
missing/partial credential set returns `None` instead of raising. Being
unconfigured is the expected "offline / no network" mode this tool must keep
working in (GH #151), not an error -- callers check for `None` and fall back
to a local-only file.

No SDK dependency. R2's S3-compatible API is signed with plain AWS
Signature Version 4 (hmac/hashlib, stdlib only) -- boto3 would be a lot of
dependency weight for one bucket and three verbs (PUT / GET-presign /
DELETE).
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import get_r2_credentials  # noqa: E402

_SERVICE = "s3"
_REGION = "auto"          # R2's fixed SigV4 region
_ALGORITHM = "AWS4-HMAC-SHA256"
# AWS SigV4 presigned URLs cap at 7 days; anything longer is rejected by the
# signer itself, not just a house convention.
_MAX_TTL_SECONDS = 7 * 24 * 3600


class R2Error(RuntimeError):
    """A request to R2 failed (network error or non-2xx response).

    Distinct from "not configured" (see get_r2_credentials returning None) --
    this means credentials exist and the call was attempted and rejected or
    could not complete. Callers must treat this loudly: fall back to the
    local file, exit non-zero, print why (GH #151's "upload failure is
    loud" requirement).
    """
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def generate_key(prefix: str = "pick-sheets") -> str:
    """An unguessable object key with no order id, no sequence, nothing
    derivable from the shipment it holds -- 128 bits of entropy is the
    ≥128-bit floor GH #151 asks for."""
    return f"{prefix}/{secrets.token_hex(16)}.html"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _derive_signing_key(secret_access_key: str, datestamp: str) -> bytes:
    k_date = _hmac(("AWS4" + secret_access_key).encode("utf-8"), datestamp)
    k_region = _hmac(k_date, _REGION)
    k_service = _hmac(k_region, _SERVICE)
    return _hmac(k_service, "aws4_request")


def _quote(s: str, safe: str = "-_.~") -> str:
    return urllib.parse.quote(s, safe=safe)


class R2Client:
    """Minimal S3-compatible client, scoped to one bucket, signed by hand."""

    def __init__(self, account_id: str, access_key_id: str,
                 secret_access_key: str, bucket: str) -> None:
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket
        self.host = f"{account_id}.r2.cloudflarestorage.com"

    # ------------------------------------------------------------------ #
    # header-signed requests (PUT / DELETE) -- server-to-server, never
    # handed to a human, so a normal Authorization header is fine.
    # ------------------------------------------------------------------ #
    def _signed_request(self, method: str, key: str, *, body: bytes = b"",
                         extra_headers: Optional[dict] = None) -> urllib.request.Request:
        now = datetime.datetime.now(datetime.timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        canonical_uri = f"/{self.bucket}/{_quote(key, safe='-_.~/')}"
        payload_hash = _sha256_hex(body)

        headers = {"host": self.host, "x-amz-date": amzdate,
                   "x-amz-content-sha256": payload_hash}
        headers.update({k.lower(): v for k, v in (extra_headers or {}).items()})
        signed_header_names = sorted(headers)
        canonical_headers = "".join(f"{h}:{headers[h]}\n" for h in signed_header_names)
        signed_headers = ";".join(signed_header_names)

        canonical_request = "\n".join([
            method, canonical_uri, "", canonical_headers, signed_headers, payload_hash])
        credential_scope = f"{datestamp}/{_REGION}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join([
            _ALGORITHM, amzdate, credential_scope, _sha256_hex(canonical_request.encode("utf-8"))])
        signature = hmac.new(self._signing_key(datestamp), string_to_sign.encode("utf-8"),
                             hashlib.sha256).hexdigest()

        auth = (f"{_ALGORITHM} Credential={self.access_key_id}/{credential_scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}")
        req_headers = {h: headers[h] for h in signed_header_names}
        req_headers["Authorization"] = auth
        url = f"https://{self.host}{canonical_uri}"
        return urllib.request.Request(url, data=body or None, method=method, headers=req_headers)

    def _signing_key(self, datestamp: str) -> bytes:
        return _derive_signing_key(self.secret_access_key, datestamp)

    def _send(self, req: urllib.request.Request) -> None:
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as e:
            if req.get_method() == "DELETE" and e.code == 404:
                return  # already gone -- delete is idempotent
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:                                        # noqa: BLE001
                pass
            raise R2Error(f"R2 {req.get_method()} {req.full_url} -> HTTP {e.code}: {body}",
                         status=e.code) from e
        except urllib.error.URLError as e:
            raise R2Error(f"R2 {req.get_method()} {req.full_url} unreachable: {e.reason}") from e

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        req = self._signed_request(
            "PUT", key, body=data,
            extra_headers={"content-type": content_type, "cache-control": "private, no-store"})
        self._send(req)

    def delete_object(self, key: str) -> None:
        self._send(self._signed_request("DELETE", key))

    # ------------------------------------------------------------------ #
    # query-string-signed GET (the presigned link handed to a human)
    # ------------------------------------------------------------------ #
    def presigned_get_url(self, key: str, ttl_seconds: int) -> str:
        ttl_seconds = max(1, min(ttl_seconds, _MAX_TTL_SECONDS))
        now = datetime.datetime.now(datetime.timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        credential_scope = f"{datestamp}/{_REGION}/{_SERVICE}/aws4_request"
        canonical_uri = f"/{self.bucket}/{_quote(key, safe='-_.~/')}"

        query = {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{self.access_key_id}/{credential_scope}",
            "X-Amz-Date": amzdate,
            "X-Amz-Expires": str(ttl_seconds),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_querystring = "&".join(
            f"{_quote(k)}={_quote(v)}" for k, v in sorted(query.items()))
        canonical_headers = f"host:{self.host}\n"
        canonical_request = "\n".join([
            "GET", canonical_uri, canonical_querystring, canonical_headers, "host",
            "UNSIGNED-PAYLOAD"])
        string_to_sign = "\n".join([
            _ALGORITHM, amzdate, credential_scope, _sha256_hex(canonical_request.encode("utf-8"))])
        signature = hmac.new(self._signing_key(datestamp), string_to_sign.encode("utf-8"),
                             hashlib.sha256).hexdigest()
        return f"https://{self.host}{canonical_uri}?{canonical_querystring}&X-Amz-Signature={signature}"


# --------------------------------------------------------------------------- #
# convenience wrappers used by tools/pick_list_html.py
# --------------------------------------------------------------------------- #
def _client() -> Optional[R2Client]:
    creds = get_r2_credentials()
    if creds is None:
        return None
    return R2Client(**creds)


def upload_pick_sheet(html_text: str, ttl_hours: float) -> dict:
    """Upload one rendered pick sheet and return a presigned link to it.

    Returns `{"configured": False}` when no R2 credentials are set anywhere
    (env or config.yaml) -- this is the tool's normal offline mode, not an
    error, so it never raises for that case.

    When configured, returns `{"configured": True, "key", "url",
    "expires_at"}`. `expires_at` describes when the *link* (this exact
    presigned URL) stops authenticating -- that expiry is enforced by the
    signature itself, not by anything server-side, so it holds even if the
    object is never explicitly deleted. The object itself keeps existing in
    the bucket until `revoke_key()` removes it; there is no automatic
    server-side deletion here (R2 lifecycle rules are day-granularity, which
    doesn't fit a 48h default cleanly, and are a bucket-level setup step, not
    something this client silently provisions). A caller that wants the
    object gone, not just unlinkable, must call `revoke_key()`.

    Raises R2Error on any failed upload -- callers must treat that loudly
    (fall back to the local file, exit non-zero, say why).
    """
    client = _client()
    if client is None:
        return {"configured": False}
    key = generate_key()
    client.put_object(key, html_text.encode("utf-8"), "text/html; charset=utf-8")
    ttl_seconds = min(int(ttl_hours * 3600), _MAX_TTL_SECONDS)
    url = client.presigned_get_url(key, ttl_seconds)
    expires_at = (datetime.datetime.now(datetime.timezone.utc)
                 + datetime.timedelta(seconds=ttl_seconds))
    return {"configured": True, "key": key, "url": url,
            "expires_at": expires_at.isoformat()}


def revoke_key(key: str) -> None:
    """Delete one object early. Raises R2Error if R2 isn't configured or the
    delete call itself fails; a 404 (already gone) is treated as success."""
    client = _client()
    if client is None:
        raise R2Error("R2 is not configured -- cannot revoke a link with no credentials")
    client.delete_object(key)
