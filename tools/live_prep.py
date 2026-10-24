#!/usr/bin/env python3
"""LIVE PREP — find a show's items and make them fit eBay Live's rules.

One command for the whole pre-show listing side:

  1. find    sweep the seller's live listings in one category (Browse API,
             the same filters as tools/live_marquee.py --pick), oldest first
  2. check   read every active offer on the store (Inventory API) and test
             each candidate against eBay Live's listing rules
             (lib/live_show.py live_flags)
  3. choose  keep candidates that comply, or only need Best Offer turned
             off, until --limit; skip the rest (reserve price, Buy It Now on
             an auction, not visible to the Inventory API) and say why
  4. fix     with --confirm, turn Best Offer off on the chosen listings
             (tools/live_lineup.py; the old terms are kept for
             --restore-best-offer after the show)
  5. write   lineup.json + item_ids.txt (the paste list) and marquee.json,
             then render the marquee (tools/live_marquee.py)

Without --confirm, nothing on eBay changes: lots that still have Best Offer
on are listed and left out of item_ids.txt.

    python tools/live_prep.py --store default --show jewelry-oct-20 \\
        --start "2026-10-20 20:00" --limit 25 --min-price 25 --max-price 100 \\
        --skip-seasonal                 # dry run
    python tools/live_prep.py ... --confirm   # turns Best Offer off on the picks

After the show:

    python tools/live_lineup.py --store default --show jewelry-oct-20 \\
        --restore-best-offer --confirm

What stays manual, in Live Studio: create the event, paste item_ids.txt into
Stream manager -> Add listings -> By Item ID/URL, "Change to auction" on the
lots you want auctioned, and a Live shipping policy.

Run from the main checkout (config.yaml and the ledgers live there), or set
EBAYBIZ_CONFIG.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import live_marquee as LM                                              # noqa: E402
import live_show as LS                                                 # noqa: E402
import stores                                                          # noqa: E402

DEFAULT_SELLER = "popsgames"   # the default store's eBay username (live_marquee)


def choose(candidates: list[dict], lots_by_id: dict[str, dict],
           limit: int) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Walk `candidates` (Browse records, in show order) and keep the first
    `limit` whose lot is eligible or fixable. Returns (kept, skipped) where
    skipped is (record, reason) for every candidate passed over before the
    limit was reached."""
    kept, skipped = [], []
    for r in candidates:
        if len(kept) >= limit:
            break
        lot = lots_by_id.get(r["item_id"])
        if lot is None:
            skipped.append((r, "no active offer on this store (made in the eBay "
                               "web UI, or another store?)"))
        elif not LS.fixable(lot):
            skipped.append((r, "; ".join(lot["flags"])))
        else:
            kept.append(r)
    return kept, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    stores.add_store_args(ap)
    ap.add_argument("--show", required=True, help="show name (folder under inventory/_live/)")
    ap.add_argument("--start", metavar="'YYYY-MM-DD HH:MM'",
                    help="event's scheduled start (Eastern unless it has an offset)")
    ap.add_argument("--seller", help=f"eBay username to sweep (default {DEFAULT_SELLER} "
                                     f"on the default store; required on any other)")
    ap.add_argument("--category", default="281",
                    help="category id to sweep (default 281, Jewelry & Watches)")
    ap.add_argument("--limit", type=int, default=25, help="lots in the show (default 25)")
    ap.add_argument("--min-price", type=float, help="skip listings priced under this")
    ap.add_argument("--max-price", type=float, help="skip listings priced over this")
    ap.add_argument("--skip-seasonal", action="store_true",
                    help="skip Christmas/holiday pieces")
    ap.add_argument("--exclude", nargs="+", default=[], metavar="ITEM_ID")
    ap.add_argument("--include", nargs="+", default=[], metavar="ITEM_ID",
                    help="listings outside --category to consider too")
    ap.add_argument("--no-marquee", action="store_true",
                    help="don't touch marquee.json or render the image")
    ap.add_argument("--confirm", action="store_true",
                    help="turn Best Offer off on the chosen listings (edits live "
                         "listings); without it, a dry run")
    args = ap.parse_args(argv)

    import tools.live_lineup as LL
    import tools.live_marquee as TM

    store = stores.resolve_store_name(args.store)
    seller = args.seller or (DEFAULT_SELLER if stores.is_default(store) else None)
    if not seller:
        raise SystemExit(f"[X] pass --seller <eBay username of store {store!r}>")
    try:
        d = LS.show_dir(args.show)
        start = LL.parse_start(args.start) if args.start else None
    except ValueError as exc:
        raise SystemExit(f"[X] {exc}")

    # 1. find
    from ebay_client import api_get, load_credentials
    creds = load_credentials(store=store)
    get = lambda path, **k: api_get(path, creds=creds, marketplace="EBAY_US", **k)  # noqa: E731
    records = TM.sweep(seller, args.category, get)
    seen = {r["item_id"] for r in records}
    records += [TM.fetch_one(i, get) for i in args.include if i not in seen]
    candidates, dropped = LM.select_items(
        records, limit=len(records), min_price=args.min_price,
        max_price=args.max_price, skip_seasonal=args.skip_seasonal,
        exclude=frozenset(args.exclude), include=frozenset(args.include))
    print(f"  find: {len(records)} live listings swept, {len(candidates)} pass the "
          f"filters" + "".join(f", {n} {why}" for why, n in dropped.items() if n))

    # 2. check
    entries, _ = LL.fetch_entries(store, None)
    by_id: dict[str, list] = {}
    for e in entries:
        by_id.setdefault((e[0].get("listing") or {}).get("listingId"), []).append(e)
    lots_by_id = {l["item_id"]: l for l in LS.build_lineup(entries)}

    # 3. choose
    kept, skipped = choose(candidates, lots_by_id, args.limit)
    n_fix = sum(1 for r in kept if lots_by_id[r["item_id"]]["flags"])
    print(f"  check: {len(kept)} chosen ({len(kept) - n_fix} comply, {n_fix} need "
          f"Best Offer off), {len(skipped)} skipped")
    for r, why in skipped:
        print(f"  [skip] {r['item_id']} {r['title'][:50]!r}: {why}")
    if len(kept) < args.limit:
        print(f"[!] only {len(kept)} of --limit {args.limit} found; widen the filters")
    if not kept:
        return 1

    # 4 + 5. fix and write the lineup (live_lineup does both)
    chosen = [e for r in kept for e in by_id[r["item_id"]]]
    LL.build(store, args.show, chosen, [], start=start, confirm=args.confirm)

    if args.no_marquee:
        return 0
    spec_path = d / "marquee.json"
    spec = (json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists()
            else LM.default_spec(args.show))
    LM.set_items(spec, kept)
    if start:
        spec["start"] = start.isoformat()
    LS._atomic_write(spec_path, json.dumps(spec, indent=1) + "\n")
    print("\n  marquee:")
    return TM.main(["--show", args.show, "--store", store])


if __name__ == "__main__":
    sys.exit(main())
