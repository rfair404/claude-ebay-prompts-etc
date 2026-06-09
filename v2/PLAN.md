# V2 Plan

Working document. We're outlining the functional parts of the app first, before any architectural decisions (data layer, eBay integration, Claude boundary, language/runtime).

Ground rules:
- Functions only at this stage — no schemas, no implementation, no scope creep.
- Each function gets captured in the user's own words first; ambiguities flagged inline.
- Architecture forks come after the function list is settled.

---

## Cross-cutting principles (apply to ALL functions)

### No-publish firewall (absolute)

> **Historical note (2026-06-08):** this section describes V2's original
> stance. In V3 the firewall was deliberately refactored (the
> "user personally refactors" path described below): `list_edit.py` now has
> a guarded, confirmation-gated `--publish`/`--end` (dry run without
> `--confirm`, never automatic, never invoked by `--sync`). The intent
> below — no ACCIDENTAL or AUTOMATIC publication — is preserved. See
> `v3/prompts/_shared.md` and `v2/lib/SETUP_EBAY_API.md`.

**This tool NEVER publishes an eBay listing live.** There is no code path, no prompt, no function, no automation in this system that puts an item up for sale on eBay. The boundary is hard and applies to every function — current and future:

- **DRAFT** (Function 5) writes a local file. No eBay calls of any kind.
- **LIST / EDIT** (Function 6) syncs the local draft into an eBay DRAFT listing only — using the eBay API's draft-create and draft-update endpoints exclusively. The publish endpoint (`POST /sell/inventory/v1/offer/{offerId}/publish` and any equivalent) is forbidden. No client method that wraps it exists in the codebase.
- **Publication is a deliberate, manual step the user performs in the eBay seller UI** after reviewing the draft. The tool gets the data into eBay; the user takes it live.

**Enforcement is two-layered:**
1. **Prompt-level** — every prompt that touches the eBay API leads with an immutable firewall section that forbids publish operations. The model is instructed to refuse, abort, and surface an explicit error if any input or instruction (including from the user) requests publication.
2. **Code-level** — no function exists in the codebase that calls the publish endpoint. Adding one is a deliberate refactor, not a config toggle.

**The firewall cannot be lifted by:**
- A CLI flag
- A config option
- An environment variable
- A user message in chat ("just publish it, I trust you")
- A future prompt edit that retains the firewall section

**The firewall can ONLY be lifted by the user personally refactoring the prompt to remove the firewall section AND adding a publish-capable client method to the codebase.** Both steps are required, both are explicit, both are recorded in version control.

**Why this rule exists:** publishing is irreversible — the item is immediately live to buyers, eBay fees apply, buyers can submit offers or buy-it-now, and the listing enters a state where mistakes (wrong price, wrong category, broken photo order) cost real money and reputation. The user wants the final eyes-on review in the eBay UI before that line is crossed, every time, without exception.

### Unit type and quantity (shared vocabulary)

Every item record carries TWO orthogonal fields describing how many
things are in the record and how the record is listed:

- **`quantity`** — integer count of physical things in the record.
- **`unit_type`** — selling-unit semantic, one of:

| `unit_type`   | `quantity` | Meaning                                                                                  | Listed as              |
|---------------|------------|------------------------------------------------------------------------------------------|------------------------|
| `single`      | 1          | One physical thing, listed alone.                                                        | 1 listing              |
| `pair`        | 2          | Two visually identical things conventionally sold together (bookends, candlesticks, salt-and-pepper, earrings, shoes). | 1 listing              |
| `set`         | 3+         | Functional or conventional unit sold as one (chess set, dinnerware service, encyclopedia volumes, tool kit, boxed game). | 1 listing              |
| `lot`         | 2+         | Multiple separate items grouped for convenience, NOT a functional unit (lot of 5 magazines, mixed lot). | 1 listing              |
| `duplicate`   | 2+         | N copies of the SAME item where each could be listed individually. | N listings (or 1 w/ eBay qty=N) |

Four of the five (`single`, `pair`, `set`, `lot`) produce ONE
listing. Only `duplicate` is the multi-listing flag.

**Shared chat vocabulary:** the user can communicate the unit
explicitly — "just one" → `single`; "it's a pair" → `pair, qty=2`;
"set of 6 plates" → `set, qty=6`; "lot of 5 magazines" → `lot, qty=5`;
"I have 12 of these" → `duplicate, qty=12`. IDENTIFY auto-classifies
from photos; the user can override at any function boundary.

**Default rule:** every record produced by IDENTIFY defaults to
`unit_type: single, quantity: 1`. Visual evidence of multiple items
does NOT auto-promote a record to `pair` / `set` / `lot` /
`duplicate`. Only an explicit user instruction (in chat) or the
user's answer to an IDENTIFY clarifying question can change a
record from the default. If photos suggest a possible grouping,
IDENTIFY surfaces it in the SHOOT SUMMARY's "Needs user
confirmation" line and waits for the user's call.

**Threading through the pipeline:**
- IDENTIFY defaults every record to `unit_type: single, quantity: 1`
  and surfaces grouping candidates as questions in the SHOOT SUMMARY.
  The user explicitly opts records into `pair` / `set` / `lot` /
  `duplicate` via chat instruction or answer to the question.
- PRICE queries the SELLING UNIT, not pieces (so a pair query
  includes "pair", a set query includes "set"). Comp prices are
  per-listing-unit, except `duplicate` which is per-piece.
- INVESTIGATE threads `unit_type` into title and description
  phrasing ("Pair of...", "Set of N...", "Lot of N...").
- CURATE pluralizes the buy card and applies weight-tier math to
  the SELLING UNIT, not the piece. `duplicate` requires each unit
  to clear the floor independently.
- DRAFT renders eBay's Quantity field per unit_type and chooses
  the correct title phrasing.

The deferred BUNDLE function (post-MVP) is the step that lets the
user reassign separately-enumerated `single` records into a single
`lot` record.

### Output-file persistence rule

Every function writes its output to a plain text or markdown file in the shoot directory. Files are overwritten on every re-run (the latest run is the current record). Standard file names:

| Function | Output file |
|---|---|
| 1 — IDENTIFY | `<shoot-dir>/identify.txt` |
| 2 — PRICE | `<shoot-dir>/price.txt` |
| 3 — CURATE | `<shoot-dir>/review.md` |
| 4 — INVESTIGATE | `<shoot-dir>/investigate.txt` |
| 5 — DRAFT | `<shoot-dir>/draft.md` (when implemented) |

**Directory layout:** one subdirectory per shoot. For test runs in this repository, that is `v2/samples/<shoot-name>/`. In production CLI use, the shoot directory is the directory containing (or alongside) the input photos.

**Overwrite policy:** every run overwrites the prior file. The user's filesystem / version control handles history if needed. This keeps each shoot directory clean and the "current state" obvious at a glance.

**Why this matters:** these files are the durable record of the workflow. The user reviews them at publish time to make final decisions, refers back to them weeks later for re-listings, and uses them as the audit trail for what claims were made about each item. The files must always be written — not just discussed in the chat output.

### eBay character limits — enforced upstream, not just at DRAFT

