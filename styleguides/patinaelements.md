# patinaelements — seller style guide

```yaml
seller: patinaelements
slug: patinaelements
version: 1
status: adopted          # draft | active | adopted
default: n/a             # promoted into prompts/ — no longer toggled per run
sample: 245 active listings (245 with full detail)
source: eBay Browse API (active listings), sampled 2026-08-24
study: _studies/patinaelements.md
adopted: 2026-08-24      # re-running `study --guide --force` overwrites the notes below
```

**Study, not copy.** Everything below is technique measured from a sample —
slot order, budgets, voice, photo conventions. No title string, sentence, or
photo of theirs is reused or paraphrased into ours. **House rules win**: the
honesty bar, the no-sensationalizing-wear rule, PII redaction, and the
maker-attribution discipline in [`../prompts/_shared.md`](../prompts/_shared.md)
override anything here. A style guide changes *how we say it*, never *what we
are willing to claim*.

## Adopted as house style — 2026-08-24

This guide is no longer an overlay to switch on. It was adopted in two passes:
the structural technique on 2026-08-24, then the tone — first-person voice, the
boilerplate, and condition/scarcity words in the lead title slot — on the
operator's call the same day. What landed where:

- **Title pattern**, including the earned lead slot —
  [`../prompts/draft.md`](../prompts/draft.md#the-house-title-pattern)
- **Body**: Opener → look-at-this line → cross-sell line → What's Included →
  **Size** → Condition → **Markings** → About → the close — draft.md and
  [`../templates/listing-v1.md`](../templates/listing-v1.md)
- **The standing close** — `store.closing_block` in
  [`../config.yaml`](../config.yaml), rendered verbatim on every listing
- **Frame count** — [`../prompts/prep.md`](../prompts/prep.md)

**Not adopted:**

- **All-caps bodies** (100% of theirs). Operator's call: harder to read on a
  phone, and it flattens the emphasis we do want.
- **The "clean, smoke-free environment" line** (100% of theirs). It is a claim
  about our premises, not about an item, and it was not confirmed. It lives in
  config the day it is.

**Adopted with a binding, not as-is:** `MINT` / `RARE` in the lead slot. The
slot is ours to use and it is the strongest word in the field, but the word is
licensed by evidence — `MINT` by the condition rubric's grade, `RARE` by actual
scarcity evidence from INVESTIGATE. Nothing earned → the era word leads. Same
for the first-person voice: it changes who is speaking, not what may be said.

The file stays as the traceable source of the numbers behind those rules, and
as the pattern for the next seller we study.

## DRAFT — titles

- Write to the cap: their titles run ~78 chars median,
  89.0% pushed to >=75. A short title is a wasted title.
- **Lead token** is usually one of: `vtg`, `estate`, `mint`, `antique`, `rare`.
- **Slot order** (mean position, 0 = first word): era 0.5,
  material 6.1, condition 0.2.
- **Era/date:** 97.6% carry an era word,
  14.3% a year or decade. Date it when we can support the date.
- **Measurement:** 29.4% carry one — size earns a slot.
- **Brand:** 51.8% put the brand aspect in the title.
- **Descriptor budget:** ~1.8 adjectives, max
  4. Past that it reads as keyword soup — cut.
- **Casing:** ~9.3% of tokens ALL-CAPS (emphasis
  on one or two words, not the whole title). **Separators:** `/` (9.4%), `-` (4.9%).

## DRAFT — description voice

- Length: ~133 words median, ~7.0
  paragraphs, ~19.9 words per sentence.
- Voice: first person in 100.0% of bodies — shop/personal voice.
- Structure: 0.0% use bullets;
  18.8% raise condition in the first third.
- **Body skeleton** — the labelled sections they use, in order:
  SIZE (100.0%) -> CONDITION (100.0%) -> MAKER'S MARK (100.0%). Where a section is near-universal, treat it as required: a body
  missing it reads as a thinner listing than theirs.
- Casing: 100.0% of bodies are set in ALL CAPS. Copy the
  *structure*, not the shouting — house style stays sentence case.
- Condition phrasing: match their *plainness*, never their drama. Our
  no-sensationalizing rule stands — expected age reads as one neutral clause.

## PREP — photography

- **Count:** median 10 photos,
  38.0% at 12+, 1.6% at the 24 cap. Shoot to that count.
- **Backdrop:** light neutral (180.6/255), colour-tinted (sat 21.1), a styled/varied set (uniformity 48.3). Shoot to that, consistently.
- **Framing:** subject fills ~66.3% of the frame (tight crop, the item owns the frame).
- **Colour:** warm (R−B 12.8) cast, frame contrast 61.7. Match their cast rather
  than pushing saturation to flatter an item.
- Anything the numbers cannot see (props, scale objects, shot order, hero choice)
  belongs here only after a human glance at the storefront grid. Leave a line
  blank rather than guessing.

## Conflicts

| Their pattern | Our rule | Resolution |
|---|---|---|
| Superlatives / drama in titles | no-sensationalizing, claim bar | our rule wins — drop the adjective |
| Attribution stated flat | `[BEST-CASE]` + verify for value-swing calls | our rule wins |
| Anything about a mailing label / PII | redaction + disclosure | our rule wins |

## Provenance

Measured 245 active listings via the Browse API. Numbers and
their derivation: [`_studies/patinaelements.md`](_studies/patinaelements.md), raw sample
`_studies/patinaelements.json`. Re-run `python lib/seller_style.py sample patinaelements`
then `study patinaelements --guide` to refresh when their style drifts.
