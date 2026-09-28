"""
Vinted Pro Integrations API client — the second marketplace's transport layer.

Vinted's official seller API ("Vinted Pro Integrations",
https://pro-docs.svc.vinted.com/) covers the whole loop the eBay side of this
repo uses: create/update/delete listings (batches of up to 100), catalog
ontologies and price suggestions, orders, shipments, shipping-label PDFs, and
webhooks. This module is the transport only — signing, credentials, the
endpoint calls, and the write guardrail. Endpoint shapes follow Vinted's OpenAPI definition
(https://pro-docs.svc.vinted.com/downloads/api.yml). Mapping a draft onto a Vinted item
(catalog_id, status_id, package_size_id, colours, photo URLs) is a separate
step that needs a real ontology dump to design against; see tools/vinted.py
`ontologies --out`.

----- Access (the part code can't solve) -----

The API is NOT self-serve. It is limited to allowlisted Vinted Pro business
accounts; tokens are issued in the Integrations Portal
(https://pro-portal.svc.vinted.com/) only after Vinted enables the account.
As documented (Sept 2026) the Pro markets are EU/UK — AT BE DE ES FR IT LU NL
PT UK, currencies EUR/GBP — and a new API user gets 500 active item slots,
reviewed after 30 days. A US Vinted account can sell by hand but has no
documented API path. Everything below works the moment a token exists; until
then `ebz vinted check` says exactly what is missing.

----- Auth -----

A token is issued as one string, "<access_key>,<signing_key>". Each request
carries:

    X-Vpi-Access-Key:  <access_key>
    X-Vpi-Hmac-Sha256: t=<unix_seconds>,v1=<hex hmac>

where the HMAC-SHA256 (keyed with the signing key) is over

    "<t>.<METHOD>.<path+query>.<access_key>.<body>"

with body = "" when there is none. The server rejects stale timestamps, so a
retry re-signs rather than replaying the old header. The exact body bytes
that are signed are the bytes sent — serialise once.

----- Environments -----

    sandbox     https://pro-public-sandbox.svc.vinted.com  (Dev Mode — safe)
    production  https://pro.svc.vinted.com

Sandbox and production issue separate tokens. The default is SANDBOX: which
environment is live is an account-setup decision a human makes in config,
never inferred here.

----- Guardrail (same shape as list_edit --publish and easypost buy_label) -----

Reads run freely. Every call that changes the account — create / update /
delete items, cancel an order, relist, add or delete a webhook — is a DRY RUN
unless `confirm=True`: without it, NO request is made and the function
returns what it WOULD have sent. Item creation additionally forces
`is_draft: true` unless `publish=True` is passed as well, so a confirmed
create lands in Vinted's drafts, not the live catalog — the REVIEW gate
still applies on this marketplace.

----- Credentials -----

Per store, mirroring ebay_client.load_credentials() (GH #147/#156):

    vinted:
      environment: sandbox            # default store
      sandbox:    {token: "AK,SK"}
      production: {token: "AK,SK"}
      stores:
        junk:                         # a named store — its own block
          environment: sandbox
          sandbox: {token: "AK,SK"}

Env vars VINTED_PRO_TOKEN / VINTED_ENVIRONMENT override config for whichever
store is resolved (handy for a one-off sandbox run without editing YAML).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from config import ConfigError, config_path, load_config
from stores import DEFAULT_STORE, resolve_store_name

ENVIRONMENTS = {
    "sandbox": "https://pro-public-sandbox.svc.vinted.com",
    "production": "https://pro.svc.vinted.com",
}
DEFAULT_ENVIRONMENT = "sandbox"

TOKEN_ENV_VAR = "VINTED_PRO_TOKEN"
ENVIRONMENT_ENV_VAR = "VINTED_ENVIRONMENT"

MAX_BATCH = 100          # documented cap for create/update/delete/validate
PRO_MARKETS = ("AT", "BE", "DE", "ES", "FR", "IT", "LU", "NL", "PT", "UK")
CURRENCIES = ("EUR", "GBP")

PORTAL_URL = "https://pro-portal.svc.vinted.com/"
DOCS_URL = "https://pro-docs.svc.vinted.com/"

WEBHOOK_EVENTS = (
    "CREATE_ITEM_SUCCESS", "CREATE_ITEM_FAILURE", "UPDATE_ITEM_SUCCESS",
    "UPDATE_ITEM_FAILURE", "DELETE_ITEM_SUCCESS", "DELETE_ITEM_FAILURE",
    "ITEM_DELETED", "ITEM_SOLD", "ITEM_UPDATED", "ORDER_CREATED",
    "CANCEL_ORDER_FAILURE", "SHIPMENT_LABEL_CREATED", "ITEM_PUBLISHED",
    "ORDER_CANCELLED", "VINTED_AUTHENTICATION_ERROR", "ITEM_DRAFT_UPDATED",
    "ITEM_REUPLOADED",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class VintedAuthError(RuntimeError):
    """Vinted rejected the token or signature (HTTP 401/403).

    A *missing* token never reaches this point — load_credentials() raises
    ConfigError before any request is built.
    """


class VintedAPIError(RuntimeError):
    """A Vinted API call returned a non-2xx response (or never got one)."""
    def __init__(self, status: int, message: str, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VintedCredentials:
    environment: str
    access_key: str
    signing_key: str
    store: str = DEFAULT_STORE

    @property
    def base_url(self) -> str:
        return ENVIRONMENTS[self.environment]

    def __repr__(self) -> str:  # never print the signing key
        return (f"VintedCredentials(environment={self.environment!r}, "
                f"access_key={self.access_key[:6]}…, store={self.store!r})")


def parse_token(token: str) -> tuple[str, str]:
    """Split the portal's "<access_key>,<signing_key>" string."""
    parts = [p.strip() for p in str(token).split(",")]
    if len(parts) != 2 or not all(parts):
        raise ConfigError(
            "Vinted Pro token must be '<access_key>,<signing_key>' — copy the "
            "whole string the Integrations Portal shows, comma included.")
    return parts[0], parts[1]


