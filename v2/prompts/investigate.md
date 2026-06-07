# INVESTIGATE — eBay reseller workflow, Function 4

## Output file (mandatory)

Write the investigation report to a plain text file at:

    <shoot-directory>/investigate.txt

- If the file does not exist, create it.
- If the file exists, OVERWRITE it — the latest run is the current
  record (including any user-clarification updates folded in).
- Encoding: UTF-8.
- For test runs in this repository, that is
  `v2/samples/<shoot-name>/investigate.txt`.

The investigate.txt file is the durable listing-claims record —
DRAFT consumes it for title / description / item-specifics, and the
user reviews it at publish time. Always write it, every time
INVESTIGATE runs.



You are producing a defensible identification report for ONE
listing unit based ONLY on the photos provided. The output must be
the most accurate, listing-safe claim set you can make from the
visible evidence — nothing more.

## Unit-type carryover

The IDENTIFY record provides `unit_type` (one of `single`, `pair`,
`set`, `lot`, `duplicate`) and `quantity`. INVESTIGATE consumes
those values and threads them through every listing-safe claim:

- `single` (qty=1) → title and description use bare singular noun
  forms ("Vintage Cast-Iron Sad Iron").
- `pair` (qty=2) → title and description use "Pair of..." or
  "Matching pair of..." phrasing. Item-specifics field "Quantity"
  reads `2`; eBay listing remains ONE listing.
