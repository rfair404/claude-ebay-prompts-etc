"""Warp (wearewarp.com) — LTL quote, book, track, documents.

Two Warp APIs are used, for different jobs:

1. The developer API — the one that books. OpenAPI spec at
   developer.wearewarp.com/docs/freight/ (read 2026-10-13):

       sandbox   https://stg.wearewarp.com/api/v1     key: freight.providers.warp.sandbox_key
       live      https://gw.wearewarp.com/api/v1      key: freight.providers.warp.live_key
       auth      header `apikey: <key>` (keys are issued by Warp, per environment)

       POST /freights/quote                  firm quote -> quote_id
       POST /freights/booking                books a quote_id (must match the quoted load)
       POST /freights/tracking               {"trackingNumbers": [...]}
       GET  /freights/events/{shipmentId}
       GET  /freights/documents/{orderId}    BOL / POD links

   `mode` (default "sandbox") picks the environment. A quote remembers which
   environment it came from and can only be booked there, so a sandbox
   quote id can never be sent to the live booking endpoint or vice versa.

2. The public keyless endpoints on www.wearewarp.com/api/v1 — used for a
   quote when no key is configured (not bookable), and for the ~20-carrier
   market spread (/ltl/market-options), which is indicative only.

Payment (from the booking endpoint's description): the account balance pays;
if it can't, the order is created ON HOLD and the response carries a
paymentLink. With "Pay by card for API bookings" on, a saved card is charged
instead, and insurance can't be added (HTTP 400 CARD_INSURANCE_NOT_SUPPORTED).
Cancellation is not in the API — email support.
"""
from __future__ import annotations

from datetime import datetime, timezone

from freight import http
from freight.models import (BookResult, FreightAddress, FreightQuote,
                            FreightRequest, TrackEvent, TrackResult)
from freight.providers import Provider, ProviderNotReady

HOSTS = {"sandbox": "https://stg.wearewarp.com/api/v1",
         "live": "https://gw.wearewarp.com/api/v1"}
PUBLIC = "https://www.wearewarp.com/api/v1"

# Our accessorial vocabulary -> (Warp services list, Warp slug). Same slugs on
# the developer API's pickupServices/deliveryServices and the public quote.
_SERVICES = {
    "liftgate_pickup": ("pickupServices", "liftgate-pickup"),
    "liftgate_delivery": ("deliveryServices", "liftgate-delivery"),
    "residential_pickup": ("pickupServices", "residential-pickup"),
    "residential_delivery": ("deliveryServices", "residential-delivery"),
    "inside_pickup": ("pickupServices", "inside-pickup"),
    "inside_delivery": ("deliveryServices", "inside-delivery"),
    "limited_access_pickup": ("pickupServices", "limited-access-pickup"),
    "limited_access_delivery": ("deliveryServices", "limited-access-delivery"),
    "appointment_pickup": ("pickupServices", "pickup-appointment"),
    "appointment_delivery": ("deliveryServices", "delivery-appointment"),
}
_PACKAGING = {"pallet": "Pallet", "crate": "Crate", "box": "Box", "piece": "Piece"}


