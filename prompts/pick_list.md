# PICK LIST — answering "give me today's pick list"

Obeys [`_shared.md`](_shared.md) house style.

**Output:** one clickable **R2 link** per box (orders for the same buyer at
the same address share one sheet). Each link is a printable
sheet uploaded to the offsite bucket (`lib/offsite.py`) and presigned for 7
days. Never give the terminal report, a file path, or a
`http://127.0.0.1:8770/...` link. The local app is only reachable from this
machine, and the person wants links that open anywhere (user decision,
2026-10-10).

Applies to any request for the pick list, the packing list, or what to ship
today. Asking for it means "give me the sheets to print". It does not mean
"summarise the queue". To print them on paper, use the `/pl` skill.

---

## Run

    .claude/skills/pick-list/links.sh

This is the `pick-list` skill. It works from a worktree and does the whole
job: it polls every store from the main checkout (never with `--print`),
stages each box's sheet under `inventory/_pick_lists/`, uploads them with one
`offsite share` and prints one `LINK` line per box. The format of each line is
in the docstring of [`tools/pick_list_links.py`](../tools/pick_list_links.py).
Links last 7 days, and re-running gives fresh ones.

The sandbox needs `api.ebay.com` and the R2 endpoint host from `config.yaml`
→ `offsite.endpoint` in `allowed_domains`.

Reply as [`SKILL.md`](../.claude/skills/pick-list/SKILL.md) ("Reply") says.
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
