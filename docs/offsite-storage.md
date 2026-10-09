# Offsite storage for listing data

Added after the 2026-10 Windows wipe, which lost every ledger, shoot photo and
`draft.md` because only one copy existed. `ebz offsite` (`lib/offsite.py`)
keeps a second copy in an S3-compatible bucket.

## What gets copied

All paths are relative to the **main checkout**. That holds even when the
command runs from a worktree.

| Path | Why |
|---|---|
| `listings_ledger*.csv`, `sales_ledger*.csv`, `listings_log.txt` | ledgers, per-store variants and `.backup-*` copies included |
| `hand_listed_locations*.csv`, `.pick_list_state*.json` | small state files that can't be rebuilt from eBay |
| `inventory/**` | photos, `draft.md`, phase output, pick sheets |
| `reports/**`, `share-inventory/**` | reports and photos staged for sharing |

These are **not** copied: `config.yaml` (it holds eBay tokens and this
bucket's keys, so back it up separately, e.g. in a password manager),
caches, `kb/index/` and other output that can be regenerated. Add extra
globs with `offsite.include`.

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

## Review pages: uploaded on build, linked by URL

`tools/review_card_html.py` uploads each `review_card.html` it writes and
prints `share: <url>`. That URL is the review link the operator gets (see
`prompts/review.md`). Under the hood:

```
python -m lib.cli offsite share <file> [--expires-days N]
```

- Uploads one file now, with its real content type so the browser renders it.
  A changed bucket copy goes to `history/` first, as with `push`.
- Prints a **presigned** GET link, 7 days at most (the SigV4 limit). The bucket
  stays private. Anyone holding the link can open that one object until it
  expires, so forward it only to people who should see the listing.
- Refuses files outside the include list, so `config.yaml` can never be shared.
- `--no-share` on `review_card_html.py` skips the upload. A failed upload
  prints `share: skipped — …` and the local page is still written.

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
- When `push` overwrites a changed object, it first copies the old one to `history/`.
- `pull` never replaces a differing local file without `--overwrite`.
- Nothing transfers without `--apply`.
