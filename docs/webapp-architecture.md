---
version: 1.0.0
updated: 2026-10-26
---

# Web app architecture

The Fair Hand app is a local web interface over the pipeline. It covers every
core function of the business in the browser, so Russell's work is clicks
instead of chat turns and typing. Claude is one service the app calls for the
judgment stages; it is no longer the UI.

**The build plan is
[#311](https://github.com/rfair404/claude-ebay-prompts-etc/issues/311).** It
supersedes #31, which this doc used to scope as four phases. This doc records
the decisions and what is built; #311 has the full slice table, the per-team
requirements and the acceptance checks.

Russell's requirements (2026-10-24): every core function works end to end from
the browser and is secure; sign-in uses OAuth and every connection (eBay, R2,
Google Drive, the other keys) is authorized and stored properly; store
switching works for any number of stores.

## On main today

| Piece | What it is |
|---|---|
| Phase 1 (#127) | `tools/dashboard.py` (`ebz dashboard`): backlog by stage, drafts awaiting review, live vs. ledger drift. Reads only what is on disk. |
| Part of Phase 2 | `webapp/` (`ebz serve`, `http://127.0.0.1:8770`): FastAPI, bound to `127.0.0.1` only. Routes: `/`, `/review/{shoot}`, `/live/{show}*`, `/pick/{token}`, `/pager`, `/freight`, `/api/jobs*`, `/healthz`. |
| Job queue | `webapp/queue.py`, a SQLite job table in `reports/jobs.db`. Job kinds in `webapp/jobs.py` are the secret-free ones only: `prep-auto` and `price-stats`. |
| `/pick/{token}` | Pick sheets with buyer PII (#151): 256-bit tokens, expiry enforced on read, `no-store` / `noindex` / `no-referrer`, no route that lists the store. |
| `/freight` | The one route on main that uses a stored provider key. Booking is a dry run, then a confirm. `freight.web_booking: false` turns booking off. |

Until slices 4 and 8 of #311 land, the boundary on main holds: no web job
calls `lib/ebay_client.py` or Apify, and eBay writes stay in chat sessions and
the CLI.

## Decisions

These are the technical director's, from #311. Status: **planned** unless a
row in "On main today" says otherwise.

- **Extend `webapp/`; no rewrite.** Business logic stays in `lib/` and
  `tools/` as functions. The app is a thin shell over them and never
  re-implements tool logic.
- **Server-rendered pages with plain JS.** Reuse the Python renderers
  (`dashboard.draw`, `review_card_html.render`, `prep_sheet_html.build`,
  `live_show.render_host`, the freight pages) plus `fetch()` JS the way
  `pager.py` does it. Job status is polled from `GET /api/jobs/{id}`. No SPA,
  npm, Jinja, HTMX or WebSockets, and no new Python dependencies.
- **One action table.** New `webapp/actions.py`:
  `ACTIONS = {name: Action(argv_template, slots, writes_ebay, needs_confirm)}`.
  Slots are typed and validated (shoot, store, order id, stage, frame=deg). One
  handler runs `python -m lib.cli <argv>` from the repo.
- **Claude stages run through `claude -p`**, following `tools/tg7_import.sh`:
  `claude -p "<stage> <shoot>" --output-format json --allowedTools <per stage>`,
  30-minute timeout, at most 4 stage jobs at once, one per item. A job succeeds
  when its stage file (`identify.txt`, `price.txt`, `investigate.txt`,
  `draft.md`) exists and is newer than the job start. The job stores
  `session_id` so the long tail continues with `claude --resume`. No Agent SDK
  and no Anthropic API key. A Claude job gets no `Bash` beyond the PRICE
  stage's browse tool, so a stage can't publish.
- **Agents use the CLI, not HTTP.** Agents (the art director included) keep
  using `ebz` and read `.prep/card.jpg` from disk. The app issues no API
  tokens.
- **Ledger writes are serialized** with a lockfile around
  `list_edit.upsert_listing`, because chat sessions and web jobs both write
  it. They run from the main checkout.

### eBay writes: store, dry run, confirm

- Every eBay write takes a validated `store` (#156), never the env default.
- Every eBay write is two steps: a dry-run job, then
  `POST /api/actions/{name}/confirm` with the dry-run job id. The confirm is
  refused if the input changed since the dry run. A confirm id is single-use
  (`UNIQUE confirm_of`), so a second POST returns the first result and never
  writes twice. `reports/jobs.db` is the audit trail.
- **Approvals are hash-bound.** `card_hash` is the sha256 of `draft.md` +
  `review_card.md` + the PREP approval digests in `.prep/prep.json`. An
  `approvals` table in `reports/jobs.db` holds sku, store, card_hash, who and
  when; it is not a ledger column. Publish refuses when the current hash
  doesn't match the approved one.

### Hosting

Localhost only, login required anyway. The app keeps binding `127.0.0.1`
(`tests/test_webapp_server.py` enforces it). No LAN, phone, port-forwarding,
Tailscale or public host in this build. Russell skipped phone access for now
(2026-10-24); a mobile intake app is proposed separately in #314.

### Login

- **Google OIDC** (authorization code with PKCE, `state` and `nonce`) in a new
  `webapp/auth.py`, stdlib `urllib`, no JWT dependency. The ID token comes
  straight from Google's token endpoint over TLS. Redirect URI
  `http://localhost:8770/auth/callback`.
- Only verified emails in `webapp.auth.allowed_emails` can sign in. That list
  lives in `config.yaml`, never in this repo.
- **Sessions:** a random 256-bit id, stored only as a hash in a `sessions`
  table in `reports/jobs.db`. Cookie `HttpOnly; SameSite=Strict; Path=/`.
  7-day sliding expiry, 30-day absolute. Logout is a POST that deletes the
  row.
- **CSRF / Origin rule:** SameSite=Strict, and every non-GET must carry a
  loopback `Origin`. Today `_local_only` lets a request with no `Origin`
  through; slice 1 closes that.
- **Exempt from login:** `/healthz`, `/live/{show}/overlay` and
  `GET /live/{show}/state`. OBS can't sign in, and none of them carry PII.
- Internet is needed only at sign-in; a live show keeps running offline, with
  `show_flow.py` as the fallback. `uvicorn.run(..., access_log=False)` keeps
  the OAuth `code=` out of the logs.

### Connections and secrets

- **One secrets file:** `~/.ebaybiz/secrets.yaml` (or `$EBAYBIZ_SECRETS`),
  mode 0600, outside the repo. `lib/config.load_config()` deep-merges it over
  `config.yaml` and refuses it if group or other can read it. Env-var
  overrides keep working; policy ids and location keys stay in `config.yaml`.
  Not the OS keyring (a locked wallet breaks unattended jobs) and not an
  encrypted file (its key would need storing too).
- **Migration:** `tools/secrets_migrate.py`, dry run by default, `--apply`
  takes a backup. It moves the eBay keys and refresh tokens (per environment
  and store), the R2, Apify, Gemini and freight keys, and the Google OAuth
  client secret. A store with no `app_id` falls back to one shared eBay keyset.
- **`/connections`:** one row per eBay store, R2, Drive, freight, Gemini,
  Apify and the Claude CLI, showing status and dates only (connected,
  expiring, expired, last checked), never any characters of a value. eBay
  reconnect is the OAuth consent per store, written through
  `ebay_reauth.set_refresh_token` as the one writer. Drive reconnects by job
  (`rclone config reconnect gdrive:`). Provider-issued API keys go in a
  write-only field.
- **Redaction:** `config.secret_values()` + `redact()` run on every job result,
  error and HTTP error detail. A test greps every response and job row for
  each secret.
- Decided (Russell, 2026-10-24): Claude sessions and stage jobs can't read the
  secrets directory (#318).

**Open: what session-run tools do after slice 4.** The Bash sandbox denies any
read of `~/.ebaybiz`, so once the migration moves the credentials, tools a
Claude session runs through Bash that load them (`pick-list/links.sh`,
`list_edit.py`, `lib.cli offsite share`, the `ebay_client` calls) will fail.
Options raised: run them only through the web job runner, a narrow broker that
hands out short-lived tokens, or list them as "Russell runs these" on operator
cards. Slice 4 needs one picked, with an acceptance check, before it merges.
See [the #311 comment](https://github.com/rfair404/claude-ebay-prompts-etc/issues/311#issuecomment-6386287565).
#327 (mail) depends on the answer.

### Stores

- The code already handles any number of stores (`lib/stores.py`,
  `ebay_client.load_credentials`); each store has its own ledgers and
  policies. Two gaps get fixed with an optional
  `storefronts.<name>.ebay_username` key: `tools/live_marquee.py` and
  `tools/live_prep.py` assume or require a typed `--seller`.
- **Store switcher in every page header.** The current store is in the URL
  (`?store=`), never in the session, so two tabs can't publish to each
  other's store. Item writes use the draft's own store
  (`stores.draft_store`); account-level actions use the URL's store. "All
  stores" is a view only, one panel per store, never pooled totals.
- **Add a store** (`/stores/new`): name it and run the eBay consent; run the
  store-policy, pickup-policy and setup-check commands, each a dry run then
  confirm; then `stores.add_store_config()` takes a backup and appends the
  policy ids to `config.yaml` after Russell confirms the diff. That is the
  only config write the web is allowed. The store's buyer-facing identity
  stays empty until Russell picks it.
- Per-store rules hold: `auto_promote` stays off for a named store until its
  block names a campaign.

## Gates that stay human

| Action | Gate |
|---|---|
| PREP stages | The art director (an agent) approves each stage through `ebz prep`. No job approves a stage by itself. |
| Connect or reconnect an account, add a store | Russell signs in to the provider himself. Adding a store writes `config.yaml` only after he confirms the diff. |
| Publish | Russell taps **Approve** on each card, which records a hash-bound approval. "Publish N approved" carries them out one at a time, for one store, re-checking each hash. No job chains past REVIEW. |
| Price, markdown, Best Offer floor, end, relist | Dry run, then confirm. |
| Ads, consignment settle, freight booking, tracking upload | Dry run, then confirm, one per click. Tracking is never batched. |
| Go live | A second tap or press-and-hold. |
| Not in the app | Buying labels (link to Seller Hub), messaging buyers, cancels and refunds, hand-editing `config.yaml` or ledgers, bank logins. |

REVIEW's one-explicit-approval rule and PREP's three-stage gate (`RUN.md`)
carry over unchanged: the app lets them be answered from a page, not
inferred, batched or defaulted.

## UX rules

Adopted by Russell, 2026-10-24.

- **Nothing is typed** that the app can look up: show names, folder names,
  order ids, SKUs, titles, buyer-paid, tracking numbers, store, spot price.
  Pickers instead, and no free-text "why" fields.
- **Questions are yes/no or pick-one**, recommendation pre-selected.
- **No copy-paste into chat.** Every command a page hands over today becomes a
  button.
- No file paths, `127.0.0.1` links or terminal reports shown as answers.
- Desk only for this build, with big tap targets.
- PREP calls (art director) and code or process calls (TD) are never shown to
  Russell.
- Look: the Pop's Games palette (`brand/pops-games/README.md`), and no Google
  Fonts or other third-party calls.

## Slices

Each slice is one PR, merged in order. Acceptance checks, files and
prerequisites (P1, P2, #307, #308 before slice 8) are in
[#311](https://github.com/rfair404/claude-ebay-prompts-etc/issues/311).

| # | Slice | Status |
|---|---|---|
| 1 | Groundwork: app-wide loopback guard and PII headers, Origin required on writes, restart marks jobs interrupted, `StreamRequest` needs `confirm` | planned |
| 2 | Login (`feat!:`; needs the Google OAuth client and a `webapp.auth` block) | planned |
| 3 | Shell, action table (local actions only), brand, store switcher | planned |
| 4 | Secrets file, migration, `/connections` | planned; blocked on the open question above |
| 5 | Stores, `/stores/new` | planned |
| 6 | Item, prep and review pages | planned |
| 7 | Claude stages | planned |
| 8 | Approvals and eBay writes | planned |
| 9 | `/today` and `/intake` | planned |
| 10 | `/ship` and freight pickers | planned |
| 11 | Live screen (one screen, works offline) | planned |
| 12 | `/reports`, `/consign`, backups | planned |
| 13 | Listings, aging and markdowns | planned |

"Fully functional" means: when slice 13 merges, every core function works from
the browser with no chat turn and no typed command, apart from the human gates
above.

## Not in scope

An SPA or build step, passwords or our own accounts, Celery or Redis, the Agent
SDK or an Anthropic key, public hosting, phone or LAN access, a second job
runner, logic copied out of `lib/` or `tools/`, bulk writes across stores,
buying labels, messaging buyers, other channels (#166, #182), the stream box
(#249), general config editing from the web, agent access over HTTP, and
payouts, monthly close and the tax calendar (a follow-up issue).
