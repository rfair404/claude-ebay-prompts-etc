---
name: lower
description: The listing manager inches live listings toward their Recommended price. Finds active listings in both stores whose price has not changed in more than 15 days, re-runs PRICE where the numbers are stale, proposes a modest whole-dollar cut that stays above Recommended, walks Russell through each proposal one yes/no at a time, and applies only the yeses. Use when Russell types /lower, or asks to "lower prices", "take the next markdown step", "what's due for a cut" or "work the aging list".
---

# /lower: the next price step, one listing at a time

Typing `/lower` is the ask, so start without confirming first. This is the
concrete form of the inventory manager's markdown plan
(`.claude/agents/inventory-manager.md`) and the listing manager's approved
markdowns (`.claude/agents/listing-manager.md` section 7). The old per-run
`reports/aging_price_cut_<date>_apply.py` scripts are retired; use `lower`.

Run everything from the main checkout (`cd /home/russ/EBAYBIZ`), never from a
worktree: it has the config, ledgers, `inventory/` and `reports/`.

Rules the tool enforces (do not work around them): cuts only on asks of $36+
(the $30-35.99 and $25-or-under bands are left alone and listed as held);
whole dollars; new ask never below Recommended plus a margin; gold and silver
never below melt on live spot, failing closed; the KBD gold hold (C019,
C020, C021); K01 consignment items are cut but noted, and never get ads;
default-store prices are delivered prices; Russell's hand prices and seasonal
holds come from `reports/lower/holds.csv`.

## 1. Scan (read-only)

    python -m lib.cli lower scan

This reads both stores, picks listings whose last price change is more than
15 days ago, and writes `reports/lower/<date>/queue.csv`, `queue.md` and
`review.html`. Status per row: PROPOSED, AT_TARGET (at Recommended, Russell's
call, no cut), STALE_PRICE (needs a fresh PRICE), HELD (with the reason) or
SKIPPED (not due yet). The last-change date comes from
`reports/lower/price_changes.csv`; with no entry it is the listing's start
date. On a fresh machine run `python -m lib.cli lower backfill` once.

## 2. Re-run PRICE where it is stale

For each STALE_PRICE row the scan queued (`rerun` column, at most 10 per
scan; PRICE on Opus is the costly step), dispatch the `price-researcher`
with the item's `shoot_dir`: run `prompts/price.md` fresh, write `price.txt`,
and change no title, since Russell's provenance claims win. Run the
independent ones together. Then run the scan again and say how many re-runs
were spent and how many remain queued. A price.txt newer than 15 days is
reused, never re-run.

## 3. Walk Russell through the proposals

If nothing is PROPOSED or AT_TARGET, say so in one line (and how many are
stale and queued) and stop.

Otherwise share the review page once:

    python -m lib.cli offsite share reports/lower/<date>/review.html

Then, per PROPOSED row, in turn: build one photo sheet with the title and
the new ask (`python tools/photo_sheet.py --title "<name>" --price '<$new>' ...`
as in `/yn`), send it, and call `AskUserQuestion` with one question: "Lower
<name> from $X to $Y? (Recommended $R, last changed N days ago)". Put
"Yes (Recommended)" first. Add the note when present (K01 consignment).
Each AT_TARGET row is one question: leave it at Recommended (Recommended), or
cut toward Conservative. A cut toward Conservative is Russell's call and
needs a fresh PRICE and his yes to a stated number. Never batch questions
into a text card. Never apply an answer Russell did not give.

## 4. Apply the yeses

Collect the SKUs (or item numbers for listings with no SKU) Russell said yes
to, then dry-run first:

    python -m lib.cli lower apply --queue reports/lower/<date>/queue.csv --yes-skus <a,b,c>

Show the result in a line or two (it refuses any listing whose live price has
moved since the scan). Then, on the yes already given, write live:

    python -m lib.cli lower apply --queue reports/lower/<date>/queue.csv --yes-skus <a,b,c> --confirm

`--confirm` appends each success to `reports/lower/price_changes.csv`, which
is what starts that listing's next 15 days. Report the count applied, the
total dropped in list price, and any refusals. Nothing prints; reports reach
Russell as R2 links.

## Recurring (unattended, read-only)

A scheduled run may do only step 1 and post the share link: no eBay writes,
no ledger writes, no PRICE re-runs (Opus spend is attended and capped). The
attended `/lower` then does steps 2 to 4. Russell approves the schedule; do
not create it.

## Rules

- Every item gets its name and its picture. An ID alone means nothing.
- Write in positive voice.
- Nothing goes live without an explicit yes to that item.
- No cached spot; if the live spot is unavailable the gold row stays held.
