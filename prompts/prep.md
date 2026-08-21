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

## The run OPENS with a best attempt, made without asking

    python -m lib.photo_prep.prep <shoot> --auto

One pass, no questions: every frame turned the way PREP reads it, every crop
planned, nothing approved and nothing rendered. Then the operator looks ONCE at
what it did and either takes it or opens the stages.

Why it is allowed to guess. The staged review below is the point of PREP, and it
also spends the operator's attention on frames where the answer was never in
doubt. Deciding first and asking second costs nothing as long as two things hold,
and they are enforced in code:

- **A guess is labelled a guess.** A frame the resolver cannot read would
  normally become an ASK and stop the run. Here it takes the best signal it has
  (the OSD proposal, else 0) and records `guessed: true`. Report those frames by
  name, every time — they are exactly the ones worth a human look, and a guess
  presented as a resolution is the one way this pass can hurt.
- **The crop is deliberately loose.** `DEFAULT_PAD` is 0.28 of the item's own
  box, and `MIN_FRAME_KEPT` (0.55) is a floor under the whole box: a crop trims
  the edges and always leaves backdrop around the item. Too generous costs a
  second look. Too tight has already thrown pixels away, and nobody re-shoots.

Present the result as a widget: every frame, the turn applied, the crop box on
the frame, and the guessed ones flagged. Apply the pass, then show ONE card
(`python tools/prep_card.py <shoot>`) of the revised frames, and ask a single
question with two answers:

- **approve** → `--approve-auto`, which signs off orientation AND crop together
  and moves to colour;
- **override** → open the interactive flow below, at `--stage orientation`.

Nothing about the gate changes. `--auto` approves nothing, `listing/` is still
written only after the stages are signed off, and `--approve-auto` stamps the
same per-stage digest a sheet approval does — so any later edit invalidates it
the same way.

## The review is STAGED, and interactive. Always.

This is where an override lands, and where a shoot goes whenever the auto pass
is not good enough. Three corrections, in this order, and **you do not move on
until the operator says the current one is right**:

    1. ORIENTATION  ->  2. CROP  ->  3. COLOUR / touch-up

This ordering is not a preference, it is a dependency. A crop is only meaningful
once the frame is the right way up. A colour judgement is only meaningful on the
final framing. Shown together, a bad crop and a bad rotation look the same on
the page and neither can be answered.

**There used to be an UNSKEW stage between orientation and crop**, warping a
rectangular item so its edges met the picture's. It was removed in 2026-08 on
the operator's call: it cost a quad fit and a full-frame resample on every
frame, it damaged more photos than it saved — a quad landing on a mat, a mount
or a soft shadow squares up the wrong rectangle — and two degrees of tilt is not
something a buyer sees in a thumbnail. Nothing plans one now. A shoot that was
squared before it went still REPLAYS its recorded warp, so re-running PREP on a
live shoot returns the pixels a buyer is already looking at.

## Orientation is DECIDED, not asked — above 95% confidence

Orientation is the one stage where the answer is usually in the picture, and it
is not a good use of the operator's attention. So the model decides it:

    python tools/prep_orient_review.py <shoot>   # -> .prep/orient_review_N.jpg

A row per frame, all four turns side by side, big enough to read printed body
copy, current call ruled green, with the OSD reading printed for reference.
**Read it, decide, apply with `--set-rotate`, and only surface the frames that
fall below the bar.** Do not build a clickable picker for this stage and do not
ask the operator to rule on frames you can read.

**What clears 95%** — a positive, legible signal:

- printed text whose baseline you can read (a masthead, body copy, a caption);
- a human figure, which in a fashion or catalog frame is the strongest cue there
  is: head up, garment hanging down;
- an object with an unambiguous upright (a locomotive on its wheels);
- **and** agreement with the rest of the shoot where the frames are alike — as a
  *prompt to look again*, never as the answer. This used to read "six spreads
  photographed in one session cannot correctly differ by 90°", and that is
  false: on `paul-fredrick` the cover was shot portrait-wise and the spreads
  were laid on the bedspread turned 90°, so the subject half genuinely differed
  within one session. The camera half is shared across a session; the way the
  item was laid down is not. Treating disagreement as proof of error is what
  made a correct OSD reading look wrong (see `docs/osd-audit-2026-08-21.md`).

**What does NOT clear it**, and goes to the operator with the sheet:

- "it looks more natural this way" with nothing legible to point at;
- a frame whose two halves disagree — a masthead upright on one page and a model
  upright on the other. Say which cue you followed and why;
