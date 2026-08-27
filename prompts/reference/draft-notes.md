# DRAFT — rationale, history, and evidence

Companion to [prompts/draft.md](../draft.md). The prompt holds the rules;
this file holds *why* they exist, plus the superseded procedures kept for
one-off use. Read this only when a rule is disputed or being changed — never
as part of a routine DRAFT run.

## The house pattern: where the numbers come from

Title pattern and body skeleton were measured across 245 active listings of
a studied seller ([study](../../styleguides/_studies/patinaelements.md)) and
adopted as house style 2026-08-24, claim bar intact. The patinaelements
guide stopped being an overlay at that point; the overlay mechanism stays
for the *next* seller we study. The evidence behind each title rule:

- **Era leads:** their mean era slot is 0.4, and 97% of titles carry an era
  word; material sits mid-title around slot 6.
- **Fill the field:** their median title is 78 characters and 89% run ≥75.
- **~2 descriptors:** their mean is 1.8, max seen 4.
- **Measurement earns a slot:** 56% of their antiques titles carry one, 37%
  of glass, 0% of clothing — size is a buying decision in some categories
  and noise in others.
- **Condition/scarcity lead:** their mean condition slot is 0.2 — when a
  word is earned, it goes first.

Body skeleton: the spine appears on 100% of their bodies; the
look-at-this line on about half; the "Please see the photos" close on 100%;
first person on 100%. Their median body is 133 words across 245 listings —
ours carries the same spine plus the opener and close, hence the 130–180
target.

## Net-floor check: the two lots that paid for it

Two magazine lots went live with auto-decline floors set from the
Recommended tier under the old 13% fee assumption. At those floors an
accepted offer netted **$13.46 and $9.67** on heavy Ground Advantage
parcels; both were raised at the REPORT phase (2026-08-15). The failure mode
is specific — a low floor looks generous and costs nothing until someone
takes it. Postage weighs most on cheap heavy things, so the check bites
hardest exactly where the old rule was loosest: sub-$50 lots of paper.

## Carrier: why there is no choice

The FedEx-vs-USPS carrier-check gate that used to live in the shipping
section was removed 2026-08-25: the FedEx policy it named (`292460878014`,
"FedEx SmartPost / Ground Economy") had been deleted from the eBay account
and returned 404, so following the gate produced an offer that could not be
published. Every item now ships on the single fulfillment policy; the
account's Media Mail and local-pickup-only policies were deleted the same
week. See docs/top-rated-plus.md.

## The old hand-chained photo sequence (superseded — kept for one-off use)

The chain was `strip_exif` → `even_background` → `trim_whitespace` →
`center_crop`, each writing a subdirectory. **Do not run it as a chain any
more.** Four subdirectories that all look plausible, plus a lexicographic
photo picker at the end, is where 66 sideways photos hid until buyers
complained. PREP replaces the sequence with one output directory and a
manifest recording what was done to each frame. The individual tools still
work for a quick one-off, and `orient.py`'s manifest is read *and written*
by PREP, so a rotation recorded in either is the call in both. What must not
happen is running both on one shoot and then guessing which directory the
draft points at.

> ⛔ **The orientation test is CONTENT, not metadata.** "Would a buyer see
> this the right way up?" cannot be answered from EXIF: a phone held at an
> angle, a book laid on its side, a box shot end-on all produce files that
> are correct by their EXIF and wrong on the page. Buyers complained about
> exactly this, and it was declared fixed twice on the strength of the
> metadata alone. Never call photos good without looking at them, and never
> at thumbnail size — a whole batch was passed off small tiles with every
> frame still rotated.

The steps, when a one-off genuinely needs them:

1. **EXIF / orientation** — bakes any real Orientation tag into the pixels
   (`exif_transpose`), then strips metadata.
       python -m lib.photo_prep.strip_exif <shoot-dir>            # -> no-exif/
       python -m lib.photo_prep.strip_exif <shoot-dir> --force    # stale outputs are
                                                                  # newer than sources
                                                                  # and skipped otherwise
1b. **Orientation review — a HUMAN rules on it.** Build a numbered contact
   sheet of the SHIPPED files (opened with NO transpose), numbers matching
   `photos:` order. Any frame not obviously right: render all four rotations
   side by side, pick by eye (text baseline first, then how the object
   naturally sits). Record the call — never rotate a shipped file in place:
       python -m lib.photo_prep.orient <shoot-dir> --set "1=180,2=ccw,3=cw"
   It writes `orientation.json` (degrees CW) and REBUILDS `no-exif/` from
   source every time, so a wrong call cannot stack and a later strip_exif
   cannot silently revert a human's decision. Show the user the sheet and
   get their ruling BEFORE anything publishes. On a flat lay with objects at
   odds, no rotation makes everything upright: pick the hero and SAY which
   secondary item stays inverted — a re-shoot issue, not a rotation one.
2. **Backdrop cleanup — only near-white/seamless backdrops** (NOT dark
   felt; the corner-sampling is tuned for light grounds).
       python -m lib.photo_prep.even_background <dir>
       python -m lib.photo_prep.trim_whitespace <dir>             # -> trimmed/
3. **Center-crop.** Centers the item at an eBay-friendly aspect (square 1:1
   default), finds the subject by background-contrast, unions ALL
   foreground pieces so a pair/set stays whole.
       python -m lib.photo_prep.center_crop <dir> --check         # verdict + reason
       python -m lib.photo_prep.center_crop <dir> --apply         # in place (backs up
                                                                  # to .orig/)
   Tunables: `--aspect 4:5`/`orig`, `--pad 0.12`, `--threshold` (default
   6%). Writes `crop_review.jpg`. The tool REFUSES a crop it can't make
   safely and prints `SKIPPED <reason>` — expect many skips on macro-heavy
   shoots; that's the tool working. `--force` crops anyway; don't,
   unattended.

Chaining: each tool reads a dir and writes a subdir — point the next step at
the previous output.
