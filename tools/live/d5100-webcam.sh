#!/usr/bin/env bash
# Feed a USB-connected Nikon D5100's live view into the "Nikon D5100" virtual
# camera (/dev/video10) so OBS can use it as a Video Capture Device.
# Leave this running for the whole show; Ctrl+C stops the feed.
# Setup (v4l2loopback, gphoto2): docs/live-streaming-setup.md
set -euo pipefail

DEV="${1:-/dev/video10}"
[ -e "$DEV" ] || { echo "$DEV missing - load v4l2loopback (docs/live-streaming-setup.md step 3)"; exit 1; }

# GNOME/KDE automount (gvfs) can grab the camera before gphoto2 does.
pkill -f gvfs-gphoto2-volume-monitor 2>/dev/null || true
# Name the D5100's port: with no --port gphoto2 takes the first camera listed,
# which is the TG-7 when it is plugged in too. PORT=usb:001,036 overrides.
PORT="${PORT:-$(gphoto2 --auto-detect | grep -i 'Nikon DSC D5100' | grep -oE 'usb:[0-9]+,[0-9]+' | head -n 1 || true)}"
[ -n "$PORT" ] || { echo "no D5100 found over USB - is it on and in M or A mode?"; exit 1; }

gphoto2 --port "$PORT" --stdout --capture-movie |
  ffmpeg -hide_banner -loglevel warning -i - \
    -vcodec rawvideo -pix_fmt yuv420p -threads 0 -f v4l2 "$DEV"
