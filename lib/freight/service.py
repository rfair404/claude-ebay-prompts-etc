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

from config import get_freight_config, get_ship_from
from freight import http
from freight.models import (BookResult, FreightAddress, FreightQuote,
                            FreightRequest, TrackResult, quote_ref)
from freight.providers import Provider, ProviderNotReady, registry
from freight.store import FreightStore, Shipment

REPO = Path(__file__).resolve().parents[2]


def db_path() -> Path:
    """reports/freight.db in the MAIN checkout, even when this code runs from
    a worktree. A booking is money spent: its record must outlive the
    worktree it was made from (removing one deletes its untracked reports/).
    A relative freight.db_path resolves against the main checkout too."""
    from offsite import data_root   # same main-checkout resolution the backup uses
    root = data_root(REPO)
    p = get_freight_config().get("db_path")
    if p:
        return Path(p) if Path(p).is_absolute() else root / p
    return root / "reports" / "freight.db"


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
        except (ProviderNotReady, http.FreightAPIError) as e:
            return name, [], str(e)
        except Exception as e:  # noqa: BLE001 — one broken provider never sinks the rest
            return name, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=max(1, len(provs))) as ex:
        for name, qs, err in ex.map(one, provs.items()):
            quotes += qs
            if err:
                errors[name] = err
            elif not qs:
                errors[name] = "no rates returned for this lane"
    for q in quotes:
        q.ref = q.ref or quote_ref(q)
    quotes.sort(key=lambda q: (q.price, not q.bookable))
    return quotes, errors


def quote_shipment(store: FreightStore, sid: int,
                   only: Optional[list[str]] = None) -> Shipment:
    s = store.require(sid)
    quotes, errors = quote_all(s.request, only)
    return store.set_quotes(sid, quotes, errors)


def pick_quote(s: Shipment, which: str | int) -> tuple[int, FreightQuote]:
    """A quote by its ref (stable) or its 1-based row number (convenient,
    but only for a dry run — rows re-sort on every re-quote)."""
    w = str(which).strip()
    for i, q in enumerate(s.quotes, 1):
        if q.ref == w:
            return i, q
    if w.isdigit() and 1 <= int(w) <= len(s.quotes):
        return int(w), s.quotes[int(w) - 1]
    raise ValueError(f"#{s.id} has no quote {w!r} (refs: "
                     f"{', '.join(q.ref for q in s.quotes) or 'none'})")


