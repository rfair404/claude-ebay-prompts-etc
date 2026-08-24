# Style guides — how another seller lists, distilled into technique

A **style guide** is a study of one public seller: how they build titles, how
their description bodies are structured and voiced, how they shoot. It is an
**optional overlay** that DRAFT (titles + voice) and PREP (photo conventions)
can load when a run asks for it.

Style guides are the mirror image of [`../specializations/`](../specializations/):
a specialization says *what an item is* (expert knowledge, always on when the
triggers match), a style guide says *how we present it* (presentation, off
unless asked for).

---

## Study, not copy

This is emulation of technique, the way you study a writer's structure without
lifting their sentences. The bright line:

- **Extracted:** measured statistics and structural patterns — title length and
  slot order, keyword budget, casing and separators, body section skeleton,
  sentence length, photo counts, backdrop and framing measurements.
- **Never extracted:** their title strings, their description sentences, their
  photos. Nothing of theirs is reused verbatim, paraphrased line-by-line, or
  emitted into one of our listings.

The raw sample under [`_studies/`](_studies/) is a **research artifact** — it
exists so every claim in a guide is traceable and re-derivable when the seller's
style drifts. It is never listing input, and the verbatim `_studies/*.json` is
**git-ignored**: the measured study (`_studies/<slug>.md`) and the guide are
what the repo carries. Re-derive the sample with one command when you need it.

## House rules win

A style guide is a stylistic overlay, never an override. When a guide's pattern
collides with a house rule, the house rule wins, every time:

| House rule | Where |
|---|---|
| The honesty bar — no inventing, `[BEST-CASE]` + verify on value-swing claims | [`../prompts/_shared.md`](../prompts/_shared.md), [`../prompts/identify.md`](../prompts/identify.md) |
| No sensationalizing expected age/wear | `../prompts/draft.md` |
| PII redaction + disclosure | `../prompts/draft.md` |
| Condition-only `conditionDescription`, category/aspect requirements | `../prompts/draft.md` |
| PREP stage contract (orientation → unskew → crop → colour) | [`../prompts/prep.md`](../prompts/prep.md) |

A guide changes *how we say it*, never *what we are willing to claim*.

---

## Registry (the index DRAFT and PREP check)

Guides are **off by default**. A run turns one on explicitly — "use the
patinaelements style guide", or `style_guide: <slug>` in a batch config. If no
guide is named, none is loaded and house defaults apply.

| Guide | Seller | Sample | What it is good for | Status |
|---|---|---|---|---|
| [patinaelements.md](patinaelements.md) | patinaelements | 245 active listings (all with full detail, 60 images measured) | General estate/antiques + jewelry: cap-length titles, a rigid SIZE → CONDITION → MAKER'S MARK body skeleton, ~10 photos | **adopted as house style 2026-08-24** |

_Add a row per guide. `default: off` is not negotiable — a style guide is only
ever loaded because a run asked for it._

**Adopted** is the end state a guide can earn: the technique stops being an
overlay and becomes a house rule in `prompts/`. That is a deliberate, one-time
promotion, not a default — it happened for patinaelements on 2026-08-24 after
the pattern held across every category sampled. The guide file stays as the
traceable source of the numbers, and what was left behind is recorded in it.

---

## Building a new one

```bash
python lib/seller_style.py sample <sellername> --details 60
python lib/seller_style.py study <sellername> --guide --images 40
```

1. **Sample** pulls the seller's ACTIVE listings via the Browse API
   ([`../lib/ebay_browse.py`](../lib/ebay_browse.py)) — a union of single-category
   calls, since Browse takes one category per call and rejects a bare seller
   filter — then fetches per-item detail (description, photo count, aspects) for
   a category-stratified subsample. Browse is the bulk collector, always. If it
   comes back thin, **say so** rather than escalating to a scrape.
2. **Study** measures the sample into [`_studies/<slug>.md`](_studies/) and
   scaffolds `<slug>.md` with the numbers filled in.
3. Read the scaffold, cut anything the numbers do not support, add what only a
   human glance at the storefront grid can see (props, shot order, hero choice).
   Claude-in-Chrome is a *quick glance* only — never the bulk collector.
4. Flip `status: active` and add a row to the registry above.

Refresh a guide by re-running both commands (`--force` overwrites the guide, so
re-apply any hand edits). Sellers drift; the study artifact is what tells you
by how much.

## Module schema

See [`_template.md`](_template.md). Required: the front-matter block
(`seller`, `slug`, `version`, `status`, `default: off`, `sample`, `source`,
`study`), the study-not-copy notice, a **DRAFT — titles** section, a
**DRAFT — description voice** section, a **PREP — photography** section, a
**Conflicts** table, and **Provenance** pointing at the study artifact.
