#!/usr/bin/env python3
"""LIVE LINEUP — build an eBay Live show's lineup from our own listings.

eBay Live has no API; Stream manager's "Add listings -> By Item ID/URL" takes
pasted Item IDs. This reads the store's offers (Inventory API, read-only),
checks each against the rules eBay documents for Live (lib/live_show.py
live_flags), and writes inventory/_live/<show>/:

  lineup.json    every requested lot, in order, with its flags
  item_ids.txt   the unflagged Item IDs, one per line — paste these

A flagged lot (Best Offer on, reserve set, Buy It Now on an auction, not
active) is left out of item_ids.txt unless --keep-flagged.

Lots flagged only for Best Offer are fixed as part of the build. Without
--confirm the tool lists them and changes nothing. With --confirm it turns
Best Offer off on those live listings (updateOffer, nothing else changed),
saves each listing's old terms in lineup.json, and puts the lots in
item_ids.txt. After the show, --restore-best-offer --confirm turns Best
Offer back on for listings that are still unsold Buy It Now.

Buy It Now lots don't need converting here. Stream manager's "Change to
auction" does that per event, for single-quantity, single-SKU listings;
lots it can't convert get a note.

tools/live_prep.py is the one-command version: it finds the show's items,
keeps only ones that comply or can be fixed, and runs this build and the
marquee.

    python tools/live_lineup.py --store junk --show oct-20 --items 880009332259 ... \
        --start "2026-10-20 20:00" [--confirm]
    python tools/live_lineup.py --store junk --show oct-20 --all
    python tools/live_lineup.py --store junk --show oct-20 --from-marquee [--confirm]
    python tools/live_lineup.py --store junk --show oct-20 --append --items <new id>
    python tools/live_lineup.py --store junk --show oct-20 --restore-best-offer [--confirm]

--start is the event's scheduled start as set in Live Studio (Eastern unless
it carries an offset). eBay keeps a show's orders in "Paid - wait to ship"
for 8 hours after it so buyers can combine shipping, and tools/pick_list.py
--poll holds them until then. A rebuild without --start keeps the saved
start; --no-start clears it, on a rebuild or on its own:

    python tools/live_lineup.py --store junk --show oct-20 --no-start

--items resolves Item IDs through the store's listings ledger (Item ID ->
SKU) and fetches just those offers; IDs the ledger doesn't know are found by
walking the account's offers. --all walks every offer on the account
(one call per SKU — a minute or two on a few hundred listings) and keeps the
active ones. Listings made in the eBay web UI aren't visible to the Inventory
API, so neither mode can see them; paste those IDs into Stream manager by hand.

Run from the main checkout (config.yaml and the ledgers live there), or set
EBAYBIZ_CONFIG. Then serve the host page: `python -m lib.cli serve` and open
http://127.0.0.1:8770/live/<show>.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import live_show as LS                                                 # noqa: E402
import stores                                                          # noqa: E402


def ledger_skus(store: str) -> dict[str, str]:
    """Item ID -> SKU from the store's listings ledger."""
    led = stores.paths(store).listings_ledger
    if not led.exists():
        return {}
    with led.open(encoding="utf-8-sig", newline="") as f:
        return {r["listing_id"]: r["sku"] for r in csv.DictReader(f)
                if r.get("listing_id") and r.get("sku")}