def _setup_help(store: str) -> str:
    block = ("vinted:\n        environment: sandbox\n        sandbox:\n"
             "          token: \"<access_key>,<signing_key>\"")
    if store != DEFAULT_STORE:
        block = ("vinted:\n        stores:\n          " + store + ":\n"
                 "            environment: sandbox\n            sandbox:\n"
                 "              token: \"<access_key>,<signing_key>\"")
    return (f"  Set {TOKEN_ENV_VAR}, OR add to {config_path()}:\n"
            f"      {block}\n"
            f"  Tokens come from the Integrations Portal ({PORTAL_URL}), which\n"
            f"  only opens once Vinted allowlists the Pro business account.")


def load_credentials(store: Optional[str] = None) -> VintedCredentials:
    """Vinted credentials for a store. Store precedence is lib/stores.py's.

    The default store reads `vinted.environment` + `vinted.<env>.token`; a
    named store reads the same shape under `vinted.stores.<name>`. The env
    vars override whichever store resolved.
    """
    store = resolve_store_name(store)
    section = load_config().get("vinted") or {}
    if store != DEFAULT_STORE:
        stores_section = section.get("stores") or {}
        if store not in stores_section:
            known = ", ".join(sorted(stores_section)) or "(none configured)"
            raise ConfigError(
                f"Vinted store '{store}' not found under vinted.stores in "
                f"{config_path()} (configured: {known}).\n" + _setup_help(store))
        section = stores_section[store] or {}

    env = os.environ.get(ENVIRONMENT_ENV_VAR) or section.get("environment") \
        or DEFAULT_ENVIRONMENT
    if env not in ENVIRONMENTS:
        raise ConfigError(
            f"Vinted environment must be one of {', '.join(ENVIRONMENTS)} "
            f"(got {env!r}).")

    token = os.environ.get(TOKEN_ENV_VAR) or (section.get(env) or {}).get("token")
    if not token:
        raise ConfigError(
            f"No Vinted Pro {env} token for store '{store}'.\n" + _setup_help(store))
    access_key, signing_key = parse_token(token)
    return VintedCredentials(environment=env, access_key=access_key,
                             signing_key=signing_key, store=store)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def sign(signing_key: str, access_key: str, method: str, path: str,
         body: str = "", timestamp: Optional[int] = None) -> str:
    """The X-Vpi-Hmac-Sha256 header value for one request.

    `path` is the request path INCLUDING its query string, exactly as sent.
    """
    t = int(time.time()) if timestamp is None else int(timestamp)
    payload = ".".join([str(t), method.upper(), path, access_key, body or ""])
    digest = hmac.new(signing_key.encode("utf-8"), payload.encode("utf-8"),
                      hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def verify_webhook(signing_key: str, header: str, raw_body: bytes | str,
                   tolerance_s: Optional[int] = 300,
                   now: Optional[float] = None) -> bool:
    """Check an X-Vpi-Webhook-Hmac-Sha256 header against the raw request body.

    Payload is "<t>.<body>", keyed with the webhook's own signing_key (from
    the create-webhook response — NOT the API token's). `tolerance_s=None`
    skips the replay-window check.
    """
    fields = dict(p.split("=", 1) for p in (header or "").split(",") if "=" in p)
    t, sig = fields.get("t"), fields.get("v1")
    if not t or not sig or not t.isdigit():
        return False
    if tolerance_s is not None:
        now = time.time() if now is None else now
        if abs(now - int(t)) > tolerance_s:
            return False
    body = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    expected = hmac.new(signing_key.encode("utf-8"), f"{t}.{body}".encode("utf-8"),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Transport — same retry policy as ebay_client / easypost_client api_send
# ---------------------------------------------------------------------------

def _with_query(path: str, params: Optional[dict]) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            path += "?" + urllib.parse.urlencode(clean, doseq=True)
    return path


def api_send(method: str, path: str, body: Any = None,
             params: Optional[dict] = None,
             creds: Optional[VintedCredentials] = None,
             raw: bool = False, retry_network_errors: bool = True) -> Any:
    """One signed request. Returns decoded JSON ({} for empty 2xx), or bytes
    when raw=True (the shipment-label PDF).

    Transient 5xx retries only for idempotent methods; a network error on a
    POST is retried only when retry_network_errors (callers that create
    things pass False, so a lost response can't double-create).
    """
    creds = creds or load_credentials()
    m = method.upper()
    full_path = _with_query(path, params)
    body_text = "" if body is None else json.dumps(body, separators=(",", ":"))
    data = body_text.encode("utf-8") if body is not None else None
    idempotent = m in ("GET", "PUT", "DELETE")

    for attempt in range(3):
        headers = {
            "Accept": "application/pdf" if raw else "application/json",
            "Content-Type": "application/json",
            "X-Vpi-Access-Key": creds.access_key,
            # re-signed per attempt: the server rejects stale timestamps
            "X-Vpi-Hmac-Sha256": sign(creds.signing_key, creds.access_key,
                                      m, full_path, body_text),
        }
        req = urllib.request.Request(creds.base_url + full_path, data=data,
                                     method=m, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                if raw:
                    return payload
                text = payload.decode("utf-8")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            if e.code in (401, 403):
                raise VintedAuthError(
                    f"Vinted rejected the request (HTTP {e.code}) on {m} "
                    f"{full_path} [{creds.environment}]: {err_body}") from e
            if e.code >= 500 and idempotent and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise VintedAPIError(e.code, f"{m} {full_path} -> HTTP {e.code}",
                                 err_body) from e
        except urllib.error.URLError as e:
            if (idempotent or retry_network_errors) and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise VintedAPIError(0, f"{m} {full_path} -> network error: {e}") from e


# ---------------------------------------------------------------------------
# Write guardrail
# ---------------------------------------------------------------------------

@dataclass
class DryRun:
    """What a guarded write WOULD have sent. No request was made."""
    method: str
    path: str
    body: Any = None
    environment: str = ""

    def __str__(self) -> str:
        return (f"DRY RUN [{self.environment}] {self.method} {self.path}\n"
                + (json.dumps(self.body, indent=2) if self.body is not None else ""))


def _guarded(method: str, path: str, body: Any, confirm: bool,
             creds: Optional[VintedCredentials], **kw) -> Any:
    creds = creds or load_credentials()
    if not confirm:
        return DryRun(method, path, body, creds.environment)
    return api_send(method, path, body=body, creds=creds,
                    retry_network_errors=False, **kw)


def _batch(items: list, what: str) -> list:
    if not items:
        raise ValueError(f"{what}: no items given")
    if len(items) > MAX_BATCH:
        raise ValueError(f"{what}: {len(items)} items — Vinted takes at most "
                         f"{MAX_BATCH} per request; split the batch")
    return items


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def get_ontologies(creds: Optional[VintedCredentials] = None) -> dict:
    """Catalogs, colours, attributes, sizes, statuses (condition), package sizes."""
    return api_send("GET", "/api/v1/ontologies", creds=creds)


def price_suggestions(catalog_id: int, brand_id: Optional[int] = None,
                      status_id: Optional[int] = None,
                      creds: Optional[VintedCredentials] = None) -> dict:
    return api_send("GET", "/api/v2/item-price-suggestions",
                    params={"catalog_id": catalog_id, "brand_id": brand_id,
                            "status_id": status_id}, creds=creds)


def list_items(after_item_id: Optional[str] = None, limit: Optional[int] = None,
               creds: Optional[VintedCredentials] = None) -> dict:
    return api_send("GET", "/api/v1/items",
                    params={"after_item_id": after_item_id, "limit": limit},
                    creds=creds)


def item_status(item_uuid: str, creds: Optional[VintedCredentials] = None) -> dict:
    return api_send("GET", f"/api/v1/items/{urllib.parse.quote(item_uuid)}/status",
                    creds=creds)


def validate_items(items: list[dict],
                   creds: Optional[VintedCredentials] = None) -> dict:
    """Server-side validation, nothing created. Free — no confirm gate."""
    return api_send("POST", "/api/v1/items/validate",
                    body={"items": _batch(items, "validate_items")}, creds=creds)


def create_items(items: list[dict], confirm: bool = False, publish: bool = False,
                 creds: Optional[VintedCredentials] = None) -> Any:
    """Create listings. DRY RUN unless confirm; lands as DRAFTS unless publish.

    Creation is asynchronous on Vinted's side — results arrive as
    CREATE_ITEM_SUCCESS/FAILURE webhooks and via item_status().
    """
    items = _batch(items, "create_items")
    if not publish:
        items = [{**it, "is_draft": True} for it in items]
    return _guarded("POST", "/api/v1/items", {"items": items}, confirm, creds)


def update_items(items: list[dict], confirm: bool = False,
                 creds: Optional[VintedCredentials] = None) -> Any:
    return _guarded("PUT", "/api/v1/items",
                    {"items": _batch(items, "update_items")}, confirm, creds)


def delete_items(item_ids: list[str], confirm: bool = False,
                 creds: Optional[VintedCredentials] = None) -> Any:
    """Only successfully-listed items can be deleted; IN_PROGRESS ones can't.
    Unknown ids fail silently on Vinted's side (202 regardless)."""
    return _guarded("DELETE", "/api/v1/items",
                    {"item_ids": _batch(list(item_ids), "delete_items")},
                    confirm, creds)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def list_orders(after_id: Optional[int] = None,
                creds: Optional[VintedCredentials] = None) -> dict:
    """Orders; page by passing the last id seen (query `after-id`)."""
    return api_send("GET", "/api/v1/orders", params={"after-id": after_id},
                    creds=creds)


def get_order(order_id: str, creds: Optional[VintedCredentials] = None) -> dict:
    return api_send("GET", f"/api/v1/orders/{urllib.parse.quote(str(order_id))}",
                    creds=creds)


def get_shipment(order_id: str, creds: Optional[VintedCredentials] = None) -> dict:
    return api_send("GET",
                    f"/api/v1/orders/{urllib.parse.quote(str(order_id))}/shipment",
                    creds=creds)


def shipment_label_pdf(order_id: str,
                       creds: Optional[VintedCredentials] = None) -> bytes:
    """The prepaid label as PDF bytes. Reading it spends nothing."""
    return api_send("GET",
                    f"/api/v1/orders/{urllib.parse.quote(str(order_id))}/shipment-label",
                    creds=creds, raw=True)


def cancel_order(order_id: str, explanation: str, confirm: bool = False,
                 creds: Optional[VintedCredentials] = None) -> Any:
    """Cancel an order (asynchronous). Vinted requires a reason, max 100 chars."""
    explanation = (explanation or "").strip()
    if not explanation or len(explanation) > 100:
        raise ValueError("cancel_order: explanation is required, at most 100 characters")
    return _guarded("POST",
                    f"/api/v1/orders/{urllib.parse.quote(str(order_id))}/cancel",
                    {"cancellation_reason_explanation": explanation}, confirm, creds)


def relist_orders(order_ids: list[int], confirm: bool = False,
                  creds: Optional[VintedCredentials] = None) -> Any:
    """Relist the items of cancelled escrow orders."""
    return _guarded("POST", "/api/v1/orders/relist",
                    {"order_ids": [int(i) for i in _batch(list(order_ids), "relist_orders")]},
                    confirm, creds)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def list_webhooks(creds: Optional[VintedCredentials] = None) -> Any:
    return api_send("GET", "/api/v1/webhooks", creds=creds)


def create_webhook(url: str, event_types: list[str], confirm: bool = False,
                   creds: Optional[VintedCredentials] = None) -> Any:
    """Register a webhook. The response carries the webhook's own signing_key
    — store it; verify_webhook() needs it and Vinted won't show it again."""
    unknown = sorted(set(event_types) - set(WEBHOOK_EVENTS))
    if unknown:
        raise ValueError(f"unknown Vinted webhook event(s): {', '.join(unknown)}")
    return _guarded("POST", "/api/v1/webhooks",
                    {"url": url, "event_types": list(event_types)}, confirm, creds)


def delete_webhook(webhook_id: str, confirm: bool = False,
                   creds: Optional[VintedCredentials] = None) -> Any:
    return _guarded("DELETE",
                    f"/api/v1/webhooks/{urllib.parse.quote(str(webhook_id))}",
                    None, confirm, creds)


# ---------------------------------------------------------------------------
# Dev Mode (sandbox only)
# ---------------------------------------------------------------------------

def dev_trigger_item_sold(item_id: str,
                          creds: Optional[VintedCredentials] = None) -> Any:
    """Simulate a sale in the sandbox — the way to exercise order handling
    before any real buyer exists. Refuses to run against production."""
    creds = creds or load_credentials()
    if creds.environment != "sandbox":
        raise VintedAPIError(0, "dev triggers exist only in the sandbox")
    return api_send("POST",
                    f"/dev/v1/triggers/item-sold/{urllib.parse.quote(str(item_id))}",
                    creds=creds, retry_network_errors=False)
