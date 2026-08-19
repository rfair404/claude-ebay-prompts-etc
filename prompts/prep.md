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

**Present it as the interactive console, every time.** Build the page and give
the user the link — do not hand over a JPEG contact sheet and do not describe
the frames in prose:

    python tools/prep_sheet_html.py <shoot>      # -> <shoot>/.prep/review.html

then publish that file as an artifact and link it. One page, three tabs in
order, a card per frame; clicking an option marks it changed and the bar at the
bottom writes the exact override command to paste back. Republish the SAME file
path after every change so the link never moves.

Why not the sheet: a fourteen-frame shoot renders a 4,000px-tall JPEG, and a
picture you scroll past in a viewer is not a surface anyone can decide on. The
JPEG builders still exist (`--stage NAME` writes `.prep/stage_N_*.jpg`) and are
the fallback when a page cannot be published.

Two things that page must never do, both learned by getting them wrong:
**never wire a control with an inline `onclick`** — the script does not reliably
run in global scope, so the page renders perfectly and every button silently
does nothing; use `addEventListener` and delegation. And **never render a lone
option as a button** — a frame that refuses a crop has nothing to choose
between, so it gets a reason and no button row at all.

    python -m lib.photo_prep.prep <shoot> --check          # plan everything, render nothing

    # --- stage 1 ------------------------------------------------------------
    python -m lib.photo_prep.prep <shoot> --stage orientation
    #   -> .prep/stage_1_orientation.jpg : every frame at +0/+90/+180/+270
    python -m lib.photo_prep.prep <shoot> --rotate NAME=DEG …    # fix what is wrong
    python -m lib.photo_prep.prep <shoot> --approve-stage orientation

    # --- stage 2 ------------------------------------------------------------
    python -m lib.photo_prep.prep <shoot> --stage crop
    #   -> .prep/stage_2_crop.jpg : upright frame with the box drawn, and the result
    python -m lib.photo_prep.prep <shoot> --crop NAME=off|on|pad0.20
    python -m lib.photo_prep.prep <shoot> --approve-stage crop

    # --- stage 3 ------------------------------------------------------------
    python -m lib.photo_prep.prep <shoot> --apply          # render every look
    python -m lib.photo_prep.prep <shoot> --stage color
    #   -> .prep/stage_3_color.jpg : final framing, then each look, adopted one green
    python -m lib.photo_prep.prep <shoot> --pick studio|punch
    python -m lib.photo_prep.prep <shoot> --approve-stage color

    # --- then, and only then -------------------------------------------------
    python -m lib.photo_prep.prep <shoot> --approve        # the publish gate
    python -m lib.photo_prep.prep <shoot> --repoint-draft --apply-repoint

**Enforced, not merely described.** A stage refuses to open until the one before
it is approved. Approving a stage clears the sign-off on every later stage, so
revisiting orientation cannot leave a stale crop approval standing. `--apply`
refuses to write `listing/` until all three are approved, and the publish gate
sits on top of that.

**How to run it with a human.** Show the stage sheet. Say what is proposed and
what you are unsure about. Wait. Apply their corrections, rebuild the sheet, show
it again. Repeat until they say it is right — then approve that stage and move
on. Do not batch the three stages into one question, and do not approve a stage
on their behalf because it "looks fine".

This replaces the old single-sheet flow. It exists because judging all three at
once is what let sideways photos through: the reviewer sees a busy sheet, the
crop looks plausible, and the rotation is never actually examined.

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