eBay's form has hard character limits on every user-visible field — the most load-bearing being title (80 chars), item-specifics tag-select values (65 chars each), and condition_description (1000 chars). These limits are documented in `ebay-fields-all.txt` and codified in the template's `_field_constraints` block at [v2/templates/listing-v1.md](v2/templates/listing-v1.md).

**The limits propagate upstream through the pipeline** so that no downstream function has to truncate or rewrite:

- **IDENTIFY** — fields that map directly to eBay item-specifics (Brand, Type, Era) are soft-capped at 65 chars. Long descriptive context goes into `Distinguishing marks` (free-text, no cap), not into the structured fields.
- **INVESTIGATE** — title claims in "Listing-safe claims" are hard-capped at 80 chars each (with char counts emitted next to every entry); item-specifics values in the same section are hard-capped at 65 chars. A pre-emit validation step walks the section before output is finalized.
- **PRICE** — search queries are guided to mirror real eBay seller-title keyword density (typically 5–9 high-signal keywords). Real seller titles are ≤80 chars, so query keywords drawn from realistic seller-title language are the keywords that can match real comps. Queries themselves can exceed 80 chars in the search box, but keyword density should reflect what sellers actually write.
- **DRAFT** — the consumer's defense-in-depth pass: the prompt has an authoritative `_field_constraints` table, per-field-type rephrase rules, and a pre-write validation pass that walks every entry. Any constraint violation that slipped through upstream is rephrased here, with the adjustment logged to `meta.notes`.

**Why upstream enforcement matters:** if INVESTIGATE emits a 120-char title claim, PRICE's exact-match comp search runs against language no real eBay seller fit into their title — so exact-match comps cannot exist by construction. DRAFT then has to rewrite the title from scratch, losing the careful phrasing INVESTIGATE chose. Enforcing at the producer (INVESTIGATE) keeps the careful phrasing intact all the way to the listing.

### Fresh-investigation rule

Every function — IDENTIFY, PRICE, INVESTIGATE, and any future additions — examines each item ONLY on the evidence visible in the current photo set. Do not import findings from:

- Prior version (V1) records or historical inventory documentation
- Previous shoots of items that LOOK similar
- External attribution sources or auction-record databases
- Memory of any prior identification of comparable items
- Stale comp data from previous market positions

**Rationale:** past findings about similar items are NOT evidence about THIS item. Visual similarity does not establish equivalence — two items that look identical may have different makers, dates, conditions, or attributions. Markets also shift, so historical comp data does not predict current market position. Re-investigating fresh prevents stale findings from propagating into new listings and protects against the failure mode where an inaccurate prior record becomes self-reinforcing across multiple downstream uses.

**Practical implications:**
- IDENTIFY treats each shoot directory as a first-time examination.
- PRICE runs a fresh comp call per item per session; does not skip because "we comped a similar one before."
- INVESTIGATE never references prior records in its report ("V1 said X" / "previously identified as Y" are explicitly forbidden) — it produces output as if the item has never been examined before.
- DRAFT consumes only the CURRENT INVESTIGATE report, not historical listings of similar items.

This rule was added after a real example surfaced where V1 records mislabeled a 2-mailer pair (one mailer was actually from a different year than recorded). The fresh-investigation rule prevents that kind of error from carrying forward.

---

## Workflow modes (top-level structure)

V2 has TWO distinct workflow modes that correspond to fundamentally different user contexts. The five core functions group into these two modes; the user invokes a workflow mode rather than chaining functions manually.

### Planning mode (pre-acquisition)

**Context:** user is in the field (estate sale, yard sale, thrift store) deciding what to buy.

**Pipeline:** `IDENTIFY → PRICE → CURATE`

**Operating direction:** speculative-upward — best-case identification surfaces upside potential, so the user can see what an item might be worth pursuing.

**Default shoot mode:** `wide` (overview shots, multiple items per frame).

**Output:** buy list (`review-YYYY-MM-DD.md`) with go/no-go price thresholds per item.

**User mindset:** "Is this worth pursuing? At what max price?"

**CLI entry point:** `ebaybiz plan <photos-directory>`

### Listing mode (post-acquisition)

**Context:** user has acquired the item(s) and is preparing to sell.

**Pipeline:** `INVESTIGATE → DRAFT` (optionally re-run `PRICE` for fresh pricing)

**Operating direction:** conservative-upward — defensible-claims identification surfaces only what's visually verifiable, so the listing doesn't overstate.

**Default shoot mode:** `single` (multiple angles of one item, or `group` for a bundle).

**Output:** listing-ready package (title, description, item specifics, photo order, suggested price).

**User mindset:** "How do I list this honestly without triggering disputes?"

**CLI entry point:** `ebaybiz list <photos-directory>`

### Orthogonality with shoot mode

Workflow mode (planning / listing) is orthogonal to shoot mode (wide / single / group / multi-angle / auto). Defaults pair them most-commonly:
- planning + wide (estate-sale walkthrough)
- listing + single (detailed re-shoot of one item)

But overrides are valid:
- planning + single (considering one high-value item)
- listing + group (bundle/lot listing)

Workflow mode picks WHICH FUNCTIONS run. Shoot mode modulates HOW IDENTIFY enumerates.

---

## Core functions

### Function 1 — IDENTIFY

**User description (verbatim):**
> Identify an item or items from a photograph. Could be a collection (stack of books, stack of movies) or a single thing (bicycle, record player). Connect to an AI that is really good at processing images FAST and accurately.