def _walk_offers(creds, wanted=None, verbose=True, workers=8):
    """Every offer on the account as (offer, title, sku). With `wanted`
    (a set of Item IDs), stop once all of them have been seen.

    One GET /offer per SKU, so the calls run `workers` at a time; on the
    default store's ~370 SKUs a serial walk took several minutes."""
    from concurrent.futures import ThreadPoolExecutor
    from ebay_client import (iter_inventory_items, get_offers_for_sku,
                             get_user_access_token, EbayAPIError)

    def offers_of(it):
        try:
            return it, get_offers_for_sku(it.get("sku"), creds=creds)
        except EbayAPIError:
            return it, []

    get_user_access_token(creds)   # refresh once, before the threads share it
    items = iter_inventory_items(creds=creds)
    found = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (it, offers) in enumerate(pool.map(offers_of, items), 1):
            if verbose and n % 100 == 0:
                print(f"    {n}/{len(items)} SKUs", flush=True)
            title = str((it.get("product") or {}).get("title") or "")
            for o in offers:
                found.add((o.get("listing") or {}).get("listingId"))
                yield o, title, it.get("sku")
            if wanted is not None and wanted <= found:
                pool.shutdown(wait=False, cancel_futures=True)
                return


def fetch_entries(store: str, item_ids: list[str] | None, verbose: bool = True):
    """(offer, title, shoot) triples for the requested listings, in order,
    plus (item_id, reason) for any that couldn't be found."""
    from ebay_client import load_credentials, get_offers_for_sku, EbayAPIError
    import tools.inventory_sync as INV

    creds = load_credentials(store=store)
    shoots = {sku: v["shoot"] for sku, v in INV.local_index(store).items()}

    if item_ids is None:
        entries = [(o, t, shoots.get(sku)) for o, t, sku in _walk_offers(creds)
                   if (o.get("listing") or {}).get("listingStatus") == "ACTIVE"]
        if verbose:
            print(f"  {len(entries)} active offers on store {store!r}")
        return entries, []

    # Fast path: the ledger maps Item ID -> SKU, one offer call each. The
    # ledger can lag eBay (listings made before it was rebuilt), so anything
    # it doesn't know is found by walking the account's offers once.
    by_id: dict[str, tuple[dict, str, str]] = {}
    sku_of = ledger_skus(store)
    for iid in dict.fromkeys(item_ids):
        sku = sku_of.get(iid)
        if not sku:
            continue
        try:
            offers = get_offers_for_sku(sku, creds=creds)
        except EbayAPIError:
            continue
        for o in offers:
            if (o.get("listing") or {}).get("listingId") == iid:
                by_id[iid] = (o, _title(sku, creds), sku)
    rest = set(item_ids) - set(by_id)
    if rest:
        if verbose:
            print(f"  {len(rest)} Item ID(s) not in the ledger — walking the "
                  f"account's offers to find them")
        for o, title, sku in _walk_offers(creds, wanted=rest):
            iid = (o.get("listing") or {}).get("listingId")
            if iid in rest and iid not in by_id:
                by_id[iid] = (o, title, sku)

    entries, missing = [], []
    for iid in item_ids:
        if iid in by_id:
            o, title, sku = by_id[iid]
            entries.append((o, title, shoots.get(sku)))
        else:
            missing.append((iid, f"no offer on store {store!r} carries this Item ID "
                                 f"(made in the eBay web UI, or another store?)"))
    return entries, missing


def _title(sku: str, creds) -> str:
    import urllib.parse
    from ebay_client import api_send
    item = api_send("GET", "/sell/inventory/v1/inventory_item/"
                    + urllib.parse.quote(str(sku), safe=""),
                    creds=creds, marketplace=None)
    return str((item.get("product") or {}).get("title") or "")


EASTERN = ZoneInfo("America/New_York")


def parse_start(text: str) -> datetime:
    """'2026-10-20 20:00' (Eastern unless it carries an offset) -> aware datetime."""
    dt = datetime.fromisoformat(text.strip().replace(" ", "T").replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=EASTERN)


def _offer_api():
    from ebay_client import load_credentials, get_offer, api_send, EbayAPIError
    from list_edit import _WRITABLE_OFFER_KEYS
    return load_credentials, get_offer, api_send, EbayAPIError, _WRITABLE_OFFER_KEYS


