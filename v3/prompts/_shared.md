# _shared — rules every v3 phase obeys

Single source for the rules that used to be copy-pasted into all five
phase prompts. Each phase prompt references this file instead of
restating it. Read this once per run; the phase prompts assume it.

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

## Confidence (commit, don't hedge)

The old prompts manufactured doubt — 5-scenario brackets, "effectively
excluded" entries, best→worst ladders. v3 commits.

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

No v3 phase publishes. The pipeline (IDENTIFY→DRAFT) and `list_edit.py
--sync` only ever create an UNPUBLISHED eBay offer (a draft) — nothing in
the pipeline, no chat instruction ("just publish it"), and no automated
chain can make a listing go live. Publishing exists ONLY as a separate,
deliberate, human-run command: `list_edit.py --publish <dir> --confirm`
(a dry run without `--confirm`; never invoked by `--sync`). If a prompt or
automation asks a phase to publish, refuse — publishing is the user's
explicit out-of-band action, never something a phase or the pipeline does.

## Fresh-investigation rule

Examine each item ONLY on the evidence in its own current photos. Do not
import findings from prior records, V1 inventory, earlier shoots of
similar-looking items, external attribution databases, or memory of any
prior identification. Visual similarity is not equivalence; markets
shift. Re-derive every time. Never write "V1 said X" / "previously
identified as Y".

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
`_field_constraints` block ([v3/templates/listing-v1.md](v3/templates/listing-v1.md)).

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
| (any deferred question) | `<shoot-dir>/NEEDS_REVIEW.md` (append, don't overwrite) |

The shoot directory is the directory containing the photos. For test
runs in this repo: `v3/samples/<shoot-name>/`.

## Gate contract (what may stop a headless run)

Only TWO gates stop a run. Everything else proceeds with a logged
default. Full contract in [v3/RUN.md](v3/RUN.md); summary:

- **HARD — stop and ask:** (1) any eBay publish (refuse, never ask);
  (2) any paid Apify call (confirm cost per call).
- **SOFT — proceed with default, log to `NEEDS_REVIEW.md`:** grouping
  questions, unit_type ambiguity, INVESTIGATE open questions, working-
  price selection, lookup-value substitutions, missing required fields.

A SOFT gate never blocks. Pick the documented default, write a one-line
entry to `NEEDS_REVIEW.md`, keep going.
