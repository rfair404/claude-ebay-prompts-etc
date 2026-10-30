---
version: 1.0.1
updated: 2026-10-30
---

# v9.0.0-alpha.1 walk-through

Ten stops, in order, for Russell to watch or drive. Run everything from
`/home/russ/EBAYBIZ` (the main checkout). Each stop says what it shows and
what needs Russell's hands. Background: [RUN.md](../RUN.md), "Ledger writes".

**This demo never touches:** publish (`--publish`, `--list`, `--confirm`),
price changes, ending listings, ad changes, tracking upload, label buys,
`--apply` on any ledger or sync, printing, or going live. Russell picks OBS
scenes; no stream is started.

## 1. Intake

Shows: TG-7 photos landing in `~/Pictures/TG-7`, never in an item folder.
Hands: plug the TG-7 in by USB and pick **Storage** on its screen.

```bash
cd /home/russ/EBAYBIZ && .claude/skills/r/tg7_pull.sh
```

## 2. Identify and price a demo lot

Shows: `identify.txt` and `price.txt` for one lot. The demo lot is
the sample ring, copied so the fixtures stay untouched.

```bash
cd /home/russ/EBAYBIZ && mkdir -p inventory/demo-ring && cp tests/fixtures/photos/*.jpg inventory/demo-ring/
```

Then tell the session to run identify and price on `demo-ring` and stop at
PRICE. Do not run DRAFT: it stamps a DRAFTED row for the demo SKU into the
real listings ledger and pushes it to R2. Hands: none.

## 3. PREP check

Shows: the PREP verdict on the ten tracked sample frames: eight crop cleanly,
two trip a guard ([README](../tests/fixtures/photos/README.md)). Read-only.

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.photo_prep.center_crop tests/fixtures/photos --check
```

## 4. Review card as an R2 link

Shows: the card Russell would approve, opened from a private link (7 days).
Cards are shared from a copy under `reports/review_cards/`, never from the
shoot folder.

```bash
cd /home/russ/EBAYBIZ && mkdir -p reports/review_cards/demo && cp inventory/demo-ring/review_card.html reports/review_cards/demo/1-demo-ring.html && .venv/bin/python -m lib.cli offsite share reports/review_cards/demo/1-demo-ring.html
```

Hands: open the link and read the card. Do not approve it.

## 5. list_edit dry run

Shows: what a listing change would send, field by field. No `--apply`, no
`--confirm`, nothing sent. The folder is listing 206662432530 (PUBLISHED), dry-run only.

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli listing --update inventory/tg7-20261029-0053/01-zippo-slim-lighter-lockheed-georgia --fields price --dry-run
```

## 6. Ledger: local mode, verify, sync preview, dashboard

Shows: ledgers in local mode (no `ledger_store:` block), then the hash-chained
event log replayed. Read-only.

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli ledger check
```

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli ledger verify
```

The finance sync as a preview (reads eBay, writes nothing without `--apply`):

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python lib/sync_actuals.py --all-stores
```

The dashboard (writes `reports/dashboard.html`) as a link:

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli dashboard --all-stores && .venv/bin/python -m lib.cli offsite share reports/dashboard.html
```

## 7. Pick list as R2 links

Shows: one sheet per box, as links. It reads orders only: no label buys, no
printing, no tracking upload. Hands: none.

```bash
cd /home/russ/EBAYBIZ && .claude/skills/pick-list/links.sh
```

## 8. Reports

Shows: the full account of one lot, then whether the last R2 push worked.

```bash
cd /home/russ/EBAYBIZ && .claude/skills/report-lot/run.sh CONSIGN-K01
```

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli offsite check
```

`offsite check` is offline: it reads `.offsite_status.json`. Add `--probe`
for the bucket round-trip.

## 9. Live show overlay and pager

Shows: the control-room pager and the OBS overlay page, served locally. Start
the app (the repo venv is required; leave it running for stop 10):

```bash
cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli serve
```

Then open `http://127.0.0.1:8770/pager`, and the overlay at
`http://127.0.0.1:8770/live/<show>/overlay` (`ls inventory/_live` names the
shows). Hands: Russell picks scenes in OBS. Do not press Start stream: it now
needs `"confirm": true` and the demo never sends it.

## 10. Web app pages

Shows, on `127.0.0.1:8770` only: the dashboard with its store switcher, then
`/review/demo-ring`, `/prep/demo-ring` (Frame Check) and `/freight`. The
buttons there queue local PREP and hero actions only; none writes to eBay.
Hands: open the pages; click nothing that says Run unless Russell wants a
local PREP action on `demo-ring`.

```bash
xdg-open http://127.0.0.1:8770/
```

## After the demo

`inventory/demo-ring` and `reports/review_cards/demo` are demo data. Stop the
app with Ctrl-C.
