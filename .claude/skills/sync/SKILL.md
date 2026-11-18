---
name: sync
description: Pull everything eBay holds down to the local files and bring the client ledgers up to date. Reconciles the listings ledger and drafts against every store's live listings (eBay wins), refreshes sales actuals from the Fulfillment API, then runs the consignment sync so each client row moves to its new state. Writes local files only; never changes eBay. Use when Russell types /sync, or asks to "sync eBay", "refresh the ledgers", "update the client ledgers" or "pull down what sold".
---

# /sync: eBay to local, then the client ledgers

Typing `/sync` is the ask: start without confirming. Every step below writes
local files only; none changes eBay. Run from the main checkout
(`cd /home/russ/EBAYBIZ`), never a worktree (`consign sync` refuses there, GH
#103), with the Bash sandbox off so ledger writes and the R2/Drive mirror
work. Order matters: listings first, sales second, clients last.

## 1. Listings: live state to the ledger and drafts

    python -m lib.cli --all-stores reconcile --apply
    python -m lib.cli --all-stores live-audit --apply

`reconcile` makes `listings_ledger*.csv` say what the Sell API says (price,
offer, listing id, live/ended). `live-audit` makes drafts and folder state
match, and lists items made in the eBay web UI that have no local row. Run
the dry run first (no `--apply`) only if a prior run stopped on an `[X]` line.

## 2. Sales: what actually sold

    python lib/sync_actuals.py --all-stores --apply

Refreshes `sales_ledger.csv` and `unwound_orders.csv` (sold price, fees,
postage; refunds and cancellations drop out) and stamps SOLD.

## 3. Client ledgers

    python -m lib.cli consign sync --apply

Reads the ledgers from steps 1 and 2 (no eBay calls) and moves each
consignment row through SHOT, DRAFTED, LISTED, SOLD, CLEARED. It backs up
first. If it stops on an `[X]` line, do not hand-fix a ledger: report the
line and finish the report.

## 4. Report (5 lines max)

Per store: listings changed, new from eBay, ended or sold since last sync.
Then, for the clients, rows that changed state (client code and item id
only, never a name) and any `[X]` or `POSSIBLE_DUPE` lines. Do not paste
client money figures; `/report-lot` makes the account. Reports and lists go
as R2 links (`python -m lib.cli offsite share <file>`), never printed.

If a step fails (token expired, API down), say which step and why, skip the
steps that depend on it (2 needs 1's ledger; 3 needs 1 and 2), and stop.
