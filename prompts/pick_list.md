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

## Run

    .claude/skills/pick-list/links.sh

This is the `pick-list` skill ([`SKILL.md`](../.claude/skills/pick-list/SKILL.md)
has the reply format). It works from a worktree and does the whole job:

1. Runs `tools/pick_list.py --poll --all-stores` in the main checkout, where
   the sheets (`pick_lists/.served/`) and the poll state
   (`.pick_list_state*.json`) live. Never with `--print`. Orders for the same
   buyer at the same address share one sheet. Orders polled earlier still get
   their sheet, so re-running is safe and always gives the full list.
2. Copies each box's sheet to `inventory/_pick_lists/pick_<order-id>.html`
   (`pick_<id1>+<id2>.html` for a shared box), because `offsite share` only
   uploads files its include globs cover.
3. Uploads them all with one `.venv/bin/python -m lib.cli offsite share`,
   presigned for 7 days. Re-running gives fresh links.
4. Prints one `LINK <ids> <url> | <title> | ship by <date>` line per box,
   with the title and date read from `pick_lists/pick_<order-id>.txt`.

The sandbox needs `api.ebay.com` and the R2 endpoint host from `config.yaml`
→ `offsite.endpoint` in `allowed_domains`.

Reply with one markdown link per box, labelled with what's in it:

    - [L.L.Bean cable knit sweater (order 04-15279-11245)](https://<r2 presigned url>), ship by 10-09

Keep the buyer's name and address out of the chat reply, because the sheet
already carries what the picker needs.

## Edge cases

- **Nothing awaiting shipment** in any store: say so in one line. No links.
- **One order needs a sheet on its own** (a re-print, or a specific id): run
  `.venv/bin/python tools/pick_list_html.py <order-id> [--store NAME] --out
  inventory/_pick_lists/pick_<order-id>.html`, then
  `.venv/bin/python -m lib.cli offsite share` on that file.
- **The upload fails or is blocked:** say so plainly, with the error. Name the
  orders, and give the `RETRY` command the script printed. Don't fall back to
  `127.0.0.1` links.
- **Orders held for an eBay Live window:** the script prints a `HELD` line.
  Pass it on in one line; those boxes get sheets after the window closes.
