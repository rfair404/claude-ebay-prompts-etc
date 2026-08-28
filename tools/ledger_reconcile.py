#!/usr/bin/env python3
"""Reconcile listings_ledger.csv against the eBay Sell API. eBay wins, always.

THE LEDGER IS A LOG, NOT A RECORD OF TRUTH.

Every writer in this repo appends its own step and nothing ever re-reads what
eBay actually holds: `list_edit.py --record` stamps DRAFTED, `--sync` stamps
SYNCED, `--publish` stamps PUBLISHED. But `--update --fields price` writes a new
price to eBay and touches no ledger row at all, so the moment a live price is
edited the ledger is wrong and stays wrong. That is exactly what happened to
`acda0eb2`: published at $99, marked down to $85 on eBay, ledger still reading
99.0 with nothing anywhere to catch it.

So the rule this file enforces: **the API is truth and the local copy is assumed
stale.** A field that disagrees is not a conflict to weigh up — it is a local
value that missed an update, and it gets overwritten.

With one scope limit, because the rule names "the API" and there is more than
one. The Sell **Inventory** API is authoritative for what is offered: price,
offer id, listing id, url, live/ended. It knows nothing about what SOLD — that
is the **Fulfillment** API, and it reaches us through sales_ledger.csv. So a
SOLD row backed by a real order outranks the Inventory API reading SYNCED. See
the SOLD note below; the first cut of this file got that wrong and would have
erased 42 sales. eBay also omits price on some ended offers, and a value eBay
declines to state is not a correction, so a known local value is never blanked.

The ledger still owns outright the columns eBay has no opinion on (`drafted_at`,
and the row itself for a SKU that was drafted but never synced).

    python tools/ledger_reconcile.py             # show the drift, change nothing
    python tools/ledger_reconcile.py --apply     # rewrite the ledger from eBay
    python tools/ledger_reconcile.py --apply --prune-unknown

A backup is written next to the ledger before any rewrite.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))

from ebay_client import (load_credentials, iter_inventory_items,        # noqa: E402
                         get_offers_for_sku, EbayAPIError)

LEDGER = REPO / "listings_ledger.csv"
SALES = REPO / "sales_ledger.csv"
FIELDS = ["sku", "status", "title", "price", "offer_id", "listing_id", "url",
          "drafted_at", "synced_at", "published_at", "ended_at", "updated_at"]

# Which ledger status a live offer implies. The ledger's vocabulary is coarser
# than eBay's, so this is a mapping and not a copy.
def _status_for(offer: dict) -> str:
    listing = offer.get("listing") or {}
    lstatus = (listing.get("listingStatus") or "").upper()
    ostatus = (offer.get("status") or "").upper()
    if lstatus == "ACTIVE":
        return "PUBLISHED"
    if lstatus in ("ENDED", "COMPLETED"):
        return "ENDED"
    if ostatus == "PUBLISHED":
        return "PUBLISHED"
    return "SYNCED"          # an unpublished offer exists on eBay


# ---------------------------------------------------------------- SOLD
#
# "THE API IS TRUTH" IS TRUE OF THE INVENTORY API, AND SOLD IS NOT ITS FIELD.
#
# The Sell Inventory API describes what is OFFERED, not what was BOUGHT. After a
# sale the inventory item and its offer survive so the item can be relisted, and
# the offer reads back as SYNCED — indistinguishable from a draft that was never
# published. Sale state lives in the Fulfillment API, which is what
# lib/sync_actuals.py reads into sales_ledger.csv.
#
# A first cut of this file mapped offer status straight onto ledger status and
# would have flipped 42 rows from SOLD to SYNCED — every one of them backed by a
# real order id and real money in sales_ledger.csv. That is not reconciliation,
# it is deleting the sales history with an authoritative-sounding justification.
#
# So SOLD is protected when an order corroborates it. The one thing that beats it
# is the listing being ACTIVE again: that is a genuine relist, and PUBLISHED is
# then the current truth.


def _sold_skus() -> set:
    if not SALES.exists():
        return set()
    with SALES.open(encoding="utf-8-sig", newline="") as f:
        return {r["sku"] for r in csv.DictReader(f) if r.get("sku")}


def _protected(row: dict, truth: dict, field: str, sold: set) -> bool:
    """True when the LOCAL value outranks eBay for this field.

    Exactly one case, and it is narrow on purpose: a SOLD row whose sale is
    corroborated by an order in sales_ledger.csv, where eBay is not showing the
    listing as live again. See the SOLD note above.
    """
    if field != "status":
        return False
    if (row.get("status") or "").strip() != "SOLD":
        return False
    # Protected whether or not an order corroborates it. An uncorroborated SOLD
    # is more likely an offline sale the Fulfillment API never saw — the mall
    # case, a direct buyer — than a mistake, and the cost of the two errors is
    # not symmetric: wrongly keeping SOLD is a stale row someone notices, wrongly
    # clearing it silently resurrects a sold item as listable stock. 5da73b50, a
    # $245 14K pendant, sits exactly here. Reported under REVIEW instead.
    return truth["status"] != "PUBLISHED"  # a relist that is ACTIVE does outrank SOLD


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ebay_truth(verbose: bool = True) -> dict:
    """sku -> the row eBay's own data implies. Offers are the authority."""
    creds = load_credentials()
    truth = {}
    skus = [it.get("sku") for it in iter_inventory_items(creds) if it.get("sku")]
    for i, sku in enumerate(skus, 1):
        if verbose and i % 25 == 0:
            print(f"  ...{i}/{len(skus)}")
        try:
            offers = get_offers_for_sku(sku, creds)
        except EbayAPIError as e:
            print(f"  ! {sku}: {e}")
            continue
        if not offers:
            # An inventory item with no offer was never listed. The ledger may
            # legitimately hold it as DRAFTED; say so rather than inventing a row.
            truth[sku] = None
            continue
        # A SKU can carry more than one offer (a CHOICE variation group). The
        # live one is the one that describes reality; fall back to the first.
        off = next((o for o in offers
                    if ((o.get("listing") or {}).get("listingStatus") or "").upper() == "ACTIVE"),
                   offers[0])
        listing = off.get("listing") or {}
        lid = str(listing.get("listingId") or "")
        price = ((off.get("pricingSummary") or {}).get("price") or {}).get("value")
        truth[sku] = {
            "sku": sku,
            "status": _status_for(off),
            "price": str(float(price)) if price is not None else "",
            "offer_id": str(off.get("offerId") or ""),
            "listing_id": lid,
            "url": f"https://www.ebay.com/itm/{lid}" if lid else "",
        }
    return truth


