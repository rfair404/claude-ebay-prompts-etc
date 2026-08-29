"""
EasyPost self-serve carrier-rate API client (GH #80).

eBay's own Logistics API is a confirmed dead end for a small seller —
Limited Release, invitation-only, USPS-only (see #32). This module bypasses
it entirely and quotes/buys the label from EasyPost instead: pay-as-you-go,
free up to 3,000 labels/month, no contract, rates across USPS/UPS/FedEx/DHL.

The purchased label's tracking number comes back in a shape that feeds
straight into `tools/pick_list.py --record-tracking ORDER_ID --carrier CODE
--tracking-number NUM --confirm` (#70) to advance the local ledger to
SHIPPED — that endpoint doesn't care who sold the postage, and this module
does not duplicate the ledger write. (#70 has not landed on `main` as of
this writing — see tools/ship_buy.py's docstring.)

----- Guardrail (same shape as lib/list_edit.py --publish / --end) -----

Quoting rates (get_rates) spends nothing and may run freely, no
confirmation gate. Buying a label (buy_label) spends real money: it is a
DRY RUN unless `confirm=True` is passed explicitly. Without it, buy_label()
makes NO call to EasyPost's purchase endpoint — it only reports what WOULD
be bought and its cost. Never inferred, never called from an unattended
poll loop.

----- Auth -----

EasyPost uses HTTP Basic Auth: the API key as the username, empty password.
EasyPost issues separate test and production keys from the same dashboard;
a test key's "purchases" never charge a real carrier. Which one is
configured is an account-setup decision a human makes — this client does
not infer or switch it.

----- Key source -----

Same precedence as every other API key in this repo (see config.py):
    1. EASYPOST_API_KEY environment variable
    2. config.yaml `easypost.api_key`
    3. ConfigError, with setup instructions

----- CLI -----

See tools/ship_quote.py (`ebz ship-quote` — free) and tools/ship_buy.py
(`ebz ship-buy` — DRY RUN unless --confirm).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from typing import Optional

from config import ConfigError, config_path, load_config

API_BASE = "https://api.easypost.com/v2"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EasyPostAuthError(RuntimeError):
    """Raised when the EasyPost API key is missing or rejected."""


class EasyPostAPIError(RuntimeError):
    """Raised when an EasyPost API call returns a non-2xx response."""
    def __init__(self, status: int, message: str, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """EasyPost API key. Precedence: env var > config file > error.

    Mirrors config.get_apify_token() / config.get_anthropic_key() exactly:
    EASYPOST_API_KEY env var first, then config.yaml `easypost.api_key`.
    """
    env = os.environ.get("EASYPOST_API_KEY")
    if env:
        return env

    config = load_config()
    key = (config.get("easypost") or {}).get("api_key")
    if key:
        return str(key)

    raise ConfigError(
        f"EasyPost API key not found.\n"
        f"  Set the EASYPOST_API_KEY environment variable, OR\n"
        f"  add this to {config_path()}:\n"
        f"      easypost:\n"
        f"        api_key: \"<your-key>\"\n"
        f"  Get a key (test or production) at https://www.easypost.com/account/api-keys\n"
        f"  A human still has to create the account and fund its balance before\n"
        f"  any real purchase can succeed — that step is deliberately not automated."
    )


# ---------------------------------------------------------------------------
# Generic JSON request — same retry policy as ebay_client.api_send
# ---------------------------------------------------------------------------

def api_send(method: str, path: str, body: Optional[dict] = None,
             api_key: Optional[str] = None) -> dict:
    """Issue a JSON request against the EasyPost REST API.

    Returns the decoded JSON body (or {} for an empty 2xx). Raises
    EasyPostAuthError on a 401 (bad/missing key) and EasyPostAPIError on any
    other non-2xx, carrying EasyPost's error body for diagnosis.

    Retry policy is deliberately identical to ebay_client.api_send: transient
    5xx is retried only for idempotent methods (GET/PUT/DELETE) so a POST
    that creates or spends money can never double-fire from a retry; a
    network error (the request likely never reached EasyPost) retries
    regardless of method.
    """
    api_key = api_key or get_api_key()
    if not path.startswith("/"):
        path = "/" + path
    url = API_BASE + path

    data = json.dumps(body).encode("utf-8") if body is not None else None
    basic = b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

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
            if e.code == 401:
                raise EasyPostAuthError(
                    f"EasyPost rejected the API key (HTTP 401): {body_text}"
                ) from e
            err = EasyPostAPIError(e.code, f"{m} {path} -> HTTP {e.code}", body_text)
            if e.code >= 500 and idempotent and attempt < 2:
                time.sleep(0.8 * (attempt + 1)); continue
            raise err from e
        except urllib.error.URLError as e:
            err = EasyPostAPIError(0, f"{m} {path} -> network error: {e}", None)
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1)); continue
            raise err from e


# ---------------------------------------------------------------------------
# Shipment inputs
# ---------------------------------------------------------------------------

@dataclass
class Address:
    """A shipping address — EasyPost's `address` object (subset used here)."""
    name: str
    street1: str
    city: str
    state: str
    zip: str
    country: str = "US"
    street2: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"name": self.name, "street1": self.street1, "city": self.city,
             "state": self.state, "zip": self.zip, "country": self.country}
        if self.street2:
            d["street2"] = self.street2
        if self.phone:
            d["phone"] = self.phone
        if self.email:
            d["email"] = self.email
        return d


