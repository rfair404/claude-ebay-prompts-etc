# IDENTIFY — rationale, history, and evidence

Companion to [prompts/identify.md](../identify.md). The prompt holds the
rules; this file holds *why* they exist. Read only when a rule is disputed
or being changed.

## Why the CLIP/forum index is disabled for marbles

CLIP embeds at 224px and matches on colour, not seams — in practice the
forum-index tools returned colour look-alikes rather than maker matches,
and the forum thread titles they surfaced are OP guesses (often wrong;
only high-rep reply answers are trustworthy). Measured against an expert
gold set, the classifier also floats toward generic machine-made calls
(67% of expert handmade/transitional called machine). The tools are kept
but dormant; the maker/type call comes from photos + the specialization's
tells alone. They run only on an explicit user request, outside the
identify record.

## The Lens OCR caveat: how it was verified

`lib/lens_id.py`'s OCR reliably reads printed/painted marks (paper labels,
painted backstamps, ink) and routinely returns "No results" on
low-contrast embossed metal stamps — verified on silver/pewter/buckle/
jewelry pieces. That is why an empty OCR result must never be read as "no
mark": for pressed metal, the step-1 close-read is the authority, and the
tool's verdict says so. Separately, `lens_id.py` can return empty datasets
with NO error when the tmpfiles host serves an HTML interstitial — always
run a known-indexed control image before believing "no matches".

## Why the maker-mark stop-and-ask exists

The user is holding the piece; a close-read of a mark beats any web or
Lens inference and costs nothing. The gate categories (jewelry, precious
metals, glass, ceramics) are the ones where a mark almost always exists
and swings value hard — a missed mark is the most expensive thing to leave
on the table. The exception clause exists because stopping on every plain
drinking glass taxes the operator for no value; the one-line
`exception:` log keeps the skip auditable.

## Why photo intake is capped

Full-res photos are the dominant token cost of a shoot, and near-duplicate
angles add cost without evidence. The 4–6-frame decisive set (hero, mark,
details, scale) resolves a single-item record; the cap never applies to
frames that carry a mark, and wide/group shoots read whatever coverage
enumeration requires.
