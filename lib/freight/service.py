"""Orchestration: quote every provider at once, book one, track it.

Used by both the CLI (tools/freight.py) and the web app (webapp/freight.py),
so the confirm gate lives in exactly one place: book() spends nothing unless
confirm=True, and a confirmed book is never retried on a network error.
"""
from __future__ import annotations

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from config import get_freight_config, get_storefront
from freight import http
from freight.models import (BookResult, FreightAddress, FreightQuote,
                            FreightRequest, TrackResult)
from freight.providers import Provider, ProviderNotReady, registry
from freight.store import FreightStore, Shipment

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO / "reports" / "freight.db"


def db_path() -> Path:
    p = get_freight_config().get("db_path")
    return (REPO / p) if p and not Path(p).is_absolute() else Path(p or DEFAULT_DB)


def open_store(path: Optional[Path] = None) -> FreightStore:
    return FreightStore(path or db_path())


def providers(only: Optional[list[str]] = None) -> dict[str, Provider]:
    """Every provider not switched off with `enabled: false`, configured from
    freight.providers.<name>."""
    cfg = get_freight_config().get("providers") or {}
    out = {}
    for name, cls in registry().items():
        pc = cfg.get(name) or {}
        if pc.get("enabled") is False:
            continue
        if only and name not in only:
            continue
        out[name] = cls(pc)
    return out


def provider_status() -> list[dict]:
    rows = []
    cfg = get_freight_config().get("providers") or {}
    for name, cls in registry().items():
        if (cfg.get(name) or {}).get("enabled") is False:
            rows.append({"provider": name, "ready": False, "note": "disabled in config"})
            continue
        ok, why = cls(cfg.get(name) or {}).status()
        rows.append({"provider": name, "ready": ok, "note": why})
    return rows


def quote_all(req: FreightRequest, only: Optional[list[str]] = None
              ) -> tuple[list[FreightQuote], dict[str, str]]:
    """Ask every provider in parallel. Returns (quotes cheapest-first,
    {provider: why it gave nothing}). One provider failing never sinks the
    rest — the whole point is comparison."""
    req.validate()
    provs = providers(only)
    quotes: list[FreightQuote] = []
    errors: dict[str, str] = {}

    def one(item):
        name, p = item
        try:
            return name, p.quote(req), None
        except (ProviderNotReady, http.FreightAPIError, ValueError) as e:
            return name, [], str(e)

    with ThreadPoolExecutor(max_workers=max(1, len(provs))) as ex:
        for name, qs, err in ex.map(one, provs.items()):
            quotes += qs
            if err:
                errors[name] = err
            elif not qs:
                errors[name] = "no rates returned for this lane"
    quotes.sort(key=lambda q: (q.price, not q.bookable))
    return quotes, errors


def quote_shipment(store: FreightStore, sid: int,
                   only: Optional[list[str]] = None) -> Shipment:
    s = store.require(sid)
    quotes, errors = quote_all(s.request, only)
    return store.set_quotes(sid, quotes, errors)


def book(store: FreightStore, sid: int, quote_index: int,
         confirm: bool = False) -> BookResult:
    """Book quote number `quote_index` (1-based, as listed) of shipment `sid`.

    DRY RUN unless confirm=True: the provider is asked only to validate,
    nothing is dispatched or charged, nothing is written. On a real booking
    the result is written to the shipment before anything else can fail, and
    any BOL the provider returned inline is saved next to the database.
    """
    s = store.require(sid)
    if s.status not in ("quoted", "draft"):
        raise ValueError(f"#{sid} is {s.status}; nothing to book")
    if not 1 <= quote_index <= len(s.quotes):
        raise ValueError(f"#{sid} has {len(s.quotes)} quote(s); pick 1-{len(s.quotes)}")
    q = s.quotes[quote_index - 1]
    if q.tier == "manual":
        raise ProviderNotReady(
            f"quote {quote_index} ({q.provider} / {q.carrier}) was entered by hand — "
            f"book it with them directly, then `ebz freight mark-booked {sid} "
            f"--quote {quote_index} --tracking PRO`")
    p = providers([q.provider]).get(q.provider)
    if p is None:
        raise ProviderNotReady(f"provider {q.provider!r} is disabled in config")
    result = p.book(q, s.request, confirm=confirm)
    if result.dry_run:
        return result
    _save_documents(store, sid, result)
    booking = asdict(result)
    booking["quote"] = q.to_dict()
    booking["quote_extra"] = q.extra
    store.record_booking(sid, booking)
    return result


def _save_documents(store: FreightStore, sid: int, result: BookResult) -> None:
    """ShipEngine returns the BOL as base64 inside the pickup response. Write
    it to disk and keep only the path in the database."""
    docs = result.raw.pop("documents", None) if isinstance(result.raw, dict) else None
    for i, d in enumerate(docs or []):
        img = d.get("image")
        if not img:
            continue
        kind = re.sub(r"[^a-z_]", "", str(d.get("type") or "doc").lower()) or "doc"
        out = store.path.parent / "freight_docs" / f"{sid}-{kind}-{i}.{d.get('format') or 'pdf'}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(img))
        if kind == "bill_of_lading" and not result.bol_url:
            result.bol_url = str(out)


