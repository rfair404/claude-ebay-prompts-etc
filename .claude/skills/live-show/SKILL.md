---
name: live-show
description: Run the eBay Live / Twitch show from the shell with no OBS GUI — launch OBS headless, start the Nikon D5100 feed, go live, switch between the Product view (D5100 + webcam) and the Up Next view (desktop window + webcam), refocus the D5100 hidden from viewers, and stop. Use when the user asks to start/stop the stream, go live, switch camera views, show what's up next, swap items, or refocus the camera.
---

# Live show: stream and camera views

You are the show's director. Everything runs through two scripts, on the
machine set up per `docs/live-streaming-setup.md` (Flatpak OBS, profiles
`Ebay` and `Untitled`, scene collection `eBay Live`, D5100 on /dev/video10).

- `tools/live/obsctl.py` — raw OBS remote (status, screenshots, any request).
- `tools/live/show_flow.py` — the show: looks, D5 feed, refocus, go live.

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
python tools/live/show_flow.py setup --window "<title>" # build Product + Up Next looks
python tools/live/show_flow.py status
```

`--window` picks the desktop window shown in Up Next by title substring
(`obsctl.py call GetInputPropertiesListPropertyItems '{"inputName":"Window Capture (Xcomposite)","propertyName":"capture_window"}'`
lists them). Avoid windows that could show private things (email, social
media, terminals) and the stream's own dashboard (it feeds back into itself).

Then, after the user says yes:

```bash
python tools/live/show_flow.py golive ebay --confirm    # or: golive twitch --confirm
```

It prints kbps and skipped frames after 5 s. Healthy is roughly 3000 kbps
and 0 skipped.

## During the show: switching views

| User says | Run |
|---|---|
| "next item", "swap", "show what's coming up" | `show_flow.py next` |
| "back to the product", "ready" | `show_flow.py back` |
| "refocus", "it's blurry" | `show_flow.py refocus` |

- `next` fades (300 ms) to Up Next while the user changes the item.
- `back` refocuses the D5100 *while Up Next is still on air* (about 5 s:
  live view stops, autofocus, restarts), then fades to Product. Viewers never
  see the freeze.
- `refocus` on the Product view hides itself behind Up Next for those ~5 s.
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
python tools/live/show_flow.py stop
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
- The Twitch profile (`Untitled`) is 16:9 by default. `golive twitch` switches
  it to the vertical 1080x1920 canvas.
