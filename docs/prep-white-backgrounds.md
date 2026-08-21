# Getting the punch effect on white-background shoots

**Proposal, not built.** Written after the dark-cloth fan-out, from measurements
on the shoots that already went through PREP.

## Why punch works on dark cloth, and why it cannot transfer as-is

On navy or black felt the item pops because of one move: the backdrop is driven
to ~15 and neutralised, so the whole frame's contrast budget goes to the item.
Everything else — saturation, the S-curve, sharpening — is a small addition on
top of that. **The separation is free, because the backdrop can always be made
darker than the goods.**

A white sweep has no equivalent move. The backdrop is already the bright end,
and the only direction available is *toward* the item's own highlights. So the
same preset that helps a brass owl actively hurts a white-on-white subject.

This is measured, not asserted. Backdrop-after versus the subject's 98th
percentile luma, on frames PREP has already processed:

| frame | backdrop after | subject p98 | headroom |
|---|---|---|---|
| esquire `…727563` | 244 | 182 | **+62** |
| coke tray `DSC_0218` | 246 | 145 | **+101** |
| coke tray `DSC_0212` | 248 | 213 | +35 |
| esquire `…659663` | 246 | 211 | +35 |
| esquire `…717080_HDR` | 247 | 247 | **−1** |

That last row is the failure. The magazine's glossy highlights land at exactly
the value the sweep was lifted to, so the item's edge dissolves into the
background. On dark felt this cannot happen; on white it is the default risk,
and nothing in the current code notices.

## The rule that should exist

> **The backdrop must remain the brightest thing in the frame.**

Concretely: cap the white target so it stays a margin above the subject's
highlight shoulder.

```
WHITE_HEADROOM = 12
target = min(WHITE_TARGET, max(subj_p98 + WHITE_HEADROOM, bg_luma))
```

On the HDR frame that pulls the target from 250 down to ~250-capped-at-nothing —
i.e. it refuses to lift at all, and says so — instead of lifting the sweep into
the item. On the other four frames it changes nothing, because they already have
35–101 levels of headroom.

This is the white-background analogue of the guards already in the colour pass:
measure the thing that would be destroyed, and back off rather than proceed.

## Where the "pop" comes from instead

If the backdrop cannot recede, the dimensionality has to come from inside the
item. Three moves, in the order I would add them:

**1. Keep the contact shadow.** On white, the shadow under an item is the only
thing grounding it; lift it away and the object floats and looks pasted on. The
current `_bg_alpha` already reduces the lift near the subject boundary as a side
effect of dilation and feathering. I would make that explicit and tunable — a
"shadow keep" radius — rather than leave it as an accident of the feather width.

**2. Clarity, not contrast.** The S-curve raises global contrast; on a white
sweep that mostly pushes the backdrop around. What reads as "pop" on a
light-background product shot is *midtone micro-contrast*: unsharp at a LARGE
radius (~2% of the short side) and low amplitude, masked to the subject.

Worth being explicit about why this is allowed under the honesty rule: it is
large-radius and low-amplitude, so it shapes form and cannot soften a scratch.
It cuts the same way as the existing sharpening — a dent becomes more legible,
not less. Any implementation must go through the same rail guard and the same
verify loop.

**3. A slightly lower white target as the default.** 250 was chosen so the sweep
keeps texture. On light shoots specifically, 244–246 leaves more room for the
item's own whites to sit below the backdrop while still reading as white on the
page.

## What I would NOT do

- **A synthetic drop shadow or a darkened rim.** It manufactures depth that was
  not photographed and looks like a cutout the moment the edge is imperfect.
- **A hard cutout onto pure white.** Halos on fuzzy edges, and buyers read
  hard-cut product shots as stock photography, which is the opposite of what an
  estate-sale listing wants to signal.
- **More saturation.** On white it reads as over-processing immediately, which is
  exactly why `punch` is not the light-backdrop default today.

## Expected yield

Of the 129 active live listings with local originals, **96 are light or
textured**. That number overstates the opportunity: a large share are
macro-heavy jewelry and document shoots where PREP already declines because
there is no studio backdrop to work on — the sterling buckle was the worked
example, 5 of 7 frames correctly untouched.

Realistic estimate: the white-background work is worth doing for the **hero
frames** of light-sweep shoots, and will do nothing for the macros. That is the
right outcome, but it means the win per listing is smaller than on dark cloth,
where whole shoots transform.

## How to validate it

The same way the dark path was validated — a measurement that would catch the
failure, not an eyeball:

- **Edge contrast across the subject boundary** (mean gradient magnitude in a
  band straddling the mask edge) must not decrease. That is the number that
  encodes "the item still separates from the sweep".
- **Headroom** must stay positive on every frame.
- The existing rail counters stay at zero.

Then run it across a handful of light shoots — `esquire-gentleman` and
`coke-tray` are already prepped and make good regression subjects, and the HDR
frame is the known-hard case.
