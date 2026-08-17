# PREP — v3, Function 2.5 (photo preparation)

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/listing/` (the ONLY directory DRAFT reads),
`<shoot-dir>/.prep/prep.json` (what was done + the approval stamp),
`<shoot-dir>/.prep/prep_presets.jpg`, `<shoot-dir>/.prep/prep_review.jpg`.

Turn a shoot's raw frames into listing-ready photos: upright, cropped to the
item, backdrop softened, foreground sharpened. Runs **after IDENTIFY, before
INVESTIGATE/DRAFT**.

INVESTIGATE keeps reading the **originals** — condition evidence must never come
from a processed file. Only DRAFT reads `listing/`.

---

## The one command

    python -m lib.photo_prep.prep <shoot-dir> --check     # analyse; render nothing
    python -m lib.photo_prep.prep <shoot-dir> --rotate NAME=DEG …   # record a look
    python -m lib.photo_prep.prep <shoot-dir> --apply     # render both looks, adopt the default
    python -m lib.photo_prep.prep <shoot-dir> --pick punch|studio   # override the default
    python -m lib.photo_prep.prep <shoot-dir> --repoint-draft --apply-repoint
    python -m lib.photo_prep.prep <shoot-dir> --approve   # HARD gate — explicit user yes ONLY

This **replaces** the old hand-chained sequence (`strip_exif` → `even_background`
→ `trim_whitespace` → `center_crop`). Do not run those as a chain any more; see
"What replaced what" below.

---

## Procedure

1. **`--check`.** Reads EXIF, segments each frame, runs page-orientation
   detection on the cropped subject, plans the crop and the colour move. Writes
   the manifest, `rotation_sheet.jpg`, and a four-way panel per unresolved frame.

2. **Read `rotation_sheet.jpg` yourself, at full size.** This is not optional and
   it is not delegable to metadata. The test is **"would a buyer see this the
   right way up?"** — a phone held at an angle, a book on its side and a box shot
   end-on all produce files that are perfect by their EXIF and wrong on the page.
   For anything doubtful, open its panel in `.prep/ask/` and pick from the four
   rotations rather than describing the problem.

3. **Record every unresolved frame** with `--rotate NAME=DEG`, where DEG is
   relative to what the sheet just showed you. **`0` is a real answer** —
   "looked at it, it is upright" — and it is what clears the frame. Frames left
   unresolved block approval, by design.

4. **`--apply`.** Renders every frame through every preset and adopts the
   backdrop's default (see below). Writes `prep_presets.jpg` (original beside
   each look) and `prep_review.jpg` (before | after for the adopted look).

5. **Show the user `prep_review.jpg` and STOP.** This is a HARD gate. Surface the
   photo count, the crop count, and anything the report flags. On their explicit
   approval, run `--approve`. Nothing else counts as approval — not "ok", not
   "looks good", not silence.

6. **`--repoint-draft`** before DRAFT renders `photos:`, so the draft points at
   `listing/`. It maps each existing entry to its own prepped counterpart and
   **preserves order** — entry one is the eBay gallery image, and a draft's list
   is often not lexicographic. Dry run unless `--apply-repoint`.

---

## The looks

Both render every time; the operator picks. They differ only in how hard they
push, never in what they may touch.

| Preset | Backdrop | Item |
|---|---|---|
| `studio` | neutralised to true black/white, fuzz blurred | sharpened |
| `punch` | same | stronger contrast and colour |

**Default: `punch` on a dark or navy cloth, `studio` on a light sweep.** A
deepened backdrop gives the item something to separate against, so the extra
push pays off; on a white sweep the same push has nothing to separate from and
reads as over-processed. `--pick` overrides. The default decides what is SHOWN
at the gate, never what publishes.

---

## What it will not do

The line is between **the studio** and **the goods**. Re-toning, neutralising
and blurring the backdrop are fair game. On the item itself: white balance,
exposure, contrast and sharpening only.

**No denoise, no smoothing, no blemish removal, ever.** Those are what soften
scratches and even out tarnish, and a listing photo that disagrees with its own
condition disclosure is worse than a flat one. Sharpening earns its place by
cutting the other way — it makes fine wear *more* legible.

Automatic refusals, all reported per frame on the sheet:

- **No studio backdrop** → no crop, no backdrop move. A macro of a maker's mark
  or a serial stamp has the item's own surface behind it; lifting aged tan paper
  toward white is cosmetically nicer and a misrepresentation.
- **A large object in the backdrop** → protected from neutralising and blurring.
  A ruler laid alongside for scale falls outside the subject mask and is *not*
  cloth; blurring it destroys measurement evidence.
- **Mask failure** (subject under 2% of frame) → every backdrop operation off.
- **Detectors disagree** on where the item is → no crop.
- **The item would be cut**, or the crop lands under 1400px → no crop.

The colour pass also measures its own output: no correction may push an item
pixel to pure black or white that was not already there.

---

## Orientation: what is trusted

Two independent rotations, composed:

- **Camera** — the EXIF Orientation tag. Objective, always applied.
- **Subject** — how the item was laid in the frame. **No metadata knows this.**

The subject half comes from page-orientation detection (objective, but only when
**corroborated by another frame in the same shoot** — measured false positives
include a textless macro read as text at higher confidence than a real magazine
cover), or from a recorded look, or it goes to ASK.

Nothing infers "probably upright" from an aspect ratio. A round item with no
defined upright is resolved the same way as everything else: someone looks once
and confirms `0`.

Recorded rotations mirror into `<shoot>/orientation.json`, the same file
[`lib/photo_prep/orient.py`](../lib/photo_prep/orient.py) writes, so a call made
in either tool is the call in both.

---

## What replaced what

| Old step | Now |
|---|---|
| `strip_exif` | PREP bakes EXIF internally; `no-exif/` is legacy output |
| `even_background`, `trim_whitespace` | PREP's backdrop pass (mask-driven, works on dark felt too) |
| `center_crop` | PREP's crop (its safety guards are reused, not reimplemented) |
| `orient.py --set` | still works; PREP reads AND writes its manifest |
| DRAFT step 1/1b/2/3 | this prompt |

`strip_exif` and `orient.py` remain useful on their own for a quick one-off.
What must not happen is running the old chain *and* PREP on one shoot and then
guessing which directory the draft points at — that ambiguity is what the single
`listing/` output and the code-level gate exist to remove.

---

## Gate contract

PREP is a **HARD gate** and stops the run, including headless, until the user
approves. It is enforced in code as well as here: `upload_photos_to_eps` refuses
photos that are not prepped and approved, so an unapproved shoot cannot reach
eBay even if this prompt is ignored. Approval goes stale automatically if a
source or an output changes — what was approved must be what is uploaded.

Flip it to soft only when the user says so.
