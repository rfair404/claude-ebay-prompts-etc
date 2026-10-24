---
name: report-lot
description: Full account of one lot - an estate buy or a consignment intake. Lists every item the lot brought in (status, store, ask, sold, net kept) and adds a summary (paid, live asking, sold, net kept, recovered vs paid; for a consignment, what the client is owed), writes it to reports/lots/ as plain text and CSV, and replies with an R2 link. Prints on CannonBW only when Russell asks for paper. Use when Russell types /report-lot, or asks for the account, ledger, printout or "what came in" for an estate, a buy or a consignment lot.
---

# /report-lot: the full account of one lot

Reply with an R2 link, not paper. Print only when Russell's own words ask
for it ("print it", "printout", "on paper"); `/report-lot` alone is not a
print request. Pick lists (`/pl`) are the only thing that prints on
command. Plain text only, no PDF or HTML (Russell's rule for lot and
client lists).

## 1. Pick the lot

A lot is a folder under `inventory/` with a `context.txt`. Map what Russell
says to a folder yourself, without asking him:

- an estate or buy: look under `inventory/ES/` and `inventory/ESTATES/`, and
  match by the date in `acquired:` or the prose in its `context.txt`;
- a consignment: `inventory/CONSIGN-*`, matched by the intake date in the
  folder name or the `client:` code in its `context.txt`.

If two folders fit equally well, run both.

## 2. Refresh, then run

For a consignment, first bring its states up to date:

    python -m lib.cli consign sync <CONSIGN-folder> --apply

If sync stops on an `[X]` line, don't fix the ledger here. Run the report
anyway (its states are from the last good sync) and pass the `[X]` line on.

Then, for each lot:

    .claude/skills/report-lot/run.sh <lot-folder>

Add `--print` only when Russell asked for paper. The script runs
`tools/report_lot.py` in the main checkout, where the ledgers and
`config.yaml` live, so it works from a worktree. Run it with the Bash
sandbox off, because the sandbox blocks writes to `reports/` (and the CUPS
socket, with `--print`). It writes `reports/lots/<lot>-<date>.txt` and
`.csv`, prints the text to the terminal, then ends with a `WROTE` line (and
a `PRINTED request id is CannonBW-NN` line with `--print`).

Then share the .txt from the main checkout:

    .venv/bin/python -m lib.cli offsite share reports/lots/<lot>-<date>.txt

## 3. Which folders count (estates)

The tool's docstring has the rules. In short, an estate's items are its own
folder, the buckets nested under it, and any bucket elsewhere whose
`context.txt` says `part_of: <lot>`. A nested bucket that says `client:` is
a consignment and is left out. When an item from the buy is filed outside
the lot, add `part_of: <lot>` to its folder's `context.txt` and give its
cost share as `spend:`. Add a `note:` line when the share is partial, for
example a mixed-source lot. Do this without asking. Russell's own words
about where the item came from are the record.

## Reply

One short block per lot, with the R2 link:

    ES/<LOT>, $<paid> estate, <acquired>: <R2 link>
    <n> items: <n> live ($<ask> asking), <n> queued, <n> photos only. <n> sold, net kept $<kept>.

When it was printed, say `Printed on CannonBW (job <N>)` on the first line.
Don't offer to print.

For a consignment, give the line count by state, the estimate range and
what the client is owed (share plus buyouts). Add any sync `[X]` line, any
member noted as mixed-source, and any item that is `PHOTOS ONLY` while
others from the lot are live. Leave out client contact details.
