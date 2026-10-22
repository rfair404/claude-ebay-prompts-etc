---
name: photo-shoot
description: Run an interactive product photo shoot with Russell, one item at a time. Russell puts each item on the podium and gives context; you drive the cameras (D5100 and TG-7 stills) and the pager, look at every frame, log what you see per item, suggest the next shot, and move to the next item when Russell says this one is done. Works on air (eBay Live / Twitch) or off air. Use when Russell says "let's shoot", "photo shoot", "next item", "capture", "take one", or "done for now" during a shoot.
---

# Interactive photo shoot

Russell is the photographer's hands and the source of context, placing and
turning the item and saying what it is. You run everything else: cameras,
pager, captures, the shoot log, and what to shoot next. The goal is speed.
Each item should take a few minutes, and moving to the next one should take
a single command.

Everything runs from the repo root through two scripts:

- `tools/live/shoot.py` keeps the shoot: folders, stills, the per-item log.
- `tools/live/show_flow.py` runs the pager and cameras. See the `live-show`
  skill for anything beyond what's here.

They talk to OBS (127.0.0.1:4455), the D5100 over USB and the TG-7 over its
Wi-Fi, so in a sandboxed session they need the sandbox off.

## Rules

- **Look only at the copies.** `capture` prints a `look:` path under the
  item's `.shoot/` folder (800 px). Read that. Never Read the full-size
  frame (`prompts/_shared.md`). `look` gives the on-air picture at 540 px.
- **Log what you see, not what you guess.** Write bullets about what the
  frame shows: the side, marks, labels, defects, and whether focus, glare
  and framing work. A guess is marked as one ("looks like a maker's stamp,
  not legible at this size"). Identification is IDENTIFY's job later. Don't
  name a maker, date or value the frame doesn't show, and don't state
  selling lore as fact.
- **Russell's context is logged as Russell's.** Pass it with
  `--note` or `log --by russell`, close to the words used.
- **Going live is public.** A streamed shoot starts only after Russell says
  yes, every time, per the `live-show` skill (`golive ... --confirm`).
  Nothing in `shoot.py` streams.
- **Keep your turns short.** Between shots, reply in one or two lines: what
  the frame shows and the next shot. Chain commands with `&&` so each step
  is one Bash call.

## Start of shoot

```bash
python tools/live/obsctl.py launch                  # no-op if OBS is up
python tools/live/show_flow.py feed start           # D5100 live view (the viewfinder)
# the TG-7 needs only its Wi-Fi joined for stills; `tg7 start` (live close-up) is a fallback
python tools/live/show_flow.py setup                # pages; add --window/--desktop-window as live-show says
python tools/live/shoot.py start [NAME]             # default shoot-YYYYMMDD; same NAME resumes
```

If Russell says where the batch came from (an estate, a sale, one house),
write it to `inventory/<shoot>/context.txt` in the format in
`lib/dir_context.py`. Every item in the shoot inherits it. A named person
goes on the `source:` line, which never reaches buyer copy.

**Off air**, OBS is just the viewfinder and the pages are only for you, so
skip the still page. **On air**, viewers see every page: Up Next while the
item changes, the item view while you shoot, and the TG-7 still while
Russell turns the item or the D5 is adjusted.

## Each item

1. **Russell puts it on the podium and says what it is.**
   ```bash
   python tools/live/shoot.py item [SLUG] --note "<what Russell said>" \
     && python tools/live/show_flow.py page item --refocus \
     && python tools/live/shoot.py look
   ```
   Read the look. Log a first impression, then give Russell a short shot
   list for this item: hero, the other sides, underside and marks, each
   detail or defect, a scale reference. The house target is 10 frames.
   Eight good frames beat twelve padded ones. If you don't know the slug
   yet, leave it off and run `shoot.py name <slug>` once you do.
2. **Each shot.** Say what to set up ("hero, front, fill the frame"), wait
   for Russell's go, then:
   ```bash
   python tools/live/shoot.py capture d5 --note "hero, front"
   ```
   Read the `look:` copy and log what it shows in one to three bullets
   (`shoot.py log "..."`). Say whether it's a keeper or needs a reshoot and
   why (soft, glare, cut off, backdrop not white). Then name the next shot.
   - `capture d5` is the full-size Nikon still. Live view pauses about
     5 to 8 s, hidden behind Up Next if the item view is on air.
     "Out of Focus" means autofocus found nothing with contrast: ask
     Russell to center the item, then retry.
   - `capture tg7` is the TG-7 still, with no pause. Use it for close-ups
     of marks, labels and defects. On air, use
     `show_flow.py still --note "base mark"` instead: the same capture into
     the open item and its log, and the photo goes on air. "focus didn't
     lock" means move it closer or add light, then retry.
   - On air, while Russell turns the item: `show_flow.py still` (or `step`:
     item -> still -> item). It covers the D5, so `show_flow.py refocus`
     there needs no Up Next; `page item` once the item is set.
3. **Russell calls it done for now** ("done", "next", "that's it for
   this one"):
   ```bash
   python tools/live/shoot.py done && python tools/live/show_flow.py page next
   ```
   Reply with the frame count and any shots still missing (marks,
   underside, a defect not yet framed). That's the reshoot list, and it
   never holds the item up. Then wait for the next item. Starting the next
   `item` closes an open one by itself, so "next one's up" can go straight
   to step 1.

`shoot.py status` shows the shoot, the open item, its frame count and the
last log lines. Use it to pick up after a break or a new session.

## End of shoot

```bash
python tools/live/shoot.py done
python tools/live/show_flow.py feed stop            # frees the D5100 for other gphoto2 work
```

Report the items, with frames per item and any reshoot list. Each item
folder `inventory/<shoot>/<NN-slug>/` is an ordinary shoot directory for the
pipeline (RUN.md: `plan` or `list`). Its `shoot_log.md` holds Russell's
context and your notes, so point IDENTIFY and INVESTIGATE at it. Handoffs:
the digital-tech runs PREP on the folders, and the photo-editor reviews the
frames.

## Troubleshooting

- `D5100 not found on USB`: the camera is off, asleep or unplugged. Use
  `page webcam` on air while Russell fixes it, or `capture tg7` meanwhile.
- `TG-7: ... not reachable`: the laptop isn't on the camera's Wi-Fi
  (`tools/live/tg7cam connect <SSID>`, Russell types the password) or the
  camera is off.
- `look` shows Up Next or the intro card: put a camera page up first
  (`page item`, or `still` for a TG-7 photo).
- `a D5100 refocus, WB change or still is already running`: the pager
  or another session is using the camera. Wait 5 to 10 s and retry.
