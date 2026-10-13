#!/usr/bin/env bash
# Launch OBS Studio in a rootless podman container on the host's X11 display,
# with Intel GPU (/dev/dri), PulseAudio/PipeWire audio, and webcams if present.
# Runs detached; the container is removed when OBS exits. See README.md.
set -euo pipefail

IMAGE=localhost/obs-studio:latest
HERE="$(cd "$(dirname "$0")" && pwd)"
# OBS profiles/scenes (and any stream keys) live outside the repo — it's public.
CONFIG_DIR="${OBS_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/obs-studio-container}"
VIDEOS_DIR="${OBS_VIDEOS_DIR:-$HOME/Videos}"
mkdir -p "$CONFIG_DIR" "$VIDEOS_DIR"

if ! podman image exists "$IMAGE"; then
  podman build -t "$IMAGE" "$HERE"
fi

devices=(--device /dev/dri)
for v in /dev/video*; do [ -e "$v" ] && devices+=(--device "$v"); done

exec podman run --rm -d \
  --name obs-studio \
  --userns=keep-id \
  --group-add keep-groups \
  --security-opt label=disable \
  --ipc=host \
  "${devices[@]}" \
  -e DISPLAY="$DISPLAY" \
  -e XAUTHORITY=/tmp/.Xauthority \
  -v "${XAUTHORITY:-$HOME/.Xauthority}:/tmp/.Xauthority:ro" \
  -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
  -e PULSE_SERVER="unix:/run/pulse/native" \
  -v "$XDG_RUNTIME_DIR/pulse/native:/run/pulse/native" \
  -v "$CONFIG_DIR:/home/ubuntu/.config/obs-studio" \
  -v "$VIDEOS_DIR:/home/ubuntu/Videos" \
  "$IMAGE" "$@"
