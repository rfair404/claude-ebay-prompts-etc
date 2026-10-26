---
version: 1.2.1
updated: 2026-10-26
---

# Offsite storage for listing data

Added after the 2026-10 Windows wipe, which lost every ledger, shoot photo and
`draft.md` because only one copy existed. `ebz offsite` (`lib/offsite.py`)
keeps a second copy in an S3-compatible bucket, and `ebz gdrive` keeps a
third on Google Drive (see the last section).

## What gets copied

All paths are relative to the **main checkout**. That holds even when the
command runs from a worktree.

| Path | Why |
|---|---|
| `listings_ledger*.csv*`, `sales_ledger*.csv*`, `unwound_orders*.csv*`, `listings_log.txt` | ledgers (`unwound_orders` holds refunded and cancelled orders), per-store variants and `.backup-*` copies included |
| `hand_listed_locations*.csv`, `ledger_reconcile_report*.json` | small state files that can't be rebuilt from eBay |
| `inventory_sheet*.csv`, `inventory_sheet*.json`, `.inventory_live*.json` | eBay exports (live-listing sheets built from the eBay APIs) |
| `pick_lists/**`, `.pick_list_state*.json` | pick lists and their state |
| `inventory/**/draft.md`, `inventory/**/identify.txt` | each item's draft and identification, at any depth (`fr/arquilt/draft.md`) |
| `inventory/**/context.txt`, `inventory/**/RECOVERED.md` | a bucket's spend and source (`ESTATES/SCJ/context.txt`), and the post-wipe recovery map that ties sold SKUs to their folders |
| `inventory/**/client_ledger.csv`, `inventory/_consign/**` | consignment: each intake's item ledger and the client registry (`clients.csv`, the only file with client names), from `ebz consign` |
| `inventory/_pick_lists/**` | pick sheets staged for `offsite share` (`prompts/pick_list.md`) |
| `reports/**`, `share-inventory/**` | reports and files staged for sharing |

**Photos and video are not copied**, wherever they sit (`.jpg`, `.png`,
`.heic`, raw formats, `.mov`, `.mp4` …; the full list is `MEDIA_SUFFIXES` in
`lib/offsite.py`). They were ~1.5 GB of the bucket against a few MB of text.

**Nothing else in a shoot folder is copied either**: not `review_card.html`
or `.prep/review.html` (they carry every photo inline, ~6 MB for a quilt),
and not `price.txt`, comps, `investigate.txt`, `NEEDS_REVIEW.md`,
`review_card.md` or `.prep/`. Those are rebuilt from `draft.md` and
`identify.txt` or by re-running their phase. Review pages are served by the
local app instead; see below.

