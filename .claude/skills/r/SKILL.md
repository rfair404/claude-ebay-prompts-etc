---
name: r
description: Short for "run". Russell types /r plus a few plain words and you run the matching job without asking. "camera photos" pulls new TG-7 photos off the card over Wi-Fi or USB. Anything else is a RUN.md pipeline request (a shoot name, a mode or a phase). Use when Russell types /r.
argument-hint: "camera photos | <shoot name> | <phase> <shoot name>"
---

# /r — run

Typing `/r` is the ask, so run it without confirming first. Match the words
after `/r` to a row below. Russell types as little as he can, so read loose
wording generously ("cam pics", "tg7 photos", "get the photos" all mean
camera photos).

| Russell types | What it means | Run |
|---|---|---|
| `camera photos` | Copy new TG-7 photos (JPG + ORF) off its card to `~/Pictures/TG-7`, over Wi-Fi or USB | `.claude/skills/r/tg7_pull.sh` |
| anything else | A pipeline run: a shoot name, `plan`/`list`/`full`, or one phase | Follow [`RUN.md`](../../../RUN.md), "Invocation" |

## Camera photos

    .claude/skills/r/tg7_pull.sh

Run it with the sandbox off (the camera's Wi-Fi at 192.168.0.10, USB mounts).
The photos always go to `~/Pictures/TG-7`, never into an `inventory/` item:
the card holds every item's photos, and per-item stills already come in
through the `photo-shoot` skill.

The script tries the camera's Wi-Fi first (`tools/live/tg7cam sync`), except
on air (`inventory/_live/ON_AIR` exists), when it skips Wi-Fi and says so.
Then it looks for the TG-7 on USB: in Storage mode as a mounted drive (it
mounts the `OM SYSTEM` card if it is plugged in but not mounted yet), in MTP
mode through `gphoto2`, picked out by port so the D5100 is left alone. Only
photos that aren't already in the folder are copied. The first line says
which way it went (`VIA wifi`, `VIA usb-storage …`, `VIA usb-mtp …`).

Reply in one or two lines: how many new photos, where they went, and which
way. If it says `NOT FOUND`, give Russell the one hands-on step: plug the
TG-7 in by USB and pick **Storage** on the camera's screen. If it also said
`ON AIR`, make that step "after the show", and don't ask him to plug in
cameras mid-show.
