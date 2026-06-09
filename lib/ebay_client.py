"""
eBay Sell API client (basic).

Wraps the subset of eBay's REST APIs that V2's DRAFT function needs:
- OAuth (app-context for read; user-context stub for write)
- Taxonomy API: category tree + per-category aspects (the item-specifics
  that DRAFT must fill)
- Schema introspection via the static `ebay_schema` module

This client is intentionally MINIMAL for now. The user is in the process
of obtaining real eBay developer credentials; before those arrive, this
module:
- Validates that credential fields can be loaded (or surface clear errors)
- Prints the static InventoryItem / Offer schema (no auth needed)
- Stubs the live Taxonomy + Sell endpoints so they're easy to activate
  once a token is configured

When credentials are configured, the same module gains live API access
without any structural changes.

----- Authentication model -----

eBay REST APIs use OAuth 2.0. Two flows:

  1. App-context (client_credentials grant)
     - Used for non-user-data: Taxonomy API (categories + aspects),
       Catalog API (product search), Browse API (read-only listing data).
     - Requires App ID + Cert ID. Token lifetime ~2 hours.
     - This client handles app-context fetch + caching automatically.

  2. User-context (authorization_code grant)
     - REQUIRED for any Sell-* API write: Inventory, Offer, Account,
       Fulfillment, Marketing.
     - Requires user OAuth consent flow: redirect user to eBay, capture
       authorization_code from callback, exchange for refresh_token,
       then exchange refresh_token for short-lived access_token.
     - This client provides the URL-generation + token-exchange helpers
       but the consent capture is manual (user runs CLI, follows URL,
       pastes back the code). Refresh tokens last ~18 months.

----- Sandbox vs Production -----

eBay maintains two completely separate environments with different
credentials, base URLs, and data:

  Sandbox: https://api.sandbox.ebay.com   — fake data, no real listings,
                                            safe for development
  Production: https://api.ebay.com        — your real eBay account

This client honors `ebay.environment: sandbox` / `production` in config.
Default: sandbox (safer until you've tested end-to-end).

----- CLI -----

    python ebay_client.py --check
        Verify credentials loaded; report what's available.

    python ebay_client.py --schema {inventory_item, offer, all}
        Print the static field reference. No API call. No credentials needed.

    python ebay_client.py --category-tree-id [--marketplace EBAY_US]
        Fetch the default category tree ID for a marketplace (Taxonomy API).
        Requires app-context credentials.

    python ebay_client.py --category-aspects <category_id> \\
        [--marketplace EBAY_US] [--tree-id 0]
        Fetch the item-aspects for a category. Returns each aspect with
        its mode (FREE_TEXT / SELECTION_ONLY), max value count, applicable
        values (if SELECTION_ONLY), and required-for-listing flag.
        Requires app-context credentials.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Optional

from config import ConfigError, config_path, load_config
from ebay_schema import SCHEMAS, get_schema, print_schema


# ---------------------------------------------------------------------------
# Environment / base URLs
# ---------------------------------------------------------------------------

ENVIRONMENTS = {
    "sandbox": {
        "api_base":   "https://api.sandbox.ebay.com",
        "auth_base":  "https://auth.sandbox.ebay.com",
        "token_url":  "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
    },
    "production": {
        "api_base":   "https://api.ebay.com",
        "auth_base":  "https://auth.ebay.com",
        "token_url":  "https://api.ebay.com/identity/v1/oauth2/token",
    },
}

DEFAULT_ENVIRONMENT = "sandbox"
DEFAULT_MARKETPLACE = "EBAY_US"

# OAuth scope sets — passed to the token endpoint
APP_SCOPES = [
    # Public scopes — fine for client_credentials
    "https://api.ebay.com/oauth/api_scope",
]
USER_SCOPES_SELL = [
    # Sell-* APIs require these for write access
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EbayAuthError(RuntimeError):
    """Raised when credentials are missing, malformed, or rejected by eBay."""


class EbayAPIError(RuntimeError):
    """Raised when an eBay API call returns a non-2xx response."""
    def __init__(self, status: int, message: str, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# Credentials loader
# ---------------------------------------------------------------------------

@dataclass
class EbayCredentials:
    """Loaded eBay credentials. Missing fields are None."""
    environment: str = DEFAULT_ENVIRONMENT
    app_id: Optional[str] = None       # aka client_id
    cert_id: Optional[str] = None      # aka client_secret
    dev_id: Optional[str] = None       # legacy XML-API ID; not needed for REST
    redirect_uri: Optional[str] = None # the OAuth redirect URI registered with eBay
    user_refresh_token: Optional[str] = None  # captured from user-consent flow

    @property
    def has_app(self) -> bool:
        return bool(self.app_id and self.cert_id)

    @property
    def has_user(self) -> bool:
        return bool(self.user_refresh_token and self.has_app)

    @property
    def env(self) -> dict:
        return ENVIRONMENTS[self.environment]


def load_credentials() -> EbayCredentials:
    """Load eBay credentials from the YAML config."""
    config = load_config()
    section = config.get("ebay") or {}

    env = section.get("environment") or DEFAULT_ENVIRONMENT
    if env not in ENVIRONMENTS:
        raise EbayAuthError(
            f"ebay.environment must be 'sandbox' or 'production' "
            f"(got '{env}' from {config_path()})"
        )

    # Per-environment credential blocks (ebay.sandbox.* / ebay.production.*)
    # take precedence; fall back to flat ebay.* fields for legacy configs.
    env_section = section.get(env) or {}

    def pick(key):
        v = env_section.get(key)
        return v if v is not None else section.get(key)

    return EbayCredentials(
        environment=env,
        app_id=pick("app_id"),
        cert_id=pick("cert_id"),
        dev_id=pick("dev_id"),
        redirect_uri=pick("redirect_uri"),
        user_refresh_token=pick("user_refresh_token"),
    )


# ---------------------------------------------------------------------------
# OAuth — app-context (client_credentials)
# ---------------------------------------------------------------------------

@dataclass
class _AppTokenCache:
    """In-memory cache for the app-context access token."""
    token: Optional[str] = None
    expires_at: float = 0.0  # epoch seconds


_app_cache = _AppTokenCache()


def get_app_access_token(creds: Optional[EbayCredentials] = None, force_refresh: bool = False) -> str:
    """Return a valid app-context access token, fetching/caching as needed.

    eBay app tokens last ~2 hours. We cache and re-fetch when within 60s of expiry.

    Raises EbayAuthError if app_id / cert_id are missing or eBay rejects them.
    """
    creds = creds or load_credentials()
    if not creds.has_app:
        raise EbayAuthError(
            "App credentials (app_id + cert_id) are not configured.\n"
            f"  Add them to {config_path()} under the 'ebay:' section:\n"
            f"      ebay:\n"
            f"        app_id: \"<Your-App-Id>\"\n"
            f"        cert_id: \"<Your-Cert-Id>\"\n"
            "  Generate keys at https://developer.ebay.com/my/keys"
        )

    now = time.time()
    if not force_refresh and _app_cache.token and _app_cache.expires_at - 60 > now:
        return _app_cache.token

    basic = base64.b64encode(f"{creds.app_id}:{creds.cert_id}".encode("utf-8")).decode("ascii")
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": " ".join(APP_SCOPES),
    }).encode("utf-8")
    req = urllib.request.Request(
        creds.env["token_url"],
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise EbayAuthError(
            f"eBay token endpoint returned HTTP {e.code} ({creds.environment}):\n"
            f"  {body_text}\n"
            f"  Check that app_id + cert_id match the {creds.environment} environment."
        ) from e
    except urllib.error.URLError as e:
        raise EbayAuthError(f"Network error reaching {creds.env['token_url']}: {e}") from e

    token = payload.get("access_token")
    ttl = payload.get("expires_in", 7200)
    if not token:
        raise EbayAuthError(f"Token response missing access_token: {payload}")

    _app_cache.token = token
    _app_cache.expires_at = now + float(ttl)
    return token


# ---------------------------------------------------------------------------
# OAuth — user-context (authorization_code → refresh_token) STUB
# ---------------------------------------------------------------------------

def user_consent_url(creds: Optional[EbayCredentials] = None,
                     scopes: Optional[list[str]] = None,
                     state: Optional[str] = None) -> str:
    """Build the URL the user visits to grant the app access to their eBay account.

    User flow (manual, run-once per refresh-token-lifetime):
      1. Run: python ebay_client.py --user-consent-url
      2. Open the URL in a browser, sign in to eBay, authorize.
      3. eBay redirects to your `redirect_uri` with ?code=... in the query.
      4. Run: python ebay_client.py --exchange-code <code>
      5. The returned refresh_token gets stored in config under
         ebay.user_refresh_token. (Manual paste for now; CLI helper later.)
    """
    creds = creds or load_credentials()
    if not creds.has_app:
        raise EbayAuthError("App credentials (app_id + cert_id) are required.")
    if not creds.redirect_uri:
        raise EbayAuthError(
            "ebay.redirect_uri is required for user-consent flow. "
            "Add to config: `ebay.redirect_uri: \"<your-registered-redirect-uri>\"`."
        )
    scopes = scopes or USER_SCOPES_SELL
    base = creds.env["auth_base"] + "/oauth2/authorize"
    params = {
        "client_id": creds.app_id,
        "response_type": "code",
        "redirect_uri": creds.redirect_uri,
        "scope": " ".join(scopes),
    }
    if state:
        params["state"] = state
    return f"{base}?{urllib.parse.urlencode(params)}"


def exchange_authorization_code(code: str, creds: Optional[EbayCredentials] = None) -> dict:
    """Exchange a user-consent authorization_code for access + refresh tokens.

    Returns the full token response dict. The caller is responsible for
    persisting `refresh_token` to config (`ebay.user_refresh_token`).
    """
    creds = creds or load_credentials()
    if not creds.has_app:
        raise EbayAuthError("App credentials (app_id + cert_id) are required.")
    if not creds.redirect_uri:
        raise EbayAuthError("ebay.redirect_uri is required.")

    basic = base64.b64encode(f"{creds.app_id}:{creds.cert_id}".encode("utf-8")).decode("ascii")
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": creds.redirect_uri,
    }).encode("utf-8")
    req = urllib.request.Request(
        creds.env["token_url"],
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Base GET request (app-context)
# ---------------------------------------------------------------------------

def api_get(path: str, query: Optional[dict] = None,
            creds: Optional[EbayCredentials] = None,
            marketplace: Optional[str] = None) -> dict:
    """Issue a GET against the eBay REST API using the app-context token.

    Args:
        path: API path (with or without leading '/'), e.g. '/commerce/taxonomy/v1/get_default_category_tree_id'
        query: query-string dict to URL-encode
        creds: optional EbayCredentials override
        marketplace: marketplace ID to set in the X-EBAY-C-MARKETPLACE-ID header.

    Returns:
        Decoded JSON body as a dict.

    Raises:
        EbayAPIError on non-2xx response.
    """
    creds = creds or load_credentials()
    token = get_app_access_token(creds)

    if not path.startswith("/"):
        path = "/" + path
    url = creds.env["api_base"] + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if marketplace:
        headers["X-EBAY-C-MARKETPLACE-ID"] = marketplace

    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise EbayAPIError(e.code, f"GET {path} → HTTP {e.code}", body_text) from e


# ---------------------------------------------------------------------------
# OAuth — user-context access token (refresh_token grant)
# ---------------------------------------------------------------------------

@dataclass
class _UserTokenCache:
    token: Optional[str] = None
    expires_at: float = 0.0


_user_cache = _UserTokenCache()


def get_user_access_token(creds: Optional[EbayCredentials] = None,
                          force_refresh: bool = False) -> str:
    """Return a valid USER-context access token from the stored refresh_token.

    Required for every Sell-* write (Inventory, Offer, Account). The
    refresh_token is captured once via the user-consent flow
    (see user_consent_url / exchange_authorization_code) and stored in
    config as ebay.user_refresh_token. User access tokens last ~2 hours;
    we cache and re-fetch within 60s of expiry.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError(
            "User refresh_token (ebay.user_refresh_token) is not configured.\n"
            "  Sell-API writes need user-context OAuth. Capture it once:\n"
            "    1. python ebay_client.py --user-consent-url\n"
            "    2. visit the URL, sign in, authorize\n"
            "    3. python ebay_client.py --exchange-code <code-from-callback>\n"
            f"    4. paste the printed refresh_token into {config_path()}"
        )

    now = time.time()
    if not force_refresh and _user_cache.token and _user_cache.expires_at - 60 > now:
        return _user_cache.token

    basic = base64.b64encode(f"{creds.app_id}:{creds.cert_id}".encode("utf-8")).decode("ascii")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": creds.user_refresh_token,
        "scope": " ".join(USER_SCOPES_SELL),
    }).encode("utf-8")
    req = urllib.request.Request(
        creds.env["token_url"], data=body, method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise EbayAuthError(
            f"Refreshing user token failed (HTTP {e.code}):\n  {body_text}\n"
            "  The refresh_token may be expired (~18 month lifetime) or scoped wrong; re-capture it."
        ) from e
    except urllib.error.URLError as e:
        raise EbayAuthError(f"Network error reaching token endpoint: {e}") from e

    token = payload.get("access_token")
    if not token:
        raise EbayAuthError(f"Refresh response missing access_token: {payload}")
    _user_cache.token = token
    _user_cache.expires_at = now + float(payload.get("expires_in", 7200))
    return token


# ---------------------------------------------------------------------------
# Generic JSON write request (user-context: POST / PUT / DELETE)
# ---------------------------------------------------------------------------

def api_send(method: str, path: str, body: Optional[dict] = None,
             creds: Optional[EbayCredentials] = None,
             marketplace: Optional[str] = DEFAULT_MARKETPLACE,
             content_language: str = "en-US",
             extra_headers: Optional[dict] = None) -> dict:
    """Issue a user-context JSON request (POST/PUT/DELETE) against the Sell API.

    Returns the decoded JSON body (or {} for empty 2xx like 204). Raises
    EbayAPIError on non-2xx, carrying eBay's error body for diagnosis.

    NOTE: There is intentionally NO helper here for the publish endpoint.
    The no-publish firewall is enforced by absence (see list_edit.py).
    """
    creds = creds or load_credentials()
    token = get_user_access_token(creds)
    if not path.startswith("/"):
        path = "/" + path
    url = creds.env["api_base"] + path

    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Language": content_language,
    }
    if marketplace:
        headers["X-EBAY-C-MARKETPLACE-ID"] = marketplace
    if extra_headers:
        headers.update(extra_headers)

    # eBay production is occasionally flaky (transient 5xx; eventual
    # consistency right after a create). Retry transient failures with
    # backoff. Only retry 5xx for IDEMPOTENT methods (GET/PUT/DELETE) so a
    # POST create can't double-fire; network errors retry for any method
    # (the request likely never reached eBay).
    m = method.upper()
    idempotent = m in ("GET", "PUT", "DELETE")
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=m, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            err = EbayAPIError(e.code, f"{m} {path} → HTTP {e.code}", body_text)
            if e.code >= 500 and idempotent and attempt < 2:
                time.sleep(0.8 * (attempt + 1)); continue
            raise err from e
        except urllib.error.URLError as e:
            err = EbayAPIError(0, f"{m} {path} → network error: {e}", None)
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1)); continue
            raise err from e


