#!/usr/bin/env python3
"""Buy a shipping label via EasyPost (GH #80).

DRY RUN BY DEFAULT. Buying postage spends real money — this makes NO call
to EasyPost's purchase endpoint unless --confirm is given explicitly. Same
gate, same style, as `lib/list_edit.py --publish/--end` (never inferred,
never something to run from an unattended poll loop). Get shipment_id +
rate_id from `ship-quote` first.

    python -m lib.cli ship-buy --shipment-id shp_... --rate-id rate_...           # DRY RUN
    python -m lib.cli ship-buy --shipment-id shp_... --rate-id rate_... --confirm  # buys it

On a real (confirmed) purchase this prints the carrier, tracking number,
and label URL, plus the exact `pick_list.py --record-tracking` call to
feed the tracking number into the local ledger's SHIPPED transition (#70)
— that ledger write is NOT done here; #70 owns it, and has not landed on
`main` as of this writing. Composing with it once it lands needs nothing
more than running the printed command.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from config import ConfigError                                        # noqa: E402
from easypost_client import EasyPostAPIError, EasyPostAuthError, Rate, buy_label  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shipment-id", required=True, help="from `ship-quote`'s output")
    ap.add_argument("--rate-id", required=True, help="from `ship-quote`'s output")
    ap.add_argument("--carrier", default="", help="carrier, for the printed summary (cosmetic)")
    ap.add_argument("--service", default="", help="service, for the printed summary (cosmetic)")
    ap.add_argument("--price", type=float, default=None,
                    help="expected price from `ship-quote`, for the DRY RUN summary (cosmetic — "
                         "a confirmed purchase always charges EasyPost's live price for --rate-id, "
                         "not this value)")
    ap.add_argument("--order-id", default=None,
                    help="eBay order ID this label is for — used only in the printed "
                         "--record-tracking follow-up command, never sent to EasyPost")
    ap.add_argument("--confirm", action="store_true",
                    help="required to actually buy the label. Without it: DRY RUN — no money "
                         "spent, no call made to EasyPost's purchase endpoint.")
    args = ap.parse_args()

    # The rate object buy_label() needs for a DRY RUN print is exactly what
    # ship-quote already showed the operator; a confirmed purchase re-quotes
    # nothing and just tells EasyPost which shipment_id/rate_id to buy.
    rate = Rate(id=args.rate_id, carrier=args.carrier, service=args.service,
               rate=args.price if args.price is not None else 0.0, currency="USD",
               delivery_days=None, shipment_id=args.shipment_id)

    try:
        result = buy_label(args.shipment_id, rate, confirm=args.confirm)
    except ConfigError as e:
        print(f"[X] {e}", file=sys.stderr)
        return 1
    except (EasyPostAuthError, EasyPostAPIError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if result.dry_run:
        print("[DRY RUN] Nothing bought. This WOULD purchase:")
        print(f"  shipment       {result.shipment_id}")
        label = f"{result.carrier} {result.service}".strip() or "(pass --carrier/--service to show)"
        print(f"  rate           {result.rate_id}  ({label})")
        cost = f"${result.price:.2f} {result.currency}" if args.price is not None else \
              "(pass --price from `ship-quote` to preview the cost here)"
        print(f"  cost           {cost}")
        print()
        print("  To actually buy this label: re-run with --confirm")
        return 0

    print("[OK] Label purchased — money was spent:")
    print(f"  carrier        {result.carrier} {result.service}")
    print(f"  cost           ${result.price:.2f} {result.currency}")
    print(f"  tracking code  {result.tracking_code}")
    print(f"  label url      {result.label_url}")
    order_id = args.order_id or "ORDER_ID"
    print()
    print("  Feed this into the local ledger's SHIPPED transition once #70 lands:")
    print(f"    python -m lib.cli pick-list --record-tracking {order_id} "
         f"--carrier {result.carrier!r} --tracking-number {result.tracking_code} --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
