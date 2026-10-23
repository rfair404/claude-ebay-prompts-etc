---
name: pl
description: Print today's pick lists on paper. Polls every store for orders awaiting shipment and sends one sheet per box to the default printer (CannonBW). Use when Russell types /pl or asks to print the pick lists.
---

# /pl — print today's pick lists

Typing `/pl` is the ask to print, so send the sheets to the printer without
confirming first. For R2 links instead of paper, use the `pick-list` skill
([`prompts/pick_list.md`](../../../prompts/pick_list.md)).

## Run

    .claude/skills/pl/print.sh

The script finds the main checkout itself, so it works from a worktree. It
runs `tools/pick_list.py --poll --all-stores` there, renders each box's HTML
sheet to PDF with headless Chromium (CUPS here has no HTML filter) and sends
it to the default printer with `lp`. Every box awaiting shipment prints, even
ones polled earlier, so running it twice prints twice. It prints one
`PRINTED <ids>` or `FAILED <ids>` line per box and a `DONE` line at the end.
Allow about 20 seconds per sheet.

Sandbox: the poll needs `api.ebay.com` in `allowed_domains`.

## Reply

Keep it to one line per box: the item title and the ship-by date, both read
from `pick_lists/pick_<order-id>.txt` in the main checkout. Leave out the
buyer's name and address.

    Printed 2 sheets on CannonBW:
    - Vintage 12K gold filled cross pendant (01-15336-14111), ship by 10-21
    - L.L.Bean cable knit sweater (04-15279-11245), ship by 10-23

- **Nothing awaiting shipment:** say so in one line.
- **A `FAILED` line, or the poll errors:** say which orders and what the
  error was. If `lpstat -p` shows the printer is off or out of paper, tell
  Russell that in one line, as the one hands-on step.
- **Orders held for an eBay Live window:** the poll says so. Pass that on in
  one line, because those boxes print after the window closes.
