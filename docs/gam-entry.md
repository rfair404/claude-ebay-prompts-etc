---
version: 1.0.0
updated: 2026-11-19
---

# GAM item entry (Greensboro Antique Mall portal)

Mall-booth items are queued on the portal from green's background Chrome, so
nothing opens on Russell's screen. Same arrangement as the comp search
(`lib/ebay_sold_browse.py --headless`).

    ssh -N -L 127.0.0.1:9223:127.0.0.1:9223 green      # tunnel, once per session
    python tools/gam_entry.py --all --dry               # show the rows
    python tools/gam_entry.py inventory/<item> ...      # fill the form, queue the item

The tool saves each item to the portal's "Items To Be Entered" list and
**never presses Submit**. Russell presses Submit in the portal.

## What runs where

- `chromium-gam.service` on green: Chrome on the shared virtual display
  (`xvfb-comps.service`, `:99`), profile `/mnt/extra/chromium-gam`, debug port
  127.0.0.1:9223. Enabled at login. Separate from the comps Chrome (9222).
- `meta.channel: mall` marks a draft as a booth item. Fields come from the
  draft (title, price, quantity, brand, color, UPC, condition). Category is
  filled only when the draft has `meta.gam_category`.

## Signing in again

The portal login is stored in that profile and survived a service restart and a
reboot of green (2026-11-19). It can still expire. When `gam_entry` says the
portal is signed out, Russell signs in himself; Claude never types the password.

1. Stop the service on green, then open the profile on Russell's screen by
   forwarding his X socket (ssh `-R` of `/tmp/.X11-unix/X0` to a socket on
   green, `DISPLAY=unix:<that socket>`), with Chrome launched against
   `/mnt/extra/chromium-gam` and `--remote-debugging-port=9223`.
2. Russell signs in and waits for the dashboard.
3. Quit Chrome (SIGTERM, so cookies reach disk) and start
   `chromium-gam.service`. Check that `/dashboard` loads signed in before
   calling it done.

Quitting Chrome with SIGKILL, or before the dashboard loads, loses the login.