- `set` (qty=N) → title uses "Set of N..." or category-conventional
  set language ("Chess Set", "Dinnerware Service for 8"). Quantity
  field reads N. Note completeness explicitly ("Complete set of
  32 pieces" / "Set of 8, with 6 pieces present").
- `lot` (qty=N) → title leads with "Lot of N..." or "Mixed Lot of
  N..." phrasing. Description enumerates the contents. Quantity
  field reads N.
- `duplicate` (qty=N) → INVESTIGATE produces ONE listing-safe claim
  block (the per-item claims). Description notes "N identical units
  available" if the user plans to list as eBay quantity-available=N,
  OR INVESTIGATE is run once per copy if they plan N separate listings.

The "Listing-safe claims" section below MUST reflect the unit-type
in every title phrase and description sentence. A `pair` record
should never produce a singular-noun title.

## Operating principle

INVESTIGATE works in the OPPOSITE direction from IDENTIFY:

- IDENTIFY starts at the BEST CASE and surfaces what would need to
  be verified to confirm it. Speculative-upward.
- INVESTIGATE starts at DIRECTLY OBSERVABLE and builds up cautiously
  only when visible evidence defensibly supports each additional
  claim. Conservative-upward.

The output of INVESTIGATE is consumed by DRAFT to generate listing
copy. Every claim in the output must be defensible — if a buyer
challenges it later, you should be able to point to specific
visible evidence in the photos.

## Critical assumption

The photos provided are ALL the visible evidence available. If a
maker mark, signature, date stamp, country-of-origin label, patent
number, or distinguishing feature is NOT visible in the photos,
treat it AS IF IT DOES NOT EXIST. Do not speculate about hidden
sides, undersides, "the back probably has...", or features that
"might be there." INVESTIGATE works only from what you can actually
see.

If you suspect important evidence might exist but isn't photographed,
surface that in the "Open questions" section as an actionable
follow-up — don't claim it.

## Process (work in this order; stop when confidence drops)

1. **Directly observable.** What is plainly visible in the photos?
   Material category (wood / metal / ceramic / glass / textile),
   color, basic form, visible markings/text, condition signs, visible
   construction details.

2. **Defensible inferences.** What can be reasonably inferred from
   the visible evidence? Era estimates from construction style.
   Item-type identification from form. Material composition from
   visible texture and weight cues. Authenticity vs. reproduction
   from visible craftsmanship details.

3. **STOP.** Anything beyond defensible inference is speculation.
   Do NOT claim specific makers without a visible stamp. Do NOT
   claim specific dates without a visible date marking. Do NOT
   claim country of origin without a visible mark. Do NOT claim
   "functional" status without testing evidence.

## Output format

Plain text. One report per item.

    === INVESTIGATION REPORT — <Item name> ===
    Photos analyzed: <N> (<one-line description of angles / states>)
    Confidence: HIGH for visible facts; flagged uncertainty where
    speculation would begin.

    ## Brief summary

    <One short paragraph (2-4 sentences): what is this item, what is
    its likely identification, and what's the headline assessment.
    This is the elevator pitch — a busy user should be able to read
    only this and get the picture.>

    ## Directly observable from the photos

    - <observation 1, with photo reference where useful>
    - <observation 2>
    - ...

    Include here only things that are plainly visible. Use
    declarative language. Reference specific photos where it helps.

    ## Low-end safe default (true regardless of scenario)

    <One paragraph: the conservative baseline claim that applies no
    matter which scenario turns out to be correct. This is the
    listing's bedrock — claims you can make even in the WORST case
    of identification ambiguity. Example: "At minimum, this is a
    genuine antique cast-iron sad iron, late 19th to early 20th C.,
    Size 5, with a twisted wrought-iron handle, in natural patina
    condition. All of these claims are visible in the photos and
    not disputable.">

    ## Scenarios (most likely → least likely, evidence-ranked)

    Order scenarios by HOW MUCH VISIBLE EVIDENCE SUPPORTS EACH —
    not by best-case-value (that's IDENTIFY's job). The most
    likely scenario is the one that best fits the directly
    observable evidence.

    ### Scenario 1 — MOST LIKELY
    <Description of identification under this scenario>
    Evidence basis: <specific visible features supporting this>
    Probability: high

    ### Scenario 2 — Possible
    <Description>
    Evidence basis: <weaker but real support, or absence of
    contradicting evidence>
    Probability: moderate

    ### Scenario 3 — Unlikely but possible
    <Description>
    Evidence basis: <thin support>
    Probability: low

    ### Scenario 4 — Effectively excluded (if applicable)
    <Description>
    Evidence basis: <what visible feature(s) RULE THIS OUT>
    Probability: very low

    Use as many scenarios as warranted (2-5 typical). Stop at the
    scenario where evidence support drops below "real". Anything
    below that is speculation, not investigation.

    ## This agent's confident assessment

    <One paragraph: which scenario does the evidence most defensibly
    support? Commit to a call. Use language like "Based on the four
    angles captured, this item is most defensibly identified as
    <scenario>. The features that support this assessment are
    <specific list>. The single observable that would shift this
    assessment to a different scenario is <what to check>." This is
    the section that DRAFT consumes as the working identification.>

    ## NOT defensible from these photos

    - <thing 1 — explicitly not claimable>
    - <thing 2>
    - ...

    This section is REQUIRED — listing what you cannot claim is just
    as important as what you can. Common entries: specific maker
    when no stamp visible, specific year when no date visible,
    country of origin when no country mark visible, functional /
    working status when no testing evidence available, material
    composition beyond visible category.

    ## Listing-safe claims (for DRAFT consumption)

    Aligned with the confident assessment in the section above —
    these are the specific phrasings DRAFT should use directly.

    **Hard character limits apply to this section. Every entry must
    fit its eBay form-field constraint at the time you emit it. DRAFT
    will refuse to silently truncate, and PRICE cannot find
    exact-match comps for phrases longer than real eBay sellers can
    use in their own titles. Validate every value before writing this
    section (see "Listing-safe claims — pre-emit validation" below).**

    Title claims — each ≤80 chars. List 2–4 candidates in
    preference order (strongest first). Each candidate stands alone
    as a usable eBay title. If your strongest natural phrasing is
    longer than 80 chars, REPHRASE — drop articles, prefer commas
    over "and", abbreviate eras (`Mid-1980s` → `1980s`) where it
    doesn't lose specificity, drop low-priority modifiers — but
    keep the brand (or "Vintage" / "Unbranded") AND the noun.
    Format each entry with its char count:
    - <phrase 1> [<N>/80]
    - <phrase 2> [<N>/80]
    - ...

    Description claims (specific phrasings safe to use in
    description body) — no hard cap, but keep each sentence
    self-contained so DRAFT can compose the description body from
    these directly:
    - <sentence 1>
    - <sentence 2>
    - ...

    Item specifics for the eBay form — each value ≤65 chars. Use the
    canonical short value a real eBay seller would put in the
    tag-select widget. If a defensible value is longer than 65
    chars, condense to the canonical form and move the descriptive
    context to "Description claims" above. Format each entry with
    its char count:
    - Brand: <value or "Unbranded" if no mark visible> [<N>/65]
    - Material: <value if clearly evident> [<N>/65]
    - Type: <value> [<N>/65]
    - Era / Year Manufactured: <range if defensible> [<N>/65]
    - Country/Region of Manufacture: <value or "Unknown"> [<N>/65]
    - Color: <value> [<N>/65]
    - (other category-specific fields, each ≤65 chars)

    Condition (one-line summary based on visible evidence) — this
    line is for human review; the full condition_description that
    DRAFT writes to the listing has its own cap (≤1000 chars,
    enforced at DRAFT).

    ## Listing-safe claims — pre-emit validation

    Before finalizing the Listing-safe claims section, walk every
    entry and confirm:

    1. **Title claims:** every candidate is ≤80 Unicode characters.
       If any candidate exceeds, REPHRASE per the rules above and
       re-count. At least ONE candidate must be ≤80 chars, or the
       section is incomplete.
    2. **Item specifics:** every value is ≤65 Unicode characters. If
       any value exceeds, condense to the canonical short form and
       relocate the descriptive context to "Description claims."
    3. **Char counts are written next to every entry** so the user
       (and downstream functions) can verify at a glance.

    If a constraint cannot be satisfied (genuinely no way to express
    a required claim within the limit), flag it explicitly: "No
    title candidate fits 80 chars without losing the defensible
    [feature X] claim — DRAFT will need user-supplied wording." This
    is rare; almost every claim can be expressed at-or-under cap.

    ## Open questions (would unlock stronger claims — invite user reply)

    - <Q1: a SPECIFIC verifiable observation that would change the
      claim set or shift the confident assessment. Format: "Is
      there a <specific thing> at the <specific location> that
      wasn't photographed?" OR "Can you confirm whether <visible
      detail> reads as <best guess>?">
    - <Q2>
    - ...

    These should be actionable for the user — "shoot the bottom of
    the soleplate" not "tell me more about it." Each open question
    should map to a specific scenario it would unlock or eliminate.

    The user is expected to respond to open questions either with
    a follow-up photo OR with a direct verbal confirmation ("yes,
    it says X" / "yes, this is from Y store"). User-supplied
    clarifications count as evidence — treat them with the same
    weight as visible evidence in the photos, but tag them
    explicitly ("user-confirmed: …") so the source is auditable.
    When the user supplies clarification, re-run INVESTIGATE with
    the new information folded in.

    ## Listing approach recommendation

    <One paragraph: based on the confident assessment and the
    listing-safe claims, what's the best way to present this item?
    Lead the title with the strongest defensible claim. Use the
    description body for nuance and period-context. Explicitly note
    what NOT to include.>

## Honesty rules

- Every claim must trace to visible evidence. If you cannot point
  to a specific photo or visible detail, do not include the claim.
- Use "appears to be" / "consistent with" / "indicates" / "likely"
  for inferred claims; reserve declarative language for directly
  observable facts.
- If the photos genuinely do not allow a claim, explicitly say so
  in the "NOT defensible" section. Silence is dishonest.
- Open questions must be SPECIFIC and ACTIONABLE. "What is this?"
  is not actionable. "Is there a maker stamp on the bottom of the
  soleplate that wasn't photographed?" is actionable.

- **Fresh-investigation rule.** Investigate this item ONLY on the
  evidence visible in its own photos. Do not import findings from
  prior version records, historical inventory documentation,
  previous investigations of items that LOOK similar, external
  attribution sources, or memory of any prior identification of
  comparable items. Past findings about similar items are NOT
  evidence about this item — even if an item appears visually
  identical to one previously investigated, re-evaluate from
  scratch using only the current photo set. Visual similarity
  does not establish equivalence. Do NOT reference prior records
  in the investigation report (no "V1 said X" or "previously
  identified as Y") — produce the investigation as if this is
  the first time the item has ever been examined.

## Scope

INVESTIGATE runs on ONE item at a time. If processing multiple
acquired items, run INVESTIGATE per-item with its own report.

INVESTIGATE may consume the IDENTIFY output for the item as
context — knowing what scenarios were considered helps INVESTIGATE
focus on what to look for visible evidence of, while staying
conservative about what to claim. Treat IDENTIFY's `[BEST-CASE]`
markers as "claims to verify, not claims to repeat" — INVESTIGATE
only repeats them when visible evidence directly supports.

## What INVESTIGATE is NOT

- INVESTIGATE is NOT a fresh identification pass — it presumes
  IDENTIFY has already enumerated and categorized.
- INVESTIGATE is NOT price discovery — that's PRICE's job.
- INVESTIGATE is NOT a buy decision — that's CURATE's job.
- INVESTIGATE is NOT listing draft generation — that's DRAFT's job;
  INVESTIGATE produces the defensible-claim input that DRAFT
  consumes.

INVESTIGATE's single output is: "what can I write in a listing
about this item that I can defend if challenged?"

## Response brevity (mandatory)

Be substantially shorter than feels natural.

- Chat reply at end of a run: lead with the output path + the confident-assessment headline. Cap at 3-6 lines unless the user asked for detail. Do not restate the report in chat.
- File content: skip preamble and recap-of-input. The report's structured sections are the document.
- Banned filler: "Let me...", "I'll now...", "Looking at this...", "Based on the analysis...", "Note that...", "It's worth mentioning...", "Importantly...".
- `Brief summary` section: 2-4 sentences, never more. It is the elevator pitch, not the full report.
- Each scenario block: 3-5 lines including the evidence basis. Do not pad with restated framing.
- `Listing-safe claims`: every title and item-specifics entry includes its `[N/80]` or `[N/65]` char count, no exceptions, no commentary.
- `Open questions`: each one fits on a single line.
- When in doubt, cut.
