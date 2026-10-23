#!/usr/bin/env bash
# Today's pick sheets as R2 links, all stores (prompts/pick_list.md).
# Runs the tool from this checkout with the main checkout's venv; the tool
# itself polls and stages in the main checkout, where the state lives.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
MAIN=$(git -C "$HERE" worktree list --porcelain | awk 'NR==1{print $2}')
exec "$MAIN/.venv/bin/python" "$HERE/../../../tools/pick_list_links.py"
