"""Warp (wearewarp.com) — the one LTL broker with a self-serve API.

Shape taken from Warp's public API reference (wearewarp.com/freight-api,
read 2026-10-13):

    base      https://www.wearewarp.com/api/v1
    auth      Authorization: Bearer wak_test_… | wak_live_…   (quotes: none)
    POST /ltl/quote            Warp's own all-inclusive bookable LTL price
    POST /ltl/market-options   20+ carrier spread — INDICATIVE, not bookable
    POST /book                 books a quote_id; charges the account/card
    GET  /track?tracking_number=
    GET  /documents?order_id=  BOL / POD links

Quoting needs no key (60/hour/IP without one). Booking needs a key; a
wak_test_ key returns production-shaped mock data with "sandbox": true and
charges nothing, so the whole flow can be exercised before a live key is
trusted. Cancellation is NOT in the API — it is an email to
support@wearewarp.com — so a confirmed book is final as far as this code is
concerned.
"""
from __future__ import annotations

from freight import http
from freight.models import (BookResult, FreightQuote, FreightRequest,
                            TrackEvent, TrackResult)
from freight.providers import Provider, ProviderNotReady

BASE = "https://www.wearewarp.com/api/v1"

_ACCESSORIAL_SLUGS = {
    "liftgate_pickup": ("pickup", "liftgate-pickup"),
    "liftgate_delivery": ("delivery", "liftgate-delivery"),
    "residential_pickup": ("pickup", "residential-pickup"),
    "residential_delivery": ("delivery", "residential-delivery"),
    "inside_pickup": ("pickup", "inside-pickup"),
    "inside_delivery": ("delivery", "inside-delivery"),
    "limited_access_pickup": ("pickup", "limited-access-pickup"),
    "limited_access_delivery": ("delivery", "limited-access-delivery"),
    "appointment_pickup": ("pickup", "pickup-appointment"),
    "appointment_delivery": ("delivery", "delivery-appointment"),
}


