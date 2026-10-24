# Playboy magazines — specialization module

```yaml
triggers: [playboy, "playboy magazine", "playboy lot", "playboy playmate", "playboy playmate of the month", "playboy centerfold", "playboy special edition", "playboy's book of lingerie", "playboy's college girls"]
version: 2.0.0
last_reviewed: 2026-10-24
sources:
  - eBay Restricted adult items policy, help id 4278 (US page bot-checked; read via ebay.ca and ebay.co.uk editions and an eBay Community Mentor quote of the US text), 2026-10-22; age clause read on the US page, 2026-10-24
  - Wikipedia — Playboy Playmate, "Underage nudity" (citing The New Yorker, Joan Acocella, "The Girls Next Door", 2006-03-20), read 2026-10-24
  - Wikipedia — Brooke Shields, Garry Gross, Traci Lords, Penthouse (magazine), read 2026-10-24
  - eBay Live policy, help id 5478, 2026-10-22
  - eBay Taxonomy + Metadata APIs, category 280 aspects and condition policy, pulled 2026-10-22
  - CGC (Certified Guaranty Company) — Playboy grading announcement (2009) and newsstand-edition article
  - Wikipedia — Playboy (first issue, 2016–17 non-nude period, end of regular print, Special Editions, Nov 1972 print run)
  - Our own comps — inventory/ES/MON1020-M/02-playboy-trump-cover/ (sold pull 2026-10-22, n=102 unique rows) and eBay Browse active pulls 2026-10-22 (2,094 unique Playboy listings, asking prices only; saved in inventory/ES/MON1020-M/_research/browse-active-20261022/)
  - Our own sales ledger — printed-paper singles and small lots, Aug–Oct 2026
```

> **Read the eBay policy section and the never-list section first.** The
> never-list decides whether an issue goes on eBay at all; the policy decides
> which frames can be listed. Both are settled before identification or
> price matter.

> **Data honesty.** Exact-issue SOLD data exists for one issue only (March
> 1990). Everything else here marked *asking* comes from active listings,
> which run high. The one issue we can check: March 1990 has a median active
> ask of about $115 (marketing sign-off, 2026-10-22) against a $48.89 sold
> median, so asks ran about 2x sold there. That is one data point. PRICE
> still runs the live sold hunt for every single and every lot.

## eBay policy (binding: read before shooting or drafting)

**Allowed, with conditions.** eBay's restricted adult items policy (help id
4278) allows adult magazines that contain nudity *"only if they don't
contain sexual activity and if no images of nudity are shown in the
listing"*, and names Playboy as an example. The UK edition says Playboy
*"can be listed as long as the listing and product don't contain nude images
or explicit content"*. The US page sits behind a bot check. The wording
above comes from the ebay.ca edition and an eBay Community Mentor's quote of
the US text, both read 2026-10-22.

- **Nudity, eBay's definition:** *"male/female genitalia, anus, or female
  breasts where the areola or nipple is visible"*, including see-through
  clothing (ebay.ca edition). Bare backs, hips and shoulders fall outside it.
  We still frame them out on interiors (house rule from
  `art_direction.md`, 2026-10-22).
- **Category:** `280` Books & Magazines > Magazines. eBay's own category
  suggestion for "playboy magazine" returns 280 first (Taxonomy API,
  2026-10-22), and 249 of 291 active raw copies of March 1990 sat in 280
  with no `adultOnly` flag (marketing sign-off). The US **Adult Only
  category is gone** for new listings: eBay closed it on 15 June 2021 and
  kept Playboy listable as long as the listing shows no nude images (Fox
  Business, 2021-06-15). So there is no adult-only visibility penalty to
  weigh, and no adult category to choose.
- **What the photos may show:**
  - **Covers:** usually listable. Newsstand covers were made to be displayed
    in shops, but **check every cover against the definition anyway** and
    never assume it passes.
  - **Spine, back cover, corners, contents page:** listable. Check the
    contents-page thumbnails, because one can carry nudity even at thumbnail
    size (the March 1990 "Worldly Women" thumbnail did).
  - **Centerfold:** never the image. Show that it is present through a frame
    that excludes all nudity (the Playmate Data Sheet flap, or a clothed
    edge panel) or state it in words only.
  - **Pictorials:** never shown.
- **No masking.** Some sellers put bars or white squares over nudity. The
  policy text we read neither allows nor forbids masking, so we don't do
  it. A compliant frame is **framed compliant at capture**. If a frame isn't
  compliant, reshoot it, or leave it out and describe that part in words.
  PREP can't cut a box out of a frame (`--crop` is only off/on/pad, and its
  approval covers the whole shoot, not one file), so there is no crop
  fallback. A cover that fails the definition is shown as spine and back
  cover only, and the cover is described in words.
