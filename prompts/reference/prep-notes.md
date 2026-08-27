# PREP — rationale, history, and evidence

Companion to [prompts/prep.md](../prep.md). The prompt holds the rules; this
file holds *why* they exist, so the prompt stays a checklist. Read this only
when a rule is disputed or being changed — never as part of a routine PREP
run.

## Frame count: where "10" comes from

Measured across 245 active listings of a seller worth matching
([study](../../styleguides/_studies/patinaelements.md)): median 10 photos,
38% at 12 or more, effectively none at the 24 cap. The patinaelements
conventions were adopted as house style on 2026-08-24, which is why that
guide is no longer an overlay; the overlay mechanism stays for the *next*
seller we study.

## Montage: the distinctness rule and the accepted risk

The studied seller montages roughly 18% of their listings, not all of them —
hence "no default to montage". The distinctness rule (companions unlike the
main view AND unlike each other) exists because scoring against the main view
alone put two photographs of the same presentation box in the same hero,
twice: each was a fine answer to "unlike the macro", and together they said
one thing twice.

The eBay picture-standards risk is accepted deliberately: the studied seller
runs montages at scale on live listings, which is evidence it is tolerated in
practice — tolerance, not a guarantee.

## Why the auto pass may guess

The staged review is the point of PREP, but it also spends the operator's
attention on frames where the answer was never in doubt. Deciding first and
asking second costs nothing as long as a guess is labelled a guess and the
crop is loose enough that "too generous" only costs a second look — "too
tight" has already thrown pixels away, and nobody re-shoots.

## Why the gate moved to confidence (2026-08-27)

Operator's call, part of the self-optimizing-sessions program (GH #36): an
approval that is always "yes" is pure friction. The per-step accept became a
confidence gate — self-approve when everything is resolved and clean, ask
only about named exceptions. Nothing about the quality bars moved; what
changed is who clicks approve when the pipeline has nothing to ask.

## UNSKEW: why it was removed (2026-08)

It warped a rectangular item so its edges met the picture's. Removed on the
operator's call: it cost a quad fit and a full-frame resample on every frame,
and it damaged more photos than it saved — a quad landing on a mat, a mount
or a soft shadow squares up the wrong rectangle — while two degrees of tilt
is not something a buyer sees in a thumbnail. The REPLAY rule (a previously
squared shoot re-applies its recorded warp) exists so re-running PREP on a
live shoot returns the pixels a buyer is already looking at.

## Orientation: the measurements behind the rules

**The OSD floor (4.0).** Measured across 73 frames with a recorded human look
beside the reading: at the old confidence floor OSD agreed with the operator
**49%** of the time — a coin toss. At 4.0 it agrees 84% and still answers 42%
of frames. Regenerate with `python tools/osd_audit.py --bands`
(`docs/osd-audit-2026-08-21.md`).

**Why 84% is still "reference, not evidence."** The one frame that shipped
sideways on `paul-fredrick` read confidence 12.29 — well clear of any bar —
on a script confidence of 0.6: tesseract confident about marks it did not
recognise as language. On the same shoot the detector read subject 270 on
five frames at confidence up to 12.2, with recognised script, all
corroborating each other, and all wrong — "high confidence, corroborated" is
exactly the state in which a headless run ships mistakes unreviewed. The
orientation gate is what catches it.

**Why OSD needs corroboration at all.** Measured false positives include a
textless macro read as text at higher confidence than a real magazine cover —
which is why an uncorroborated OSD read goes to ASK instead of resolving.

**Why session agreement is a prompt, not an answer.** The rule used to read
"six spreads photographed in one session cannot correctly differ by 90°", and
that is false: on `paul-fredrick` the cover was shot portrait-wise and the
spreads were laid on the bedspread turned 90°. The camera half is shared
across a session; the way the item was laid down is not. Treating
disagreement as proof of error made a correct OSD reading look wrong.

**Why OSD beats the vision estimate on printed media.** Measured on
`fall-and-winter-1980`: the three frames resolved `exif+osd` all landed
upright; two of the three resolved `exif+vision` shipped rotated 90° — models
on their sides, body text running vertically — and none were flagged ASK. The
pipeline was confidently wrong rather than uncertain.

**Why nothing is planned before orientation approval.** Crop and colour
describe the geometry they were computed on. Six catalog spreads shipped with
crops planned at 0° while the manifest ended up saying 270° — hence `--check`
stops after orientation, and `plan_geometry()` runs only against approved
rotations.

## The Frame Check page: rules learned by breaking them

Every constraint in the prompt's page-contract list was paid for:

