# PRICE — v4, Function 2

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/price.txt` (overwrite).

Characterize the cleaned sold distribution, then place tiers on it. Stage B pulls
two sold-market views (`best_match` = representative body; `price_high` =
ceiling); [`lib/price_stats.py`](../lib/price_stats.py) does the filtering +
statistics. An exact comp, when found, short-circuits the distribution and
anchors Recommended directly. Rationale + history:
[reference/price-notes.md](reference/price-notes.md).

**Autonomy:** no step gates or stops. Construct queries, run the stages, iterate
yourself, end-to-end.

## Selling-unit awareness

Query the selling unit, not pieces. `pair` → include "pair"; `set` → "set"/"set
of N"; `lot` → "lot of N"/"mixed lot" (lots discount to sum-of-parts —
single-item comps ×N are Tier C reference only); `duplicate` → price one piece
(CURATE scales). Quoted prices are per-listing-unit except `duplicate`
(per-piece).

## Directory context — provenance is a filter, not a floor

Per _shared's `context.txt` cascade: `lib.dir_context.load_context(shoot_dir)`
can narrow comp selection (era/storage rule out mismatched comps) but
never anchors a price. `ctx.cost` (kept a string — `FREE` and `spent
$650` both occur) feeds margin math elsewhere; it is never a floor on the
ask here.

## Delivered-price basis (default)

A comp's `sold_price` is item-only; the buyer's real outlay is
`sold_price + shipping_cost` = **delivered**. Our listings default to free
shipping, so our list price IS the delivered price. Never anchor a free-shipping
price on a comp's item-only `sold_price`.

1. Anchor on delivered: Stage B saves `total_price` = sold + shipping.
2. Free-shipping listing → `price_stats.py … --price-field total` is the
   headline run; item-only (`--price-field sold`) is secondary at most.
   Buyer-paid/calculated shipping listing → anchor item-only.
3. Translate to **net-to-us** for each candidate price:
   `net ≈ list − our_postage − fees` (postage from IDENTIFY weight/dims).
   Fee band (measured; re-measure with `python lib/report.py --performance`
   when bands move — never quote from a remembered rate):

   | delivered price | fee rate |
   |---|---|
   | under $25 | 18% |
   | $25–50 | 17.5% |
   | $50–100 | 16.5% |
   | $100–250 | 16.5% |
   | $250+ | 15% |

State the basis (`delivered` / `item-only`) on the Distribution line.

## Silver — category override: rarity-check, exact comp, push HIGH

Trigger: "silver" in ANY sense, regardless of content — sterling/.925, coin,
.800/.835/.900, hallmarked, silver-on-copper, silverplate/EPNS, nickel
silver/German silver/alpaca/silver-tone. No exemptions. Then:

1. **Rarity double-check (mandatory).** Work maker/hallmark/pattern/assay +
   weight. Sought maker, rare pattern, early assay, unusual form, matched
   pair/set, heavy gauge? Solid silver: compute the **melt floor**
   (troy-oz × spot × purity) — price never below melt. One-line `Rarity:`
   verdict in price.txt.
2. **Hunt the EXACT comp harder.** No era-peer settling; run the full ladder,
   widen the bracket rather than fall back. Silver is identifiable — an exact
   maker+pattern+form comp anchors the ceiling.
3. **Push HIGH by default.** Working/list price = **Push-high (vetted ceiling)**
   tier, NOT median, with Best Offer ON and auto-decline at Recommended.
   Bounded by vetted comps — honest ceiling only (plate/silver-on-copper stays
   modest; solid never below melt).

State `SILVER: push-high strategy applied` in price.txt.

## The exact-match hunt (before any era-peer fallback)

Escalate only as each stage dries up; never stop to ask.

1. **Canonical query** — IDENTIFY's short Brand+Type+Era (not the marks prose).
   Bare words, no punctuation/filler, 5–9 high-signal keywords. Era only if a
   printed date is visible in a photo.

2. **Stage A — WebSearch** (free, broad). Tag `[A — WebSearch]`. Log each usable
   hit to `<shoot-dir>/comps.csv` as stage A.

3. **Stage B — eBay sold via the LOGGED-IN BROWSER (dual query — the core).**
   Tag `[B — Browser]`. Free, no gate.

   > **APIFY IS DISABLED** (`apify.enabled: false`; raises
   > `ApifyDisabledError`). Do not re-enable it; use the browser.
   > The anonymous in-app browser gets a CAPTCHA — never solve it. Use
   > **claude-in-chrome** (the user's logged-in Chrome).

   Procedure:
   1. `python lib/ebay_sold_browse.py "<query>" --urls [--ladder]` → the two
      sold-search URLs (`best_match` + `price_high`, delivered-basis ceiling).
   2. Navigate claude-in-chrome to each URL.
   3. `python lib/ebay_sold_browse.py --js` prints the in-page extractor; paste
      into `javascript_tool` → `{ok, header, n, rows:[…]}`.
   4. Save the JSON;
      `python lib/ebay_sold_browse.py "<query>" --ingest-json <file> --sort <sort>`
      → run JSON for `price_stats.py`/`comps_csv.py`.
   5. **ALWAYS give the user both URLs in your reply** — they price with you,
      not after you.

   **Ladder walk = ONE call, not one navigate per rung.** From any loaded
   ebay.com tab, `--js-multi <file.json>` fetches every rung × sort
   same-origin and extracts each; read the returned per-query summaries
   (`n`, `loose_dropped`, delivered min/med/max over the `delivered_n` rows
   with known shipping, sample titles), pick the
   formulation that has a real cohort, then ingest just that one with
   `--ingest-json <file> --pick "<label>"`. Add `--also "<alt phrasing>"` to
   test a formulation the ladder wouldn't generate. Still give the user the
   URLs.

   `--parse <textfile>` is the no-JS fallback (no URLs/thumbnails).
   `Best offer accepted` on a comp ⇒ the ask wasn't the clearing price — treat
   as a **soft** ceiling. Do NOT "simplify" the extractor's anti-scrape guards
   (fake-id placeholder cards, reversed `derosnopS` sponsored tag, welded
   spans — see [reference/price-notes.md](reference/price-notes.md)).

   No Chrome MCP ⇒ record `B — UNAVAILABLE: no logged-in browser`, fall through
   to Stage A only. Never silently skip B; never re-enable Apify.

   **Zero comps:** `challenge:true` ⇒ verification page — don't solve, reload
   and retry. `n:0` on a page that visibly has results ⇒ selector drift — fix
   the extractor, don't guess. A real "0 results" page ⇒ thin market — descend
   the ladder.

4. **Thin → query ladder** (internal to PRICE; never changes draft/SEO/SKU):
   - L1: Brand + Type + Era [+ one distinguishing word]
   - L2: Type + material
   - L3: category noun (+ material)

   Run the dual query at L1; if unique surviving comps < 3, step down and
   re-run — or run every rung at once with `--js-multi`. Log each formulation
   in the Hunt line. `price_high` often has data
   when `best_match` is empty — `price_stats` then uses it as the
   representative set (flagged).

5. **Stage C — Chrome sold-search browse (only if confidence still LOW after
   A+B):** no exact match or <~3 usable USD comps; dispersion >2× among
   candidate anchors; Stage B unavailable/suspect; or high-value item. URL with
   `LH_Sold=1&LH_Complete=1&_sop=3`; rows prefixed "Sold <date>"; skip
   Sponsored/house ads. Tag `[C — Chrome]`. If per-item hrefs can't be
   captured, cite the sold-search URL and say so. Log to comps.csv as stage C.

6. **Active fallback** (zero sold matches only): active listings, tag
   `[active — ASKING PRICE]`, Tier C ceiling-context only.

7. **Cross-reference:** A vs B (vs C). Agreement = high confidence; divergence
   = trust direct-eBay sold (B/C) over A.

Only after the hunt dries up, fall back to the closest era-peer — and state how
hard you looked. An exact match beats any era-peer; commit to it (per _shared).

## Tooling — CLI contracts (do NOT read the .py source)

`python lib/ebay_sold_browse.py`:
- `"<query>" --urls [--ladder] [--condition <new|used>]` → the two URLs.
- `--js` → the in-page extractor JS (returns `challenge:true` on a
  verification page — don't solve it).
- `"<query>" --js-multi [FILENAME] [--also "<alt>"] [--multi-sorts a,b]` → JS
  that fetches every ladder rung × sort from the current ebay.com tab and
  returns per-query summaries; with FILENAME it also saves the full rows.
  Rungs print to stderr. Max 8 fetches.
- `--ingest-json <multi-file> --pick "<label>"` → ingest one rung out of a
  `--js-multi` save (labels look like `L2|price_high`; omit `--pick` and it
  lists them).
- `"<query>" --ingest-json <file> --sort <best_match|price_high> [--page N] --save-dir <shoot-dir>`
  → field-coverage line + `Saved results: <path>`.
- `--parse <pagetext.txt>` fallback.
- Saved JSON: `comps` list with `sold_price`, `total_price`, `shipping_cost`,
  `title`, `url`, `item_id`, `thumbnail`, `sold_date`, `listing_type`,
  `bids_count`, `bo_accepted`, `seller_username`, `seller_feedback_score`/`_pct`;
  top-level `charm_price_share` / `currency_leak_suspected`.

`python lib/price_stats.py`:
- `--best-match <bm.json> [--price-high <ph.json>] --unit <single|pair|set|lot|duplicate> --condition <new|used> [--require-tokens <tok>...] [--price-field <sold|total>]`
- Prints the Distribution line (n/median/IQR/dispersion), vetted ceiling, three
  tiers, confidence, per-filter drop log. `--price-field total` for free-ship.
  Fold the block into `price.txt`; n<3 ⇒ `thin` ⇒ era-peer fallback.

## Saved comp artifacts (every stage leaves a reviewable record)

- **Stage B** → two JSONs (one per sort) beside `price.txt` via `--save-dir`.
- **Stages A + C** → rows in `<shoot-dir>/comps.csv` via:

      python lib/comps_csv.py --shoot-dir <dir> --item <N> --stage A \
        --query "<q>" --price <p> --title "<t>" --url "<url>" \
        --sold-date <YYYY-MM-DD> --condition "<c>" --note "<why>"

  No shell? Write comps.csv directly, exact header:
  `captured_at,item,stage,query,price,title,sold_date,condition,listing_type,url,note`
- Unify: `python lib/comps_csv.py --shoot-dir <dir> --from-apify-json <run.json>`.
- Fresh per run: `--reset` once before appending.
- A priceless comp (context hit, asking price) still gets a row — blank price,
  reason in `note`.

## URLs (mandatory — the user verifies comps by clicking)

A comp without a URL is not a comp. Direct `ebay.com/itm/<id>` URLs; sold-search
URL only when a per-item href genuinely can't be captured (say so). Every output
ends with the consolidated Comp URLs block; never a bare price without its URL.

## Research log (MANDATORY — proof every stage actually ran)

Per item, account for all three stages: `RAN` (with proof) / `SKIPPED` /
`UNAVAILABLE` (reason) / `NOT TRIGGERED` (C only). (Stage A/B/C = search
method; distinct from comp-quality Tier A/B/C.)

    Research log — Item <N>
      A · WebSearch : RAN — query "<q>" — <n> hits — <one-line finding> — logged to comps.csv
      B · Browser   : RAN — query "<q>" — best_match n=<n> + price_high n=<n> — saved 2 JSONs — price_stats n_kept=<n>, conf=<good|partial|thin>
                      [ALT] UNAVAILABLE — <no claude-in-chrome in this environment | challenge page persisted>
                      [ALT] THIN — dual query ran, page says "0 results" — market genuinely empty (ladder descended to L<k>)
                      [ALT] BLOCKED — extractor n:0 on a page with visible results — selector drift; Stage B contributes NO tier until fixed
      C · Chrome    : NOT TRIGGERED — confidence OK (price_stats conf good/partial)
                      [ALT] RAN — <trigger> — <n> rows — logged to comps.csv
                      [ALT] UNAVAILABLE — <reason>

Hard rules:
- A and B must show `RAN` on every item; otherwise it's a flagged problem —
  also append to `NEEDS_REVIEW.md`.
- B's `RAN` line must carry both per-sort counts + the saved-JSON proof. No
  saved JSONs ⇒ it did not run ⇒ `UNAVAILABLE — <reason>`, never `RAN`.
- C is always accounted for — never omit the line.
- The log records what you DID; the tiers record what you CONCLUDED.

## Output

Lead with the headline, then Research log, comps, tiers.

    Max supported price: $X   [anchored on: <exact match | era-peer + gap>]

Per item:

    === PRICE — Item <N> (<short name>) ===
    Comps refreshed: YYYY-MM-DD   ·   Data quality: good / partial / thin
    Research log:  (the A/B/C block — REQUIRED)
    Hunt: <one line — ladder levels + formulations, sources, exact-match yes/no>
    Distribution: n=<k> median=$<m> IQR=$<p25>–$<p75> ceiling=$<c> dispersion=<d>  basis=<delivered|item-only>

Distribution line is REQUIRED whenever Stage B ran — `price_stats.py` output
folded in, plus its row-0 flag, ceiling candidates to vet, and the per-filter
drop log. Stage B unavailable/thin ⇒ say so here instead.

Per scenario (or just "primary"):

    Query: <query>   Source(s): <A / B / C>
    Tier A — direct match (anchors price):
      • $<price> — "<title>"  ·  Sold <date>, <condition>
                  Match: <one line>   URL: <url>
    Tier B — branded/mint ceiling (the vetted price_high comp):
      • $<price> — "<title>"  ·  Ceiling note: <why not anchor>   URL: <url>
    Tier C — excluded:
      • $<price> — "<title>"  ·  Reason: <single bid / <50 fb seller / wrong unit / wrong condition / >2.5× median / asking price>   URL: <url>

Comps >12 months old ⇒ `[STALE]`. Every Tier C needs a specific reason; when
Stage B ran, Tier C = `price_stats`'s drop log, transcribed not re-judged.

### Comp URLs (MANDATORY, ends every item)

    Comp URLs — Item <N> (open to verify):
      Exact / near-exact match:
        • $<price> — "<short title>" — <https://www.ebay.com/itm/...>
      Ceiling / context:
        • $<price> — "<short title>" — <https://www.ebay.com/itm/...>
      All sold results (eBay search): <sold-search URL>

No exact match ⇒ say "none found" on that line, still list closest era-peers +
the sold-search URL.

**Chat presentation — thumbnail board (HARD RULE — _shared.md "Showing comps
to the user").** Any comps surfaced in chat MUST be a visual board where every
comp has all three: thumbnail **embedded as a base64 `data:` URI** (remote
`img src` is CSP-blocked), a clickable link to the genuine
`https://www.ebay.com/itm/<id>`, and the delivered price + a
match/ceiling/excluded tag. Self-contained HTML via
`SendUserFile(display:"render")` or `show_widget`; build it from the saved comp
JSON (`thumbnail` + `url` per comp). Markdown tables / text lists / remote
images are NOT acceptable. Dial the query for a tight exact-match cohort BEFORE
showing the board. `price.txt` + `comps.csv` stay as specified.