- **Feedback photos** may not contain nudity even when the item does
  (ebay.co.uk edition). That's the buyer's business, not ours, but don't
  ask buyers for photos.
- **eBay Live / Twitch:** the eBay Live policy (help 5478) lists *"products
  displaying nudity"* among items that need eBay's written approval, and
  bans nudity on stream. Every Playboy contains nudity, so **keep Playboys
  off eBay Live and Twitch** unless Russell gets written approval. Clips
  and marquees follow the same rule. A never-list issue (next section)
  stays off them even with approval.
- **Not allowed at all:** magazines with sexual activity. Penthouse and
  Playgirl get the same image rules. For any other men's title, stop and
  check 4278 before shooting.
- **Shipping:** periodicals with ads are not Media Mail (DMM 173.4.2, see
  `prompts/draft.md`). They ship Ground Advantage. Adult material is
  restricted in some destination countries (price.txt research note,
  2026-10-22), so every Playboy ships **US only**. There's one rule per
  store:
  - **Main store (singles):** set `shipping.us_only: true`. That routes to
    the US-only policy `297194269014` instead of the default worldwide/eIS
    policy.
  - **Junk store (lots):** leave `shipping.us_only` **unset**. The junk
    default policy `264925613013` already ships US only, and setting the
    flag there hard-stops sync.

## Issues never listed on eBay (subject under 18)

4278 adds an age rule: *"When listing films, magazines, books, or photos
that contain images with nudity that are otherwise allowed under this
policy, you must ensure that all nude persons were above the age of 18 at
the time the images were produced."* (US page, read 2026-10-24.)

**The check: before shooting, and again before PREP.** Look up the issue's
Playmate and cover subject in the tables below. A documented under-18
subject in a nude or partly nude pictorial makes the issue **ineligible for
eBay on every store**, as a single or inside any lot, and it never goes on
eBay Live, Twitch or clips. For a match:

- Shoot the cover and spine only, for the record. The interior stays
  unshot.
