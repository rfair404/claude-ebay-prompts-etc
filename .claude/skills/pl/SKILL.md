---
name: pl
description: Print today's pick lists on paper. Polls every store for orders awaiting shipment and sends one sheet per box to CannonBW, named explicitly so the system default can be the label printer. Use when Russell types /pl or asks to print the pick lists.
---

# /pl — print today's pick lists

Typing `/pl` is the ask to print, so send the sheets to the printer without
confirming first. For R2 links instead of paper, use the `pick-list` skill
([`prompts/pick_list.md`](../../../prompts/pick_list.md)).

## Run

    .claude/skills/pl/print.sh

The script finds the main checkout itself, so it works from a worktree. It
runs `tools/pick_list_links.py --print` there, which polls every store,
renders each box's HTML sheet to PDF with headless Chromium (CUPS here has no
HTML filter) and sends it to CannonBW with `lp -d CannonBW`. The printer is
named explicitly, never the system default, because the default is the
QL-1100 label printer. Every box awaiting shipment prints, even ones polled
earlier, so running it twice prints twice. It prints one
`PRINTED <ids> | <title> | ship by <date>` or `FAILED <ids> (<why>)` line per
box, `HELD` and `NONE` lines as the links do, and a `DONE` line at the end
(formats in that tool's docstring). Allow about 20 seconds per sheet.

Sandbox: the poll needs `api.ebay.com` in `allowed_domains`.

## Reply

Keep it to one line per box: the item title and the ship-by date from its
`PRINTED` line. Leave out the buyer's name and address.

    Printed 2 sheets on CannonBW:
    - Vintage 12K gold filled cross pendant (01-15336-14111), ship by 10-21
    - L.L.Bean cable knit sweater (04-15279-11245), ship by 10-23

- **`NONE`** (nothing awaiting shipment): say so in one line.
- **A `FAILED` line:** say which orders, or the poll, and what the error
  was. If `lpstat -p CannonBW` shows the printer is off or out of
  paper, tell Russell that in one line, as the one hands-on step.
- **A `HELD` line** (orders held for an eBay Live window): pass it on in
  one line, because those boxes print after the window closes.
