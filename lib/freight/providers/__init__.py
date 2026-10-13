"""Provider registry. A provider is a class with:

    name: str
    status() -> (usable: bool, why: str)     # for `ebz freight providers`
    quote(req) -> list[FreightQuote]         # free; may raise FreightAPIError
    book(quote, req, confirm) -> BookResult  # DRY RUN unless confirm=True
    track(booking: dict) -> TrackResult

Adding one is a module here plus one line in REGISTRY.
"""
from __future__ import annotations

from freight.models import (BookResult, FreightQuote, FreightRequest,
                            TrackResult)


class ProviderNotReady(RuntimeError):
    """The provider can't do this (no key, no API access, no such feature).
    Distinct from FreightAPIError: nothing was sent."""


class Provider:
    name = ""

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    def status(self) -> tuple[bool, str]:
        return True, ""

    def quote(self, req: FreightRequest) -> list[FreightQuote]:
        raise ProviderNotReady(f"{self.name} does not quote via API")

    def book(self, quote: FreightQuote, req: FreightRequest,
             confirm: bool = False) -> BookResult:
        raise ProviderNotReady(f"{self.name} does not book via API — book it "
                               f"with the carrier, then `ebz freight mark-booked`")

    def track(self, booking: dict) -> TrackResult:
        raise ProviderNotReady(f"{self.name} does not track via API")


def registry() -> dict[str, type[Provider]]:
    from freight.providers.shipengine import ShipEngine
    from freight.providers.uship import UShip
    from freight.providers.warp import Warp
    return {"warp": Warp, "shipengine": ShipEngine, "uship": UShip}
