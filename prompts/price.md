# PRICE — v3, Function 2

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/price.txt` (overwrite).

Find what the same or nearly-identical item actually sold for, establish
a defensible price, and surface category-specific selling risks. Reads
IDENTIFY records.

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

3. **Stage B — Apify eBay sold** (the default direct-eBay comp source; no
   gate). Backend Actor `automation-lab/ebay-sold-scraper` — it **pins a US
   residential proxy** so eBay returns USD natively, and returns structured
   comps with per-item URLs, sold dates, condition, listing type / bid
   count, and seller stats. Tag `[B — Apify]`. ~$0.10/run. **Use whichever
   execution path your environment supports — try them in this order:**

   - **Path 1 — Apify MCP tool (preferred; works even with no sandbox
     egress).** If an Apify MCP connector is available (e.g. in a Cowork
     tab), call the actor `automation-lab/ebay-sold-scraper` through that
     tool with input `{searchQueries:["<query>"], maxListingsPerSearch:30,
     maxSearchPages:3, sort:"price_high", listingType:"all"}`. It returns
     the dataset items (fields: `soldPrice`, `soldDate`, `title`, `url`,
     `condition`, `listingType`, `bidsCount`, `sellerName`,
     `sellerFeedbackCount`/`Percent`, `shippingCost`). Record the **run id**
     the tool reports. MCP calls are brokered outside the code sandbox, so
     this works where direct `api.apify.com` egress is blocked.
   - **Path 2 — stdlib CLI (when you have a shell + egress to
     api.apify.com).** Run `python lib/apify_ebay.py "<query>" --save-dir
     <shoot-dir>` (no pip — standard library only). It saves the raw results
     JSON beside `price.txt` and prints `Apify run: <id>` + `Saved results:
     <path>`. Capture both.
   - **Map + validate (either path):** fields → comps (same shape); drop
     single-bid / >2× median outliers to Tier C. The CLI auto-runs the
     charm-price currency check; **on the MCP path you must eyeball it** —
     genuine USD eBay prices cluster on .99/.95/.00/.50; if they don't,
     suspect a currency leak and prefer Stage C. Never trust a
     `currency`/`USD` label; the price pattern is what matters. (The
     US-proxy actor makes leaks unlikely — the old caffein.dev actor leaked
     BRL/CZK at ~5× mislabeled USD, which is why we switched.)
   - **Save the results (both paths).** The CLI auto-saves the run JSON
     (`--save-dir <shoot-dir>` to place it beside `price.txt`). On the MCP
     path, write the returned items to `<shoot-dir>/apify_<runId>.json`
     yourself. This is the audit trail + cache for the comps.
   - **Proof-of-run is mandatory.** Record the **Apify run id** (from the
     MCP result or the CLI's `Apify run:` line) AND the saved JSON path in
     the Research log — both are verifiable. **Never report Stage B as "ran"
     without a run id.**
   - Still drop outliers; if Stage B yields <3 usable comps or dispersion
     disagrees badly with Stage A, treat as LOW confidence → Stage C.
   - **If NEITHER path is available** (no Apify MCP tool AND no
     shell/egress — e.g. a sandbox whose proxy 403s `api.apify.com`):
     record `B — UNAVAILABLE: <reason>` in the Research log and fall through
     to Stage C. Do NOT silently skip B.

4. **If no exact match yet, broaden and re-run A+B:** drop the
   least-load-bearing keyword (5→3 words), then try a synonym for Type.
   Iterate 2–3 formulations.

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

Apify is the default Stage B source and runs without a gate. The backend
Actor is `automation-lab/ebay-sold-scraper` (configurable via
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

- **Stage B (Apify)** → JSON (auto-saved; `--save-dir <shoot-dir>` puts it
  beside `price.txt`; MCP path: write items to `<shoot-dir>/apify_<runId>.json`).
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
      B · Apify     : RAN via <MCP|CLI> — query "<q>" — run <runId> — <n> comps — USD-validated (charm <x>%) — saved <path>.json
                      [ALT] UNAVAILABLE — <no Apify MCP tool AND no shell/egress | api.apify.com egress blocked (sandbox proxy) | no token | CurrencyLeakError>
                      (run id comes from the MCP tool result or the CLI's `Apify run:` line; no `pip install` needed)
      C · Chrome    : NOT TRIGGERED — confidence OK (<n> usable comps from A+B)
                      [ALT] RAN — <low-confidence trigger> — <n> rows — logged to comps.csv
                      [ALT] UNAVAILABLE — <no browser/Chrome MCP in this environment>

Hard rules for the log:
- **A and B must show `RAN` on every item.** If either is `SKIPPED`/
  `UNAVAILABLE`, that is a flagged problem — also append a line to
  `NEEDS_REVIEW.md` so the user sees research was incomplete.
- **B's `RAN` line MUST carry a run id** (proof it hit the Apify backend).
  No run id ⇒ it did not run ⇒ write `UNAVAILABLE — <reason>`, never `RAN`.
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
