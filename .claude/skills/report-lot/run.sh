#!/usr/bin/env bash
# Full account of one lot: item ledger + summary, written to reports/lots/
# and (only with --print, when Russell asks) printed on CannonBW. Runs the main checkout's tool and
# venv, where the ledgers and config.yaml live; the work is in
# tools/report_lot.py.
set -uo pipefail

MAIN=$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')
cd "$MAIN" && exec "$MAIN/.venv/bin/python" "$MAIN/tools/report_lot.py" "$@"
