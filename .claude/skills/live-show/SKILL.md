---
name: live-show
description: Run the eBay Live / Twitch show from the shell with no OBS GUI — launch OBS headless, start the Nikon D5100 feed, go live, page between the show's views (intro pre-show card, item view = D5100 + webcam, next/pause view = desktop window + webcam, desktop = an app window like Claude + webcam, multi = D5100 + webcam + desktop window at once, TG-7 still = a TG-7 photo on air while the D5100 is adjusted), refocus the D5100 hidden from viewers, and stop. Use when the user asks to start/stop the stream, go live, switch camera views, show what's up next, swap items, or refocus the camera.
---

# Live show: stream and camera views

You are the show's director. Everything runs through two scripts, on the
machine set up per `docs/live-streaming-setup.md` (Flatpak OBS, profiles
`Ebay`, scene collection `eBay Live`, D5100 on /dev/video10).

- `tools/live/obsctl.py` — raw OBS remote (status, screenshots, any request).
- `tools/live/show_flow.py` — the show: pages (intro / item / next), D5 feed, refocus, go live.

Run them from the repo root. They talk to OBS on 127.0.0.1:4455 and to the
camera over USB, so in a sandboxed session they need the sandbox off.

## Rules

- **Going live is public. Ask the user first, every time**, naming the
  destination (eBay or Twitch). Only then pass `--confirm`.
- On eBay, streaming only sends video. The show starts when **the user** clicks
  *Start event* in Stream manager. Don't automate that click.
- Never print or type a stream key or stream URL. `obsctl.py call` redacts
  them; don't work around that. The user pastes keys into OBS themselves.
- eBay draws the lot overlay. Leave the `Live Overlay` source hidden unless asked.
- Check your work by looking: `obsctl.py shot program -o <file> --width 540`,
  then Read the image. Don't report a view change you haven't seen.

## Start of show

In this order:

```bash
python tools/live/obsctl.py launch                    # OBS, no window (no-op if running)
python tools/live/show_flow.py feed start              # D5100 live view -> OBS (runs tools/d5100_webcam.sh)
python tools/live/show_flow.py feed status             # "running"; "running, no frames for Ns" = camera not sending
python tools/live/show_flow.py setup --window "<title>" # build the intro, item, next, webcam and TG-7 still pages
python tools/live/show_flow.py watch start   # black panel over any camera that drops
python tools/live/show_flow.py standin on              # only if the D5100 is out; setup keeps this state
python tools/live/show_flow.py page intro              # pre-show card while you get ready
python tools/live/show_flow.py status
```

The web pager needs FastAPI, which only the repo venv has:
`/home/russ/EBAYBIZ/.venv/bin/python -m lib.cli serve` (system `python -m
lib.cli serve` fails). `show_flow.py` itself runs on any Python 3.

`setup --intro-text "..."` changes the pre-show card's text (default
"Starting soon").

