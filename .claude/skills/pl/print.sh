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
  # A sheet the poll saved in an item folder has only a meta file in
  # pick_lists/.served/, so read it through pick_store (as pick_list_links.py
  # does) instead of by path.
  html="$TMP/$token.html"
  if ! why=$(.venv/bin/python - "$token" "$html" 2>&1 <<'PY'
import sys
sys.path.insert(0, "lib")
import pick_store
html, status = pick_store.fetch(sys.argv[1])
if html is None:
    sys.exit(status)
open(sys.argv[2], "w", encoding="utf-8").write(html)
PY
  ); then echo "FAILED $ids (no sheet: ${why:-error})"; failed=$((failed+1)); continue; fi
  pdf="$TMP/$token.pdf"
  # CUPS here has no HTML filter, so render to PDF first. A throwaway profile
  # keeps headless Chromium off Russell's open browser.
  chromium --headless --disable-gpu --no-pdf-header-footer \
    --user-data-dir="$TMP/profile" --print-to-pdf="$pdf" "file://$html" >/dev/null 2>&1
  if [ -s "$pdf" ] && lp "$pdf" >/dev/null; then
    echo "PRINTED $ids"; printed=$((printed+1))
  else
    echo "FAILED $ids (render or lp failed)"; failed=$((failed+1))
  fi
done < <(sed -nE 's#^  (.+)  [a-z]+://[^ ]+/pick/([A-Za-z0-9_-]+)$#\2\t\1#p' <<<"$OUT")

echo "DONE printed=$printed failed=$failed printer=$(lpstat -d 2>/dev/null | sed 's/.*: //')"
[ "$failed" -eq 0 ]
