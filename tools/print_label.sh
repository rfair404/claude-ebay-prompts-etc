#!/usr/bin/env bash
# Print a 4x6 shipping label on the QL-1100, by name.
#
#   tools/print_label.sh <order-id|label.pdf|label.png|https://...label-url>
#
# The system default is CannonBW (pick sheets), so labels must name the label
# printer. An order ID prints Seller Hub's download, ~/Downloads/"eBay label
# <order-id>.pdf". A URL is downloaded to $TMPDIR first. Exits non-zero if
# CUPS refuses the job.
set -euo pipefail

PRINTER=QL-1100

src=${1:?usage: print_label.sh <order-id, label file or URL>}
if [[ $src =~ ^[0-9]{2}-[0-9]{5}-[0-9]{5}$ ]]; then
  file="$HOME/Downloads/eBay label $src.pdf"
elif [[ $src == http://* || $src == https://* ]]; then
  file="${TMPDIR:-/tmp}/label-$(date +%s).${src##*.}"
  file=${file%%\?*}
  curl -fsSL "$src" -o "$file"
else
  file=$src
fi
[ -s "$file" ] || { echo "no label at $file" >&2; exit 1; }

lpstat -p "$PRINTER" | grep -q disabled && { echo "$PRINTER is paused: cupsenable $PRINTER" >&2; exit 1; }
lp -d "$PRINTER" -o PageSize=103x164 -o fit-to-page "$file"
