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

## The review is STAGED, and interactive. Always.

Three corrections, in this order, and **you do not move on until the operator
says the current one is right**:

    1. ORIENTATION  ->  2. CROP  ->  3. COLOUR / touch-up

This ordering is not a preference, it is a dependency. A crop is only meaningful
once the frame is the right way up. A colour judgement is only meaningful on the
framing that will actually ship. Shown together, a bad crop and a bad rotation
look the same on the page and neither can be answered.

Each stage shows **a card per photo with a thumbnail for every option at that
stage, side by side**, current choice ruled green. The operator is picking from
pictures, never reading a description of a picture.

**Present it as the Frame Check page, every time — orientation, crop AND
colour.** This is the settled format. Do not hand over a JPEG contact sheet, do
not describe the frames in prose, and do not invent a different layout for the
crop or colour stage: all three use the same page, the same card, the same
controls.

    python tools/prep_sheet_html.py <shoot>      # -> <shoot>/.prep/review.html

then publish that file as an artifact and give the user the link. Republish the
SAME file path after every change so the link never moves.

What the page must have, on every stage:

| | |
|---|---|
| **A card per frame** | one picture per card, big enough to judge |
| **Every option side by side** | as clickable thumbnails, current one ruled green |
| **An override on every card** | including frames the pipeline refused — a card you cannot argue with is not a review. A refused crop still offers *force a crop* |
| **A free-text box on every card** | the options only cover the overrides we thought of; "the smokestack is clipped" has to have somewhere to go. Typed notes ride along with the command |
| **An Accept button per stage** | says plainly that it sends the stage as shown, with any changes and notes attached |
| **Click any picture to open it full size** | the thumbnail is enough to make the call, the detail is what the call rests on |
| **The exact command, copyable** | the page cannot reach the CLI; a fake Apply button would be worse than admitting that |

**The page must work with JavaScript switched off.** This is the rule that cost
the most to learn. Selection is native radio inputs, the picture shown is a CSS
`:has()` rule, the tabs are a radio group, the full-size preview is `:target`.
Script is layered on top for one job — assembling the command — and the page is
fully usable when it never runs. Two versions built the DOM in JS and routed
every click through a handler; both rendered perfectly and neither responded to
a single click in the viewer the operator actually uses. Script-dependent UI
fails as a page that looks finished and does nothing.

Rules that follow from getting it wrong, in order of how much they cost:

1. **No inline `onclick`, and no JS-built DOM.** Render the markup from Python.
2. **Never assume a key event landed on an element** — it can land on the
   document, which has no `closest()`, and the handler dies silently.
3. **Never render a lone option as a button.** One option is not a choice; it
   reads as broken. Give it a real alternative or no button row at all.
4. **Write each image's bytes once**, as a CSS custom property on the card, and
   paint the thumbnail, the option chip and the full-size preview from it. Three
   copies took a fourteen-frame shoot to 15 MB against a 16 MB ceiling.
5. **Anything that looks clickable must be clickable**; anything that is not
   gets no affordance.

The JPEG builders still exist (`--stage NAME` writes `.prep/stage_N_*.jpg`) and
are the fallback when no page can be published.

## The looks

Both render every time; the operator picks. They differ only in how hard they
push, never in what they may touch.

| Preset | Backdrop | Item |
|---|---|---|
| `half` | studio at half strength — every move halved | half the pop and sharpen |
| `studio` | neutralised to true black/white, fuzz blurred | sharpened |
| `punch` | same | stronger contrast and colour |

`half` is not a fourth set of numbers to keep in step. It is studio with `k=0.5`,
the same multiplier the rail guard already backs off with, so it halves the
white-balance gain, the backdrop curve, the neutralise, the blur, the pop and
the sharpen together. Reach for it when a look reads washed out: the wash comes
from the correction, so less correction is less wash.

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
