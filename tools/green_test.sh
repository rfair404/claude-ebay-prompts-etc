#!/usr/bin/env bash
# Run the test suite on green, the second machine on the LAN, instead of here.
#
#   tools/green_test.sh [pytest args]     # e.g. tests/test_prep.py -x
#   tools/green_test.sh --setup           # once: venv + the CI's test deps on green
#
# A full run peaks near 900 MB, and several sessions running it at once beside
# the desktop app, Firefox and OBS got the app OOM-killed (2026-10-24). This
# copies the checkout it is run from (tracked and untracked, minus ignored
# files) to green:~/ebaybiz-test/<checkout name>/ and runs pytest there.
# `ssh green` comes from ~/.ssh/config (HostName green.lan, key ~/.ssh/green).
set -euo pipefail

HOST=green
VENV='~/ebaybiz-venv'
repo=$(git rev-parse --show-toplevel)
dest="ebaybiz-test/$(basename "$repo")"

if [[ ${1:-} == --setup ]]; then
  # The same packages .github/workflows/tests.yml installs.
  ssh "$HOST" "python3 -m venv $VENV && $VENV/bin/pip install -q --upgrade pip &&
    $VENV/bin/pip install -q pytest numpy pillow pyyaml opencv-python-headless tzdata fastapi httpx"
  exit
fi

ssh "$HOST" "mkdir -p $dest"
rsync -a --delete --exclude=.git --exclude=/inventory --exclude=/reports \
  --filter=':- .gitignore' "$repo/" "$HOST:$dest/"
ssh "$HOST" "cd $dest && nice $VENV/bin/python -m pytest -q -p no:cacheprovider $(printf '%q ' "$@")"
