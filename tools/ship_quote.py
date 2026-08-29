#!/usr/bin/env python3
"""Quote shipping rates via EasyPost (GH #80).

Bypasses eBay's own Logistics API (confirmed a dead end for a small
seller — Limited Release, invitation-only, USPS-only; see #32) and asks
EasyPost for a live rate table across USPS/UPS/FedEx/DHL instead.

Spends nothing. Quoting is never gated — no --confirm exists here because
none is needed; buying (`ship-buy`) is the gated step.

    python -m lib.cli ship-quote \\
        --to-name "Jane Buyer" --to-street1 "1 Main St" --to-city Springfield \\
        --to-state IL --to-zip 62704 \\
        --from-name "My Store" --from-street1 "9 Ship St" --from-city Elgin \\
        --from-state IL --from-zip 60120 \\
        --weight-oz 24 --length-in 10 --width-in 8 --height-in 4

Prints every rate, cheapest first, plus the exact `ship-buy` invocation
(as a DRY RUN) to buy the cheapest one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from config import ConfigError                                       # noqa: E402
from easypost_client import (                                        # noqa: E402
    Address, EasyPostAPIError, EasyPostAuthError, Parcel, get_rates,
)


def _add_address_args(ap: argparse.ArgumentParser, prefix: str, label: str) -> None:
    ap.add_argument(f"--{prefix}-name", required=True, help=f"{label} recipient name")
    ap.add_argument(f"--{prefix}-street1", required=True, help=f"{label} street address")
    ap.add_argument(f"--{prefix}-street2", default=None, help=f"{label} street address line 2")
    ap.add_argument(f"--{prefix}-city", required=True, help=f"{label} city")
    ap.add_argument(f"--{prefix}-state", required=True, help=f"{label} state/province code")
    ap.add_argument(f"--{prefix}-zip", required=True, help=f"{label} postal code")
    ap.add_argument(f"--{prefix}-country", default="US", help=f"{label} country code (default US)")
    ap.add_argument(f"--{prefix}-phone", default=None,
                    help=f"{label} phone (some carrier services require one)")


def _address_from_args(args: argparse.Namespace, prefix: str) -> Address:
    return Address(
        name=getattr(args, f"{prefix}_name"),
        street1=getattr(args, f"{prefix}_street1"),
        street2=getattr(args, f"{prefix}_street2"),
        city=getattr(args, f"{prefix}_city"),
        state=getattr(args, f"{prefix}_state"),
        zip=getattr(args, f"{prefix}_zip"),
        country=getattr(args, f"{prefix}_country"),
        phone=getattr(args, f"{prefix}_phone"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_address_args(ap, "to", "ship-to")
    _add_address_args(ap, "from", "ship-from")
    ap.add_argument("--weight-oz", type=float, required=True, help="parcel weight, ounces")
    ap.add_argument("--length-in", type=float, default=None, help="parcel length, inches")
    ap.add_argument("--width-in", type=float, default=None, help="parcel width, inches")
    ap.add_argument("--height-in", type=float, default=None, help="parcel height, inches")
    ap.add_argument("--json", action="store_true", help="print raw rates as JSON")
    args = ap.parse_args()

    to_addr = _address_from_args(args, "to")
    from_addr = _address_from_args(args, "from")
    parcel = Parcel(weight_oz=args.weight_oz, length_in=args.length_in,
                    width_in=args.width_in, height_in=args.height_in)

    try:
        shipment_id, rates = get_rates(to_addr, from_addr, parcel)
    except ConfigError as e:
        print(f"[X] {e}", file=sys.stderr)
        return 1
    except (EasyPostAuthError, EasyPostAPIError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"shipment_id": shipment_id,
                          "rates": [r.__dict__ for r in rates]}, indent=2))
        return 0

    if not rates:
        print(f"shipment {shipment_id}: no rates returned")
        return 0

    print(f"shipment {shipment_id} — {len(rates)} rate(s), cheapest first:")
    for r in rates:
        days = f"~{r.delivery_days}d" if r.delivery_days is not None else "? d"
        print(f"  [{r.id}]  {r.carrier:<8} {r.service:<24} ${r.rate:>8.2f} {r.currency}  {days}")

    cheapest = rates[0]
    print()
    print("  Nothing has been bought — quoting spends nothing. To buy the cheapest")
    print("  one (still a DRY RUN — add --confirm yourself to actually spend money):")
    print(f"    python -m lib.cli ship-buy --shipment-id {shipment_id} "
         f"--rate-id {cheapest.id} --carrier {cheapest.carrier} "
         f"--service {cheapest.service!r} --price {cheapest.rate:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
