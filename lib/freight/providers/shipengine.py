"""ShipEngine (ShipStation API) LTL — quotes from carrier accounts you own.

Shape taken from ShipEngine's v-beta LTL docs (shipengine.com/docs/ltl/,
read 2026-10-13):

    base   https://api.shipengine.com      auth  API-Key: <key>
    GET  /v-beta/ltl/carriers/                       connected LTL carriers
    POST /v-beta/ltl/quotes/{carrier_id}             contracted-rate quote
    POST /v-beta/ltl/quotes/{quote_id}/pickup        book: pickup + PRO + BOL
    GET  /v-beta/ltl/tracking?carrier_code=&pro_number=

What this is NOT: a broker. ShipEngine only quotes carriers whose accounts
you have connected to it (Estes, ABF, XPO, …), and LTL needs its Advanced
plan or higher. Until a carrier is connected it returns nothing, and says so.

Unverified, so config-driven rather than guessed:
  * bill_to — the docs' example is {"type": "third_party", "payment_terms":
    "prepaid", "account": …}. The default here is the shipper (origin) paying
    prepaid; set freight.providers.shipengine.bill_to if your carrier needs
    something else.
  * accessorial codes are per carrier. They are matched by NAME against the
    carrier's own `options` list from /ltl/carriers (e.g. "Lift gate
    required at pickup" -> lftp). Anything unmatched is a warning on the
    quote, never silently dropped.
  * carrier.test defaults to TRUE on pickups, so a first confirmed booking
    is a carrier test transaction. Set `test: false` to dispatch for real.
"""
from __future__ import annotations

from freight import http
from freight.models import (ACCESSORIALS, BookResult, FreightAddress,
                            FreightQuote, FreightRequest, TrackEvent,
                            TrackResult)
from freight.providers import Provider, ProviderNotReady

BASE = "https://api.shipengine.com/v-beta/ltl"

# accessorial -> (words that must all appear in the carrier's option name)
_MATCH = {
    "liftgate_pickup": (("lift", "pickup"),),
    "liftgate_delivery": (("lift", "deliver"),),
    "residential_pickup": (("residential", "pickup"),),
    "residential_delivery": (("residential", "deliver"),),
    "inside_pickup": (("inside", "pickup"),),
    "inside_delivery": (("inside", "deliver"),),
    "limited_access_pickup": (("limited", "pickup"),),
    "limited_access_delivery": (("limited", "deliver"),),
    "appointment_pickup": (("appointment", "pickup"),),
    "appointment_delivery": (("appointment", "deliver"), ("notif", "deliver")),
}


def _option_code(carrier: dict, accessorial: str) -> str | None:
    for opt in carrier.get("options") or []:
        if "quote" not in (opt.get("features") or ["quote"]):
            continue
        name = str(opt.get("name") or "").lower()
        for words in _MATCH[accessorial]:
            if all(w in name for w in words):
                return str(opt.get("code"))
    return None


def _package_code(carrier: dict, unit_type: str) -> str:
    wanted = {"pallet": ("pallet", "skid"), "crate": ("crate",),
              "box": ("box", "carton"), "piece": ("piece",)}[unit_type]
    for p in carrier.get("packages") or []:
        if any(w in str(p.get("name") or "").lower() for w in wanted):
            return str(p.get("code"))
    return "pkg"


def _party(a: FreightAddress) -> dict:
    addr = {"company_name": a.company or a.name or "Shipper",
            "address_line1": a.street1, "city_locality": a.city,
            "state_province": a.state, "postal_code": a.zip,
            "country_code": a.country or "US"}
    if a.street2:
        addr["address_line2"] = a.street2
    return {"contact": {"name": a.name, "phone_number": a.phone, "email": a.email},
            "address": addr}


