"""single-pass mode — V4_PLAN Phase 4, item 2 (#30).

    python -m lib.cli single-pass <shoot-dir> [--json]

The pipeline (IDENTIFY -> PREP -> PRICE -> INVESTIGATE -> DRAFT) already runs
front-to-back with only one gate under `full`/`list` mode: REVIEW, the
publish gate (see RUN.md's gate contract). In practice it still gets driven
as separate conversational turns, one chat message per stage, on a routine
item that never needed a single one of them. This module is the fast path:
a read-only gate check across whatever each stage has already written, so an
agent (or the ebz operator) can walk a routine shoot straight through and get
ONE review card, with the conversation spent only on the item that actually
needs it.

WHAT THIS MODULE IS NOT: it does not identify, price, or draft anything.
IDENTIFY/PRICE/DRAFT are prompts — a person or an agent reads photos, prices
comps, and writes copy; there is no Python "run PRICE" to call. Reimplementing
any of that here would be exactly the bug the plan's Ground rules forbid ("a
diet that changes behavior is a bug"). What single-pass DOES own is orchestration:
did the stage already finish clean, and if every stage did, assemble the one
card REVIEW would already build. Every check below is a pure read of files
each stage was already contractually required to write (`_shared.md`'s
persistence table); none of them is invented for this feature.

STAGE ORDER. V4_PLAN/#30 shorthand the chain as "PREP->IDENTIFY->PRICE->DRAFT".
The actual dependency order, per RUN.md's `list`-mode sequence and each
stage's own "Reads" column, is IDENTIFY -> PREP(gate) -> PRICE -> INVESTIGATE
-> DRAFT (DRAFT hard-aborts without investigate.txt — see draft.md
"Preconditions"). This module follows the real order so a "clean" verdict
means what it says; STAGE_ORDER is the one place that matters and is easy to
audit against RUN.md if the pipeline order ever changes.

THE ONE THING THAT CAN STILL STOP THE CHAIN. PREP already has a confidence
gate (#36, PR #37): everything resolved and nothing guessed -> it approves
itself and shows a card as a record; anything guessed or flagged -> it asks
about THOSE frames only, by name. `check_prep` below reads exactly that
signal out of `.prep/prep.json` (`orientation.guessed`, `orientation.needs_ask`,
an un-overridden crop refusal) — no new gate, just reading the one PREP
already has.

The other stages don't have a machine-checkable manifest the way PREP does,
because their "gate" is a human judgement call baked into the prompt (a
maker's mark that isn't decisively readable; a marble shoot's crop-gate
contact sheet; the GOOD/BETTER/BEST poll when a tier swings value and the
photos can't decide it — all three already documented as the HARD,
interactive-only stops in `identify.md` / `marbles.md`). Nothing here changes
those triggers. What's new is only how the ask is reported when a stage is
run under single-pass: instead of pausing the chat, it writes
`<shoot>/.single_pass/ask.json` (see prompts/single_pass.md) and stops that
item's work; this module reads the file back. Same question, same trigger,
same honesty bar — just a file instead of a chat pause, exactly the kind of
invocation-only change the plan calls for.

PRICE never blocks (price.md: "Autonomy: no step gates or stops") and DRAFT
has no interactive gate of its own (its only hard stop is the investigate.txt
precondition, which the stage order already prevents from being reached
prematurely) — `check_price`/`check_investigate` therefore never return an
ask; they exist so the stage list stays uniform and so a future gate on
either stage has somewhere obvious to land.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Real dependency order (see module docstring "STAGE ORDER").
STAGE_ORDER: tuple[str, ...] = ("identify", "prep", "price", "investigate", "draft")

# Where each stage's finished record lives (`_shared.md` "Output-file
# persistence"). PREP's is the manifest, not `listing/`, because the manifest
# is what carries the confidence-gate signal.
STAGE_OUTPUT: dict[str, str] = {
    "identify": "identify.txt",
    "prep": ".prep/prep.json",
    "price": "price.txt",
    "investigate": "investigate.txt",
    "draft": "draft.md",
}

# The single-pass ask sentinel (see prompts/single_pass.md). A stage writes
# one entry here, tagged with its own name, the moment it hits ITS OWN
# already-documented interactive HARD stop — never a new condition.
ASK_FILE = ".single_pass/ask.json"


@dataclass(frozen=True)
class Ask:
    """One named exception a stage could not resolve on its own."""
    stage: str
    detail: str

    def __str__(self) -> str:          # pragma: no cover - trivial
        return f"{self.stage}: {self.detail}"


@dataclass
class SinglePassResult:
    shoot: Path
    clean: bool
    stages_cleared: tuple[str, ...]
    blocked_at: Optional[str] = None
    asks: tuple[Ask, ...] = ()
    review_card_md: Optional[Path] = None
    review_card_html: Optional[Path] = None

    def as_dict(self) -> dict:
        return {
            "shoot": str(self.shoot),
            "clean": self.clean,
            "stages_cleared": list(self.stages_cleared),
            "blocked_at": self.blocked_at,
            "asks": [{"stage": a.stage, "detail": a.detail} for a in self.asks],
            "review_card_md": str(self.review_card_md) if self.review_card_md else None,
            "review_card_html": str(self.review_card_html) if self.review_card_html else None,
        }

    def summary(self) -> str:
        """The terse verdict line + one ASK row per exception (Phase 2's
        `OK n/m, k flagged -> detail` convention, adapted: n/m here counts
        STAGES cleared, not the flagged items within the one that blocked)."""
        total = len(STAGE_ORDER)
        cleared = len(self.stages_cleared)
        if self.clean:
            lines = [f"single-pass: OK {cleared}/{total} -> {self.review_card_html}"]
        else:
            lines = [f"single-pass: OK {cleared}/{total}, blocked at "
                     f"{self.blocked_at} ({len(self.asks)} flagged)"]
        lines += [f"  ASK[{a.stage}] {a.detail}" for a in self.asks]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# per-stage checks — pure reads, no side effects on any stage's own files
# ---------------------------------------------------------------------------

def _pending_ask(shoot: Path, stage: str) -> list[Ask]:
    """The generic sentinel read shared by every stage with an interactive
    HARD stop of its own (see module docstring). Absent file == nothing
    pending; a malformed file is treated the same as absent rather than
    raising, because a single-pass gate check must never itself become the
    thing that crashes a routine run."""
    p = shoot / ASK_FILE
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = raw if isinstance(raw, list) else [raw]
    return [
        Ask(stage, str(e.get("detail") or e.get("question") or "unspecified"))
        for e in entries
        if isinstance(e, dict) and e.get("stage") == stage
    ]


def check_identify(shoot: Path) -> list[Ask]:
    if not (shoot / STAGE_OUTPUT["identify"]).exists():
        return [Ask("identify", "identify.txt not written yet — run IDENTIFY first.")]
    # The maker-mark stop-and-ask gate and the GOOD/BETTER/BEST poll
    # (identify.md) are the only interactive HARD stops IDENTIFY has;
    # grouping questions and needs_followup_photo are documented SOFT
    # gates (_shared.md gate contract) and never block single-pass.
    return _pending_ask(shoot, "identify")


def check_prep(shoot: Path) -> list[Ask]:
    manifest_path = shoot / ".prep" / "prep.json"
    if not manifest_path.exists():
        return [Ask("prep", "not run — `python -m lib.photo_prep.prep <shoot> --auto` first.")]
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [Ask("prep", "prep.json is not valid JSON — re-run --check.")]

    photos: dict = m.get("photos") or {}
    if not photos:
        return [Ask("prep", "manifest has no frames — run --check/--auto first.")]

    asks: list[Ask] = []
    for name in sorted(photos):
        rec = photos[name] or {}
        o = rec.get("orientation") or {}
        if o.get("needs_ask"):
            asks.append(Ask("prep", f"{name}: orientation unresolved — no legible "
                                     f"cue, needs a human look."))
        elif o.get("guessed"):
            prop = o.get("osd_proposal")
            asks.append(Ask("prep", f"{name}: orientation guessed at "
                                     f"{o.get('subject_angle')}deg (OSD proposal "
                                     f"{prop}) — confirm or correct."))
        crop = rec.get("crop") or {}
        # A refusal the pipeline made on its own (no `operator` key) is exactly
        # PREP's "crop the pipeline refused" exception; an operator's own
        # deliberate "keep as shot" is not something to ask about again.
        if crop.get("applied") is False and crop.get("reason") and not crop.get("operator"):
            asks.append(Ask("prep", f"{name}: crop refused — {crop['reason']}"))

    if asks:
        return asks
    if not m.get("approved"):
        # Everything resolved and nothing guessed is exactly the case PREP's
        # own gate auto-approves (prep.md "gate on confidence, not on the
        # operator") — this file just hasn't recorded that sign-off yet.
        return [Ask("prep", "auto pass is clean but not yet approved — run "
                             "--approve-auto (then --apply/--pick/--approve if "
                             "colour hasn't been decided).")]
    return []


def check_price(shoot: Path) -> list[Ask]:
    if not (shoot / STAGE_OUTPUT["price"]).exists():
        return [Ask("price", "price.txt not written yet — run PRICE first.")]
    # price.md is explicit: "Autonomy: no step gates or stops." A thin market
    # or a missing exact comp is logged in price.txt/NEEDS_REVIEW.md, never
    # asked — single-pass changes nothing about that.
    return []


def check_investigate(shoot: Path) -> list[Ask]:
    if not (shoot / STAGE_OUTPUT["investigate"]).exists():
        return [Ask("investigate", "investigate.txt not written yet — run "
                                    "INVESTIGATE first.")]
    return _pending_ask(shoot, "investigate")


def check_draft(shoot: Path) -> list[Ask]:
    if not (shoot / STAGE_OUTPUT["draft"]).exists():
        return [Ask("draft", "draft.md not written yet — run DRAFT first.")]
    return _pending_ask(shoot, "draft")


STAGE_CHECK: dict[str, Callable[[Path], list[Ask]]] = {
    "identify": check_identify,
    "prep": check_prep,
    "price": check_price,
    "investigate": check_investigate,
    "draft": check_draft,
}


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def _default_review_builder(draft_path: Path):
    """Wraps REVIEW's own card assembly (`list_edit.build_review_card`) —
    the same function `list_edit.py --review` calls. Imported lazily so a
    plain gate check never pulls in eBay credentials/network."""
    from list_edit import build_review_card
    return build_review_card(draft_path)


def _default_html_builder(shoot: Path) -> Path:
    """Wraps the same page REVIEW publishes (`tools/review_card_html.py`)."""
    import tools.review_card_html as review_card_html
    return review_card_html.build(shoot)


def run_single_pass(
    shoot: Path,
    *,
    review_builder: Optional[Callable[[Path], object]] = None,
    html_builder: Optional[Callable[[Path], Path]] = None,
) -> SinglePassResult:
    """Walk IDENTIFY->PREP->PRICE->INVESTIGATE->DRAFT and stop at the first
    stage that cannot resolve something on its own.

    Every stage's file is read, never written — this function makes no
    identification, pricing, or copy decision, so the same inputs produce
    exactly the same per-stage outputs whether they were made in one sitting
    or four (the plan's "a diet that changes behavior is a bug" bar). Only
    when every stage is clean does it render ONE review card, via the same
    machinery REVIEW already uses (`review_builder`/`html_builder` default to
    that; tests inject fakes to stay offline).
    """
    shoot = Path(shoot)
    cleared: list[str] = []
    for stage in STAGE_ORDER:
        asks = STAGE_CHECK[stage](shoot)
        if asks:
            return SinglePassResult(
                shoot=shoot, clean=False, stages_cleared=tuple(cleared),
                blocked_at=stage, asks=tuple(asks),
            )
        cleared.append(stage)

    review_builder = review_builder or _default_review_builder
    html_builder = html_builder or _default_html_builder

    card_md_path = shoot / "review_card.md"
    if not card_md_path.exists():
        review_builder(shoot / "draft.md")
    html_path = html_builder(shoot)

    return SinglePassResult(
        shoot=shoot, clean=True, stages_cleared=tuple(cleared),
        review_card_md=card_md_path if card_md_path.exists() else None,
        review_card_html=html_path,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="single-pass",
        description="IDENTIFY->PREP->PRICE->INVESTIGATE->DRAFT in one pass for "
                     "a routine item: ONE review card when every stage's own "
                     "confidence gate is clean, else the specific exception.")
    ap.add_argument("shoot_dir", help="the shoot directory (inventory/<name>)")
    ap.add_argument("--json", action="store_true",
                     help="machine-readable result instead of the verdict line")
    a = ap.parse_args(argv)

    shoot = Path(a.shoot_dir)
    if not shoot.is_dir():
        ap.error(f"no such shoot directory: {shoot}")

    result = run_single_pass(shoot)
    if a.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(result.summary())
    return 0 if result.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
