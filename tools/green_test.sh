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
# IPv4 only: green.lan resolves to its IPv6 address first, and green's ufw
# opens SSH to the IPv4 LAN only (tools/green_setup.py).
set -euo pipefail
SSH=(ssh -o AddressFamily=inet)

HOST=green
VENV='~/ebaybiz-venv'
repo=$(git rev-parse --show-toplevel)
dest="ebaybiz-test/$(basename "$repo")"

if [[ ${1:-} == --setup ]]; then
  # The same packages .github/workflows/tests.yml installs.
  "${SSH[@]}" "$HOST" "python3 -m venv $VENV && $VENV/bin/pip install -q --upgrade pip &&
    $VENV/bin/pip install -q pytest numpy pillow pyyaml opencv-python-headless tzdata fastapi httpx"
  exit
fi

"${SSH[@]}" "$HOST" "mkdir -p $dest"
# git's own list, not a .gitignore filter: some fixtures are tracked although
# an ignore rule matches them (tests/fixtures/photos/*.jpg).
git -C "$repo" ls-files --cached --others --exclude-standard | sed 's|^|/|' |
  rsync -a -e "ssh -o AddressFamily=inet" --delete-excluded --include='*/' --include-from=- --exclude='*' -m \
    "$repo/" "$HOST:$dest/"
"${SSH[@]}" "$HOST" "cd $dest && nice $VENV/bin/python -m pytest -q -p no:cacheprovider $(printf '%q ' "$@")"
