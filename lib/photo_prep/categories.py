"""Category profiles — the per-category answers PREP used to ask flag by flag.

Almost every knob on the PREP command line is not a preference. It is a
statement about *what is in front of the camera*, and it has the same answer
every time for a given kind of goods:

  * a catalog is flat printed paper, so the subject detector has to be told the
    item is the sheet and not the model printed on it (`subject.mask_for`);
  * a catalog also always ships `asshot`, because a scan-like reproduction of
    printed colour is the thing buyers are judging;
  * so rendering the other five looks for a catalog produces images nobody will
    ever open, at roughly 25 seconds each on a 12 MP frame.

Carrying those answers as loose flags meant re-typing them per shoot, getting
them wrong on a batch, and -- because `--subject` had to be repeated on every
re-check -- silently demoting a paper shoot back to `auto`. A category names the
whole set once.

    python lib/photo_prep/prep.py <shoot> --category printed --check

WHAT A CATEGORY IS ALLOWED TO SET

Only the inputs to a measurement, never the result of one. A category may say
which detector to believe, which looks are worth rendering, and how tight the
framing should be. It may NOT pre-approve a stage, assert an orientation, or
choose a frame's crop box -- those are decided per frame, by measurement and by
the operator at the gate. The four-stage order and the human gates are unchanged
by any category (see prompts/prep.md).

WHY `looks` IS A FILTER AND NOT A CHOICE

`looks` narrows what gets RENDERED, not what gets picked. The operator still
picks at the colour stage and `--pick` still overrides. A category that renders
one look is saying "the comparison is not live for this kind of item", which is
true for printed media and is why the batch is six times faster; it is not
saying "this look is approved".

ADDING ONE

Keep it grounded. Every field should trace to something observed on a real
shoot, and the docstring should say what. A category invented from taste is a
policy change wearing a config file, and it will be applied to hundreds of
frames before anyone notices it was a guess.
"""
from __future__ import annotations

from typing import Optional

DEFAULT_CATEGORY = "default"


# Each profile is the *complete* set of overrides for its category. A key that
# is absent means "keep PREP's own default", so a profile stays readable as the
# short list of ways this category actually differs.
CATEGORIES: dict[str, dict] = {
    # The unchanged pipeline: believe both detectors, render every look so the
    # operator can compare at the colour gate.
    "default": dict(
        label="no category — every look rendered for comparison",
        subject="auto",
        looks=None,          # None = all of color.PRESETS
    ),

    # Catalogs, magazines, record sleeves, printed ephemera.
    #
    # subject=paper: u2net cuts out the cover model and returns a mask that is a
    # strict sub-region of the paper, which `_containment` scores 1.0 and so
    # cannot catch. The LAB detector reads paper against a sweep correctly.
    # See the module docstring in subject.py.
    #
    # looks=asshot: printed colour is the thing being sold, and correcting it is
    # a claim about the item's colour that we cannot stand behind. This was
    # already the standing practice (run_apply's docstring named it before this
    # module existed); the category makes it the default rather than a flag
    # someone has to remember on every shoot.
    "printed": dict(
        label="flat printed goods — catalogs, magazines, sleeves",
        subject="paper",
        looks=("asshot",),
    ),
}


def names() -> tuple:
    return tuple(CATEGORIES)


def profile(name: Optional[str]) -> dict:
    """The profile for `name`, or the default one. Unknown names raise."""
    key = name or DEFAULT_CATEGORY
    if key not in CATEGORIES:
        raise ValueError(
            f"unknown category {key!r}; use one of {', '.join(names())}")
    return dict(CATEGORIES[key])


def looks_for(name: Optional[str]) -> tuple:
    """Which presets this category renders. Empty tuple = all of them.

    Empty rather than None so the caller can pass it straight into `run_apply`'s
    `only`, which already treats a falsy value as "render everything".
    """
    return tuple(profile(name).get("looks") or ())


def subject_for(name: Optional[str]) -> str:
    return profile(name).get("subject", "auto")


def describe(name: Optional[str]) -> str:
    p = profile(name)
    looks = p.get("looks")
    return (f"{name or DEFAULT_CATEGORY}: {p.get('label', '')} "
            f"[subject={p.get('subject', 'auto')}, "
            f"looks={'all' if not looks else '+'.join(looks)}]")