@dataclass
class Parcel:
    """A parcel — EasyPost's `parcel` object. weight is OUNCES, dims INCHES
    (EasyPost's default `imperial` unit system, which this client assumes).
    Dimensions are optional (weight-only quoting), but most carrier
    services need them to return a full rate table.
    """
    weight_oz: float
    length_in: Optional[float] = None
    width_in: Optional[float] = None
    height_in: Optional[float] = None

    def to_dict(self) -> dict:
        d: dict = {"weight": self.weight_oz}
        if self.length_in and self.width_in and self.height_in:
            d.update({"length": self.length_in, "width": self.width_in,
                      "height": self.height_in})
        return d


# ---------------------------------------------------------------------------
# Rate quoting — free, no confirmation gate
# ---------------------------------------------------------------------------

@dataclass
class Rate:
    id: str
    carrier: str
    service: str
    rate: float
    currency: str
    delivery_days: Optional[int]
    shipment_id: str


def get_rates(to_address: Address, from_address: Address, parcel: Parcel,
              api_key: Optional[str] = None) -> tuple[str, list[Rate]]:
    """Create a shipment with EasyPost and return (shipment_id, rates).

    This is the ONLY call in this module that may run unattended / freely —
    it creates a quote, never a purchase, and spends nothing. Rates are
    sorted cheapest first. `shipment_id` plus a chosen `Rate.id` is exactly
    what buy_label() needs.
    """
    body = {"shipment": {"to_address": to_address.to_dict(),
                         "from_address": from_address.to_dict(),
                         "parcel": parcel.to_dict()}}
    resp = api_send("POST", "/shipments", body=body, api_key=api_key)
    shipment_id = str(resp.get("id") or "")

    rates: list[Rate] = []
    for r in resp.get("rates") or []:
        try:
            price = float(r.get("rate"))
        except (TypeError, ValueError):
            continue
        rates.append(Rate(id=str(r.get("id") or ""),
                          carrier=str(r.get("carrier") or ""),
                          service=str(r.get("service") or ""),
                          rate=price,
                          currency=str(r.get("currency") or "USD"),
                          delivery_days=r.get("delivery_days"),
                          shipment_id=shipment_id))
    rates.sort(key=lambda r: r.rate)
    return shipment_id, rates


# ---------------------------------------------------------------------------
# BUY — explicit, manual, confirmation-gated (the one purchase path)
# ---------------------------------------------------------------------------

@dataclass
class BuyResult:
    dry_run: bool
    shipment_id: str
    rate_id: str
    carrier: str
    service: str
    price: float
    currency: str = "USD"
    tracking_code: Optional[str] = None    # set only on a real purchase
    label_url: Optional[str] = None        # set only on a real purchase
    postage_label_id: Optional[str] = None  # set only on a real purchase


def buy_label(shipment_id: str, rate: Rate, confirm: bool = False,
              api_key: Optional[str] = None) -> BuyResult:
    """Buy postage for a previously-quoted rate. DRY RUN unless confirm=True.

    Guarded exactly like list_edit.publish_offer(): with confirm=False this
    makes NO call to EasyPost's purchase endpoint (POST
    /shipments/{id}/buy) — it only reports what WOULD be bought and its
    cost, using the rate already returned by get_rates(). This is the ONLY
    function in this module that can spend money, and the ONLY place that
    calls the buy endpoint.

    On a real purchase, the returned `carrier` + `tracking_code` are in the
    exact shape `tools/pick_list.py --record-tracking` (#70) expects — feed
    them straight through; this function does not touch the local ledger.
    """
    if not confirm:
        return BuyResult(dry_run=True, shipment_id=shipment_id, rate_id=rate.id,
                         carrier=rate.carrier, service=rate.service,
                         price=rate.rate, currency=rate.currency)

    resp = api_send("POST", f"/shipments/{shipment_id}/buy",
                    body={"rate": {"id": rate.id}}, api_key=api_key)
    sel = resp.get("selected_rate") or {}
    label = resp.get("postage_label") or {}
    try:
        price = float(sel.get("rate") or rate.rate)
    except (TypeError, ValueError):
        price = rate.rate
    return BuyResult(dry_run=False, shipment_id=shipment_id,
                     rate_id=str(sel.get("id") or rate.id),
                     carrier=str(sel.get("carrier") or rate.carrier),
                     service=str(sel.get("service") or rate.service),
                     price=price,
                     currency=str(sel.get("currency") or rate.currency),
                     tracking_code=resp.get("tracking_code") or None,
                     label_url=label.get("label_url") or None,
                     postage_label_id=(str(label.get("id")) if label.get("id") else None))
