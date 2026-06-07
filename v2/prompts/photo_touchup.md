# PHOTO TOUCHUP — agent-assisted iterative photo cleanup

## What this prompt is for

A single rule set doesn't work across every photo — every shot has its
own subject, background, lighting, framing. This prompt drives me as an
agent to do **iterative, vision-assisted touchup** on one photo at a
time: I look at the photo, diagnose what's wrong, try techniques with
specific parameters, look at the result, self-critique, adjust, and
repeat until I have an output I'd defend. Then I present it to the user
for feedback.

It is NOT a single deterministic pipeline. It's an exploratory loop with
me as the operator.

## When to invoke

- "Touch up `<photo path>`" — single-photo mode.
- "Touch up the photos in `<directory>`" — batch mode (same loop per
  photo, with cross-photo parameter carryover).
- "Touch up `<directory>`, but with white backdrops" / "...with wood
  backgrounds" / "...with black backdrops" — user may volunteer
  backdrop context to bias my technique selection.

## Available techniques (current toolkit)

Every script is independently callable, with single-purpose CLI and
parallel-across-images support. Module-level constants in each file are
the authoritative tunables.

| Technique | Script | Key tunables | What it does |
|---|---|---|---|
| EXIF strip | `v2/lib/photo_prep/strip_exif.py` | (none) | Remove camera EXIF — fixes false-orientation viewer rendering. Idempotent, run once per shoot first. |
| Trim background | `v2/lib/photo_prep/trim_whitespace.py` | `--fuzziness` (ΔE threshold for subject; 12–35 typical), `--margin-pct` (margin around subject; 0.03 default) | Crop to bounding box of subject pixels. Self-calibrates background color from corner samples; outlier-rejects corners that disagree. Refuses to over-crop (passthrough at <5% or >95% kept). |
| Even background | `v2/lib/photo_prep/even_background.py` | `--fuzziness` (called this for legacy reasons; **really** the lower bound of the soft mask), `--edge-band` (soft transition width) | Replace background pixels with median corner color. Subject pixels untouched. Soft anti-aliased mask. **Empirically: replace_below ~12–15, keep_above ~22–25 works for near-white backdrops.** Map to CLI as `--fuzziness 12 --edge-band 10` for replace_below=12 / keep_above=22. |
| Inspect one | `v2/lib/photo_prep/_inspect_one.py` | `--fuzziness`, `--margin-pct` | Diagnostic for trim — prints corner colors, ΔE distribution, mask coverage at multiple thresholds, saves a sample crop. Workshop tool. |

### Future techniques (not yet built — note them when needed)

- Deskew (correct small angle rotation)
- Manual rotation (90/180/270 — when EXIF strip + auto-detection fail)
- Sharpen
- Brightness / contrast adjustment
- White balance correction
- Gaussian blur on background pixels (currently we *replace*; *blur*
  would preserve natural backdrop texture while smoothing variance)
- Weighted-ΔE mode for even_background (luminance counts less than
  chroma — handles bright bg creases that current uniform-ΔE misses)

If a photo needs one of these and the script doesn't exist yet, I write
a short ad-hoc Python block inline (using the same numpy + Pillow stack
as the other scripts) rather than blocking on tool work.

## The loop (per photo)

### Step 1 — Look at the original

Use the `Read` tool to view the source photo. Form a written diagnosis:

- **Subject:** what is it, where in the frame, how much of the frame
  does it cover, what colors dominate
- **Background:** uniform or busy, dominant color (white / off-white /
  warm-gray / wood / black / patterned), shadow & lighting variance
  (none / mild / strong), wrinkles / creases / props bleeding in
- **Framing:** centered, off-center, subject extends to edges
- **Orientation:** correctly upright, sideways, upside-down
- **Sharpness:** in focus, motion-blurred, soft

Write this diagnosis out — don't skip it. Subsequent decisions depend
on it.

### Step 2 — Plan the operations

From the diagnosis, decide which techniques will help and in what order.
Default order when relevant:

1. **EXIF strip** if not already done for this shoot.
2. **Manual rotation** if orientation is wrong (only when I can confidently
   tell from the photo content).
3. **Even background** if backdrop is meant to be uniform but isn't.
   Run BEFORE trim because evening makes trim's subject mask cleaner.
4. **Trim** to crop to the subject with margin.
5. (Future: sharpen, color correction, etc.)

Skip steps that don't apply. A perfectly-framed sharp shot on a uniform
backdrop may only need EXIF strip. A typical user shot may need all of
the above.

### Step 3 — Pick starting parameters

Use the photo's diagnosis to pick initial parameters, not hardcoded
defaults:

- **trim_whitespace `--fuzziness`:** start at 12 for clean uniform
  backgrounds, 25–30 for backgrounds with lighting variance / shadows /
  wrinkles. If the diagnosis flagged "uneven lighting", start at 30.
- **even_background `--fuzziness` (= replace_below):** start at 12. Try
  15 if the backdrop has visible variance.
- **even_background `--edge-band`:** start at 10 (so the keep_above
  threshold lands ~22–25). Subject damage observed if edge_band pushes
  keep_above past ~30 — the lighter parts of the subject start blending
  with bg color.

### Step 4 — Execute

Run the selected scripts in order. For each run, use a clearly-named
output directory under `<input>/touched-up/iter-N/` so each iteration
is preserved for comparison. Track which parameter values were used.

