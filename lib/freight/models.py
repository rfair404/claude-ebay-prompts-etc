"""Provider-neutral freight shapes. Every provider module translates to and
from these; nothing outside providers/ sees a broker's own JSON."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

# Accessorials, as one vocabulary. Each provider maps the ones it can and
# reports the rest back as a warning on its quote — an accessorial silently
# dropped is a quote that is wrong at the dock (a residential delivery with
# no liftgate is a re-delivery fee, or a refused one).
ACCESSORIALS = {
    "liftgate_pickup":       "Liftgate at pickup",
    "liftgate_delivery":     "Liftgate at delivery",
    "residential_pickup":    "Residential pickup",
    "residential_delivery":  "Residential delivery",
    "inside_pickup":         "Inside pickup",
    "inside_delivery":       "Inside delivery",
    "limited_access_pickup": "Limited-access pickup",
    "limited_access_delivery": "Limited-access delivery",
    "appointment_pickup":    "Pickup appointment",
    "appointment_delivery":  "Delivery appointment",
}

UNIT_TYPES = ("pallet", "crate", "box", "piece")

_ZIP = re.compile(r"^\d{5}(-\d{4})?$")


@dataclass
class FreightAddress:
    """A pickup or delivery stop. Only `zip` is needed to quote; booking
    needs the rest (every broker wants a named, phoned contact on site)."""
    zip: str
    name: str = ""
    company: str = ""
    street1: str = ""
    street2: str = ""
    city: str = ""
    state: str = ""
    country: str = "US"
    phone: str = ""
    email: str = ""

    def bookable_gaps(self) -> list[str]:
        return [f for f in ("name", "street1", "city", "state", "phone")
                if not getattr(self, f)]


@dataclass
class HandlingUnit:
    """`count` identical units (pallets, crates…). Weight is PER UNIT, pounds;
    dims inches — what a dock scale and a tape measure give you."""
    length_in: float
    width_in: float
    height_in: float
    weight_lbs: float
    count: int = 1
    unit_type: str = "pallet"
    freight_class: Optional[str] = None   # e.g. "70", "92.5"; None -> estimated
    stackable: bool = False
    description: str = ""

    @property
    def cubic_feet(self) -> float:
        return self.length_in * self.width_in * self.height_in / 1728.0

    @property
    def density_pcf(self) -> float:
        return self.weight_lbs / self.cubic_feet if self.cubic_feet else 0.0

    def effective_class(self) -> str:
        return self.freight_class or estimate_freight_class(self.density_pcf)


@dataclass
class FreightRequest:
    origin: FreightAddress
    destination: FreightAddress
    pickup_date: str                       # YYYY-MM-DD
    units: list[HandlingUnit]
    accessorials: list[str] = field(default_factory=list)
    commodity: str = ""                    # plain words: "antique sewing machine"
    declared_value: Optional[float] = None
    reference: str = ""                    # eBay order id / SKU — rides on the BOL

    @property
    def total_weight_lbs(self) -> float:
        return sum(u.weight_lbs * u.count for u in self.units)

    @property
    def total_units(self) -> int:
        return sum(u.count for u in self.units)

    def validate(self) -> None:
        """Raise ValueError naming every problem at once — a quote round-trip
        to five brokers to learn one zip was mistyped is a slow way to find
        out."""
        errs = []
        for label, a in (("origin", self.origin), ("destination", self.destination)):
            if not _ZIP.match(a.zip or ""):
                errs.append(f"{label} zip {a.zip!r} is not a 5-digit US zip")
        try:
            date.fromisoformat(self.pickup_date)
        except (TypeError, ValueError):
            errs.append(f"pickup_date {self.pickup_date!r} is not YYYY-MM-DD")
        if not self.units:
            errs.append("at least one handling unit is required")
        for i, u in enumerate(self.units, 1):
            if min(u.length_in, u.width_in, u.height_in) <= 0:
                errs.append(f"unit {i}: every dimension must be > 0")
            if u.weight_lbs <= 0:
                errs.append(f"unit {i}: weight must be > 0")
            if u.count < 1:
                errs.append(f"unit {i}: count must be >= 1")
            if u.unit_type not in UNIT_TYPES:
                errs.append(f"unit {i}: unit_type {u.unit_type!r} not one of {UNIT_TYPES}")
        bad = [a for a in self.accessorials if a not in ACCESSORIALS]
        if bad:
            errs.append(f"unknown accessorial(s) {bad}; known: {sorted(ACCESSORIALS)}")
        if errs:
            raise ValueError("; ".join(errs))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FreightRequest":
        return cls(
            origin=FreightAddress(**d["origin"]),
            destination=FreightAddress(**d["destination"]),
            pickup_date=d["pickup_date"],
            units=[HandlingUnit(**u) for u in d.get("units") or []],
            accessorials=list(d.get("accessorials") or []),
            commodity=d.get("commodity") or "",
            declared_value=d.get("declared_value"),
            reference=d.get("reference") or "",
        )


@dataclass
class FreightQuote:
    provider: str
    carrier: str
    price: float
    quote_id: str = ""                    # what the provider books against
    service: str = ""
    currency: str = "USD"
    transit_days: Optional[int] = None
    expires_at: Optional[str] = None
    bookable: bool = False                # can `book` act on this via the API?
    tier: str = ""                        # "indicative" | "firm" | "manual" | ""
    warnings: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)   # provider ids book() needs back

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FreightQuote":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class BookResult:
    dry_run: bool
    provider: str
    quote_id: str
    price: float
    carrier: str = ""
    booking_id: str = ""          # the provider's shipment / pickup id
    tracking_number: str = ""     # PRO or broker shipment number
    bol_url: str = ""
    tracking_url: str = ""
    payment_link: str = ""        # set when the provider booked it but it's unpaid
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class TrackEvent:
    when: str
    status: str
    location: str = ""


@dataclass
class TrackResult:
    status: str
    events: list[TrackEvent] = field(default_factory=list)
    delivered: bool = False


# NMFC density scale, docket 2025-1 (effective 2025-07-19): 13 tiers, pounds
# per cubic foot -> class. This is an ESTIMATE for density-rated commodities
# only — many items carry a fixed NMFC item class regardless of density, and
# a carrier that re-weighs and re-measures at the terminal bills what it
# finds. Pass a class explicitly whenever you know it.
_DENSITY_CLASSES = [
    (1, "400"), (2, "300"), (4, "250"), (6, "175"), (8, "125"), (10, "100"),
    (12, "92.5"), (15, "85"), (22.5, "70"), (30, "65"), (35, "60"), (50, "55"),
]


def estimate_freight_class(density_pcf: float) -> str:
    for upper, cls in _DENSITY_CLASSES:
        if density_pcf < upper:
            return cls
    return "50"
