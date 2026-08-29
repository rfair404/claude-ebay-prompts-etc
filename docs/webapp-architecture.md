# Web app / backend-service architecture plan (#31)

Scopes issue #31 ("Claude becomes a backend service, not the UI") as a
sequenced, risk-ordered plan, following the doc-first pattern this repo
already uses for large ideas (PR #60 scoped #59's background dispatch, PR
#64 scoped #62's conductor/workers model — both landed a planning doc plus
at most one small, safe, concrete piece, not the whole architecture). This
document is that first piece for #31: no server, no job queue, and no
secrets/network change ships with it.

## The problem, as it actually shows up in this codebase

The pipeline already runs headless end-to-end (`RUN.md`) and already has a
real command surface (`lib/cli.py`, the `ebz` dispatcher) — but every stage
still executes *inside* a Claude Code conversation, including the parts
that need no judgment at all:

- **PREP's `--auto` crop/orientation pass**, `lib/price_stats.py`'s tier
  math, `lib/list_edit.py`'s offer/draft building, and the ledger CSV
  read-modify-write are plain deterministic code today. None of them need
  a model in the loop, but all of them currently only run when a chat
  session invokes them.
- **The UI is one-shot and ephemeral.** `tools/review_card_html.py` and
  `tools/prep_sheet_html.py` already generate real HTML review surfaces —
  `review_card.html`, `.prep/review.html` — but each is a one-off file
  published once per run, not a live page someone can return to. There is
  no standing view of "what's in the backlog right now."
- **`tools/sales_report.py` already proves the read-only-dashboard pattern
  works.** Its `sync()`/`gather()` split — sync from the API, then draw
  `reports/sales_dashboard.html` from local CSVs only, with `--no-sync`
  skipping the network entirely — is exactly the shape a Phase 1 dashboard
  needs, just not generalized past sales.

