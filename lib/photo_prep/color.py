"""Colour correction that measures its own output.

Three jobs, in the order they matter:

  1. **White balance** off the backdrop. A white sweep or a black felt is a grey
     card — whatever cast it shows is the room's light, not the item. Gains are
     clamped hard (±15%) so a backdrop that is genuinely coloured can never tint
     the item toward a colour it does not have.
  2. **Backdrop normalisation.** Lift a near-white ground to ~250, or deepen a
     dark ground to ~15, using a tone curve with a knee below which subject
     pixels barely move. Dark grounds go to 15 rather than 0 — pure black kills
     the edge separation that makes a marble read as round.
  3. **A gentle subject pass**: saturation, a mild S-curve, and unsharp.

What is deliberately NOT here: denoise, bilateral smoothing, blemish removal,
selective local contrast. Those are the operations that soften scratches and
even out tarnish, and a listing photo that disagrees with its own condition
disclosure is worse than a flat one. Unsharp is included precisely because it
cuts the other way — it makes fine wear MORE legible, not less.

The correctness mechanism is the loop at the bottom of `correct()`. Every
render is measured against the subject mask: if the correction clipped subject
highlights or crushed subject shadows that were not already gone in the
original, the strength is reduced and it renders again. So the output is not
trusted because the parameters looked reasonable — it is checked, and it backs
itself off until it passes.

In-process:
    analyze(bgr, mask) -> BackdropStats
    correct(bgr, mask, pop=...) -> (bgr_out, report dict)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

# Backdrop classification, in sRGB 0-255 luma. A white sweep photographs
# 170-235; navy/black felt 15-70. Anything between is a wood table, a coloured
# cloth or a room — real colour we have no business normalising.
LIGHT_BG_MIN = 150.0
DARK_BG_MAX = 80.0

WHITE_TARGET = 250.0     # not 255: leave headroom so the sweep keeps its texture
BLACK_TARGET = 26.0      # not 0: pure black flattens the item's silhouette.
                         # Was 15.0 — too aggressive on naturally-lit felt:
                         # measured a 31.6-luma ground pulled to 14.8, with
                         # pure-black pixels going 0.58% -> 3.82%. 26 still
                         # normalises across frames but leaves felt reading
                         # as felt rather than as a hole.
WB_CLAMP = 0.15          # max per-channel gain deviation
WB_MIN_BG_LUMA = 25.0    # below this the bg channel ratios are sensor noise

# Relative channel spread, (max-min)/mean, of the backdrop's median colour —
# the test for whether the backdrop can serve as a grey card at all.
#
# Three regimes, measured:
#   0.01-0.03  a neutral sweep under neutral light
#   up to ~0.15  a neutral sweep under a real illuminant cast (tungsten, warm
#                LED) — exactly the case white balance exists to fix
#   0.24-0.51  the navy felt behind the brass dog — a COLOURED CLOTH, whose
#              cast is the cloth itself and must never be balanced away
#
# The bar sits between the second and third. Setting it tight enough to exclude
# an illuminant cast would disable white balance on every warm-lit shoot, which
# is the opposite of the goal.
WB_MAX_CHROMA = 0.15

MAX_BACKOFF = 5          # strength reductions before giving up and shipping 0
BACKOFF_FACTOR = 0.65
CLIP_TOLERANCE = 0.0005  # new clipped subject pixels allowed, as a fraction

# Subject pass. `scurve` is the strength of a contrast curve that PINS 0 and
# 255 — not a linear expansion about mid-grey. The linear version was measured
# doing real damage on the first shoot: 68k clipped and 419k crushed subject
# pixels on one frame, which forced the verify loop to throttle the whole
# correction to 42% and take the backdrop lift down with it.
_POP = {
    "off":    dict(sat=1.00, scurve=0.00, unsharp=0.00),
    "gentle": dict(sat=1.06, scurve=0.12, unsharp=0.30),
    "strong": dict(sat=1.14, scurve=0.25, unsharp=0.55),
}
# A backdrop is cloth, and cloth is nearly colourless. Measured across the
# christmas-train shoot, max(RGB)-min(RGB) over TRUE backdrop reaches only 24 at
# the 99th percentile, while painted red starts at 54 and sits around 100-160.
# The gap is wide and clean, so chroma identifies "this is the item" wherever
# luma cannot — which is the case that broke: on ZZ170057 the segmenter kept the
# reindeer and handed the entire red car to the backdrop, and the neutralise
# pass duly drove it toward grey, costing 38% of the red's saturation.
CHROMA_OBJECT_MIN = 40.0
HALO_CAP = 20.0   # max unsharp over/undershoot, in luma units, per pixel
DIFFUSE_LONG = 1400   # resolution the backdrop blur is computed at (see _bg_diffuse)
ANALYZE_LONG = 1000   # resolution the backdrop STATISTICS are measured at (see analyze)

# The looks offered at the gate. Every image is rendered through all of them and
# the operator picks one for the shoot; nothing here is chosen automatically.
#
# They differ only in how hard they push, never in what they are allowed to
# touch: the backdrop may be re-toned, neutralised and blurred, and the item may
# be sharpened and given contrast. No preset denoises, smooths or retouches the
# item — that stays out of reach at every level, because it is what hides wear.
#
# `wb` is the one switch that reaches the ITEM's colour. White balance is a
# whole-frame gain — it has to be, a cast is in every pixel — so on a coloured
# cloth whose chroma slips under WB_MAX_CHROMA it re-tints the goods along with
# the backdrop. A look that sets wb=False keeps every backdrop operation and
# gives up only that global gain.
PRESETS = {
    "studio":  dict(pop="gentle", bg_neutralize=1.0, bg_diffuse=0.85, sharpen=0.45,
                    wb=True,
                    label="backdrop neutralised + fuzz blurred; item sharpened"),
    "punch":   dict(pop="strong", bg_neutralize=1.0, bg_diffuse=1.0, sharpen=0.65,
                    wb=True,
                    label="as studio, with stronger item contrast and colour"),
    # Studio with every move halved. Not a fourth set of numbers to keep in
    # step: `k` is the same multiplier the rail guard already backs off with,
    # so `half` is literally studio at 0.5 — half the white-balance gain, half
    # the backdrop curve, half the neutralise, half the blur, half the pop and
    # half the sharpen. Added because studio read as washed out on the
    # christmas-train shoot, and the wash comes from the correction: less
    # correction, less wash.
    "half":    dict(pop="gentle", bg_neutralize=1.0, bg_diffuse=0.85, sharpen=0.45,
                    wb=True, k=0.5,
                    label="studio at half strength — every move halved"),
    # The camera's colour profile, kept. Backdrop work runs at full strength —
    # tone curve, neutralise, blur — and the item is sharpened harder than any
    # other look, but nothing touches its colour: no white balance, no
    # saturation, no contrast curve. Measured on the keys shoot (navy felt,
    # chroma straddling WB_MAX_CHROMA): punch moved the metal's mean by 21 of
    # 255 and reversed its channel order, rendering copper as green-gold; this
    # look moves it by 5, uniformly across R, G and B, which is sharpening
    # rather than a tint. Reach for it whenever the ground is a coloured cloth.
    # Studio at a tenth. Chosen off the measured ladder (100/50/25/12.5/6/3)
    # on the christmas-train shoot: the operator wanted the felt left alone and
    # only the cast and the lighting gradient taken off. At this strength the
    # backdrop moves about 4 luma of the ~28 that full strength moves, and the
    # item is left essentially as shot.
    "tenth":   dict(pop="gentle", bg_neutralize=1.0, bg_diffuse=0.85, sharpen=0.45,
                    wb=True, k=0.10,
                    label="studio at 10% — cast and gradient off, felt kept"),
    # The escape hatch: ship the frame exactly as the camera recorded it.
    # k=0 multiplies every move by zero — white balance, tone curve, neutralise,
    # blur, pop and sharpen alike — so the output is the input, byte for byte in
    # everything but JPEG re-encoding. Added when a mask failure greyed a fairy
    # doll's magenta wings on a live listing: the right answer to a correction
    # that cannot be trusted on a given shoot is no correction, not a gentler
    # one. Orientation and crop are unaffected; those are not colour claims.
    "asshot":  dict(pop="gentle", bg_neutralize=1.0, bg_diffuse=0.85, sharpen=0.45,
                    wb=True, k=0.0,
                    label="as shot — no colour correction at all"),
    "crisp":   dict(pop="off", bg_neutralize=1.0, bg_diffuse=1.0, sharpen=0.85,
                    wb=False,
                    label="camera colour kept; backdrop cleaned, item sharpened hard"),
}

# Which look a shoot gets without anyone choosing. Dark cloth takes `punch`:
# that is where the extra contrast and colour pay off, because a deepened
# backdrop gives the item somewhere to separate against. On a white sweep the
# same push has nothing to separate from and reads as over-processing, so light
# backdrops stay on `studio`.
#
# This is a default, not a decision — both looks are always rendered, `--pick`
# overrides, and the approval gate is unchanged either way.
DEFAULT_PRESET = "studio"
DEFAULT_PRESET_BY_BACKDROP = {"dark": "punch", "light": "studio", "other": "studio"}

# ...with one exception, and it is the combination that broke: a WARM-METAL item
# on a DARK cloth defaults to `crisp` instead.
#
# Why this pairing specifically. A dark cloth is rarely a true grey — felt is
# navy, charcoal, deep green — and its chroma lands right on WB_MAX_CHROMA, so
# white balance fires on some frames of a shoot and skips others. The gain it
# then applies is a green/blue lift, which is the exact opposite of what brass,
# bronze and gold are made of, and the item is where the eye goes. Measured on
# the goodwill/keys shoot (brass keys on navy felt): white balance fired on 7 of
# 22 frames; on the worst, `punch` moved the item's mean colour by 21 of 255 and
# reversed its channel order, rendering copper as green-gold, while the other 15
# frames of the SAME keys were left alone — so the set did not even match
# itself. `crisp` renders the same frame at a drift of 4 with red still leading
# green by 13.
#
# A cool or neutral item on the same cloth does not have this problem: a green
# lift on steel or on white porcelain is a small, honest cast correction. So the
# switch is keyed on the item's own warmth, not on the backdrop alone.
WARM_SUBJECT_MIN_RB = 12.0   # mean R minus mean B over the item's COLOURED part

# Neutral things share the frame with the item constantly — the scale rule laid
# alongside, a white tag, a steel fitting — and the subject mask swallows them.
# Measured on goodwill/keys: including them, the five ruler frames read R-B 4
# against 20-21 for the key-only frames, i.e. the ruler alone would have flipped
# the shoot's verdict. Excluding pixels with no colour in them fixes it at the
# source — those same ruler frames then read 34-39, in line with the rest of the
# shoot — because a grey object contributes nothing to a question about hue.
WARM_SAT_FLOOR = 0.10        # (max-min)/max per pixel; below this it is grey


def subject_warmth(bgr: np.ndarray, mask: np.ndarray) -> dict:
    """How warm the ITEM's own colour is — the brass/gold test.

    Measured over the lit, COLOURED part of the subject: shadowed metal is
    nearly black and its channel ratios are sensor noise, and grey pixels (a
    ruler, a steel fitting, a white tag caught by the mask) carry no hue to
    average. What is left is the colour a buyer actually sees.

    Returns the mean RGB of that part, R-B, how much of the lit subject was
    coloured at all, and the verdict.
    """
    import cv2

    if mask is None or bgr is None:
        return dict(r_minus_b=0.0, mean_rgb=[0, 0, 0], pixels=0, warm=False)

    long_side = max(bgr.shape[:2])
    if long_side > ANALYZE_LONG:                       # a statistic, not a render
        s = ANALYZE_LONG / float(long_side)
        small = cv2.resize(bgr, (0, 0), fx=s, fy=s, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, (small.shape[1], small.shape[0]),
                       interpolation=cv2.INTER_NEAREST)
    else:
        small, m = bgr, mask

    rgb = small[:, :, ::-1].astype(np.float32)
    subj = rgb[m > 0]
    if subj.shape[0] < 64:                             # mask failed; no verdict
        return dict(r_minus_b=0.0, mean_rgb=[0, 0, 0], pixels=int(subj.shape[0]),
                    warm=False)

    lum = subj.mean(axis=1)
    lit = subj[lum >= np.median(lum)]

    hi, lo = lit.max(axis=1), lit.min(axis=1)
    sat = (hi - lo) / np.maximum(hi, 1.0)
    coloured = lit[sat >= WARM_SAT_FLOOR]
    coloured_frac = float(coloured.shape[0]) / float(lit.shape[0])
    if coloured.shape[0] < 64:          # a genuinely grey item: not warm, no verdict to make
        return dict(r_minus_b=0.0, mean_rgb=[0, 0, 0], pixels=int(lit.shape[0]),
                    coloured_frac=round(coloured_frac, 3), warm=False)

    mean = coloured.mean(axis=0)
    r_minus_b = float(mean[0] - mean[2])
    return dict(r_minus_b=round(r_minus_b, 2),
                mean_rgb=[int(v) for v in mean],
                pixels=int(coloured.shape[0]),
                coloured_frac=round(coloured_frac, 3),
                warm=r_minus_b >= WARM_SUBJECT_MIN_RB)


def default_preset_for(bg_class: Optional[str],
                       warm_subject: bool = False,
                       new_item: bool = True) -> str:
    """The preset a shoot gets by default.

    `crisp` is the house default for NEW items: it cleans the backdrop at full
    strength and does not touch the item's colour at all, which is the only
    setting that cannot misrepresent the goods. It was adopted after an audit of
    already-published photos found item colour destroyed on 14 frames — a fairy
    doll's magenta wings rendered grey, a red catalog page drained to 2% of its
    saturation — every one of them a mask failure feeding a correction that was
    behaving correctly on a wrong premise.

    `new_item=False` means the shoot is already live on eBay. Those keep the
    look they were published under: re-rendering an existing listing into a
    different look silently changes pictures a buyer may already have seen, and
    a bulk re-default would do it to every listing at once. Change one on
    purpose with `--pick`.

    `warm_subject` comes from `subject_warmth` aggregated over the shoot —
    brass, bronze, gold, gilt, copper on a dark ground. It selects `crisp` too,
    for the same reason: leave the item's colour as the camera recorded it.
    """
    if new_item:
        return "crisp"
    if warm_subject and bg_class == "dark":
        return "crisp"
    return DEFAULT_PRESET_BY_BACKDROP.get(bg_class or "", DEFAULT_PRESET)


# Interquartile spread of the backdrop's luma. A real sweep is smooth: measured
# 10-30 across the white-box and bedspread shoots. A macro's "backdrop" is the
# item's own printed surface and runs 39-113. The gap is wide and clean, so one
# threshold separates "there is a studio behind this" from "this frame IS the
# item" — which decides both whether a crop is wanted and whether the
# background is ours to touch.
BG_IQR_MAX = 35.0


@dataclass
class BackdropStats:
    """What the backdrop is, measured off the non-subject pixels."""
    bg_class: str                  # "light" | "dark" | "other"
    bg_luma: float
    bg_rgb: tuple
    bg_iqr: float                  # luma interquartile spread — smoothness
    bg_rough: float                # high-frequency spread — texture, gradients removed
    bg_pixels: int
    subject_pixels: int
    subj_p2: float                 # 2nd percentile subject luma (shadow foot)
    subj_p98: float                # 98th percentile subject luma (highlight shoulder)

    @property
    def is_sweep(self) -> bool:
        """True when a studio backdrop is actually present behind the item.

        False on detail frames — a macro of a maker's mark, a serial stamp, a
        chip. On those, what the segmenter calls "background" is the item's own
        surface, and normalising it would lift aged tan paper toward white:
        cosmetically nicer, and a misrepresentation of the goods.
        """
        return self.bg_class in ("light", "dark") and self.bg_iqr <= BG_IQR_MAX


def _luma(rgbf: np.ndarray) -> np.ndarray:
    """Rec.709 luma on 0-255 float RGB."""
    return (0.2126 * rgbf[..., 0] + 0.7152 * rgbf[..., 1] + 0.0722 * rgbf[..., 2])


def _bg_selector(mask: np.ndarray) -> np.ndarray:
    """Backdrop pixels, with the subject dilated first.

    The few pixels just outside the mask are the subject's own soft edge and
    its contact shadow. Sampling them as "backdrop" drags the measured
    background dark, which makes the lift overshoot — so grow the subject
    before taking the complement.
    """
    import cv2
    h, w = mask.shape[:2]
    k = max(3, int(round(0.015 * min(h, w))) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    grown = cv2.dilate((mask > 0).astype(np.uint8), kernel)
    return grown == 0


def analyze(bgr: np.ndarray, mask: np.ndarray) -> BackdropStats:
    """Measure the backdrop and the subject's tonal extremes.

    Everything returned here is a STATISTIC — medians, percentiles, spreads — so
    it is computed on a downscaled copy. At full resolution this was the single
    slowest thing in the stage (7.1s of a 26.6s render on a 3000x3000 frame, and
    it runs twice per correction), almost all of it one Gaussian with a
    90-pixel sigma. The pixel counts are scaled back up so the clipping budget
    stays in real units.
    """
    import cv2
    full_h, full_w = mask.shape[:2]
    scale = min(1.0, ANALYZE_LONG / max(full_h, full_w))
    if scale < 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, (bgr.shape[1], bgr.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
    px_scale = (full_h * full_w) / float(max(mask.shape[0] * mask.shape[1], 1))

    rgbf = bgr[:, :, ::-1].astype(np.float32)
    lum = _luma(rgbf)

    bg_sel = _bg_selector(mask)
    subj_sel = mask > 0

    if bg_sel.sum() < 500:
        # Subject fills the frame — there is no backdrop to normalise.
        return BackdropStats("other", float(lum.mean()), (0, 0, 0), 999.0, 999.0,
                             int(bg_sel.sum() * px_scale), int(subj_sel.sum() * px_scale),
                             float(np.percentile(lum, 2)), float(np.percentile(lum, 98)))

    bg_px = rgbf[bg_sel]
    bg_rgb = np.median(bg_px, axis=0)
    bg_luma = float(_luma(bg_rgb))
    bg_lum = lum[bg_sel]
    bg_iqr = float(np.percentile(bg_lum, 75) - np.percentile(bg_lum, 25))

    # High-frequency roughness: spread of the backdrop AFTER removing a heavy
    # blur. `bg_iqr` alone cannot tell a printed surface from a sweep that is
    # simply lit unevenly — both raise it — and on the brass-dog shoot that
    # misread two perfectly good felt frames as macros and left them navy while
    # their twelve siblings went black. Texture is high-frequency; a lighting
    # gradient is not, and the blur removes it.
    import cv2
    sigma = max(4.0, 0.03 * min(lum.shape))
    resid = (lum - cv2.GaussianBlur(lum, (0, 0), sigma))[bg_sel]
    bg_rough = float(np.percentile(resid, 75) - np.percentile(resid, 25))

    if bg_luma >= LIGHT_BG_MIN:
        cls = "light"
    elif bg_luma <= DARK_BG_MAX:
        cls = "dark"
    else:
        cls = "other"

    if subj_sel.sum() > 100:
        s = lum[subj_sel]
        p2, p98 = float(np.percentile(s, 2)), float(np.percentile(s, 98))
    else:
        p2, p98 = 0.0, 255.0

    return BackdropStats(
        bg_class=cls,
        bg_luma=bg_luma,
        bg_rgb=tuple(round(float(v), 1) for v in bg_rgb),
        bg_iqr=bg_iqr,
        bg_rough=bg_rough,
        bg_pixels=int(bg_sel.sum() * px_scale),
        subject_pixels=int(subj_sel.sum() * px_scale),
        subj_p2=p2,
        subj_p98=p98,
    )


# ---------------------------------------------------------------------------
# Tone curve
# ---------------------------------------------------------------------------

def _backdrop_lut(stats: BackdropStats, sweep: Optional[bool] = None,
                  bg_class: Optional[str] = None) -> Optional[np.ndarray]:
    """A monotone 256-entry curve that moves the backdrop and pins the subject.

    Light backdrop: identity below a knee, then bg_luma -> WHITE_TARGET, then a
    compressing run to 255. The compression is why nothing this curve touches
    can be pushed past white.

    Dark backdrop: the mirror image — bg_luma -> BLACK_TARGET, identity above
    the knee.

    The knee is set as a fraction of the backdrop's own level, NOT from the
    subject's tones. It used to key off the subject's highlight shoulder, back
    when the curve was global and had to dodge the item; since the curve is now
    applied through the mask, the subject is out of its way entirely, and the
    knee's only remaining job is deciding how much of the backdrop's own shadow
    gradient comes up with it. Keying it to the subject just made a dark item
    lift the room's shadows harder, which is a coupling with no meaning.
    """
    # `sweep` lets the caller decide this on the UNCROPPED frame. It must be:
    # a tight crop keeps only a thin margin of backdrop, which is mostly the
    # item's contact shadow, so its spread reads as "textured" and a perfectly
    # good white sweep gets left dull. Whether there is a studio behind the item
    # is a fact about the shot, not about how it was later framed.
    if not (stats.is_sweep if sweep is None else sweep):
        return None                           # no studio behind it — leave it alone

    cls = bg_class or stats.bg_class
    if cls == "light":
        bg = stats.bg_luma
        if bg >= WHITE_TARGET:
            return None                       # already at or past target
        knee = max(0.0, min(bg * 0.65, bg - 4.0))
        xs = [0.0, knee, bg, 255.0]
        ys = [0.0, knee, WHITE_TARGET, 255.0]

    elif cls == "dark":
        bg = stats.bg_luma
        if bg <= BLACK_TARGET:
            return None
        knee = min(255.0, max(bg + 0.35 * (255.0 - bg), bg + 4.0))
        xs = [0.0, bg, knee, 255.0]
        ys = [0.0, BLACK_TARGET, knee, 255.0]

    else:
        return None

    # Strictly increasing x is required by np.interp; a degenerate knee (subject
    # tones touching the backdrop) collapses two control points onto each other.
    xs_c, ys_c = [xs[0]], [ys[0]]
    for x, y in zip(xs[1:], ys[1:]):
        if x > xs_c[-1] + 0.5:
            xs_c.append(x)
            ys_c.append(y)
    if len(xs_c) < 2:
        return None

    lut = np.interp(np.arange(256, dtype=np.float32), xs_c, ys_c)
    lut = np.maximum.accumulate(lut)          # monotone, always
    return np.clip(lut, 0, 255).astype(np.float32)


def _white_balance_gains(stats: BackdropStats,
                         bg_class: Optional[str] = None) -> tuple[np.ndarray, str]:
    """Per-channel gains that neutralise the backdrop's cast."""
    if (bg_class or stats.bg_class) not in ("light", "dark"):
        return np.ones(3, np.float32), "skipped (backdrop is not neutral)"
    if stats.bg_luma < WB_MIN_BG_LUMA:
        return np.ones(3, np.float32), f"skipped (backdrop too dark, luma {stats.bg_luma:.0f})"

    bg = np.array(stats.bg_rgb, np.float32)
    if bg.min() <= 1.0:
        return np.ones(3, np.float32), "skipped (a backdrop channel is at zero)"

    # Is this backdrop actually a grey card? Neutralising against a COLOURED
    # cloth does not remove a cast, it invents one: the brass hound dog was shot
    # on navy felt, and balancing the navy away pushed the whole frame warm and
    # rendered plain brass as polished gold — on a live listing whose own title
    # says brass. The ±15% clamp bounded the size of that error without
    # preventing it; every frame simply pinned to the rail.
    chroma = float((bg.max() - bg.min()) / max(bg.mean(), 1.0))
    if chroma > WB_MAX_CHROMA:
        return (np.ones(3, np.float32),
                f"skipped (backdrop is coloured, chroma {chroma:.2f} — not a grey reference)")

    gains = bg.mean() / bg
    clamped = np.clip(gains, 1.0 - WB_CLAMP, 1.0 + WB_CLAMP)
    note = "R{:.3f} G{:.3f} B{:.3f}".format(*clamped)
    if not np.allclose(gains, clamped, atol=1e-3):
        note += " (clamped)"
    return clamped.astype(np.float32), note