def mark_booked(store: FreightStore, sid: int, quote_index: int, *,
                tracking: str = "", price: Optional[float] = None,
                bol_url: str = "", note: str = "") -> Shipment:
    """Record a booking made outside the API (phone, uship.com, a carrier's
    site) so tracking, the pick list and the freight margin still see it."""
    s = store.require(sid)
    if not 1 <= quote_index <= len(s.quotes):
        raise ValueError(f"#{sid} has {len(s.quotes)} quote(s); pick 1-{len(s.quotes)}")
    q = s.quotes[quote_index - 1]
    booking = asdict(BookResult(dry_run=False, provider=q.provider, quote_id=q.quote_id,
                                price=q.price if price is None else price,
                                carrier=q.carrier, tracking_number=tracking,
                                bol_url=bol_url, warnings=[note] if note else []))
    booking["quote"] = q.to_dict()
    booking["quote_extra"] = q.extra
    booking["manual"] = True
    return store.record_booking(sid, booking)


def track(store: FreightStore, sid: int) -> TrackResult:
    """Ask the booking's provider where it is; advance the status on a clear
    answer. A manually booked shipment has no API to ask — update it with
    `ebz freight status`."""
    s = store.require(sid)
    if not s.booking:
        raise ValueError(f"#{sid} isn't booked")
    if s.booking.get("manual"):
        raise ProviderNotReady(f"#{sid} was booked by hand — check the carrier's site "
                               f"(PRO {s.booking.get('tracking_number') or '?'})")
    p = providers([s.booking["provider"]]).get(s.booking["provider"])
    if p is None:
        raise ProviderNotReady(f"provider {s.booking['provider']!r} is disabled in config")
    t = p.track(s.booking)
    target = "delivered" if t.delivered else _status_from(t.status)
    if target and target != s.status and _later(target, s.status):
        store.set_status(sid, target, note=f"tracking: {t.status}")
    return t


def documents(store: FreightStore, sid: int) -> list[dict]:
    """BOL / POD links for a booked shipment, from its provider."""
    s = store.require(sid)
    if not s.booking:
        raise ValueError(f"#{sid} isn't booked")
    if s.booking.get("manual"):
        raise ProviderNotReady(f"#{sid} was booked by hand — its BOL came from the carrier")
    p = providers([s.booking["provider"]]).get(s.booking["provider"])
    if p is None or not hasattr(p, "documents"):
        raise ProviderNotReady(f"{s.booking['provider']} has no documents API here")
    return p.documents(s.booking)


_ORDER = ["booked", "picked_up", "in_transit", "delivered"]


def _later(a: str, b: str) -> bool:
    return b in _ORDER and a in _ORDER and _ORDER.index(a) > _ORDER.index(b)


def _status_from(raw: str) -> Optional[str]:
    r = (raw or "").lower().replace("-", "_").replace(" ", "_")
    if "deliver" in r and "out_for" not in r:
        return "delivered"
    if "transit" in r or "out_for" in r or "terminal" in r:
        return "in_transit"
    if "pick" in r and ("up" in r or "success" in r):
        return "picked_up"
    return None


def from_draft(shoot_dir: Path) -> dict:
    """Title, SKU and a starting handling unit from a shoot's draft.md.

    The draft's package_in/weight are the item as boxed for parcel — on a
    pallet it gets bigger and heavier — so this is a starting point to edit,
    not a measurement. Returns {} fields it can't find rather than zeros."""
    from draft_io import parse_draft
    from freight.models import HandlingUnit
    d = parse_draft(Path(shoot_dir) / "draft.md")
    out: dict = {"title": str(d.get("title") or ""),
                 "sku": str(d.get("meta.ebay_inventory_sku") or d.get("meta.item_id") or ""),
                 "store": str(d.get("store") or "")}
    lb = float(d.get("shipping.weight.major_lb") or 0) + \
        float(d.get("shipping.weight.minor_oz") or 0) / 16
    dims = [float(d.get(f"shipping.package_in.{k}") or 0) for k in ("length", "width", "depth")]
    if lb > 0 and all(x > 0 for x in dims):
        out["unit"] = HandlingUnit(length_in=dims[0], width_in=dims[1], height_in=dims[2],
                                   weight_lbs=round(lb, 1), unit_type="piece",
                                   description=out["title"][:60])
    return out


def store_origin(store_name: Optional[str]) -> FreightAddress:
    """The store's ship_from (storefront config) as a pickup stop."""
    sf = get_storefront(store_name).get("ship_from") or {}
    if not isinstance(sf, dict):
        sf = {}
    return FreightAddress(
        zip=str(sf.get("zip") or ""), name=str(sf.get("name") or ""),
        street1=str(sf.get("street1") or ""), street2=str(sf.get("street2") or ""),
        city=str(sf.get("city") or ""), state=str(sf.get("state") or ""),
        country=str(sf.get("country") or "US"), phone=str(sf.get("phone") or ""),
        email=str(sf.get("email") or ""))