def compute_drift(by_sku: dict, truth: dict, sold: set) -> dict:
    """Diff the ledger against eBay's truth. No I/O, no printing — a pure function
    so the reconciliation logic can be tested without hitting the API.

    Returns a dict with keys: drift, missing, orphan, never_listed, blanked,
    unbacked (lists) and protected (count).
    """
    drift, missing, never_listed, blanked = [], [], [], []

    for sku, t in truth.items():
        row = by_sku.get(sku)
        if t is None:
            if row and row.get("status") not in ("DRAFTED", ""):
                never_listed.append((sku, row.get("status")))
            continue
        if row is None:
            missing.append(t)
            continue
        for k in ("status", "price", "offer_id", "listing_id", "url"):
            was, now = (row.get(k) or "").strip(), (t[k] or "").strip()
            if _protected(row, t, k, sold):
                continue
            if was and not now:
                blanked.append((sku, k, was))     # eBay omits it; keep ours
                continue
            # price is the one field where "99.0" and "99.00" mean the same thing
            if k == "price" and was and now:
                try:
                    if abs(float(was) - float(now)) < 0.005:
                        continue
                except ValueError:
                    pass
            if was != now:
                drift.append((sku, k, was or "(blank)", now or "(blank)"))

    protected = sum(1 for s, r in by_sku.items()
                    if (r.get("status") or "") == "SOLD" and s in sold
                    and truth.get(s) and truth[s]["status"] != "PUBLISHED")
    orphan = [sku for sku in by_sku if sku not in truth]
    unbacked = sorted(s for s, r in by_sku.items()
                      if (r.get("status") or "") == "SOLD" and s not in sold
                      and truth.get(s) and truth[s]["status"] != "PUBLISHED")

    return {"drift": drift, "missing": missing, "orphan": orphan,
            "never_listed": never_listed, "blanked": blanked,
            "unbacked": unbacked, "protected": protected}


