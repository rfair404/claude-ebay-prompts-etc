# PICK LIST — answering "give me / print today's pick list"

Obeys [`_shared.md`](_shared.md) house style.

**Output:** one clickable **R2 link** per order. Each link is a printable
sheet uploaded to the offsite bucket (`lib/offsite.py`) and presigned for 7
days. Never give the terminal report, a file path, or a
`http://127.0.0.1:8770/...` link. The local app is only reachable from this
machine, and the person wants links that open anywhere (user decision,
2026-10-10).

Applies to any request for the pick list, the packing list, or what to ship
today. Asking for it means "give me the sheets to print". It does not mean
"summarise the queue".

---

## Steps

1. **Run from the main checkout, not a worktree.** The rendered sheets land
   in `<repo>/pick_lists/.served/`, which is not linked into worktrees. The
   idempotency state (`.pick_list_state*.json`) also lives only in the main
   checkout.

2. **Render every shipment, all stores:**

       .venv/bin/python tools/pick_list.py --poll --all-stores

   The sandbox needs `api.ebay.com` in `allowed_domains`. For each box it
   prints `<order-id>[ + <order-id>]  http://127.0.0.1:8770/pick/<token>`. The
   rendered sheet is `pick_lists/.served/<token>.html`. Orders for the same
   buyer at the same address share one sheet and one line.
   Orders printed on an earlier poll still get their line, so re-running it is
   safe and always gives the full list. Ignore the `SERVE_HINT` warning about
   the local app not running, because you don't need the app for R2 links.
   Never add `--print`, which sends the job to a printer without being asked.

3. **Stage each sheet under `inventory/`.** `offsite share` only uploads
   listing data that the offsite include globs cover, and `pick_lists/` isn't
   one of them. Copy each line's sheet to
   `inventory/_pick_lists/pick_<order-id>.html`. For a shared-box line, join
   the ids with `+`, as in `pick_<id1>+<id2>.html`.

4. **Upload to R2 and get the links, all at once:**

       .venv/bin/python -m lib.cli offsite share inventory/_pick_lists/pick_<id>.html [...]

   It prints one presigned URL per file, in argument order. The sandbox needs
   the R2 endpoint host from `config.yaml` → `offsite.endpoint` in
   `allowed_domains`. Links expire after 7 days. Re-running steps 2–4 gives
   fresh links.

5. **Reply with the links.** Write one markdown link per order. Label each
   one with what's in the box so the person knows which sheet is which:

       - [L.L.Bean cable knit sweater (order 04-15279-11245)](https://<r2 presigned url>), ship by 10-09

   For a shared box, give one link labelled with every order id in it. Get the
   item title and ship-by date from `pick_lists/pick_<order-id>.txt`, or from
   the plain report (`tools/pick_list.py --store <name>`). Keep the buyer's
   name and address out of the chat reply, because the sheet already carries
   what the picker needs.

## Edge cases

- **Nothing awaiting shipment** in any store: say so in one line. No links.
- **One order needs a sheet on its own** (a re-print, or a specific id): run
  `.venv/bin/python tools/pick_list_html.py <order-id> [--store NAME] --out
  inventory/_pick_lists/pick_<order-id>.html`. Then do step 4 for that file.
- **The upload fails or is blocked:** say so plainly, with the error. Name the
  orders and their sheet files, and give the one `offsite share` command that
  would produce the links. Don't fall back to `127.0.0.1` links.
