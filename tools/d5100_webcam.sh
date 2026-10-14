#!/bin/bash
# Feed the Nikon D5100's USB live view into a virtual webcam (/dev/video10, "Nikon D5100")
# so OBS, browsers and video-call apps can use it. Live view over USB is 640x426, ~24 fps.
#
# One-time setup (Debian/Parrot, kernel 7.0): gphoto2, ffmpeg, and v4l2loopback >= 0.15.4.
# Debian's v4l2loopback-dkms 0.15.0 does not build on kernel 7.0; install upstream 0.15.4
# (github.com/umlaeute/v4l2loopback) via DKMS instead, and load it with:
#   /etc/modules-load.d/v4l2loopback.conf: v4l2loopback
#   /etc/modprobe.d/v4l2loopback.conf:     options v4l2loopback exclusive_caps=1 card_label="Nikon D5100" video_nr=10
#
# Usage: plug in the camera (dial on M or A, lens cap off), switch it on, run this, then pick
# "Nikon D5100" as the camera in OBS. Ctrl+C to stop. The feed restarts if the camera drops
# live view. Every 2s a frame is written to $SNAP, for checking framing while OBS holds the
# webcam. The camera takes one USB connection at a time, so other gphoto2 commands (ISO,
# shutter) only work while this is stopped.
DEV=${DEV:-/dev/video10}
SNAP=${SNAP:-${TMPDIR:-/tmp}/d5100_latest.jpg}
while true; do
  gphoto2 --stdout --capture-movie 2>/dev/null \
    | ffmpeg -hide_banner -loglevel error -i - \
        -map 0:v -vcodec rawvideo -pix_fmt yuv420p -threads 0 -f v4l2 "$DEV" \
        -map 0:v -vf fps=0.5 -update 1 -y "$SNAP"
  echo "$(date +%T) feed stopped, restarting in 2s (Ctrl+C to quit)" >&2
  sleep 2
done
