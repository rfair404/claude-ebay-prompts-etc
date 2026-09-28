#!/usr/bin/env python3
"""Re-run eBay's user-consent flow and store the new refresh token. One command.

    python tools/ebay_reauth.py                       # from the main checkout
    python tools/ebay_reauth.py --config PATH         # any other config.yaml
    python tools/ebay_reauth.py --store junk          # re-auth a NAMED store (GH #147)

Why this exists instead of `ebay_client.py --user-consent-url` / `--exchange-code`:

- The code eBay puts in the redirect URL is URL-encoded (`v%5E1.1%23i%5E1...`).
  Passed through as-is, the exchange fails. It also holds `^` and `#`, which
  shells mangle when it's pasted as an argument. Here it's read with input(),
  and you can paste the WHOLE address-bar URL; the code is pulled out and decoded.
- The code expires in about 5 minutes, so there's no manual copy/edit step.
- `--exchange-code` prints the raw `user_refresh_token:` line to paste in
  yourself; this writes it directly into the right block — the default
  store's active-environment block (`ebay: <env>: user_refresh_token:`), or
  a named store's flat block (`ebay: stores: <name>: user_refresh_token:`,
  see lib/ebay_client.py's load_credentials()) — after a backup.
- The token is never printed.

Afterwards it refreshes with the full scope set (incl. sell.finances, #126) to
prove the new token carries it.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))


def extract_code(pasted: str) -> str:
    """The authorization code from a pasted redirect URL or a bare code."""
    s = pasted.strip().strip('"').strip("'")
    if "code=" in s:
        query = urllib.parse.urlparse(s).query or s.split("?", 1)[-1]
        code = urllib.parse.parse_qs(query).get("code", [""])[0]   # parse_qs decodes
    elif s.lower().startswith("http") or "?" in s:
        code = ""            # a URL with no code= in it: consent was declined or failed
    else:
        code = urllib.parse.unquote(s)
    return code.strip()


def set_refresh_token(text: str, token: str, *, store: str = "default", env: str = "sandbox") -> str:
    """config.yaml text with the right `user_refresh_token:` line replaced.
    Only that one line changes; comments, order and line endings are kept.

    store="default": the path is `ebay: <env>: user_refresh_token:` — one
        keyset per environment, unchanged since before GH #147.
    Any other store: the path is `ebay: stores: <store>: user_refresh_token:`
        — flat, no further environment sub-nesting (a named store already
        picks one real account; see lib/ebay_client.py's
        load_credentials() docstring for why).

    Walks the YAML by indentation (2 spaces/level) tracking which key is
    open at each level, rather than assuming a fixed depth — the two
    shapes above nest to different depths under the same `ebay:` root.
    """
    target = ["ebay", env] if store == "default" else ["ebay", "stores", store]
    lines = text.splitlines(keepends=True)
    stack: list[str] = []
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^( *)(\S[^:]*):", line)
        if not m or len(m.group(1)) % 2:
            continue                                          # not a `key:` line we track
        indent, key = m.group(1), m.group(2)
        level = len(indent) // 2
        stack = stack[:level] + [key]
        if stack[:-1] == target and key == "user_refresh_token":
            nl = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f'{indent}user_refresh_token: "{token}"{nl}'
            return "".join(lines)
    path = ":".join(target)
    raise ValueError(f"no `{path}:user_refresh_token:` line found")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config.yaml to update (default: the one the pipeline reads)")
    ap.add_argument("--store", metavar="NAME", default=None,
                    help="Re-auth a NAMED store (GH #147) — e.g. 'junk' under "
                         "ebay.stores.<NAME> in config.yaml. Omit for the "
                         "default/primary store.")
    a = ap.parse_args()
    if a.config:
        os.environ["EBAYBIZ_CONFIG"] = str(Path(a.config).expanduser().resolve())

    import ebay_client as ec                                          # after EBAYBIZ_CONFIG
    from config import config_path, load_config

    cfg = config_path()
    creds = ec.load_credentials(a.store)
    print(f"config:      {cfg}")
    print(f"store:       {creds.store}")
    print(f"environment: {creds.environment}")
    print()
    url = ec.user_consent_url(creds)
    print("1. Opening eBay's consent page in your browser. If it doesn't open, copy this URL:")
    print()
    print(url)
    print()
    try:
        webbrowser.open(url)
    except Exception:                                                 # noqa: BLE001
        pass
    print("2. Sign in and click 'Agree'. You'll land on a page that may say it can't be")
    print("   reached, or show an error. That's fine: the code is in its address bar.")
    print("3. Copy the WHOLE address-bar URL and paste it here within ~5 minutes.")
    print()
    code = extract_code(input("Paste the redirect URL (or just the code): "))
    if not code:
        print("[X] no code found in what was pasted (look for `code=` in the URL)")
        return 1

    try:
        tokens = ec.exchange_authorization_code(code, creds)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[X] eBay refused the code (HTTP {e.code}): {body}")
        print("    invalid_grant usually means the code expired or was already used:")
        print("    run this again and paste within a couple of minutes.")
        return 1

    rt = tokens.get("refresh_token")
    if not rt:
        print(f"[X] eBay's response had no refresh_token (keys: {sorted(tokens)})")
        return 1
    try:
        new_text = set_refresh_token(cfg.read_bytes().decode("utf-8"),  # keep CRLF
                                     rt, store=creds.store, env=creds.environment)
    except ValueError as e:
        print(f"[X] {e} in {cfg}; nothing written")
        return 1
    backup = cfg.with_name(f"config.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    shutil.copyfile(cfg, backup)
    cfg.write_text(new_text, encoding="utf-8", newline="")
    block = creds.environment if creds.store == "default" else f"stores.{creds.store}"
    print(f"[OK] refresh token written to {cfg.name} ({block} block); "
          f"previous config backed up to {backup.name}")

    # Prove the new token carries the full scope set, sell.finances included.
    load_config(reload=True)            # config.py caches the pre-write file
    ec.reset_token_cache(creds.store)   # drop whatever was cached under the old token
    ec.get_user_access_token(ec.load_credentials(creds.store), force_refresh=True)
    if not ec.has_full_user_scopes(creds.store, creds.environment):
        print("[!] the new token works, but eBay did NOT grant sell.finances.")
        return 1
    print("[OK] sell.finances is granted: the full scope set refreshes cleanly")
    print()
    print("Next, from the main checkout:")
    print("    python lib/sync_actuals.py --days 730 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
