---
name: pick-list
description: Today's pick lists as links. Polls every store for orders awaiting shipment, uploads one sheet per box to R2 and replies with one link per box. Use when Russell asks for the pick list, the packing list, what ships today or what to pack, unless he asks to print them on paper (that is /pl).
---

# /pick-list — today's pick lists as R2 links

Asking for the pick list means "give me the sheets", not "summarise the
queue". Run the script without confirming first. To print on paper instead,
use `/pl`.

## Run

    .claude/skills/pick-list/links.sh

It works from a worktree. It runs `tools/pick_list.py --poll --all-stores` in
the main checkout (never `--print`), copies each box's sheet to
`inventory/_pick_lists/pick_<id>[+<id2>].html`, uploads them with one
`lib.cli offsite share`, and prints:

    LINK <ids> <url> | <title> | ship by <YYYY-MM-DD>
    HELD <n> order(s) from eBay Live show '<show>' held until <time> ...
    FAILED <ids>|poll (<reason>)
    RETRY <the offsite share command for sheets that didn't upload>
    NONE
    DONE links=N failed=N held=<orders held>

Links last 7 days. Running it again gives fresh links. Sandbox: needs
`api.ebay.com` and the R2 endpoint host (`*.r2.cloudflarestorage.com`) in
`allowed_domains`.

## Reply

One markdown link per box, labelled with the title and order id(s) from its
`LINK` line, then the ship-by date as MM-DD:

    Pick lists, 2 boxes:
    - [Vintage 12K gold filled cross pendant (01-15336-14111)](<url>), ship by 10-21
    - [L.L.Bean cable knit sweater + 1 more (04-15279-11245 + 04-15279-11246)](<url>), ship by 10-23

Never include the buyer's name or address, a `127.0.0.1` link, or a file
path.

- **`NONE`:** say nothing is awaiting shipment, in one line.
- **`FAILED` or `RETRY`:** give the links that worked, then name the orders
  that failed and the error, and give the `RETRY` command as one bash block.
  Don't fall back to `127.0.0.1` links.
- **`HELD`:** say in one line which show's orders are held and until when;
  those boxes get sheets after the window closes.