These are also **not** copied: `config.yaml` (it holds eBay tokens and this
bucket's keys, so back it up separately, e.g. in a password manager),
caches, `kb/index/` and other output that can be regenerated. Add extra
globs with `offsite.include`. A `dir/**` glob takes everything under the
directory except photos. `dir/**/name` takes files called `name` at any
depth under it. Any other glob matches one path level, and `*` never
crosses a `/`.

### Removing files pushed before a rule change

Photos, review pages and other shoot files uploaded by older versions stay
in the bucket, because `push` never deletes. `status` lists them under "no
longer synced". `pull` does not restore them. To delete them and their
`history/` copies:

```
python -m lib.cli offsite prune            # dry run: lists what would go
python -m lib.cli offsite prune --apply
```

`prune` deletes only objects the rules no longer select. It never deletes a
ledger or `draft.md` that is missing locally.

The bucket holds revenue data and buyer addresses (pick sheets inside
`inventory/`). **Keep it private**: no public access and no r2.dev URL.

## Provider: Cloudflare R2 (recommended)

- 10 GB storage free, then $0.015/GB-month. No egress fees, so a full restore costs nothing.
- Any S3-compatible service works with no code changes. Backblaze B2 ($6/TB-month, keeps
  all object versions by default) is the cheaper choice if photos grow past ~100 GB.

### One-time setup (R2)

1. Cloudflare dashboard → **R2** → enable it (asks for a payment method; the free tier still applies).
2. Create a bucket, e.g. `ebaybiz-data`.
3. R2 → **Manage API tokens** → create a token with *Object Read & Write*, scoped to that bucket.
   Copy the Access Key ID, Secret Access Key and the S3 endpoint
   (`https://<account_id>.r2.cloudflarestorage.com`).
4. Add an `offsite:` block to `config.yaml` (template in `lib/config.example.yaml`).
5. From the main checkout:

```
python -m lib.cli offsite check          # write/read round-trip
python -m lib.cli offsite push           # dry run: what would upload
python -m lib.cli offsite push --apply
```

## Day to day

```
python -m lib.cli offsite status         # diff, no transfer
python -m lib.cli offsite push --apply   # after a shoot / listing session
```

Nightly, via crontab (`crontab -e`):

```
30 2 * * * cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli offsite push --apply >> .offsite.log 2>&1
```

## Review pages: served by the local app, not uploaded

`tools/review_card_html.py` writes `review_card.html` and prints
`review: http://127.0.0.1:8770/review/<shoot>`, the local app's live render of
the same page (`python -m lib.cli serve`, `webapp/server.py`). That is the
review link the operator gets (see `prompts/review.md`). Nothing is
uploaded, and `offsite share` refuses a review page. The link opens on this
machine only.

## Sharing one file: pick sheets

```
python -m lib.cli offsite share <file> [--expires-days N]
```

- Uploads one file now, with its real content type so the browser renders it.
  A changed bucket copy goes to `history/` first, as with `push`.
- Prints a **presigned** GET link, 7 days at most (the SigV4 limit). The bucket
  stays private. Anyone holding the link can open that one object until it
  expires, so forward it only to people who should see the listing.
- Refuses files outside the include list, so `config.yaml` can never be shared.

## Restore

```
python -m lib.cli offsite pull --apply              # restore files missing locally
python -m lib.cli offsite pull --apply --overwrite  # the bucket's copy wins on differences;
                                                     # local copies kept in .offsite_conflicts/
```

An older version of an overwritten file is under
`history/<UTC stamp>/<path>` in the bucket.

## Safety rules

- `push` never deletes a remote object. If a file is deleted locally, the bucket copy stays.
- `prune` deletes only objects the include/exclude rules no longer select, such as old photos.
- When `push` overwrites a changed object, it first copies the old one to `history/`.
- `pull` never replaces a differing local file without `--overwrite`.
- Nothing transfers without `--apply`.

## Ledger store (GH #380, Phase 1)

The ledgers' cloud copy that writers lock against, so two sessions writing
at once can't lose a row. It is separate from the `offsite` bucket above,
which keeps backing the ledgers up as before.

- **What it is:** an R2 bucket, `ebaybiz-ledgers`, behind a Worker
  (`cloud/ledger-worker/worker.js`). The Worker is the only door: no S3 token
  for this bucket exists. It answers only GET and PUT, has no delete, and
  refuses (403) any key not on its list. Every request needs the
  `LEDGER_TOKEN` secret.
- **Allowed keys:** `listings_ledger.csv`, `sales_ledger.csv`,
  `unwound_orders.csv`, their `-junk` versions, and `client_ledger-K01.csv`.
  The list is `ALLOWED` in `worker.js` and `LEDGERS` in `lib/ledger_store.py`;
  `tests/test_ledger_store.py` keeps them equal. `inventory_sheet` (rebuilt
  from eBay) and `clients.csv` (client names) are deliberately off it.
- **Writes:** `ledger_store.update()` reads the key, applies the change and
  writes it back only if nobody else wrote in between (ETag `If-Match`,
  retried on conflict). The local CSV stays as a cache, so readers don't
  change. Every PUT keeps the version it replaced as `history/<key>/<stamp>`,
  kept 90 days.
- **Offline:** the write lands in the local CSV and is marked pending in
  `.ledger_etags.json`. `ledger push` uploads it if the cloud hasn't moved;
  otherwise it stops and names the rebuild from eBay.
- **Local mode:** with no `ledger_store:` block in `config.yaml`, writes stay
  local as before. Once `.ledger_etags.json` exists, this checkout has written
  to the cloud, and removing the block makes ledger writes refuse rather than
  fork the record.
- **Cost:** expected to stay within the R2 and Workers free tiers.

### Setup (once): run after P1.4 merges

Russell has nothing to do after this release. The setup and seed below wait
until P1.2 to P1.4 have merged.

Don't run `ledger seed --apply` until P1.2 to P1.4 have converted every
writer. Until then, `list_edit`, `sync_actuals` and `consign` still write the
CSVs directly, so the seeded cloud copy goes stale. The first `update()` after
P1.2 then GETs the stale cloud rows, applies `fn` and `atomic_write`s them
over the local CSV (`lib/ledger_store.py` `update()`, 200 branch). Every row
written since the seed is lost, and cloud mode leaves no `.backup-*`.

When P1.4 has merged, run it from the main checkout (the GH #103 rule). The script logs in to Cloudflare,
creates the bucket and its 90-day `history/` rule, deploys the Worker, sets a
fresh token, writes the `ledger_store:` block into `config.yaml` (don't type
it by hand), runs `ledger check` and logs out. Re-running it rotates the token.

```
bash cloud/ledger-worker/setup.sh --phase 1
python -m lib.cli ledger seed --apply
```

### Commands

Each is `python -m lib.cli ledger ...`, run from the main checkout. `seed`
and `restore` are dry runs without `--apply`.

| Command | Does |
|---|---|
| `check` | each key's state, plus the allowlist probe (fails unless an unlisted key gets 403) |
| `pull [--discard-pending]` | cloud copy to the local CSVs; skips keys with an unpushed offline write unless told to discard it |
| `push` | uploads pending offline writes |
| `seed [--apply]` | first upload of each local ledger; never overwrites a seeded key |
| `history <key>` | that key's saved versions (90 days) |
| `restore <key> <stamp> [--apply]` | puts a saved version back; the one it replaces goes to `history/`, so a restore can be undone |

## Second copy: Google Drive

`ebz gdrive` (`lib/gdrive_mirror.py`) copies the same file set to Google Drive
with [rclone](https://rclone.org/drive/). It is a different provider and a
different account from R2, so one lapsed bill, revoked key or bad push can't
take out both copies.

It runs on its own:

- **After every ledger write**: draft, sync, publish, price/title edit
  (`--update`), end, delete, sale (`lib/sync_actuals.py --apply`), shipment
  (`pick-list`) and `reconcile --apply`. A detached background job pushes the
  ledger files about 20 s later. Writes that land while it waits or runs are
  batched into one upload. It never blocks or fails the command that wrote
  the ledger. Only the main checkout's ledgers count; a worktree's copy and
  a test's temporary ledger don't trigger it. Log: `.gdrive.log`.
- **Daily at ~03:15**: a systemd user timer pushes the full set.
  `Persistent=true` means a day the machine was off runs at the next boot.

Drive layout: `<folder>/data/<path>` is the current copy, and
`<folder>/history/<UTC stamp>/<path>` holds versions a push replaced
(`rclone --backup-dir`). Push uses `rclone copy`, never `sync`, so it never
deletes on Drive.

### Setup (once)

1. Install rclone. Needs your password:

   ```
   sudo apt install rclone
   ```

2. Create the remote. This opens a browser to sign in to Google. Take the
   defaults, except name it `gdrive`, pick storage `drive`, and pick scope
   `1` (full access) or `3` (files rclone creates only):

   ```
   rclone config
   ```

3. Add the `gdrive:` block to `config.yaml` (template in
   `lib/config.example.yaml`):

   ```yaml
   gdrive:
     remote: "gdrive"
     folder: "EBAYBIZ-backup"
     auto: true
   ```

4. Check it, do the first full push, and turn on the daily timer:

   ```
   python -m lib.cli gdrive check
   python -m lib.cli gdrive push --apply
   python -m lib.cli gdrive install-timer --apply
   ```

### Day to day

```
python -m lib.cli gdrive push                    # dry run: what would upload
python -m lib.cli gdrive push --apply --ledgers  # just the ledgers, now
systemctl --user list-timers ebaybiz-gdrive.timer
journalctl --user -u ebaybiz-gdrive.service      # daily run output
tail .gdrive.log                                 # after-write runs
```

Set `EBAYBIZ_GDRIVE_AUTO=0` to turn off the after-write push for one
command, or `auto: false` to turn it off for good. To restore from Drive,
copy `<folder>/data/` back with `rclone copy gdrive:EBAYBIZ-backup/data .`
from the main checkout. Check what is there first:
`rclone ls gdrive:EBAYBIZ-backup/data`.
