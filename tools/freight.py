#!/usr/bin/env python3
"""LTL freight: compare quotes across brokers, book one, track it.

For what a parcel carrier won't take — the >25 lb / >24 in pickup-only
items whose listings say "message me for a freight quote". Quoting is free
and runs freely. Booking spends money: DRY RUN unless --confirm.

    # a buyer asks "what to 30301?" — answer in one command, nothing saved
    python -m lib.cli freight quote --to-zip 30301 --length 48 --width 40 \\
        --height 50 --weight 320 --residential-delivery --liftgate-delivery

    # a sale: open a shipment (dims from the draft), quote it, book it
    python -m lib.cli freight new --shoot fr/pfaff463 --order-id 12-34567-89012 \\
        --to-name "Jane Buyer" --to-street1 "1 Main St" --to-city Atlanta \\
        --to-state GA --to-zip 30301 --to-phone 4045550100 \\
        --length 48 --width 40 --height 50 --weight 320 --quote
    python -m lib.cli freight book 3 --quote 1                    # DRY RUN, prints the ref
    python -m lib.cli freight book 3 --quote 9f3a2c1e --confirm   # books + charges

    python -m lib.cli freight list | show 3 | track 3 | track --open | docs 3
    python -m lib.cli freight add-quote 3 --provider uship --carrier "Estes via uShip" --price 412
    python -m lib.cli freight mark-booked 3 --quote 9f3a2c1e --tracking PRO123
    python -m lib.cli freight status 3 delivered
    python -m lib.cli freight providers

Origin defaults to the store's storefront `ship_from` (same as ship-quote);
any --from-* flag overrides it. The web app shows the same shipments at
http://127.0.0.1:8770/freight.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stores                                                         # noqa: E402
from config import ConfigError                                        # noqa: E402
from freight import http                                              # noqa: E402
from freight import service as svc                                    # noqa: E402
from freight.models import (ACCESSORIALS, FreightAddress, FreightQuote,  # noqa: E402
                            FreightRequest, HandlingUnit)
from freight.providers import ProviderNotReady                        # noqa: E402
from freight.store import STATUSES, Shipment                          # noqa: E402

INVENTORY = ROOT / "inventory"
ADDR = ("name", "company", "street1", "street2", "city", "state", "zip", "phone", "email")


def next_business_day(today: date | None = None) -> str:
    d = (today or date.today()) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


# ---------------------------------------------------------------- arguments

def _add_addr(ap, prefix: str) -> None:
    for f in ADDR:
        ap.add_argument(f"--{prefix}-{f}", default=None)


def _add_load(ap) -> None:
    g = ap.add_argument_group("load (one line of identical units)")
    g.add_argument("--length", type=float, help="inches, per unit")
    g.add_argument("--width", type=float, help="inches, per unit")
    g.add_argument("--height", type=float, help="inches, per unit")
    g.add_argument("--weight", type=float, help="POUNDS, per unit, packed")
    g.add_argument("--count", type=int, default=1, help="number of identical units (default 1)")
    g.add_argument("--unit-type", default=None, choices=("pallet", "crate", "box", "piece"),
                   help="default pallet (piece when the unit comes from --shoot)")
    g.add_argument("--class", dest="freight_class", default=None,
                   help="NMFC freight class if known (else estimated from density)")
    g.add_argument("--stackable", action="store_true")
    g.add_argument("--commodity", default="", help='plain words, e.g. "antique sewing machine"')
    g.add_argument("--declared-value", type=float, default=None,
                   help="for cargo insurance quotes (Warp)")
    g.add_argument("--pickup-date", default=None, help="YYYY-MM-DD (default next business day)")
    a = ap.add_argument_group("accessorials")
    for key, label in ACCESSORIALS.items():
        a.add_argument(f"--{key.replace('_', '-')}", dest=f"acc_{key}",
                       action="store_true", help=label)


def _addr(args, prefix: str, base: FreightAddress | None = None) -> FreightAddress:
    a = base or FreightAddress(zip="")
    for f in ADDR:
        v = getattr(args, f"{prefix}_{f}", None)
        if v:
            setattr(a, f, v)
    return a


def _request(args, unit_from_draft: HandlingUnit | None = None) -> FreightRequest:
    store = stores.resolve_store_name(args.store)
    # A malformed ship_from is a ConfigError and stops here — silently
    # quoting from a blank origin is how a bad config goes unnoticed.
    origin = _addr(args, "from", svc.store_origin(store))
    if not origin.zip:
        raise SystemExit("[X] no origin zip — pass --from-zip or set storefront ship_from "
                         "in config.yaml")
    dest = _addr(args, "to")
    u = unit_from_draft
    given = [args.length, args.width, args.height, args.weight]
    if any(x is not None for x in given) or u is None:
        if any(x is None for x in given):
            raise SystemExit("[X] give all of --length --width --height --weight "
                             "(per unit, packed)" + (" — the draft has no package dims" if
                                                     unit_from_draft is None and
                                                     getattr(args, "shoot", None) else ""))
        u = HandlingUnit(length_in=args.length, width_in=args.width, height_in=args.height,
                         weight_lbs=args.weight)
    u.count = args.count
    u.unit_type = args.unit_type or u.unit_type
    u.freight_class = args.freight_class or u.freight_class
    u.stackable = args.stackable or u.stackable
    req = FreightRequest(
        origin=origin, destination=dest,
        pickup_date=args.pickup_date or next_business_day(), units=[u],
        accessorials=[k for k in ACCESSORIALS if getattr(args, f"acc_{k}", False)],
        commodity=args.commodity or u.description, declared_value=args.declared_value,
        reference=getattr(args, "order_id", None) or "")
    try:
        req.validate()
    except ValueError as e:
        raise SystemExit(f"[X] {e}")
    return req


# ---------------------------------------------------------------- printing

def _money(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _book_label(q: FreightQuote) -> str:
    if q.bookable:
        return "API"
    return "by hand" if q.tier == "manual" else "compare"


def print_quotes(quotes: list[FreightQuote], errors: dict, market_rows: int = 8) -> None:
    """Every bookable/manual quote, plus the cheapest `market_rows` of the
    indicative spread (0 = all) — 20+ carrier rows bury the answer."""
    shown = 0
    hidden = 0
    if quotes:
        print(f"  {'#':>2}  {'ref':<8}  {'provider':<10} {'carrier':<26} {'price':>10}  "
              f"{'days':>4}  {'tier':<10} book")
        for i, q in enumerate(quotes, 1):
            if q.tier == "indicative" and not q.bookable:
                if market_rows and shown >= market_rows:
                    hidden += 1
                    continue
                shown += 1
            days = "?" if q.transit_days is None else str(q.transit_days)
            print(f"  {i:>2}  {q.ref:<8}  {q.provider:<10} {q.carrier[:26]:<26} "
                  f"{_money(q.price):>10}  {days:>4}  {q.tier[:10]:<10} {_book_label(q)}"
                  + ("  EXPIRED" if q.expired() else ""))
            included = (q.extra or {}).get("included")
            if included:
                print(f"        includes: {', '.join(map(str, included))}")
            charges = (q.extra or {}).get("charges")
            if charges:
                print("        " + " · ".join(
                    f"{c.get('description')} {_money(c.get('amount'))}" for c in charges))
            for w in q.warnings:
                print(f"        ! {w}")
        if hidden:
            print(f"      … {hidden} more market rate(s) — --all-rates to list them")
    else:
        print("  no quotes")
    for name, why in errors.items():
        why = why[len(name) + 2:] if why.startswith(f"{name}: ") else why
        print(f"  -- {name}: {why}")


def print_shipment(s: Shipment) -> None:
    r = s.request
    print(f"#{s.id}  [{s.status}]  {s.title or '(untitled)'}{_flag(s)}")
    print(f"  store {s.store or 'default'}   order {s.order_id or '—'}   sku {s.sku or '—'}")
    print(f"  {r.origin.zip} {r.origin.city} -> {r.destination.zip} {r.destination.city} "
          f"{r.destination.name}   pickup {r.pickup_date}")
    for x in r.units:
        print(f"  {x.count} x {x.unit_type} {x.length_in:g}x{x.width_in:g}x{x.height_in:g} in, "
              f"{x.weight_lbs:g} lb each — {x.density_pcf:.1f} pcf, class "
              f"{x.effective_class()}{'' if x.freight_class else ' (est.)'}")
    if r.accessorials:
        print("  accessorials: " + ", ".join(ACCESSORIALS[a] for a in r.accessorials))
    if s.status == "booking":
        last = next((e for e in reversed(s.events) if e.get("status") == "book_unknown"), {})
        print(f"  ! booking outcome unknown: {last.get('note') or '?'}")
        print(f"    check the provider, then `freight mark-booked {s.id} --quote REF "
              f"--tracking …` or `freight status {s.id} quoted`")
    if s.booking:
        b = s.booking
        if b.get("payment_link"):
            print(f"  UNPAID — on hold until paid: {b['payment_link']}")
        print(f"  BOOKED {b.get('provider')} / {b.get('carrier')}  {_money(b.get('price'))}  "
              f"tracking {b.get('tracking_number') or '—'}")
        for k in ("tracking_url", "bol_url"):
            if b.get(k):
                print(f"    {k}: {b[k]}")
    if s.buyer_paid is not None:
        print(f"  buyer paid {_money(s.buyer_paid)}   margin {_money(s.margin)}")
    if s.notes:
        print(f"  notes: {s.notes}")
    if not s.booking:
        print(f"  quotes ({s.quoted_at or 'never quoted'}):")
        print_quotes(s.quotes, s.quote_errors)
    for e in s.events:
        print(f"  · {e.get('when')}  {e.get('status')}  {e.get('note') or ''}")


# ---------------------------------------------------------------- commands

def cmd_providers(args) -> int:
    for r in svc.provider_status():
        print(f"  {'ok ' if r['ready'] else '-- '} {r['provider']:<11} {r['note']}")
    return 0


def cmd_quote(args) -> int:
    req = _request(args)
    quotes, errors = svc.quote_all(req, args.provider)
    if args.json:
        print(json.dumps({"quotes": [q.to_dict() for q in quotes], "errors": errors}, indent=2))
        return 0
    u = req.units[0]
    print(f"{req.origin.zip} -> {req.destination.zip}, {u.count} x {u.weight_lbs:g} lb "
          f"{u.unit_type}, class {u.effective_class()}"
          f"{'' if u.freight_class else ' (est.)'}, pickup {req.pickup_date}")
    print_quotes(quotes, errors, 0 if args.all_rates else 8)
    print("\n  Nothing saved or booked. `freight new ... --quote` to open a shipment.")
    return 0


def _resolve_shoot(rel: str) -> Path:
    p = (INVENTORY / rel).resolve()
    if INVENTORY.resolve() not in p.parents or not (p / "draft.md").exists():
        raise SystemExit(f"[X] no draft.md under inventory/{rel}")
    return p


def cmd_new(args) -> int:
    meta: dict = {}
    if args.shoot:
        meta = svc.from_draft(_resolve_shoot(args.shoot))
    req = _request(args, meta.get("unit"))
    store = stores.resolve_store_name(args.store)
    fs = svc.open_store()
    sid = fs.create(req, store=store, order_id=args.order_id or "",
                    sku=args.sku or meta.get("sku", ""),
                    title=args.title or meta.get("title", ""),
                    notes=args.notes or "", buyer_paid=args.buyer_paid)
    print(f"[OK] freight shipment #{sid} opened")
    if args.quote:
        svc.quote_shipment(fs, sid, args.provider)
    print_shipment(fs.require(sid))
    return 0


def _flag(s: Shipment) -> str:
    if s.status == "booking":
        return "  [OUTCOME UNKNOWN — check the provider]"
    if s.status == "booked" and (s.booking or {}).get("payment_link"):
        return "  [UNPAID — on hold]"
    return ""


def cmd_list(args) -> int:
    rows = svc.open_store().list(status=args.status, open_only=not args.all)
    if not rows:
        print("no freight shipments" + ("" if args.all else " open (--all for history)"))
    for s in rows:
        best = s.cheapest
        price = _money((s.booking or {}).get("price") if s.booking else
                       (best.price if best else None))
        print(f"  #{s.id:<4} {s.status:<10} {s.request.origin.zip}->{s.request.destination.zip}  "
              f"{price:>10}  {(s.booking or {}).get('carrier') or (best.carrier if best else ''):<20} "
              f"{s.title[:40]}{_flag(s)}")
    return 0


def cmd_show(args) -> int:
    s = svc.open_store().require(args.id)
    if args.json:
        print(json.dumps(s.to_dict(), indent=2))
    else:
        print_shipment(s)
    return 0


def cmd_requote(args) -> int:
    fs = svc.open_store()
    s = svc.quote_shipment(fs, args.id, args.provider)
    print_shipment(s)
    return 0


def cmd_add_quote(args) -> int:
    fs = svc.open_store()
    q = FreightQuote(provider=args.provider, carrier=args.carrier, price=args.price,
                     service=args.service or "", transit_days=args.transit_days,
                     quote_id=args.quote_id or "", expires_at=args.expires,
                     warnings=[args.note] if args.note else [])
    print_shipment(fs.add_manual_quote(args.id, q))
    return 0


def cmd_book(args) -> int:
    fs = svc.open_store()
    s = fs.require(args.id)
    r = svc.book(fs, args.id, args.quote, confirm=args.confirm)
    if r.dry_run:
        _, q = svc.pick_quote(s, args.quote)
        print("[DRY RUN] Nothing booked, nothing charged. This WOULD book:")
        print(f"  #{s.id} {s.request.origin.zip} -> {s.request.destination.zip}  "
              f"pickup {s.request.pickup_date}")
        print(f"  {q.provider} / {q.carrier} {q.service}  {_money(q.price)}  ref {q.ref}  "
              f"(provider quote {q.quote_id})")
        for w in r.warnings + q.warnings:
            print(f"  ! {w}")
        print(f"\n  To book exactly this quote: python -m lib.cli freight book {s.id} "
              f"--quote {q.ref} --confirm")
        return 0
    sandbox = (r.raw or {}).get("mode") == "sandbox"
    print("[OK] Booked in the SANDBOX — no truck, no charge:" if sandbox else
          "[OK] Booked — a real pickup is scheduled" +
          (" (UNPAID, on hold):" if r.payment_link else " and money is committed:"))
    print(f"  {r.provider} / {r.carrier}  {_money(r.price)}")
    print(f"  booking {r.booking_id or '—'}   tracking {r.tracking_number or '—'}")
    for k in ("tracking_url", "bol_url"):
        if getattr(r, k):
            print(f"  {k}: {getattr(r, k)}")
    if r.payment_link:
        print(f"  PAY HERE (order is on hold until paid): {r.payment_link}")
    for w in r.warnings:
        print(f"  ! {w}")
    if s.order_id and r.tracking_number:
        store_flag = f"--store {s.store} " if s.store else ""
        print("\n  Put the PRO on the eBay order (writes to eBay — your call). CODE is")
        print(f"  eBay's shipping-carrier code for {r.carrier}; check it before running:")
        print(f"    python -m lib.cli pick-list {store_flag}--record-tracking {s.order_id} "
              f"--carrier CODE --tracking-number {r.tracking_number} --confirm")
    return 0


def cmd_mark_booked(args) -> int:
    fs = svc.open_store()
    print_shipment(svc.mark_booked(fs, args.id, args.quote, tracking=args.tracking or "",
                                   price=args.price, bol_url=args.bol or "",
                                   note=args.note or ""))
    return 0


def cmd_track(args) -> int:
    fs = svc.open_store()
    ids = [s.id for s in fs.list(open_only=True) if s.booking] if args.open else [args.id]
    if not ids or ids == [None]:
        print("nothing booked and open" if args.open else "[X] give an id or --open")
        return 0 if args.open else 2
    rc = 0
    for sid in ids:
        try:
            t = svc.track(fs, sid)
        except (ProviderNotReady, http.FreightAPIError, ValueError) as e:
            print(f"  #{sid}: {e}")
            rc = 1
            continue
        print(f"  #{sid}: {t.status}{'  (delivered)' if t.delivered else ''} -> now "
              f"{fs.require(sid).status}")
        for e in t.events[:5]:
            print(f"      {e.when}  {e.status}  {e.location}")
    return rc


def cmd_docs(args) -> int:
    docs = svc.documents(svc.open_store(), args.id)
    if not docs:
        print(f"  #{args.id}: no documents yet (the BOL can lag the booking)")
    for d in docs:
        print(f"  {d.get('type')}{'/' + d['podType'] if d.get('podType') else ''}: {d.get('url')}")
    return 0


def cmd_status(args) -> int:
    print_shipment(svc.open_store().set_status(args.id, args.status, note=args.note or ""))
    return 0


def cmd_set(args) -> int:
    fs = svc.open_store()
    fields = {k: getattr(args, k) for k in ("order_id", "sku", "title", "notes", "buyer_paid")
              if getattr(args, k) is not None}
    s = fs.set_fields(args.id, **fields)
    to_given = any(getattr(args, f"to_{f}") for f in ADDR)
    from_given = any(getattr(args, f"from_{f}") for f in ADDR)
    if to_given or from_given or args.pickup_date:
        req = s.request
        req.destination = _addr(args, "to", req.destination)
        req.origin = _addr(args, "from", req.origin)
        req.pickup_date = args.pickup_date or req.pickup_date
        had = len(s.quotes)
        fs.update_request(args.id, req)
        if had and not fs.require(args.id).quotes:
            print("  lane changed — quotes cleared, re-quote before booking")
    print_shipment(fs.require(args.id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ebz freight", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    stores.add_store_args(ap, help_extra="Supplies the default origin (storefront ship_from).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("providers", help="which brokers are wired up")

    q = sub.add_parser("quote", help="ad-hoc comparison, nothing saved")
    _add_addr(q, "from")
    _add_addr(q, "to")
    _add_load(q)
    q.add_argument("--provider", action="append", help="only these (repeatable)")
    q.add_argument("--json", action="store_true")
    q.add_argument("--all-rates", action="store_true", help="list every market rate")

    n = sub.add_parser("new", help="open a shipment (optionally quote it now)")
    _add_addr(n, "from")
    _add_addr(n, "to")
    _add_load(n)
    n.add_argument("--shoot", help="inventory/ path of the item — title, SKU, package dims")
    n.add_argument("--order-id")
    n.add_argument("--sku")
    n.add_argument("--title")
    n.add_argument("--notes")
    n.add_argument("--buyer-paid", type=float, help="freight the buyer paid you")
    n.add_argument("--quote", action="store_true", help="quote every provider now")
    n.add_argument("--provider", action="append")

    ls = sub.add_parser("list")
    ls.add_argument("--all", action="store_true", help="include delivered/cancelled")
    ls.add_argument("--status", choices=STATUSES)

    sh = sub.add_parser("show")
    sh.add_argument("id", type=int)
    sh.add_argument("--json", action="store_true")

    rq = sub.add_parser("requote", help="re-run every provider (manual quotes kept)")
    rq.add_argument("id", type=int)
    rq.add_argument("--provider", action="append")

    aq = sub.add_parser("add-quote", help="record a quote got by phone / web")
    aq.add_argument("id", type=int)
    aq.add_argument("--provider", required=True, help="uship, freightquote, a carrier…")
    aq.add_argument("--carrier", required=True)
    aq.add_argument("--price", type=float, required=True)
    aq.add_argument("--service")
    aq.add_argument("--transit-days", type=int)
    aq.add_argument("--quote-id")
    aq.add_argument("--expires")
    aq.add_argument("--note")

    b = sub.add_parser("book", help="book a quote — DRY RUN unless --confirm")
    b.add_argument("id", type=int)
    b.add_argument("--quote", required=True,
                   help="the quote's ref (or, for a dry run, its row number)")
    b.add_argument("--confirm", action="store_true",
                   help="actually book. Without it nothing is dispatched or charged.")

    mb = sub.add_parser("mark-booked", help="record a booking made outside the API")
    mb.add_argument("id", type=int)
    mb.add_argument("--quote", required=True, help="the quote's ref or row number")
    mb.add_argument("--tracking", help="PRO / shipment number")
    mb.add_argument("--price", type=float, help="final price if it differs from the quote")
    mb.add_argument("--bol", help="BOL link or path")
    mb.add_argument("--note")

    t = sub.add_parser("track")
    t.add_argument("id", type=int, nargs="?")
    t.add_argument("--open", action="store_true", help="every open, booked shipment")

    dc = sub.add_parser("docs", help="BOL / POD links for a booked shipment")
    dc.add_argument("id", type=int)

    st = sub.add_parser("status", help="set status by hand")
    st.add_argument("id", type=int)
    st.add_argument("status", choices=STATUSES)
    st.add_argument("--note")

    se = sub.add_parser("set", help="edit order/sku/title/notes/buyer-paid, pickup date or either stop")
    se.add_argument("id", type=int)
    for f in ("order_id", "sku", "title", "notes"):
        se.add_argument(f"--{f.replace('_', '-')}", dest=f)
    se.add_argument("--buyer-paid", type=float, dest="buyer_paid")
    se.add_argument("--pickup-date", help="YYYY-MM-DD; changing it clears the quotes")
    _add_addr(se, "from")
    _add_addr(se, "to")
    return ap


COMMANDS = {
    "providers": cmd_providers, "quote": cmd_quote, "new": cmd_new, "list": cmd_list,
    "show": cmd_show, "requote": cmd_requote, "add-quote": cmd_add_quote,
    "book": cmd_book, "mark-booked": cmd_mark_booked, "track": cmd_track,
    "status": cmd_status, "set": cmd_set, "docs": cmd_docs,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.store:
        try:
            stores.validate_store_name(args.store)
        except ValueError as e:
            raise SystemExit(f"[X] {e}")
    try:
        return COMMANDS[args.cmd](args)
    except (KeyError, ValueError, ProviderNotReady, ConfigError) as e:
        print(f"[X] {e.args[0] if isinstance(e, KeyError) else e}", file=sys.stderr)
        return 1
    except (http.FreightAPIError, RuntimeError) as e:
        print(f"[X] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