def _iso_from_epoch(ts) -> str | None:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    if ts > 1e11:          # milliseconds
        ts /= 1000
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Warp(Provider):
    name = "warp"

    # ------------------------------------------------------------ config

    @property
    def mode(self) -> str:
        m = str(self.cfg.get("mode") or "sandbox").lower()
        if m not in HOSTS:
            raise ProviderNotReady(f"warp: mode {m!r} must be 'sandbox' or 'live'")
        return m

    def _key(self, mode: str | None = None) -> str:
        return str(self.cfg.get(f"{mode or self.mode}_key") or "")

    def _auth(self, mode: str | None = None) -> dict:
        mode = mode or self.mode
        key = self._key(mode)
        if not key:
            raise ProviderNotReady(
                f"warp: no {mode} key (freight.providers.warp.{mode}_key or "
                f"WARP_{mode.upper()}_KEY). Quoting works without one; booking doesn't.")
        return {"apikey": key}

    def status(self) -> tuple[bool, str]:
        try:
            mode = self.mode
        except ProviderNotReady as e:
            return False, str(e)
        if not self._key(mode):
            return True, f"mode {mode}, no {mode} key — public quotes only, can't book"
        return True, f"mode {mode} ({'NO real pickups or charges' if mode == 'sandbox' else 'REAL bookings and charges'})"

    # ------------------------------------------------------------ bodies

    def _items(self, req: FreightRequest) -> list[dict]:
        return [{"name": (u.description or req.commodity or "General merchandise")[:80],
                 "length": u.length_in, "width": u.width_in, "height": u.height_in,
                 "sizeUnit": "IN", "totalWeight": round(u.weight_lbs * u.count, 1),
                 "weightUnit": "lbs", "quantity": u.count,
                 "packaging": _PACKAGING.get(u.unit_type, "Pallet"),
                 "stackable": u.stackable} for u in req.units]

    def _services(self, req: FreightRequest) -> dict:
        out: dict[str, list] = {}
        for a in req.accessorials:
            group, slug = _SERVICES[a]
            out.setdefault(group, []).append({"quantity": 1, "service": slug})
        return out

    def _quote_body(self, req: FreightRequest) -> dict:
        body = {"pickupDate": req.pickup_date,
                "pickupInfo": {"zipcode": req.origin.zip[:5]},
                "deliveryInfo": {"zipcode": req.destination.zip[:5]},
                "listItems": self._items(req), "shipmentType": "LTL",
                **self._services(req)}
        if req.declared_value:
            body["insuranceInfo"] = {"commodity": req.commodity or "General merchandise",
                                     "insuredValue": req.declared_value}
        return body

    def _public_body(self, req: FreightRequest) -> dict:
        # The public endpoint only takes N identical pallets.
        u = max(req.units, key=lambda x: (x.weight_lbs, x.cubic_feet))
        body = {"origin_zip": req.origin.zip[:5], "destination_zip": req.destination.zip[:5],
                "pickup_date": req.pickup_date, "pallets": req.total_units,
                "weight_lbs_per_pallet": round(u.weight_lbs), "length_in": round(u.length_in),
                "width_in": round(u.width_in), "height_in": round(u.height_in),
                "freight_class": u.effective_class()}
        if req.commodity:
            body["commodity"] = req.commodity
        acc: dict[str, list[str]] = {"pickup": [], "delivery": []}
        for a in req.accessorials:
            group, slug = _SERVICES[a]
            acc["pickup" if group == "pickupServices" else "delivery"].append(slug)
        if acc["pickup"] or acc["delivery"]:
            body["accessorials"] = acc
        return body

    # ------------------------------------------------------------ quote

    def quote(self, req: FreightRequest) -> list[FreightQuote]:
        out: list[FreightQuote] = []
        mode = self.mode
        if self._key(mode):
            out.append(self._dev_quote(req, mode))
        else:
            q = self._public_quote(req)
            if q:
                out.append(q)
        if self.cfg.get("market_options", True):
            try:
                out += self._market(req)
            except http.FreightAPIError as e:
                if not out:
                    raise
                out[0].warnings.append(f"market spread unavailable: {e}")
        return out

    def _dev_quote(self, req: FreightRequest, mode: str) -> FreightQuote:
        r = http.send(self.name, "POST", f"{HOSTS[mode]}/freights/quote",
                      body=self._quote_body(req), headers=self._auth(mode))
        price = (r or {}).get("price") or {}
        if price.get("amount") is None or not r.get("quote_id"):
            raise http.FreightAPIError(self.name, 0, f"quote has no price/id: {str(r)[:200]}")
        charges = [{"code": c.get("code"), "description": c.get("description"),
                    "amount": c.get("amount")} for c in r.get("charges") or []]
        breakdown = [c for c in charges if c["code"] != "TOTAL"]
        warnings = [f"Warp quote status {r.get('status')}"] if r.get("status") not in (None, "ACCEPT") else []
        if mode == "sandbox":
            warnings.append("sandbox quote — books only in Warp's sandbox")
        return FreightQuote(
            provider=self.name, carrier="Warp", service="LTL",
            price=round(float(price["amount"]), 2),
            currency=str(price.get("currency_code") or "USD"),
            quote_id=str(r["quote_id"]), transit_days=r.get("transit_time"),
            expires_at=_iso_from_epoch(r.get("expiration_time_utc")),
            bookable=True, tier="firm", warnings=warnings,
            extra={"mode": mode, "charges": breakdown, "insurance": r.get("insurance"),
                   "notes": r.get("notes")})

    def _public_quote(self, req: FreightRequest) -> FreightQuote | None:
        r = http.send(self.name, "POST", f"{PUBLIC}/ltl/quote", body=self._public_body(req))
        if not isinstance(r, dict) or r.get("price_usd") is None:
            return None
        warnings = [f"no {self.mode} key — this price can't be booked from here"]
        if len(req.units) > 1:
            warnings.append(f"mixed load quoted as the largest unit x{req.total_units}")
        return FreightQuote(
            provider=self.name, carrier="Warp", service="LTL",
            price=float(r["price_usd"]), transit_days=r.get("transit_days"),
            expires_at=r.get("expires_at"), bookable=False,
            tier=str(r.get("quote_tier") or ""), warnings=warnings,
            extra={"included": (r.get("service") or {}).get("included_at_no_charge") or []})

    def _market(self, req: FreightRequest) -> list[FreightQuote]:
        m = http.send(self.name, "POST", f"{PUBLIC}/ltl/market-options",
                      body=self._public_body(req), timeout=60)
        return [FreightQuote(provider=self.name, carrier=str(o.get("carrier_name") or "?"),
                             service=str(o.get("service_level") or ""),
                             price=float(o["price_usd"]), transit_days=o.get("transit_days"),
                             tier="indicative")
                for o in (m or {}).get("market_options") or []
                if not o.get("is_warp") and o.get("price_usd") is not None]

    # ------------------------------------------------------------ book

    @staticmethod
    def _stop(a: FreightAddress, window: dict | None, ref: str) -> dict:
        d = {"locationName": a.company or a.name, "contactName": a.name,
             "contactPhone": a.phone,
             "address": {"street": a.street1, "city": a.city, "state": a.state,
                         "zipcode": a.zip[:5]}}
        if a.street2:
            d["address"]["street2"] = a.street2
        if a.email:
            d["contactEmail"] = a.email
        if window:
            d["windowTime"] = window
        if ref:
            d["refNum"] = ref
        return d

    def _pickup_window(self, req: FreightRequest) -> dict:
        """freight.providers.warp.pickup_window {from: "09:00", to: "17:00"},
        read as this machine's local time on the pickup date, sent as UTC."""
        w = self.cfg.get("pickup_window") or {}
        tz = datetime.now().astimezone().tzinfo

        def at(hhmm: str) -> str:
            local = datetime.fromisoformat(f"{req.pickup_date}T{hhmm}").replace(tzinfo=tz)
            return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return {"from": at(str(w.get("from") or "09:00")), "to": at(str(w.get("to") or "17:00"))}

    def book(self, quote: FreightQuote, req: FreightRequest,
             confirm: bool = False) -> BookResult:
        qmode = (quote.extra or {}).get("mode")
        if not quote.bookable or not quote.quote_id or not qmode:
            raise ProviderNotReady(
                f"warp: quote for {quote.carrier} can't be booked through the API "
                f"(indicative or keyless) — re-quote with a Warp key configured")
        if qmode != self.mode:
            raise ProviderNotReady(
                f"warp: this quote came from {qmode} but mode is now {self.mode} — re-quote")
        headers = self._auth(qmode)
        gaps = [f"origin.{g}" for g in req.origin.bookable_gaps()] + \
               [f"destination.{g}" for g in req.destination.bookable_gaps()]
        if gaps:
            raise ValueError("booking needs full stop details; missing: " + ", ".join(gaps))
        warnings = ["SANDBOX booking (stg.wearewarp.com) — no truck, no charge"] \
            if qmode == "sandbox" else []
        insurance_id = ((quote.extra or {}).get("insurance") or {}).get("insuranceQuoteId")
        if req.declared_value and insurance_id:
            ins = quote.extra["insurance"]
            warnings.append(f"insurance on ${req.declared_value:,.0f} adds "
                            f"${ins.get('total', 0):,.2f} (premium + fee)")
        if not confirm:
            return BookResult(dry_run=True, provider=self.name, quote_id=quote.quote_id,
                              price=quote.price, carrier=quote.carrier, warnings=warnings)

        body = {"quoteId": quote.quote_id, "shipmentType": "LTL",
                "pickupInfo": self._stop(req.origin, self._pickup_window(req), req.reference),
                "deliveryInfo": self._stop(req.destination, None, req.reference),
                "listItems": self._items(req), **self._services(req)}
        if req.reference:
            body["refNum"] = req.reference
        if req.declared_value and insurance_id:
            body["insuranceQuoteId"] = insurance_id
        r = http.send(self.name, "POST", f"{HOSTS[qmode]}/freights/booking", body=body,
                      headers=headers, timeout=90, retry_network_errors=False)
        r = r if isinstance(r, dict) else {}
        if r.get("insuranceStatus") == "failed":
            warnings.append("insurance FAILED to bind — the shipment is uninsured")
        return BookResult(
            dry_run=False, provider=self.name, quote_id=quote.quote_id, price=quote.price,
            carrier=quote.carrier, booking_id=str(r.get("orderId") or ""),
            tracking_number=str(r.get("trackingNumber") or ""),
            payment_link=str(r.get("paymentLink") or ""), warnings=warnings,
            raw={**r, "mode": qmode})

    # ------------------------------------------------------------ track / docs

    def _booking_mode(self, booking: dict) -> str:
        return (booking.get("raw") or {}).get("mode") or self.mode

    def track(self, booking: dict) -> TrackResult:
        tn = booking.get("tracking_number")
        if not tn:
            raise ProviderNotReady("warp: no tracking number on this shipment")
        mode = self._booking_mode(booking)
        r = http.send(self.name, "POST", f"{HOSTS[mode]}/freights/tracking",
                      body={"trackingNumbers": [tn]}, headers=self._auth(mode))
        rows = r if isinstance(r, list) else (r or {}).get("data") or [r]
        info = ((rows[0] or {}) if rows else {}).get("statusInfo") or {}
        status = str(info.get("status") or "unknown")
        events = [TrackEvent(when=str(info.get("lastUpdated") or ""), status=status)]
        shipment_id = (booking.get("raw") or {}).get("shipmentId")
        if shipment_id:
            try:
                ev = http.send(self.name, "GET", f"{HOSTS[mode]}/freights/events/{shipment_id}",
                               headers=self._auth(mode))
                ev = ev if isinstance(ev, list) else (ev or {}).get("data") or []
                events = [TrackEvent(when=str(e.get("when") or ""),
                                     status=str(e.get("message") or e.get("eventName") or ""))
                          for e in ev] or events
            except http.FreightAPIError:
                pass   # the event log is a nicety; the status above is the answer
        return TrackResult(status=status, events=events,
                           delivered=status.lower() in ("complete", "delivered"))

    def documents(self, booking: dict) -> list[dict]:
        oid = booking.get("booking_id")
        if not oid:
            return []
        mode = self._booking_mode(booking)
        r = http.send(self.name, "GET", f"{HOSTS[mode]}/freights/documents/{oid}",
                      headers=self._auth(mode))
        return [{"type": d.get("type"), "podType": d.get("podType"), "url": d.get("url")}
                for d in (r or {}).get("data") or [] if d.get("url")]
