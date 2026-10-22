---
name: live-show
description: Run the eBay Live / Twitch show from the shell with no OBS GUI — launch OBS headless, start the Nikon D5100 feed, go live, page between the show's views (intro pre-show card, item view = D5100 + webcam, next/pause view = desktop window + webcam, desktop = an app window like Claude + webcam), refocus the D5100 hidden from viewers, and stop. Use when the user asks to start/stop the stream, go live, switch camera views, show what's up next, swap items, or refocus the camera.
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

```bash
python tools/live/obsctl.py launch                    # OBS, no window (no-op if running)
python tools/live/show_flow.py feed start              # D5100 live view -> OBS
python tools/live/show_flow.py setup --window "<title>" # build the intro, item and next pages
python tools/live/show_flow.py page intro              # pre-show card while you get ready
python tools/live/show_flow.py status
```

`setup --intro-text "..."` changes the pre-show card's text (default
"Starting soon").

`--window` picks the desktop window shown in Up Next by title substring
(`obsctl.py call GetInputPropertiesListPropertyItems '{"inputName":"Window Capture (Xcomposite)","propertyName":"capture_window"}'`
lists them). Avoid windows that could show private things (email, social
media, terminals) and the stream's own dashboard (it feeds back into itself).

Then, after the user says yes:

```bash
python tools/live/show_flow.py golive ebay --confirm    # and/or: golive twitch --confirm
```

It prints kbps (and skipped frames for eBay) after 5 s. Healthy is roughly
3000 kbps and 0 skipped. eBay and Twitch are independent: both can run, and
`stop ebay` / `stop twitch` end one. Each start is its own yes from the
user. Twitch needs a one-time `twitch-setup` plus the user pasting the
Twitch URL into OBS (docs/live-streaming-setup.md step 2); `golive twitch`
says so if that's missing.

## During the show: the pager

A show has four pages. One command puts any of them on air:
`show_flow.py page intro|item|next|desktop`.

| User says | Run |
|---|---|
| "intro", "starting soon", "pre-show" | `show_flow.py page intro` |
| "next item", "swap", "pause", "show what's coming up" | `show_flow.py page next` |
| "back to the product", "ready", "item view" | `show_flow.py page item` |
| "desktop", "show my screen", "face cam view" | `show_flow.py page desktop` |
| "refocus", "it's blurry" | `show_flow.py refocus` |
| "battery's dead", "use the stand-in" / "camera's back" | `show_flow.py standin on` / `standin off` |
| "music", "start the music" / "kill the music" | `show_music.py on` / `show_music.py off` |

- `page next` fades (300 ms) to Up Next while the user changes the item.
- `page item` refocuses the D5100 *while the current page is still on air*
  (about 5 s: live view stops, autofocus, restarts), then fades to the item
  view. Viewers never see the freeze. It starts the feed if it's off, and
  fails right away, staying on the current page, if the camera isn't on USB.
- `refocus` on the item view hides itself behind Up Next for those ~5 s.
- Before Up Next goes on air the pager checks the captured window's *live*
  title and hides the window (card + webcam only) if it looks private or is
  the stream itself (Twitch, eBay Live, mail, My eBay, terminals, ...). The
  result says `window hidden`; tell the user to open the next lot in a
  window of its own and re-run `setup --window`.
- `next` / `back` are aliases of `page next` / `page item`.
- `show_music.py on|off` fades the CC0 background music in or out over 2 s
  (`off` mutes and pauses it). It's the only music allowed on the show: never
  play anything else (eBay Live only allows music we hold rights to). If it
  says to run `setup`, run `show_music.py setup` (and `build` first if the
  file is missing); docs/live-streaming-setup.md section 7.
- `standin on` puts a white panel in the item view in place of the D5100;
  while it's on, `page item` skips the refocus and `refocus` refuses. The
  pager page has the same switch, and it stays on through reloads (it lives
  in OBS).
- `page desktop` shows the window picked with `setup --desktop-window
  "<title>" [--desktop-crop-left <px>]` under the webcam. The privacy guard
  doesn't check it (the host chose it), so remind the user that whatever is
  in that window is on air.
- Web: the local app (`python -m lib.cli serve`) has a button page at
  http://localhost:8770/pager (keys 1/2/3/R) and serves the same pager at
  `/api/pager` (`GET` state, `POST {"page": ...}`, `POST /api/pager/refocus`).
  A switch made there shows up in `status` here, and vice versa.
- "autofocus couldn't lock" means the center AF point sees nothing with
  contrast, usually an empty podium. Focus stays where it was. Ask the user to
  center the item and run `refocus` again.

Reframing (digital zoom/pan of the D5 shot): set the `Nikon D5100` item's
transform in `eBay Main` with `obsctl.py call SetSceneItemTransform`. Keep
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
- D5 black or frozen: `show_flow.py feed status`; check that the camera is on
  and in M or A mode. The feed script restarts itself if live view drops. Log:
  `$TMPDIR/d5100_feed.log`.
- gphoto2 commands (ISO, shutter, stills) need the feed stopped first. The
  camera takes one USB connection. Note that live-view brightness on the D5100
  is automatic: ISO and shutter only change stills, and more light is what
  helps the stream.
- Twitch runs on OBS's recording output, so `obsctl.py quit` refuses while
  it's live, and there's no local recording during a Twitch stream.
