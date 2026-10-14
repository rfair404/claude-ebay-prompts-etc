"""LTL freight — quote across several brokers, book one, track it.

The parcel path (lib/easypost_client.py, `ebz ship-quote`/`ship-buy`) stops at
a box a carrier will take at the counter. Ship-risky items — the >25 lb /
>24 in pickup-only listings of docs/local-pickup-fragile.md — have so far
offered freight "by quote in the description", with the quote itself got by
hand. This package is that quote, done properly:

    models.py      FreightRequest / HandlingUnit / FreightQuote / BookResult
    providers/     one module per broker, all behind one Provider interface
    service.py     fan a request out to every configured provider, rank the
                   answers, book one (DRY RUN unless confirm=True), track it
    store.py       the shipments themselves — SQLite at reports/freight.db

Providers today (see each module's docstring for what is and isn't verified):

    warp        wearewarp.com — firm quotes, booking, tracking and BOL via its
                developer API (sandbox / live key), plus a keyless public quote
                and a ~20-carrier indicative spread
    shipengine  ShipEngine/ShipStation API LTL — quotes from the LTL carrier
                accounts *you* connect (Advanced plan or higher)
    uship       uShip — API docs are invite-only; until access is granted it
                quotes nothing and says so. Quotes from uship.com go in by hand.
    manual      any quote got by phone or web form (uShip site, Freightquote,
                a local carrier) — recorded so it ranks next to the API ones

Guardrail — same shape as easypost_client.buy_label(): quoting spends nothing
and runs freely; booking spends money and is a DRY RUN unless confirm=True.
"""
from freight.models import (  # noqa: F401
    ACCESSORIALS, BookResult, FreightAddress, FreightQuote, FreightRequest,
    HandlingUnit, TrackEvent, TrackResult, estimate_freight_class,
)
