# V2 — resume prompt

Paste this whole file as your first message in a fresh Claude conversation
when you want to pick up where we left off. It gives Claude the context
it needs to continue without re-deriving everything.

---

## Prompt to paste

I'm continuing work on **V2 of an eBay reselling CLI tool** — a Python-based
local CLI that replaces a v1 Claude-in-Chrome workflow. Project root:
`C:\Users\Reuseum\Documents\Claude\Projects\ebaybiz`. All V2 work lives
under `v2/`.

**Please start by reading these files in order to get the full picture:**

1. `v2/PLAN.md` — single source of truth. Contains all 5 function specs,
   cross-cutting principles (fresh-investigation rule, output-file persistence
   rule, three-source search strategy), workflow modes (planning vs listing),
   and decisions to date.
2. `v2/prompts/identify.md`, `price.md`, `investigate.md`, `curate.md`,
   `draft.md` — the five prompt files for Functions 1–5. `draft.md`
   reads identify/investigate/price outputs and renders the
   `v2/templates/listing-v1.md` template into `<shoot-dir>/draft.md`.
3. `v2/templates/listing-v1.md` — default listing template (YAML
   frontmatter with eBay field constraints + markdown body).
4. `v2/lib/README.md` — Python lib setup, Apify wrapper status.
5. `v2/samples/` — real test artifacts (single-test-iron, single-test-polo-mailers,
   single-test-polo-safari, single-test-polo-blue-logo, shoot-pottery,
   shoot-dining-decor, shoot-marble-console). Each has `identify.txt` +
   `price.txt` + `review.md` and/or `investigate.txt` showing what the
   pipeline produces.

## Where we are right now (high level)

**Pipeline (5 functions, MVP):**
`IDENTIFY → PRICE → CURATE → INVESTIGATE → DRAFT`

- Functions 1–5: prompt files complete; Functions 1–4 tested manually
  against real photos
- Function 5 (DRAFT): prompt at `v2/prompts/draft.md` renders the
  `v2/templates/listing-v1.md` template using identify.txt +
  investigate.txt + price.txt as inputs; not yet exercised end-to-end
  on a real shoot
- Function 6 (LIST / EDIT): API path stubbed in `v2/lib/list_edit.py`
  pending eBay developer key. Interim Chrome stand-in active at
  `v2/prompts/list_edit_chrome.md`. Starts from price.txt's most
  recent Tier A exact-match comp via "Sell similar" (Path A); falls
  back to prelist + "Continue without match" empty form (Path B) when
  no Tier A exact-match exists. Title is preserved-and-flagged in
  Path A (not auto-overwritten); every other field is filled from
  draft.md. Same no-publish firewall as the API path.

**Workflow modes:**
- `planning` (pre-acquisition): IDENTIFY → PRICE → CURATE — produces a buy list
- `listing` (post-acquisition): INVESTIGATE → DRAFT — produces listing-ready
  package

**Three comp sources (different cost / speed):**
- Source A: WebSearch (free, fast, broad)
- Source B: Claude in Chrome → eBay direct (free, slow, reliable)
- Source C: Apify API (~$0.12/query, fast, **requires user approval before
  each call**)

**Cross-cutting rules:**
- Fresh-investigation rule: never import findings from prior records or
  visually similar items
- Output-file persistence: every function writes to a deterministic file
  in the shoot directory, overwrites on re-run
- Apify confirmation gate: never call Source C without explicit user OK

## Python + config state

- Python 3.12.10 installed via winget at
  `C:\Users\Reuseum\AppData\Local\Programs\Python\Python312\python.exe`
- Dependencies installed: `apify-client`, `pyyaml`
- Config file at `%APPDATA%\ebaybiz\config.yaml` with real Apify token
- Verify the setup is still good:
  ```powershell
  cd C:\Users\Reuseum\Documents\Claude\Projects\ebaybiz\v2\lib
  python config.py --check
  ```
  Expected: `[OK] APIFY_API_TOKEN: ok` and `[OK] ANTHROPIC_API_KEY: ok`

## Known issues