def clear_best_offer(store: str, lots: list[dict], confirm: bool) -> int:
    """Turn Best Offer off on the lots flagged only for it, keeping each
    listing's old terms on the lot ("best_offer_was") for the post-show
    restore. Dry run unless `confirm`. Each listing is re-read first and
    skipped unless it is still an ACTIVE listing with Best Offer on.
    Returns the number changed."""
    targets = LS.best_offer_only(lots)
    if not targets:
        return 0
    if not confirm:
        print(f"\n  Best Offer is on for {len(targets)} lot(s); eBay Live won't take "
              f"them. Re-run with --confirm to turn it off on these listings:")
        for l in targets:
            print(f"    lot {l['lot']} {l['item_id']} {l['title'][:50]!r}")
        return 0

    load_credentials, get_offer, api_send, EbayAPIError, writable = _offer_api()
    creds = load_credentials(store=store)
    done = 0
    for l in targets:
        try:
            cur = get_offer(l["offer_id"], creds=creds)
            status = (cur.get("listing") or {}).get("listingStatus")
            terms = (cur.get("listingPolicies") or {}).get("bestOfferTerms") or {}
            if status != "ACTIVE" or not terms.get("bestOfferEnabled"):
                print(f"    [skip] lot {l['lot']} {l['item_id']}: now "
                      f"{status}, Best Offer {'on' if terms.get('bestOfferEnabled') else 'off'}")
                l["flags"] = LS.live_flags(cur)
                continue
            api_send("PUT", f"/sell/inventory/v1/offer/{l['offer_id']}",
                     LS.offer_with_best_offer(cur, writable, None), creds=creds)
        except EbayAPIError as exc:
            print(f"    [X] lot {l['lot']} {l['item_id']}: {exc}")
            continue
        l["best_offer_was"] = terms
        l["flags"] = [f for f in l["flags"] if f != LS.BEST_OFFER_FLAG]
        done += 1
        print(f"    [OK] lot {l['lot']} {l['item_id']}: Best Offer off")
    print(f"  Best Offer turned off on {done}/{len(targets)} listing(s)")
    return done


def restore_best_offer(store: str, lots: list[dict], confirm: bool) -> int:
    """After the show: put back the Best Offer terms prep turned off, on
    listings that are still unsold Buy It Now under the same Item ID. A
    listing that sold or ended drops its record; one that is still running
    as an auction keeps it for a later run. Dry run unless `confirm`."""
    targets = [l for l in lots if l.get("best_offer_was") and l.get("offer_id")]
    if not targets:
        print("  no lots have Best Offer terms to restore")
        return 0
    if not confirm:
        print(f"  would restore Best Offer on up to {len(targets)} listing(s) "
              f"(still-unsold Buy It Now only); re-run with --confirm")
        for l in targets:
            print(f"    lot {l['lot']} {l['item_id']} {l['title'][:50]!r}")
        return 0

    load_credentials, get_offer, api_send, EbayAPIError, writable = _offer_api()
    creds = load_credentials(store=store)
    done = 0
    for l in targets:
        try:
            cur = get_offer(l["offer_id"], creds=creds)
            listing = cur.get("listing") or {}
            status = listing.get("listingStatus")
            if status != "ACTIVE":
                print(f"    [done] lot {l['lot']} {l['item_id']}: {status}, nothing to restore")
                l.pop("best_offer_was")
                continue
            if cur.get("format") != "FIXED_PRICE" or listing.get("listingId") != l["item_id"]:
                print(f"    [wait] lot {l['lot']} {l['item_id']}: still "
                      f"{cur.get('format')} {listing.get('listingId')}; try again later")
                continue
            api_send("PUT", f"/sell/inventory/v1/offer/{l['offer_id']}",
                     LS.offer_with_best_offer(cur, writable, l["best_offer_was"]),
                     creds=creds)
        except EbayAPIError as exc:
            print(f"    [X] lot {l['lot']} {l['item_id']}: {exc}")
            continue
        l.pop("best_offer_was")
        done += 1
        print(f"    [OK] lot {l['lot']} {l['item_id']}: Best Offer back on")
    print(f"  Best Offer restored on {done}/{len(targets)} listing(s)")
    return done


