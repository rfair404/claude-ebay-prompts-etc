# PREP — v4, Function 2.5 (photo preparation)

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/listing/` (the ONLY directory DRAFT reads),
`<shoot-dir>/.prep/prep.json` (what was done + the approval stamp),
`<shoot-dir>/.prep/prep_presets.jpg`, `<shoot-dir>/.prep/prep_review.jpg`.

Turn a shoot's raw frames into listing-ready photos: upright, cropped to the
item, backdrop softened, foreground sharpened. Runs **after IDENTIFY, before
INVESTIGATE/DRAFT**. INVESTIGATE keeps reading the **originals** — condition
evidence never comes from a processed file. Only DRAFT reads `listing/`.
Rationale, history and the measurements behind every rule:
[reference/prep-notes.md](reference/prep-notes.md).

## Style guide (optional overlay — OFF unless the run turns it on)

- Guides live under [`../styleguides/`](../styleguides/README.md). Load one
  only when the run names it or a batch config sets `style_guide: <slug>`,
  then read that guide's **PREP — photography** section.
- Advisory only, and it lands on the review card, not in the stage contract:
  a guide may tune backdrop/framing/count taste; it cannot add a stage,
  override a look, or justify a drained frame. The stages (orientation →
  crop → colour), look defaults, `--category` handling and the printed-media
  rules are unchanged.
- Note the loaded guide on the review card (`style_guide: <slug>`).

## Frame count — house target 10

- Count frames BEFORE PREP runs. Under target → say so on the review card
  (`frames: 6 (house target 10)`) and name the missing shots: the
  marked/signed detail, the underside, each disclosed defect, a scale
  reference. That list is the reshoot request; it is not a reason to hold the
  listing.
- PREP never invents frames. Eight good frames beat twelve padded ones.
- Marble photo-protocol shoots keep their own spec — that is a per-item
  agreement, not a count target.

## The hero MAY be a montage — decided per listing

    python -m lib.photo_prep.hero_montage propose <shoot>
    python -m lib.photo_prep.hero_montage apply   <shoot> --style m2 --repoint

- **No default style, and no default to montage at all.** `propose` renders
  both candidates over a numbered strip of every frame; the operator picks:
  **montage 1** (main + one supporting view — inset for a detail, split
  panels for a different state or object), **montage 2** (main + two
  thumbnails down the quietest corner), or **neither** (clean frame as hero,
  details in slots 2 onward). A single clear object often needs no help.
- The picker takes the main view from listing order — never reordering, that
  is the operator's decision — and chooses companions for DISTINCTNESS: each
  unlike the main view AND unlike the companions already chosen. If nothing
  left is distinct enough it says so and offers the two-frame hero only.
  Override with `--frames 1,6,3` — the numbers are the strip.
- It warns when the main frame's subject runs off the edge of the picture,
  naming the frames that show it whole.
- **Honesty rule the tool cannot enforce:** a montage must never imply the
  buyer gets more than one item. When in doubt, thumbnails rather than equal
  panels — a thumbnail reads as a detail, equal panels read as an inventory.
- eBay's picture standards discourage composited gallery images; running them
  is a deliberate, revocable risk. If eBay ever pulls one: clean frame to
  hero, montage to slot two, nothing else changes.

## The run OPENS with a best attempt, made without asking

    python -m lib.photo_prep.prep <shoot> --auto

One pass, no questions: every frame turned the way PREP reads it, every crop
planned, nothing approved and nothing rendered. Two protections are enforced
in code:

- **A guess is labelled a guess.** A frame the resolver cannot read takes the
  best signal it has (the OSD proposal, else 0) and records `guessed: true`.
  Report guessed frames by name, every time — a guess presented as a
  resolution is the one way this pass can hurt.
- **The crop is deliberately loose.** `DEFAULT_PAD` 0.28 of the item's box,
  `MIN_FRAME_KEPT` 0.55 floor: a crop trims edges and always leaves backdrop.

Apply the pass, then **gate on confidence, not on the operator** (operator's
call, 2026-08-27) — and this now covers colour too, not just orientation and
crop:

- **Every frame resolved, nothing guessed, every self-check clean** — the
  normal case — run `--approve-auto` (orientation + crop), pick the look "The
  looks → Defaults" already calls for that shoot (deterministic: `crisp` for
  a new item, the published look for a relist, backdrop-led `punch`/`studio`
  otherwise), and `--approve-stage color` it too. Show ONE card
  (`python tools/prep_card.py <shoot>`) of the revised frames — including the
  chosen colour look — as a record of what shipped, not as a question; the
  run does not stop to wait.
- **Anything guessed or flagged** — a `guessed: true` frame, low mask
  coverage, a "colour pass touched item pixels" self-check flag, a crop the
  pipeline refused, or a shoot the Defaults rule genuinely can't call (no
  clear cloth colour, a look outside the three presets) — ask about THOSE
  frames or that ONE look-choice only, and approve/apply the rest. The one
  question is about the exception, never "may I continue".

Auto-approval never buys a lower bar: `--auto` approves nothing by itself,
`listing/` is still written only after sign-off, and `--approve-auto` /
`--approve-stage color` stamp the same per-stage digest a sheet approval
does — any later edit invalidates it the same way.

## Long shoots — background the run, resume across a kill

A `--check` / `--auto` / `--apply` over a whole shoot directory is
dispatched with `run_in_background: true` **as the default**, not as an
occasional call for a shoot that "seems big" — a multi-frame `--apply`
routinely runs past the Bash tool's 600 s hard timeout, and a foreground
call that gets killed there loses the entire invocation for nothing.

- **A killed or interrupted `--apply` is re-invoked with `--resume`**, never
  restarted plain. The manifest is checkpointed after every rendered frame,
  so `--resume` re-renders only the frame that was in flight when it was
  killed, not the whole shoot (`docs/prep-resume-plan.md`).
- **`--jobs N`** renders up to N frames concurrently (default 1 — serial,
  today's exact behaviour). Frame processing is embarrassingly parallel;
  use `--jobs` on a machine with cores to spare to finish a long `--apply`
  well before it would ever near the timeout. Composes with `--resume`: a
  `--jobs N` run killed mid-batch resumes exactly like a serial one did.

## The interactive review — the exception path, STAGED

Where an override lands, and where a shoot goes when the auto pass is not
good enough. Three corrections, in dependency order, and you do not move on
until the operator says the current one is right:

    1. ORIENTATION  ->  2. CROP  ->  3. COLOUR / touch-up

A crop is only meaningful once the frame is upright; a colour judgement only
on the final framing. (UNSKEW was removed 2026-08; nothing plans one, but a
previously squared shoot REPLAYS its recorded warp, so re-running PREP on a
live shoot returns the pixels a buyer is already looking at.)

## Orientation is DECIDED, not asked — above 95% confidence

    python tools/prep_orient_review.py <shoot>   # -> .prep/orient_review_N.jpg

Read the sheet (a row per frame, all four turns, OSD reading printed for
reference), decide, apply with `--set-rotate`, and surface only the frames
below the bar. Do not build a clickable picker for this stage, and never ask
the operator to rule on frames you can read.

**Clears 95%** — a positive, legible signal:

- printed text whose baseline you can read;
- a human figure (head up, garment hanging down) — the strongest cue in a
  fashion or catalog frame;
- an object with an unambiguous upright;
- **and** agreement with like frames in the shoot — as a *prompt to look
  again*, never as the answer: the camera half is shared across a session,
  the way the item was laid down is not.

**Does NOT clear** (→ operator, with the sheet): "looks more natural" with
nothing legible to point at; a frame whose two halves disagree (say which cue
you followed and why); a round or flat item with no defined upright; anything
resting on OSD alone.

**OSD is reference, not evidence.** Confidence floor 4.0; below it OSD
reports no answer and the frame becomes an ASK (regenerate the bands with
`python tools/osd_audit.py --bands`). A reading above the floor is STILL
reference: high orientation confidence with weak script confidence means
tesseract is confident about marks it does not recognise as language, and on
flat printed media OSD can be confidently wrong — the orientation gate is
what catches it.

State the confidence per frame and name the cue. A number without a cue is
not a judgement.

**Orientation is first — nothing else is measured yet.** `--check` resolves
which way is up and stops; it does NOT plan crop or colour, which would
otherwise be measured against a rotation nobody confirmed.
`--approve-stage orientation` then runs `plan_geometry()` itself against the
just-approved rotations; planning refuses outright while orientation is
unapproved.

## The Frame Check page — the locked review format

Every stage — orientation, crop AND colour — and every modified image is
shown the same way: same page, same card, same controls. Never a JPEG contact
sheet, never prose in place of a picture, never a per-stage improvisation.

    python tools/prep_sheet_html.py <shoot>      # -> <shoot>/.prep/review.html

Publish that file as an artifact and link it; republish the SAME path after
every change so the link never moves. Pair it with a one-click accept in chat
so the operator never copies a command by hand. (The JPEG builders —
`--stage NAME` → `.prep/stage_N_*.jpg` — remain the fallback when no page can
be published.)

**Never show a result without what it came from.** The question is "was the
right thing removed", never "is this a good picture":

| Stage | Before | After | Options offered |
|---|---|---|---|
| orientation | as the camera gave it | at the chosen turn | all four turns |
| crop | the full frame | the crop result | cropped / as shot / force a crop |
| colour | as shot | each rendered look | as shot + every look |

The page must have, on every stage — all load-bearing:

1. **A card per frame**, one picture per card, big enough to judge.
2. **Every option side by side as thumbnails**, current choice ruled green —
   the operator picks from pictures, never a description of a picture.
3. **Click any picture to open it full size**; arrow keys step frame to
   frame.
4. **An override on every card**, including frames the pipeline refused — a
   card you cannot argue with is not a review. A refused crop still offers
   *force a crop*, and says it cannot be previewed.
5. **A free-text box on every card** — "the smokestack is clipped" needs
   somewhere to go; typed notes ride along with the command.
6. **The reason, per frame, in words** ("no studio backdrop (luma 192)"). A
   refusal without a reason reads as a failure.
7. **An Accept button per stage**, stating it sends the stage as shown.
8. **The exact command, copyable, in IDEMPOTENT form** (`--set-rotate`, never
   `--rotate` — a relative command moves the frame again on every paste).

**The page must work with JavaScript OFF, in a sandboxed frame, on old CSS.**
Held by `tests/test_prep_sheet_html.py`:

1. No inline `onclick`, no JS-built DOM — render the markup from Python.
   Script does ONE job (assembling the command); the page is fully usable
   when it never runs.
2. Nothing may depend on a modern selector, a URL or a script — no `:has()`,
   no `:target`. Every piece of state is a native input at the TOP of its
   container, driving following siblings via `~`. A control whose input is
   nested inside the thing it styles cannot work.
3. Selector rules match the option's **index**, generated up to `MAX_OPTS` —
   never hand-listed values. A stage must be able to add an option without
   anyone editing a stylesheet.
4. Write each image's bytes ONCE, as a CSS custom property on the card, and
   paint thumbnail, option chip and preview from it.
5. Never assume a key event landed on an element — it can land on the
   document, which has no `closest()`.
6. Never render a lone option as a button; anything that looks clickable must
   be clickable, and anything that is not gets no affordance.

**Verify the page in a browser before handing it over** — every option on
every card, checking the picture changes in BOTH the card and the full-size
preview. The costliest bugs rendered perfectly and did nothing.

## The looks

All render for comparison; the operator picks. They differ in how hard they
push, never in what they may touch.

| Preset | Backdrop | Item |
|---|---|---|
| `half` | studio with every move halved (`k=0.5`) | half the pop and sharpen |
| `studio` | neutralised to true black/white, fuzz blurred | sharpened |
| `punch` | same | stronger contrast and colour |

Reach for `half` when a look reads washed out: the wash comes from the
correction, so less correction is less wash.

**Defaults:**

- **`crisp` for any NEW item, whatever the backdrop** — the only look that
  cannot misrepresent the goods: full-strength backdrop cleanup, item colour
  exactly as the camera recorded it.
- **An item ALREADY LIVE keeps the look it was published under.** Change one
  on purpose with `--pick`, never in bulk — re-rendering silently changes
  pictures a buyer may have seen.
- Backdrop-led defaults for existing shoots: `punch` on dark or navy cloth,
  `studio` on a light sweep. The default decides what is SHOWN at the gate,
  never what publishes.

## Printed media — books, magazines, catalogs, mailers

- **NO crop: force `--crop <every frame>=off`.** The crop detector locks onto
  the photograph PRINTED in the layout and crops the merchandise away. The
  object of the listing is the whole page, edges included — cover wear,
  corners and squareness live exactly where a tight crop cuts. Set crop off
  BEFORE `--apply`; a crop change afterwards invalidates the renders and
  renders every frame twice.
- **Colour: `asshot` (k=0), no exceptions from the default path.** The
  segmenter hands the printed page to the backdrop pass, which neutralises it
  toward paper white; the damage scales linearly with `k` and the failure is
  silent — a drained spread still looks like a spread. `tenth` is the
  compromise when a genuine cast has to come off.
- **Orientation: prefer the OSD read over the vision estimate** when the two
  disagree — text is the one thing on a page with an unambiguous upright, and
  the corroboration rule guards OSD's known false positive.

## Categories — say what the goods ARE, once

```bash
python -m lib.photo_prep.prep <shoot> --category printed --check
```

| category | detector | looks rendered |
|---|---|---|
| `default` | `auto` — both detectors, arbitrated on agreement | all six, for comparison |
| `printed` | `paper` — LAB decides; u2net kept as a second opinion | `asshot` only |

- `looks` narrows what is RENDERED, never what is picked. A one-look category
  says *the comparison is not live for this kind of item*, not *this look is
  approved*. `--pick` still overrides.
- The category persists in the manifest, so a later `--check`/`--apply` that
  omits the flag gets the same answer. Changing it drops the crop/colour
  sign-offs — every box downstream of the detector was measured against the
  old one. `--subject auto|paper` is the per-shoot escape hatch and outranks
  it.
- Categories are data (`lib/photo_prep/categories.py`), but every field must
  trace to something observed on a real shoot. A category invented from taste
  is a policy change wearing a config file.

## What it will not do

The line is between **the studio** and **the goods**. Re-toning, neutralising
and blurring the backdrop are fair game. On the item itself: white balance,
exposure, contrast and sharpening only.

**No denoise, no smoothing, no blemish removal, ever.** Those soften
scratches and even out tarnish, and a listing photo that disagrees with its
own condition disclosure is worse than a flat one. Sharpening earns its place
by cutting the other way — it makes fine wear *more* legible.

Automatic refusals, each reported per frame on the sheet:

- **No studio backdrop** → no crop, no backdrop move (a mark macro's
  "backdrop" is the item's own surface; lifting it is misrepresentation).
- **A large object in the backdrop** → protected from neutralising and
  blurring (a scale ruler is measurement evidence).
- **Mask failure** (subject under 2% of frame) → every backdrop operation off.
- **Detectors disagree** on where the item is → no crop.
- **The item would be cut**, or the crop lands under 1400px → no crop.

The colour pass also measures its own output: no correction may push an item
pixel to pure black or white that was not already there.

**When a frame looks damaged, measure INSIDE the subject mask before calling
it damage** — most flagged frames are the correction correctly removing a
backdrop cast. Low mask coverage (flat printed covers; thin silver on a light
ground) is the tell that segmentation handed item to backdrop.

## Orientation: what is trusted

Two independent rotations, composed: **camera** (the EXIF Orientation tag —
objective, always applied) and **subject** (how the item was laid in the
frame — no metadata knows this).

The subject half comes from OSD **corroborated by another like frame in the
same shoot**, or from a recorded look, or it goes to ASK. Nothing infers
"probably upright" from an aspect ratio; a round item is resolved by someone
looking once and confirming `0`. Recorded rotations mirror into
`<shoot>/orientation.json`, shared with
[`lib/photo_prep/orient.py`](../lib/photo_prep/orient.py) — a call made in
either tool is the call in both.

## The old chain

`strip_exif` and `orient.py` remain useful for a quick one-off; PREP subsumes
the rest (mapping in the notes). Never run the old chain AND PREP on one
shoot — the single `listing/` output and the code-level gate exist to remove
exactly that ambiguity.

## Gate contract

PREP is a **HARD gate** and stops the run, including headless, until
approval. Enforced in code: `upload_photos_to_eps` refuses photos that are
not prepped and approved, so an unapproved shoot cannot reach eBay even if
this prompt is ignored. Approval goes stale automatically when a source or an
output changes — what was approved must be what is uploaded. Flip to soft
only when the user says so.