- a round or flat item with no defined upright;
- anything resting on OSD alone. **OSD is reference, not evidence.** Measured
  across 73 frames where a human look was recorded beside the reading, OSD
  agreed with the operator **49%** of the time at the old confidence floor —
  a coin toss. The floor is now 4.0, where it agrees 84% of the time and
  answers 42% of frames; below that it reports no answer and the frame becomes
  an ASK. Regenerate the numbers with `python tools/osd_audit.py --bands`.

  Note what this does NOT license: a reading that clears 4.0 is still
  reference. 84% is not 100%, and the one frame that shipped sideways on
  `paul-fredrick` read confidence 12.29 — well clear of any bar — on a script
  confidence of 0.6. High orientation confidence with weak script confidence
  means tesseract is confident about marks it does not recognise as language.

State the confidence per frame when you report, and name the cue. A number
without a cue is not a judgement.

**Orientation is first, and that means nothing else is measured yet.**
`--check` reads EXIF, segments, runs the text detector and resolves which way is
up — and stops there. It does NOT plan the crop or the colour reading, because
each of those describes the geometry it was computed on: measure them against a
rotation nobody has confirmed, and a later turn silently invalidates both. Six catalog spreads shipped exactly that way, with crops
planned at 0° while the manifest ended up saying 270°.

`--approve-stage orientation` then runs `plan_geometry()` itself, against the
rotations just approved, so the operator never has to remember a second command.
Planning refuses outright while orientation is unapproved. It costs one extra
decode per frame; it buys the guarantee that no downstream number was ever
computed against a rotation that later changed.

Each stage shows **a card per photo with a thumbnail for every option at that
stage, side by side**, current choice ruled green. The operator is picking from
pictures, never reading a description of a picture.

**Present it as the Frame Check page, every time — orientation, crop AND
colour.** This is the settled format. Do not hand over a JPEG contact sheet, do
not describe the frames in prose, and do not invent a different layout for any
stage: all three use the same page, the same card, the same controls.

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
0. **Nothing may depend on a modern selector, a URL or a script.** Three
   mechanisms have been tried and abandoned, each of which worked perfectly in a
   normal tab and was dead in the frame the operator reads these pages in: a
   JS-built DOM (script never ran), `:target` for the previews and the Accept
   panel (a URL fragment never lands in a sandboxed frame), and `:has()` for
   selection, tabs and chips (a 2022 selector — where it is missing the page
   draws perfectly and answers nothing). Every piece of state is a native input
   at the TOP of its container and everything it drives is a following sibling
   reached by `~`, which has worked since CSS2. A control whose input is nested
   inside the thing it styles cannot work — that is how the Accept button
   shipped inert. Held by `tests/test_prep_sheet_html.py`.
6. **Never hand-list the options in CSS.** The rule that decides which picture a
   card shows matches on the option's **index**, generated up to `MAX_OPTS` — not
   on its value. An earlier version spelled the values out (`"0"`, `"90"`, `"on"`,
   `"off"`, `"studio"`, `"punch"`), so the day the colour stage grew `half`,
   `tenth` and `crisp`, picking any of the three matched no rule: the card went
   blank and the full-size preview opened empty, on the stage the operator uses
   most. A stage must be able to add an option without anyone remembering to edit
   a stylesheet. Held by `tests/test_prep_sheet_html.py`, which fails if the rules
   go back to being hand-listed or a stage outgrows the generated range.

**Verify the page in a browser before handing it over** — every option on every
card, checking that the picture changes in BOTH the card and the full-size
preview. Both bugs above rendered perfectly and did nothing; neither is visible
in the markup, and neither would have shipped if one pass had actually clicked
through the options. `tests/test_prep_sheet_html.py` covers the generator; the
click-through covers the page.

The JPEG builders still exist (`--stage NAME` writes `.prep/stage_N_*.jpg`) and
are the fallback when no page can be published.

## SHOWING A MODIFIED IMAGE — the locked template

Every time PREP changes a picture, the operator sees it this way. No
exceptions, no per-stage improvisation, and never a prose description in place
of the picture.

**The rule: never show a result without what it came from.** A cropped frame
alone is unreviewable — the question is not "is this a good picture", it is
"was the right thing removed". The same holds for a rotation and a colour pass.

    BEFORE            →   AFTER              →   why, in the operator's words
    (what it was)         (what will ship)       ("would cut 10% off the page")

