"""
Whatnot Seller API client — the transport half of the Whatnot channel.

Whatnot's Seller API is GraphQL-only (https://developers.whatnot.com/docs):

    staging     https://api.stage.whatnot.com/seller-api/graphql
    production  https://api.whatnot.com/seller-api/graphql

As of 2026-10 it is a *Developer Preview* and Whatnot is not accepting new
applicants. An account that already has access generates a personal access
token in the seller dashboard (`wn_access_tk_test_...` on staging,
`wn_access_tk_...` on production). That token is the only credential this
client uses: it carries `full_access` on the seller's own account, which OAuth
apps never get, and it needs no refresh dance. OAuth (third-party "Connect"
apps) is deliberately not implemented — this pipeline only ever lists to its
own account.

----- Credential source -----

Same precedence as every other API key in this repo (see lib/config.py):
    1. WHATNOT_ACCESS_TOKEN environment variable
    2. config.yaml  whatnot.<environment>.access_token
    3. ConfigError, with setup instructions

`whatnot.environment` picks `stage` (the default, as the docs recommend while
the API is in preview) or `production`.

----- What lives here vs lib/whatnot_list.py -----

This module only speaks GraphQL and reads public reference data (the product
taxonomy). Building a product from a draft, the confirm-gated publish, and
the ledger live in lib/whatnot_list.py — the same split as
lib/ebay_client.py / lib/list_edit.py.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config import ConfigError, config_path, load_config

ENDPOINTS = {
    "stage": "https://api.stage.whatnot.com/seller-api/graphql",
    "production": "https://api.whatnot.com/seller-api/graphql",
}
TAXONOMY_URL = "https://api.whatnot.com/seller-api/rest/product-taxonomy/US.txt"
DEFAULT_ENVIRONMENT = "stage"
# Whatnot asks for no more than 10 req/s during the preview; this client is
# one-request-at-a-time, so spacing retries is the only throttle it needs.
_RETRIES = 3


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class WhatnotAuthError(RuntimeError):
    """The token was rejected (HTTP 401/403). A *missing* token never gets
    here — load_credentials() raises ConfigError before any HTTP call."""


class WhatnotAPIError(RuntimeError):
    """A non-2xx response, a top-level GraphQL `errors` array, or a mutation's
    `userErrors`. `errors` is the list of {message, field?} dicts."""
    def __init__(self, message: str, *, status: int = 0,
                 errors: Optional[list] = None, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.errors = errors or []
        self.body = body


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass
class WhatnotCredentials:
    environment: str
    access_token: str

    @property
    def endpoint(self) -> str:
        return ENDPOINTS[self.environment]


def _whatnot_config() -> dict:
    sec = load_config().get("whatnot") or {}
    return sec if isinstance(sec, dict) else {}


def whatnot_setting(key: str, default: Any = None) -> Any:
    """A top-level `whatnot.<key>` value (shipping_profile_id, attributes, ...)."""
    return _whatnot_config().get(key, default)


def load_credentials(environment: Optional[str] = None) -> WhatnotCredentials:
    sec = _whatnot_config()
    env = (environment or os.environ.get("WHATNOT_ENVIRONMENT")
           or sec.get("environment") or DEFAULT_ENVIRONMENT)
    env = str(env).strip().lower()
    if env == "staging":
        env = "stage"
    if env not in ENDPOINTS:
        raise ConfigError(f"whatnot.environment must be 'stage' or 'production', not {env!r}")

    token = os.environ.get("WHATNOT_ACCESS_TOKEN")
    if not token:
        token = ((sec.get(env) or {}) if isinstance(sec.get(env), dict) else {}).get("access_token")
    if not token or "REPLACE_ME" in str(token):
        raise ConfigError(
            f"Whatnot access token not found for environment {env!r}.\n"
            f"  Set WHATNOT_ACCESS_TOKEN, OR add this to {config_path()}:\n"
            f"      whatnot:\n"
            f"        environment: {env}\n"
            f"        {env}:\n"
            f"          access_token: \"wn_access_tk_...\"\n"
            f"  The token is generated in Whatnot's seller dashboard once Whatnot has\n"
            f"  granted the account Seller API access (Developer Preview — gated).")
    token = str(token).strip()
    # Staging and production tokens are visibly different; catch the swap
    # before it turns into a confusing 401 (or a real listing on production).
    is_test = token.startswith("wn_access_tk_test_")
    if env == "production" and is_test:
        raise ConfigError("whatnot.environment is production but the token is a staging "
                          "(wn_access_tk_test_) token.")
    if env == "stage" and token.startswith("wn_access_tk_") and not is_test:
        raise ConfigError("whatnot.environment is stage but the token is a production token.")
    return WhatnotCredentials(environment=env, access_token=token)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def graphql(query: str, variables: Optional[dict] = None,
            creds: Optional[WhatnotCredentials] = None) -> dict:
    """POST one GraphQL operation; return its `data`.

    Raises WhatnotAPIError on HTTP errors or a top-level `errors` array.
    Queries retry on network errors, 5xx and 429. Mutations retry on 429 only:
    a 5xx or a dropped connection leaves it unknown whether the write landed,
    and a blind retry of productCreate would make a duplicate product.
    """
    creds = creds or load_credentials()
    is_mutation = query.lstrip().startswith("mutation")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    last: Optional[Exception] = None
    for attempt in range(1, _RETRIES + 1):
        req = urllib.request.Request(
            creds.endpoint, data=payload, method="POST",
            headers={"Authorization": f"Bearer {creds.access_token}",
                     "Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "ebaybiz-whatnot/1"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace") if e.fp else ""
            if e.code in (401, 403):
                raise WhatnotAuthError(
                    f"Whatnot rejected the access token (HTTP {e.code}) on "
                    f"{creds.environment}: {text[:300]}") from None
            retryable = e.code == 429 or (e.code >= 500 and not is_mutation)
            last = WhatnotAPIError(f"HTTP {e.code} from Whatnot: {text[:500]}",
                                   status=e.code, body=text)
            if retryable and attempt < _RETRIES:
                time.sleep(1.0 * attempt)
                continue
            raise last from None
        except urllib.error.URLError as e:
            last = WhatnotAPIError(f"network error talking to Whatnot: {e.reason}")
            if not is_mutation and attempt < _RETRIES:
                time.sleep(1.0 * attempt)
                continue
            raise last from None
        try:
            doc = json.loads(body)
        except json.JSONDecodeError:
            raise WhatnotAPIError(f"non-JSON response from Whatnot: {body[:300]}",
                                  body=body) from None
        if doc.get("errors"):
            msgs = "; ".join(str(x.get("message")) for x in doc["errors"])
            raise WhatnotAPIError(f"GraphQL error: {msgs}", errors=doc["errors"], body=body)
        return doc.get("data") or {}
    raise last or WhatnotAPIError("Whatnot request failed")


def raise_user_errors(payload: Optional[dict], op: str) -> dict:
    """Mutations report validation failures in `userErrors`, with HTTP 200."""
    payload = payload or {}
    errs = payload.get("userErrors") or []
    if errs:
        msgs = "; ".join(
            (".".join(e.get("field") or []) + ": " if e.get("field") else "") + str(e.get("message"))
            for e in errs)
        raise WhatnotAPIError(f"{op} rejected: {msgs}", errors=errs)
    return payload


def nodes(connection: Optional[dict]) -> list[dict]:
    """Flatten a Relay connection ({edges:[{node}]}) — tolerate a bare `nodes`."""
    if not connection:
        return []
    if "edges" in connection:
        return [e.get("node") or {} for e in connection.get("edges") or []]
    return list(connection.get("nodes") or [])


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def taxonomy_global_id(node_id: str | int) -> str:
    """Whatnot wants `taxonomyId` as base64("ProductTaxonomyNode:<id>"). Accept
    either the bare number from US.txt or an already-encoded id."""
    s = str(node_id).strip()
    if s.isdigit():
        return base64.b64encode(f"ProductTaxonomyNode:{s}".encode()).decode()
    return s


def fetch_taxonomy(cache: Optional[Path] = None, max_age_days: int = 30) -> list[tuple[str, str]]:
    """The public US product taxonomy as [(id, "A > B > C")]. Needs no token.
    Cached next to the config file for `max_age_days`."""
    cache = cache or (config_path().parent / ".whatnot_taxonomy_US.txt")
    text = None
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_days * 86400:
            text = cache.read_text(encoding="utf-8")
    except OSError:
        text = None
    if text is None:
        # Python's default User-Agent gets a 403 from Whatnot's CDN.
        req = urllib.request.Request(TAXONOMY_URL, headers={"User-Agent": "ebaybiz-whatnot/1"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
        try:
            cache.write_text(text, encoding="utf-8")
        except OSError:
            pass
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\d+)\s+-\s+(.+)$", line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def search_taxonomy(terms: str, limit: int = 25) -> list[tuple[str, str]]:
    words = [w.lower() for w in terms.split() if w.strip()]
    hits = [(i, p) for i, p in fetch_taxonomy() if all(w in p.lower() for w in words)]
    # Prefer the most specific (deepest) matches first.
    hits.sort(key=lambda t: -t[1].count(">"))
    return hits[:limit]


def get_shipping_profiles(creds: Optional[WhatnotCredentials] = None) -> list[dict]:
    data = graphql("query { shippingProfiles { id name weight weightUnit } }", creds=creds)
    return list(data.get("shippingProfiles") or [])


def get_product_attributes(taxonomy_id: str | int,
                           creds: Optional[WhatnotCredentials] = None) -> list[dict]:
    """Attributes (condition, brand, ...) and their allowed options for a
    taxonomy node — the IDs that go into ProductInput.attributes."""
    q = ("query($filter: ProductAttributeFilterInput!) {"
         " productAttributes(first: 100, filter: $filter) {"
         "  edges { node { id key name valueType options required } } } }")
    data = graphql(q, {"filter": {"taxonomyId": {"equals": taxonomy_global_id(taxonomy_id)}}}, creds=creds)
    return nodes(data.get("productAttributes"))


def whoami(creds: Optional[WhatnotCredentials] = None) -> dict:
    return graphql("query { me { id username canGoLive } }", creds=creds).get("me") or {}
