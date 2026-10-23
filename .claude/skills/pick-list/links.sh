#!/usr/bin/env bash
# Today's pick sheets as R2 links, all stores (prompts/pick_list.md).
# Runs the main checkout's tool and venv, where the poll state lives, the
# same way print.sh does.
set -uo pipefail

MAIN=$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')
exec "$MAIN/.venv/bin/python" "$MAIN/tools/pick_list_links.py"