def overlap_warnings(store: str, show: str) -> list[str]:
    """Warnings for other stores' shows whose shopping windows overlap this
    show's saved one. Each store's pick list holds only its own orders."""
    me = (show, store)
    return [f"[!] overlaps eBay Live "
            f"{LS.describe_window(b if (a['show'], a['store']) == me else a)}"
            f"; each store's pick list holds only its own show's orders"
            for a, b in LS.cross_store_overlaps(LS.open_windows(None))
            if me in ((a["show"], a["store"]), (b["show"], b["store"]))]


def build(store: str, show: str, entries, missing, *, append: bool = False,
          keep_flagged: bool = False, start: datetime | None = None,
          confirm: bool = False, clear_start: bool = False) -> tuple[Path, list[dict]]:
    """Number the lots, fix what can be fixed (Best Offer, with `confirm`),
    save lineup.json and item_ids.txt, and print the report. Shared with
    tools/live_prep.py. Without `start` the saved start is kept, unless
    `clear_start`."""
    existing = LS.load_lineup(show)
    if existing and existing.get("store") != store:
        raise SystemExit(f"[X] show {show!r} was built for store "
                         f"{existing.get('store')!r}, not {store!r}")
    new = LS.build_lineup(entries)
    if existing and append:
        lots = LS.merge_lineup(existing["lots"], new)
    else:
        lots = new
        if existing:
            orphans = LS.carry_restores(existing["lots"], lots)
            if orphans:
                raise SystemExit(
                    f"[X] show {show!r} turned Best Offer off on {len(orphans)} listing(s) "
                    f"this lineup drops ({', '.join(l['item_id'] for l in orphans)}). "
                    f"Run --restore-best-offer --confirm first, or use --append.")
            print(f"  replacing the existing {len(existing['lots'])}-lot lineup "
                  f"(use --append to add instead)")
    if len(lots) > LS.MAX_LOTS:
        print(f"[!] {len(lots)} lots — Stream manager takes {LS.MAX_LOTS} per event")
    if start is None and not clear_start and existing and existing.get("scheduled_start"):
        start = parse_start(existing["scheduled_start"])

    clear_best_offer(store, lots, confirm=confirm)
    d = LS.save_lineup(show, store, lots, keep_flagged=keep_flagged,
                       scheduled_start=start)
    ids = LS.eligible_ids(lots, keep_flagged)
    flagged = [l for l in lots if l["flags"]]
    noted = [l for l in lots if l.get("notes") and not l["flags"]]

    print(f"\nShow {show!r} on store {store!r}: {len(lots)} lots, "
          f"{len(ids)} in item_ids.txt, {len(flagged)} flagged")
    for l in flagged:
        print(f"  [flag] lot {l['lot']} {l['item_id']} {l['title'][:50]!r}: "
              + "; ".join(l["flags"]))
    for l in noted:
        print(f"  [note] lot {l['lot']} {l['item_id']} {l['title'][:50]!r}: "
              + "; ".join(l["notes"]))
    for iid, why in missing:
        print(f"  [skip] {iid}: {why}")
    if start:
        closes = start + LS.SHOPPING_WINDOW
        print(f"\nScheduled start {start.astimezone(EASTERN):%a %b %d %I:%M %p} ET; "
              f"pick lists hold this show's orders until "
              f"{closes.astimezone(EASTERN):%a %I:%M %p} ET.")
        for line in overlap_warnings(store, show):
            print(line)
    elif clear_start:
        print("\nScheduled start cleared: pick lists no longer hold this show's orders.")
    else:
        print("\n[!] no --start: pick lists won't wait out the 8-hour "
              "combined-shipping window for this show")
    print(f"\nPaste into Stream manager -> Add listings -> By Item ID/URL:\n"
          f"  {(d / 'item_ids.txt').resolve()}")
    print(f"Host page:  http://127.0.0.1:8770/live/{show}")
    print(f"OBS source: http://127.0.0.1:8770/live/{show}/overlay")
    return d, lots