1. **Apify Actor inconsistency.** The `caffein.dev/ebay-sold-listings`
   Actor returns prices inconsistently — same itemId, different runs,
   prices off by exactly 5.017× (BRL/USD rate). Documented evidence:
   itemId `267583308210` came back as $280 (Run XPEs), $1404.82 (Run Jy1aq),
   $280 (Run IDy56). Two of three runs were correct; one inflated 5×.
   Cause: scraping proxy IP rotation between USD and BRL eBay locales.
2. **DRAFT prompt not written yet.** Function 5 exists only in PLAN.md
   as a spec; no `v2/prompts/draft.md` exists.
3. **BUNDLE function deferred.** Documented as future enhancement that
   slots between IDENTIFY and PRICE for lot/bundle listings.
4. **Marketplace Insights API application pending.** User to submit; Claude
   to draft compelling use-case copy once V2 scope is fully settled.

## Possible next steps (your pick)

Pick one or tell me something else:

- **A. Address the Apify Actor issue.** File a bug with `caffein.dev`
  (full repro evidence is in the V2 conversation history), OR switch to
  one of these alternative Actors and validate against the same Polo
  On Safari landmark ($300 sale, https://www.ebay.com/itm/206286396483):
  - `astronomical_reception/ebay-sold-lite` (cheapest, fastest)
  - `marielise.dev/ebay-sold-listings-intelligence` (adds analytics)
  - `midwest_united/ebay-sold-comps` (has lot normalization + outlier fencing)
  Swap is a one-line config change in `~/.ebaybiz/config.yaml`.

- **B. Write the DRAFT prompt** (Function 5). It consumes
  INVESTIGATE's "Listing-safe claims" section + the user-approved
  comp price from CURATE/PRICE, and produces the eBay listing
  package (title, description, item specifics, photo order,
  suggested price). The format should mirror what we built for
  the V1 listing-template.md but consume INVESTIGATE's output
  directly rather than asking the user to fill it in.

- **C. Test more items.** Run the existing 4-function pipeline
  end-to-end on a new image with photos in a directory I point you
  at. The user-clarification loop on INVESTIGATE works well — let's
  exercise it more.

- **D. Start the CLI orchestrator.** Build `ebaybiz plan <dir>`
  and `ebaybiz list <dir>` as actual Python entry points that
  wire the prompts + Apify wrapper + filesystem together. This
  is where the project becomes a real CLI tool rather than a
  collection of prompts.

- **E. Apply for Marketplace Insights API.** Draft the use-case
  description for me; I submit at
  https://developer.ebay.com/grow/application-growth-check

## Quick context for prompt-driven testing

If we test more items today, the established pattern is:
1. User points at a photo directory (single mode = multiple angles of
   one item)
2. Claude reads photos, produces `identify.txt` per the IDENTIFY prompt
3. Claude produces `investigate.txt` with the confident-assessment
   structure (most likely → least likely scenarios)
4. Claude asks user for any clarifications (real-world example: user
   confirmed Lenox Square Atlanta store edition for the Polo On Safari
   catalog)
5. Claude runs PRICE: Source A (free) + Source B (free Chrome) + asks
   before Source C (paid Apify)
6. Claude produces `price.txt` with three-tier price options
   (conservative / recommended / push-high) and explicit
   "Awaiting user approval. Final price decision deferred to publish
   time" gate
7. User reviews, decides working price (or refines), proceeds to
   CURATE / DRAFT as appropriate

The MVP item value bar is **$100 minimum net profit** (configurable in
config.yaml profile). Heavy items (>25 lb) have a weight-tier multiplier
on the floor: 1.5× heavy, 3× oversized, 5× freight.

## Don't do these things

- Don't import V1 findings or prior identifications into new
  investigations — fresh-investigation rule applies.
- Don't call Source C (Apify) without explicit user approval — it
  costs ~$0.12 each call.
- Don't commit a real API token to `config.example.yaml` (the example
  file is the template; the real config lives at
  `%APPDATA%\ebaybiz\config.yaml`).
- Don't autonomously commit to a final published price for a listing
  — PRICE produces "max supported price" and waits for user approval;
  final published price is the user's call at publish time.

---

**My request: please confirm you've read the files listed above, then
ask me which path I want to take today.**
