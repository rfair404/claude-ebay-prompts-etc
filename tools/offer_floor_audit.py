#!/usr/bin/env python3
"""offer_floor_audit — does each live listing's Best Offer floor match its price file?

PRICE writes a justified band per shoot (Conservative / Recommended / Push-high)
and the listing strategy line names an auto-decline, almost always the
Recommended tier. eBay enforces that only if the offer actually carries
`bestOfferTerms.autoDeclinePrice`. When it does not, every offer above zero
reaches a human, and the ones that get accepted land under the floor.

The dashboard already shows the damage after the fact: 11 tracked sales closed
below their own Conservative floor, $309 under in total, the Burberry scarf at
$99 against a $225 floor. This tool looks at the LIVE listings instead, so the
next one can be caught before it sells.

Three findings, worst first:

  NO FLOOR      Best Offer is on and no autoDeclinePrice is set — anything can
                be accepted by hand, and the price file's floor is advisory.
  BELOW FLOOR   autoDeclinePrice sits under the shoot's Conservative tier.
  UNDER REC     autoDeclinePrice is between Conservative and Recommended — legal
                but softer than the price file asked for.

    python tools/offer_floor_audit.py                 # report
    python tools/offer_floor_audit.py --csv out.csv

It only READS. Repairing an offer is a PUT against the Sell Inventory API and is
deliberately not automated here — see the note the report prints.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import price_vs_actual as pva                                     # noqa: E402

SKU_RE = re.compile(r'^\s*ebay_inventory_sku:\s*"?([^"\n]+)"?', re.M)


def shoot_by_sku() -> dict:
    """sku -> shoot directory, read from every draft.md on disk.

    Drafts are the only place the SKU and the shoot meet. A shoot can own
    several drafts (item-1/ … item-5/), and each draft has its own SKU, so this
    maps per draft rather than per shoot.
    """
    out = {}
    for d in (REPO / "inventory").rglob("draft.md"):
        m = SKU_RE.search(d.read_text(encoding="utf-8", errors="replace"))
        if m:
            out[m.group(1).strip()] = d.parent
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv")
    ap.add_argument("--limit", type=int, default=0, help="stop after N offers (debug)")
    a = ap.parse_args()

    from ebay_client import get_offers_for_sku

    sheet = []
    with (REPO / "inventory_sheet.csv").open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if (r.get("live") or "").lower() == "yes":
                sheet.append(r)

    by_sku = shoot_by_sku()
    rows, checked, missing_band = [], 0, 0
    for r in sheet:
        sku = r.get("sku") or ""
        shoot = by_sku.get(sku)
        if not shoot:
            continue
        band = pva.read_price(shoot)
        if not band or not band.get("floor"):
            missing_band += 1
            continue
        try:
            offers = get_offers_for_sku(sku)
        except Exception as e:                                    # noqa: BLE001
            print(f"  ! {sku}: {str(e)[:90]}")
            continue
        for o in offers or []:
            if o.get("status") != "PUBLISHED":
                continue
            checked += 1
            terms = (o.get("listingPolicies") or {}).get("bestOfferTerms") or {}
            enabled = bool(terms.get("bestOfferEnabled"))
            decline = terms.get("autoDeclinePrice") or {}
            dv = pva._f(decline.get("value")) if decline else None
            price = pva._f(((o.get("pricingSummary") or {}).get("price") or {}).get("value"))
            floor, rec = band["floor"], band.get("recommended")
            if not enabled:
                verdict = "no best offer"
            elif dv is None:
                verdict = "NO FLOOR"
            elif dv < floor:
                verdict = "BELOW FLOOR"
            elif rec and dv < rec:
                verdict = "under rec"
            else:
                verdict = "ok"
            rows.append({
                "verdict": verdict, "sku": sku,
                "shoot": shoot.relative_to(REPO).as_posix().replace("inventory/", ""),
                "listing_id": r.get("listing_id", ""), "title": (r.get("title") or "")[:44],
                "ask": price, "auto_decline": dv, "floor": floor, "rec": rec,
            })
        if a.limit and checked >= a.limit:
            break

    order = {"NO FLOOR": 0, "BELOW FLOOR": 1, "under rec": 2, "no best offer": 3, "ok": 4}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), -(r["ask"] or 0)))

    def m(v):
        return f"{v:,.0f}" if v else "—"

    print(f"{'verdict':<13} {'ask':>7} {'decline':>8} {'floor':>7} {'rec':>7}  item")
    print("-" * 104)
    for r in rows:
        if r["verdict"] == "ok":
            continue
        print(f"{r['verdict']:<13} {m(r['ask']):>7} {m(r['auto_decline']):>8} "
              f"{m(r['floor']):>7} {m(r['rec']):>7}  {r['title']}")
    n = len(rows)
    c = {k: sum(1 for r in rows if r["verdict"] == k) for k in order}
    print("-" * 104)
    print(f"{n} published offers matched to a price band "
          f"({missing_band} live listings have no banded price.txt)")
    for k in ("NO FLOOR", "BELOW FLOOR", "under rec", "no best offer", "ok"):
        print(f"  {k:<14} {c.get(k, 0)}")
    print("\nRepair is a PUT on the offer (listingPolicies.bestOfferTerms."
          "autoDeclinePrice)\nand is not automated here — it changes a live listing's terms.")

    if a.csv and rows:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"[OK] wrote {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
