#!/usr/bin/env bash
# Install and start the daily R2 push timer as a systemd user timer.
# Dry run by default; pass --apply to copy, reload and enable.
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
dst="$HOME/.config/systemd/user"
units=(ebaybiz-offsite.service ebaybiz-offsite-notify.service ebaybiz-offsite.timer)
if [[ "${1:-}" != "--apply" ]]; then
  echo "dry run: would copy ${units[*]} to $dst and enable ebaybiz-offsite.timer"
  echo "re-run with --apply"; exit 0
fi
mkdir -p "$dst"
for u in "${units[@]}"; do cp "$src/$u" "$dst/$u"; done
systemctl --user daemon-reload
systemctl --user enable --now ebaybiz-offsite.timer
systemctl --user list-timers ebaybiz-offsite.timer