### Step 5 — Look at the result

Use the `Read` tool on the final output. Critique against these
criteria:

**Subject quality (most important):**
- Is the subject fully visible and recognizable? **If subject pixels
  have been blasted toward bg color, that's a hard fail — go back to
  step 3 and tighten the even-bg thresholds.**
- Are subject highlights / texture / fine detail preserved?
- Is there a halo around the subject (mask edge too narrow)?

**Background quality:**
- Is the backdrop noticeably more uniform than the original?
- Are there visible mask artifacts — hard edges, halos, color shifts?
- Are there obvious un-touched bg patches (lighting wrinkles, props,
  surface texture the algorithm missed)?

**Framing:**
- Is the subject well-positioned within the new frame?
- Is the crop too tight (clipping the subject) or too loose (lots of
  unused bg)?
- Is the aspect ratio reasonable for an eBay listing (1:1, 4:3, 3:2)?

**Orientation & sharpness:**
- Right-side-up? Not skewed?
- As sharp as the source (didn't lose detail through processing)?

Write a short critique. Be specific: "subject's tan stripes are
slightly washed out" is actionable; "looks bad" is not.

### Step 6 — Decide

Three outcomes from the critique:

- **Satisfied** — output meets every criterion above well enough that
  I'd put it on an eBay listing. Save as the final result and move to
  Step 7.
- **Needs adjustment** — specific issue identified. Adjust the
  parameter that addresses it (e.g., subject washing out → lower
  `--edge-band` to keep the keep_above threshold lower; visible bg
  creases → consider weighted-ΔE mode). Loop back to Step 4.
- **Stuck after 3 iterations** — if I've tried three parameter
  combinations and none is satisfactory, save the best of the three
  as `<photo>.touched-up.jpg`, write a note explaining what's hard
  about this photo, and ask the user for direction.

### Step 7 — Present to user

Always include in the response back to the user:

- Path to the final output
- A one-paragraph summary of what was done: techniques applied,
  parameter values, how many iterations
- Anything I'd flag for user judgment ("the backdrop wrinkles in the
  upper-left were too bright for the algorithm to even out cleanly —
  I left them. If you want them gone I can try weighted-ΔE.")
- For single-photo invocation: ask for explicit user feedback before
  considering the work done

## Batch mode

When invoked on a directory:

1. Pick one representative photo first — usually the first
   lexicographically, or one the user calls out.
2. Run the full single-photo loop on it.
3. Confirm with user that the result is good.
4. Apply the same operations with the same parameters to the
   remaining photos.
5. For each subsequent photo, spot-check the result via Read. If a
   photo looks meaningfully different from the test photo (different
   lighting, different framing), drop back into single-photo
   iteration for that one.
6. Final report: how many photos auto-applied, how many needed
   per-photo tuning, paths to outputs.

This is faster than iterating from scratch on each photo, but stays
photo-aware via the spot checks.

## Self-critique honesty rules

- **If subject is damaged, that's a failure.** Don't ship a "clean
  backdrop" output if it cost the subject's appearance. The user
  explicitly stated: "you can still recognize the [subject]" — this is
  the hard constraint.
- **A photo that doesn't need a technique shouldn't get that technique.**
  A perfectly-framed shot doesn't need cropping. A clean backdrop
  doesn't need evening. Detect and skip.
- **An algorithm's confidence is not the same as visual quality.** I
  must look at every output, not trust the script's "subject ~24%"
  metric alone.
- **Document failure honestly.** If I can't make a photo look good,
  say so. Don't ship a bad output dressed up as success.

## What this prompt is NOT

- NOT a one-shot deterministic batch tool — that's what
  `strip_exif.py` etc. are by themselves. Call them directly when
  you want a single deterministic pass.
- NOT a substitute for shooting better photos. If the source is
  blurry, dark, or has the subject extending off-frame, no touchup
  will fix that — flag for re-shoot.
- NOT a generative tool. No inpainting, no fill, no AI upscale, no
  invented detail. Every output pixel traces back to a source pixel
  or to a sampled-from-source background color.

## Output location

For each touched-up photo: `<input-dir>/touched-up/<original-name>`.
Iteration intermediates live in `<input-dir>/touched-up/iter-N/` and
are kept for the user's reference until they explicitly ask to clean
up. A short per-photo log goes to `<input-dir>/touched-up/<name>.log`
recording techniques applied, parameter values, iteration count, and
the final critique.

## Response brevity (mandatory)

Be substantially shorter than feels natural.

- Diagnosis (Step 1): bulleted observations, 6 bullets max. Subject / bg / framing / orientation / sharpness / one-line problem statement. Not paragraphs.
- Plan (Step 2): name the operations + parameters in 2-4 lines. No prose explanation of why.
- Critique (Step 5): table or bullet list, one line per criterion. Not paragraphs.
- Chat reply at end: output path + a one-line outcome ("photo 1 done: subject clean, bg uniform, minor crease in upper-left left intact"). Cap at 3-6 lines unless the user asked for detail.
- Banned filler: "Let me...", "I'll now...", "Looking at this...", "Based on the analysis...", "Note that...", "It's worth mentioning...", "Importantly...".
- Per-photo log: structured key:value lines, not narrative.
- When in doubt, cut.