- Mark the draft's `meta:` block with `ebay_eligible: false`,
  `ebay_ineligible_reason:` (the table row and its source, e.g. "eBay adult
  policy 4278: Linda Moon, 17") and `channel:` as Russell directs. October
  1966 went to `mall`, the Greensboro antique-mall booth.
- The art director's PREP sign-off repeats the check
  (`.claude/agents/art-director.md`, "PREP sign-off: eligibility first").

**The reference.** Wikipedia, "Playboy Playmate", section "Underage nudity"
(https://en.wikipedia.org/wiki/Playboy_Playmate), read 2026-10-24. It cites
The New Yorker: Joan Acocella, "The Girls Next Door", 2006-03-20, p. 144.
It is a starting list, neither complete nor official, and eBay doesn't
publish its own screen. A Wikipedia birth date with no citation settles
nothing by itself: treat the issue as an open question and take it to the
collection-curator for a cited source.

**When eBay holds a Playboy listing,** the listing manager reads the hold
reason in that account's eBay Messages, and a confirmed age hold adds the
issue here. TODO (listing manager): confirm the October 1966 reason in the
junk account's Messages; the art director's trace to 4278 is the likely
cause.

**US monthly issues, per the reference:**

| Issue | Playmate (as the page names her) | Age when she posed |
|---|---|---|
| January 1958 | Elizabeth Ann Roberts ("Schoolmate Playmate") | 16 |
| April 1959 | Nancy Crawford | 17 |
| July 1960 | Teddi Smith | 17 |
| December 1963 | Donna Michelle | 17 |
| September 1965 | Patti Reynolds | 17 |
| October 1966 | Linda Moon | 17. eBay held our junk-store listing 880046873226 on 2026-10-24; we ended it, and the copy goes to the mall booth. |

**Other editions, same page:** German *Playboy*, 1977, Ursula Buchfellner
(16); Dutch *Playboy*, June 1988, Karin and Mirjam van Breeschooten (17).
Their later US issues (October 1979; September 1989) were posed at 18, per
the page, and pass this check.

**Never buy, never list (outside the Playboy monthlies, same rule):**

| Title | Subject | Source (Wikipedia, read 2026-10-24) |
|---|---|---|
| *Sugar 'n' Spice* (Playboy Press) | Brooke Shields, photographed nude at 10 by Garry Gross in 1975 | "Brooke Shields"; "Garry Gross" |
| Any magazine featuring Traci Lords dated 1984–86, e.g. *Penthouse* September 1984 (her centerfold, at 16), *Velvet*, *Juggs*, *Club* | Traci Lords, under 18 until May 1986 | "Traci Lords"; "Penthouse (magazine)" |

## When this applies

- A Playboy magazine (US monthly), any year, single or lot, raw or graded.
- **Playboy Special Editions** (newsstand specials such as *Book of
  Lingerie* and *College Girls*; Wikipedia) are separate publications. This
  module applies, but they are their own taxonomy row. Never mix them into a
  monthly-issue lot or comp set.
- **Carve-outs:**
  - Foreign-language editions are different products with different comps.
  - Playboy trading cards, posters, calendars and books go to the general
    pipeline. The policy section still applies to their photos.
  - **A loose centerfold sold alone** is a different product (and its image
    *is* the nudity), so stop and ask.
  - Any magazine with sexual activity is prohibited (4278), so stop.

## Fast triage (the big-lot pass)

About 30 seconds an issue, done from the intake frames (see **Intake**):

1. **Date it** from the spine or cover date line, and check the date against
   the never-list. A match comes out of the run here and goes off eBay (see
   **Issues never listed on eBay**). Dec 1953 and every other 1950s issue go
   straight to **look closer**.
2. **Physical status:** is the centerfold attached? Any mailing label? Water
   damage, cut pages, a detached cover? Incomplete or damaged issues go to
   the incomplete pile and are never mixed into a clean lot.
3. **Is it a separate product?** Signed by hand (not the printed Playmate
   signature), in a CGC slab, or factory-sealed in its polybag: **look
   closer**.
4. **Cover and contents:** a famous non-Playmate on the cover (actor,
   musician, politician) or a landmark interview named on the cover:
   **look closer** (an exact-issue comp decides it).
5. Everything else that passed step 1 is **lot filler**. That's the large
   majority of any run.
   Active asks for single issues from 1965 to 2005 cluster at $10–$17
   median *asking* (Browse, 2026-10-22). That's under the single-out
   threshold (see **Format**).

When in doubt, pull it for a closer look. The exact-issue sold comp makes
the call, not the cover's fame.

## Taxonomy — what's in a run

| Type | How to recognize | Notes |
|---|---|---|
| Monthly issue, 1953–2020 | Cover date line "MONTH YEAR • $price"; spine reads PLAYBOY / ENTERTAINMENT FOR MEN / MONTH YEAR (seen on March 1990) | First issue Dec 1953, undated (Wikipedia). Read the volume/number from the contents page; don't compute it. "Vol. 37 No. 3" = March 1990 is the only one we've read. |
| Anniversary / holiday issue | Cover says "Anniversary Issue" | Dec in the 1950s–60s, Jan in later years (from active titles: Dec 1956 "Third", Dec 1958 "Fifth", Jan 1982/1986/1988/1999). Asks sit in the ordinary $13–$25 band. **Not a key by itself.** |
| Non-nude era | March 2016 to early 2017 | Playboy dropped full nudity from March 2016 and reversed in Feb 2017 (Wikipedia). Still Playboy, still 4278. Check every frame anyway. |
| Quarterly / end of regular print | 2019–Spring 2020 | Spring 2020 was the last regularly scheduled print issue (Wikipedia). |
| Special Edition | Separate masthead (e.g. *Playboy's Book of Lingerie*) | Own lots, own comps. |
| Newsstand vs subscription copy | Subscription copies carry a mailing label on the cover | In CGC's newsstand-identification article (written about Sports Illustrated), newsstand copies are the ones collectors prefer. We haven't measured a Playboy premium; one sold March 1990 comp led its title with "Newsstand Edition". State it as a fact, not a premium. |
| Price/edition variants | Different cover price or "European price" | Traded as separate products (March 1990: "$3.95 cover price" and "European price" variants in comps). Claim a variant only when the cover shows it. |
| CGC-graded | Slab with label | Separate product. Graded March 1990 copies sold at $158–$2,007, against $49 raw. Never comp raw against slabs. CGC began grading Playboy on 18 June 2009 (CGC). |
| Reprint of Dec 1953 | Active listings openly sell "First Issue Reprint" copies, including a "2007 Reprint" (Browse, 2026-10-22) | **Presume any Dec 1953 copy is a reprint until proven otherwise.** |

## The money types (named watch-list)

A short list. Each entry is a reason to run the exact-issue comp, not a
price. Don't extend this list from memory.

| Issue | Why it's watched | Evidence |
|---|---|---|
| Dec 1953 (No. 1, Marilyn Monroe) | First issue (Wikipedia) | Reprints are common (see Taxonomy). Hand it to the art director plus a Heritage/CGC reference check before any claim. |
| Any 1950s issue that passes the never-list | Era premium | 1958 single issues ask a $29.99 median (n=181) against $10–$17 for later years (Browse, asking). |
| March 1990 (Donald Trump cover and interview) | Celebrity cover plus interview | **Sold**: n=30 raw, unsigned, median $48.89 delivered, IQR $41.68–$69.58 (our price.txt, 2026-10-22). |
| Nov 1976 (Jimmy Carter interview) | Landmark interview with a presidential candidate (Wikipedia) | No comp pulled yet. |
| Nov 1972 | Best-selling single issue ever, 7,161,561 copies (Wikipedia). Common by print run, so any premium is demand, not scarcity | No comp pulled yet. |
| Genuinely hand-signed copies | Separate product | Two "signed" March 1990 copies sold at $500 and $600 (sold pull; their authentication is unknown to us). A PSA-encapsulated Trump-signed copy is listed at $3,218.99 asking. Authentication (COA/PSA/Beckett) decides it, not us. |

## Value drivers (priority order)

1. **Issue identity:** the date, the cover subject and the interview. An
   exact-issue comp beats everything else.
2. **Centerfold attached.** A copy without its centerfold is incomplete.
   Treat it as reading-copy grade and lot it separately.
3. **Separate-product status:** a hand signature, a slab, or factory
   sealing.
4. **Condition**, graded with the vocabulary below.
5. **Newsstand (no label)** versus subscription. Disclose it either way.
6. **Era.** Only the 1950s show a clear premium in asks. 1965–2005 look
   flat at $10–$17 median asking.

## Condition grading

eBay category 280 accepts exactly five conditions (Metadata API,
2026-10-22): **Brand New (1000), Like New (2750), Very Good (4000), Good
(5000), Acceptable (6000).** There is **no Used – Excellent** in 280.
Where the rubric would say Excellent, take the tie-break **down to
`USED_VERY_GOOD`**. Reserve `NEW` for a copy still factory-sealed in its
original polybag, and only on the main store (an as-is, no-returns store is
the wrong place for a NEW claim; see the storefront notes in `config.yaml`).

Use these terms, as `<location>: <defect>` per `condition-rubric.md`:

| Location | Vocabulary |
|---|---|
| Spine | spine stress lines (light, or colour-breaking); spine roll (the book leans); spine split at head/foot; bumped spine head/foot; spine soiling/speckling; spine sun-fade |
| Binding | staples tight / loose / rusted; cover detached at one staple. **Check in hand whether the copy is stapled at all.** March 1990 showed a flat printed spine, so stapled vs square-bound is unconfirmed. Say what you find, never what you assume. |
| Cover | corner bumps / dog-ears / creases (state colour-breaking); edge wear; surface scuffing; tears; writing; price sticker; fading |
| Mailing label | present (subscription copy) / none (newsstand) / removed, residue remains. Don't put the address in a photo: redact it per `_shared.md` PII. |
| Centerfold | attached / detached but present / missing; splits at the folds; tear at a staple; fold creases (normal, but state them) |
| Inserts | subscription or blow-in cards present / absent, **only if checked**. "Complete" is a page-count claim and needs a page check. Without one, don't say it. |
| Interior | page wave; tanning; pages cut or clipped (coupons, pictorials); writing; water staining or rippling; loose pages |
| Odor | in-hand only (musty, smoke). Check `context.txt` blocked phrases. |

**Grade bands (with the rubric's tie-break down):**
- **Like New:** flat, sharp corners, no stress lines, centerfold attached and
  tight, no label.
- **Very Good:** light handling, minor spine soiling or a bumped corner,
  centerfold attached (March 1990 graded here).
- **Good:** stress lines, rolled spine, a creased corner, a label, or light
  interior wave.
- **Acceptable:** a missing centerfold, cut pages, water damage, a detached
  cover, or heavy wear.

**Lots take the grade of their weakest issue,** and the condition section
names every defect by issue.

## Authentication & fakes

- **Printed signatures are not autographs.** March 1990's centerfold carries
  a printed "Deborah Driggs" signature, confirmed against another copy
  (`signature_research.md`). Whether every centerfold of an era has one is
  **not established**, so judge each issue on its own. Never write
  "signed" without hand-signed evidence (ink sitting on top of the print,
  sheen, indentation) **and** treat a real autograph as a separate product
  that needs authentication.
- **Reprints:** Dec 1953 reprints and facsimiles are openly sold (see
  Taxonomy). Any Dec 1953 copy needs a reference comparison before
  IDENTIFY names it an original.
- **Variants:** claim a price or European variant only from the printed
  cover price. Comps for variants and slabs never anchor a raw copy.
- **Copy-swap returns:** record the UPC digits and the defect photos for
  each single (price.txt research note, March 1990).

## Price tiers (coarse bands)

| Tier | Band | What lands here | Listing unit |
|---|---|---|---|
| $ | < $15 sold delivered | Most issues 1965–2020 | Lot |
| $$ | $15–$25 | A named cover or interview with a modest following | Lot, **with the name in the lot title** |
| $$$ | $25–$100 | Celebrity cover/interview keys (e.g. March 1990, $48.89 median); many 1950s issues (to be checked) | Single |
| $$$$ | > $100 raw | Dec 1953 original (if proven); hand-signed or graded copies | Single, exact comp; push-high and Best Offer per draft.md #140 |

Tiers are triage signals, not quotes. PRICE still hunts live comps.

## Format: singles, lots and auctions

**The single-out threshold is about $25 sold, delivered, for the exact
issue (raw, unsigned, n ≥ 3).** A free-shipped single on the main store
keeps:

    net_single = P × (1 − fee − ad) − postage

- **fee:** PRICE's band (17.5% at $25–50; 18% under $25).
- **ad:** about 10%. The main store auto-promotes every listing at publish
  (memory: promote-on-publish), so this applies to every single.
- **postage:** plan $7.00–$9.50 for one issue on Ground Advantage, and
  replace it with the weighed figure.

Compare that with what the same issue adds inside a lot, about **$3–$4**:
- Measured on 1990: two "Complete Year Lot of 12 w/ Donald Trump" sales
  at $81.85 and $96.85 delivered, less about $49 for the Trump issue,
  leaves about $33–$48 for 11 issues.
- Active 6–12-issue lots ask a median of $4.22 per issue (IQR
  $3.02–$6.25), and 13–30-issue lots ask $2.86 per issue (*Browse,
  asking*).
- Our own "Inside Sports Magazine Lot of 5" auction sold at $14.99 on
  2026-06-01, about $3 per issue (sales_ledger.csv).

At common-issue prices ($10–$17 *asking*, less when sold), fee, ad and
postage take most or all of a single's price, so common issues go in lots.

**Keys (single listings):** fixed price, main store, free shipping, at
PRICE's tier.
- On March 1990, auctions didn't beat Buy It Now. Raw single auctions sold
  at a $45.85 median delivered (n=11, and 6 of the 11 closed on 1–2 bids,
  so essentially at the start price). Plain BIN sales ran a $55.53 median
  (n=18; uncleaned grouping of the same pull).
- Our own store's printed-paper singles and small lots sold at 75–100% of
  their fixed ask with free shipping (sales_ledger.csv, Jul–Oct 2026; the
  rows that record a percent of ask).

**Lots: one complete year where we have one; otherwise 10–12 issues by
decade.**
- Never mix centerfold-attached issues with incomplete ones. Never put a
  Special Edition in a monthly lot. A never-list issue goes in no lot at
  all, so a year holding one lists as a partial year.
- Where a key would break a complete year, price it both ways (key single
  plus the partial lot, against the year lot) and take the higher total. On
  a tie, keep the year whole: one listing, one parcel. 1990 can't settle
  it, because the partial-lot value above is itself backed out of the
  year-lot sales.
- A 12-issue lot weighs roughly 6–10 lb on IDENTIFY's 0.5–0.8 lb per-issue
  estimate. Free shipping at that weight would eat a large share of a
  $40–$50 lot, so **lots ship buyer-pays calculated.** **Weigh every lot
  before it lists.** The estimate is unweighed, and the shipping figure
  feeds the start-bid formula below.
- eBay's final value fee is charged on the **total including buyer-paid
  shipping**, so buyer-pays shipping still costs us its fee share.

**Lots as auctions: a fixed end date, which is the point when the goal is
to empty the lot.**
- The active Playboy market is almost all fixed price: 2,064 of 2,094
  unique active listings (30 auctions) across 11 Browse queries,
  2026-10-22. That's a crowded BIN shelf. Whether auctions do better in
  this thinner pool is **an inference, not measured**.
- In a thin auction the start bid is the sale price: 6 of 11 March 1990
  auctions closed on 1–2 bids. So **the start bid is the lowest price we're
  happy to take,** never $0.99. For common issues from 1965 to 2020, the
  start bid per issue is:

      start_per_issue = max( $3 , (C / N + 0.28) / 0.835 )

  - **C:** the magazines' share of the acquisition cost. It's a named input
    from the lot's records, never written here.
  - **N:** the issue count from intake.
  - **0.835** and **0.28** are the accountant's constants (2026-10-22). They
    cover the final value fee on the item and on about $18 of buyer-paid
    shipping, for a **12-issue lot**. Re-derive them for other lot sizes
    or a different weighed shipping figure.
  - **The $3 floor** is the bottom quartile of active lot asks and the low
    end of the 1990 sold estimate.

  At the floor alone, lots cover the cost share only when the run is large.
  The accountant checks C/N for each lot after intake. Pre-1965 lots get
  their own comps. The start bid is Russell's call per batch; this formula
  is the proposal.
- Duration and timing: Russell's stated preference (2026-10-08, memory) is
  **10-day auctions listed Friday about 8 PM ET, ending Monday about 8 PM
  ET**. That is his preference, not a measured best. Our only auction run
  so far (9 junk-store jewelry lots ending 2026-10-19) has no recorded
  outcome in the ledger yet.
- Mechanics: the draft carries `format: AUCTION`, `listing_duration:
  DAYS_10` and `price` = start bid. Best Offer is off. **An auction needs a
  payment policy without immediate pay.** The junk store has one
  (`payment_policy_id_auction`); the main store's is null, and
  `list_edit.py` preflight blocks an auction there (errorId 25003).
- Unsold lot: relist once at the same start. If it's still unsold, merge it
  into a bigger lot. Don't walk the start bid down.

## Listing template

### Titles (≤ 80 characters; only facts the photos and INVESTIGATE support)

- **Single:** `<Month> <Year> Playboy Magazine <cover subject> <Interview|Cover> <Playmate> Centerfold`
  - e.g. `March 1990 Playboy Magazine Donald Trump Interview Deborah Driggs Centerfold` (76/80)
  - "Centerfold" only when it's attached and seen.
  - "Newsstand" only with no label.
- **Lot, complete year:** `<Year> Playboy Magazine Full Year All 12 Issues Centerfolds Attached <name> <name>`
  - "Complete Year" is the hobby's search phrase (it's in the 1990 sold
    titles). Use it only when every issue was checked in hand: centerfold
    attached and no pages cut.
- **Lot, mixed:** `<Decade|Year–Year> Playboy Magazine Lot of <N> Centerfolds Attached <name> <name>`
  - Name the $$ issues (the cover names as printed).
- **Never** "Rare", "Mint", "Complete" (as in complete pages), "Signed",
  "Autographed", "Nude", "Sexy" or "XXX". Words describing nudity invite a
  policy review and add no search value we've measured. No scarcity words
  without INVESTIGATE's scarcity evidence (draft.md).

### Item specifics (category 280, Taxonomy API 2026-10-22)

| Aspect | Value | Notes |
|---|---|---|
| Publication Name | `Playboy` | The category's one required aspect |
| Publication Year | e.g. `1990` | Selection-only, single value: single-year lots only; leave blank for multi-year lots |
| Publication Month | e.g. `March` | Multi-value: list every month in a single-year lot |
| Language | `English` | |
| Publication Frequency | `Monthly` (`Special Issue` for Special Editions; `Quarterly` years only if the cover says so) | |
| Issue Number / Volume | From the contents page only, e.g. `3` / `37` | Never computed |
| Publisher | Only as printed on the contents/masthead page | Blank if not read |
| Signed | `No` | `Yes` only with authentication |
| Signed By | Only when authenticated | |
| Custom: Featured Person/Artist | Cover subject as printed | Used on March 1990 |
| Custom: Playmate | Name as printed on the Data Sheet | |
| Template fields | `type: Magazine`, `brand: Playboy`, `time_period_manufactured: <decade>s` | |

Leave `Subscription` blank: it's ambiguous (a subscription offer, or a
subscriber copy?).

### Required photos (all must comply with the policy section)

**Single (in order):**
1. Front cover, whole, with margin. Check it for nudity.
2. Back cover, whole.
3. Spine, end to end.
4. Top and bottom corners, close.
5. Contents page, framed at capture so no thumbnail shows nudity.
6. Centerfold-present evidence: the folded flap with the Playmate Data
   Sheet up, framed from the gutter outward with the pictorial page out of
   frame. Skip it if no compliant framing exists, and say "attached" in
   words.
7. Mailing label, if present (address hidden).
8. Every disclosed defect, nudity-safe framing.
9. A staple or spine macro.

**Lot (in order):**
1. All issues stacked spines-out in one frame, so the count is legible.
2. Covers shingled 4–6 per frame so every issue's masthead and date line
   shows (each cover checked).
3. Back covers, about 6 per frame (optional).
4. The worst defect in the lot, close.

**PREP note:** the `printed` category forces crop **off** (prep.md). PREP
has no explicit crop box: `--crop` is off, on or a pad, and its approval
covers the whole shoot, not one file. So nudity is solved **at capture
only**. A frame that isn't compliant as shot is reshot compliant, or left
out with that part described in words. Non-compliant frames stay in the
folder as condition evidence and never go into `photos:`.

### Description skeleton (draft.md's fixed sections)

- **Opener:** "This is an original `<Month Year>` issue of Playboy magazine,
  with `<cover subject as printed>` on the cover."
- **Look-at-this line:** the interview or feature named on the contents
  page, or a period detail on the covers (the back-cover ad). Describe the
  centerfold only as present and attached. **Never describe the nudity.**
- **More-coming line (every Playboy listing):** "More Playboy years are
  coming soon, so stay tuned and check back often." (Russell, 2026-10-24.)
- **More Playboy table:** every other live Playboy listing, as one HTML
  table with one row per year, newest first. It has three columns:
  - **Year.**
  - **Full-year lot:** the volume or year lot, linked to its listing; blank
    when there isn't one.
  - **Monthly singles:** the month names (Jan, Mar, Dec), each linked to its
    single listing.

  A 3-column table keeps it readable on the phone. Only live listings get a
  row. The listing the buyer is reading is left out. The cross-promotion
  refresh below keeps the table current.
- **What's Included:**
  - Single: one magazine, plus "Centerfold, attached: Playmate `<name>`".
  - Lot: **every issue by month and year**, with its centerfold status and
    any cover names.
- **Size:** measured trim and packed weight (GH #170: no placeholders at
  publish).
- **Condition:** `<location>: <defect>` bullets, using the vocabulary above.
  For lots, per issue.
- **Markings:** the cover date and price as printed; the UPC; label or no
  label; the printed Playmate signature "as issued", where confirmed.
- **About this item:** one or two sentences from INVESTIGATE's listing
  approach.
- **Close:** the storefront's standing block, verbatim.

## Intake (inventorying a run with almost no typing)

The goal: one CSV row per issue, read by an agent from batch frames.
Russell's hands sort and place stacks; he types nothing.

**Before shooting (hands only), sort into three piles:**
- **A:** centerfold attached.
- **B:** centerfold missing or loose, or the issue is water-damaged, has
  cut pages or a detached cover.
- **C, set aside:** a 1950s date, signed in ink, in a slab, or sealed in a
  polybag. These get a full single shoot later, once each one passes the
  never-list check.

**Shot list (TG-7 primary; the agent drives `tools/live/shoot.py`, one
"item" per batch):**
1. **Spine stack.** 8–10 issues stacked spines toward the camera, square to
   the lens, backdrop above and below. TG-7 stills land tighter and higher
   than the preview (shoot_log, 2026-10-22), so leave a margin. The agent
   reads the dates from crops cut from the full-size frame (never the raw
   frame in the main thread; `_shared.md`).
2. **Cover shingle.** The same 8–10 issues fanned top to bottom so each
   cover's masthead, date line and cover lines show, 4–6 per frame. This
   catches blank or worn spines, cover subjects and mailing labels.
3. **Back-cover shingle.** One frame per batch: spots back-cover damage.
   Optional.
4. **Pile B only:** one frame of the worst issue's damage.
5. Repeat per batch. **Pile C:** no intake frames; each issue that passes
   the never-list gets the **Single** photo list above.

Intake frames are internal evidence, not listing photos. Covers that fail
the nudity definition are fine to have in the folder; they never go into
`photos:`.

**What the agent writes:** `inventory/<lot>/magazines_intake.csv`, one row
per issue, with these columns:
`batch, pile, year, month, cover_names (as printed), label (y/n/unread),
spine_or_cover_defects, special (special-edition/variant/none),
read_confidence, frames`.

Unreadable dates are marked `unread` with the frame, never guessed. The
agent then:
1. Checks every dated row against the never-list and pulls the matches out
   of the plan, each with its reason. An `unread` row stays out of every
   lot until its date is read.
2. Counts issues by year and finds complete years.
3. Flags $$/$$$ candidates (fast-triage steps 3–4) for an exact-issue sold
   comp.
4. Writes the CURATE Mode B lot plan.

## Output hooks (maps onto IDENTIFY fields)

- **Brand** → `Playboy`.
- **Type** → `Playboy magazine, <Month Year>[, <cover subject> cover]`, or
  `Lot of N Playboy magazines, <years>`.
- **Era** → the printed cover date only.
- **Condition** → the vocabulary above. **Grade from the category-280 set
  (no Used – Excellent).**
- **Distinguishing marks** → the cover date/price line, UPC, label status,
  centerfold status, printed signature (and how it was confirmed), Data
  Sheet name, **value tier ($–$$$$)**, and **nudity flag per frame**
  (which frames are unusable as-is).
- **needs_followup_photo** → the specific missing shot from the photo list
  (bottom corners, spine foot, staples, a compliant centerfold-flap frame).
- **PRICE** →
  - Single: query `Playboy <Month> <Year> <cover subject>`. Exclude CGC,
    signed, variant and lot rows.
  - `price_stats.py` false-positives "complete"/"N issue(s)" as lots
    (NEEDS_REVIEW, March 1990). Neutralise those tokens in a filtered copy
    of the comps until #263 merges.
  - Lots: query `Playboy <year> complete year` / `Playboy magazine lot
    <decade>s`. Price on item-only when the lot ships buyer-pays.
- **DRAFT** →
  - A never-list issue: `meta.ebay_eligible: false`,
    `meta.ebay_ineligible_reason` and `meta.channel` (see **Issues never
    listed on eBay**). No eBay fields after that.
  - `category_id: "280"`.
  - US-only shipping, one rule per store:
    - Main-store singles: `shipping.us_only: true` (routes to `297194269014`).
    - Junk-store lots: leave `us_only` unset (its default `264925613013` is
      already US only; setting it hard-stops sync).
  - Auction lots: weigh first; start bid per the **Format** formula.
  - Best Offer per the #140 gate (off on auctions and under $100).
  - **Never promote a junk-store listing.**
  - `meta.notes` records the nudity check of every frame in `photos:`.

### Cross-promotion refresh (every Playboy publish)

Russell's standing rule (2026-10-24). Each time a Playboy listing goes live,
the listing manager revises **all** existing live Playboy listings so that
each one's More Playboy table lists every other live Playboy listing (year
lots and monthly singles), and drops any that have sold or ended.

- Russell's rule covers this refresh, so it needs no per-listing yes. It
  covers the More Playboy table only. Any other change still needs his yes.
- Run `--update ... --dry-run` first, then the write. Read each listing back
  afterwards.
- An auction that has bids allows only an addition to its description. Append
  the new rows there, and never rewrite the table.
- Links across stores (main and junk): check eBay's links policy before the
  first cross-store link. Until that check is done, link within the same
  store only.

## Sources

- eBay Restricted adult items policy, help id 4278:
  - US (bot-checked; quoted via the Mentor answer below): https://www.ebay.com/help/policies/prohibited-restricted-items/adult-items-policy?id=4278
  - Canada edition (magazine rule, nudity definition): https://www.ebay.ca/help/policies/prohibited-restricted-items/adult-items-policy?id=4278
  - UK edition (Playboy named; feedback images): https://www.ebay.co.uk/help/policies/prohibited-restricted-items/adult-items-policy?id=4278
  - The age clause, read on the US page 2026-10-24 (the .ca edition has
    the same words; the .co.uk edition says "vintage films"): *"When listing films, magazines, books, or
    photos that contain images with nudity that are otherwise allowed under
    this policy, you must ensure that all nude persons were above the age of
    18 at the time the images were produced."*
- Wikipedia, read 2026-10-24, for the never-list:
  - Playboy Playmate, "Underage nudity" (cites The New Yorker, Joan
    Acocella, "The Girls Next Door", 2006-03-20, p. 144): https://en.wikipedia.org/wiki/Playboy_Playmate
  - Brooke Shields (*Sugar 'n' Spice*): https://en.wikipedia.org/wiki/Brooke_Shields
  - Garry Gross (the 1975 photographs): https://en.wikipedia.org/wiki/Garry_Gross
  - Traci Lords (1984–86 career, age): https://en.wikipedia.org/wiki/Traci_Lords
  - Penthouse (September 1984 centerfold, at 16): https://en.wikipedia.org/wiki/Penthouse_(magazine)
- eBay Community, Ask a Mentor, "Playboy Magazines" (category 280; the US
  policy quote; Adult Only gone): https://community.ebay.com/t5/Ask-a-Mentor/Playboy-Magazines/td-p/34459167
- Fox Business, 2021-06-15, eBay ends the US Adult Only category; Playboy
  still listable without nude images: https://www.foxbusiness.com/lifestyle/ebay-sexually-explicit-content-sales
- eBay Live policy, help id 5478 ("products displaying nudity" need written
  approval): https://www.ebay.com/help/policies/selling-policies/ebay-live-policy?id=5478
- eBay Taxonomy API `get_item_aspects_for_category` (280) and Metadata API
  `get_item_condition_policies` (280), pulled 2026-10-22 with
  `lib/ebay_client.py`.
- CGC, Playboy grading from 18 June 2009: https://www.cgcgrading.com/news/articles/cgc-magazine-grading-playboy-sports-illustrated-29uVaPvrBqcliPKYLyadyR/
- CGC, newsstand-edition identification (written about Sports Illustrated;
  the label/newsstand mechanics apply to magazines generally): https://www.cgcgrading.com/news/articles/sports-illustrated-newsstand-e9FzYwEdlLWT2FSVS3UfR/
- Wikipedia, Playboy (first issue, Nov 1972 print run, Carter interview,
  2016–17 nudity change, end of regular print, Special Editions): https://en.wikipedia.org/wiki/Playboy
- Our comps (pipeline data, gitignored):
  - Sold: `inventory/ES/MON1020-M/02-playboy-trump-cover/` (`price.txt`,
    `apify_run_*.json`, `signature_research.md`, `art_direction.md`).
  - Active: `inventory/ES/MON1020-M/_research/browse-active-20261022/`.
    These are the saved Browse pulls plus `summarize.py`, which recomputes
    every *asking* figure here into `summary.json`: the 2,094 listings, the
    per-issue lot asks, the median asks by year, and the $3,218.99
    signed-copy ask.
  - Ours: `sales_ledger.csv` (printed-paper sales; "Inside Sports Magazine
    Lot of 5", auction, $14.99, 2026-06-01).
- Not used: the "what's my Playboy worth" articles that web searches surface
  (appraisily, marketrealist, historytools and similar). They're unsourced
  price lists, below this module's source bar.
