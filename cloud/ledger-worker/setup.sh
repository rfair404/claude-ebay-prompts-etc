#!/usr/bin/env bash
# One-time Cloudflare setup for the ledger Worker (GH #380). Russell runs it
# from the main checkout after the PR merges:
#
#   bash cloud/ledger-worker/setup.sh --phase 1
#
# Phase 1: log in, create the R2 bucket, expire history/ after 90 days, deploy
# the Worker, set a fresh LEDGER_TOKEN secret, write `ledger_store:` into
# config.yaml (gitignored), run `ledger check`, log out. Re-running it rotates
# the token. The token is never printed and never written anywhere in git.
#
# Commands per developers.cloudflare.com/workers/wrangler/commands/ (r2,
# workers, general). Override the binary with WRANGLER="wrangler" if it is
# installed globally.
set -euo pipefail

PHASE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --phase) PHASE="${2:-}"; shift 2 ;;
    --phase=*) PHASE="${1#--phase=}"; shift ;;
    *) echo "usage: $0 --phase 1" >&2; exit 2 ;;
  esac
done
if [ "$PHASE" != "1" ]; then
  echo "only --phase 1 exists so far (phases 2 and 3 come with their PRs)" >&2
  exit 2
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUCKET="ebaybiz-ledgers"
read -r -a W <<< "${WRANGLER:-npx wrangler}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$REPO/.venv/bin/python" ]; then PY="$REPO/.venv/bin/python"; else PY="python3"; fi
fi

cd "$HERE"

"${W[@]}" login
trap '"${W[@]}" logout || true' EXIT     # only Russell can redeploy or change the list

echo "== R2 bucket $BUCKET"
if ! "${W[@]}" r2 bucket create "$BUCKET"; then
  echo "   (create failed — fine if the bucket already exists; continuing)"
fi
"${W[@]}" r2 bucket lifecycle add "$BUCKET" history-90d history/ --expire-days 90 --force

echo "== deploy"
DEPLOY_LOG="$(mktemp)"
"${W[@]}" deploy | tee "$DEPLOY_LOG"
URL="$(grep -oE 'https://[A-Za-z0-9.-]+\.workers\.dev' "$DEPLOY_LOG" | head -n1 || true)"
rm -f "$DEPLOY_LOG"
if [ -z "$URL" ]; then
  echo "[X] couldn't read the workers.dev URL from the deploy output" >&2
  exit 1
fi

echo "== LEDGER_TOKEN (new random token, not shown)"
TOKEN="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s' "$TOKEN" | "${W[@]}" secret put LEDGER_TOKEN

echo "== config.yaml ledger_store:"
LEDGER_URL="$URL" NEW_LEDGER_TOKEN="$TOKEN" "$PY" - "$REPO" <<'PYEOF'
import os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "lib"))
import config
path = config.config_path()
text = path.read_text(encoding="utf-8") if path.exists() else ""
# drop an old top-level ledger_store: block (its lines up to the next top-level key)
text = re.sub(r"(?m)^# Ledger Worker \(GH #380\).*\n", "", text)
text = re.sub(r"(?ms)^ledger_store:.*?(?=^\S|\Z)", "", text).rstrip("\n")
block = ("\n\n# Ledger Worker (GH #380) — written by cloud/ledger-worker/setup.sh\n"
         "ledger_store:\n"
         f"  url: \"{os.environ['LEDGER_URL']}\"\n"
         f"  token: \"{os.environ['NEW_LEDGER_TOKEN']}\"\n")
tmp = path.with_name(path.name + ".tmp-ledger")
tmp.write_text(text + block, encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print(f"   wrote ledger_store: to {path}")
PYEOF
unset TOKEN

echo "== ledger check"
cd "$REPO"
for i in 1 2 3; do
  if "$PY" -m lib.cli ledger check; then exit 0; fi
  echo "   (retrying in 10 s — a new secret can take a moment)"; sleep 10
done
echo "[X] ledger check failed" >&2
exit 1