# ---------------------------------------------------------------------------
# Render + verify
# ---------------------------------------------------------------------------

def _bg_alpha(mask: np.ndarray, feather_frac: float = 0.015) -> np.ndarray:
    """Per-pixel weight for the backdrop curve: 1 on the sweep, 0 on the item.

    The curve started out global, and on the first real shoot that throttled it
    to 42% strength: a glossy white magazine page sits at the same luma as the
    white sweep behind it, so lifting the backdrop lifted the page into
    clipping, and the verify loop (correctly) refused. The luma histogram cannot
    separate them — but the mask can.

    So the curve is applied THROUGH the mask. The subject is dilated first so
    its soft edge is protected, then the boundary is feathered, so there is no
    seam where the correction stops. The item's own pixels are then untouched by
    the backdrop move, which is both better-looking and the honest arrangement:
    what gets normalised is the room, not the goods.
    """
    import cv2
    h, w = mask.shape[:2]
    k = max(3, int(round(feather_frac * min(h, w))) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    grown = cv2.dilate((mask > 0).astype(np.uint8), kernel)
    alpha = cv2.GaussianBlur((1 - grown).astype(np.float32), (0, 0), max(1.0, k / 2.0))
    return np.clip(alpha, 0.0, 1.0)


def _apply_luma_lut(rgbf: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Move each pixel's luma along the curve, keeping its chroma ratio.

    Per-channel LUT application would shift hue wherever the curve is steep;
    scaling all three channels by the same luma ratio does not.
    """
    lum = _luma(rgbf)
    idx = np.clip(lum, 0, 255).astype(np.uint8)
    out_lum = lut[idx]
    ratio = out_lum / np.maximum(lum, 1e-3)
    return rgbf * ratio[..., None]


def _bg_neutralize(rgbf: np.ndarray, alpha: np.ndarray, strength: float) -> np.ndarray:
    """Pull the BACKDROP's colour toward neutral grey. The item is untouched.

    This is the safe half of the thing `_white_balance_gains` refuses to do.
    Balancing navy felt away *globally* recolours the goods — that is how brass
    came out gold. Desaturating it *through the mask* only ever changes the
    cloth: navy felt reads black, and the brass keeps the colour it actually is.

    Applied at the backdrop's own luma, so a deepened sweep stays deep.
    """
    if strength <= 0:
        return rgbf
    grey = _luma(rgbf)[..., None]
    a = (alpha * strength)
    return rgbf * (1.0 - a) + grey * a


def _protect_objects(rgbf: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Remove real objects from the backdrop weight, keeping only true backdrop.

    Segmentation returns THE subject. A frame often contains something else that
    is not the subject and is emphatically not backdrop: a ruler laid alongside
    for scale, a hang tag, an original box, a size card. On the brass-dog shoot
    the ruler fell outside the mask, so it was treated as cloth — desaturated to
    grey and blurred. That is measurement evidence, destroyed to tidy a backdrop.

    Fuzz and a ruler differ by SIZE, which is the one thing that separates them
    reliably: lint is a few pixels across, a ruler is thousands. So anything in
    the backdrop that deviates from it and forms a large connected region is
    protected; the small stuff is left for the blur.

    Deviation is measured two ways, because one is not enough. LUMA catches a
    steel ruler on dark felt. It does NOT catch a vivid object of roughly the
    same brightness as the cloth — and that is exactly what a red-painted toy
    car on grey-navy felt is. CHROMA catches that: cloth is nearly colourless
    and paint is not (see CHROMA_OBJECT_MIN). Either signal, on a large enough
    region, marks an object.
    """
    import cv2
    h, w = alpha.shape[:2]
    scale = min(1.0, 700.0 / max(h, w))
    small_a = cv2.resize(alpha, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    lum = cv2.resize(_luma(rgbf), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    sh, sw = lum.shape[:2]

    bg = small_a > 0.5
    if bg.sum() < 100:
        return alpha

    base = float(np.median(lum[bg]))
    spread = max(6.0, float(np.percentile(lum[bg], 75) - np.percentile(lum[bg], 25)))
    off_luma = np.abs(lum - base) > 2.5 * spread

    small_rgb = cv2.resize(rgbf, (sw, sh), interpolation=cv2.INTER_AREA)
    chroma = small_rgb.max(axis=2) - small_rgb.min(axis=2)
    off_colour = chroma > CHROMA_OBJECT_MIN

    deviant = ((off_luma | off_colour) & bg).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(deviant, 8)
    area_min = 0.004 * sh * sw          # ~0.4% of the frame reads as an object
    protect = np.zeros((sh, sw), np.uint8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= area_min:
            protect[labels == i] = 1
    if not protect.any():
        return alpha

    k = max(3, int(round(0.02 * min(sh, sw))) | 1)
    protect = cv2.dilate(protect, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    protect = cv2.GaussianBlur(protect.astype(np.float32), (0, 0), max(1.0, k / 2.0))
    protect = cv2.resize(np.clip(protect, 0, 1), (w, h), interpolation=cv2.INTER_LINEAR)
    return alpha * (1.0 - protect)


def _bg_diffuse(rgbf: np.ndarray, alpha: np.ndarray, strength: float,
                mask: np.ndarray) -> np.ndarray:
    """Blur the backdrop — lint, dust and felt fuzz — without touching the item.

    Two things make this safe to do automatically:

    * **Normalised convolution.** A plain blur of the whole frame drags the
      item's colour outward and paints a halo of it onto the backdrop. Blurring
      `image x backdrop_weight` and dividing by the blurred weight means only
      backdrop pixels ever contribute, so the item cannot bleed out.
    * **Median first.** A bright lint fibre put through a Gaussian becomes a
      soft bright streak — smeared, not removed. A median deletes it outright,
      and the Gaussian afterwards evens what is left.

    This is a studio operation on the sweep, not a retouch of the goods, which
    is exactly why it is allowed to be automatic while denoising the item is not.
    """
    if strength <= 0:
        return rgbf
    import cv2
    h, w = rgbf.shape[:2]

    # The whole computation is a blur, so it does not need full resolution —
    # and at 3000x3000 a median with a useful kernel is the slowest thing in the
    # stage by a wide margin. Doing it on a downscaled copy and scaling the
    # result back is visually identical and several times quicker. Only the
    # SMOOTHED layer is downscaled; the blend weight stays full-resolution, so
    # the boundary against the item is as crisp as ever.
    scale = min(1.0, DIFFUSE_LONG / max(h, w))
    small = (cv2.resize(rgbf, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1.0 else rgbf)
    small_a = (cv2.resize(alpha, (small.shape[1], small.shape[0]),
                          interpolation=cv2.INTER_AREA) if scale < 1.0 else alpha)
    sh, sw = small.shape[:2]

    # Median on the 8-bit copy (cv2 requires uint8 for ksize > 5).
    k = max(3, int(round(0.006 * min(sh, sw))) | 1)
    despeckled = cv2.medianBlur(_quantized(small), min(k, 11)).astype(np.float32)

    weight = np.clip(small_a, 0.0, 1.0)
    sigma = max(2.0, 0.008 * min(sh, sw))
    num = cv2.GaussianBlur(despeckled * weight[..., None], (0, 0), sigma)
    den = cv2.GaussianBlur(weight, (0, 0), sigma)[..., None]
    smoothed = num / np.maximum(den, 1e-3)

    if scale < 1.0:
        smoothed = cv2.resize(smoothed, (w, h), interpolation=cv2.INTER_LINEAR)

    a = (alpha * strength)[..., None]
    return rgbf * (1.0 - a) + smoothed * a


def _sharpen_subject(rgbf: np.ndarray, alpha_bg: np.ndarray,
                     amount: float) -> np.ndarray:
    """Unsharp restricted to the item — the other half of diffuse-background.

    Sharpening the backdrop is worse than pointless: it re-crisps the very fuzz
    `_bg_diffuse` just removed. Weighting by the inverse of the backdrop alpha
    puts the detail gain where the buyer is looking.
    """
    if amount <= 0:
        return rgbf
    import cv2
    h, w = rgbf.shape[:2]
    sigma = max(0.8, 0.0035 * min(h, w))
    blur = cv2.GaussianBlur(rgbf, (0, 0), sigma)
    delta = np.clip(rgbf - blur, -HALO_CAP, HALO_CAP)
    fg = np.clip(1.0 - alpha_bg, 0.0, 1.0)[..., None]
    out = rgbf + amount * delta * fg
    return np.clip(out, np.minimum(rgbf, 1.0), np.maximum(rgbf, 254.0))


def _pop(rgbf: np.ndarray, level: str, k: float = 1.0,
         unsharp_override: Optional[float] = None) -> np.ndarray:
    """Saturation + a rail-safe S-curve + clamped unsharp. No smoothing at all.

    `k` scales the whole pass so it participates in the verify loop's back-off
    instead of being switched on and off — an all-or-nothing pop meant the loop
    could only choose between "damages the subject" and "does nothing".
    """
    import cv2
    p = _POP.get(level, _POP["gentle"])
    sat = 1.0 + (p["sat"] - 1.0) * k
    scurve = p["scurve"] * k
    unsharp = (p["unsharp"] if unsharp_override is None else unsharp_override) * k
    if sat == 1.0 and scurve == 0.0 and unsharp == 0.0:
        return rgbf

    out = rgbf
    if sat != 1.0:
        grey = _luma(out)[..., None]
        out = grey + sat * (out - grey)

    if scurve > 0:
        # y = t + k·t(1-t)(2t-1): steeper through the midtones, and it pins both
        # ends — t=0 and t=1 are fixed points, so contrast cannot manufacture a
        # blown highlight the way a linear expansion does.
        t = np.arange(256, dtype=np.float32) / 255.0
        s = t + scurve * t * (1.0 - t) * (2.0 * t - 1.0)
        # …but the shadow lobe of an S-curve still darkens what is already dark,
        # and on a dark item that is exactly where a scratch or a chip lives.
        # Fade the curve out below ~t=0.25 so deep shadow is left alone: contrast
        # is cosmetic, shadow detail is evidence, and evidence wins.
        w = np.clip((t - 0.10) / 0.15, 0.0, 1.0)
        out = _apply_luma_lut(out, np.clip(t + (s - t) * w, 0, 1) * 255.0)

    if unsharp > 0:
        h, w = out.shape[:2]
        sigma = max(0.8, 0.0035 * min(h, w))
        blur = cv2.GaussianBlur(out, (0, 0), sigma)
        # Cap the halo: unsharp's overshoot at a hard edge (a dark item against
        # a white sweep) is what reaches the rails. Capping keeps the fine-detail
        # gain — which is the point, it makes wear MORE legible — without the
        # edge artefact.
        delta = np.clip(out - blur, -HALO_CAP, HALO_CAP)
        out = out + unsharp * delta

    # The subject pass may not MANUFACTURE a rail. Measured on the first shoot:
    # unsharp's undershoot beside high-contrast text drove 49k subject pixels to
    # pure black, and saturation another 5k — enough to make the verify loop
    # throttle the entire correction, backdrop lift included, to nothing.
    #
    # Clamping each channel to stay inside the range its own input already
    # occupied makes that structural rather than iterative: sharpening and
    # saturation still do their work everywhere in the interior, and a pixel can
    # only end up at 0 or 255 if it arrived that way.
    return np.clip(out, np.minimum(rgbf, 1.0), np.maximum(rgbf, 254.0))


def _quantized(rgbf: np.ndarray) -> np.ndarray:
    """What the pixel will actually be in the saved 8-bit file."""
    return np.rint(np.clip(rgbf, 0, 255)).astype(np.uint8)


def _at_rails(rgbf: np.ndarray, subj: np.ndarray) -> tuple[int, int]:
    """(clipped, crushed) subject pixels — any channel already at a rail."""
    if subj.sum() == 0:
        return 0, 0
    px = _quantized(rgbf)[subj]
    return (int((px == 255).any(axis=1).sum()), int((px == 0).any(axis=1).sum()))


def _damage(before: np.ndarray, after: np.ndarray, subj: np.ndarray) -> tuple[int, int]:
    """Subject pixels THIS correction pushed onto a rail — the honest measure.

    Two things had to be got right here, both learned from the first real shoot:

    * **Transitions, not totals.** Comparing aggregate rail counts before and
      after does not work — an image with large black areas has hundreds of
      thousands of pixels near zero, and a mild curve reshuffling them makes the
      totals jump by 400k while destroying nothing. What matters is the pixel
      that carried detail and now does not.
    * **Measure the 8-bit result.** In float, a pixel at 1.4 sliding to 0.4
      looks like a loss; quantized, both are 1 and 0 respectively — and the
      question is only ever what survives into the saved file.
    """
    if subj.sum() == 0:
        return 0, 0
    b, a = _quantized(before)[subj], _quantized(after)[subj]
    b_hi, a_hi = (b == 255).any(axis=1), (a == 255).any(axis=1)
    b_lo, a_lo = (b == 0).any(axis=1), (a == 0).any(axis=1)
    return int((a_hi & ~b_hi).sum()), int((a_lo & ~b_lo).sum())


def correct(bgr: np.ndarray,
            mask: np.ndarray,
            pop: str = "gentle",
            stats: Optional[BackdropStats] = None,
            sweep: Optional[bool] = None,
            bg_class: Optional[str] = None,
            preset: Optional[str] = None) -> tuple[np.ndarray, dict]:
    """White-balance, treat the backdrop, sharpen the item — then verify.

    `preset` selects one of PRESETS. Without it the call behaves as it always
    did: backdrop tone only, subject pass at the given `pop` level, no backdrop
    neutralising or diffusion.

    Returns (corrected BGR, report). The report carries every number the review
    sheet needs to show what was done, including the strength the loop settled
    on and the rail counts that forced it there.
    """
    cfg = dict(PRESETS[preset]) if preset else dict(
        pop=pop, bg_neutralize=0.0, bg_diffuse=0.0, sharpen=None)

    st = stats or analyze(bgr, mask)
    rgb0 = bgr[:, :, ::-1].astype(np.float32)
    subj = mask > 0

    if cfg.get("wb", True):
        gains, wb_note = _white_balance_gains(st, bg_class)
    else:
        gains, wb_note = np.ones(3, np.float32), "off (preset keeps item colour as shot)"
    lut = _backdrop_lut(st, sweep, bg_class)

    # Backdrop operations are gated on there BEING a backdrop, exactly like the
    # tone curve. Diffusing a detail macro would blur the item's own surface —
    # the one retouch this module is not allowed to do.
    is_sweep = st.is_sweep if sweep is None else bool(sweep)

    # If segmentation found essentially nothing, "backdrop" means "the entire
    # frame" and every backdrop operation would run over the item itself. Seen
    # live: one brass-dog frame reported a subject box of 0.1% of the frame.
    # Re-toning, neutralising and blurring all of that is the worst thing this
    # module could do, so when the mask fails, it does none of them.
    mask_failed = float((mask > 0).mean()) < 0.02
    if mask_failed:
        is_sweep = False
        lut = None


    clip0, crush0 = _at_rails(rgb0, subj)
    budget = max(4, int(CLIP_TOLERANCE * max(st.subject_pixels, 1)))

    # A preset may start below full strength (`k`). The rail guard walks it
    # further down from wherever it starts; it never walks it up.
    strength, attempts = float(cfg.get("k", 1.0)), []
    out, new_clip, new_crush = rgb0, 0, 0

    # A ZERO-STRENGTH LOOK IS A PASSTHROUGH, SO DO NOT COMPUTE IT.
    #
    # Every knob in the loop is multiplied by `strength`, and at k == 0 the
    # loop's output is discarded outright further down -- the `out_u8` branch
    # hands back the original pixels, because a float32 round-trip rounds a
    # million pixels by a grey level. So the loop was running white balance,
    # the luma LUT, neutralise, diffuse, pop and sharpen at full resolution to
    # build an array nothing reads. Measured at 26s on one 12 MP catalog frame.
    # `asshot` is the standing look for printed media, the highest-volume
    # category we shoot, so this burned on nearly every catalog frame.
    #
    # Skipping it is exact, not an approximation. With strength 0 the first
    # pass leaves `work` equal to `rgb0` and `_damage` quantizes before
    # comparing, so the attempt it would have recorded is zeros -- appended
    # here so `backoffs` still counts from a real attempt. An empty iterable
    # then sends the loop straight to its `else`, which already sets exactly
    # the values this case wants.
    if strength == 0.0:
        attempts.append(dict(strength=0.0, new_clipped=0, new_crushed=0))

    # Both of these feed the loop and nothing else, so they are built after the
    # guard above rather than before it -- `_bg_alpha` is a pair of full-frame
    # Gaussian blurs (~1.8s at 12 MP) and `_protect_objects` another ~0.3s, and
    # at k == 0 the loop that would read them never runs.
    alpha = _bg_alpha(mask) if strength else None

    # Rulers, hang tags and boxes are not backdrop even when they fall outside
    # the mask. Only the tonal curve is allowed over them; neutralising and
    # blurring are not.
    obj_alpha = (_protect_objects(rgb0, alpha) if is_sweep else alpha) if strength else None

    for _ in (() if strength == 0.0 else range(MAX_BACKOFF + 1)):
        work = rgb0 * (1.0 + strength * (gains - 1.0))[None, None, :]
        if lut is not None:
            ident = np.arange(256, dtype=np.float32)
            eff = ident + strength * (lut - ident)
            lifted = _apply_luma_lut(work, np.clip(eff, 0, 255))
            a3 = alpha[..., None]
            # Blend in place. Written out longhand because the natural
            # expression — work*(1-a3) + lifted*a3 — allocates two more
            # full-resolution float32 arrays, and at 4928x3264 each one is
            # 193 MB. Seven such copies per frame times six workers is 8 GB,
            # which is how this stage came to swap a 16 GB machine to a halt.
            lifted -= work
            lifted *= a3
            work += lifted
            del lifted, a3
        if is_sweep:
            work = _bg_neutralize(work, obj_alpha[..., None],
                                  cfg["bg_neutralize"] * strength)
            work = _bg_diffuse(work, obj_alpha, cfg["bg_diffuse"] * strength, mask)

        # With a preset the unsharp moves out of the global pass and onto the
        # subject alone — sharpening a backdrop just re-crisps the fuzz that was
        # deliberately diffused a line earlier.
        work = _pop(work, cfg["pop"], k=strength,
                    unsharp_override=0.0 if cfg["sharpen"] is not None else None)
        if cfg["sharpen"] is not None:
            work = _sharpen_subject(work, alpha, cfg["sharpen"] * strength)

        # The whole correction may not manufacture a rail ON THE ITEM. The pop
        # pass guards itself, but white balance can do it too: a +15% gain on a
        # marble's specular highlight pushes 230 past 255, and on the dark-felt
        # shoot that alone throttled the backdrop deepening to nothing on four
        # frames out of eleven. Background pixels stay unclamped — the sweep is
        # allowed to go where the curve sends it.
        if subj.any():
            lo, hi = np.minimum(rgb0, 1.0), np.maximum(rgb0, 254.0)
            work = np.where(subj[..., None], np.clip(work, lo, hi), work)

        clip1, crush1 = _damage(rgb0, work, subj)
        if out is not work:
            del out          # release the previous attempt before keeping this one
        attempts.append(dict(strength=round(strength, 3),
                             new_clipped=clip1, new_crushed=crush1))
        if clip1 <= budget and crush1 <= budget:
            out, new_clip, new_crush = work, clip1, crush1
            break
        strength *= BACKOFF_FACTOR
        out, new_clip, new_crush = work, clip1, crush1
    else:
        # Never converged -- ship the original rather than a correction we know
        # damages the subject. Also the k == 0 path above, which skips the loop
        # entirely and wants these very values.
        out, strength, new_clip, new_crush = rgb0, 0.0, 0, 0

    # A zero-strength look must be a true passthrough. Every knob is already
    # multiplied by 0 above, so the loop returns the input — but it returns it
    # through float32 and back, which rounds a million pixels by one grey level.
    # "As shot" has to mean as shot, so hand back the original array itself.
    if float(cfg.get("k", 1.0)) == 0.0:
        # NB out_u8 is RGB here — the return flips it back to BGR — so the
        # passthrough has to hand over an RGB copy. Handing over `bgr` itself
        # sails through every test that only checks "did the pixels move" and
        # ships the frame with its red and blue channels swapped.
        out_u8 = bgr[:, :, ::-1].copy()
    else:
        out_u8 = np.clip(out, 0, 255).astype(np.uint8)
    after = analyze(out_u8[:, :, ::-1], mask)

    report = dict(
        backdrop=asdict(st),
        is_sweep=st.is_sweep if sweep is None else bool(sweep),
        wb_gains=[round(float(g), 4) for g in gains],
        wb_note=wb_note,
        curve="none" if lut is None else
              f"{bg_class or st.bg_class} -> "
              f"{WHITE_TARGET if (bg_class or st.bg_class) == 'light' else BLACK_TARGET:.0f}",
        preset=preset or "(none)",
        pop=f"{cfg['pop']} x{strength:.2f}" if strength else "off",
        mask_failed=mask_failed,
        bg_neutralize=round(cfg["bg_neutralize"] * strength, 3) if is_sweep else 0.0,
        bg_diffuse=round(cfg["bg_diffuse"] * strength, 3) if is_sweep else 0.0,
        sharpen=(round(cfg["sharpen"] * strength, 3)
                 if cfg["sharpen"] is not None else None),
        strength=round(float(strength), 3),
        bg_luma_before=round(st.bg_luma, 1),
        bg_luma_after=round(after.bg_luma, 1),
        subject_at_rails_before=[clip0, crush0],
        subject_newly_clipped=new_clip,
        subject_newly_crushed=new_crush,
        backoffs=len(attempts) - 1,
        attempts=attempts,
    )
    return out_u8[:, :, ::-1], report