So the gap #31 identifies is real, and closer to being closed than the
issue's "architecture sketch" implies: the API surface (`lib/cli.py`), the
structured-output convention (Phase 2 of V4_PLAN, "terse to stdout, detail
to JSON"), and a working static-HTML dashboard pattern already exist. What
does not exist yet is (a) a live/standing web view instead of one-shot
files, (b) a job queue so uploads/approvals don't require a chat turn, and
(c) any story for running credentials outside a single operator's local
shell.

### Status of the stated prerequisite (#30)

Issue #31 says this work is "downstream of #30, not parallel to it."
`docs/V4_PLAN.md` shows Phases 1, 3, 4, and 5 of #30 checked off as
landed: prompt diet, the `ebz` CLI, the on-disk comp cache + single-pass
mode, and the session observer. Phase 2 ("terse tool output") is written
as a convention, not a checklist, and checking the code directly: none of
its five named targets (`lib/photo_prep/prep.py`, `lib/list_edit.py`,
`tools/sales_report.py`, `tools/ledger_reconcile.py`,
`tools/live_audit.py`) print the `OK n/m, k flagged → <file>.json` shape
the convention describes yet — the one attempt at it
(`tools/ledger_reconcile.py`) is an open, unmerged PR as of this writing.
So #30 is landed for three of four phases that matter here, not five of
five. It still doesn't block this plan: Phase 2 is about making a tool's
*terse stdout* readable by a human or an LLM reading a transcript: this
plan's Phase 2 already calls `gather()` and friends as Python functions
directly (see the module-mapping table below), never by parsing a CLI's
printed text, so the "skinny prompts + structured records + one CLI"
precondition #31 actually needs is in place regardless.

## Phased plan

Each phase is scoped so its risk matches what it's allowed to touch. Later
phases build on earlier ones' code, not around it.

### Phase 1 — read-only local dashboard (no server, no new network surface)

**What:** Extend the existing static-HTML-generator pattern —
`tools/sales_report.py`'s `gather()` → `reports/sales_dashboard.html` — to
cover the views #31 asks for beyond sales: backlog by stage (one row per
`inventory/<shoot>/` via `lib/status.py`'s existing per-shoot state logic),
drafts awaiting review, and live-listing vs. ledger drift. Still a `python
-m lib.cli <new-command>` invocation that writes an HTML file to open
locally — not a running server, not a background job, not a phone-reachable
page.

**Why first:** it needs nothing this repo doesn't already have. No secrets
beyond what `sales_report.py` already reads (none for the CSV-only path;
`--no-sync` mode touches zero credentials). No new dependency. No network
listener at all — "hosting" is moot because there's no process staying up.
It directly replaces the "review cards live in chat scrollback" complaint
with a page that persists on disk, at essentially the cost of one more
report script.

**Why not more yet:** a live/served page implies a process that stays
running, which is the first real step toward "network-reachable" — worth
keeping separate from "can I see my backlog as a page" so that capability
lands with zero new attack surface.

### Phase 2 — local job queue + non-secret writes (still localhost-only)

**What:** A FastAPI app + a small worker, running only on the operator's
own machine (`127.0.0.1`, no bind to `0.0.0.0`), that:

- Turns Phase 1's dashboard from a generated file into a live page backed
  by the same read functions.
- Wraps the **secret-free** half of the pipeline as background jobs:
  `lib/photo_prep/prep.py`'s `--auto` pass on upload, `lib/price_stats.py`
  tier math over an already-saved comp JSON, `lib/list_edit.py`'s local-only
  functions (`record_draft`, `validate_draft_for_sync`, `upsert_listing` —
  all local file/CSV operations, no eBay call). `build_review_card()` is
  NOT in this set — it calls `preflight_listing()`, which loads credentials
  and hits eBay's category/policy endpoints; it stays Phase-3 alongside the
  other eBay-backed functions (see the module-mapping table below).
- Lets the review queue accept **local writes**: approving a PREP stage
  (writes `.prep/prep.json`), editing a draft field, approving/declining a
  review card *locally* (still nothing published — see Phase 3).

**Why here:** this is the actual "deterministic 80%" #31 names, and none
of it needs eBay or Apify credentials — it's local image processing, pure
math, and CSV/file writes the pipeline already performs today from inside
a chat session. Moving it behind a local job queue removes the
conversational round-trip for routine approvals without moving a single
credential anywhere new. `listings_ledger.csv`'s single-writer constraint
(RUN.md's "the ledger is the one hard constraint") carries over unchanged:
the job worker serializes ledger writes exactly the way the conductor
model already does for concurrent chat workers.

**Explicitly not in Phase 2:** PRICE's Apify comp pull (needs the Apify
token), anything in `lib/ebay_client.py` (publish, offers, policy sweep,
Fulfillment sync) — those stay chat-only until Phase 3's secrets story
lands.

### Phase 3 — network reachability + the secrets story + live eBay/Apify writes

**What:** Only once Phases 1–2 exist and a human has reviewed a written
secrets plan:

- eBay + Apify tokens move from `config.yaml`/env into backend-managed
  storage, scoped to the backend process, never sent to the frontend.
- The app becomes reachable from somewhere other than the same machine —
  phone-based review, per the issue's own framing.
- PRICE's Apify pull and every `lib/ebay_client.py`/`lib/list_edit.py`
  network call (publish, withdraw, policy sweep, Fulfillment sync) become
  backend jobs, replacing the equivalent `ebz`/chat invocation.
- Scheduled jobs (`lib/sync_actuals.py`, `tools/ledger_reconcile.py`)
  move from "run when a session remembers to" to an actual cron/worker
  schedule.

**Gate:** this phase does not start until a human has reviewed and signed
off a secrets document (see Risks below) — this plan does not authorize
that move, only describes what it will look like when someone decides to
do it.

### Phase 4 — Claude as an invoked backend service

**What:** IDENTIFY, DRAFT copywriting, and PRICE's judgment calls (median
vs. push-high, rarity checks) move from chat turns to Agent SDK/API calls
the backend makes per-stage, with the skinny structured prompts #30 already
produced as input and a structured stage record as output — no
conversation. Chat stays the escape hatch for the long tail #31 names:
novel categories, marble deep-dives, forum research, new specializations,
pipeline debugging.

**Why last:** it's the piece that actually matches the issue's title
("Claude becomes a backend service"), but it depends on Phase 3's backend
existing and adds its own secret (an Anthropic API key, held server-side)
on top of eBay/Apify — no reason to design it before the infrastructure
it runs inside is real.

## Module mapping: what wraps as-is vs. what needs work

| Module | Role in the web app | Ready as-is? |
|---|---|---|
| `lib/cli.py` | Command registry — already close to an API surface; each `COMMANDS` entry is a candidate job type | Ready for Phase 2 (shell out per job); the registry pattern (name → module, one-line purpose) maps directly to a job-type table |
| `lib/status.py` (`python -m lib.cli status <shoot-dir>`) | Backlog-by-shoot data for the dashboard | Ready as-is — already has a `--json` structured-output mode |
| `tools/sales_report.py` (`_rows`, `gather`) | Sales/drift dashboard data layer | Ready as-is — `gather()` is already a pure function returning the dict a template needs, separate from the HTML-writing step; no extraction needed, just `from tools.sales_report import gather` (`lib/cli.py` puts the repo root, not `tools/`, on `sys.path` and dispatches to this module as `tools.sales_report`) |
| `lib/price_stats.py` | PRICE tier math | Ready as-is — stdlib-only, already a pure function (`price_from_runs`), no secrets |
| `lib/photo_prep/prep.py` | PREP pipeline (orientation/crop/colour) | Ready as-is for background jobs — already stage-gated with flags (`--auto`, `--check`, `--approve-stage`) that map directly to job endpoints; needs a queue wrapper, not a rewrite |
| `tools/review_card_html.py`, `tools/prep_sheet_html.py` | Review/prep HTML rendering | These already prototype the front-end — Phase 1/2 can serve their output live instead of publishing one-off files; the rendering logic itself doesn't need to change |
| `lib/list_edit.py` | Draft build, ledger CSV read/write, eBay publish/withdraw | **Split needed.** Local-only functions (`record_draft`, `validate_draft_for_sync`, `upsert_listing`) are secret-free and Phase-2-safe. `preflight_listing()` loads credentials and calls eBay category/policy endpoints (allowed-condition metadata, shipping policy lookup) — it is NOT secret-free despite being local-file-triggered. `build_review_card()` calls `preflight_listing()` internally, so it inherits the same eBay dependency. Both stay Phase-3-only alongside `create_or_update_listing`, `publish_offer`, `withdraw_offer_by_id`, `delete_offer_by_id` — a Phase-2 review queue needs either a cached/precomputed preflight result or to defer the review card's preflight section until Phase 3 |
| `lib/ebay_client.py` | All eBay Sell/Trading API calls | Entirely Phase 3+ — every function needs live credentials; needs an auth-gated internal layer, token never reaches the frontend |
| `lib/config.py` | Config/secrets loader | The thing that needs the secrets-story rework before Phase 3 — today it assumes a single local operator's env/file; a backend process needs a different trust model (see Risks) |
| `lib/sync_actuals.py`, `tools/ledger_reconcile.py`, `tools/policy_sweep.py` | Scheduled sync/reconcile | Phase 3+: becomes a real cron/worker job once eBay creds live server-side; logic itself is unchanged |

