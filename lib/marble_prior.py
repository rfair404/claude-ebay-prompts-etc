#!/usr/bin/env python3
"""Operating prior for marble ID — the built-in "value lean" + age dial.

WHY: CLIP retrieval + the corpus mode pull every call toward generic machine-
made (measured: tools/gold_eval.py). A raw classifier has NO idea WHERE these
marbles came from. But the user's stream is specific: estate lots from older
people — a kid's 1950s collection, a generational set, sometimes a few modern
ones mixed in. That provenance is real evidence, and it says: most are VINTAGE
but MACHINE-MADE; handmade is unlikely-but-possible (just not found yet);
dollar-store-modern is a minority tail.

This module encodes that as a tunable prior the user dials by CONFIDENCE IN AGE:

    age_confidence in [0,1]   (presets below)
      0.10 dollar-store   bag-fresh, repeats by the hundred, no story
      0.25 no-provenance  loose jar, unknown source
      0.50 estate-typical DEFAULT — estate of an older person, mixed-age set
      0.72 old-collection named/dated collection, period boxes/packaging
      0.90 known-antique  documented pre-1930 provenance

Raise it when a shoot smells old (period packaging, oxidized box, an older
seller's story); lower it when it looks bag-fresh / dollar-store.

WHAT IT DOES (two layers):
  1. A FAMILY prior over {handmade, transitional, machine, nonglass} combined
     Bayesian-style with the CLIP family likelihood (posterior ~ likelihood x
     prior). Machine stays the plurality at every dial setting; handmade rises
     with age but never dominates from the prior alone.
  2. An ERA lean (vintage vs modern) WITHIN machine-made — so an unresolved
     machine call defaults to VINTAGE swirl/patch (keep/lot), not modern filler.

WHAT IT WILL NOT DO (the clamps — honesty rails the dial can't override):
  * HANDMADE / ANTIQUE is never ASSERTED from prior+CLIP alone — it takes a
    positive observed tell (rough pontil, cane structure, Lutz, sulphide figure).
    The prior can only raise it to "live — check the poles", never to an ID.
  * MODERN is never ruled out — a modern-import floor stays until a raking-light
    (orange-peel) / UV check clears it, no matter how high the age dial.
  * Named MONEY-TYPES ($$$+: Guinea, NLR Superman, Popeye, comic, sulphide,
    Lutz) still require their named-pattern tell — the dial moves the DEFAULT
    lean, never the proof bar for a value-moving claim.

So the dial sets what you ASSUME on an unresolved marble (the keep/lot/pull
lean), not what you get to CLAIM. See specializations/marbles.md (method != origin
!= era) and tools/gold_eval.py (why the raw classifier needs this prior at all).

  python lib/marble_prior.py                 # print the default stance
  python lib/marble_prior.py --age 0.72      # ...at a higher age confidence
  python lib/marble_prior.py --age old-collection
"""
from __future__ import annotations

FAMILIES = ("handmade", "transitional", "machine", "nonglass")

# The one knob. Edit this line to move the durable default, or pass --age / a
# preset per run. 0.50 = "estate-typical" (older person's mixed-age set).
DEFAULT_AGE_CONFIDENCE = 0.50

PRESETS = {
    "dollar-store": 0.10,
    "no-provenance": 0.25,
    "estate-typical": 0.50,
    "old-collection": 0.72,
    "known-antique": 0.90,
}

# Family base-rate anchors: (prob at age 0.0, prob at age 1.0); linear interp.
# Tuned to marbles.md reality (machine dominates any pile) + the estate stream
# (vintage-machine plurality, handmade a real-but-minority "generational" tail).
_FAMILY_ANCHORS = {
    "handmade":     (0.05, 0.22),   # rises with age — but see HANDMADE clamp
    "transitional": (0.02, 0.09),
    "machine":      (0.85, 0.65),   # always the plurality (the clamp on softness)
    "nonglass":     (0.08, 0.04),
}

# Era lean WITHIN machine-made: P(vintage | machine) at age 0.0 and 1.0.
# At the 0.50 default this is ~0.58 — "more likely vintage but machine-made".
_VINTAGE_FRAC = (0.30, 0.85)

# --- clamps (the dial cannot cross these) ------------------------------------
MODERN_FLOOR = 0.10          # modern stays at least this plausible at any dial
HANDMADE_NEEDS_TELL = True   # handmade/antique never ASSERTED from prior alone
MONEY_TYPE_NEEDS_TELL = True  # $$$+ named types need their named-pattern tell


