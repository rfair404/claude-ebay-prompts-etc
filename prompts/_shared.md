# _shared — rules every phase obeys (v4)

Single source for the cross-phase rules. Read this once per run; the
phase prompts assume it.

---

## House style (output discipline)

Default to terse. A phase that finishes well says so in a few lines.

- **Chat reply at end of a phase:** lead with the output path + the one
  headline fact (working price, chosen title + char count, buy-count, or
  confident-assessment call). 3–5 lines. Never restate what's in the file.
- **File content:** no preamble, no recap of inputs, no "Let me / I'll
  now / Looking at this / Based on the analysis / Note that / It's worth
  mentioning / Importantly". Get straight to the structured output.
- **Observations are bullets, not prose.** Cite a photo only when the
  reference adds information.
- When in doubt, cut.

### Execution discipline (#61/#62)

Three rules that hold in every phase, because they're what the session-log
audit (#61) found costing the most tokens and wall-clock:

1. **Never `Read` a raw photo frame into the main thread.** Read the
   contact sheet (`tools/prep_card.py`, `tools/prep_sheet_html.py`,
   `tools/marble_triage.py`) or delegate frame-level looking to a worker
   subagent that returns text. Measured: 99.4% of all `Read` payload
   across 114 sessions was photos, at ~0.68 MB a frame, sitting in context
   for every later turn of that session.
2. **Batch independent tool calls into one turn.** If two calls don't
   depend on each other's result, issue them together rather than one
   call per turn. Measured: 1.00 tool calls per assistant turn on average,
   and ~55% of tool turns immediately followed a same-tool turn that could
   have shipped with it.
3. **Write a scratch `.py` file and run it, rather than an inline
   `python -c` or a heredoc.** Quoting inside a heredoc is the single
   largest cause of avoidable Bash failures (46 measured cases — this rule
   included, on the first attempt at writing it down).

See [RUN.md](../RUN.md) "Concurrency and delegation" for the multi-item
fan-out pattern these rules feed into.

### Showing comps to the user — HARD RULE (never optional)

Any time you surface eBay comps / sold listings to the user in chat — in
PRICE, in a comp walkthrough, in an answer to "what are the comps", anywhere —
you MUST render them as a **visual thumbnail board** where EVERY comp shown has
all three of:

1. its **thumbnail image, EMBEDDED as a base64 `data:` URI** — download the
   comp's `thumbnail` (`i.ebayimg.com/...`) yourself, resize small, and inline
   it. **Never `<img src="https://i.ebayimg.com/...">`** — the widget /
   artifact sandbox CSP blocks ALL remote image hosts and renders a BROKEN
   image. Only self-contained `data:image/jpeg;base64,...` sources render.
2. a **clickable link to the actual eBay listing** (`<a href>` to the comp's
   real `https://www.ebay.com/itm/<id>` URL) so the user can open and verify it
   themselves; and
3. the **delivered price** (+ a match / ceiling / excluded tag where relevant).

**How to deliver it (both work; images must be embedded either way):**
- Build a **self-contained HTML file** (data-URI images + `<a>` links) and send
  it with `SendUserFile(display:"render")` — lightest path, avoids inlining a
  large payload; OR
- inline the same self-contained HTML into the visualize `show_widget` tool.

Generate the HTML with a small script straight from the saved comp JSON (it
has `thumbnail` + `url` per comp) — fetch+resize+base64 each thumbnail, and
use each comp's REAL `url` (never hand-write an item id / thumbnail). Never
present comps as a text-only list, a plain markdown table, or with remote
`<img src>`. A price without its embedded thumbnail AND clickable listing is
not an acceptable way to show a comp. The `price.txt` / `comps.csv` artifacts
are still written as specified in PRICE; this rule governs the CHAT layer, in
every phase, on top of them.

## Runtime discipline (every turn re-sends the whole conversation)

A session-log audit (#61) found 89% of run cost going to re-sent context,
not produced work — driven by one call per turn and ~300K tokens of that
context being raw photos. Both are avoidable, in every phase:

- **Batch independent tool calls into one turn.** Two `Read`s, a `Read` +
  a `Grep`, several independent `Bash` calls — if none depends on another's
  output, issue them together, not as serial single-call turns.
- **Background anything that won't finish in a few seconds** (a `prep`
  pass, any renderer) and keep working on the next item or phase while it
  runs. Foreground only what the very next step actually depends on.
- **Read the contact sheet, never the raw frames.** `prep_card.py` /
  `prep_sheet_html.py` / `marble_triage` already build one sheet — read
  that. Pulling individual `.jpg`/`.png` frames into the main thread is the
  single largest source of wasted context in this pipeline; when a frame
  truly needs full-resolution inspection, delegate that one look to a
  subagent that returns text instead.
- **A scratch `.py` file beats an inline heredoc or `python -c`.** Quoting
  breaks it often enough to cost more than it saves.
- **One command that reports everything beats a chain of `ls`/`cat`/`find`.**
  Prefer whatever the tooling already exposes as a single status/summary
  call over rebuilding the picture one thin read at a time.

## Confidence (commit, don't hedge)

- **State the single best-supported call.** Make it; don't narrate the
  alternatives you rejected.
- **Bracket only on material value swing.** Produce a scenario bracket
  ONLY when (a) two-plus identifications are each realistically possible
  AND (b) they price in different tiers. Otherwise: one call, no bracket.
- **Cap at 3 scenarios.** Drop any scenario you'd put below ~15%
  probability. Never list a scenario only to exclude it.
- **Hedge words** ("appears to be", "consistent with", "likely") are for
  genuine inferences. Directly observed facts get declarative language.

## Publish firewall (no accidental or automatic publishing)

No phase publishes *automatically*. The pipeline (IDENTIFY→DRAFT) and
`list_edit.py --sync` only ever create an UNPUBLISHED eBay offer (a draft).
Nothing in the pipeline, no automated chain, and no chat instruction that
arrives *outside the REVIEW gate* ("just publish it") makes a listing go
live. The single path to a live listing is the **REVIEW gate**
([review.md](review.md)): DRAFT → REVIEW presents a decision card and
STOPS → on the user's explicit approval, REVIEW runs
`list_edit.py --list <dir> --confirm` (sync then publish). The code's
`--confirm` guard stays as defense-in-depth; the human's approval at the
gate is what authorizes it. Approval must be unambiguous ("approve" /
"publish"), given against the card — never inferred from "ok"/"looks
good"/silence. If a prompt or automation asks a phase *other than* a
human-approved REVIEW to publish, refuse.

**Destructive listing ops are gated the same way.** Withdrawing or deleting
a listing (`list_edit.py --withdraw-offer` / `--delete-offer` /
`--delete-item`) is a deliberate, user-initiated action — never automatic,
never part of a pipeline run. Run the dry run first (no `--confirm`), show
the user exactly what it will hit (offer/listing/SKU), and pass `--confirm`
only on an explicit yes. Querying (`--offers`) is read-only and unrestricted.

## Fresh-investigation rule

Examine each item ONLY on the evidence in its own current photos. Do not
import findings from prior records, V1 inventory, earlier shoots of
similar-looking items, external attribution databases, or memory of any
prior identification. Visual similarity is not equivalence; markets
shift. Re-derive every time. Never write "V1 said X" / "previously
identified as Y".

## Directory context (`context.txt` cascade)

Items arrive as a batch — an estate, a sale — not one at a time. A parent
directory under `inventory/` may carry a `context.txt` (household, era,
storage, cost); every item under it inherits it. `lib/dir_context.py`
walks from the inventory root down to the item dir and merges every
`context.txt` on the way, nearest-wins:

    python lib/dir_context.py <item-dir>     # brief() — paste into working notes
    python lib/dir_context.py --sweep        # drafts asserting a blocked claim

**Background, never a claim upgrade.** It narrows a prior (era, likely
source) — it never makes a marble German or a book first-edition. Same
rail the specializations carry: refine, don't override.

**Blocks are hard.** A claim in `ctx.blocked` (smoke-free, pet-free,
climate-controlled and variants) is forbidden, not flagged, at any
confidence — even where photos alone would support it. DRAFT is the
enforcement point (see draft.md); it also applies to IDENTIFY/
INVESTIGATE/PRICE simply by never asserting it.

**`source:` never leaves this file.** It may name a person for local
reference; nothing downstream — chat, a stage's own output file, the
review card, buyer-facing copy — repeats it. `brief()`/`public_keys`
already omit it; don't read `.keys["source"]` into anything that isn't
purely local.

**Absent chain = today's behavior.** No `context.txt` anywhere in the
chain changes nothing.

## Unit type and quantity

Every item record carries `unit_type` + `quantity`.

| `unit_type` | `quantity` | Meaning | Listed as |
|---|---|---|---|
| `single` | 1 | One thing, listed alone. | 1 listing |
| `pair` | 2 | Two identical things sold together (bookends, candlesticks, earrings). | 1 listing |
| `set` | 3+ | Functional/conventional unit (chess set, dinnerware service, tool kit). | 1 listing |
| `lot` | 2+ | Separate items grouped for convenience, not a functional unit. | 1 listing |
| `duplicate` | 2+ | N copies of the SAME item, each listable alone. | N listings (or 1 w/ eBay qty=N) |

Only `duplicate` is the multi-listing flag.

**Default: `single`, qty=1.** Visual evidence of grouping does NOT
auto-promote. Only an explicit user instruction, or the user's answer to
a grouping question, changes a record off the default. When photos
suggest a grouping the user hasn't named, keep the default and log the
question (see gate contract — this is a SOFT gate, never a stop).

**Threading:** PRICE queries the selling unit (pair/set/lot framing in
the query); INVESTIGATE phrases titles to match ("Pair of…", "Set of
N…", "Lot of N…"); CURATE applies weight-tier math to the selling unit;
DRAFT renders eBay Quantity per the table (1 for single/pair/set/lot, N
for duplicate).

## eBay character limits (enforce at the producer)

Limits propagate upstream so nothing downstream has to truncate. Counts
are Unicode characters.

| Field | Cap |
|---|---|
| Title | 80 |
| Item-specifics value (each) | 65 |
| condition_description | 1000 |
| UPC | 20 |

- IDENTIFY: write Brand / Type / Era as the canonical short value a
  seller would actually use (≤65). Rich context goes in Distinguishing
  marks (free-text, uncapped), never stuffed into a capped field.
- INVESTIGATE: every title candidate ≤80 (emit `[N/80]`); every
  item-specific ≤65 (emit `[N/65]`). Validate before writing.
- PRICE: queries mirror real seller-title keyword density (5–9 high-
  signal words) so exact-match comps can exist.
- DRAFT: defense-in-depth — rephrase (never mid-word truncate) any value
  that slipped through, log the adjustment to `meta.notes`.

The authoritative machine-readable limits live in the template's
`_field_constraints` block ([templates/listing-v1.md](../templates/listing-v1.md)).

## Output-file persistence

Every phase writes a deterministic file in the shoot directory, UTF-8,
overwriting the prior run (latest run = current record).

| Phase | File |
|---|---|
| IDENTIFY | `<shoot-dir>/identify.txt` |
| PRICE | `<shoot-dir>/price.txt` |
| CURATE | `<shoot-dir>/review.md` |
| INVESTIGATE | `<shoot-dir>/investigate.txt` |
| DRAFT | `<shoot-dir>/draft.md` |
| REVIEW | `<shoot-dir>/review_card.md` |
| (any deferred question) | `<shoot-dir>/NEEDS_REVIEW.md` (append, don't overwrite) |

The shoot directory is the directory containing the photos — by default an
`inventory/<shoot-name>/` dir at the repo root. `inventory/` is the default
content store ("our" data: photos + per-item outputs); it is **gitignored and
never version-controlled**.

## Gate contract (what may stop a headless run)

In a headless run, ONE gate stops it — REVIEW; everything else proceeds with a
logged default. An **interactive** run adds one more: IDENTIFY's maker-mark
stop-and-ask. Full contract in [RUN.md](../RUN.md); summary:

- **HARD — stop and ask:** the REVIEW gate — after DRAFT, present the
  review card and STOP; publish LIVE only on explicit approval
  ([review.md](review.md)). (PRICE's Apify call is no longer a gate — it
  runs automatically as Stage B of the comp hunt.)
- **HARD (interactive only) — stop and ask:** IDENTIFY's maker-mark gate —
  in a gate category (jewelry, precious metals, glass, pottery — editable list in
  [identify.md](identify.md)) with a mark that's likely-present but undecisive,
  stop and ask the user to read the inside marking before searches or settling
  Brand. A clear no-mark-likely exception may skip it (logged). Headless (no
  user to ask) → degrades to SOFT (`needs_followup_photo` + a `NEEDS_REVIEW.md`
  line, then proceed).
- **HARD (interactive only) — stop and show:** the marble CROP gate — for a
  bulk/group marble shoot, after generating the per-marble crops + numbered
  contact sheet (`marble_triage --crops-only --expect N`), present the contact
  sheet to the user and STOP; begin IDENTIFY only on their go-ahead. Headless
  (no user) → SOFT: self-verify crop count == expected, log any mismatch to
  `NEEDS_REVIEW.md`, then proceed. See
  [marbles.md](../specializations/marbles.md) (⛔ CROP GATE).
- **SOFT — proceed with default, log to `NEEDS_REVIEW.md`:** grouping
  questions, unit_type ambiguity, INVESTIGATE open questions, working-
  price selection, lookup-value substitutions, missing required fields,
  the **local-pickup suggestion** for ship-risky items (heavy/oversized/
  fragile — DRAFT suggests pickup-only but never assumes it; default
  `SHIP`, see [draft.md](draft.md) "Local-pickup gate").

A SOFT gate never blocks. Pick the documented default, write a one-line
entry to `NEEDS_REVIEW.md`, keep going.