# ---------------------------------------------------------------------------
# Account API helpers — business policies + inventory locations
# (createOffer requires the policy IDs + a merchantLocationKey; these are
#  account-specific and captured once into config. --setup-check lists them.)
# ---------------------------------------------------------------------------

def get_fulfillment_policies(marketplace: str = DEFAULT_MARKETPLACE,
                             creds: Optional[EbayCredentials] = None) -> list[dict]:
    data = api_send("GET", "/sell/account/v1/fulfillment_policy",
                    creds=creds, marketplace=None,
                    extra_headers={"X-EBAY-C-MARKETPLACE-ID": marketplace})
    return data.get("fulfillmentPolicies") or []


def get_payment_policies(marketplace: str = DEFAULT_MARKETPLACE,
                         creds: Optional[EbayCredentials] = None) -> list[dict]:
    data = api_send("GET", "/sell/account/v1/payment_policy",
                    creds=creds, marketplace=None,
                    extra_headers={"X-EBAY-C-MARKETPLACE-ID": marketplace})
    return data.get("paymentPolicies") or []


def get_return_policies(marketplace: str = DEFAULT_MARKETPLACE,
                        creds: Optional[EbayCredentials] = None) -> list[dict]:
    data = api_send("GET", "/sell/account/v1/return_policy",
                    creds=creds, marketplace=None,
                    extra_headers={"X-EBAY-C-MARKETPLACE-ID": marketplace})
    return data.get("returnPolicies") or []