class Warp(Provider):
    name = "warp"

    def _key(self) -> str:
        return str(self.cfg.get("api_key") or "")

    def _headers(self, need_key: bool = False) -> dict:
        key = self._key()
        if need_key and not key:
            raise ProviderNotReady(
                "warp: booking needs an API key (freight.providers.warp.api_key "
                "or WARP_API_KEY). Quoting works without one.")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def status(self) -> tuple[bool, str]:
        key = self._key()
        if not key:
            return True, "quotes keyless; add a key to book"
        mode = "sandbox" if key.startswith("wak_test_") else "LIVE"
        return True, f"key set ({mode})"

    def _body(self, req: FreightRequest) -> dict:
        # Warp's simple form is N identical pallets; mixed loads go in items[].
        # Its items[] field names aren't in the public reference, so a mixed
        # load is quoted as the heaviest/largest unit repeated — conservative,
        # and flagged on the quote so nobody books it blind.
        u = max(req.units, key=lambda x: (x.weight_lbs, x.cubic_feet))
        body = {
            "origin_zip": req.origin.zip[:5],
            "destination_zip": req.destination.zip[:5],
            "pickup_date": req.pickup_date,
            "pallets": req.total_units,
            "weight_lbs_per_pallet": round(u.weight_lbs),
            "length_in": round(u.length_in),
            "width_in": round(u.width_in),
            "height_in": round(u.height_in),
            "freight_class": u.effective_class(),
        }
        if req.commodity:
            body["commodity"] = req.commodity
        acc: dict[str, list[str]] = {"pickup": [], "delivery": []}
        for a in req.accessorials:
            side, slug = _ACCESSORIAL_SLUGS[a]
            acc[side].append(slug)
        if acc["pickup"] or acc["delivery"]:
            body["accessorials"] = acc
        if req.declared_value:
            body["insuranceInfo"] = {"commodity": req.commodity or "general merchandise",
                                     "insuredValue": req.declared_value}
        return body

    def _mixed_warning(self, req: FreightRequest) -> list[str]:
        if len(req.units) > 1:
            return ["mixed load quoted as the largest unit repeated "
                    f"{req.total_units}x — real price is likely lower"]
        return []

    def quote(self, req: FreightRequest) -> list[FreightQuote]:
        body = self._body(req)
        warn = self._mixed_warning(req)
        out: list[FreightQuote] = []
        r = http.send(self.name, "POST", f"{BASE}/ltl/quote", body=body,
                      headers=self._headers(), timeout=60)
        if isinstance(r, dict) and r.get("price_usd") is not None:
            w = list(warn)
            missing = r.get("missing_for_ship") or []
            if missing:
                w.append("Warp needs before booking: " + ", ".join(map(str, missing)))
            included = (r.get("service") or {}).get("included_at_no_charge") or []
            out.append(FreightQuote(
                provider=self.name, carrier="Warp",
                service=str((r.get("service") or {}).get("vehicle") or "LTL"),
                price=float(r["price_usd"]), quote_id=str(r.get("quote_id") or ""),
                transit_days=r.get("transit_days"), expires_at=r.get("expires_at"),
                bookable=bool(r.get("quote_id")), tier=str(r.get("quote_tier") or ""),
                warnings=w,
                extra={"included": included, "insurance": r.get("insurance"),
                       "delivery_date": r.get("delivery_date")}))
        if self.cfg.get("market_options", True):
            # Up to 45 s per Warp's docs. Indicative only — these are for
            # comparison; Warp's own quote above is the bookable one.
            try:
                m = http.send(self.name, "POST", f"{BASE}/ltl/market-options",
                              body=body, headers=self._headers(), timeout=60)
            except http.FreightAPIError as e:
                if out:
                    out[0].warnings.append(f"market spread unavailable: {e}")
                    return out
                raise
            for o in (m or {}).get("market_options") or []:
                if o.get("is_warp") or o.get("price_usd") is None:
                    continue
                out.append(FreightQuote(
                    provider=self.name, carrier=str(o.get("carrier_name") or "?"),
                    service=str(o.get("service_level") or ""),
                    price=float(o["price_usd"]), transit_days=o.get("transit_days"),
                    bookable=bool(o.get("bookable")) and bool(o.get("quote_id")),
                    quote_id=str(o.get("quote_id") or ""),
                    tier="indicative", warnings=list(warn)))
        return out

    def book(self, quote: FreightQuote, req: FreightRequest,
             confirm: bool = False) -> BookResult:
        headers = self._headers(need_key=True)
        if not quote.bookable or not quote.quote_id:
            raise ProviderNotReady(
                f"warp: {quote.carrier} is an indicative market rate — only "
                f"Warp's own quote is bookable through the API")
        gaps = [f"origin.{g}" for g in req.origin.bookable_gaps()] + \
               [f"destination.{g}" for g in req.destination.bookable_gaps()]
        if gaps:
            raise ValueError("booking needs full stop details; missing: " + ", ".join(gaps))
        if not confirm:
            return BookResult(dry_run=True, provider=self.name, quote_id=quote.quote_id,
                              price=quote.price, carrier=quote.carrier)

        def stop(a):
            d = {"street": a.street1, "city": a.city, "state": a.state,
                 "zipCode": a.zip[:5], "contactName": a.name, "phone": a.phone,
                 "email": a.email}
            if a.company:
                d["company"] = a.company
            if a.street2:
                d["street2"] = a.street2
            return d
        body = {"quote_id": quote.quote_id,
                "patch": {"pickup": stop(req.origin), "delivery": stop(req.destination)}}
        if req.reference:
            body["reference"] = req.reference
        r = http.send(self.name, "POST", f"{BASE}/book", body=body, headers=headers,
                      timeout=90, retry_network_errors=False)
        r = r if isinstance(r, dict) else {}
        warnings = ["SANDBOX booking — nothing was dispatched or charged"] if r.get("sandbox") else []
        return BookResult(
            dry_run=False, provider=self.name, quote_id=quote.quote_id,
            price=quote.price, carrier=quote.carrier,
            booking_id=str(r.get("order_id") or r.get("shipment_id") or ""),
            tracking_number=str(r.get("tracking_number") or r.get("shipment_number") or ""),
            tracking_url=str(r.get("tracking_dashboard") or ""),
            warnings=warnings, raw=r)

    def track(self, booking: dict) -> TrackResult:
        tn = booking.get("tracking_number")
        if not tn:
            raise ProviderNotReady("warp: no tracking number on this shipment")
        r = http.send(self.name, "GET", f"{BASE}/track", params={"tracking_number": tn},
                      headers=self._headers())
        rows = r if isinstance(r, list) else [r]
        info = (rows[0] or {}).get("statusInfo", {}) if rows else {}
        status = str(info.get("status") or "unknown")
        events = [TrackEvent(when=str(info.get("lastUpdated") or ""), status=status)]
        shipment_id = (booking.get("raw") or {}).get("shipment_id")
        if shipment_id:
            try:
                ev = http.send(self.name, "GET", f"{BASE}/events",
                               params={"shipment_id": shipment_id},
                               headers=self._headers())
                events = [TrackEvent(when=str(e.get("when") or ""),
                                     status=str(e.get("message") or e.get("eventName") or ""))
                          for e in (ev or [])] or events
            except http.FreightAPIError:
                pass   # the event log is a nicety; the status above is the answer
        return TrackResult(status=status, events=events,
                           delivered=status.lower() in ("complete", "delivered"))

    def documents(self, booking: dict) -> list[dict]:
        oid = booking.get("booking_id")
        if not oid:
            return []
        r = http.send(self.name, "GET", f"{BASE}/documents", params={"order_id": oid},
                      headers=self._headers(need_key=True))
        return [{"type": d.get("type"), "url": d.get("url")}
                for d in (r or {}).get("data") or [] if d.get("url")]
