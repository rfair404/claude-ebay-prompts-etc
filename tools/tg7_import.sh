#!/usr/bin/env bash
# Runs when the TG-7's card mounts (tools/systemd/tg7-import.service). Pulls
# new photos to ~/Pictures/TG-7 with tg7_pull.sh, copies just the new JPGs into
# a new lot inventory/tg7-<date>-<time>/, and starts IDENTIFY on it headless.
# A mount with nothing new does nothing. Log: inventory/<lot>/import.log.
set -uo pipefail

MAIN=${TG7_MAIN:-$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')}
DEST=${TG7_PHOTOS:-$HOME/Pictures/TG-7}
CLAUDE=${CLAUDE_BIN:-$(command -v claude || ls ~/.config/Claude/claude-code/*/claude 2>/dev/null | sort -V | tail -1)}

out=$(TG7_MAIN=$MAIN TG7_PHOTOS=$DEST "$MAIN/.claude/skills/r/tg7_pull.sh")
echo "$out"
mapfile -t new < <(sed -nE 's/^  (.+\.JPG)$/\1/p' <<<"$out")
[ ${#new[@]} -eq 0 ] && exit 0

LOT=tg7-$(date +%Y%m%d-%H%M)
DIR=$MAIN/inventory/$LOT
mkdir -p "$DIR"
for f in "${new[@]}"; do cp "$DEST/$f" "$DIR/"; done
exec >>"$DIR/import.log" 2>&1
echo "$(date -Is) imported ${#new[@]} photo(s) from $DEST"

notify() { notify-send -a "TG-7" "$1" "$2" 2>/dev/null || true; }
notify "TG-7: ${#new[@]} new photo(s)" "inventory/$LOT — identifying"

if [ -z "$CLAUDE" ]; then
  echo "no claude CLI found; run: identify $LOT"
  notify "TG-7: identify not started" "no claude CLI found"
  exit 1
fi
cd "$MAIN" && "$CLAUDE" -p "identify $LOT" --permission-mode acceptEdits \
  --allowedTools "Read Write Edit Glob Grep WebSearch WebFetch"
rc=$?
echo "$(date -Is) identify exit $rc"
if [ $rc -eq 0 ] && [ -f "$DIR/identify.txt" ]; then
  notify "TG-7: identify done" "inventory/$LOT/identify.txt"
else
  notify "TG-7: identify failed" "see inventory/$LOT/import.log"
fi
exit $rc
