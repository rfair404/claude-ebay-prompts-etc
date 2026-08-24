# <seller> — seller style guide

<!--
`lib/seller_style.py study <seller> --guide` scaffolds this file with the
measured numbers already in place. Then: cut anything the numbers do not
support, add what only a human glance can see, flip status to active, and add a
row to README.md's registry. Delete these comments.
-->

```yaml
seller: <ebay username>
slug: <kebab-slug>
version: 1
status: draft            # draft | active
default: off             # style guides are OFF unless a run turns one on
sample: <n> active listings (<m> with full detail)
source: eBay Browse API (active listings)
study: _studies/<slug>.md
```

**Study, not copy.** Technique only — slot order, budgets, voice, photo
conventions. No title string, sentence, or photo of theirs is reused or
paraphrased into ours. **House rules win** (see
[`README.md`](README.md#house-rules-win)): the honesty bar, no-sensationalizing
wear, PII redaction, and the maker-attribution discipline override anything
here.

## Turn it on

Per run: "use the <slug> style guide". Per batch: `style_guide: <slug>`.
Off by default.

## DRAFT — titles

Length target and cap pressure; lead token; slot order (era / maker / object /
material / measurement); descriptor budget; casing and separators. Every line
should trace to a number in the study artifact.

## DRAFT — description voice

Body length, paragraph count, sentence length; voice (first person / shop /
neutral catalog); the **body skeleton** — which labelled sections appear and in
what order; how condition is raised. Note where our phrasing rules override.

## PREP — photography

Photo count target; backdrop (lightness, neutrality, uniformity); framing and
subject fill; colour cast and contrast. Anything not measurable from the images
goes here only after a human glance at the storefront grid — leave a line blank
rather than guessing.

## Conflicts

| Their pattern | Our rule | Resolution |
|---|---|---|
| | | our rule wins |

## Provenance

Sample size, date, and a pointer to `_studies/<slug>.md` + `.json`, plus the
exact commands to re-derive.