def get_inventory_locations(creds: Optional[EbayCredentials] = None) -> list[dict]:
    data = api_send("GET", "/sell/inventory/v1/location", creds=creds, marketplace=None)
    return data.get("locations") or []


# ---------------------------------------------------------------------------
# eBay Picture Services (EPS) upload — Trading API UploadSiteHostedPictures
# ---------------------------------------------------------------------------
#
# The REST Sell API has no binary-image upload endpoint; product.imageUrls
# must already be hosted. The documented way to host a local file on eBay
# is the Trading API call UploadSiteHostedPictures, which returns a durable
# EPS FullURL. It uses the same OAuth user token (as an IAF token) plus the
# dev/app/cert names. This is the function that makes photo upload headless
# — no browser, no drag-and-drop (the V1 + Chrome-stand-in pain point).

_TRADING_COMPAT_LEVEL = "967"
_TRADING_SITE_ID = "0"  # US


def _trading_endpoint(creds: EbayCredentials) -> str:
    return ("https://api.sandbox.ebay.com/ws/api.dll"
            if creds.environment == "sandbox"
            else "https://api.ebay.com/ws/api.dll")


def upload_site_hosted_picture(image_bytes: bytes, picture_name: str = "photo",
                               creds: Optional[EbayCredentials] = None) -> str:
    """Upload one image to EPS via UploadSiteHostedPictures; return the EPS URL.

    Requires: app_id, cert_id, dev_id (Trading API needs all three names),
    and a user refresh_token. Raises EbayAuthError if any are missing.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("EPS upload needs a user refresh_token (Sell scope). See --check.")
    if not creds.dev_id:
        raise EbayAuthError(
            "EPS upload via the Trading API needs ebay.dev_id (Dev ID) in config.\n"
            "  Find it at https://developer.ebay.com/my/keys (same keyset as app_id/cert_id)."
        )
    iaf = get_user_access_token(creds)

    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<PictureName>{_xml_escape(picture_name)}</PictureName>"
        "<PictureSet>Supersize</PictureSet>"
        "</UploadSiteHostedPicturesRequest>"
    )

    boundary = "----ebaybizEPSboundary7d01b2"
    pre = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="XML Payload"\r\n'
        "Content-Type: text/xml;charset=utf-8\r\n\r\n"
        f"{xml}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="dummy"; filename="{_xml_escape(picture_name)}.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    payload = pre + image_bytes + post

    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": _TRADING_COMPAT_LEVEL,
        "X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
        "X-EBAY-API-SITEID": _TRADING_SITE_ID,
        "X-EBAY-API-DEV-NAME": creds.dev_id,
        "X-EBAY-API-APP-NAME": creds.app_id,
        "X-EBAY-API-CERT-NAME": creds.cert_id,
        "X-EBAY-API-IAF-TOKEN": iaf,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(_trading_endpoint(creds), data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise EbayAPIError(e.code, f"UploadSiteHostedPictures → HTTP {e.code}",
                           e.read().decode("utf-8", errors="replace")) from e

    ack = _xml_tag(body_text, "Ack") or _xml_tag(body_text, "ack")
    if ack and ack.lower() not in ("success", "warning"):
        raise EbayAPIError(0, "UploadSiteHostedPictures returned a Failure", body_text)
    full_url = _xml_tag(body_text, "FullURL")
    if not full_url:
        raise EbayAPIError(0, "UploadSiteHostedPictures response had no <FullURL>", body_text)
    return full_url


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _xml_tag(xml_text: str, tag: str) -> Optional[str]:
    """Extract the first <tag>...</tag> text (namespace-agnostic, no XML dep)."""
    m = re.search(rf"<(?:\w+:)?{tag}>(.*?)</(?:\w+:)?{tag}>", xml_text, re.DOTALL)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Taxonomy API helpers (introspection — what DRAFT will need)
# ---------------------------------------------------------------------------

def get_default_category_tree_id(marketplace: str = DEFAULT_MARKETPLACE,
                                  creds: Optional[EbayCredentials] = None) -> str:
    """Return the default category tree ID for a marketplace (e.g. '0' for EBAY_US)."""
    data = api_get(
        "/commerce/taxonomy/v1/get_default_category_tree_id",
        query={"marketplace_id": marketplace},
        creds=creds,
    )
    tree_id = data.get("categoryTreeId")
    if not tree_id:
        raise EbayAPIError(0, f"Unexpected Taxonomy response: {data}", json.dumps(data))
    return str(tree_id)


def get_category_aspects(category_id: str,
                         marketplace: str = DEFAULT_MARKETPLACE,
                         tree_id: Optional[str] = None,
                         creds: Optional[EbayCredentials] = None) -> dict:
    """Fetch the item-aspect definitions for a category (Taxonomy API).

    The aspects returned are the fields eBay expects/recommends in
    product.aspects when listing in this category. DRAFT consumes this
    to know which item-specifics to fill and what values are allowed.

    Returns the raw Taxonomy response (key: aspects), which is a list of
    aspect dicts. Each has:
      - localizedAspectName: the aspect name (e.g., "Brand")
      - aspectConstraint: { aspectDataType, aspectMode (FREE_TEXT/SELECTION_ONLY),
                            aspectRequired, aspectUsage, expectedRequiredByDate, ... }
      - aspectValues: list of allowed values (if SELECTION_ONLY); empty if FREE_TEXT
    """
    if tree_id is None:
        tree_id = get_default_category_tree_id(marketplace, creds=creds)
    return api_get(
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category",
        query={"category_id": str(category_id)},
        creds=creds,
        marketplace=marketplace,
    )


def get_category_suggestions(query: str,
                              marketplace: str = DEFAULT_MARKETPLACE,
                              tree_id: Optional[str] = None,
                              creds: Optional[EbayCredentials] = None) -> dict:
    """Ask eBay to suggest leaf categories that match a free-text query.

    Useful for DRAFT to convert "Polo Ralph Lauren vintage fashion catalog"
    into a categoryId (e.g. 1184 for Collectibles > Paper > Catalogs).
    """
    if tree_id is None:
        tree_id = get_default_category_tree_id(marketplace, creds=creds)
    return api_get(
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
        query={"q": query},
        creds=creds,
        marketplace=marketplace,
    )


# ---------------------------------------------------------------------------
# Pretty printer for aspects (CLI use)
# ---------------------------------------------------------------------------

def print_category_aspects(aspects_response: dict, indent: int = 0) -> None:
    """Print Taxonomy `get_item_aspects_for_category` response readable."""
    aspects = aspects_response.get("aspects") or []
    if not aspects:
        print("(no aspects returned)")
        return

    bar = "  " * indent
    for asp in aspects:
        name = asp.get("localizedAspectName", "?")
        c = asp.get("aspectConstraint") or {}
        dtype = c.get("aspectDataType", "STRING")
        mode = c.get("aspectMode", "FREE_TEXT")
        req = "REQUIRED" if c.get("aspectRequired") else "optional"
        usage = c.get("aspectUsage", "")  # RECOMMENDED / OPTIONAL
        max_n = c.get("itemToAspectCardinality", "SINGLE")  # SINGLE / MULTI

        print(f"{bar}{name}  [{req}, usage={usage}, type={dtype}, mode={mode}, cardinality={max_n}]")

        # If SELECTION_ONLY, show allowed values
        vals = asp.get("aspectValues") or []
        if mode == "SELECTION_ONLY" and vals:
            sample = [v.get("localizedValue") for v in vals[:8]]
            more = f" ... +{len(vals) - 8} more" if len(vals) > 8 else ""
            print(f"{bar}  values: {sample}{more}")
        elif vals:
            # FREE_TEXT can still have suggested values
            sample = [v.get("localizedValue") for v in vals[:5]]
            print(f"{bar}  suggested: {sample}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    # Windows default console encoding is cp1252; many of our schema notes
    # use em-dashes and other non-cp1252 chars. Force utf-8 so the printed
    # output is readable regardless of terminal codepage.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="ebaybiz V2 — eBay Sell API client (basic)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--check", action="store_true",
                        help="Verify credentials loaded; report what's available.")
    parser.add_argument("--schema", choices=["inventory_item", "offer", "all"],
                        help="Print the static field reference.")
    parser.add_argument("--category-tree-id", action="store_true",
                        help="Fetch default category tree ID (requires app creds).")
    parser.add_argument("--category-suggestions", metavar="QUERY",
                        help="Suggest categories for a query (requires app creds).")
    parser.add_argument("--category-aspects", metavar="CATEGORY_ID",
                        help="Fetch aspects for a category (requires app creds).")
    parser.add_argument("--user-consent-url", action="store_true",
                        help="Print the URL the user must visit to grant access.")
    parser.add_argument("--exchange-code", metavar="CODE",
                        help="Exchange authorization_code for refresh+access tokens.")
    parser.add_argument("--marketplace", default=DEFAULT_MARKETPLACE,
                        help=f"Marketplace ID (default {DEFAULT_MARKETPLACE}).")
    parser.add_argument("--tree-id", default=None,
                        help="Override category tree ID (skips lookup).")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of pretty-printed.")

    args = parser.parse_args()

    # Wrap live-API operations in a clean error handler so credential
    # / network errors print as a one-screen message, not a traceback.
    try:
        _cli_run(parser, args)
    except (EbayAuthError, EbayAPIError, ConfigError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


def _cli_run(parser: "argparse.ArgumentParser", args: "argparse.Namespace") -> None:
    # --schema works without credentials
    if args.schema:
        names = ["inventory_item", "offer"] if args.schema == "all" else [args.schema]
        for n in names:
            schema, display_name = get_schema(n)
            print_schema(schema, display_name)
            print()
        return

    # --check works without credentials (it reports their absence)
    if args.check:
        creds = load_credentials()
        print(f"environment:        {creds.environment}")
        print(f"app_id:             {'set' if creds.app_id else '(missing)'}")
        print(f"cert_id:            {'set' if creds.cert_id else '(missing)'}")
        print(f"dev_id:             {'set' if creds.dev_id else '(missing — fine for REST)'}")
        print(f"redirect_uri:       {creds.redirect_uri or '(missing — needed for user-consent flow)'}")
        print(f"user_refresh_token: {'set' if creds.user_refresh_token else '(missing — needed for writes)'}")
        print()
        if creds.has_app:
            try:
                token = get_app_access_token(creds)
                print(f"[OK] App token obtained ({len(token)} chars, cached for ~2h)")
            except EbayAuthError as e:
                print(f"[X] App token fetch failed: {e}")
                sys.exit(1)
        else:
            print("[--] App-context fetch skipped (app_id + cert_id required).")
        if creds.has_user:
            print("[OK] User refresh_token present — write operations possible.")
        else:
            print("[--] User refresh_token absent — only read operations available.")
        return

    # Live API operations — require credentials
    creds = load_credentials()

    if args.user_consent_url:
        print(user_consent_url(creds))
        return

    if args.exchange_code:
        tokens = exchange_authorization_code(args.exchange_code, creds)
        if args.json:
            print(json.dumps(tokens, indent=2))
        else:
            print("Token response:")
            for k, v in tokens.items():
                if k in ("access_token", "refresh_token"):
                    print(f"  {k}: {str(v)[:24]}... ({len(str(v))} chars)")
                else:
                    print(f"  {k}: {v}")
            rt = tokens.get("refresh_token")
            if rt:
                print()
                print(f"ACTION: Add the following to {config_path()}:")
                print(f"  ebay:")
                print(f"    user_refresh_token: \"{rt}\"")
        return

    if args.category_tree_id:
        tree_id = get_default_category_tree_id(args.marketplace, creds=creds)
        print(f"marketplace={args.marketplace}  tree_id={tree_id}")
        return

    if args.category_suggestions:
        data = get_category_suggestions(args.category_suggestions, args.marketplace,
                                         tree_id=args.tree_id, creds=creds)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            suggestions = data.get("categorySuggestions") or []
            print(f"{len(suggestions)} suggestion(s) for: {args.category_suggestions!r}")
            for i, sug in enumerate(suggestions[:10], 1):
                cat = sug.get("category") or {}
                path = " > ".join(c.get("categoryName", "?")
                                  for c in (sug.get("categoryTreeNodeAncestors") or [])[::-1])
                if path:
                    path += " > "
                print(f"  {i}. [{cat.get('categoryId')}] {path}{cat.get('categoryName')}")
        return

    if args.category_aspects:
        data = get_category_aspects(args.category_aspects, args.marketplace,
                                     tree_id=args.tree_id, creds=creds)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            aspects = data.get("aspects") or []
            print(f"=== Aspects for category {args.category_aspects} "
                  f"(marketplace={args.marketplace}) — {len(aspects)} total ===")
            print_category_aspects(data)
        return

    parser.print_help()


if __name__ == "__main__":
    _cli()