def book(store: FreightStore, sid: int, which: str | int,
         confirm: bool = False) -> BookResult:
    """Book quote `which` of shipment `sid`.

    DRY RUN unless confirm=True: the provider is asked only to validate,
    nothing is dispatched or charged, nothing is written.

    A confirmed booking must name the quote by its REF, not its row number —
    the row a person read may not be the row that exists now. Then:
      1. claim the shipment (status -> booking) atomically, so no second
         process can book it at the same time;
      2. call the provider once;
      3. on success, record the booking FIRST, then save any inline BOL —
         a disk error must not erase the record of money spent;
      4. on a definite refusal, release the claim; on no response (timeout,
         dropped connection, 5xx gateway error) keep it and log
         book_unknown — the booking may exist, and only a person checking
         the provider can say.
    """
    s = store.require(sid)
    if s.status == "booking":
        raise ValueError(f"#{sid} already has a booking attempt in progress or of unknown "
                         "outcome — check the provider before trying again")
    if s.status not in ("quoted", "draft"):
        raise ValueError(f"#{sid} is {s.status}; nothing to book")
    n, q = pick_quote(s, which)
    if q.tier == "manual":
        raise ProviderNotReady(
            f"quote {q.ref} ({q.provider} / {q.carrier}) was entered by hand — "
            f"book it with them directly, then `ebz freight mark-booked {sid} "
            f"--quote {q.ref} --tracking PRO`")
    if confirm and str(which).strip() != q.ref:
        raise ValueError(f"a confirmed booking names the quote by ref, not row number — "
                         f"row {n} is now {q.provider} / {q.carrier} ${q.price:,.2f} "
                         f"(ref {q.ref}); re-run with --quote {q.ref} if that's the one")
    if q.expired():
        raise ValueError(f"quote {q.ref} ({q.carrier} ${q.price:,.2f}) expired at "
                         f"{q.expires_at} — re-quote before booking")
    p = providers([q.provider]).get(q.provider)
    if p is None:
        raise ProviderNotReady(f"provider {q.provider!r} is disabled in config")
    if not confirm:
        return p.book(q, s.request, confirm=False)

    prev = store.claim_booking(sid)
    try:
        result = p.book(q, s.request, confirm=True)
    except http.FreightAPIError as e:
        if e.no_response or e.status >= 500:
            store.mark_book_unknown(sid, f"{q.provider} gave no clear answer: {e}")
            raise http.FreightAPIError(
                e.provider, e.status,
                f"OUTCOME UNKNOWN — the booking may have gone through. Check "
                f"{q.provider} before anything else; then `ebz freight mark-booked "
                f"{sid} --quote {q.ref} --tracking …` or `ebz freight status {sid} "
                f"quoted`. ({e})", e.body) from e
        store.release_booking(sid, prev, str(e))
        raise
    except (ProviderNotReady, ValueError) as e:      # refused before any request
        store.release_booking(sid, prev, str(e))
        raise
    except BaseException as e:
        store.mark_book_unknown(sid, f"unexpected {type(e).__name__}: {e}")
        raise

    docs = result.raw.pop("documents", None) if isinstance(result.raw, dict) else None
    booking = _booking_dict(result, q)
    try:
        store.record_booking(sid, booking)
    except Exception as e:
        # The provider has the booking; we failed to write it down. Say so
        # loudly, with the ids, and leave the shipment locked in `booking`.
        raise RuntimeError(
            f"BOOKED with {q.provider} (booking {result.booking_id or '?'}, tracking "
            f"{result.tracking_number or '?'}, ${result.price:,.2f}) but recording it "
            f"in {store.path} failed: {e}. Record it with `ebz freight mark-booked "
            f"{sid} --quote {q.ref} --tracking {result.tracking_number or 'PRO'}`.") from e
    if docs:
        try:
            _save_documents(store.path.parent, sid, docs, result)
        except Exception as e:  # noqa: BLE001 — the booking is recorded; this is a nicety
            result.warnings.append(f"booked, but saving the BOL failed: {e}")
        booking = _booking_dict(result, q)
        store.update_booking(sid, booking)
    return result


def _booking_dict(result: BookResult, q: FreightQuote) -> dict:
    booking = asdict(result)
    booking["quote"] = q.to_dict()
    booking["quote_extra"] = q.extra
    return booking


def _save_documents(folder: Path, sid: int, docs: list, result: BookResult) -> None:
    """ShipEngine returns the BOL as base64 inside the pickup response. Write
    it to disk and keep only the path in the database."""
    for i, d in enumerate(docs or []):
        img = d.get("image")
        if not img:
            continue
        kind = re.sub(r"[^a-z_]", "", str(d.get("type") or "doc").lower()) or "doc"
        out = folder / "freight_docs" / f"{sid}-{kind}-{i}.{d.get('format') or 'pdf'}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(img))
        if kind == "bill_of_lading" and not result.bol_url:
            result.bol_url = str(out)


def mark_booked(store: FreightStore, sid: int, which: str | int, *,
                tracking: str = "", price: Optional[float] = None,
                bol_url: str = "", note: str = "") -> Shipment:
    """Record a booking made outside the API (phone, uship.com, a carrier's
    site) — or resolve a `booking` whose outcome was unknown — so tracking,
    the pick list and the freight margin still see it."""
    s = store.require(sid)
    _, q = pick_quote(s, which)
    booking = _booking_dict(BookResult(dry_run=False, provider=q.provider,
                                       quote_id=q.quote_id,
                                       price=q.price if price is None else price,
                                       carrier=q.carrier, tracking_number=tracking,
                                       bol_url=bol_url, warnings=[note] if note else []), q)
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
    """The store's ship_from (config.get_ship_from) as a pickup stop."""
    sf = get_ship_from(store_name)
    return FreightAddress(**{k: str(sf.get(k) or "") for k in
                             ("zip", "name", "company", "street1", "street2", "city",
                              "state", "phone", "email")},
                          country=str(sf.get("country") or "US"))
