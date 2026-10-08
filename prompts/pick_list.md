# PICK LIST — answering "give me / print today's pick list"

Obeys [`_shared.md`](_shared.md) house style.

**Output:** one clickable link per shipment, each one a printable sheet served
by the local app. Never just the terminal report, and never a file path.

Applies to any request for the pick list, the packing list, or what to ship
today. Asking for it means "give me the sheets to print". It does not mean
"summarise the queue".

---

## Steps

1. **Run from the main checkout, not a worktree.** The app serves sheets out
   of `<repo>/pick_lists/.served/`. That folder is not linked into worktrees,
   so a sheet published from a worktree gives a link that 404s. The
   idempotency state (`.pick_list_state*.json`) also lives only in the main
   checkout.

2. **Make sure the app is up on 127.0.0.1:8770.**

       curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8770/healthz

   If it isn't, start it with `preview_start {name: "app"}`
   (`.claude/launch.json` runs `.venv/bin/python -m lib.cli serve`). Fall back
   to running that command in the background.

3. **Render and publish every shipment, all stores:**

       .venv/bin/python tools/pick_list.py --poll --all-stores

   The sandbox needs `api.ebay.com` in `allowed_domains`. `--poll` renders the
   buyer-safe HTML (`tools/pick_list_html.py`) for each new order, drops it in
   the item's `inventory/` folder, publishes it, and prints
   `<order-id>[ + <order-id>]  http://127.0.0.1:8770/pick/<token>`. It prints
   one line per box. Orders going to the same buyer at the same address share
   a sheet.
   Orders printed on an earlier poll still get their line, and an expired link
   is republished. So re-running it is safe and always gives the full list.
   Never add `--print`, which sends the job to a printer without being asked.

4. **Reply with the links.** Write one markdown link per shipment, labelled
   with what's in the box so the person knows which sheet is which:

       - [L.L.Bean cable knit sweater (order 04-15279-11245)](http://127.0.0.1:8770/pick/<token>), ship by 10-09

   Get the item title and ship-by date from the plain report
   (`tools/pick_list.py --store <name>`). Keep the street address out of the
   chat reply. The sheet already carries what the picker needs.

## Edge cases

- **Nothing awaiting shipment** in any store: say so in one line. No links.
- **One order needs a sheet on its own** (re-print, or a specific id):
  `.venv/bin/python tools/pick_list_html.py <order-id> [--store NAME]` prints
  `[OK] <url>`.
- **The tool prints the `SERVE_HINT` warning** (the app isn't running): start
  it (step 2) and give the same links. They work as soon as it is up.
- **The app can't be started:** give the links anyway, plus the one command
  that starts the app. Don't fall back to `--local-only` paths unless the
  person asks for files.
