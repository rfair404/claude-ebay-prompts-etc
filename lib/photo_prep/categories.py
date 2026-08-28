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
which detector to believe, which looks are worth rendering, how tight the
framing should be, and which of the RENDERED looks to show first. It may NOT
pre-approve a stage, assert an orientation, or choose a frame's crop box --
those are decided per frame, by measurement and by the operator at the gate.
The four-stage order and the human gates are unchanged by any category (see
prompts/prep.md).

WHY `looks` IS A FILTER AND NOT A CHOICE

`looks` narrows what gets RENDERED, not what gets picked. The operator still
picks at the colour stage and `--pick` still overrides. A category that renders
one look is saying "the comparison is not live for this kind of item", which is
true for printed media and is why the batch is six times faster; it is not
saying "this look is approved".

THE SCHEMA (issue #23 -- carrying more than `subject` + `looks`)

The two shipped categories only needed a detector and a look filter, which is
why that was the whole schema for a while. A category is a statement about the
GOODS, though, and the goods also determine the framing and which of the
rendered looks is worth showing first -- so the profile now carries those too.
Every key below is optional; an absent one means "keep PREP's own default",
which is what makes `default` and `printed` stay exactly as they were.

  * `subject`, `looks` -- unchanged, see above.
  * `aspect`, `pad` -- the same framing knobs `--aspect`/`--pad` already set
    (see `center_crop.DEFAULT_ASPECT`-equivalent constants in `prep.py`). A
    flat catalog and a ring do not want the same margin; this is an INPUT to
    `plan_crop`, exactly like the flags it replaces, not a crop box.
  * `default_look` -- which of the RENDERED looks the shoot defaults to,
    replacing `color.default_preset_for`'s backdrop/warmth heuristic when set.
    This is not a wider power than the heuristic already had: adopting a
    default into `listing/` was already automatic (`run_apply`'s auto-pick),
    already just a default, and already fully subordinate to a deliberate
    `--pick` and to the approval gate. A category naming one look is doing the
    same thing the heuristic does, on the same authority.
  * `unskew` -- whether the (retired) unskew stage would apply to this kind of
    goods, for a round or otherwise non-rectangular item that has no rectangle
    to square. See UNSKEW below: this is currently inert everywhere, and is
    carried so a revived stage would not have to re-litigate every category.
  * `specialization` -- a cross-reference to the matching module under
    `specializations/`, if one exists (validated at import time so it can
    never point at a file that is not there). See SPECIALIZATIONS below: it is
    read-only, and nothing in this module or in PREP acts on it.

WHAT WAS DELIBERATELY LEFT OUT

`pop`. The subject-pass strength is not a category field, because it is
already not a free variable once a preset renders: every entry in
`color.PRESETS` bakes in its own `pop` level (`studio` and `half` and `tenth`
are "gentle", `punch` is "strong", `crisp` is "off"), and `color.correct` only
reads its standalone `pop` argument when NO preset is given -- which is never,
from `run_apply`. That is not a bug this module should paper over with a
second knob that would either be silently overridden by the preset's own
`pop` or would have to fight it. The `--pop` flag on the command line stays
the standalone escape hatch for `color.correct` called without a preset (as
several unit tests do); a category may not touch it. `_ALLOWED_KEYS` below
enforces that a category profile setting `pop` is a loud failure at import
time, not a silent no-op discovered later.

UNSKEW -- WHY THE FIELD ABOVE HAS NO CODE PATH YET

The unskew stage was retired repo-wide in 2026-08 (see `unskew.py`'s module
docstring): there is no `plan()` any more, for ANY category, so "does unskew
apply to this category" is already moot -- every shoot's answer is "the stage
never runs" regardless of what a profile says. The field is carried anyway,
purely as a forward-looking answer, so that if the stage is ever revived
nobody has to go back and decide, category by category, whether a round item
had a rectangle to square. `prep.py._unskewed` never reads it: that function
replays a warp a shoot was ALREADY PUBLISHED with, and a category declaring
itself unskew-inapplicable must not be able to retroactively un-publish a real
historical decision. That is the same "never assert a result" rule as the
crop box, applied to a stage that happens to be gone.

SPECIALIZATIONS -- WHY THE HOOK IS READ-ONLY

`specializations/` already carries per-category expertise for IDENTIFY --
maker-mark vocabularies, value tiers, the inspection shots a specialist would
ask for (see `specializations/README.md`). PREP and IDENTIFY are different
phases run by different mechanisms (this module is code; IDENTIFY's is a
prompt reading Markdown on demand), so `specialization` is deliberately just a
name lookup: it tells a caller which file to go read, and validates that the
file exists. It is never consulted by anything in this module or in `prep.py`
that touches a pixel, a crop box, or an approval -- doing so would let an
IDENTIFY-side judgement call (a maker guess, a value tier) reach into a
measurement it has no business influencing.

EXPLICIT, NOT INFERRED (the other open question in the issue)

The issue also asks whether a category should be inferrable from the shoot
path or the draft (e.g. anything under `more-mags-444/` is `printed`), with
`--category` as an override. This module stays explicit-only: no path or
draft sniffing. Two reasons, not one:

  * the issue's own text already calls inference the riskier of the two -- "a
    category invented from taste is a policy change... applied to hundreds of
    frames before anyone notices it was a guess" applies just as much to an
    inferred category as to an invented profile, and inference adds a second
    way to be wrong (a bad match rule) on top of the first (a bad profile);
  * this repo's own prior automated-triage passes on adjacent "should this be
    inferred or explicit" questions (elsewhere in this issue tracker) have
    landed on explicit every time a wrong guess would re-measure hundreds of
    frames before a human sees it -- which is exactly this decision's shape.

`--category` remains the one and only way a shoot gets a category, and it
persists in the manifest so a later pass that omits the flag gets the same
answer rather than silently falling back to `default` (see `prep.category_of`).

ADDING ONE

Keep it grounded. Every field should trace to something observed on a real
shoot, and the docstring should say what. A category invented from taste is a
policy change wearing a config file, and it will be applied to hundreds of
frames before anyone notices it was a guess. That standard is why this module
still ships only two categories: `jewelry`/`marbles`/`silverplate`-style
profiles were considered for this issue and set aside, because most of their
fields would have had nothing to trace to beyond "this seems reasonable" --
which is precisely the failure mode this section exists to keep out. Extending
the mechanism (this schema, and the wiring in `prep.py`) is the actual ask;
a new profile is only worth adding once its fields are grounded the same way
`printed`'s already are.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

DEFAULT_CATEGORY = "default"

# Every key a profile is allowed to carry. Anything else is a typo or a field
# that was rejected on purpose (see `pop` in the module docstring) -- both are
# a loud failure here rather than a silent no-op discovered downstream.
_ALLOWED_KEYS = frozenset({
    "label", "subject", "looks", "aspect", "pad", "default_look", "unskew",
    "specialization",
})

# Where the specialization cross-reference is validated against. Repo root is
# two parents up from lib/photo_prep/categories.py.
_SPECIALIZATIONS_DIR = Path(__file__).resolve().parents[2] / "specializations"


# Each profile is the *complete* set of overrides for its category. A key that
# is absent means "keep PREP's own default", so a profile stays readable as the
# short list of ways this category actually differs.
CATEGORIES: dict[str, dict] = {
    # `crisp` only: camera colour kept, backdrop cleaned, item sharpened hard —
    # the one look that cannot misrepresent the goods (color.default_preset_for
    # already picks it for every new item; this just stops rendering the other
    # five nobody was going to pick instead). Rendering all six cost ~25s/frame
    # EACH for a comparison that was almost never used — `--filters` at --apply
    # time renders every look when a shoot genuinely wants the comparison.
    "default": dict(
        label="crisp only — the house default, auto-applied; --filters for the full comparison",
        subject="auto",
        looks=("crisp",),
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
    #
    # No aspect/pad/default_look/unskew/specialization: nothing observed about
    # a printed shoot says its framing, its default look among the ones it
    # still renders (there is only one), or its unskew-applicability should
    # differ from the generic case, and there is no specializations/ module
    # keyed on "printed media" to point at.
    "printed": dict(
        label="flat printed goods — catalogs, magazines, sleeves",
        subject="paper",
        looks=("asshot",),
    ),
}


def _validate() -> None:
    """Fail at import time, not three re-runs later on a real batch.

    A profile is plain data, so a typo in it is not a syntax error -- it is a
    shoot that renders nothing, references a preset that does not exist, or
    (new in #23) points `specialization` at a file nobody wrote. All of that
    is cheap to catch here and expensive to catch on a live shoot.
    """
    from . import color as colormod

    for name, prof in CATEGORIES.items():
        unknown = set(prof) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(
                f"category {name!r} sets unknown field(s) {sorted(unknown)!r}. "
                f"'pop' in particular is deliberately not a category field -- "
                f"see the module docstring's WHAT WAS DELIBERATELY LEFT OUT.")

        look = prof.get("default_look")
        if look is not None and look not in colormod.PRESETS:
            raise ValueError(
                f"category {name!r} sets default_look={look!r}, which is not "
                f"a preset in color.PRESETS")
        rendered = prof.get("looks")
        if look is not None and rendered and look not in rendered:
            raise ValueError(
                f"category {name!r} sets default_look={look!r}, which its own "
                f"looks={rendered!r} never renders -- a default has to be "
                f"among the RENDERED set (see WHY `looks` IS A FILTER)")

        pad = prof.get("pad")
        if pad is not None and not (0.0 <= float(pad) < 1.0):
            raise ValueError(
                f"category {name!r} sets pad={pad!r}, outside [0, 1)")

        spec = prof.get("specialization")
        if spec is not None:
            path = _SPECIALIZATIONS_DIR / f"{spec}.md"
            if not path.is_file():
                raise ValueError(
                    f"category {name!r} points specialization at {spec!r}, "
                    f"but {path} does not exist")


_validate()


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


def aspect_for(name: Optional[str]) -> Optional[str]:
    """The category's preferred crop aspect ("W:H", or "orig"), or None.

    None means "PREP's own default (`prep.DEFAULT_ASPECT`) applies" -- the same
    convention `looks`/`subject` already use, and what keeps `default` and
    `printed` byte-for-byte unchanged by this field existing.
    """
    return profile(name).get("aspect")


def pad_for(name: Optional[str]) -> Optional[float]:
    """The category's preferred crop margin, or None for `prep.DEFAULT_PAD`."""
    pad = profile(name).get("pad")
    return float(pad) if pad is not None else None


def default_look_for(name: Optional[str]) -> Optional[str]:
    """The look this category defaults to among the ones it renders, or None.

    None means "fall back to `color.default_preset_for`'s backdrop/warmth
    heuristic" -- exactly what happens today. See the module docstring for why
    this is not a wider authority than that heuristic already had.
    """
    return profile(name).get("default_look")


def unskew_applies_for(name: Optional[str]) -> bool:
    """Whether the (retired) unskew stage would apply to this category.

    Always True unless a profile says otherwise, and -- see UNSKEW in the
    module docstring -- currently read by nothing that plans or replays a
    warp. It exists so a revived stage would not start from zero.
    """
    return bool(profile(name).get("unskew", True))


def specialization_for(name: Optional[str]) -> Optional[str]:
    """The specializations/ module this category cross-references, or None.

    Read-only. Nothing here or in `prep.py` acts on this value -- see
    SPECIALIZATIONS in the module docstring for why an IDENTIFY-side
    judgement must not reach into a PREP measurement.
    """
    return profile(name).get("specialization")


def describe(name: Optional[str]) -> str:
    p = profile(name)
    looks = p.get("looks")
    bits = [f"subject={p.get('subject', 'auto')}",
            f"looks={'all' if not looks else '+'.join(looks)}"]
    if p.get("aspect"):
        bits.append(f"aspect={p['aspect']}")
    if p.get("pad") is not None:
        bits.append(f"pad={p['pad']}")
    if p.get("default_look"):
        bits.append(f"default_look={p['default_look']}")
    if not p.get("unskew", True):
        bits.append("unskew=n/a")
    if p.get("specialization"):
        bits.append(f"specialization={p['specialization']}")
    return f"{name or DEFAULT_CATEGORY}: {p.get('label', '')} [{', '.join(bits)}]"
