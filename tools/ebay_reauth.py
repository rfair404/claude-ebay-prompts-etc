#!/usr/bin/env python3
"""Re-run eBay's user-consent flow and store the new refresh token. One command.

    python tools/ebay_reauth.py                       # from the main checkout
    python tools/ebay_reauth.py --config PATH         # any other config.yaml

Why this exists instead of `ebay_client.py --user-consent-url` / `--exchange-code`:

- The code eBay puts in the redirect URL is URL-encoded (`v%5E1.1%23i%5E1...`).
  Passed through as-is, the exchange fails. It also holds `^` and `#`, which
  shells mangle when it's pasted as an argument. Here it's read with input(),
  and you can paste the WHOLE address-bar URL; the code is pulled out and decoded.
- The code expires in about 5 minutes, so there's no manual copy/edit step.
- `--exchange-code` prints `ebay: user_refresh_token:`, but config.yaml keeps
  one keyset per environment (`ebay: production: user_refresh_token:`). This
  writes the right block, after a backup.
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


def set_refresh_token(text: str, env: str, token: str) -> str:
    """config.yaml text with `ebay: <env>: user_refresh_token:` replaced. Only
    that one line changes; comments, order and line endings are kept."""
    lines = text.splitlines(keepends=True)
    in_ebay = in_env = False
    for i, line in enumerate(lines):
        if re.match(r"^\S", line) and not line.lstrip().startswith("#"):   # top-level key
            in_ebay = line.startswith("ebay:")
            in_env = False
            continue
        if in_ebay and re.match(r"^  [^\s#]", line):                         # ebay: child
            in_env = line.strip().startswith(f"{env}:")
            continue
        if in_env and re.match(r"^\s+user_refresh_token\s*:", line):
            indent = re.match(r"^(\s*)", line).group(1)
            nl = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f'{indent}user_refresh_token: "{token}"{nl}'
            return "".join(lines)
    raise ValueError(f"no `ebay: {env}: user_refresh_token:` line found")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config.yaml to update (default: the one the pipeline reads)")
    a = ap.parse_args()
    if a.config:
        os.environ["EBAYBIZ_CONFIG"] = str(Path(a.config).expanduser().resolve())

    import ebay_client as ec                                          # after EBAYBIZ_CONFIG
    from config import config_path, load_config

    cfg = config_path()
    creds = ec.load_credentials()
    print(f"config:      {cfg}")
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
                                     creds.environment, rt)
    except ValueError as e:
        print(f"[X] {e} in {cfg}; nothing written")
        return 1
    backup = cfg.with_name(f"config.backup-{datetime.now():%Y%m%d-%H%M%S}.yaml")
    shutil.copyfile(cfg, backup)
    cfg.write_text(new_text, encoding="utf-8", newline="")
    print(f"[OK] refresh token written to {cfg.name} ({creds.environment} block); "
          f"previous config backed up to {backup.name}")

    # Prove the new token carries the full scope set, sell.finances included.
    load_config(reload=True)            # config.py caches the pre-write file
    ec._user_cache.token = None
    ec._user_scopes = ec.USER_SCOPES_SELL
    ec.get_user_access_token(ec.load_credentials(), force_refresh=True)
    if ec._user_scopes is not ec.USER_SCOPES_SELL:
        print("[!] the new token works, but eBay did NOT grant sell.finances.")
        return 1
    print("[OK] sell.finances is granted: the full scope set refreshes cleanly")
    print()
    print("Next, from the main checkout:")
    print("    python lib/sync_actuals.py --days 730 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