def write_report(path: Path, by_sku: dict, result: dict) -> None:
    """Full detail, for when the terse summary says something is flagged."""
    doc = {
        "checked_at": _now(),
        "drift": [{"sku": s, "field": k, "ledger": was, "ebay": now}
                  for s, k, was, now in result["drift"]],
        "missing": [{"sku": t["sku"], "status": t["status"], "price": t["price"],
                     "url": t["url"]} for t in result["missing"]],
        "orphan": [{"sku": s, "status": by_sku[s].get("status", ""),
                    "title": by_sku[s].get("title", "")} for s in result["orphan"]],
        "never_listed": [{"sku": s, "ledger_status": st}
                         for s, st in result["never_listed"]],
        "blanked": [{"sku": s, "field": k, "kept_value": was}
                    for s, k, was in result["blanked"]],
        "unbacked_sold": [{"sku": s, "price": by_sku[s].get("price", ""),
                           "title": by_sku[s].get("title", ""),
                           "url": by_sku[s].get("url", "")}
                          for s in result["unbacked"]],
        "protected_sold": result["protected"],
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the ledger from eBay (default: report only)")
    ap.add_argument("--prune-unknown", action="store_true", dest="prune",
                    help="drop ledger rows for SKUs eBay has no inventory item for "
                         "(default: keep them and flag)")
    a = ap.parse_args()

    if not LEDGER.exists():
        print(f"no ledger at {LEDGER}")
        return 1

    with LEDGER.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    by_sku = {r["sku"]: r for r in rows}

    print("reading eBay ...")
    truth = ebay_truth()
    print()

    sold = _sold_skus()
    result = compute_drift(by_sku, truth, sold)
    drift, missing, orphan = result["drift"], result["missing"], result["orphan"]
    never_listed, blanked, unbacked = (result["never_listed"], result["blanked"],
                                       result["unbacked"])
    protected = result["protected"]

    flagged = (len(drift) + len(missing) + len(orphan) + len(never_listed)
              + len(blanked) + len(unbacked))
    # Always (re)written, flagged or not — otherwise a fixed drift leaves a
    # stale report describing a problem that no longer exists.
    report = LEDGER.with_name("ledger_reconcile_report.json")
    write_report(report, by_sku, result)
    if not flagged:
        print(f"OK — {len(truth)} SKUs checked, ledger matches eBay"
             + (f" ({protected} SOLD row(s) protected)." if protected else "."))
    else:
        print(f"{flagged} flagged across {len(truth)} SKUs checked -> {report.name}")
        for label, items in (("drift", drift), ("missing", missing), ("orphan", orphan),
                             ("no-offer", never_listed), ("kept", blanked),
                             ("review (SOLD, no order)", unbacked)):
            if items:
                print(f"  {label}: {len(items)}")
        if protected:
            print(f"  protected (SOLD kept over eBay): {protected}")

    if not a.apply:
        if drift or missing:
            print("report only — re-run with --apply to rewrite the ledger from eBay")
        return 0

    if not (drift or missing or (orphan and a.prune)):
        print("nothing to write.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = LEDGER.with_name(f"listings_ledger.backup-{stamp}.csv")
    shutil.copyfile(LEDGER, backup)
    print(f"backed up -> {backup.name}")

    now = _now()
    for sku, t in truth.items():
        if t is None:
            continue
        row = by_sku.get(sku)
        if row is None:
            row = {k: "" for k in FIELDS}
            row["sku"] = sku
            by_sku[sku] = row
            rows.append(row)
        changed = False
        for k in ("status", "price", "offer_id", "listing_id", "url"):
            if _protected(row, t, k, sold) or ((row.get(k) or "").strip()
                                               and not (t[k] or "").strip()):
                continue
            if (row.get(k) or "").strip() != (t[k] or "").strip():
                if k == "price" and row.get(k) and t[k]:
                    try:
                        if abs(float(row[k]) - float(t[k])) < 0.005:
                            continue
                    except ValueError:
                        pass
                row[k] = t[k]
                changed = True
        # Timestamps eBay does not carry: fill only if the state is new to us.
        if t["status"] == "PUBLISHED" and not row.get("published_at"):
            row["published_at"] = now
            changed = True
        if t["status"] == "ENDED" and not row.get("ended_at"):
            row["ended_at"] = now
            changed = True
        if changed:
            row["updated_at"] = now

    if a.prune and orphan:
        rows = [r for r in rows if r["sku"] not in set(orphan)]
        print(f"pruned {len(orphan)} row(s) eBay does not know")

    with LEDGER.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"wrote {LEDGER.name} — {len(rows)} rows, eBay treated as truth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
