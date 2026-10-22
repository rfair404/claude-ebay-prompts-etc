# Stream box: OBS and the cameras on a second machine

The laptop runs out of CPU during shows: on the 10-21 show OBS skipped 99.4%
of frames. The **stream box** is an old desktop with a GPU that takes over
the heavy part. OBS, the D5100, the webcam, the TG-7 and the mic move to it,
and both destinations (eBay over WHIP, Twitch over RTMP) encode on its GPU.
The laptop keeps Claude, the browser and the pager, and drives the box over
the LAN. Tracking issue: [#249](https://github.com/rfair404/claude-ebay-prompts-etc/issues/249).

**The box is optional.** If it isn't set up, or it fails, the show runs on
the laptop exactly as before (`docs/live-streaming-setup.md`). Nothing here
changes the laptop's OBS, scenes, Flatpak or virtual cameras.

## Architecture

```
 laptop (192.168.1.137)                          stream box (same /24)
 ---------------------------------------         -------------------------------------------
 Claude, browser, pager (lib.cli serve)          OBS (Flatpak), auto-login X session
 show_flow.py / obsctl.py  --- obs-websocket --> :4455, password, firewalled to the /24
                           --- ssh (key) ------> :22, firewalled to the /24
 OBS still installed: the fallback               D5100 (USB) -> /dev/video10
                                                 TG-7 (Wi-Fi) -> /dev/video11
                                                 webcam, mic
                                                 GPU: eBay WHIP + Twitch RTMP encodes
```

| Piece | Lives in |
|---|---|
| Provisioning, run from the laptop | `tools/live/stream_box/provision.py` |
| The box's OBS config step (runs on the box) | `tools/live/stream_box/box_apply.py` |
| Profile and scene templates it copies | `tools/live/obs/` (the same files the laptop uses) |
| Tests | `tests/test_stream_box.py` |

## Optional, with the laptop as fallback

- With no box configured (no `OBS_WS_HOST`, no box config), every show tool
  talks to OBS on the laptop at 127.0.0.1:4455 with the local D5100 feed,
  refocus and white balance, exactly as today. Tests pin that default.
- `provision.py` changes only the box. On the laptop it reads the OBS
  templates (and, with `--copy-keys`, the laptop's Ebay profile) and writes
  one file: the box's obs-websocket password, 0600, at
  `~/.config/pops-stream-box/obs-config/plugin_config/obs-websocket/config.json`.
  It refuses to write inside the repo or the laptop's OBS config, and a test
  checks that a full run adds that one file and nothing else. (ssh itself
  records the box's host key in `~/.ssh/known_hosts` on first contact.)
- The laptop's OBS config stays as it is, so falling back needs no reinstall:
  run the show tools without `OBS_WS_HOST`.
- Until the follow-up below lands, the show tools only reach the box when
  told to, one command at a time:

  ```bash
  OBS_WS_HOST=<box> OBS_CONFIG_DIR=~/.config/pops-stream-box/obs-config python tools/live/obsctl.py status
  ```

## What the script does

Run from the laptop. It reads the box first (no sudo), then runs one setup
script on the box with sudo. It asks for the box password once per pass,
usually twice, because a new driver needs a reboot in between. The script
waits for the box to come back and then finishes.

1. Reads the box: `lspci`, OS, network, Wi-Fi, whether OBS runs.
2. Packages: Flatpak, gphoto2, ffmpeg, v4l2 tools, PipeWire, ufw.
3. GPU driver, chosen from lspci (a discrete card beats the board's own
   graphics): NVIDIA's proprietary driver via `ubuntu-drivers`, or Mesa
   VAAPI for AMD and Intel.
4. Display: X, openbox and auto-login, because OBS needs a screen. The box
   never sleeps.
5. v4l2loopback with /dev/video10 ("Nikon D5100") and /dev/video11 ("OM
   TG-7") at boot. If the distro's DKMS package doesn't build, it falls back
   to upstream 0.15.4.
6. OBS from Flathub, per user, as on the laptop. On NVIDIA this adds the
   matching GL extension; on Intel, the VAAPI extension.
7. Firewall: SSH and obs-websocket allowed only from the box's own /24,
   everything else inbound denied, plus the TG-7's live-view UDP from
   192.168.0.10. It refuses if the laptop isn't inside that /24, so it can't
   lock the laptop out.
8. A 1 s test encode on the GPU decides the encoder:

   | GPU | eBay (stream output) | Twitch (Custom Output) |
   |---|---|---|
   | NVIDIA, NVENC works | `obs_nvenc_h264_tex`, CBR 3000, no B-frames | ffmpeg `h264_nvenc`, full size |
   | AMD/Intel, VAAPI works | `ffmpeg_vaapi_tex`, CBR 3000, no B-frames | `libx264` superfast 720x1280 on the box's CPU (Custom Output can't use VAAPI) |
   | none, or the test fails | `obs_x264` superfast | `libx264` superfast 720x1280 |

9. OBS config (`box_apply.py`):
   - the Ebay profile from the repo template, with the encoder above. Keys
     already on the box, such as the stream URLs, are kept.
   - the eBay Live scene collection, only if the box doesn't have one yet.
     `--reset-scenes` overwrites it.
   - obs-websocket on, with a password generated on the box. The password
     is never printed, logged or put on a command line.
   - OBS opening on that profile and collection, with no first-run wizard.
10. Starts OBS in the auto-login session, copies the password to the laptop
    file above, and checks that OBS answers over the LAN.

Re-running is safe: each step checks before it changes anything. The whole
run refuses while OBS runs on the box. `--stop-obs` stops OBS first, but
only if it can confirm nothing is live. `--dry-run` reads the box and prints
every command without changing anything. Offline, with no box at all:

```bash
python3 tools/live/stream_box/provision.py --dry-run --gpu nvidia --lan 192.168.1.0/24
```

`--copy-keys` copies the eBay WHIP service and the Twitch URL from the
laptop's OBS profile into the box's, over SSH stdin, without showing them.
Without it, paste both into OBS on the box (`docs/live-streaming-setup.md`
step 2).

## Setting the box up (**user**: Russell)

1. Put the GPU in. Plug a monitor into **the GPU** (not the motherboard) and
   a keyboard into the box. Connect the box to the router with a network
   cable.
2. Boot the box from the installer USB and pick **Ubuntu Server**. If the
   USB is a different installer, stop and tell the technical director.
3. In the installer:
   - Keyboard and network: keep the defaults (DHCP).
   - Storage: **Use an entire disk**, and pick the USB stick or SSD the box
     will run from. Don't pick the installer USB.
   - Profile: your name `Russell`, server's name `streambox`, username
     `russ`, and a password you'll remember (the setup asks for it).
   - SSH: tick **Install OpenSSH server**. Under **Import SSH identity**
     choose **from GitHub**, username `rfair404`. Untick **Allow password
     authentication over SSH**.
   - Featured snaps: none.
   - When it says **Reboot now**, take the installer USB out and reboot.
4. Plug the D5100, the webcam and the mic into the box.
5. On the laptop, run this. It finds the box by its name, so you don't need
   its address. It asks for the box password from step 3 (usually twice,
   with a reboot in between) and takes about 15–30 minutes:

   ```bash
   cd ~/EBAYBIZ && python3 tools/live/stream_box/provision.py --find streambox --copy-keys
   ```

   The last lines show the encoders and `OBS <version> answers on <address>:4455`.

If `--find` can't see the box, the installer's network screen shows its
address. Use that instead: `python3 tools/live/stream_box/provision.py <address> --copy-keys`.

## After the box is up

The box runs OBS, but the show tools still drive the laptop's cameras. Next
is the wiring PR from #249, after #246, #245 and #243 land:

- obs-websocket password from the stream-box file, kept separate from
  `OBS_CONFIG_DIR`, and box mode only on an explicit setting (`OBS_WS_HOST`
  or one `STREAM_BOX=user@host`)
- if the box is configured but can't be reached: fail fast, say how to fall
  back, and leave nothing half switched
- D5100 feed, refocus, white balance and stills over SSH on the box
  (`feed_*`, `_gphoto2`, `d5_usb`), and feed state read from the box
- TG-7 stream and refocus from the box. It needs Wi-Fi on the box (a USB
  adapter) and NetworkManager for `tg7cam connect`.
- the Hold still's folder created on the box, not the laptop
- `twitch-setup` follows the box's encoder instead of writing `libx264`
- launching and quitting OBS on the box (`obsctl.py launch|quit` are local
  only)
- the window-capture pages. The recommendation in #249 is a browser on the
  box for Up Next, keeping the `SAFE_WINDOW`/`PRIVATE_WINDOW` guard, and a
  low-fps window stream from the laptop for Desktop/Multi.
- the background-music file and photo-shoot stills copied between the
  machines
- this guide folded into `live-streaming-setup.md` and the live-show skill
