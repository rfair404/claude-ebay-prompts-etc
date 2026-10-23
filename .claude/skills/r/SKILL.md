---
name: r
description: Short for "run". Russell types /r plus a few plain words and you run the matching job without asking. "camera photos" pulls new TG-7 photos off the card over USB, or Wi-Fi when it is not on USB. Anything else is a RUN.md pipeline request (a shoot name, a mode or a phase). Use when Russell types /r.
argument-hint: "camera photos | <shoot name> | <phase> <shoot name>"
---

# /r — run

Typing `/r` is the ask, so run it without confirming first. Match the words
after `/r` to a row below. Russell types as little as he can, so read loose
wording generously ("cam pics", "tg7 photos", "get the photos" all mean
camera photos).

| Russell types | What it means | Run |
|---|---|---|
| `camera photos` | Copy new TG-7 photos (JPG + ORF) off its card to `~/Pictures/TG-7`, over USB (Wi-Fi if not on USB) | `.claude/skills/r/tg7_pull.sh` |
| anything else | A pipeline run: a shoot name, `plan`/`list`/`full`, or one phase | Follow [`RUN.md`](../../../RUN.md), "Invocation" |

## Camera photos

    .claude/skills/r/tg7_pull.sh

Run it with the sandbox off (the camera's Wi-Fi at 192.168.0.10, USB mounts).
The photos always go to `~/Pictures/TG-7`, never into an `inventory/` item:
the card holds every item's photos, and per-item stills already come in
through the `photo-shoot` skill.

The script tries USB first: the TG-7 in Storage mode as a mounted drive (it
mounts the `OM SYSTEM` card if it is plugged in but not mounted yet), then in
MTP mode through `gphoto2`, picked out by port so the D5100 is left alone.
Only when the TG-7 isn't on USB does it fall back to the camera's Wi-Fi at
192.168.0.10 (`tools/live/tg7cam sync`), and on air (`inventory/_live/ON_AIR`
exists) it skips Wi-Fi and says so. USB comes first because the laptop has
one Wi-Fi card, so a Wi-Fi pull takes it off the home network, and the
camera's Wi-Fi is usually off. Only photos that aren't already in the folder
are copied. Last, it hops the laptop's Wi-Fi onto the camera's (below). The
first line says which way it went (`VIA usb-storage …`, `VIA usb-mtp …`,
`VIA wifi`, `HOP started …`).

Reply in one or two lines: how many new photos, where they went, and which
way. If it says `NOT FOUND`, give Russell the one hands-on step: plug the
TG-7 in by USB and pick **Storage** on the camera's screen. If it also said
`ON AIR`, make that step "after the show", and don't ask him to plug in
cameras mid-show.

### Wi-Fi hop (`HOP started`)

When the camera isn't on USB and doesn't answer, but its saved Wi-Fi profile
(`TG-7-…` in NetworkManager) is in range, the script hands off to
`tg7_wifi_hop.sh` and returns at once with
`HOP started; status at <path>`. The laptop has one Wi-Fi adapter, so the
hop drops the home Wi-Fi, joins the camera, syncs, and always rejoins the home
Wi-Fi (on failure too, with retries, inside a 10-minute limit). It starts
after a 30-second pause so this turn can finish. Never on air.

**Claude is offline for the hop.** Do exactly this and nothing else:

1. Right away, start the wait as a background Bash call
   (`run_in_background: true`):

       .claude/skills/r/tg7_pull.sh --wait

2. End the turn with this one line to Russell: "Switching to the TG-7's
   Wi-Fi to pull photos; back online in about a minute."
3. Make no other tool call that needs the network until the wait finishes.
   It finishes when the status file is written, which is after the laptop
   is back on its own Wi-Fi.
4. Read its output: line 1 is `ok` or `failed: <reason>`, then
   `new: <n> photo(s) -> <dir>`, `network: …` and the log path. Reply as for
   any pull (`VIA wifi hop`), or with the reason if it failed, and carry on
   with the import. If `network:` says `NOT back on …`, say so first: the
   laptop needs its Wi-Fi picked from the menu.