## The four open questions from the issue

**Hosting.** Local-only through Phase 2 — there is no process listening on
a network interface until Phase 3 by design. When Phase 3 is reached,
recommend a private-network overlay (e.g. Tailscale) over port-forwarding
or public hosting: phone reachability doesn't require public exposure, and
a small resale business has no reason to accept that risk for a
convenience feature. Public hosting is out of scope for this plan
entirely.

**Job runner.** Agree with the issue's instinct — something boring. For
Phase 2's actual volume (one operator, a handful of items in flight at
once), recommend FastAPI + a plain worker pool (`concurrent.futures` or a
subprocess per job, shelling out through `lib/cli.py` exactly as a human
would) backed by a SQLite job table for state — no Celery, no Redis, no
message broker. That infrastructure is sized for a load this pipeline
doesn't have; add it later only if Phase 2 usage actually justifies it.

**Auth/secrets.** No phase in this plan moves eBay or Apify credentials
into a network-reachable process without a separate, human-reviewed
secrets document first. This plan's default recommendation for when that
document is written: secrets load from OS-level protected storage (a
keychain or a file with restricted permissions) rather than process env
vars in a long-running web server; the backend process is the only thing
that ever holds a token — the frontend and phone client never receive one,
only session auth to the backend itself; and the network layer (Tailscale
or equivalent) is the perimeter, not the app's own auth alone. This is a
recommendation for that future document to start from, not a decision this
PR makes.

**Phone review scope.** Recommend a two-tier split rather than forcing
everything phone-first: PREP crop/orientation approval and the REVIEW
card's routine decision (title, price ladder, ⚠ warning lines) are
thumbnail/summary-sized and fit a phone screen fine — most of the daily
approval volume. Full-resolution defect inspection (hairline cracks,
maker's-mark reading, fine wear) stays a desktop task until a phone-side
pinch-zoom full-res viewer is specifically built, which this plan defers
to Phase 3/4 rather than blocking Phase 1/2 on.

## Non-goals and risks

- **This document does not build the FastAPI server, the frontend, or any
  job queue.** No runtime code ships with this PR beyond, at most, the
  small groundwork noted below.
- **No phase in this plan is authorized to move eBay or Apify credentials
  into a network-reachable service without a human-reviewed secrets story
  first.** Phase 3 names what that review needs to cover; it does not
  perform it. Nothing here should be read as that review having already
  happened or as pre-approval to skip it.
- **No phase weakens an existing gate.** REVIEW's one-explicit-approval
  rule and PREP's three-stage photo gate (RUN.md) carry over unchanged —
  Phase 2 lets them be answered from a web page instead of a chat card,
  it does not let them be inferred, batched, or defaulted.
- **Frontend framework choice is deliberately left open.** This plan does
  not commit to React vs. server-rendered HTML vs. htmx — that's a Phase 2
  implementation decision, not an architecture decision this doc needs to
  make.
- **Job-runner and hosting choices favor boring over scalable**, matching
  this repo's actual load (one operator, single-digit concurrent items) —
  revisit only if Phase 2 usage data says otherwise, not preemptively.

## Precedent this plan follows

PR #60 (`docs: plan background dispatch for local-state-independent
stages`, #59) and PR #64 (`docs+tool: conductor/workers concurrency
shape`, #62) both scoped a large execution-model idea into a planning doc
plus one small, safe, concrete piece — not the full build. This document
follows the same shape for #31: the doc is the deliverable; Phase 1 (the
extended static dashboard) is the natural next PR-sized unit once this
plan is reviewed, not something this PR builds unasked.
