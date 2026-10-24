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
# FPS caps the webcam feed (e.g. FPS=10 for a lighter preview); unset = the camera's ~24 fps.
RATE=()
[ -n "${FPS:-}" ] && RATE=(-vf "fps=$FPS")
# The camera's clock: live view dies when the D5100's date has reset (Russell, 2026-10-22), so
# every (re)start sets it to this computer's time first. A reset date (year before 2020) is
# logged and noted in $CLOCK, one line "<when>\t<what the camera said>", which show_flow.py's
# camera watch reads (clock_marker() there derives the same path). A sane date removes the
# note. Best effort: bounded by CLOCK_TIMEOUT seconds per gphoto2 call, never fatal.
case ${SNAP##*/} in ?*.*) CLOCK=${SNAP%.*}.clock ;; *) CLOCK=$SNAP.clock ;; esac
CLOCK_TIMEOUT=${CLOCK_TIMEOUT:-5}

set_clock() {
  local out cur said year rc
  # gphoto2 2.5 prints a DATE widget as "Type: DATE", "Current: <epoch>", "Printable: <%c>"
  if out=$(timeout -k 2 "$CLOCK_TIMEOUT" gphoto2 --get-config datetime 2>&1); then
    cur=$(sed -n 's/^Current: *\(-\{0,1\}[0-9][0-9]*\) *$/\1/p' <<<"$out" | head -n 1)
    said=$(sed -n 's/^Printable: *//p' <<<"$out" | head -n 1)
    if [ -n "$cur" ] && year=$(date -d "@$cur" +%Y 2>/dev/null); then
      said=$(date -d "@$cur" '+%F %T')
    else  # no epoch: a year in the printable date, or unknown
      year=$(grep -o '\b[12][0-9]\{3\}\b' <<<"$said" | tail -n 1)
    fi
    if [ -n "$year" ] && [ "$year" -lt 2020 ]; then
      echo "$(date +%T) D5100 clock reset (camera said $said); setting it to now" >&2
      printf '%s\t%s\n' "$(date '+%F %T')" "$said" >"$CLOCK"
    elif [ -n "$year" ]; then
      rm -f "$CLOCK"
    fi
  else
    rc=$?
    echo "$(date +%T) couldn't read the D5100 clock (gphoto2 exit $rc)" >&2
  fi
  timeout -k 2 "$CLOCK_TIMEOUT" gphoto2 --set-config datetime=now >/dev/null 2>&1 \
    || echo "$(date +%T) couldn't set the D5100 clock; starting live view anyway" >&2
}

while true; do
  set_clock
  gphoto2 --stdout --capture-movie 2>/dev/null \
    | ffmpeg -hide_banner -loglevel error -i - \
        -map 0:v "${RATE[@]}" -vcodec rawvideo -pix_fmt yuv420p -threads 0 -f v4l2 "$DEV" \
        -map 0:v -vf fps=0.5 -update 1 -y "$SNAP"
  echo "$(date +%T) feed stopped, restarting in 2s (Ctrl+C to quit)" >&2
  sleep 2
done