**What this function does:**
- Input: a directory containing one or more photographs. The directory represents a single "shoot" — could be one item from multiple angles, or a collection (stack of books, group of items on a table). All images in the directory are processed in a single pass.
- Output: a structured list of items recognized across all photos, deduplicated (if the same item appears in multiple shots, it's one record).

**Shoot modes (added after testing — IDENTIFY now context-aware):**

IDENTIFY's enumeration and dedup behavior shifts based on the structure of the shoot. Five modes:

- **wide** — multiple distinct items in one or more photos (estate-sale / yard-sale / vignette scenes). Current default behavior. Aggressive enumeration, top-to-bottom, left-to-right ordering.
- **single** — multiple photos of ONE physical item from various angles. Produces ONE record using all angles for max field confidence (resolves more `[BEST-CASE]` assumptions because more info is available).
- **group** — multiple items of the same category laid out together (stack of comics, tray of jewelry). Enumerate each separately; do NOT cross-dedup unless items are clearly identical duplicates (then collapse with `quantity: N`).
- **multi-angle** — mixed scene shot from multiple angles. Combines angle dedup with enumeration.
- **auto** — model inspects photos and picks the best-fit mode, surfacing the choice in SHOOT SUMMARY for user correction. Default when no mode is supplied.

**How the mode is specified (three knobs in precedence order):**

1. CLI flag at runtime — `ebaybiz identify <dir> --mode single` (highest precedence, per-run override)
2. Strategy profile default — `~/.ebaybiz/config.yaml` sets a default mode per profile (e.g., a vintage-clothing reseller defaults to `single`; an estate-sale flipper defaults to `wide`)
3. Auto-detect fallback — model infers from photo content; surfaces inferred mode in output

Mode is shoot-level (one mode per directory), not per-item. SHOOT SUMMARY always includes the active mode and its source (specified / from profile / auto-detected).

**Downstream functions are mode-agnostic:** PRICE and CURATE process whatever items come out of IDENTIFY regardless of mode. No prompt changes downstream.

**Disambiguation rule (refined after Function 1 manual testing):**

IDENTIFY treats multiple physical items as ONE record only when they meet at least one of these conditions:

1. **Functional unit** — items designed to be used together as a single thing (chess set, dinnerware service, tool kit, boxed game, model kit).
2. **Mounted / cased / framed together** — items physically combined into a single display piece (framed medal display, mounted coin collection).
3. **Matched pair / set** — visually identical items conventionally sold as a unit (pair of identical candlesticks, bookends, salt-and-pepper shakers).
4. **Provisional group with follow-up flag** — items that are clearly alike (same category, similar visual appearance) but cannot be cleanly enumerated from the available photos. Group them as one record AND mark `needs_followup_photo: yes` with a note describing what additional shots would allow proper splitting.

Otherwise items are enumerated separately — even if they share a brand, an era, or are stacked together for storage. Downstream BUNDLE step (deferred) can group related-but-separate items into a lot listing if desired.

**Angle dedup remains:** the same physical item seen from multiple angles in different photos = ONE record.

**New per-item field:** `needs_followup_photo: yes/no` with a note describing what shot is needed. Used both for provisional groups (above) and for any item the model can't confidently identify from the photos provided.

**Non-inventory items:** items clearly not for sale (a handwritten note, a pen, packaging, furniture) are still enumerated but marked `collectability: none / not for sale` so CURATE can filter.

**Best-case identification with multi-scenario bracket (added after testing):**

When brand, maker, era, or material can be reasonably inferred but not confirmed without further inspection (a label, hallmark, signature, date page, material test):

- IDENTIFY's default operating stance is **best-case** — operate as if the item is the high-end identification. Example: an unlabeled multi-stripe wool blanket with classic Hudson's Bay coloring → `Brand: Hudson's Bay [BEST-CASE]`, not `[ASSUMPTION] Hudson's Bay / Pendleton / Faribault style`.
- Every `[BEST-CASE]` marker is paired with a `Scenario bracket` block on the item record.
- **Multi-scenario support:** the bracket lists 2–5 scenarios (best → worst), not just binary best/worst. Intermediate scenarios capture meaningful identifications that sit between the extremes (Hudson's Bay vs. Pendleton vs. Faribault vs. unbranded — four scenarios). Use the right number for the item.
- The bracket includes a "How to distinguish" section — specific observables or tests that separate the scenarios, telling the user what to verify.
- PRICE consumes the bracket by generating one eBay query per scenario, returning comps tagged by scenario. CURATE then presents the price range as "if Hudson's Bay → $X–$Y; if Pendleton → $A–$B; if unbranded → $C–$D."
- Distinction from `[ASSUMPTION]`: `[BEST-CASE]` is for inferences that materially change value tier; `[ASSUMPTION]` is for approximate inferences within a single tier (e.g. "1970s, mid-to-late" where any 70s date prices similarly).
- The `[BEST-CASE]` marker is not a license to invent — it requires a genuinely plausible visual basis.

**Per-item output fields:**
- **Category** — "what is it" at the general level. E.g. `bike`, `record player`, `book`, `backgammon set`.
- **Brand** — maker, when visible/identifiable. E.g. `Diamondback`, `Victrola`, `Polo Ralph Lauren`.
- **Type** — more specific descriptor. E.g. `Boys BMX bike`, `Hand-crank record player`, `Hardcover photo book`.
- **Era** — best-effort date or date range (`1980s`, `c. 1975`, `early 90s`).
- **Collectability tier** — one of: `collectable` / `vintage` / `antique` / `modern`. Drives downstream pricing strategy.
- **Condition** — included in the same single pass ("tell me everything you can see"). Free-text notes on visible wear, defects, completeness. Refinement of structure (free-text vs. defect list vs. both) deferred until we see the model output in practice.
- **Estimated weight** (added after testing) — best-effort weight estimate of the item itself (not packed shipping weight), with tier classification: light (<5 lb), medium (5–15 lb), heavy (15–25 lb), oversized (25–50 lb), freight (50+ lb), requires-movers. Consumed by PRICE (shipping cost estimates) and CURATE (weight-tier profit-floor multiplier). For matched-set groupings, give total weight of the set.
- **Estimated dimensions** (added after testing) — best-effort size estimate of the item (L×W×H for boxes, diameter×height for cylinders, W×D×H for furniture, free-text for irregular). Consumed by PRICE (dimensional-weight shipping math, USPS/UPS oversize detection) and CURATE (box-sizing decisions, oversize/freight flagging). For matched-set groupings, per-piece dimensions plus combined-pack note.

Implied but unstated — likely worth including:
- Confidence per field, or an `[ASSUMPTION]` marker for inferred values (carry over from V1's honest-marking convention).
- Free-text "distinguishing marks" note for anything the model wants to flag but doesn't fit the fields above (photographer credit, dated copyright page, unusual cover, etc.).

**Image AI — decided:**
- **Claude Sonnet 4.5 vision** for V2 iteration 1.
- **Gemini 2.0 Flash** planned as a secondary option in a later iteration (speed-critical or cost-sensitive paths). Not in MVP.
- Barcode/ISBN fast-path (pyzbar + OpenLibrary/Google Books) — deferred, possible phase 2 add-on.

**Output destination — decided (interim):**
- For MVP: write to a flat, human-readable text file the user can open and read.
- Long-term: a more versatile storage system (defined later, after all functions are scoped).
- Explicit: do not over-design storage at this stage.

**Settled for Function 1.** Ready to move to Function 2.

### Function 2 — PRICE (competitive pricing data gathering)

**User description (verbatim):**
> Perform competitive pricing data gathering. Assuming items are antique / collectable / vintage or niche, you can't get pricing from typical APIs or Google searches. Find what the exact same or nearly identical comparable item has sold for recently.

**What this function does:**
- Input: an item record from Function 1 (Category, Brand, Type, Era, Collectability, Condition, plus distinguishing marks).
- Output: a list of comparable sold items with prices, dated, classified by tier (direct match / branded ceiling / outlier).

**Where to look — source landscape:**

*Primary (MVP):*
- **eBay sold listings** — `LH_Sold=1&LH_Complete=1&_sop=3` (highest sold price first). The workhorse, validated by V1. Covers 70–85% of the user's typical inventory. No clean API path — public sold data is gated behind partner-only Marketplace Insights API. Access via web scraping or Chrome automation, handling `splashui/challenge` bot challenges.

*Secondary (phase 2 / category-dependent):*
- **WorthPoint** — paid (~$30/mo), 20+ years of eBay sold + auction archive. For long-tail items.
- **Terapeak** — eBay-owned, in Seller Hub. Subscription-gated.
- **LiveAuctioneers / Invaluable** — aggregate auction house realized prices. Free to browse. For higher-end pieces.
- **Heritage Auctions (HA.com)** — free archive. Coins, comics, sports memorabilia, fine art.
- **Discogs** — music media. Free API, condition-graded. Mandatory if music ever in inventory.
- **PriceCharting** — video games + trading cards.
- **Grailed** — high-end menswear / streetwear.
- **AbeBooks / Biblio** — rare books (asks, not sold; upper bound).
- **Specialty dealer archives** — for niches that fall off mainstream platforms.

*Non-structured signal (deferred):*
- Reddit niche subs (r/Flipping, vintage-specific subs) for rarity gut-checks.
- LLM-as-researcher with web search for ultra-niche items where structured data is thin.

**Tier framework (carry from V1):**
- **Tier A** — direct match (same era, size, completeness). Anchors pricing.
- **Tier B** — branded ceiling (named maker variant). Sets upper bound; unbranded comps must price below cheapest Tier B.
- **Tier C** — outliers (single bid, low-feedback seller, anomalous price). Excluded from anchor.
- Mark stale comps (>12 months) as `[STALE]`.

**Per-comp output fields (draft):**
- Source (eBay / WorthPoint / etc.)
- Title (as listed)
- Sold price
- Sold date
- Condition (as listed)
- URL
- Tier (A / B / C)
- One-line "what matches / what differs" note

**Access strategy — decided:**
- **No Chrome automation.** V1's Chrome-based approach is explicitly rejected for V2 — too slow, too fragile.
- **eBay via API** for V2 (not browser scraping with a real browser).
- Honest caveat: official eBay sold-data access (Marketplace Insights API) is partner-gated and not guaranteed to be approved. The eBay plugin's *implementation* may start as raw-HTTP scraping with bot-challenge handling, third-party API wrapper, or official API — whichever is available. The plugin *interface* hides this from the rest of the system.

**Plugin architecture — decided:**
- **Pricing sources are plugins.** eBay is the core/first plugin shipped in MVP. Additional sources (AbeBooks, Grailed, Discogs, LiveAuctioneers, WorthPoint, etc.) can be added later as drop-in plugins without changing core.
- Each plugin handles its own auth, rate limiting, error handling, and source-specific quirks.
- Plugins normalize their output to a common comp-record shape.
- Plugins can declare category applicability (Discogs only for music, Grailed only for high-end menswear, etc.) so the system can route queries intelligently.
- Plugin interface design itself is deferred — it's architecture, settled after function list is complete.

**Three-source search strategy (Apify tabled as opt-in after data-integrity testing):**

PRICE has THREE distinct search sources, each tagged in the output so the user always knows where a comp came from:

| Source | Cost | Speed | Coverage | Default behavior |
|---|---|---|---|---|
| **A — WebSearch** | $0 | ~5s | broad (Google indexing) | runs by default |
| **B — Claude in Chrome → eBay** | $0 | ~30–60s | direct eBay sold-listings | runs by default |
| **C — Apify** | **~$0.12/query** | ~10–30s | direct eBay (structured) | **TABLED — opt-in only**, with mandatory confirmation per call |

**Default behavior:** Sources A and B always run. **Source C is tabled** — the integration remains available but does NOT run by default and is NOT proactively suggested on every PRICE run. Source C is invoked only when:

1. **The user explicitly asks for Apify data** ("run Apify on this", "use the API method", "check Apify too"), OR
2. **Sources A and B both fail** to produce a usable comp set — in that single case, PRICE may surface ONE fallback prompt offering Source C as an option ("Sources A and B came back thin; want me to try Apify, ~$0.12, known coverage variability?").

Either way, the per-call cost-confirmation gate ("About to spend ~$0.12 to run an Apify query for X. Approve?") is non-negotiable — the human-in-the-loop pricing principle extends to the search-side too.

**Why Source C was tabled (documented in `samples/PRL-batches/.../lot2b/apify_bug_check.md`, 2026-05-26):**

Two-run reproducibility test on the `caffein.dev/ebay-sold-listings` Actor surfaced multiple data-integrity issues:

- **Severe coverage variability** — same query / same parameters / 30 seconds apart returned 17 vs. 30 results with only 11 overlapping items. The $214 exact-match comp for our test item was present in one run and absent in the other. Cross-referencing against Source B (Chrome) catches this; relying on a single Apify call does not.
- **GBP→USD silent conversion** — UK listings (e.g. seller `alciren`) get converted at the live FX rate but `sold_currency` still reads "USD," masking the original currency. Telltale: oddly-precise cents like `$94.51` / `$121.52` in price ranges where eBay normally uses round numbers. Math is internally consistent; the field is misleading.
- **Historical BRL inflation (prior session)** — 5.017× price inflation on Brazilian proxy exits (untransformed BRL labeled as USD). NOT reproduced in the 2026-05-26 retest, but the failure mode remains possible.

The historical BRL bug was the original motivation for opening this issue, but the coverage-variability finding is the more pressing operational problem — a single Apify call can silently miss the strongest available comp. Until either (a) the wrapper adds round-number heuristic flagging, multi-run merging, or currency override, or (b) we evaluate an alternative Actor (`astronomical_reception/ebay-sold-lite`, `marielise.dev/ebay-sold-listings-intelligence`, `midwest_united/ebay-sold-comps`), Source C is off the default path.

**Mandatory URL on every comp** — at both sources. The user must be able to click through and verify each comp.

**Human-in-the-loop pricing gate** — PRICE never autonomously commits to a price. Output leads with "Max supported price: $X" and explicitly closes with "Awaiting user approval. Accept $X as the working price, or refine? Final price decision deferred to publish time — this analysis is recorded for review then."

**Three-tier price-point structure** in every PRICE output (added after Polo RL Fall 1981 testing):
- **Conservative** — directly-defensible-no-objection price; closest era-peer or category-mid anchor
- **Recommended (max supported)** — the headline; strongest available comp (exact match if found, closest era-peer otherwise)
- **Push-high** — defensible upper bound; highest comparable comp with stated premium reason (rarity, store attribution, photographer credit, theme, year-specificity)

**Handling the "no exact match" case** (common for niche items like pre-1985 Polo RL catalogs):
- State explicitly that no exact-match comp was found in either source
- Anchor on the closest era-peer (with era-gap noted)
- Acknowledge market structure (no recent comps = rarity signal)
- Suggest the user save the eBay search as a passive watch
- Note SEO benefit of specific-year attribution even when no comp anchors it directly

**The PRICE output file is the persistent analytical record** — the user reviews it at PRICE time, may approve a working price for downstream functions, and revisits it at publish time to make the final price call. The file (`v2/samples/<shoot>/price.txt`) is the durable artifact.

**MVP plugin set:**
- **eBay** — the only plugin shipped in MVP. Framework supports more, but only one is implemented at first.
  - **Tier 1 backend:** WebSearch (already available, no setup).
  - **Tier 2 backend (interim):** Claude in Chrome MCP browser automation for direct eBay sold-listing browsing.
  - **Tier 2 backend (target, swap-in post-MVP):** Apify eBay scraper Actors — pay-per-result REST API (~$0.50 per 1000 results, $5 free credit).
  - **Backend (long-term target):** official eBay Marketplace Insights API once Application Growth Check approves access. Plugin interface unchanged.

**Optional later-add plugins (not MVP, but the architecture supports them):**

*Alternative eBay backends (swap-in if Apify becomes limiting):*
- **RapidAPI sold-listings wrappers** — cheap backstop, quality-variable.
- **ScraperAPI / ScrapingBee** — DIY extraction on top of managed scraping infra. Becomes attractive once multiple non-eBay plugins also need scraping.
- **Bright Data / Oxylabs** — enterprise-grade, only relevant at much higher volume.

*Additional source plugins (broaden coverage beyond eBay):*
- **Discogs** — music media (records, CDs). Free API.
- **Grailed** — high-end menswear / streetwear.
- **AbeBooks / Biblio** — rare books.
- **LiveAuctioneers / Invaluable / Heritage Auctions** — auction-house realized prices for higher-end pieces.
- **WorthPoint** — deep historical comps archive (~$30/mo).
- **PriceCharting** — video games, trading cards.

Each is a separate plugin, enabled per-item-category. Add when a category becomes a real part of the user's inventory.

**Known risk to revisit:**
- Marketplace Insights API approval timeline and outcome. Apify keeps the system shipping in the meantime.

**Query generation — decided:**
- **LLM-built with user edit step.** Claude reads the Function 1 item record (Brand + Type + Era + distinguishing marks), proposes the best eBay search query. CLI surfaces the proposed query; user can accept, edit, or override before it runs. Combines smart construction with a human sanity-check before any Apify call is spent.

**Reasonable defaults for remaining minor decisions (flag if you want to change):**
- **Comps per item:** Apify returns up to ~20 raw hits; Claude classifies into Tier A/B/C and filters/summarizes down to the most useful (~10 max in the flat file).
- **Output destination:** appended to the same flat text file as Function 1's output, under each item's record. (Per the interim flat-file decision; revisits when storage is designed.)
- **Re-run policy:** replace prior comps for that item, with a "comps refreshed YYYY-MM-DD" timestamp. No versioning in MVP.

**Per-item market research (added after blanket test):**

PRICE doesn't just gather price comps — for each item, it also produces a research summary with:

- **Authenticity & fakes** — does this category have a meaningful repro/fake problem? Common tells, authentication steps. (Bakelite hot-water test, Hermès stitch counts, Tiffany lamp repros, vintage band tee tag dating, etc.)
- **Listing restrictions & legal issues** — eBay-prohibited items, CITES treaty (ivory, bone, taxidermy, certain furs), NAGPRA (Native American cultural items), militaria region-bans (Nazi-era in Germany/France/Austria), hazardous materials, expired items, recalls, US-state-specific restrictions, international shipping eligibility.
- **Known scams & buyer issues** — switch-in-box returns (sneakers/handbags/watches), altered grading slabs (cards/coins), "didn't work" returns (electronics), vintage clothing dispute patterns. Recommendations for protection.
- **Authentication services available** — does eBay's Authenticity Guarantee program cover this category at this value tier?
- **Disclosure recommendations** — what should the seller explicitly disclose in the listing to head off returns/disputes?

This research lives in the PRICE prompt because the model needs the item context and the comp data together to produce useful guidance. Documented in `v2/prompts/price.md`.

**Prompts now exist in repo:**
- `v2/prompts/identify.md` — Function 1 prompt with multi-scenario bracket
- `v2/prompts/price.md` — Function 2 prompt with query generation + classification + research
- `v2/prompts/curate.md` — Function 3 prompt with buy-list output format, profit-floor logic, sort order, two-threshold buy-point math
- `v2/prompts/investigate.md` — Function 4 prompt with conservative-upward defensible-claims analysis
- `v2/prompts/draft.md` — Function 5 prompt: template-fill transform that reads identify.txt + investigate.txt + price.txt and renders the listing-v1 template into `<shoot-dir>/draft.md`, enforcing `_field_constraints` (maxLen, required, numeric, lookup-only) and surfacing every mapping decision in `meta.notes`

**Settled for Function 2.** Ready to move to Function 3.

### Function 3 — CURATE (buy-list / decision support)

_(Promoted from Function 4 after pivot to buy-side workflow. DRAFT moved to Function 4.)_

**Reframed purpose:** CURATE produces a **prioritized buy list** the user can take into the field (estate sale, yard sale, thrift store, etc.) and use to make confident go/no-go buy decisions at the moment of purchase.

**Input:** all item records from IDENTIFY + their PRICE outputs + the active strategy profile config.

**Output:** a single sorted markdown buy list (`review-YYYY-MM-DD.md` or similar) with one decision card per saleable item.

**Sort order:**
1. **Collectability tier** (descending — most sought-after first):
   `collectable > antique > vintage > modern`
   Items marked `collectability: none (not for sale)` are filtered out entirely.
2. **Best-case sale price** (descending) within each tier.

**Per-item buy decision card includes:**
- Best-case / realistic / worst-case sale price expectations (lifted from PRICE's scenario brackets)
- **Buy Point** — single dollar threshold: "buy if ≤ $X, pass if higher." Computed from worst-case sale × strategy-profile multipliers (margin target, fees, shipping, acquisition friction)
- **Pre-buy confirmation checklist** — the assumptions to verify in person before committing (lifted from each item's Scenario bracket "How to distinguish" section)
- **Acquisition friction notes** — shipping size/weight class, fragility, Media Mail eligibility, special handling

**Strategy profile** drives the Buy Point math (configurable via `~/.ebaybiz/config.yaml` with named profiles per the earlier decision). Profiles encode:
- Margin target (e.g. 50% — sell for at least 2x buy price)
- Buy Point multiplier (e.g. 0.5 of worst-case sale floor for confident buying; 0.4 for more aggressive)
- eBay + payment fee assumption (e.g. 13%)
- Shipping cost estimator (by weight class / fragility)
- Acquisition cost components (drive distance × $/mile, time cost)
- Packing time / overhead for fragile items

**Items with thin PRICE data** (the magazine stack case) get a "needs more data — re-shoot / verify before deciding" flag instead of a Buy Point.

**Profit floor (per-item viability filter):**

Items where the maximum reachable profit cannot clear a configurable floor are excluded from the buy list entirely. The reasoning is labor cost — photographing, listing, packing, shipping, post-sale support all take time, and small-margin items don't repay that effort.

- **Default base floor:** $100 net profit per item.
- **Weight-tier multiplier on the floor** (added after pottery shoot testing):
  - Light (<5 lb): 1.0× → $100 effective floor
  - Medium (5–15 lb): 1.0× → $100
  - Heavy (15–25 lb): 1.5× → $150
  - Oversized (25–50 lb): 3.0× → $300
  - Freight (50+ lb): 5.0× → $500
  - Requires movers: skip entirely unless ultra-high-value override
- **Formula:** `best_case_sale_upper × (1 - fee_pct) − shipping_estimate − conservative_buy_point ≥ effective_floor` (where `effective_floor = profit_floor × weight_multiplier`)
  → If false, the item is filtered into the "skipped — below floor" section. The skipped entry shows the weight tier and elevated floor in the math.
- **Rationale:** heavy items consume more photographing time, more pack time and materials, more shipping cost, have a smaller buyer pool (local-pickup-only is geographically constrained), and carry higher damage-claim risk. User explicitly prefers small/light/easy-to-ship inventory; heavy items must be substantially more profitable to be worth the labor and logistics burden.
- Skipped items are shown with the math so the user can see what was filtered and override if a category exception applies (e.g. niche items that bundle well).
- **Strategy implication:** under a $100 floor, low-margin items (small ephemera, mass-market paperbacks, common school supplies) get filtered out. This pushes inventory strategy toward higher-value items OR makes the deferred BUNDLE function more important (combining multiple sub-floor items into a single above-floor listing).
- Floor is configurable in the strategy profile.

**Settled.** The original Function 4 review-file-with-decision-blocks pattern is replaced by this buy-list pattern.

---

### Function 4 — INVESTIGATE (defensible identification per acquired item)

_(Added after iron-weight single-mode test. Runs on items the user has acquired, before DRAFT.)_

**Purpose:** produces a defensible identification report for ONE item. The output drives DRAFT's listing copy and prevents over-stated claims that could trigger INAD returns, eBay policy violations, or buyer disputes.

**Operating principle (opposite of IDENTIFY):**
- IDENTIFY starts at BEST CASE and surfaces what would need verification. Speculative-upward.
- INVESTIGATE starts at DIRECTLY OBSERVABLE and builds up cautiously only when visible evidence defensibly supports each additional claim. Conservative-upward.

**Critical assumption:** the photos are ALL the visible evidence. If a maker mark, signature, date stamp, or distinguishing feature is NOT visible in the photos, treat it as if it does not exist. No speculating about hidden sides.

**Output structure:**
1. Directly observable from the photos (plainly visible facts)
2. Defensible inferences (reasonable from visible evidence, with stated basis per inference)
3. NOT defensible from these photos (explicit list of what cannot be claimed)
4. Listing-safe claims — title phrases, description sentences, item-specifics values — that DRAFT consumes verbatim
5. Open questions (specific, actionable photo follow-ups that would unlock stronger claims)
6. Listing approach recommendation (paragraph on how to present this item conservatively)

**Where it fits:** between CURATE (buy decision) and DRAFT (listing creation). Typically runs after acquisition when the user has the physical item and possibly fresh detailed photos.

**Per-item, not per-shoot.** One INVESTIGATE report per acquired item.

**Prompt:** `v2/prompts/investigate.md`

**Settled.**

---

### Function 5 — DRAFT (create a draft listing locally — post-buy, post-investigate)

_(Moved from Function 4 after INVESTIGATE was inserted. Now consumes INVESTIGATE's listing-safe claims directly.)_

**User description (verbatim):**
> Creating a Draft listing — done locally, NOT integrated with eBay directly yet. Structured text document at first, that I can review, refactor to JSON or some other structured data type later. There should absolutely be a repeatable, consistent template, and templates should follow version specs. It would need to have the ability to contain a lot of fields in key:value kind of stores.

**What this function does:**
- Input: an item record (from Function 1) plus comps (from Function 2), and any pricing/curation decisions made between Function 2 and Function 3.
- Output: a single draft listing file on disk, structured, human-readable, ready for user review.
- **No eBay calls. Local file write only.** This function exists specifically so the user can iterate fast — open the draft in any editor, tweak, re-render, repeat — without any network round-trip or API quota involved. eBay-side work is the responsibility of Function 6 (LIST / EDIT), and even that function is firewalled from publishing (see "No-publish firewall" cross-cutting principle).

**Design principles (user-stated):**
- **Versioned templates.** Templates evolve over time; every generated draft records which template version produced it. Old drafts remain interpretable even after the template changes. Migration tooling possible later.
- **Heavy key:value structure.** eBay listings have many fields (brand, type, year, material, color, etc. plus pricing, shipping, returns). The draft must hold all of them cleanly.
- **Human-readable now, machine-readable easily.** Starts as a structured text file. JSON conversion later should be near-trivial — meaning we should pick a format that's already structured, not a freeform document.

**Recommended file format (subject to your call):**
- **YAML frontmatter + markdown body.**
  - Frontmatter (top of file, between `---` lines): every structured field — `template_version`, `item_id`, `brand`, `type`, `year`, `price`, `weight_lb`, `dims_in`, `category`, `item_specifics` (sub-map), etc.
  - Markdown body (below the closing `---`): the listing description — the only free-text section eBay actually wants in prose.
- Why this fits: native key:value, hierarchical (nested item-specifics), trivially loads into Python as a dict, trivially serializes to JSON later, human-readable in any text editor, version controllable in git, and matches the natural shape of an eBay listing (structured fields + one prose blob).
- Alternative: pure YAML (no markdown body, description becomes a multi-line YAML string). Cleaner, but loses easy-edit prose. I'd lean against.
- The V1 `listing_template.md` already organically had this shape; V2 just formalizes it.

**Template versioning approach (proposed):**
- Templates live as files in the repo: `templates/listing-v1.yaml`, `listing-v2.yaml`, etc.
- Each generated draft records `template_version: v1` in its frontmatter.
- Bumping versions is intentional: when fields are added/renamed/restructured, a new version file is created. Old drafts keep their original version tag.
- Optional later: migration script that converts a v1 draft into a v2 draft.

**Function boundary — decided:**
- DRAFT is a **pure rendering step.** Input: a finalized listing-set definition (one item OR a bundle, with a price already decided upstream). Output: one draft file.
- Curation, bundling, and pricing decisions live in **Function 4** (likely named CURATE or PRICE — to be confirmed when we define it).
- This keeps each function single-purpose: DRAFT doesn't reason about *what* to list, only *how* to render it.

**Fill behavior — decided:**
- **Auto-fill via Claude, user reviews.** DRAFT calls Claude with the listing-set definition + relevant item record(s) + relevant comps, and Claude fills in the full draft: title, description, condition disclosure, item-specifics values, etc.
- User reviews the resulting file and edits in any text editor before any push step.
- Matches V1 spirit (agent fills, user verifies) but moves the prompt logic into versioned code instead of versioned markdown files.

**File format — decided:**
- **YAML frontmatter + markdown body.**
  - Top of file: `---`, then all structured fields (template_version, item_id(s), brand, type, year, price, weight/dims, item_specifics map, etc.), then `---`.
  - Body: the listing description in plain markdown — the one section that's genuinely free-text.
- Trivial to parse to a Python dict, trivial to convert to JSON later, version-controllable, editable in any editor.

**Template versioning approach (settled):**
- Templates live as files in the repo: `v2/templates/listing-v1.md`, etc. The `.md` extension is used because the file is YAML frontmatter + markdown body — same shape as a generated draft.
- Each generated draft records `template_version: v1` in its frontmatter.
- The template's frontmatter carries an `_field_constraints` map (dot-path → `{ required, max_len, numeric, lookup_only }`) sourced from the eBay Add Item form capture at `ebay-fields-all.txt`. `list_edit.py:validate_draft_for_sync()` reads this map to enforce limits before any eBay API call. Inline YAML comments on each field carry the same constraint in human-readable form.
- Template changes that add/rename/restructure fields = new version file. Old drafts keep their original tag.
- Migration tooling is a later concern; the versioning is what makes it possible.
- **Frontmatter parsing:** anchor on `^---$` (lines containing only `---`), not on substring `---`. The template uses `# --- Section ---` style YAML comments internally, and a naive `split("---")` will mis-split. Use a regex like `^---\s*\n(.*?)\n---\s*\n(.*)` with DOTALL.

**Settled for Function 3.** Ready to move to Function 4.

**Historical context — original Function 4 spec (now superseded by the new Function 3 — CURATE buy-list spec above):**

The original Function 4 was a sell-side "review tentative drafts + finalize decisions" pass. It used three signals (Demand, Price spectrum HIGH/LOW, Buy Point). After the pivot to a buy-side workflow, those three signals are still the underlying inputs — but the output is now a sorted buy list rather than a review-and-edit pass. Strategy customization (config file + named profiles) and LLM-judged condition adjustment carry forward. Bundling stays deferred (see Function 5 below).

### Function 6 — LIST / EDIT (sync local draft to eBay DRAFT listing)

_(Added after the DRAFT vs. eBay-side split was made explicit. Runs after Function 5, only on user instruction, never as part of an automated chain.)_

**Purpose:** push a locally-finalized DRAFT file (from Function 5) into eBay as a **DRAFT listing** — either creating a new eBay draft or updating an existing one. The listing stays in eBay's draft state. Going live is a manual user action in the eBay seller UI.

**Hard constraint (see No-publish firewall cross-cutting principle):** this function NEVER publishes. No prompt path, no code path, no CLI flag, no chat instruction can cause publication. The firewall is immutable absent a deliberate user refactor of both prompt and client code.

**Why this is its own function (split from DRAFT):**
- DRAFT optimizes for fast local iteration — open in an editor, tweak, re-render, repeat — with zero network involvement.
- Once the user is satisfied with the local file, LIST / EDIT moves that data into eBay's system as a saved draft visible in Seller Hub, ready for the user's final review and manual publish in eBay's own UI.
- Keeping eBay-API surface area in exactly one function makes the firewall easier to audit: there is one place where "is this call publish-capable?" needs to be checked, not two.

**V1 pain points this function exists to fix (motivating context):**

V1 attempted to push listings to eBay by driving the **Seller Hub edit form** via Chrome automation. That approach failed repeatedly on two specific failure modes:

1. **Missing descriptions** — V1 frequently arrived at the description editor with no description ready to paste, or pasted a description that didn't survive eBay's rich-text editor (loss of paragraphs, dropped formatting, content cleared on a re-render). Symptom: published-ready listings going up with empty or half-filled description bodies.
2. **Image upload challenges** — V1's image upload step depended on the Seller Hub drag-and-drop widget, which combined unstable selectors, slow per-image upload, occasional silent failures, and ordering inconsistencies. Symptoms: listings going up with missing photos, photos in wrong order, or the upload step hanging entirely.

Function 6 resolves both classes of failure by **bypassing the Seller Hub form entirely** and using the eBay Sell API:

- **Description handling via the Inventory API.** The description body is a single field (`product.description`) on the InventoryItem object. It's submitted as one HTTP payload, not typed into a rich-text editor. Whatever the local DRAFT file contains in its markdown body is what eBay stores. No editor round-trip, no formatting loss, no empty-description failure mode.
- **Image upload via eBay Picture Services (EPS).** Photos are POSTed to EPS, which returns durable URLs. Those URLs are then attached to the InventoryItem in a defined order. No drag-and-drop, no widget-state surprises, no order randomness.

Both improvements depend on having the eBay developer key in hand. Until then, the function is **stubbed** (see Stub status below) — its surface area exists so the rest of the pipeline can wire against it, but every call fails fast with a clear "awaiting developer key" message.

**Input:**
- A finalized local draft file (YAML frontmatter + markdown body) from Function 5.
- The active eBay API credentials from config (developer key, OAuth tokens).

**Output:**
- A created or updated eBay DRAFT listing (inventory item + unpublished offer).
- The local draft file is annotated in-place with `ebay_offer_id`, `ebay_inventory_sku`, and `last_synced: <timestamp>` in its frontmatter, so subsequent runs know whether to create-new or update-existing.

**Behavior — create vs. edit:**
- If the local draft's frontmatter has **no `ebay_offer_id`** → CREATE flow:
  1. Upload local photos to eBay Picture Services (EPS), collect EPS URLs.
  2. Call `createOrReplaceInventoryItem` with the SKU-level fields (title, description, item specifics, condition, photos, weight, dimensions).
  3. Call `createOffer` with the listing-level fields (price, listing format, category, returns/shipping/payment policies). Offer is created **unpublished** — the offer object is the eBay-side "draft."
  4. Write back `ebay_offer_id` and `ebay_inventory_sku` into the local file frontmatter.
- If the local draft's frontmatter **has an `ebay_offer_id`** → EDIT flow:
  1. Re-upload changed photos to EPS if needed (compare local photo hashes to last-synced hashes).
  2. Call `createOrReplaceInventoryItem` on the existing SKU with the latest field values.
  3. Call `updateOffer` on the existing offer ID with the latest price / category / policy values.
  4. The offer remains unpublished. Update `last_synced` timestamp.
- **Idempotent:** running twice with no local changes is a no-op (or a trivial timestamp bump).

**Pre-flight validation (before any eBay API call):**
- Required item-specifics for the chosen category are present (eBay rejects offers missing required specifics).
- Photos meet eBay's dimension/format/file-size requirements.
- Title is within 80 characters.
- Price is a positive number and matches the user-approved price from PRICE/CURATE.
- **Firewall self-check:** the prompt re-asserts the no-publish rule and confirms the operation type is `draft-create` or `draft-update`, never `publish`.

**Failures surface back to the user:**
- Missing required item-specific → ask the user to fill it in the local draft, do not push a half-complete draft.
- Photo dimension / format issue → flag for photo prep (likely a future pre-step), do not silently downscale.
- Category-mismatch with item specifics → ask the user to pick the right category, do not guess.
- eBay API error → surface the raw error message and abort. No retries that could leave eBay in an inconsistent state.

**Open questions (resolve when implementing):**
- **Photo upload pipeline.** EPS upload is its own multi-step dance (request upload slot → POST image → get EPS URL). May warrant a thin helper module.
- **Photo prep prerequisite.** eBay has minimum dimensions and prefers specific aspect ratios. Whether photo prep is a separate function or baked into LIST / EDIT is TBD.
- **Conflict policy** if the user edits the eBay draft in Seller Hub AND the local file diverges. Tentative default: **local file is authoritative**, eBay-side edits get overwritten on next sync. A `--pull` mode (eBay → local) is a possible later addition for the case where the user prefers to do final tweaks in Seller Hub.
- **OAuth token refresh.** eBay's user-token has a 2-hour lifetime and needs a refresh-token dance. Handled inside the eBay client module.
- **Sandbox first.** Initial implementation targets eBay's sandbox environment. Production switchover is a config change once the dev key + Marketplace Insights approval are both in hand.

**Prompt:** `v2/prompts/list_edit.md` (not yet written; pending eBay developer key).

**Interim Chrome stand-in (active until API is wired):**

While `lib/list_edit.py` is stubbed pending the eBay developer key, a Chrome-based stand-in lives at [v2/prompts/list_edit_chrome.md](v2/prompts/list_edit_chrome.md). It uses the Claude-in-Chrome MCP tools to drive eBay's seller UI directly. The stand-in:

- **Picks its starting point from price.txt.** Primary path (Path A): the most recent Tier A exact-match sold comp from PRICE's output — the stand-in navigates there and clicks **"Sell similar"**. Rationale: the comp is the same item that recently sold, so its eBay category and item-specifics field set are correct by definition. Fallback path (Path B, when no Tier A exact-match exists): `prelist/suggest` + **"Continue without match"** to land on a bare empty form. Neither path uses eBay's "Find similar" buy-side search pre-fill (that picks an arbitrary listing — the failure mode we are avoiding).
- **Title is preserved-and-flagged, not overwritten, in Path A.** The cloned source comp's title sold the source item — it demonstrably works. INVESTIGATE/DRAFT's generated title might be better, worse, or equally good but we do not presume. If the cloned title and draft title differ, the discrepancy is surfaced in the status report for the user to decide in Seller Hub. Every other text field IS overwritten from draft.md.
- **Item specifics in Path A:** overwrite where draft has a value; preserve cloned value where draft is empty (the source comp's seller filled it because the category called for it).
- **Photos in Path A:** delete cloned source photos first (stranger's images — not legally ours to reuse), then upload from draft.md `photos:` in order.
- Uses the **React-native-value-setter** pattern (from V1's `listing_template.md` lessons) to programmatically replace field values rather than clicking-and-typing — much faster against eBay's slow React UI.
- For the **description**, sets BOTH the outer `aria-label="Description"` textarea AND the inner iframe's contenteditable div (the V1 lesson that fixed the missing-description failure mode).
- Honors the **same no-publish firewall** as the API path: terminal action is "Save for later" / "Save as draft," never "List it" / "Publish."
- When `lib/list_edit.py` is implemented with the eBay Sell API, this prompt is deprecated. Both paths produce the same result (an eBay DRAFT listing visible in Seller Hub, awaiting the user's manual publish).

**Stub status of the target API path (current):**

Until the eBay developer key arrives and OAuth user-consent flow is captured, the API-based implementation is **stubbed** in code at `v2/lib/list_edit.py`. The stub behavior:

- Public entry points (`create_or_update_listing`, `upload_photos_to_eps`) exist with their real signatures so callers and tests can be wired up.
- Every entry point fails fast with `NotImplementedError("LIST/EDIT is stubbed — awaiting eBay developer key. See PLAN.md Function 6.")` — no partial / silent / pretend-success paths.
- No publish function exists in the codebase, period. The firewall is enforced by absence: there's nothing to call.
- The stub module has a `--check` CLI that confirms the stub is wired up correctly and reports what credentials are still needed (app_id, cert_id, redirect_uri, user_refresh_token).

When the dev key arrives, the implementation fills in:
1. `createOrReplaceInventoryItem` call (description + item-specifics + photo URLs)
2. EPS photo upload helper (the V1 image-upload fix)
3. `createOffer` / `updateOffer` calls (price + listing format + policies, kept as draft)
4. Frontmatter writeback (offer_id, inventory_sku, last_synced)

The firewall (no publish call ever) survives the switchover by design — the only way to add it is for the user to manually write the publish wrapper.

**Status:** **stubbed in code, spec settled.** Awaits eBay developer key for full implementation.

---

### Function 7 — BUNDLE — **deferred, not in MVP**

Bundle / debundle is a real and useful distinction, but it doesn't belong as Function 5. The decision ("list these as a single lot vs. list each individually") naturally lives **between Function 1 (IDENTIFY) and Function 2 (PRICE)** — if a group of items will be sold as one lot, you comp the lot, not each item. Putting it downstream of PRICE means re-doing comp work.

**MVP assumption:** every item from IDENTIFY emerges with an auto-classified `unit_type` (usually `single`); no user-driven re-bundling. 4 functions only.

**Future enhancement (post-MVP):** a step that slots in at "1.5" — after IDENTIFY enumerates items, before PRICE runs, the user can reassign separately-enumerated `single` records into one `unit_type: lot` record (or split a `lot` back into multiple `single` records). The unit_type vocabulary (see "Unit type and quantity" cross-cutting principle) is already in place; BUNDLE just gives the user explicit control over which items merge.

V1 already proved out bundling manually (the 5-lot Polo RL scheme grouped 11 pieces into 5 lots), so the design pattern is known — we just defer the automation. Under the new vocabulary, V1's "5-lot scheme" = 5 records each with `unit_type: lot` and `quantity: 2-3`.

---

**MVP function count is 5, in the order: IDENTIFY → PRICE → CURATE → INVESTIGATE → DRAFT.** The conceptual unit flowing from Function 2 onward is a "listing unit" — for MVP, always one item. Pre-acquisition functions (IDENTIFY, PRICE, CURATE) work from buy-side photos; post-acquisition functions (INVESTIGATE, DRAFT) work from listing-side photos (typically more thorough re-shoots).

**Function 6 (LIST / EDIT)** is MVP+1 — it ships once the eBay developer key is in hand and pushes the local DRAFT into eBay as an unpublished draft listing. It is governed by the No-publish firewall and never goes live on its own. **Function 7 (BUNDLE)** remains deferred.

---

## Open architectural forks (deferred — do not answer yet)

- Where does Claude (the LLM) live in V2? (pure tooling vs API baked in vs hybrid)
- How should V2 push drafts to eBay? (Chrome hardened vs eBay Sell API vs Chrome-only)
- MVP scope — smallest useful cut?
- Language/runtime — Python continuity vs open?
- Data layer — SQLite vs JSON tree vs other?

---

## Follow-up actions (parallel to the build)

- **Apply for eBay Marketplace Insights API access** via eBay's Application Growth Check process.
  - Landing page (process overview): https://developer.ebay.com/grow/application-growth-check
  - Ticket form (where you submit): https://developer.ebay.com/my/support/tickets?tab=app-check
  - Pre-reqs: register a free eBay Developer Program account, create a Production keyset.
  - OAuth scope being requested: `https://api.ebay.com/oauth/api_scope/buy.marketplace.insights`
  - **Owner:** user submits the application.
  - **Claude's task (deferred until V2 scope is finalized):** draft a compelling use-case description that frames this as legitimate seller tooling. Wait until function list is complete so the description reflects the actual app.
  - Expected timeline: days to weeks; rejections common. Apify backend is what keeps V2 shipping in the meantime.

---

## Out of scope (until further notice)

- Multi-user / multi-tenant
- Web UI
- Mobile app
- Anything beyond the local CLI