### Distribution-based tiers (always)

From `price_stats.py` on the cleaned set — adopt its numbers, don't re-derive.
Free-shipping ⇒ these are delivered prices; always pair with net-to-us.

- **Conservative** — 25th percentile of the like-condition cleaned set.
- **Recommended (max supported)** — median. In headless flow this is the
  provisional working price (SOFT gate — logged to NEEDS_REVIEW, not a stop).
- **Push-high** — the vetted `price_high` ceiling. If flagged `needs_vetting`
  (>2.5× median) and it doesn't vet as comparable, drop to the
  90th-percentile fallback it prints. State which you used.

**Exact-match short-circuit:** ≥1 true exact comp (same item + condition) ⇒
anchor Recommended on the exact-match median instead; say so; keep the
distribution as context.

**Thin market (conf=thin, n<3):** no percentiles — closest-comp/era-peer
method, widen the bracket, flag rarity. The three tiers still apply, anchored
on the era-peer.

**Best Offer gate:** enable if list > Recommended; auto-decline at Recommended.

### No-exact-match case

Say so plainly; anchor on the closest era-peer with the gap noted; flag the
rarity signal; suggest a saved-search watch; note the SEO upside of
year-specific attribution.

### Close

    Working price (provisional): $X (Recommended tier). Final price is the
    user's call at publish time; recorded here for review.

## Research notes (per item — 2–4 lines each; `N/A — <reason>` if inapplicable)

- **Authenticity & fakes** — repro problem? Name the tell + an auth step.
- **Restrictions & legal** — eBay-prohibited, CITES, NAGPRA, militaria bans,
  hazmat, recalls, intl-ship eligibility, state rules. Sellable internationally?
- **Scams & buyer issues** — category-typical scams + the protection.
- **Authenticity Guarantee** — covered at this value tier?
- **Disclosure** — what to state up front to head off returns.

## Skip / honesty

- Skip `none (not for sale)`. DO run on `needs_followup_photo: yes` (tells the
  user if a re-shoot pays).
- Don't invent comps — say "thin/absent" outright.
- Fresh comps every run; never reuse prior classifications.

## Closing

Per _shared: working-price headline + path. Don't restate the comp list.
