#!/usr/bin/env bash
# Runs when the TG-7's card mounts (tools/systemd/tg7-import.service). Pulls
# new photos to ~/Pictures/TG-7 with tg7_pull.sh (USB only: nobody is
# watching, so it never hops the laptop's Wi-Fi), copies just the new JPGs into
# a staging lot inventory/tg7-<date>-<time>/, and starts the inventory-manager
# on it headless to triage it ("Camera import triage" in
# .claude/agents/inventory-manager.md): sort each frame to its item, catalog
# what's new, and IDENTIFY items that now have their own shots. Its only shell
# command is tools/tg7_triage.py, which moves frames and logs them.
# A mount with nothing new does nothing. Log: inventory/<lot>/import.log;
# the outcome is in inventory/<lot>/TRIAGE.txt.
# One-shot hint: if inventory/_consign/tg7_next.txt exists (lines `client: KBD`
# and/or `model: haiku`), this import is a first-pass triage for that
# consignment client on that model (--model), with no IDENTIFY. The import
# renames it tg7_next.used-<lot>.txt, so it applies to one import only.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
MAIN=${TG7_MAIN:-$(git -C "$HERE" worktree list --porcelain | awk 'NR==1{print $2}')}
DEST=${TG7_PHOTOS:-$HOME/Pictures/TG-7}
CLAUDE=${CLAUDE_BIN:-$(command -v claude || ls ~/.config/Claude/claude-code/*/claude 2>/dev/null | sort -V | tail -1)}

out=$(TG7_USB_ONLY=1 TG7_MAIN=$MAIN TG7_PHOTOS=$DEST "$MAIN/.claude/skills/r/tg7_pull.sh")
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
notify "TG-7: ${#new[@]} new photo(s)" "inventory/$LOT — sorting"

if [ -z "$CLAUDE" ]; then
  echo "no claude CLI found; run: triage inventory/$LOT"
  notify "TG-7: sorting not started" "no claude CLI found"
  exit 1
fi
prompt="Camera import triage for inventory/$LOT, per your \"Camera import triage\" section. Unattended: ask nothing; no PRICE, PREP or DRAFT; nothing on eBay."
model=()
HINT=$MAIN/inventory/_consign/tg7_next.txt
if [ -f "$HINT" ]; then
  client=$(sed -nE 's/^client:[[:space:]]*([A-Z0-9]{2,8})[[:space:]]*$/\1/p' "$HINT" | head -1)
  m=$(sed -nE 's/^model:[[:space:]]*([a-z0-9.-]+)[[:space:]]*$/\1/p' "$HINT" | head -1)
  mv "$HINT" "${HINT%.txt}.used-$LOT.txt"
  echo "hint used: client=${client:-none} model=${m:-default}"
  [ -n "$m" ] && model=(--model "$m")
  prompt+=" Hint for this import: first-pass triage only (\"Triage-only hint\" in that section)."
  [ -n "$client" ] && prompt+=" This lot is a consigned bin for client $client: catalog with add --no-folder; consign import waits for an attended run."
fi
cd "$MAIN" && "$CLAUDE" --agent inventory-manager "${model[@]}" -p "$prompt" \
  --permission-mode acceptEdits \
  --allowedTools Read Write Edit Glob Grep "Bash(python tools/tg7_triage.py:*)" \
  "Bash(python3 tools/tg7_triage.py:*)"
rc=$?
summary=$(python3 "$HERE/tg7_triage.py" summary "$DIR" 2>&1)
echo "$(date -Is) triage exit $rc: $summary"
if [ $rc -eq 0 ] && [ -f "$DIR/TRIAGE.txt" ]; then
  notify "TG-7: sorted" "$summary"
else
  notify "TG-7: sorting failed" "$summary — see inventory/$LOT/import.log"
fi
exit $rc
