"""uShip — present, honest, and not yet wired.

uShip's shipper API (LTL rates + booking, "LTL Direct" / "LTL Connect") is
documented at developer.uship.com, but every guide there is invite-only
("You must have been invited to get access", checked 2026-10-13) and sandbox
keys come from apisupport@uship.com. Nothing here guesses at endpoints that
can't be read.

Until access is granted:
  * `quote` raises ProviderNotReady with the next step, so the comparison
    table shows uShip as "not connected" instead of quietly omitting it.
  * Quotes from the uship.com website go in by hand —
    `ebz freight add-quote ID --provider uship --carrier … --price …` —
    and rank alongside the API quotes.

Once you have the docs: implement quote/book/track here against them, using
freight.http.send (it already carries the no-retry-on-book rule) and the
config block freight.providers.uship ({api_key, sandbox}).
"""
from __future__ import annotations

from freight.models import FreightQuote, FreightRequest
from freight.providers import Provider, ProviderNotReady


class UShip(Provider):
    name = "uship"

    def status(self) -> tuple[bool, str]:
        return False, ("API docs are invite-only — email apisupport@uship.com; "
                       "add uship.com quotes by hand meanwhile")

    def quote(self, req: FreightRequest) -> list[FreightQuote]:
        raise ProviderNotReady("uship: API not connected (invite-only docs). Get a "
                               "quote on uship.com and record it with "
                               "`ebz freight add-quote --provider uship`")