| Stage | Before | After | Options offered |
|---|---|---|---|
| orientation | the frame as the camera gave it | at the chosen turn | all four turns |
| crop | the full frame | the crop result | cropped / as shot / force a crop |
| colour | as shot | each rendered look | as shot + every look |

Requirements, all of them load-bearing:

1. **A card per frame**, one picture per card, big enough to judge.
2. **Every option side by side as a thumbnail**, current choice ruled green.
3. **Click any picture to open it full size**; arrow keys step frame to frame.
4. **An override on every card**, including frames the pipeline refused. A card
   you cannot argue with is not a review. A refused crop still offers *force a
   crop*, and says it cannot be previewed.
5. **A free-text box on every card.** The options only cover the overrides we
   thought of; "the smokestack is clipped" needs somewhere to go.
6. **The reason, per frame, in words** — "no studio backdrop (luma 192)", "would
   cut 10% off the subject". A refusal without a reason reads as a failure.
7. **An Accept button per stage**, stating that it sends the stage as shown.
8. **The exact command, copyable**, and generated in an IDEMPOTENT form
   (`--set-rotate`, not `--rotate`) — see the rules below.

Build it with:

    python tools/prep_sheet_html.py <shoot>      # -> <shoot>/.prep/review.html

publish that file as an artifact, link it, and republish the SAME path after
every change so the link never moves. Pair it with a one-click accept in chat
so the operator never copies a command by hand.

**The page must work with JavaScript switched off.** Selection is native radio
inputs, the shown picture is a CSS `:has()` rule, tabs are a radio group, the
preview is `:target`. Script does one job — assembling the command — and the
page is fully usable when it never runs. Two versions built the DOM in JS and
routed every click through a handler; both rendered perfectly and neither
responded to a single click in the viewer the operator actually uses.

Rules learned by breaking them, in order of what they cost:

1. **No inline `onclick`, and no JS-built DOM.** Render the markup from Python.
2. **A generated command must be idempotent.** `--rotate` is relative to what
   the sheet shows; a page that emits it as if it were absolute moves the frame
   again on every paste. Use `--set-rotate`.
3. **Never assume a key event landed on an element** — it can land on the
   document, which has no `closest()`, and the handler dies silently.
4. **Never render a lone option as a button.** One option is not a choice.
5. **Write each image's bytes once**, as a CSS custom property on the card.
   Three copies took a fourteen-frame shoot to 15 MB against a 16 MB ceiling.
6. **Anything that looks clickable must be clickable**; anything that is not
   gets no affordance.

The JPEG builders (`--stage NAME`) remain as the fallback when no page can be
published.

---

## What the audit found, and what it changed

An audit of 819 already-published frames (`tools/prep_saturation_audit.py`, then
`tools/prep_saturation_verify.py` against the subject mask) found item colour
destroyed on **14 frames across 10 shoots**, 9 of them live. Worth keeping in
mind because every one had the same shape:

- the correction was behaving **correctly on a wrong premise**. Segmentation
  handed part of the item to the backdrop, and the backdrop pass neutralised it,
  which is exactly what it is told to do to cloth;
- the tell is **mask coverage**: 43–70% on flat printed catalog covers, 5–9% on
  thin silver on a light ground. Those two subjects are the weakness;
- the first-pass sweep flagged 39 shoots, but **54 of 76 frames were the
  correction working** — a backdrop cast being removed. Measure inside the mask
  before calling anything damage.

Mitigations now in the code: `_protect_objects` tests chroma as well as luma
(`CHROMA_OBJECT_MIN`, measured — cloth reaches 24, paint starts at 54), `crisp`
is the default for new items, and `asshot` exists for when a shoot's mask cannot
be trusted at all.

**Open defect — OSD can be confidently wrong.** On paul-fredrick the detector
read subject 270 on five frames at confidence up to 12.2 with recognised script,
all corroborating each other, and all wrong: the pages ship at 270 applied, not
0. The one frame it could not read is the one the operator kept correcting by
hand, and they were right every time. "High confidence, corroborated" is exactly
the state in which a headless run would ship these unreviewed. Do not trust OSD
alone on flat printed media; the orientation gate is what catches it.

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

### Printed media renders `asshot`. No exceptions from the default path.

**Books, magazines, catalogs, mailers — any shoot whose subject is printed paper
— default to `asshot` (k=0), not to `studio`.**

