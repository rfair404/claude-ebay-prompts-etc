#!/usr/bin/env bash
# Copy new TG-7 photos (JPG + ORF) off the card: over the camera's Wi-Fi if
# it answers, else over USB (Storage mode mounts as a drive, MTP goes through
# gphoto2). Files already in DEST with the same name and size are skipped.
#   tg7_pull.sh [DEST]     DEST defaults to ~/Pictures/TG-7 ($TG7_PHOTOS)
set -uo pipefail

MAIN=$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')
DEST=${1:-${TG7_PHOTOS:-$HOME/Pictures/TG-7}}
mkdir -p "$DEST"

# Wi-Fi: the camera is at 192.168.0.10 when the laptop is on its network.
if curl -s -m 3 -o /dev/null -A "OI.Share v2" http://192.168.0.10/get_caminfo.cgi; then
  echo "VIA wifi"
  exec python3 "$MAIN/tools/live/tg7cam" sync --to "$DEST"
fi

# USB Storage: a mounted card with an OM SYSTEM DCIM folder. Mount it first
# if the drive is plugged in but not mounted yet.
find_card() { ls -d /media/"$USER"/*/DCIM/*OMSYS* /run/media/"$USER"/*/DCIM/*OMSYS* 2>/dev/null | head -1; }
SRC=$(find_card)
if [ -z "$SRC" ]; then
  for dev in $(lsblk -rpno NAME,TRAN,MOUNTPOINT | awk '$2=="usb"{print $1}'); do
    for part in $(lsblk -rpno NAME,FSTYPE,MOUNTPOINT "$dev" | awk '$2 ~ /fat|exfat/ && $3==""{print $1}'); do
      udisksctl mount -b "$part" >/dev/null 2>&1
    done
  done
  SRC=$(find_card)
fi
if [ -n "$SRC" ]; then
  echo "VIA usb-storage $SRC"
  n=0
  for f in "$SRC"/*.JPG "$SRC"/*.ORF; do
    [ -f "$f" ] || continue
    d="$DEST/$(basename "$f")"
    [ -f "$d" ] && [ "$(stat -c %s "$f")" = "$(stat -c %s "$d")" ] && continue
    cp "$f" "$d.part" && mv "$d.part" "$d" && { echo "  $(basename "$f")"; n=$((n+1)); }
  done
  echo "$n new photo(s) -> $DEST"
  exit 0
fi

# USB MTP: pick the TG-7 out of gphoto2's list by port, so a D5100 that is
# also plugged in is left alone.
PORT=$(gphoto2 --auto-detect 2>/dev/null | grep -iE 'olympus|om system|om digital|tg-7' | grep -oE 'usb:[0-9,]+' | head -1)
if [ -n "$PORT" ]; then
  echo "VIA usb-mtp $PORT"
  cd "$DEST" && gphoto2 --port "$PORT" --get-all-files --skip-existing --filename "%f.%C"
  exit $?
fi

echo "NOT FOUND: TG-7 not on its Wi-Fi (192.168.0.10) and not on USB"
exit 2
