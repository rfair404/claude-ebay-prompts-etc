#!/usr/bin/env python3
"""Vinted Pro Integrations — account check and read-only inspection.

    python -m lib.cli vinted check                  # config + a signed call
    python -m lib.cli vinted ontologies --out FILE  # catalog/status/package ids
    python -m lib.cli vinted items [--limit N]
    python -m lib.cli vinted orders
    python -m lib.cli vinted webhooks
    python -m lib.cli --store junk vinted check

`check` is the first thing to run once the Integrations Portal hands out a
token: it reports which store/environment resolved, then makes one signed GET
(/api/v1/webhooks — cheap, no side effects) to prove the key and the HMAC
signature are accepted.

Everything here is read-only. Writes (create/update/delete items, cancel,
webhooks) live in lib/vinted_client.py behind confirm=True and are driven by
the listing pipeline, not typed ad hoc here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import stores                                                         # noqa: E402
import vinted_client as V                                             # noqa: E402
from config import ConfigError                                        # noqa: E402


def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_check(creds: V.VintedCredentials) -> int:
    print(f"store:        {creds.store}")
    print(f"environment:  {creds.environment}  ({creds.base_url})")
    print(f"access_key:   {creds.access_key[:6]}…")
    try:
        hooks = V.list_webhooks(creds=creds)
    except V.VintedAuthError as e:
        print(f"[X] token/signature rejected: {e}")
        print("    Check the token is for THIS environment (sandbox and "
              "production issue separate tokens) and the machine clock is right "
              "— Vinted rejects stale signature timestamps.")
        return 1
    except V.VintedAPIError as e:
        print(f"[X] {e}\n    {e.body or ''}")
        return 1
    n = len(hooks.get("webhooks", hooks) if isinstance(hooks, dict) else hooks or [])
    print(f"[OK] signed request accepted — {n} webhook(s) registered")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ebz vinted", description=__doc__.split("\n\n")[0])
    stores.add_store_args(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="resolve credentials and make one signed call")
    p = sub.add_parser("ontologies", help="catalogs, statuses, colours, package sizes")
    p.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    p = sub.add_parser("items", help="items created through the API")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--after", default=None, help="after_item_id (pagination)")
    p = sub.add_parser("orders", help="orders")
    p.add_argument("--after", type=int, default=None, help="after-id (pagination)")
    sub.add_parser("webhooks", help="registered webhooks")
    args = ap.parse_args(argv)

    try:
        creds = V.load_credentials(args.store)
    except ConfigError as e:
        print(f"[X] {e}")
        return 1

    if args.cmd == "check":
        return cmd_check(creds)
    try:
        if args.cmd == "ontologies":
            data = V.get_ontologies(creds=creds)
            if args.out:
                args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
                print(f"[OK] wrote {args.out}")
                return 0
            _dump(data)
        elif args.cmd == "items":
            _dump(V.list_items(after_item_id=args.after, limit=args.limit, creds=creds))
        elif args.cmd == "orders":
            _dump(V.list_orders(after_id=args.after, creds=creds))
        elif args.cmd == "webhooks":
            _dump(V.list_webhooks(creds=creds))
    except (V.VintedAuthError, V.VintedAPIError) as e:
        print(f"[X] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
