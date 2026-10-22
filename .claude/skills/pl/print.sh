#!/usr/bin/env bash
# Print every pick sheet awaiting shipment, all stores, on the default printer.
# One sheet per box. Runs from the main checkout, where pick_lists/ and the
# poll state live (prompts/pick_list.md step 1).
set -uo pipefail

MAIN=$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')
cd "$MAIN" || exit 1

OUT=$(.venv/bin/python tools/pick_list.py --poll --all-stores) || { echo "$OUT"; exit 1; }
echo "$OUT"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
printed=0 failed=0
# Poll prints one line per box: "  <id>[ + <id>]  http://.../pick/<token>"
while IFS=$'\t' read -r token ids; do
  html="$MAIN/pick_lists/.served/$token.html"
  if [ ! -f "$html" ]; then echo "FAILED $ids (no sheet $html)"; failed=$((failed+1)); continue; fi
  pdf="$TMP/$token.pdf"
  # CUPS here has no HTML filter, so render to PDF first. A throwaway profile
  # keeps headless Chromium off Russell's open browser.
  chromium --headless --disable-gpu --no-pdf-header-footer \
    --user-data-dir="$TMP/profile" --print-to-pdf="$pdf" "file://$html" >/dev/null 2>&1
  if [ -s "$pdf" ] && lp "$pdf" >/dev/null; then
    echo "PRINTED $ids"; printed=$((printed+1))
  else
    echo "FAILED $ids (sheet $html)"; failed=$((failed+1))
  fi
done < <(sed -nE 's#^  (.+)  [a-z]+://[^ ]+/pick/([A-Za-z0-9_-]+)$#\2\t\1#p' <<<"$OUT")

echo "DONE printed=$printed failed=$failed printer=$(lpstat -d 2>/dev/null | sed 's/.*: //')"
[ "$failed" -eq 0 ]
