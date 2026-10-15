#!/usr/bin/env python3
"""LIVE LINEUP — build an eBay Live show's lineup from our own listings.

eBay Live has no API; Stream manager's "Add listings -> By Item ID/URL" takes
pasted Item IDs. This reads the store's offers (Inventory API, read-only),
checks each against the rules eBay documents for Live (lib/live_show.py
live_flags), and writes inventory/_live/<show>/:

  lineup.json    every requested lot, in order, with its flags
  item_ids.txt   the unflagged Item IDs, one per line — paste these

It never changes a listing. A flagged lot (Best Offer on, reserve set, not
active) is left out of item_ids.txt unless --keep-flagged; fixing the
listing is a separate, deliberate edit.

    python tools/live_lineup.py --store junk --show oct-20 --items 880009332259 ...
    python tools/live_lineup.py --store junk --show oct-20 --all
    python tools/live_lineup.py --store junk --show oct-20 --append --items <new id>

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
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    stores.add_store_args(ap)
    ap.add_argument("--show", required=True, help="show name (folder under inventory/_live/)")
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--items", nargs="+", metavar="ITEM_ID", help="Item IDs, in show order")
    pick.add_argument("--all", action="store_true", help="every active listing on the store")
    ap.add_argument("--append", action="store_true",
                    help="add to an existing lineup (mid-show) instead of replacing it")
    ap.add_argument("--keep-flagged", action="store_true",
                    help="include flagged lots in item_ids.txt anyway")
    args = ap.parse_args(argv)

    store = stores.resolve_store_name(args.store)
    try:
        LS.validate_show_name(args.show)
    except ValueError as exc:
        raise SystemExit(f"[X] {exc}")

    existing = LS.load_lineup(args.show)
    if existing and existing.get("store") != store:
        raise SystemExit(f"[X] show {args.show!r} was built for store "
                         f"{existing.get('store')!r}, not {store!r}")
    if existing and not args.append:
        print(f"  replacing the existing {len(existing['lots'])}-lot lineup "
              f"(use --append to add instead)")

    entries, missing = fetch_entries(store, None if args.all else args.items)
    new = LS.build_lineup(entries)
    lots = LS.merge_lineup(existing["lots"], new) if (existing and args.append) else new
    if len(lots) > LS.MAX_LOTS:
        print(f"[!] {len(lots)} lots — Stream manager takes {LS.MAX_LOTS} per event")

    d = LS.save_lineup(args.show, store, lots, keep_flagged=args.keep_flagged)
    ids = LS.eligible_ids(lots, args.keep_flagged)
    flagged = [l for l in lots if l["flags"]]

    print(f"\nShow {args.show!r} on store {store!r}: {len(lots)} lots, "
          f"{len(ids)} in item_ids.txt, {len(flagged)} flagged")
    for l in flagged:
        print(f"  [flag] lot {l['lot']} {l['item_id']} {l['title'][:50]!r}: "
              + "; ".join(l["flags"]))
    for iid, why in missing:
        print(f"  [skip] {iid}: {why}")
    print(f"\nPaste into Stream manager -> Add listings -> By Item ID/URL:\n"
          f"  {(d / 'item_ids.txt').resolve()}")
    print(f"Host page:  http://127.0.0.1:8770/live/{args.show}")
    print(f"OBS source: http://127.0.0.1:8770/live/{args.show}/overlay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