class ShipEngine(Provider):
    name = "shipengine"

    def _key(self) -> str:
        return str(self.cfg.get("api_key") or "")

    def _headers(self) -> dict:
        if not self._key():
            raise ProviderNotReady(
                "shipengine: no API key (freight.providers.shipengine.api_key or "
                "SHIPENGINE_API_KEY). LTL needs the Advanced plan and at least one "
                "connected LTL carrier account.")
        return {"API-Key": self._key()}

    def status(self) -> tuple[bool, str]:
        if not self._key():
            return False, "no API key (Advanced plan + connected LTL carriers needed)"
        return True, "key set"

    def carriers(self) -> list[dict]:
        r = http.send(self.name, "GET", f"{BASE}/carriers/", headers=self._headers())
        cs = (r or {}).get("carriers") or []
        only = set(self.cfg.get("carrier_ids") or [])
        return [c for c in cs if "quote" in (c.get("features") or [])
                and (not only or c.get("carrier_id") in only)]

    def _body(self, req: FreightRequest, carrier: dict) -> tuple[dict, list[str]]:
        warnings: list[str] = []
        options = []
        for a in req.accessorials:
            code = _option_code(carrier, a)
            if code:
                options.append({"code": code})
            else:
                warnings.append(f"{ACCESSORIALS[a]} not offered by {carrier.get('name')} "
                                "— not in this price")
        packages = []
        for u in req.units:
            pkg = {"code": _package_code(carrier, u.unit_type),
                   "freight_class": u.effective_class(),
                   "description": u.description or req.commodity or "General merchandise",
                   "dimensions": {"length": u.length_in, "width": u.width_in,
                                  "height": u.height_in, "unit": "inches"},
                   "weight": {"value": u.weight_lbs * u.count, "unit": "pounds"},
                   "quantity": u.count, "stackable": u.stackable,
                   "hazardous_materials": False}
            if not u.freight_class:
                warnings.append(f"freight class {pkg['freight_class']} estimated from "
                                f"density {u.density_pcf:.1f} pcf — confirm it")
            packages.append(pkg)
        shipper = _party(req.origin)
        bill_to = dict(self.cfg.get("bill_to") or
                       {"type": "shipper", "payment_terms": "prepaid"})
        bill_to.setdefault("address", shipper["address"])
        bill_to.setdefault("contact", shipper["contact"])
        body = {"shipment": {
            "service_code": self.cfg.get("service_code") or "stnd",
            "pickup_date": req.pickup_date,
            "packages": packages, "options": options,
            "ship_from": shipper, "ship_to": _party(req.destination),
            "bill_to": bill_to,
            "requested_by": {"company_name": shipper["address"]["company_name"],
                             "contact": shipper["contact"]},
        }}
        return body, warnings

    def quote(self, req: FreightRequest) -> list[FreightQuote]:
        carriers = self.carriers()
        if not carriers:
            raise ProviderNotReady("shipengine: no connected LTL carrier offers quotes "
                                   "— connect one in ShipEngine first")
        out, errors = [], []
        for c in carriers:
            body, warnings = self._body(req, c)
            try:
                r = http.send(self.name, "POST", f"{BASE}/quotes/{c['carrier_id']}",
                              body=body, headers=self._headers())
            except http.FreightAPIError as e:
                errors.append(f"{c.get('name')}: {e}")
                continue
            total = next((ch for ch in (r or {}).get("charges") or []
                          if ch.get("type") == "total"), None)
            if not total:
                errors.append(f"{c.get('name')}: no total in quote")
                continue
            amt = total.get("amount") or {}
            msg = (r or {}).get("carrier_message")
            out.append(FreightQuote(
                provider=self.name, carrier=str(c.get("name") or c.get("scac") or "?"),
                service=str(((r or {}).get("service") or {}).get("carrier_description") or ""),
                price=float(amt.get("value")), currency=str(amt.get("currency") or "USD"),
                quote_id=str(r.get("quote_id") or ""),
                transit_days=r.get("estimated_delivery_days"),
                expires_at=r.get("expiration_date"), bookable=bool(r.get("quote_id")),
                tier="firm", warnings=warnings + ([str(msg)] if msg else []),
                extra={"carrier_id": c.get("carrier_id"), "scac": c.get("scac"),
                       "charges": r.get("charges")}))
        if not out and errors:
            raise http.FreightAPIError(self.name, 0, "; ".join(errors))
        for q in out:
            q.warnings += [f"other carrier failed — {e}" for e in errors]
        return out

    def book(self, quote: FreightQuote, req: FreightRequest,
             confirm: bool = False) -> BookResult:
        headers = self._headers()
        gaps = req.origin.bookable_gaps() + req.destination.bookable_gaps()
        if gaps:
            raise ValueError("booking needs full stop details; missing: " + ", ".join(gaps))
        test = bool(self.cfg.get("test", True))
        warnings = ["carrier TEST transaction (shipengine test: true) — no truck is "
                    "coming"] if test else []
        if not confirm:
            return BookResult(dry_run=True, provider=self.name, quote_id=quote.quote_id,
                              price=quote.price, carrier=quote.carrier, warnings=warnings)
        win = self.cfg.get("pickup_window") or {}
        body = {"pickup_date": req.pickup_date,
                "pickup_window": {"start_at": win.get("start", "09:00:00"),
                                  "end_at": win.get("end", "17:00:00"),
                                  "closing_at": win.get("closing", win.get("end", "17:00:00"))},
                "carrier": {"test": test,
                            "instructions": self.cfg.get("instructions") or ""}}
        r = http.send(self.name, "POST", f"{BASE}/quotes/{quote.quote_id}/pickup",
                      body=body, headers=headers, retry_network_errors=False)
        r = r if isinstance(r, dict) else {}
        warnings += [str(w.get("message")) for w in r.get("warnings") or [] if w.get("message")]
        return BookResult(dry_run=False, provider=self.name, quote_id=quote.quote_id,
                          price=quote.price, carrier=quote.carrier,
                          booking_id=str(r.get("pickup_id") or ""),
                          tracking_number=str(r.get("pro_number") or ""),
                          warnings=warnings, raw=r)

    def track(self, booking: dict) -> TrackResult:
        pro = booking.get("tracking_number")
        scac = (booking.get("quote_extra") or {}).get("scac")
        if not (pro and scac):
            raise ProviderNotReady("shipengine: tracking needs the PRO number and carrier SCAC")
        r = http.send(self.name, "GET", f"{BASE}/tracking",
                      params={"carrier_code": scac, "pro_number": pro},
                      headers=self._headers())
        evs = []
        for e in (r or {}).get("events") or []:
            loc = e.get("location") or {}
            evs.append(TrackEvent(
                when=f"{e.get('date', '')} {e.get('time', '')}".strip(),
                status=str((e.get("carrier") or {}).get("description") or e.get("status") or ""),
                location=", ".join(x for x in (loc.get("city_locality"),
                                               loc.get("state_province")) if x)))
        last = ((r or {}).get("events") or [{}])[0].get("status") or "unknown"
        delivered = bool(((r or {}).get("delivery") or {}).get("actual", {}).get("date")) \
            or last == "delivered"
        return TrackResult(status=str(last), events=evs, delivered=delivered)