def _lerp(a, b, t):
    return a + (b - a) * t


def _clip01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


def resolve_age(age):
    """Accept None / a float in [0,1] / a preset name -> float in [0,1]."""
    if age is None:
        return DEFAULT_AGE_CONFIDENCE
    if isinstance(age, str):
        key = age.strip().lower()
        if key in PRESETS:
            return PRESETS[key]
        return _clip01(float(key))           # numeric string
    return _clip01(age)


def family_prior(age=None):
    """-> dict family -> prior prob (sums to 1) at this age confidence."""
    a = resolve_age(age)
    raw = {f: _lerp(lo, hi, a) for f, (lo, hi) in _FAMILY_ANCHORS.items()}
    s = sum(raw.values())
    return {f: v / s for f, v in raw.items()}


def vintage_fraction(age=None):
    """P(vintage | machine-made) at this age — the era lean within machine."""
    return max(0.0, min(1.0 - MODERN_FLOOR,
                        _lerp(_VINTAGE_FRAC[0], _VINTAGE_FRAC[1], resolve_age(age))))


def apply_prior(likelihood, age=None, strength=1.0):
    """Bayesian posterior over families: posterior ~ likelihood x prior^strength.

    `likelihood`: dict family->p OR a sequence aligned to FAMILIES.
    `strength` (>=0): how hard the prior bites (0 = ignore prior, 1 = full Bayes,
    >1 = stronger lean). Returns dict family->posterior (sums to 1).
    """
    if not isinstance(likelihood, dict):
        likelihood = {f: float(likelihood[i]) for i, f in enumerate(FAMILIES)}
    pri = family_prior(age)
    post = {f: likelihood.get(f, 0.0) * (pri[f] ** strength) for f in FAMILIES}
    s = sum(post.values()) or 1.0
    return {f: post[f] / s for f in FAMILIES}


def value_posture(age=None):
    """The DEFAULT lean for an UNRESOLVED machine-made estate marble at this dial.

    Returns dict {age, vintage_frac, era, tier, action}. This is the keep/lot/
    pull lean — NOT a claim; a named/handmade upgrade still needs its tell.
    """
    a = resolve_age(age)
    vf = vintage_fraction(a)
    if vf >= 0.55:
        era = "vintage (machine-made)"
    elif vf >= 0.40:
        era = "vintage-or-modern (machine-made, era open)"
    else:
        era = "lean modern (machine-made)"
    if a >= 0.72:
        tier = "$ to low-$$ (vintage swirl/patch)"
        action = "KEEP / lot — pull on any named-pattern or pontil tell"
    elif a >= 0.40:
        tier = "$ to low-$$ (vintage-lean swirl/patch)"
        action = "LOT — keep together; pull on a tell, don't trash"
    else:
        tier = "$ filler"
        action = "LOT/jar — modern until a tell says otherwise"
    return {"age": a, "vintage_frac": vf, "era": era, "tier": tier, "action": action}


def describe(age=None):
    """Human-readable stance string for the CLI / classify report."""
    a = resolve_age(age)
    name = next((k for k, v in PRESETS.items() if abs(v - a) < 1e-9), None)
    fp = family_prior(a)
    vp = value_posture(a)
    lines = [
        f"MARBLE OPERATING PRIOR  —  age_confidence = {a:.2f}"
        + (f"  ({name})" if name else "  (custom)"),
        "  family prior:  " + "  ".join(
            f"{f} {fp[f]:.0%}" for f in ("machine", "handmade", "transitional", "nonglass")),
        f"  era lean (if machine): ~{vp['vintage_frac']:.0%} vintage / "
        f"{1 - vp['vintage_frac']:.0%} modern  (modern floor {MODERN_FLOOR:.0%})",
        f"  default on an unresolved marble: {vp['era']} -> {vp['tier']}",
        f"    action lean: {vp['action']}",
        "  CLAMPS (dial can't cross): handmade/antique needs a pontil/cane/Lutz "
        "tell to CLAIM; named $$$+ types need their pattern; modern never ruled "
        "out pre-UV/raking.",
    ]
    return "\n".join(lines)


def main():
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="print the marble operating prior")
    ap.add_argument("--age", default=None,
                    help="0..1 or a preset: " + ", ".join(PRESETS))
    a = ap.parse_args()
    print(describe(a.age))


if __name__ == "__main__":
    main()
