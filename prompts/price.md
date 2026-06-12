# PRICE — v3, Function 2

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/price.txt` (overwrite).

Find what the same or nearly-identical item actually sold for, establish
a defensible price, and surface category-specific selling risks. Reads
IDENTIFY records.

**Pricing model (v2 — distribution-based):** the goal is no longer "pick
the strongest comp." It is **characterize the cleaned sold distribution,
then place tiers on it.** Stage B pulls two complementary views of the sold
market (`best_match` = the representative body; `price_high` = the ceiling),
[`lib/price_stats.py`](../lib/price_stats.py) does the deterministic
filtering + statistics, and the tiers trace to a sample size + percentiles
rather than a hand-picked comp. Full rationale:
[docs/price-strategy-v2.md](../docs/price-strategy-v2.md). The exact-match
hunt below still runs — an exact comp, when one exists, short-circuits the
distribution and anchors Recommended directly.

## Autonomy (Goal: dig for the exact match, don't ask permission to look)

No step in PRICE gates or stops. v2 made you propose queries and wait,
and Apify used to require cost approval — v3 does neither. You construct
queries, run the stages, and iterate them yourself. PRICE runs
end-to-end without ever stopping for the user.

## Selling-unit awareness

Query the selling unit, not pieces. `pair` → include "pair";
`set` → "set"/"set of N"; `lot` → "lot of N"/"mixed lot" (lots sell at a
discount to sum-of-parts — single-item comps ×N are Tier C reference
only); `duplicate` → price one piece (CURATE scales). All quoted prices
are per-listing-unit except `duplicate` (per-piece).

## Silver — rarity double-check, exact comp, push HIGH (category override)

Silver gets special handling. In practice our silver has been UNDER-priced and
sells immediately — money left on the table. Whenever IDENTIFY tags an item as
silver in ANY form — sterling / .925, coin silver, .800/.835/.900 continental,
hallmarked silver, silver-on-copper, or silverplate — apply ALL of the following
and OVERRIDE the plain distribution defaults:

1. **Rarity double-check (mandatory).** Before settling a price, explicitly work
   the maker / hallmark / pattern / assay + (for solid silver) the weight. Ask:
   is THIS piece scarce — a sought maker (Tiffany, Georg Jensen, Gorham, Jensen,
   Kirk, etc.), a rare or discontinued pattern, an early assay date, an unusual
   form, a matched pair/set, or heavy gauge? For solid silver compute the **melt
   floor** (troy-oz × spot × purity) — price NEVER drops below melt. Record a
   one-line `Rarity:` verdict in price.txt.
2. **Hunt the EXACT comp harder.** Do NOT settle for an era-peer on silver. Run
   the full query ladder, and if Stage B (Apify) is thin, ESCALATE to Stage C
   (Chrome eBay-sold) rather than falling back — an exact maker+pattern+form comp
   anchors the ceiling. Silver is identifiable; an exact match usually exists if
   you dig.
3. **Push HIGH by default.** For silver the provisional working / list price is
   the **Push-high (vetted ceiling)** tier, NOT the median — with Best Offer ON
   and auto-decline at the Recommended/median (the DRAFT Best Offer gate does
   this automatically once list > Recommended). List at the top of the supported
   range and let Best Offer capture the market; "sold immediately" means we
   listed too low. Still bounded by VETTED comps — push to the honest ceiling,
   never invent value above it (plate / silver-on-copper ceilings stay modest;
   solid silver never below melt).

State `SILVER: push-high strategy applied` in price.txt so the choice is visible
at the REVIEW gate.

## The exact-match hunt (run before any era-peer fallback)

For each item, hunt the exact match, escalating only as each stage dries
up. Do NOT settle for an era-peer until the hunt is exhausted. Every
stage is autonomous — PRICE never stops to ask.

1. **Canonical query** — built from IDENTIFY's short Brand+Type+Era
   values (not the Distinguishing-marks prose). Bare words; drop
   punctuation and filler ("with/and/the"). 5–9 high-signal keywords —
   real seller-title density, so exact comps can exist. Include era ONLY
   if a printed date is visible in a photo.

2. **Stage A — WebSearch** (free, ~5s, broad). Casts wide across the open
   web + marketplaces. Tag `[A — WebSearch]`. **Log each usable hit to
   `<shoot-dir>/comps.csv` as stage A** (see "Saved comp artifacts").

3. **Stage B — Apify eBay sold (DUAL QUERY — the v2 core).** The default
   direct-eBay comp source; no gate. Backend Actor
   `automation-lab/ebay-sold-scraper` — it **pins a US residential proxy** so
   eBay returns USD natively, and returns structured comps with per-item
   URLs, sold dates, condition, listing type / bid count, and seller stats.
   Tag `[B — Apify]`. ~$0.20/item (two runs). **Run the SAME query twice,
   once per sort** — this is what makes distribution pricing possible:

   - **`best_match` run** — the representative body of the distribution
     (`sort:"best_match"`, `maxListingsPerSearch:30`, `maxSearchPages:2`).
   - **`price_high` run** — the ceiling/outlier set, sorted descending
     (`sort:"price_high"`, `maxListingsPerSearch:20`, `maxSearchPages:2`).
   - **Path 1 — Apify MCP tool (preferred; works even with no sandbox
     egress).** If an Apify MCP connector is available (e.g. in a Cowork
     tab), call `automation-lab/ebay-sold-scraper` through it once per sort
     with input `{searchQueries:["<query>"], maxListingsPerSearch:<30|20>,
     maxSearchPages:2, sort:"<best_match|price_high>", listingType:"all"}`.
     Returned fields: `soldPrice`, `soldDate`, `title`, `url`, `condition`,
     `listingType`, `bidsCount`, `sellerName`, `sellerFeedbackCount`/
     `Percent`, `shippingCost`. Record **each run id**. MCP calls are
     brokered outside the code sandbox, so this works where direct
     `api.apify.com` egress is blocked. Write each run's items to
     `<shoot-dir>/apify_<sort>_<runId>.json` in the
     [`save_run_json`](../lib/apify_ebay.py) shape (a `comps` list of objects
     with the snake_case fields `sold_price`, `total_price`, `title`, `url`,
     `condition`, `sold_date`, `listing_type`, `bids_count`,
     `seller_feedback_score` — `price_stats.py` reads exactly this).
   - **Path 2 — stdlib CLI (when you have a shell + egress to
     api.apify.com).** Run it once per sort, saving each JSON beside
     `price.txt`, and pass `--sku`/`--title` so each run gets a Console
     status message:
     `python lib/apify_ebay.py "<query>" --sort best_match --max 30 --pages 2 --save-dir <shoot-dir> --sku <sku> --title "<listing title>"`
     and
     `python lib/apify_ebay.py "<query>" --sort price_high --max 20 --pages 2 --save-dir <shoot-dir> --sku <sku> --title "<listing title>"`.
     Each prints `Apify run: <id>` + `Saved results: <path>` — capture both
     for each run. The CLI auto-runs the charm-price currency check.
     - **`--sku`/`--title`** label the run in the Apify Console runs list
       (status-message column) as `[best] <sku> <title[:10]>` /
       `[sold_highest] <sku> <title[:10]>`, so past runs are diagnosable
       there without opening each one (posted on completion; best-effort,
       never blocks). The SKU is the deterministic 8-hex hash from
       `list_edit.py`; if the item isn't recorded yet (PRICE runs before
       DRAFT), pass the working title alone or the shoot-folder name as
       `--sku`. Both are OPTIONAL — with neither, the run is still labeled by
       its query (`[best] <query>`), so no run is anonymous. (No status is
       posted on the MCP path — the connector only sends the actor input.)
   - **Currency sanity (both paths):** genuine USD eBay prices cluster on
     .99/.95/.00/.50. The CLI validates automatically; **on the MCP path
     eyeball it** — if prices don't cluster on charm endings, suspect a
     currency leak and prefer Stage C. Never trust a `currency`/`USD` label;
     the price pattern is what matters. (The US-proxy actor makes leaks
     unlikely — the old caffein.dev actor leaked BRL/CZK at ~5× mislabeled
     USD, which is why we switched.)
   - **Then run the distribution engine** on the two saved JSONs (use the
     actual `Saved results:` path from each run — the CLI names them
     `apify_run_<ts>_<runId>.json`; the MCP path uses the
     `apify_<sort>_<runId>.json` names above):

         python lib/price_stats.py \
           --best-match <best_match run JSON> \
           --price-high <price_high run JSON> \
           --unit <unit_type> --condition <new|used> \
           --require-tokens <brand/type tokens from IDENTIFY>

     It applies the normalize-before-stats filters (row-0 flag, unit match,
     same-item core tokens, condition cohort, single-bid/low-feedback
     exclusions — each drop logged), computes `n / median / IQR / dispersion`
     off the cleaned `best_match` set, vets the `price_high` ceiling, and
     emits the three tiers + a confidence label. Fold its text block into
     `price.txt` and adopt its tiers (see "Distribution-based tiers" below).
     **No shell?** Do the same filtering + percentiles by hand from the saved
     JSONs, using the rules in [docs/price-strategy-v2.md](../docs/price-strategy-v2.md)
     and the constants in `price_stats.py`; show your work.
   - **Proof-of-run is mandatory.** Record **both Apify run ids** AND both
     saved JSON paths in the Research log. **Never report Stage B as "ran"
     without run ids.**
   - **If NEITHER path is available** (no Apify MCP tool AND no
     shell/egress — e.g. a sandbox whose proxy 403s `api.apify.com`):
     record `B — UNAVAILABLE: <reason>` in the Research log and fall through
     to Stage C. Do NOT silently skip B.

4. **Thin results → broaden the query (query ladder, NOT a new draft).** The
   comp *search query* is internal to PRICE; broadening it never changes the
   `draft.md`, SEO title, SKU, or ledger record. Build queries from
   IDENTIFY's short Brand / Type / Era / Category fields as a specificity
   ladder:
   - **L1 (specific):** Brand + Type + Era [+ one distinguishing word].
   - **L2 (broader):** Type + material; drop era + modifiers.
   - **L3 (broadest):** category noun (+ material).

   Run the dual query (`best_match` + `price_high`) at L1. If the combined
   unique comps that survive `price_stats` filters are **below 3**, step down
   (L2, then L3) and re-run, until enough comps or the ladder is exhausted.
   Log each formulation in the Hunt line. Note: `price_high` often returns
   data when `best_match` is empty (observed: mask-red `best_match`=0,
   `price_high`=25) — an empty `best_match` doesn't block, since
   `price_stats` uses `price_high` as the representative set when `best_match`
   is empty (flagged in its output).

5. **Stage C — Chrome → eBay sold (OPTIONAL — low-confidence only).** The
   browser browse path is no longer routine. Invoke it ONLY when
   confidence is still LOW after A+B — i.e. any of:
   - no exact match, or fewer than ~3 usable USD sold comps;
   - dispersion too wide to anchor a tier (roughly >2× spread among the
     candidate anchors);
   - Apify was unavailable/suspect and you need direct-eBay data;
   - high-value item where a wrong anchor is costly and a ~60s second read
     is worth it.

   When triggered: SOLD URL with `LH_Sold=1&LH_Complete=1&_sop=3`. Extract
   rows prefixed "Sold <date>"; skip Sponsored / "Shop on eBay" house ads.
   Tag `[C — Chrome]`. `get_page_text` gives titles/prices/dates but not
   per-item URLs; when you can't capture each href (via `read_page`/
   `find`), cite the SOLD-search URL as the verifiable source and say so.
   Subject to browser read-tier limits (see [list_edit_chrome.md](list_edit_chrome.md)).
   **Log each comp to `<shoot-dir>/comps.csv` as stage C** (see "Saved comp
   artifacts").

6. **Active fallback** (only when sold returns zero direct matches): drop
   the sold filters, capture active listings, tag `[active — ASKING
   PRICE]`, treat as Tier C ceiling-context only.

7. **Cross-reference** A vs B (vs C if it ran): agreement = high-confidence
   anchor; divergence = trust the direct-eBay sold source (B/C) over A's
   broad web results.

Only after the hunt dries up do you fall back to the closest era-peer —
and then state how hard you looked ("3 formulations, A+B+C, no exact
match"). Per _shared, an exact match found this way beats any era-peer;
commit to it.

## Apify notes (the Stage B backend)

Apify is the default Stage B source and runs without a gate. v2 issues
**two runs per item** — `best_match` (representative) and `price_high`
(ceiling) — and feeds both to [`lib/price_stats.py`](../lib/price_stats.py).
The backend Actor is `automation-lab/ebay-sold-scraper` (configurable via
`apify.ebay_actor`), chosen because it pins a **US residential proxy** —
so eBay serves USD natively and the foreign-currency leak that sank the
previous actor doesn't occur. The CLI/wrapper validates every run with the
charm-price currency check (defense in depth) and raises `CurrencyLeakError`
if a run ever looks FX-converted.

**Two execution paths, by environment (see Stage B above):**
- **Apify MCP connector** — preferred in restricted environments (e.g. a
  Cowork tab) whose sandbox proxy blocks `api.apify.com`. MCP calls are
  brokered outside the sandbox, so they reach Apify when raw HTTPS can't.
  No pip, no wrapper file, token lives in the connector.
- **stdlib CLI** (`python lib/apify_ebay.py`) — for shell environments with
  egress. No third-party package (standard library only); needs Python +
  network to `api.apify.com` + the Apify token.

If neither is available (no MCP tool, no shell/egress), Stage B is
`UNAVAILABLE` and PRICE routes to Chrome (Stage C). Same if the Apify token
is unset.

## Saved comp artifacts (every stage leaves a reviewable record)

The user reviews the raw research, so each stage persists its comps:

- **Stage B (Apify)** → **two** JSONs, one per sort (auto-saved; `--save-dir
  <shoot-dir>` puts them beside `price.txt`; MCP path: write items to
  `<shoot-dir>/apify_best_match_<runId>.json` and
  `<shoot-dir>/apify_price_high_<runId>.json`). `price_stats.py` reads both.
- **Stages A (WebSearch) and C (Chrome)** → rows in **`<shoot-dir>/comps.csv`**,
  the single spreadsheet the user opens to review every comp across sources.
  Append each usable comp with `lib/comps_csv.py`:

      python lib/comps_csv.py --shoot-dir <dir> --item <N> --stage A \
        --query "<q>" --price <p> --title "<t>" --url "<url>" \
        --sold-date <YYYY-MM-DD> --condition "<c>" --note "<why>"

  (stage `C` for Chrome). **No shell?** Write `<shoot-dir>/comps.csv`
  directly with the Write tool using this exact header:
  `captured_at,item,stage,query,price,title,sold_date,condition,listing_type,url,note`
- **Unify (recommended):** fold Stage B into the same CSV so one file holds
  all three —
  `python lib/comps_csv.py --shoot-dir <dir> --from-apify-json <run.json>`.
- **Fresh per run:** `python lib/comps_csv.py --shoot-dir <dir> --reset`
  once at the start of pricing an item, before appending.

A comp with no price (a WebSearch context hit, a dealer asking price) still
gets a row — leave `price` blank and say why in `note`.

## URLs (mandatory — the user verifies comps by clicking them)

Every comp carries a clickable source URL inline (a comp without a URL is
not a comp). In ADDITION, every PRICE output ends with a consolidated
**Comp URLs** list (see Output) so the user can click straight through and
view the comps themselves — **exact / near-exact matches first**, then
ceiling/context comps, then the eBay sold-search URL for the whole result
set. These live in `price.txt` (persisted), so they're saved for reference.

- Use **direct per-item eBay listing URLs** (`ebay.com/itm/<id>`) for exact
  matches whenever you have them (Stage B/Apify returns one per comp;
  Stage A/WebSearch and direct Chrome hrefs too).
- Only when a per-item href genuinely can't be captured (e.g. Chrome
  `get_page_text`) may you fall back to the sold-search URL — and say so.
- Never list a bare price without its URL in this section.

## Research log (MANDATORY — proof every search type actually ran)

Every PRICE run, for every item, emits a Research log accounting for ALL
THREE stages explicitly. This is non-negotiable evidence: it shows what
research happened, not just the comps that survived. Stages A and B run on
EVERY item; C is conditional. Each stage is `RAN` (with proof) /
`SKIPPED` / `UNAVAILABLE` (with a reason) / `NOT TRIGGERED` (C only).

(Note: "Stage A/B/C" = the search METHOD — WebSearch / Apify / Chrome.
Don't confuse with the comp-quality "Tier A/B/C" further down.)

    Research log — Item <N>
      A · WebSearch : RAN — query "<q>" — <n> hits — <one-line finding> — logged to comps.csv
      B · Apify     : RAN via <MCP|CLI> — query "<q>" — best_match run <id> (<n>) + price_high run <id> (<n>) — USD-validated (charm <x>%) — saved 2 JSONs — price_stats n_kept=<n>, conf=<good|partial|thin>
                      [ALT] UNAVAILABLE — <no Apify MCP tool AND no shell/egress | api.apify.com egress blocked (sandbox proxy) | no token | CurrencyLeakError>
                      (run ids come from each sort's MCP result / CLI `Apify run:` line; no `pip install` needed)
      C · Chrome    : NOT TRIGGERED — confidence OK (price_stats conf good/partial)
                      [ALT] RAN — <low-confidence trigger: conf=thin or dispersion too wide> — <n> rows — logged to comps.csv
                      [ALT] UNAVAILABLE — <no browser/Chrome MCP in this environment>

Hard rules for the log:
- **A and B must show `RAN` on every item.** If either is `SKIPPED`/
  `UNAVAILABLE`, that is a flagged problem — also append a line to
  `NEEDS_REVIEW.md` so the user sees research was incomplete.
- **B's `RAN` line MUST carry both run ids** (best_match + price_high — proof
  it hit the Apify backend). No run id ⇒ it did not run ⇒ write
  `UNAVAILABLE — <reason>`, never `RAN`. (If a thin/empty market means only
  one sort returned data, note which and why.)
- **C must be accounted for** — `NOT TRIGGERED` + why, `RAN` + trigger, or
  `UNAVAILABLE` + why. Never omit the C line.
- The log records what you DID; the tiers below record what you CONCLUDED.

## Output

Lead with the headline, then the Research log, then comps, then tiers.

    Max supported price: $X   [anchored on: <exact match | era-peer + gap>]

Per item:

    === PRICE — Item <N> (<short name>) ===
    Comps refreshed: YYYY-MM-DD   ·   Data quality: good / partial / thin
    Research log:  (the A/B/C block above — REQUIRED)
    Hunt: <one line — ladder levels + formulations tried, sources, exact-match yes/no>
    Distribution: n=<k> median=$<m> IQR=$<p25>–$<p75> ceiling=$<c> dispersion=<d>
                  (best_match run <id> + price_high run <id>)

The **Distribution** line is REQUIRED whenever Stage B ran — it is the
`price_stats.py` output folded in, and it is the proof the tiers came from a
sample, not a vibe. Also fold in `price_stats`'s row-0 flag, ceiling
candidates to vet, and the per-filter drop log (so the user sees what was
excluded and why). If Stage B was UNAVAILABLE / thin, say so here instead.

For each scenario (or just "primary" when no bracket — most items):

    Query: <query>   Source(s): <A / B / C>
    Tier A — direct match (anchors price):
      • $<price> — "<title>"  ·  Sold <date>, <condition>
                  Match: <one line>   URL: <url>
    Tier B — branded/mint ceiling (the vetted price_high comp):
      • $<price> — "<title>"  ·  Ceiling note: <why ceiling not anchor>   URL: <url>
    Tier C — excluded:
      • $<price> — "<title>"  ·  Reason: <single bid / <50 fb seller / wrong unit / wrong condition / >2.5× median / asking price>   URL: <url>

Mark comps >12 months old `[STALE]`. Every Tier C needs a specific reason.
When Stage B ran, the Tier-C exclusions are exactly `price_stats`'s
per-filter drop log (unit / token / condition / single-bid / low-feedback) —
transcribe them rather than re-judging.

### Comp URLs — verify these yourself (MANDATORY, ends every item)

A consolidated, click-through list so the user can open the comps directly.
Exact/near-exact anchors first; include the price + a short title with each
URL so the list is scannable on its own. Required on every item.

    Comp URLs — Item <N> (open to verify):
      Exact / near-exact match:
        • $<price> — "<short title>" — <https://www.ebay.com/itm/...>
        • $<price> — "<short title>" — <https://www.ebay.com/itm/...>
      Ceiling / context:
        • $<price> — "<short title>" — <https://www.ebay.com/itm/...>
      All sold results (eBay search): <sold-search URL>

If NO exact match exists, say so on the "Exact / near-exact" line
("none found") and still list the closest era-peers + the sold-search URL.

### Distribution-based tiers (always)

The three tiers come from `price_stats.py` on the cleaned `best_match`
distribution. Adopt its numbers; don't re-derive by eye.

- **Conservative** — **25th percentile** of the like-condition cleaned set
  (no-objection floor).
- **Recommended (max supported)** — **median** of the like-condition cleaned
  set (the typical sold price, not the ceiling). **In headless flow this is
  the provisional working price** (SOFT gate — logged to NEEDS_REVIEW, not a
  stop).
- **Push-high** — the **vetted `price_high` ceiling**: the highest surviving
  `price_high` comp that you confirm is the same item/condition. If
  `price_stats` flagged it `needs_vetting` (>2.5× median) and it does NOT
  vet out as comparable (it's a bundle/mislisting/different model), drop to
  the **90th-percentile fallback** it prints. State which you used.

**Exact-match short-circuit (keep):** if the hunt found ≥1 *true* exact-match
comp (same item, same condition), anchor **Recommended** on the median of
those exact matches instead of the distribution median, and say so. Keep the
distribution as context. The exact comp beats the distribution — commit to it
(per _shared).

**Thin market (`price_stats` confidence = `thin`, n<3):** do NOT use
percentiles. Fall back to today's closest-comp / era-peer method — anchor on
the nearest comp/era-peer, widen the bracket, flag rarity (see "No-exact-
match case"). The three tiers still apply, anchored on the era-peer.

**Best Offer gate (unchanged):** enable Best Offer if list > Recommended;
set auto-decline at Recommended.

### No-exact-match case

State plainly that no exact comp was found; anchor on the closest
era-peer with the gap noted; flag the rarity signal; suggest saving the
eBay search as a watch; note the SEO upside of year-specific attribution.
The three tiers still apply, anchored on the era-peer.

### Close

    Working price (provisional): $X (Recommended tier). Final price is the
    user's call at publish time; recorded here for review.

(No stop. Headless adopts the provisional price and logs it; PRICE runs
end-to-end without a gate.)

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
