# OBS Studio in a container

OBS Studio from the official Ubuntu PPA
([install instructions](https://github.com/obsproject/obs-studio/wiki/install-instructions#linux)),
run in rootless podman on the host's X11 display. It's the streaming client for
eBay Live; the lower-third overlay at `/live/<show>/overlay` (from #202) is
added as an OBS Browser source.

```bash
tools/obs-studio/run.sh
```

The first run builds `localhost/obs-studio:latest` (Ubuntu 24.04, ~2.3 GB). To
pick up a newer OBS release, rebuild:

```bash
podman build --pull -t localhost/obs-studio:latest tools/obs-studio
```

## What's passed through

| Host | Container | Why |
|---|---|---|
| `$DISPLAY`, `/tmp/.X11-unix`, `$XAUTHORITY` | same | X11 window |
| `/dev/dri` | same | OpenGL renderer + VAAPI H.264 encoder (Intel) |
| `/dev/video*` | same | webcams, if any are plugged in at launch |
| `$XDG_RUNTIME_DIR/pulse/native` | `/run/pulse/native` | mic + desktop audio (PipeWire's Pulse socket works) |
| `~/.config/obs-studio-container` | `~/.config/obs-studio` | profiles, scenes, stream keys — kept out of the repo |
| `~/Videos` | `~/Videos` | recordings |

Override the last two with `OBS_CONFIG_DIR` and `OBS_VIDEOS_DIR`. Extra
arguments go to `obs` (e.g. `run.sh --startstreaming`).

`--userns=keep-id` maps you to the image's uid-1000 `ubuntu` user, so files it
writes are owned by you. `--ipc=host` is for X11 shared memory.

## Known gaps

- **Virtual camera** needs the `v4l2loopback` kernel module on the host.
- **VLC Video Source** isn't available; libvlc isn't in the image.
- Webcams plugged in after launch aren't seen until OBS is restarted.
- X11 only. A Wayland session would need the PipeWire screen-capture portal
  passed through instead.

Logs: `podman logs obs-studio`.
