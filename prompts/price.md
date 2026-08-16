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
distribution and anchors Recommended directly. All tiers are placed on the
**DELIVERED (sold + shipping) basis** whenever the listing is free-shipping
(our default) — see "Delivered-price basis" below; never anchor a free-shipping
price on a comp's item-only `sold_price`.

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

## Delivered-price basis (anchor on sold + shipping — the default)

eBay comps almost always charge BUYER-PAID shipping, so a comp's `sold_price` is
the ITEM price only — the buyer's real outlay is `sold_price + shipping_cost`, the
**delivered price**. Our listings default to FREE shipping, so OUR list price IS the
delivered price. Comparing our free-ship price to a comp's item-only `sold_price`
understates the comp by its shipping (often $10–25 on breakables / heavier items)
and makes a fair price look like a push above the market. So:

1. **Anchor on the DELIVERED price.** Stage B carries `total_price` =
   `sold_price + shipping_cost`. `lib/ebay_sold_browse.py` computes and saves it
   automatically — the in-page extractor reads "+$24.25 delivery" → 24.25 and
   "Free delivery" → 0 straight off the card, so `total_price` is always present.
2. **Run the distribution on the delivered basis when the listing is free-shipping**
   (the default): `price_stats.py … --price-field total`. The delivered tiers are the
   headline and decide the sale price; an item-only run (`--price-field sold`) is at
   most a secondary read. (If the listing will use buyer-paid / calculated shipping
   instead, anchor item-only — then our list price is the item price and the buyer
   covers shipping exactly like the comps.)