The correction cannot be trusted on this class, and the reason is structural
rather than a tuning miss. A catalog is photographed open on a light sweep, so
the printed page IS a large light field with ink on it: the segmenter hands that
page to the backdrop, and the backdrop pass then does exactly what it is told to
do to a backdrop — neutralise it toward paper white. White balance has nothing
reliable to lock onto either, because the page's own ink is the dominant colour
and glossy stock throws the sweep's cast straight back into the lens.

Measured on `more-mags-444/fall-and-winter-1980`, on renders that were already
live: saturation against source-selected pixels fell 51.8% on the hero, 60.9%
and 61.3% on two interior spreads, and 94.0% on the mailer. The damage scales
linearly with `k` — studio -39.4%, half -19.2%, tenth -3.5%, asshot -0.2% —
which is the signature of the backdrop pass eating the subject, not of a bad
curve. Re-running under the chroma guard did not change those numbers.

`tenth` is the compromise if a genuine cast has to come off. `asshot` is the
default because the failure is silent: a drained catalog spread still looks like
a catalog spread, and nothing in the pipeline flags it.

**Default: `crisp` for any NEW item, whatever the backdrop.** It is the only
look that cannot misrepresent the goods — full-strength backdrop cleanup, the
item's colour left exactly as the camera recorded it. An item ALREADY LIVE keeps
the look it was published under; re-rendering it into a different one silently
changes pictures a buyer may already have seen. Change one on purpose with
`--pick`, never in bulk.

The older backdrop-led defaults still apply to existing shoots:
**`punch` on a dark or navy cloth, `studio` on a light sweep.** A
deepened backdrop gives the item something to separate against, so the extra
push pays off; on a white sweep the same push has nothing to separate from and
reads as over-processed. `--pick` overrides. The default decides what is SHOWN
at the gate, never what publishes.

---

## Categories — say what the goods ARE, once

Most of PREP's flags are not preferences. They are statements about what is in
front of the camera, and they have the same answer every time for a given kind
of goods. `--category` carries the whole set:

```bash
python -m lib.photo_prep.prep <shoot> --category printed --check
```

| category | detector | looks rendered |
|---|---|---|
| `default` | `auto` — both detectors, arbitrated on agreement | all six, for comparison |
| `printed` | `paper` — LAB decides; u2net kept as a second opinion | `asshot` only |

**Why `printed` needs its own detector.** On a catalog the salient object is the
picture PRINTED ON the item, so u2net cuts out the cover model and returns a
mask that is a strict sub-region of the paper. The containment test cannot catch
that — a box wholly inside the paper's box scores 1.0. LAB has no such
confusion: paper against a sweep is the figure/ground split it measures.

**Why it renders one look.** The section above already says printed media ships
`asshot`, always. Rendering the other five produces images nobody opens, at
~25s a frame each. Measured on five 12 MP catalog frames, same decisions and
the same gates: **8m10s under `default` → 45.6s under `printed`, 10.7×**. Per
frame, once u2net is loaded, that is ~94s → ~5.1s.

Two of those seconds came back from `asshot` itself: at `k=0` every term in the
colour correction is multiplied by zero and the result discarded, so the loop is
now skipped outright rather than computed and thrown away (26s → 3.9s a frame).
The pixels and the full colour report are asserted identical to the old path.

`looks` narrows what is RENDERED, never what is picked. The operator still
chooses at the colour stage and `--pick` still overrides. A one-look category
says *the comparison is not live for this kind of item* — not *this look is
approved*.

**The category persists in the manifest**, so a later `--check` or `--apply`
that does not repeat the flag gets the same answer. Changing it drops the
crop/colour sign-offs, because every box downstream of the detector was
measured against the old one. `--subject auto|paper` remains available as the
escape hatch for a shoot its category gets wrong, and outranks it.

Categories live in `lib/photo_prep/categories.py`. Adding one is data, not code
— but keep it grounded: every field should trace to something observed on a real
shoot. A category invented from taste is a policy change wearing a config file,
and it reaches hundreds of frames before anyone notices it was a guess.

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

On printed media, prefer the page-orientation (OSD) read over the vision
estimate when the two disagree. Text is the one thing on a catalog page that has
an unambiguous upright, and the corroboration rule already guards OSD's known
false positive. Measured on `fall-and-winter-1980`: the three frames resolved
`exif+osd` all landed upright, while two of the three resolved `exif+vision`
shipped rotated 90 degrees with the models on their sides and the body text
running vertically — and none of them were flagged ASK, so the pipeline was
confidently wrong rather than uncertain.

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
