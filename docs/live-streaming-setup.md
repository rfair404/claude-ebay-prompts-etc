# Live-streaming machine setup (eBay Live + OBS)

The checklist for making a fresh Linux laptop ready to stream an eBay Live
show. Claude: work through the steps in order, run each **Verify** line, and
stop at anything marked **user** — those need a password, a stream secret, or
hands on a camera.

Built and verified 2026-10-13/14 on Parrot 7.3 (Debian 13 base), kernel
7.0.9, KDE on X11, Intel UHD 620 laptop. Debian/Ubuntu-family commands.

| Piece | Lives in |
|---|---|
| OBS eBay profile + vertical scene (no secrets) | `tools/live/obs/` |
| Nikon D5100 webcam feed | `tools/d5100_webcam.sh` (what `show_flow.py feed start` runs) |
| OM System TG-7 Wi-Fi control | `tools/live/tg7cam` |
| OBS remote control, no GUI | `tools/live/obsctl.py` |
| Show views, refocus, go live | `tools/live/show_flow.py`, skill `.claude/skills/live-show/` |

## 1. OBS Studio — Flatpak, not the PPA

The [OBS install notes](https://github.com/obsproject/obs-studio/wiki/install-instructions#linux)
target their PPA at Ubuntu only; on Debian use the Flathub build. (A podman
container running the Ubuntu PPA build was tried and dropped: it lagged the
current release, and Twitch refused its version.)

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub com.obsproject.Studio
```

**Verify:** `flatpak info --user com.obsproject.Studio` matches the latest
tag at https://github.com/obsproject/obs-studio/releases. Launch once with
`flatpak run com.obsproject.Studio`, close it, then check the newest log in
`~/.var/app/com.obsproject.Studio/config/obs-studio/logs/` for
`FFmpeg VAAPI H264 encoding supported` (hardware encoder present) and
`obs-webrtc.so` in the loaded modules (WHIP output present).

Update before a show: `flatpak update --user com.obsproject.Studio`.

## 2. OBS eBay profile

Profile `Ebay` follows eBay's own guide,
[Stream with OBS](https://www.ebay.com/ebaylive/studio/resources/obs), with
two Linux substitutions:

| eBay's guide | Profile | Why it differs |
|---|---|---|
| Output Mode Advanced, Rescale Disabled | same | |
| CBR, 3000 Kbps, keyframe 1 s | same | |
| CPU Usage: superfast | x264 `superfast`, `bframes=0` | WebRTC/WHIP can't carry B-frames |
| Audio 128 Kbps | same | |
| Audio encoder CoreAudio AAC | **Opus** | CoreAudio is macOS-only; WHIP requires Opus (eBay: accept OBS's Opus prompt) |
| 1080x1920 base + output, 30 FPS | same | |
| Service | **WHIP**, Bearer Token blank | eBay's "No delay (WHIP)" option |

If x264 drops frames on a weak CPU, switch Encoder to `ffmpeg_vaapi_tex`
(FFmpeg VAAPI H.264) in Settings > Output > Streaming and keep CBR 3000.

Install (OBS must be **closed** — it rewrites these files on exit):

```bash
C=~/.var/app/com.obsproject.Studio/config/obs-studio/basic
mkdir -p "$C/profiles/Ebay" "$C/scenes"
cp tools/live/obs/profiles/Ebay/* "$C/profiles/Ebay/"
sed -i "s#=~#=$HOME#" "$C/profiles/Ebay/basic.ini"
cp tools/live/obs/scenes/eBay_Live.json "$C/scenes/"
```

Then in OBS: Profile > Ebay, Scene Collection > eBay Live.

**user — the stream URL.** eBay treats it as a secret ("Do not share your
stream URL or stream key publicly"), so it is not in this repo and Claude does
not type it in. Stream manager > event > Streaming information > No delay
(WHIP) > copy Stream URL; OBS Settings > Stream > Service **WHIP** (bottom of
the list, under Show All...) > paste into Server. The URL stays the same for
every event.

**Twitch rides along in the same profile**, so eBay and Twitch can run at
once and start/stop independently. OBS has one stream output (eBay's WHIP),
and Debian's ffmpeg 7.1 has no WHIP muxer for a relay, so Twitch goes out
on the *recording* output: Custom Output (FFmpeg) > Output to URL, flv, x264
veryfast 3000k, AAC. With nothing live, run `python
tools/live/show_flow.py twitch-setup` to set all of that except the URL.
Then (**user**): OBS Settings > Output > Recording > "File path or URL",
paste `rtmp://live.twitch.tv/app/<stream key>` (Twitch dashboard > Settings
> Stream), and click OK. The cost: no local recording while Twitch is
live, and a second encode. The old `Untitled` profile is no longer used.

**After `twitch-setup`, Record is Twitch.** In the `Ebay` profile, OBS's
Start Recording button (or a recording hotkey) starts a *public* Twitch
broadcast, not a file. To record to a file again, with nothing live: OBS
Settings > Output > Recording > Type **Standard** (or `python
tools/live/obsctl.py call SetProfileParameter
'{"parameterCategory":"AdvOut","parameterName":"RecType","parameterValue":"Standard"}'`).
The Twitch URL stays in the profile; `twitch-setup` switches back.

Don't run Tools > Auto-Configuration Wizard on the eBay profile: its bandwidth
test only speaks RTMP (it fails with "Unknown protocol" against the WHIP URL)
and it rewrites the output settings.

**Verify:** with a draft event open in Stream manager, Start Streaming in OBS;
the log shows `[obs-webrtc] ... Connect time` and Stream manager's preview
fills within seconds. Start Streaming only sends video — the show starts with
Start event in Stream manager.

Testing: create the event with **Save as Draft** (eBay's
[Practice before you go live](https://www.ebay.com/ebaylive/studio/resources/practice)).
Purchases in a draft event are real orders — cancel them in Seller Hub.

## 3. Virtual cameras — v4l2loopback ≥ 0.15.4

Debian 13's `v4l2loopback-dkms` 0.15.0 fails to build on kernel 7.x
(`implicit declaration of function 'setup_timer'`). Build upstream 0.15.4+
through DKMS instead (**user** — sudo):

```bash
sudo apt install dkms v4l2loopback-utils v4l-utils linux-headers-$(uname -r)
sudo apt remove -y v4l2loopback-dkms
git clone --depth 1 -b v0.15.4 https://github.com/v4l2loopback/v4l2loopback "$TMPDIR/v4l2loopback"
sudo cp -r "$TMPDIR/v4l2loopback" /usr/src/v4l2loopback-0.15.4
sudo dkms install v4l2loopback/0.15.4
```

Two devices — Nikon on 10, TG-7 on 11 — loaded at boot:

```bash
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
sudo tee /etc/modprobe.d/v4l2loopback.conf <<'EOF'
options v4l2loopback devices=2 exclusive_caps=1,1 video_nr=10,11 card_label="Nikon D5100","OM TG-7"
EOF
sudo modprobe -r v4l2loopback; sudo modprobe v4l2loopback
```

(The 2026-10 machine had only the Nikon device; the TG-7 line is the planned
second one and hasn't been loaded yet.)

**Verify:** `dkms status | grep v4l2loopback` says `installed`;
`v4l2-ctl --list-devices` lists "Nikon D5100" at /dev/video10 (and "OM TG-7"
at /dev/video11). Built-in webcam shows up separately as /dev/video0.

## 4. Nikon D5100 (USB live view)

```bash
sudo apt install gphoto2 ffmpeg
```

**user:** USB cable in, mode dial on M or A, camera on. Then run
`python tools/live/show_flow.py feed start` (it runs `tools/d5100_webcam.sh`
detached and restarts it if live view drops); in OBS add Sources > Video
Capture Device (V4L2) > "Nikon D5100".

**Verify:** `gphoto2 --auto-detect` lists the D5100, and `show_flow.py feed
status` says `running`. `running, no frames for Ns` means the feed loop is up
but the camera sends nothing (off, asleep, unplugged, dial not on M/A).

## 5. OM System TG-7 (close-up camera)

What the camera offers, from its
[manual](https://download.omsystem.com/pages/inst/tg7/manual_tg7_ENU.pdf)
and probing on 2026-10-14:

- **USB:** Storage / MTP / Charge only. No webcam mode; over PTP it reports
  "No Image Capture". USB gives files, nothing live.
- **HDMI (micro, Type D):** live view while shooting, up to 1080p/4K. Needs a
  USB HDMI capture stick (any UVC one; shows up as a normal OBS camera). HDMI
  is disabled while USB is connected to a computer. No camera control over
  HDMI (HDMI Control/CEC is playback-only).
- **Wi-Fi:** full remote control + live view via the OM Image Share
  protocol at 192.168.0.10 — `tools/live/tg7cam` (focus point, AF, shoot,
  sync the card, LED, zoom, properties, live view into /dev/video11 or UDP).
  Tested on a TG-7 (firmware 1.1) 2026-10-21: live view runs 25 fps at
  640x480 (1280x960 is 15 fps), and keeps running through AF, a shot, the
  LED and downloading photos. The TG-7 has no `exec_shutter`; it shoots with
  `exec_takemotion com=starttake` (focus at a point, then fire). The laptop
  joins the *camera's* network, so it needs internet from somewhere else
  (below).
- **Bluetooth:** the camera advertises as its serial number with service
  `adc505f9-4e58-4b71-b8ca-983bb8c73e4f`; the laptop can connect without
  pairing. The documented BLE commands are only auth / power-on / Wi-Fi wake
  ([protocol notes](https://github.com/KyleJohnstonNet/android-om1-camera-importer/blob/main/docs/oishare-protocol.md));
  the phone app's remote shutter runs over Wi-Fi. The RM-WR1 Bluetooth remote
  does half-press AF + shutter, but its protocol is unpublished.

Install `tg7cam`:

```bash
mkdir -p ~/bin && ln -sf "$PWD/tools/live/tg7cam" ~/bin/tg7cam   # a link, so updates apply
tg7cam --help-setup        # loopback / OBS Media Source options
```

(`~/bin` must be on `PATH`; otherwise call `~/bin/tg7cam`.) Photos from
`tg7cam shoot` and `tg7cam sync` land in `~/Pictures/TG-7` unless `--to DIR`
or `$TG7_PHOTOS` says otherwise.

### Wi-Fi control during a show needs a second internet path

The laptop's Intel 9560 allows one ordinary Wi-Fi connection at a time
(`iw list`: `#{ managed } <= 1`; the second slot is P2P-only), and Docker
can't add a radio. So joining the camera's Wi-Fi drops the internet unless
the stream has another route — `tg7cam connect` refuses in that case. Options:
USB tethering from a phone, Ethernet (the laptop has a port), or a USB Wi-Fi
adapter. Check a new machine's card with:

```bash
sudo apt install iw
iw list | grep -A8 'valid interface combinations'
```

Then: **user** — camera menu Wi-Fi/Bluetooth Settings > Device Connection
shows the SSID/password; `tg7cam connect <SSID>` prompts for the password;
`tg7cam probe`. The camera's own controls lock while a computer is connected.
During a show, `show_flow.py tg7 start` runs the stream (section 6).

## 6. Running the show without the OBS window

OBS is driven over its built-in obs-websocket (v5). Turn it on once (**user**
or Claude): OBS Tools > WebSocket Server Settings > Enable, port 4455, with
authentication. The password stays in OBS's own config, and
`tools/live/obsctl.py` reads it from there. The Flatpak OBS listens on every
interface, so the password is what keeps the LAN out.

Run order, from the repo root:

```bash
python tools/live/obsctl.py launch            # 1. OBS minimized to the tray, no window
python tools/live/show_flow.py feed start     # 2. tools/d5100_webcam.sh (self-restarting), detached
python tools/live/show_flow.py feed status    #    want "running", not "running, no frames for Ns"
python tools/live/show_flow.py setup --window "<title substring>"   # 3. build/refresh the pages
python tools/live/show_flow.py standin on     # 4. only if the D5100 is out (setup keeps this state)
python tools/live/show_flow.py page intro     # 5. pre-show card, before going live
/home/russ/EBAYBIZ/.venv/bin/python -m lib.cli serve   # 6. optional: the /pager page (needs the repo venv)
python tools/live/show_flow.py golive ebay --confirm    # 7. and/or: golive twitch --confirm
python tools/live/show_flow.py page item | next | webcam   # the pager; also: refocus
python tools/live/show_flow.py tg7 start      # TG-7 live view -> the closeup page (before 3 the first time)
python tools/live/show_flow.py step           # item -> closeup -> hold -> item ...
python tools/live/show_flow.py hold | release # freeze the camera page while the item turns
python tools/live/show_flow.py stop                     # both; or: stop ebay | stop twitch
```

**closeup** (scene `Close-up`) is the TG-7 below the same webcam strip
(`setup --tg7-rotation 90|270` if it's mounted on its side). The TG-7
source is a v4l2 camera on /dev/video11 when that loopback device exists,
else a Media Source on `udp://127.0.0.1:5011`; re-run `setup` after adding
the device and it switches. `refocus` on the closeup page is the TG-7's
half-press (0.3 s, live view keeps running), so nothing is hidden.

**hold** (scene `Hold`) freezes the camera page on air: OBS screenshots the
scene, and the Hold page shows that still under the live webcam strip, so
the host can turn the item without viewers watching it spin. `release` fades
back to the live camera. **step** is the item sequence on one key (Space on
the pager): wide (item) -> close-up -> hold, turn the item -> wide again,
and so on. From a hold it releases to the *other* camera; from any
non-camera page it starts at item.

`show_flow.py` and `obsctl.py` need only the standard library; the web app
needs FastAPI, which is in the repo venv (`/home/russ/EBAYBIZ/.venv`), not
the system Python: `python -m lib.cli serve` under system Python fails.

`setup` builds the show's pages in the `eBay Live` collection (a fourth,
**desktop** = scene `Desktop with Face Cam`, an app window such as Claude on
white under the webcam, comes with `--desktop-window "<title>"`; `standin
on|off` swaps the D5100 for a white panel when its battery is dead, and a
later `setup` leaves it as it was). `--window` must name a window that is
still open: OBS's window list keeps closed ones, and setup refuses them.
**webcam** (scene `Webcam Only`) is the fallback page: the webcam alone,
cover-cropped to the top 1080x1350 of the dark background, with the mic. It
never touches the D5100, so it's where to go if the camera dies mid-show.
**intro** (scene `Intro`) is the pre-show card: `--intro-text`, default
"Starting soon", on a dark background with no cameras. **item** (scene
`eBay Main`) is the D5100 full frame (rotated 270 for the side-mounted
camera) with the webcam full width across the top. **next** (scene `Up Next`)
is the pause / next-item card: a desktop window centered on the dark
background, under the same webcam strip. `page item` refocuses the D5100
while the current page is still on air, starting the feed if it's off, then
fades to the item view. `next` and `back` still work as aliases of
`page next` and `page item`.

Up Next shows whatever its window shows *at that moment*, and a browser
window drifts: the store page at setup can be Twitch (a feedback loop),
Stream manager or My eBay an hour later. So before Up Next goes on air, the
pager reads the window's live title and shows the window only if it is on
the allowlist `show_flow.SAFE_WINDOW` (an eBay listing, search or store page,
"... | eBay"; "Pop's Games"; "popsgames") and not vetoed by
`show_flow.PRIVATE_WINDOW` (Twitch, eBay Live, Seller Hub, My eBay, mail,
private browsing, orders, terminals, ...). Anything else, including a closed
window, a full-screen desktop capture or a non-eBay site, is hidden: viewers
get the dark card and the webcam strip. Keep the next lot's page in a window
of its own. Up Next is the only page that ever shows that window; every
other page (and `setup`) only hides it.

The local web app (`/home/russ/EBAYBIZ/.venv/bin/python -m lib.cli serve`,
the repo venv) has a control page at
http://localhost:8770/pager: one button per page, Hold and Step buttons, a
Refocus button, a TG-7 feed button, the LIVE timer, both feeds' status, and
keys 1-6 (intro, item, next, desktop, webcam, close-up), H (hold/release),
Space (step) and R. The same pager is served as JSON for other
pages or agents:
`GET /api/pager`, `POST /api/pager {"page": "item"}`, `POST
/api/pager/refocus`, `POST /api/pager/hold {"on": true}`, `POST
/api/pager/step`, `POST /api/pager/tg7 {"action": "start"}` and `POST
/api/pager/stream {"dest": "twitch", "action": "start"}`. The page has Start/Stop buttons for eBay and Twitch,
each with a confirmation. `webapp/pager.py` documents the JSON and error codes.
`show_flow.py page|status|refocus --json` prints the same objects.

**Verify:** `python tools/live/obsctl.py status` prints the OBS version and
canvas; `python tools/live/obsctl.py shot program -o /tmp/p.jpg` writes the
program picture.

Measured 2026-10-15: a refocus costs about 4.6 s with the D5 picture frozen
(stop 0.5 s, autofocus 1.4 s, live view restart 2.7 s), so `back` and
`refocus` cover it with Up Next. Live-view brightness on the D5100 is
automatic: ISO and shutter only change stills, so add light for a cleaner
stream.

## Open items

- TG-7 HDMI: buy a capture stick + micro-HDMI cable; confirm INFO hides the
  overlay on the HDMI feed and how long the battery lasts on HDMI.
- TG-7 Wi-Fi: **user** — the one-time `sudo` for /dev/video11
  (`tg7cam --help-setup`), then `show_flow.py setup`, to move the TG-7 off
  UDP onto a v4l2 source (lower latency, and `reconnect` can reopen it).
- The D5100 refocus could hide behind `hold` instead of Up Next (viewers
  would see the item, frozen, instead of the card).
- Optional: RM-WR1 + USB relay across its button for scripted AF/shoot over
  Bluetooth.
- D5100 refocus in about 1 s: hold one libgphoto2 session (python-gphoto2)
  that pulls live-view frames and triggers AF, instead of restarting
  `gphoto2 --capture-movie`.