3. **Translate to NET-TO-US.** Free shipping means we absorb postage AND pay eBay
   fees on the full delivered amount: `net ≈ list − our_postage − fees`
   (postage from IDENTIFY's weight/dims). **Fee rate — measured, not assumed.**
   This prompt used to say 13% + $0.40; across 92 real sales the account actually
   paid **16.0% blended**, and the rate is regressive, so use the band:

   | delivered price | fee rate to use |
   |---|---|
   | under $25 | 18% |
   | $25–50 | 17.5% |
   | $50–100 | 16.5% |
   | $100–250 | 16.5% |
   | $250+ | 15% |

   Re-measure with `python lib/report.py --performance` (REPORT, Function 7) and
   update this table when the bands move — never quote a net-to-us figure from a
   remembered rate. Surface net-to-us for
   each candidate price so the user sees what each option actually returns — two
   listings at the same delivered price net differently if one ships a 1 lb item and
   the other a 10 lb one.

State the basis used (`delivered` / `item-only`) on the Distribution line so the
choice is visible at REVIEW.

## Silver — rarity double-check, exact comp, push HIGH (category override)

Silver gets special handling. In practice our silver has been UNDER-priced and
sells immediately — money left on the table. The trigger is the word "silver" in
ANY form, **regardless of precious-metal content** — base-metal "silver" counts
too: sterling / .925, coin silver, .800/.835/.900 continental, hallmarked silver,
silver-on-copper, silverplate / EPNS, AND **nickel silver / German silver /
alpaca / silver-tone / "silver" plate over base metal**. Do NOT exempt a piece
just because it isn't precious — push high on all of it. Whenever IDENTIFY tags
an item as silver in any of these senses, apply ALL of the following and OVERRIDE
the plain distribution defaults:

1. **Rarity double-check (mandatory).** Before settling a price, explicitly work
   the maker / hallmark / pattern / assay + (for solid silver) the weight. Ask:
   is THIS piece scarce — a sought maker (Tiffany, Georg Jensen, Gorham, Jensen,
   Kirk, etc.), a rare or discontinued pattern, an early assay date, an unusual
   form, a matched pair/set, or heavy gauge? For solid silver compute the **melt
   floor** (troy-oz × spot × purity) — price NEVER drops below melt. Record a
   one-line `Rarity:` verdict in price.txt.
2. **Hunt the EXACT comp harder.** Do NOT settle for an era-peer on silver. Run
   the full query ladder, and if Stage B is thin, descend the ladder and widen
   the bracket rather than falling back — an exact maker+pattern+form comp
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

3. **Stage B — eBay sold via the LOGGED-IN BROWSER (DUAL QUERY — the v2 core).**
   The default direct-eBay comp source; no gate. Tag `[B — Browser]`. **Free.**

   > **APIFY IS DISABLED** (2026-08-15, `apify.enabled: false`). Every actor
   > we depended on was eventually blocked or deleted, and a blocked actor
   > returns 0 items with a SUCCEEDED status — indistinguishable from a thin
   > market. Calling it now raises `ApifyDisabledError`. Do not re-enable it
   > to "just get comps"; use the browser.

   **The anonymous in-app browser does NOT work for this** — eBay serves it a
   "Security Measure / verify yourself" CAPTCHA. Never try to solve it. Use
   **claude-in-chrome** (the user's real, logged-in Chrome), which is not
   challenged.

   Procedure:
   1. `python lib/ebay_sold_browse.py "<query>" --urls [--ladder]` — prints
      the `best_match` (representative body) and `price_high` (**ceiling,
      delivered basis**) URLs.
   2. Navigate **claude-in-chrome** to each URL.
   3. Run the in-page extractor: `python lib/ebay_sold_browse.py --js` prints
      it; paste that into `javascript_tool`. It returns
      `{ok, header, n, rows:[…]}` — one row per real sold listing, with the
      item URL and thumbnail.
   4. Save that JSON and
      `python lib/ebay_sold_browse.py "<query>" --ingest-json <file> --sort <sort>`
      → writes a run JSON in the SAME shape the Apify path produced.
   5. `price_stats.py` and `comps_csv.py` consume it unchanged.
   6. **ALWAYS give the user both URLs in your reply** so they can browse the
      identical comp set themselves. This is not optional — they price with
      you, not after you.

   `--parse <textfile>` is a fallback for when JS execution isn't available.
   It cannot recover item URLs or thumbnails; prefer the extractor.

   What the browser gives that no actor did: **`Best offer accepted`** — a
   sale at that flag means the ASK was not the clearing price, so treat it as
   a **soft** ceiling, not a hard one. Also delivered shipping, seller
   feedback score + %, bid counts, item URLs and thumbnails. Measured on a
   live 60-comp page: every field populated, 0 missing.

   **Anti-scrape traps the extractor already handles — do not "simplify" them
   away.** eBay salts results with placeholder cards pointing at a FAKE item
   id (`/itm/123456`); the extractor requires a >=9-digit id. eBay also
   renders "Sponsored" REVERSED as `derosnopS`, so it can't be string-matched
   — and it appears on nearly every card, so it is useless as a filter.
   Finally, adjacent spans have no whitespace between them, so raw
   `textContent` welds them ("Sell one like this" + seller name →
   `thiscelticitaliansilver`); the extractor joins leaf nodes with newlines
   instead.

   **If the browser path is unavailable** (no Chrome MCP in this environment):
   record `B — UNAVAILABLE: no logged-in browser` in the Research log and fall
   through to Stage A only. Do NOT silently skip B, and do NOT re-enable Apify.

   **Zero comps.** With the browser there is no silent-block ambiguity — you
   can SEE the page. If the extractor returns `challenge:true`, eBay served a
   verification page: do NOT solve it, reload in Chrome and retry. If it
   returns `n:0` on a page that visibly has results, the `li.s-card` selector
   has drifted again — fix the extractor, don't guess a price. A real 0 on a
   page that says "0 results" is a genuinely thin market: descend the ladder.

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

## Tooling — CLI contracts (use these; do NOT read the .py source)

Everything PRICE needs from `lib/` is below — treat it as the interface and
don't open the implementation files (reading them is wasted tokens). Stage B
runs twice per item (`best_match` + `price_high`) through the user's logged-in
Chrome; every save runs the charm-price currency check + flags a suspected FX
leak. No token, no cost, no actor. No Chrome MCP ⇒ Stage B `UNAVAILABLE`.

`python lib/ebay_sold_browse.py` — eBay sold comps via the browser:
- URLs: `"<query>" --urls [--ladder] [--condition <new|used>]` — prints the
  `best_match` + `price_high` sold-search URLs. **Give these to the user.**
- extractor: `--js` — prints the in-page JS. Paste into claude-in-chrome's
  `javascript_tool` on each loaded page; returns `{ok, header, n, rows:[…]}`.
  If it returns `challenge:true`, eBay wants verification — do NOT solve it.
- ingest: `"<query>" --ingest-json <file> --sort <best_match|price_high> [--page N] --save-dir <shoot-dir>`
  → prints a field-coverage line (urls / delivered / seller / Best-Offer-accepted
  / auctions) and `Saved results: <path>`.
- fallback: `--parse <pagetext.txt>` when JS isn't available (no URLs/thumbnails).
- Saved JSON has a `comps` list with `sold_price`, `total_price` (=sold+shipping),
  `shipping_cost`, `title`, `url`, `item_id`, `thumbnail`, `sold_date`,
  `listing_type`, `bids_count`, `bo_accepted`, `seller_username`,
  `seller_feedback_score`/`_pct`, plus top-level `charm_price_share` /
  `currency_leak_suspected`. Same shape the Apify path wrote, so `price_stats.py`
  and `comps_csv.py` are unchanged.

`python lib/price_stats.py` — distribution tiers from the saved JSON(s):
- `--best-match <bm.json> [--price-high <ph.json>] --unit <single|pair|set|lot|duplicate> --condition <new|used> [--require-tokens <tok>...] [--price-field <sold|total>]`
- prints the Distribution line (n / median / IQR / dispersion), vetted ceiling, the three tiers, a confidence label, and the per-filter drop log. `--price-field total` for free-ship listings (default — see Delivered-price basis). Fold its block into `price.txt`; n<3 ⇒ confidence `thin`, fall back to the closest era-peer (it says so).

## Saved comp artifacts (every stage leaves a reviewable record)

The user reviews the raw research, so each stage persists its comps:

- **Stage B (Browser)** → **two** JSONs, one per sort, saved beside `price.txt`
  by `--save-dir <shoot-dir>` (live run auto-saves; MCP path saves via
  `--ingest`). `price_stats.py` reads both.
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

A comp without a URL is not a comp. Use **direct per-item** `ebay.com/itm/<id>`
URLs (Stage B returns one per comp; Stage A / Chrome hrefs too); fall back to the
sold-search URL only when a per-item href genuinely can't be captured (e.g.
Chrome `get_page_text`) — and say so. Every output also ends with the
consolidated **Comp URLs** block (exact/near-exact first, then ceiling/context,
then the sold-search URL — format under Output); never list a bare price without
its URL there.

## Research log (MANDATORY — proof every search type actually ran)

Every PRICE run, for every item, emits a Research log accounting for ALL
THREE stages explicitly. This is non-negotiable evidence: it shows what
research happened, not just the comps that survived. Stages A and B run on
EVERY item; C is conditional. Each stage is `RAN` (with proof) /
`SKIPPED` / `UNAVAILABLE` (with a reason) / `NOT TRIGGERED` (C only).

(Note: "Stage A/B" = the search METHOD — WebSearch / logged-in Chrome.
Don't confuse with the comp-quality "Tier A/B/C" further down.)

    Research log — Item <N>
      A · WebSearch : RAN — query "<q>" — <n> hits — <one-line finding> — logged to comps.csv
      B · Apify     : RAN via <MCP|CLI> — query "<q>" — best_match run <id> (<n>) + price_high run <id> (<n>) — USD-validated (charm <x>%) — saved 2 JSONs — price_stats n_kept=<n>, conf=<good|partial|thin>
                      [ALT] UNAVAILABLE — <no Apify MCP tool AND no shell/egress | api.apify.com egress blocked (sandbox proxy) | no token | CurrencyLeakError>
                      [ALT] THIN (verified) — 0 comps, control query "<q>" returned <n> — backend healthy, market genuinely empty
                      [ALT] BLOCKED — 0 comps AND control query "<q>" returned <n> (< 5) — backend down, NOT thin; Stage B contributes NO tier
                      [NOTE] served by FALLBACK <actor> — primary returned 0 (silently blocked)
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

**Chat presentation — thumbnail board (HARD RULE — see [_shared.md](_shared.md)
"Showing comps to the user").** Any time eBay comps are surfaced to the user in
chat — the PRICE output, a comp walkthrough, or any answer that quotes comps —
you MUST render them as a visual board where EVERY comp carries all three:
**its thumbnail image EMBEDDED as a base64 `data:` URI** (download + resize +
inline — NOT `<img src="https://i.ebayimg.com/...">`, which the sandbox CSP
blocks into a broken image), **a clickable link to the real eBay listing**
(`<a href>` to the comp's genuine `https://www.ebay.com/itm/<id>` — never a
hand-written id), and **the delivered price** (+ a match/ceiling/excluded tag).
Deliver as a self-contained HTML file via `SendUserFile(display:"render")`, or
inline the same self-contained HTML into `show_widget`. A plain markdown table,
a text list, or remote-`src` images are NOT acceptable — the user requires
seeing real thumbnails and clicking through to verify each listing himself.
Build the HTML with a small script straight from the saved comp JSON (it has
`thumbnail` + `url` per comp). This is the chat layer; the text `price.txt` +
`comps.csv` artifacts stay as specified above. Dial the query
(material/size/origin qualifiers) for a tight exact-match cohort BEFORE showing
the board.

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
distribution. Adopt its numbers; don't re-derive by eye. For a free-shipping
listing these are **delivered** prices (run with `--price-field total`), so the
list price maps to them 1:1; always pair them with the net-to-us figure (list −
our_postage − fees) per the Delivered-price basis section.

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