`--window` picks the desktop window shown in Up Next by title substring
(`obsctl.py call GetInputPropertiesListPropertyItems '{"inputName":"Window Capture (Xcomposite)","propertyName":"capture_window"}'`
lists them, but that list keeps closed windows; setup refuses one that has
closed). Up Next only shows a window whose live title is on the allowlist
(`show_flow.SAFE_WINDOW`: an eBay listing/store page "... | eBay", "Pop's
Games", "popsgames"), so pick the next lot's eBay page; setup warns if the
window it picked will be hidden.

Then, after the user says yes:

```bash
python tools/live/show_flow.py golive ebay --confirm    # and/or: golive twitch --confirm
```

It prints kbps (and skipped frames for eBay) after 5 s. Healthy is roughly
3000 kbps and 0 skipped. eBay and Twitch are independent: both can run, and
`stop ebay` / `stop twitch` end one. Each start is its own yes from the
user. Twitch needs a one-time `twitch-setup` plus the user pasting the
Twitch URL into OBS (docs/live-streaming-setup.md step 2); `golive twitch`
says so if that's missing. After `twitch-setup`, OBS's **Record is Twitch**
in the `Ebay` profile: Start Recording is a public Twitch broadcast. Never
start a recording to "save a copy". To record to a file, with nothing live,
set OBS Settings > Output > Recording > Type back to Standard (the doc has
the `obsctl.py call` for it); `twitch-setup` switches back.

## During the show: the pager

One command puts any page on air:
`show_flow.py page intro|item|closeup|next|desktop|webcam|multi`.

| User says | Run |
|---|---|
| "intro", "starting soon", "pre-show" | `show_flow.py page intro` |
| "next item", "swap", "pause", "show what's coming up" | `show_flow.py page next` |
| "back to the product", "ready", "item view" | `show_flow.py page item` |
| "desktop", "show my screen", "face cam view" | `show_flow.py page desktop` |
| "everything at once", "both cameras and the screen", "multi view" | `show_flow.py page multi` |
| "camera's dead", "just the webcam", "fallback" | `show_flow.py page webcam` |
| "TG-7", "show it close", "cover the camera while I fix it" | `show_flow.py still` (`--refocus`: and refocus the D5 behind it) |
| "next side", "turn it", "step" | `show_flow.py step` (item -> still -> item -> ...) |
| "refocus", "it's blurry" | `show_flow.py refocus` |
| "too orange", "too blue", "white balance", "set WB to daylight" | `show_flow.py wb` (read) / `show_flow.py wb daylight` |
| "camera's black", "picture froze" | `show_flow.py reconnect` (or `reconnect d5` / `reconnect webcam`) |
| "watch the cameras", "black out a dead camera", "is the camera up?" | `show_flow.py watch start` / `watch status` / `watch stop` |
| "battery's dead", "use the stand-in" / "camera's back" | `show_flow.py standin on` / `standin off` |
| "music", "start the music" / "kill the music" | `show_music.py on` / `show_music.py off` |
| "folk music", "switch to lo-fi" | `show_music.py use folk` / `show_music.py use lofi` |

- `page next` fades (300 ms) to Up Next while the user changes the item.
- Every `page` is one scene switch; it never refocuses or waits for the
  camera. `page item` fades straight to the item view with whatever the D5
  shows, so after an item swap run `refocus` once the item is on the podium.
- `refocus` takes about 5 s (live view stops, autofocus, restarts). On the
  item view it hides behind Up Next for those seconds, then fades back,
  unless the user asked for a page meanwhile (that page stays). A second
  refocus while one runs is refused (`Busy`), not queued. If it fails, the
  feed is left running and the error starts with `refocus failed:`.
- `still` takes a full-size TG-7 photo (the page on air stays while the
  TG-7 shoots, a few seconds over Wi-Fi), then fades to the TG-7 still: the
  photo under the live webcam strip. It stays until `page item` or `step`
  (no timer). With it on air, `refocus`, `wb` and D5 stills run behind it
  (no Up Next) and leave it up, so adjust the D5 there. A TG-7 failure (no
  Wi-Fi, focus didn't lock) leaves the page on air and says why. With a
  photo shoot open the frame lands in its item and log (`shoot.py`), else in
  `inventory/_live/stills/<date>/`. `step` goes item -> still -> item; the
  live TG-7 close-up (`tg7 start`, `page closeup`) and `hold` are manual
  fallbacks a normal show doesn't need.
- `wb [PRESET]` reads or sets the D5100's white balance: `auto`,
  `daylight`, `cloudy`, `shade`, `tungsten` (or `incandescent`),
  `fluorescent`, `flash`, or a label the camera lists (`wb` alone prints
  them). It needs the USB connection, so it freezes the D5 like a refocus
  (5–12 s, hidden behind Up Next on item/multi), takes the same lock and
  refuses while the stand-in is on. D5100 only; the TG-7 isn't touched.
  Confirmed on hardware: the live-view feed follows it, not just stills
  (2026-10-22: red/blue 1.13 Auto, 1.04 Tungsten, 1.13 Auto again).
- `page item --refocus` refocuses first, behind the current page, then
  switches (fails fast, staying put, if the camera isn't on USB).
- `reconnect` makes OBS reopen the D5 and the webcam (one or both); use it
  when a camera's picture is black or frozen but `feed status` says
  `running`. Viewers see a blink of black on that camera.
- Before Up Next goes on air the pager checks the captured window's *live*
  title and shows the window only if it's on the allowlist (a public eBay
  page or Pop's Games) and not vetoed (Twitch, eBay Live, Seller Hub, My
  eBay, mail, terminals, ...); otherwise card + webcam only. The result
  says `window hidden`; tell the user to open the next lot's eBay page in a
  window of its own and re-run `setup --window`. Up Next is the only page
  that ever shows that window; every other page hides it.
- `watch start` runs the camera watch in the background. Every 2 seconds it
  looks at the D5100, the webcam and the TG-7; one that stays bad for 3
  looks (feed stopped, no frames, no picture or a frozen one in OBS) gets a
  black panel over its slot on every page that shows it, and the panel comes
  off after 5 good looks. `watch status` (and `status`) says each camera's
  state and why; a `page` onto a blacked camera prints `black in place of:`.
  It skips the D5 during a refocus, WB change or still and while the
  stand-in is on. The panels are only on while the watch runs: `watch stop`
  clears them. The pager page shows a chip per camera and has a Start/Stop
  camera watch button.
- `page webcam` (scene `Webcam Only`) is the fallback: the webcam alone with
  the mic. It never touches the D5100, so use it if the camera dies mid-show.
- `next` / `back` are aliases of `page next` / `page item`.
- `show_music.py on|off` fades the CC0 background music in or out over 2 s
  (`off` mutes and pauses it). It's the only music allowed on the show: never
  play anything else (eBay Live only allows music we hold rights to). If it
  says to run `setup`, run `show_music.py setup` (and `build <preset>` first if
  the file is missing); docs/live-streaming-setup.md section 7. `use lofi|folk`
  switches the mix with the same fades; the pager has a button per preset.
- `standin on` puts a white panel in the item view in place of the D5100;
  while it's on, `page item --refocus` skips the refocus and `refocus` refuses. The
  pager page has the same switch, and it stays on through reloads (it lives
  in OBS).
- `page desktop` shows the window picked with `setup --desktop-window
  "<title>" [--desktop-crop-left <px>]` under the webcam. The privacy guard
  doesn't check it (the host chose it), so remind the user that whatever is
  in that window is on air.
- `page multi` (scene `Multi View`) is all three at once: the D5100 (left)
  and the webcam (right) side by side in the top 1080x810, the same desktop
  window as `page desktop` full width under them. `setup` builds it
  together with the desktop page, so it needs `--desktop-window` once too;
  same privacy note. `refocus` on it hides behind Up Next like the item
  view, and the stand-in leaves its D5 slot dark.
- Web: the local app (`/home/russ/EBAYBIZ/.venv/bin/python -m lib.cli
  serve`) has a control-room screen at http://localhost:8770/pager (preview
  and program monitors with a transition bar: a tile click or key 1-7
  lines up intro/item/next/desktop/webcam/close-up/multi on preview, Enter
  takes it with the chosen transition, Shift+Enter cuts, Shift+1-7 cut
  straight to air; T TG-7 still, Shift+T still + D5 refocus, Space step;
  R refocus, C reconnect
  cameras, stream and OBS telemetry, the lineup with `[` / `]`, an event
  log) and serves the same pager at `/api/pager` (`GET` state + `health`, `POST
  {"page": ...}`, `POST /api/pager/refocus`, `POST /api/pager/reconnect`).
  A switch made there shows up in `status` here, and vice versa.
- "autofocus couldn't lock" means the center AF point sees nothing with
  contrast, usually an empty podium. Focus stays where it was. Ask the user to
  center the item and run `refocus` again.

Reframing (digital zoom/pan of the D5 shot): set the `Nikon D5100` item's
transform in `eBay Main (camera 1)` with `obsctl.py call SetSceneItemTransform`. Keep
`rotation` 270 and `cropToBounds` true. Scale the bounds (base 1920x1080) by
the zoom and shift the position so the target point lands at canvas center
(540, 960). Ease it over ~3 s in small steps rather than jumping. The live view
is only 640x426, so anything past ~2.5x mostly enlarges blur. For tighter
shots, suggest the user zoom the lens or bring the item closer.

## End of show

```bash
python tools/live/show_flow.py stop           # both destinations
python tools/live/show_flow.py feed stop     # frees the camera for stills/settings
python tools/live/obsctl.py quit             # optional; refuses while live/recording
```

## Troubleshooting

- `can't reach obs-websocket`: OBS isn't running (`obsctl.py launch`), or its
  WebSocket server is off (OBS Tools menu).
- D5 black or frozen: `show_flow.py feed status`. `running, no frames for
  Ns` means the loop is alive but the camera sends nothing: check that it is
  on, awake, plugged in and in M or A mode. Meanwhile `page webcam`. If it
  says `running` but OBS's picture is still black or frozen, `reconnect d5`. The feed script restarts itself if live view drops. Log:
  `$TMPDIR/d5100_feed.log`.
- A camera slot went black: `watch status` says why; the watch reopens it in
  OBS when the device has frames; `reconnect` or `page webcam` meanwhile.
  Log: `$TMPDIR/show_flow_watch.log`.
- D5 reason `clock reset (camera said <date>)`: the feed sets the camera's
  clock on every restart, so frames should return on their own. See
  docs/live-streaming-setup.md section 4, "Clock".
- gphoto2 commands (ISO, shutter, stills) need the feed stopped first. The
  camera takes one USB connection. Note that live-view brightness on the D5100
  is automatic: ISO and shutter only change stills, and more light is what
  helps the stream. `show_flow.py wb` handles the stop and restart itself.
- `D5100 not found on USB` usually means the camera went to sleep (it drops
  off `lsusb`): half-press the shutter to wake it, then retry.
- Twitch runs on OBS's recording output, so `obsctl.py quit` refuses while
  it's live, and there's no local recording during a Twitch stream.
