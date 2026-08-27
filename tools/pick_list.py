#!/usr/bin/env python3
"""Pick list for orders that still have to be packed.

Reads eBay's Fulfillment API and prints what a person carries to the shelves:
what to pull, where it lives locally, where it is going, and by when.

By default it lists only orders awaiting shipment — the filter eBay honours is
a single `orderfulfillmentstatus` with both open states in one brace group;
asking for one state at a time is an HTTP 400. When nothing is waiting there is
nothing to pick, so --latest N renders the most recent already-shipped orders
instead, which is the only way to see the format when the queue is empty.

Buyer names and street addresses are in this output. It prints to the terminal
and, with --out, to a local file; it is never written anywhere that leaves the
machine.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from ebay_client import api_send                                    # noqa: E402
from sync_actuals import fetch_orders, load_listings_ledger, match_sale, scan_drafts  # noqa: E402

OPEN_FILTER = "orderfulfillmentstatus:%7BNOT_STARTED%7CIN_PROGRESS%7D"


def _money(m: dict | None) -> str:
    return f"${float((m or {}).get('value', 0)):,.2f}"


def fetch_open() -> list[dict]:
    orders, offset = [], 0
    while True:
        d = api_send("GET", f"/sell/fulfillment/v1/order?limit=50&offset={offset}"
                            f"&filter={OPEN_FILTER}", creds=None, marketplace=None)
        batch = d.get("orders") or []
        orders.extend(batch)
        offset += 50
        if offset >= (d.get("total") or 0) or not batch:
            return orders


def ship_to(o: dict) -> dict:
    for f in o.get("fulfillmentStartInstructions") or []:
        step = f.get("shippingStep") or {}
        if step.get("shipTo"):
            return {**step["shipTo"], "carrier": step.get("shippingCarrierCode", ""),
                    "service": step.get("shippingServiceCode", ""),
                    "by": (f.get("maxEstimatedDeliveryDate") or "")[:10]}
    return {}


def render(o: dict, drafts: list[dict], ledger: list[dict]) -> str:
    to = ship_to(o)
    addr = to.get("contactAddress") or {}
    items = o.get("lineItems") or []
    ship_by = min((li.get("lineItemFulfillmentInstructions", {}).get("shipByDate") or "zz"
                   for li in items), default="")[:10]

    L = []
    L.append("=" * 66)
    L.append(f"PICK  order {o.get('orderId','')}   sales record #{o.get('salesRecordReference','')}"
             f"   {o.get('creationDate','')[:10]}")
    L.append(f"      status {o.get('orderFulfillmentStatus','')} / {o.get('orderPaymentStatus','')}"
             + (f"   SHIP BY {ship_by}" if ship_by and ship_by != "zz" else ""))
    L.append("-" * 66)
    for n, li in enumerate(items, 1):
        row = {"sku": li.get("sku") or "", "listing_id": li.get("legacyItemId", ""),
               "title": li.get("title", "")}
        folder, ask, how = match_sale(row, drafts, ledger)
        L.append(f"  [{n}] x{li.get('quantity',1)}  {li.get('title','')}")
        L.append(f"       item {li.get('legacyItemId','')}"
                 + (f"   sku {row['sku']}" if row["sku"] else "   sku —")
                 + f"   {_money(li.get('lineItemCost'))}")
        L.append(f"       FROM  {folder or '(no local folder — listed by hand)'}"
                 + (f"   [{how}]" if folder else ""))
    L.append("-" * 66)
    L.append(f"  SHIP TO   {to.get('fullName','')}")
    for line in (addr.get("addressLine1"), addr.get("addressLine2")):
        if line:
            L.append(f"            {line}")
    L.append(f"            {addr.get('city','')}, {addr.get('stateOrProvince','')} "
             f"{addr.get('postalCode','')} {addr.get('countryCode','')}")
    L.append(f"  VIA       {to.get('carrier','')} {to.get('service','')}"
             f"   buyer paid {_money((o.get('pricingSummary') or {}).get('deliveryCost'))} shipping")
    L.append(f"  ORDER     {_money((o.get('pricingSummary') or {}).get('total'))} total"
             f"   ·   you keep {_money((o.get('paymentSummary') or {}).get('totalDueSeller'))}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latest", type=int, metavar="N",
                    help="ignore the queue; render the N most recent orders (format check)")
    ap.add_argument("--days", type=int, default=30, help="window for --latest (default 30)")
    ap.add_argument("--order-id", help="render one specific order")
    ap.add_argument("--out", metavar="FILE", help="also write to a local file")
    args = ap.parse_args()

    if args.latest or args.order_id:
        orders = sorted(fetch_orders(args.days, verbose=False),
                        key=lambda o: o.get("creationDate", ""), reverse=True)
        if args.order_id:
            orders = [o for o in orders if args.order_id in
                      (o.get("orderId", ""), o.get("legacyOrderId", ""))]
            if not orders:
                print(f"no order {args.order_id} in the last {args.days} days")
                return 1
        else:
            orders = orders[:args.latest]
        header = f"(not the queue — {len(orders)} most recent order(s), already shipped)"
    else:
        orders = fetch_open()
        header = f"AWAITING SHIPMENT — {len(orders)} order(s)"
        if not orders:
            print("Nothing to pick — no orders awaiting shipment.\n"
                  "Use --latest 1 to see the format against a recent order.")
            return 0

    drafts, ledger = scan_drafts(), load_listings_ledger()
    out = "\n".join([header] + [render(o, drafts, ledger) for o in orders] + ["=" * 66])
    print(out)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        print(f"\n[OK] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
