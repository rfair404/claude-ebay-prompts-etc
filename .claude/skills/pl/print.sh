#!/usr/bin/env bash
# Print every pick sheet awaiting shipment, all stores, on CannonBW.
# One sheet per box. Runs the main checkout's tool and venv, where
# pick_lists/ and the poll state live (prompts/pick_list.md step 1); the
# work is in tools/pick_list_links.py --print.
set -uo pipefail

MAIN=$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')
exec "$MAIN/.venv/bin/python" "$MAIN/tools/pick_list_links.py" --print
