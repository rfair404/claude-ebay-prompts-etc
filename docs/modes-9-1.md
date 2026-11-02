---
version: 0.1.0
updated: 2026-10-31
---

# Modes — Triage, List, Manage (v9.1.0 plan)

PLAN, not built. Russell's three ways of working, made explicit so the app, the CLI and the agents all name the same thing. Extends [#311](https://github.com/rfair404/claude-ebay-prompts-etc/issues/311) (web app); replaces its slice 13 and gives slice 9 (`/intake`) a home.

A mode is a **view over existing state, not new state.** No ledger column, no `config.yaml` key, no new dependency. Every mode reads `lib/status.gather` and the ledgers and writes through the same functions the CLI uses. Version bump: **MINOR** (additive).

## The three modes

| | Triage | List | Manage |
|---|---|---|---|
| Question | What do we have? | Is this one item right? | What is out there, and what should change? |
| Unit | A wide shot, a bin, a lot | One item | A set of listings, any store |
| Input | Photos | An item with its own shots | Ledgers + live eBay data |
| Done when | Every frame is accounted for and every object has a folder | The card is approved and the listing is live in the right place | The batch is applied and the ledger agrees with eBay |
| Page | `/triage` | `/item/{shoot}` (→ prep → review) | `/manage` |
| Today's code | `/intake` skill, `tools/tg7_triage.py`, `consign split` (#415), RUN.md `plan` | RUN.md `list`/`full`, `/review`, `/prep` | `inventory-manager` aging, `policy_sweep`, `live_audit`, ad-hoc price-cut scripts |

Mode switch sits in the shell header beside the store switcher. The store stays in the URL; the mode is the path.

### Triage — account for everything

- Shows each wide frame with its objects numbered, the item folders they became, and **unaccounted frames** (assigned to no item, not marked duplicate/junk). The count must reach zero.
- Actions: assign frames to an item, split an object out of an item (`consign split`), merge two items, mark junk/duplicate, flag HOLD. All local; none touches eBay.
- Output is `catalog.csv` + item folders, as `/intake` writes them. Items stop here at "has its own shoot set". PREP does not start in Triage ([rule](../.claude/agents/inventory-manager.md)).
- Rough value only (estimator ranges). No PRICE, no DRAFT.

### List — one item, done right

- One item fills the screen: stage strip (IDENTIFY → PRICE → INVESTIGATE → DRAFT → PREP → REVIEW), "Run next stage", blockers in plain words (cost basis missing, `[PREP GATE]`, melt floor, offer floor).
- **"Right place" is a checked list on the card, not a free choice:** store (the draft's own, `stores.draft_store`), category, shipping/return/payment policies for that store, promote-on-publish rule for that store, consign client. A mismatch blocks Approve.
- Publish only on Russell's hash-bound Approve (#311 slice 8). This mode is #311 slices 6–8 plus the checklist.

### Manage — see it all, move it, change it in bulk

- **One table**, all stores side by side (never pooled totals): item picture, title, store, status, days listed, price, floor, Best Offer floor, lot/client, hold. Filter and sort on any column; saved filters ("oldest 20 on default", "unsold >60d", "K01").
- **Select rows → pick an action:**
  - Price: % off, to a tier (Push-high / Recommended / Conservative), or step-down next rung. Best Offer floors move by the same proportion.
  - Reassign: move to another store (see below).
  - End, relist, hold / seasonal hold, add to a live-show lineup.
- **Every bulk action is dry run → table → confirm.** The table shows old → new per row, total dollars off, and a skip box on each row with the reason shown for rows the rules excluded. One confirm applies the checked rows through `update_listing_fields`; a stale input (row changed since the dry run) is refused.
- **Rules are one pure function with one table-driven test**, in `lib/markdown_rules.py`: $36+ only, $30–35.99 and ≤$25 left alone, never below live-spot melt (fail closed, #298), cost, or Conservative; hand-set prices and seasonal holds skipped; K01 never promoted; junk store never promoted; whole-dollar results only.
- **Reassign store = end on A, rebuild draft, publish on B**, because eBay can't move a listing. It changes what customers see, so it is a customer-facing confirm: the dry run shows the target store's policies (Live vs default shipping, free-shipping rule, promote rule), the new SKU, and that A's row becomes ENDED while B's is a new DRAFTED→PUBLISHED. Runs per item, never batched past the per-store approval.

## Slices (each one PR, split by file ownership)

| # | Slice | Files | Done when |
|---|---|---|---|
| M1 | Mode shell | `webapp/ui.py`, `lib/status.py` (mode of a state), `docs` | Header shows Triage / List / Manage; each lands on its page; item states name the mode they belong to. |
| M2 | `/triage` | new `webapp/triage.py`, `tools/tg7_triage.py` | Unaccounted-frame count; assign, split, merge, junk actions; always a new lot (never replaces). |
| M3 | `/manage` read-only | new `webapp/manage.py`, `lib/ebay_browse.py` | Table, filters, saved filters, per-store panels; no write buttons. |
| M4 | Bulk rules + dry run | new `lib/markdown_rules.py`, `webapp/actions.py` | Rule table test passes; dry run lists skips with reasons; confirm applies once; double confirm is a no-op. After #311 slice 8. |
| M5 | Reassign store | `lib/draft_rebuild.py` use, `webapp/manage.py` | Ledger A ENDED, B new row; policies re-mapped; wrong-store confirm refused. |
| M6 | CLI + docs | `lib/cli.py`, `RUN.md`, `docs/README.md` | `ebz triage\|list\|manage`; RUN.md `plan`/`list`/`full` stay as aliases; this doc bumped to 1.0.0. |

## Order and collisions

- Needs #311 slices 1–3 (security, shell, actions) for M1–M3, and slice 8 (approvals, confirm) for M4–M5.
- Hot files: `webapp/ui.py`, `webapp/actions.py`, `lib/list_edit.py` (untouched here; call, don't edit). Before each slice run the `gh pr list` file check in `CLAUDE.md`.
- Supersedes #311 slice 13 (`tools/aging.py`): M3–M4 absorb it. Slice 9's `/intake` becomes Triage's upload. Say so on #311 when filing.
- Not in scope: new ledger statuses, PRICE logic changes, labels, live show.