- **JS-off / no modern selectors.** Two versions built the DOM in JS and
  routed every click through a handler; both rendered perfectly and neither
  responded to a single click in the sandboxed frame the operator actually
  reads these pages in. Three mechanisms were tried and abandoned — a
  JS-built DOM (script never ran), `:target` previews (a URL fragment never
  lands in a sandboxed frame), `:has()` selection (a 2022 selector — where
  missing, the page draws perfectly and answers nothing). Native inputs
  driving `~` siblings has worked since CSS2. The Accept button once shipped
  inert because its input was nested inside the element it styled.
- **Index-matched selector rules.** An earlier version hand-listed option
  values (`"0"`, `"90"`, `"studio"`, …) in CSS; the day the colour stage grew
  `half`, `tenth` and `crisp`, picking any of the three matched no rule — the
  card went blank and the preview opened empty, on the stage the operator
  uses most. `tests/test_prep_sheet_html.py` fails if the rules go back to
  being hand-listed or a stage outgrows `MAX_OPTS`.
- **One copy of each image's bytes.** Three copies took a fourteen-frame
  shoot to 15 MB against the 16 MB artifact ceiling.
- **Idempotent commands.** `--rotate` is relative to what the sheet shows; a
  page emitting it as if absolute moved the frame again on every paste —
  hence `--set-rotate`.
- **Click-through verification.** Both of the worst bugs rendered perfectly
  and did nothing; neither was visible in the markup. The generator test
  covers the generator; only clicking covers the page.

## The saturation audit (2026-08-19): why "measure inside the mask"

An audit of 819 already-published frames (`tools/prep_saturation_audit.py`,
then `tools/prep_saturation_verify.py` against the subject mask) found item
colour destroyed on 14 frames across 10 shoots, 9 live. Every one had the
same shape: the correction behaving **correctly on a wrong premise** —
segmentation handed part of the item to the backdrop, and the backdrop pass
neutralised it, exactly as it is told to do to cloth. The tell is mask
coverage: 43–70% on flat printed catalog covers, 5–9% on thin silver on a
light ground. The first-pass sweep flagged 39 shoots, but 54 of 76 flagged
frames were the correction *working* (a backdrop cast being removed) — hence:
measure inside the mask before calling anything damage.

Mitigations now in code: `_protect_objects` tests chroma as well as luma
(`CHROMA_OBJECT_MIN`, measured — cloth reaches 24, paint starts at 54),
`crisp` is the default for new items, `asshot` exists for a shoot whose mask
cannot be trusted at all.

## Printed media: the measurements

**Crop.** Before the rule, of 56 cropped media frames the median kept 75% of
the frame and the tail ran to 19%: `j-crew/3` shipped cropped to a printed
boot with the J.CREW masthead outside the frame; `mark-shale-business-casual`
to a printed chair (24% kept); `brother-tree` into body text (18.9%); the
`fall-and-winter-1980` mailer to a bare black bar — the crop locked onto the
redaction rectangle over the address.

**Colour.** On `more-mags-444/fall-and-winter-1980`, live renders: saturation
against source-selected pixels fell 51.8% on the hero, 60.9% and 61.3% on two
spreads, 94.0% on the mailer. Damage scales linearly with `k` — studio
−39.4%, half −19.2%, tenth −3.5%, asshot −0.2% — the signature of the
backdrop pass eating the subject, not of a bad curve. Re-running under the
chroma guard did not change the numbers. The failure is silent (a drained
spread still looks like a spread), which is why `asshot` is the default
rather than a warning.

**Why `printed` has its own detector.** On a catalog the salient object is
the picture printed ON the item, so u2net cuts out the cover model and
returns a mask that is a strict sub-region of the paper — and the containment
test cannot catch it (a box wholly inside the paper's box scores 1.0). LAB
has no such confusion: paper against a sweep is exactly the figure/ground
split it measures.

**The speed numbers.** Rendering five looks nobody opens costs ~25s a frame.
Measured on five 12 MP catalog frames, same decisions, same gates: 8m10s
under `default` → 45.6s under `printed` (10.7×); per frame, once u2net is
loaded, ~94s → ~5.1s. Two of those seconds came from `asshot` itself: at
`k=0` every correction term was being computed, multiplied by zero and
discarded; the loop is now skipped (26s → 3.9s a frame), with pixels and the
colour report asserted identical to the old path.

## What replaced what (the old chain)

| Old step | Now |
|---|---|
| `strip_exif` | PREP bakes EXIF internally; `no-exif/` is legacy output |
| `even_background`, `trim_whitespace` | PREP's backdrop pass (mask-driven, works on dark felt too) |
| `center_crop` | PREP's crop (its safety guards are reused, not reimplemented) |
| `orient.py --set` | still works; PREP reads AND writes its manifest |
| DRAFT step 1/1b/2/3 | the PREP prompt |

The prohibition on running the old chain and PREP on one shoot exists because
the resulting ambiguity — which directory does the draft point at? — is what
the single `listing/` output was built to remove.
