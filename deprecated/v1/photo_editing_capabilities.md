# Photo Editing Capabilities

What I can do with your photos, what I can't, and how to ask so I do it well.

---

## What I CAN do

I have a Linux sandbox with Python + standard image tooling. Common tools available or installable:

- **Pillow (PIL)** — resize, crop, rotate, format convert, brightness/contrast/saturation, basic filters
- **OpenCV** — perspective correction (de-skew), denoise, sharpen, edge detection, color correction
- **ImageMagick** (`magick` / `convert`) — batch ops, auto-levels, white balance, advanced compositing
- **rembg** (installable) — automated background removal (decent for solid objects on plain bgs)
- **pyheif / pillow-heif** — convert iPhone HEIC to JPG
- **exiftool** — strip or read EXIF metadata
- **Real-ESRGAN / waifu2x** — upscale low-res shots (installable, slower)

Concretely, I can do batch edits on a folder like:
- Auto-rotate to portrait/landscape, straighten, deskew
- Crop to remove dead background
- White balance / color correction (huge for vintage paper that scans yellow)
- Boost contrast / sharpen text and logos
- Resize to eBay's preferred 1600×1600 (or square pad with white)
- Convert HEIC → JPG, strip EXIF (privacy + smaller files)
- Background removal → white background (eBay's #1 photo rule)
- Composite a grid/contact-sheet of an item's shots for review
- Watermark (but eBay discourages this)

## What I CAN'T do

- Run Photoshop or Lightroom
- Generative AI edits (no inpainting, no object removal, no fill, no upscale-with-detail)
- True photorealistic background replacement (rembg is good not perfect — paper edges sometimes get nicked)
- Edit RAW files from pro cameras unless you tell me which body (rawpy works for common formats)
- See or correct things I can't see — I can't fix focus that wasn't there, can't recover blown highlights

## How to ask (so I do it well)

Best prompts are specific about the OUTCOME, not the technique:

GOOD:
- "Brighten the rrl folder, white background, square crop, 1600×1600"
- "Strip yellow cast from the britches folder — they look scanned, should look like clean paper"
- "Make the gilhes cover shot pop — boost the gold embossing without crushing detail"
- "Batch convert all polo_* folders from HEIC to JPG, auto-rotate, strip EXIF"

LESS GOOD:
- "Make it better" (better how?)
- "Photoshop this" (I'm not Photoshop)
- "Use AI to remove the background" (I don't have generative tools — I'd use rembg, which has limits)

If you give me one "reference look" you like, I can match the rest of a folder to it. Example: "edit `rrl_01.jpg` how I want it, then apply the same look to the rest of `rrl/`."

## eBay-specific recipe (paper ephemera)

A standard pass I'd recommend for every folder:

1. Auto-rotate + straighten
2. Crop to subject + small margin
3. White balance correction (kill the yellow/blue cast)
4. Light contrast + sharpening (text + logos)
5. White-pad to square 1600×1600
6. Strip EXIF, save as JPG quality 85
7. Rename sequentially: `polo_p7_01.jpg`, `polo_p7_02.jpg`, ...

Tell me "run the standard ebay pass on [folder]" and I'll do all 7 steps.

## Things to shoot WELL so editing is easier

The best edit is the one I don't have to do. From your side:
- **Light:** diffuse window light or a single softbox. Avoid mixed indoor/outdoor light.
- **Background:** plain white poster board or a sheet — saves rembg from doing magic
- **Square-on:** camera parallel to the cover; less perspective to fix
- **Focus:** tap to focus on text, not the corner
- **Fill the frame:** but leave 10% margin for crop room
- **Manual white balance** if your camera supports it — daylight is fine

## Limits to remember

- I can edit what you shoot. I can't invent detail (a blurry copyright page won't get readable, a dark spine won't reveal a hidden title).
- Heavy batch processing on hundreds of large RAWs may hit my sandbox timeouts. If a folder has 200+ files, I may chunk it.
- I work blind to your color taste — show me one reference edit you like and I'll match.

## Quickstart for our current job

When you say "photos ready in [folder]", default behavior unless told otherwise:
1. Standard ebay pass (all 7 steps above)
2. Per-folder review: I'll flag any shot that's too dark/blurry/cropped-poorly to fix and ask you to reshoot
3. Upload best 12–24 shots per draft to the matching eBay listing

---

# Production photo pipeline (`edit_lot.py`)

Canonical script: `PRL-batches/polo-RL-cats/edit_lot.py`. Two style profiles tuned for eBay output. Parallel + chunked to fit the 45-second bash timeout safely.

## Two style profiles

**`--style paper` (DEFAULT)** — paper ephemera: catalogs, mailers, brochures, magazines, postcards. Output is **1600×1600 square with white pad** (eBay's preferred shape), 10% crop margin, JPEG q90, no EXIF.

**`--style natural`** — 3D objects: backgammon set, ceramics, clothing, anything photographed on a sheet/table where the natural background reads better than a hard cutout. Output is longest-edge **2400 px, no white-fill, 4% crop margin**, JPEG q90, no EXIF.

The shared rotation + deskew + smart-crop logic runs in both modes — only the final framing differs.

## Per-photo pipeline (both styles)

| Step | What | Why |
|---|---|---|
| 1 | Load **WITHOUT** `exif_transpose` | Empirically validated on the user's Pixel HDR pipeline: EXIF Orientation=3 is a **false claim** — source pixels are already stored right-side up. `exif_transpose` would rotate them wrong. Trust the raw pixels. |
| 2 | OSD rotation via Tesseract (2-pass vote) | Tests raw baseline vs 180°-flipped, picks orientation with higher Tesseract orientation_conf (threshold 1.5). Catches the rare wrong-way-up shot. Silent on text-light covers → keep raw (correct for this camera). For cameras with reliable EXIF, the OSD vote still works — they'll just need a manual `--use-exif` flag (not yet wired). |
| 3 | Deskew ±5° via Canny + HoughLines | Median angle of dominant horizontal/vertical lines. Cap prevents false rotations on busy interiors. |
| 4 | Smart crop (gated) | Subject bbox via dual Otsu (inverse + normal) + morphological close + union of big contours. **Only crops if subject covers <60% of frame** — already-tight shots pass through untouched. |
| 5 | Framing | `paper`: square-pad to 1600×1600 white (no destructive crop, just letterbox). `natural`: resize longest edge to 2400 px, no padding. |
| 6 | Save JPEG q90, NO EXIF | `Image.fromarray` strips all metadata — output is unambiguous to every viewer. No rotation ambiguity, no GPS leak, smaller files. |

## Why each rotation choice

| Source camera | OSD confident? | Decision |
|---|---|---|
| Pixel HDR (validated case) | n/a | Raw pixels are correct. Use them. |
| Any | Yes on raw baseline | Apply OSD's rotate value to raw. |
| Any | Yes on 180° flipped | Apply 180° flip. |
| Any | No signal (cover, no text) | Keep raw pixels untouched. |

OSD only fires on photos with enough readable text. For cover shots with sparse text it's silent and we keep the raw pixels — which is correct for the validated Pixel HDR pipeline. For cameras whose EXIF you trust, you can pre-process with `mogrify -auto-orient src/*.jpg` to bake EXIF into pixels before running this script. Manual single-file `mogrify -rotate 180 file.jpg` is the fallback for any remaining edge cases.

## Parallel + chunked invocation

**Sandbox timing observed:** OSD call under 4-way contention ≈ 2-4 s. Full pipeline per photo ≈ 4-6 s. 16 photos / 4 workers ≈ 30-40 s wall time — uncomfortably close to the 45 s bash timeout.

**Default chunk size 8** keeps each chunk's wall time at 18-25 s with margin to spare. Each chunk is its own `multiprocessing.Pool` of 4 workers; chunks run sequentially within one invocation.

```bash
# Single folder — paper style (default), auto-chunked
python3 PRL-batches/polo-RL-cats/edit_lot.py \
  PRL-batches/polo-RL-cats/lot1 \
  PRL-batches/polo-RL-cats/lot1/edited

# Natural style for backgammon-type items
python3 PRL-batches/polo-RL-cats/edit_lot.py \
  backgammon1/raw  backgammon1/edited  --style natural

# All Polo lots in one go (loops sequentially across folders, parallel within each)
for d in PRL-batches/polo-RL-cats/lot*/; do
  python3 PRL-batches/polo-RL-cats/edit_lot.py "$d" "${d%/}/edited"
done

# Adjust chunk size if needed (smaller = safer for slow systems)
python3 edit_lot.py src dst --chunk-size 6
python3 edit_lot.py src dst --chunk-size 0   # disable chunking, all at once
```

**Workers:** stay at 4. 8 workers may pressure memory with HDR jpegs. Adjust `WORKERS` constant at top of script if needed.

## Tunables (top of `edit_lot.py`)

```
SQUARE_SIZE              1600     paper style output dimension
NATURAL_MAX_EDGE         2400     natural style longest edge
ANGLE_CAP                5.0      max deskew degrees
CROP_IF_COVERAGE_BELOW   0.60     don't crop if subject already fills >this
CROP_IF_COVERAGE_ABOVE   0.30     don't crop if subject coverage <this
                                  (bbox detection unreliable on very small subjects)
OSD_CONF_TRUST           1.5      Tesseract orientation_conf threshold
WORKERS                  2        parallel pool size (=nproc on this sandbox)
BG_RGB                   (255,255,255)  pad color (eBay white)
```

## Speed knobs

Two flags for the speed/accuracy tradeoff:

| Mode | Wall time on lot2 (14 photos) | Catches sideways text? | When to use |
|---|---|---|---|
| `--fast` | **~17s** | NO — skips OSD entirely | Cover-only lots, or after you've confirmed pixels are upright on this camera |
| (default — OSD ON) | **~32s** | Yes for upside-down covers + text-heavy interiors with strong signal | Mixed lots, interior spreads, any time you're unsure |

The bulk of the time savings comes from **two optimizations baked into the script**:
1. **Pre-resize to 1.5×output** before any cv2 / Tesseract work — operating on a ~2400px array instead of a 4096px array is roughly 3× faster across every step (deskew, OSD, bbox).
2. **WORKERS auto-tuned to nproc** (2 on this sandbox). Higher worker counts oversubscribe CPU on Tesseract calls and slow things down rather than speeding them up — verified empirically.

## Rotation logic (current — 4-pass smart OSD)

1. Test baseline pixels (no exif_transpose — the validated Pixel HDR pipeline stores upright).
2. If conf ≥ 1.5 → apply OSD's rotation, done.
3. Else test 180°-flipped. If conf ≥ 1.5 → apply.
4. Else if either pass had any signal (≥ 0.3), also test 90° CW and 90° CCW (catches sideways text on interior product pages).
5. If all four pass low-conf → keep raw baseline (correct ~95% of time for this camera).

## Crop bounds (current — bounded range)

Crop only fires when subject coverage is in **(0.30, 0.60)**:
- **>0.60** = subject already fills frame, crop would waste pixels
- **<0.30** = bbox detection probably caught just a label or logo, would over-crop
- **0.30–0.60** = sweet spot, apply with 10% margin

## Known limits

- Sideways text on densely-packed interior catalog pages (lot2 example: small print spread) can still slip past OSD if Tesseract conf stays below threshold. **Fallback**: list those filenames for a single `mogrify -rotate 90 file.jpg` follow-up. Detection at OSD threshold 0.5 would catch more but risks false rotations on covers with text labels.
- Subjects shot at very oblique angles (skew > 5°) intentionally skip deskew rather than risk a false rotation. Re-shoot or accept the angle.

## Manual fix-ups after auto-pass

If a small number of photos came out wrong-way (OSD silent and EXIF was wrong), supply a follow-up call listing the filenames + desired rotation. The script's `rotate_array()` helper handles 90/180/270; or use ImageMagick:

```bash
mogrify -rotate 180 path/to/file.jpg
```

## What this pipeline INTENTIONALLY doesn't do

- **No background removal / rembg** — fragile on paper edges, eats time, eBay accepts natural and white-padded both.
- **No color correction / white-balance** — non-destructive default; layer in only when a folder needs it.
- **No upscaling** — original resolution → resampled DOWN to target. Upscaling fakes detail.
- **No watermark** — eBay discourages it.

If a job needs any of these, layer it on as a second pass against the `edited/` output.
