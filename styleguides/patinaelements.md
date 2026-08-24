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

This guide is no longer an overlay to switch on. Everything below except the
claim words was folded into the house rules: the title pattern in
[`../prompts/draft.md`](../prompts/draft.md#the-house-title-pattern), the body
skeleton (Hook / What's Included / **Size** / Condition / **Markings** / About)
in draft.md and [`../templates/listing-v1.md`](../templates/listing-v1.md), and
the frame-count target in [`../prompts/prep.md`](../prompts/prep.md).

**Not adopted, deliberately:** their all-caps bodies, first-person shop voice,
boilerplate (cross-sell line, payment terms), and condition/scarcity words in
the lead title slot. Those are identity or claim-bar matters, not technique.

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
