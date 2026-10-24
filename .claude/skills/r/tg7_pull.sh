#!/usr/bin/env bash
# Copy new TG-7 photos (JPG + ORF) off the card: over USB first (Storage mode
# mounts as a drive, MTP goes through gphoto2), else over the camera's Wi-Fi
# if it answers, else by hopping the laptop's Wi-Fi onto the camera's network
# (tg7_wifi_hop.sh, detached). USB comes first because the laptop has one
# Wi-Fi card and the camera's Wi-Fi is usually off. Files already in DEST
# with the same name and size are skipped. Photos land in ~/Pictures/TG-7
# ($TG7_PHOTOS). TG7_USB_ONLY=1 stops after the USB tries, so an unattended
# run (tools/tg7_import.sh) never touches the camera's Wi-Fi or hops.
#
#   tg7_pull.sh          pull (prints "HOP started; status at <path>" for a hop)
#   tg7_pull.sh --wait   wait for a hop's status file and print it
set -uo pipefail

MAIN=${TG7_MAIN:-$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')}
DEST=${TG7_PHOTOS:-$HOME/Pictures/TG-7}
HOP=$(dirname "$0")/tg7_wifi_hop.sh
STATUS=${XDG_RUNTIME_DIR:-/tmp}/tg7_hop.status

# After a hop: the status file is written once the laptop is back on its own
# network. A hop is capped at 10 min plus its reconnect, so give up at 15.
if [ "${1:-}" = --wait ]; then
  for _ in $(seq 180); do
    [ -f "$STATUS" ] && exec cat "$STATUS"
    sleep 5
  done
  echo "NO STATUS after 15 min: see ${STATUS%.status}.log"
  exit 1
fi

mkdir -p "$DEST"

# USB Storage: a mounted card with an OM SYSTEM DCIM folder. If the card
# (volume label "OM SYSTEM") is plugged in but not mounted yet, mount it.
find_card() { ls -d /media/"$USER"/*/DCIM/*OMSYS* /run/media/"$USER"/*/DCIM/*OMSYS* 2>/dev/null | head -1; }
SRC=$(find_card)
CARD='/dev/disk/by-label/OM\x20SYSTEM'
if [ -z "$SRC" ] && [ -e "$CARD" ]; then
  udisksctl mount -b "$CARD" >/dev/null 2>&1
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
  # Only JPG and ORF, by file number ("#12  ZZ120012.JPG  rd ..."), so videos stay on the card.
  NUMS=$(gphoto2 --port "$PORT" --list-files 2>/dev/null | sed -nE 's/^#([0-9]+) +[^ ]+\.(JPG|ORF) .*/\1/ip' | paste -sd,)
  [ -z "$NUMS" ] && { echo "0 new photo(s) -> $DEST"; exit 0; }
  cd "$DEST" && gphoto2 --port "$PORT" --get-file "$NUMS" --skip-existing --filename "%f.%C"
  exit $?
fi

if [ -n "${TG7_USB_ONLY:-}" ]; then
  echo "NOT FOUND (USB only): TG-7 not on USB"
  exit 2
fi

# Wi-Fi, only when the TG-7 is not on USB: the camera is at 192.168.0.10
# when the laptop is on its network. On air, the stream owns the network, so leave the camera's Wi-Fi alone.
if [ -e "$MAIN/inventory/_live/ON_AIR" ]; then
  echo "ON AIR: skipping the TG-7 Wi-Fi pull (inventory/_live/ON_AIR is set)"
elif curl -s -m 3 -o /dev/null -A "OI.Share v2" http://192.168.0.10/get_caminfo.cgi; then
  echo "VIA wifi"
  exec python3 "$MAIN/tools/live/tg7cam" sync --to "$DEST"
fi

# Wi-Fi hop: not on USB and the camera did not answer, but a saved TG-7 Wi-Fi
# profile is in range. The laptop has one Wi-Fi adapter, so joining the
# camera drops the network it is on, Claude included. The hop runs detached, needs nothing from Claude, and
# always reconnects; its result lands in $STATUS.
if [ ! -e "$MAIN/inventory/_live/ON_AIR" ] && bash "$HOP" --check >/dev/null 2>&1; then
  rm -f "$STATUS"
  TG7_MAIN=$MAIN TG7_PHOTOS=$DEST setsid nohup bash "$HOP" >/dev/null 2>&1 </dev/null &
  echo "HOP started; status at $STATUS"
  exit 0
fi

echo "NOT FOUND: TG-7 not on USB and not on its Wi-Fi (192.168.0.10)"
exit 2
