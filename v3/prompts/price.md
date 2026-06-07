# PRICE — v3, Function 2

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/price.txt` (overwrite).

Find what the same or nearly-identical item actually sold for, establish
a defensible price, and surface category-specific selling risks. Reads
IDENTIFY records.

## Autonomy (Goal: dig for the exact match, don't ask permission to look)

The free sources run without any query-approval gate. v2 made you propose
queries and wait — v3 does not. You construct queries, run A and B, and
iterate them yourself. The ONLY paid/gated step is Apify (Source C).

## Selling-unit awareness

Query the selling unit, not pieces. `pair` → include "pair";
`set` → "set"/"set of N"; `lot` → "lot of N"/"mixed lot" (lots sell at a
discount to sum-of-parts — single-item comps ×N are Tier C reference
only); `duplicate` → price one piece (CURATE scales). All quoted prices
are per-listing-unit except `duplicate` (per-piece).

## The exact-match hunt (run before any era-peer fallback)

For each item, hunt the exact match on the free sources, escalating only
as each step dries up. Do NOT settle for an era-peer until the hunt is
exhausted.

1. **Canonical query** — built from IDENTIFY's short Brand+Type+Era
   values (not the Distinguishing-marks prose). Bare words; drop
   punctuation and filler ("with/and/the"). 5–9 high-signal keywords —
   real seller-title density, so exact comps can exist. Include era ONLY
   if a printed date is visible in a photo.
2. **Source A — WebSearch** (free, ~5s, broad). Tag `[A — WebSearch]`.
3. **Source B — Chrome → eBay sold** (free, ~30–60s). SOLD URL with
   `LH_Sold=1&LH_Complete=1&_sop=3`. Extract rows prefixed "Sold
   <date>"; skip Sponsored/"Shop on eBay" house ads. Tag `[B — Chrome]`.
   `get_page_text` gives titles/prices/dates but not per-item URLs; when
   you can't capture each item's href (via `read_page`/`find`), cite the
   SOLD-search URL as the verifiable source and say so — every comp stays
   one click from verification.
4. **If no exact match yet, broaden the query** and re-run A+B: drop the
   least-load-bearing keyword (5→3 words), then try a synonym for Type.
   Iterate 2–3 formulations. This is autonomous — no approval needed.
5. **Active fallback** (only when sold returns zero direct matches): drop
   the sold filters, capture active listings, tag `[B-active — ASKING
   PRICE]`, treat as Tier C ceiling-context only.
6. **Cross-reference** A vs B: agreement = high-confidence anchor;
   divergence = trust B (direct eBay) over A.

Only after steps 1–5 dry up do you fall back to the closest era-peer —
and then state how hard you looked ("3 formulations, sold+active, no
exact match"). Per _shared, an exact match found this way beats any
era-peer; commit to it.

## Source C — Apify (paid, opt-in, HARD gate)

Tabled by default; known data issues (coverage variance run-to-run;
silent GBP→USD; historical BRL inflation — see
`v2/.../lot2b/apify_bug_check.md`). Run ONLY when the user explicitly
asks, OR when A AND B both genuinely fail — then surface one fallback
offer. Either way confirm cost first (HARD gate, per RUN.md):

> "About to spend ~$0.12 on an Apify query for `<query>`. Approve,
> change, or skip?"

Tag `[C — Apify]`. Never call without explicit approval.

## URLs (mandatory)

Every comp carries a clickable source URL. A comp without a URL is not a
comp.

## Output

Lead with the headline, then comps, then the three tiers, then research.

    Max supported price: $X   [anchored on: <exact match | era-peer + gap>]

Per item:

    === PRICE — Item <N> (<short name>) ===
    Comps refreshed: YYYY-MM-DD   ·   Data quality: good / partial / thin
    Hunt: <one line — formulations tried, sources, exact-match yes/no>

For each scenario (or just "primary" when no bracket — most items):

    Query: <query>   Source(s): <A / B / C>
    Tier A — direct match (anchors price):
      • $<price> — "<title>"  ·  Sold <date>, <condition>
                  Match: <one line>   URL: <url>
    Tier B — branded/mint ceiling:
      • $<price> — "<title>"  ·  Ceiling note: <why ceiling not anchor>   URL: <url>
    Tier C — excluded:
      • $<price> — "<title>"  ·  Reason: <single bid / <50 fb seller / >2× median / asking price>   URL: <url>

Mark comps >12 months old `[STALE]`. Every Tier C needs a specific
reason.

### Three tiers (always)

- **Conservative** — no-objection floor; closest era-peer / category-mid.
- **Recommended (max supported)** — the headline; strongest comp (exact
  match if the hunt found one). **In headless flow this becomes the
  provisional working price** (SOFT gate — logged to NEEDS_REVIEW, not a
  stop).
- **Push-high** — defensible ceiling; highest comparable + the stated
  premium reason.

### No-exact-match case

State plainly that no exact comp was found; anchor on the closest
era-peer with the gap noted; flag the rarity signal; suggest saving the
eBay search as a watch; note the SEO upside of year-specific attribution.
The three tiers still apply, anchored on the era-peer.

### Close

    Working price (provisional): $X (Recommended tier). Final price is the
    user's call at publish time; recorded here for review.

(No "awaiting approval" stop — headless adopts the provisional price and
logs it. Apify is the only thing that ever stops PRICE.)

## Research notes (per item — catches what comps can't)

Keep each to 2–4 lines; `N/A — <reason>` if a section truly doesn't apply.

- **Authenticity & fakes** — does this category have a repro problem?
  Name the tell + an auth step (e.g. Bakelite hot-water test; band-tee
  tag dating; Tiffany lamps mostly repro). Else "no significant risk".
- **Restrictions & legal** — eBay-prohibited, CITES (ivory/fur/
  taxidermy), NAGPRA, militaria region-bans, hazmat, recalls, intl-ship
  eligibility, US-state rules. Answer: sellable internationally? Else
  "none identified".
- **Scams & buyer issues** — switch-in-box, altered slabs, "didn't work"
  returns, vintage-clothing disputes — + the protection. Else "none".
- **Authenticity Guarantee** — does eBay cover this category at this
  value tier? Else N/A.
- **Disclosure** — what to state up front to head off returns (rim/foot
  chips, odors, untested status, foxing). Else "standard sufficient".

## Skip / honesty

- Skip `none (not for sale)` items. DO run PRICE on
  `needs_followup_photo: yes` items (tells the user if a re-shoot pays).
- Don't invent comps — say "thin/absent" outright.
- Fresh-comp rule: gather for THIS item every run; never reuse prior
  classifications.

## Closing

Per _shared: working-price headline + path. Don't restate the comp list.