def marquee_ids(show: str) -> list[str]:
    """The Item IDs the show's marquee advertises, in its order."""
    p = LS.show_dir(show) / "marquee.json"
    if not p.exists():
        raise SystemExit(f"[X] show {show!r} has no marquee.json — run "
                         f"tools/live_marquee.py --pick, or tools/live_prep.py")
    spec = json.loads(p.read_text(encoding="utf-8"))
    return [i["item_id"] for i in spec.get("items") or [] if i.get("item_id")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    stores.add_store_args(ap)
    ap.add_argument("--show", required=True, help="show name (folder under inventory/_live/)")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--items", nargs="+", metavar="ITEM_ID", help="Item IDs, in show order")
    pick.add_argument("--all", action="store_true", help="every active listing on the store")
    pick.add_argument("--from-marquee", action="store_true",
                      help="the items in the show's marquee.json")
    pick.add_argument("--restore-best-offer", action="store_true",
                      help="after the show: turn Best Offer back on for unsold lots "
                           "prep turned it off on")
    ap.add_argument("--append", action="store_true",
                    help="add to an existing lineup (mid-show) instead of replacing it")
    ap.add_argument("--keep-flagged", action="store_true",
                    help="include flagged lots in item_ids.txt anyway")
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--start", metavar="'YYYY-MM-DD HH:MM'",
                      help="event's scheduled start (Eastern unless it has an offset)")
    when.add_argument("--no-start", action="store_true",
                      help="clear the saved scheduled start, so pick lists stop "
                           "holding this show's orders (alone: changes nothing else)")
    ap.add_argument("--confirm", action="store_true",
                    help="really edit live listings (Best Offer off, or back on "
                         "with --restore-best-offer); without it, a dry run")
    args = ap.parse_args(argv)
    if not (args.items or args.all or args.from_marquee or args.restore_best_offer
            or args.no_start):
        ap.error("one of --items --all --from-marquee --restore-best-offer is "
                 "required (or --no-start on its own)")

    store = stores.resolve_store_name(args.store)
    try:
        LS.validate_show_name(args.show)
        start = parse_start(args.start) if args.start else None
    except ValueError as exc:
        raise SystemExit(f"[X] {exc}")

    if args.no_start and not (args.items or args.all or args.from_marquee
                              or args.restore_best_offer):
        lineup = LS.load_lineup(args.show)
        if not lineup or lineup.get("store") != store:
            raise SystemExit(f"[X] no lineup for show {args.show!r} on store {store!r}")
        LS.set_scheduled_start(args.show, None)
        print(f"Show {args.show!r}: scheduled start cleared (was "
              f"{lineup.get('scheduled_start') or 'not set'}); pick lists no "
              f"longer hold its orders.")
        return 0

    if args.restore_best_offer:
        lineup = LS.load_lineup(args.show)
        if not lineup or lineup.get("store") != store:
            raise SystemExit(f"[X] no lineup for show {args.show!r} on store {store!r}")
        restore_best_offer(store, lineup["lots"], confirm=args.confirm)
        if args.confirm:
            LS.save_lineup(args.show, store, lineup["lots"], keep_flagged=args.keep_flagged,
                           scheduled_start=(parse_start(lineup["scheduled_start"])
                                            if lineup.get("scheduled_start") else None))
        if args.no_start:
            LS.set_scheduled_start(args.show, None)
            print(f"Show {args.show!r}: scheduled start cleared")
        return 0

    ids = marquee_ids(args.show) if args.from_marquee else args.items
    entries, missing = fetch_entries(store, None if args.all else ids)
    build(store, args.show, entries, missing, append=args.append,
          keep_flagged=args.keep_flagged, start=start, confirm=args.confirm,
          clear_start=args.no_start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
