"""webapp/actions.py — the action table (#311 slice 3): every button in the
app that runs a pipeline command is one row here, not a handler of its own.

    ACTIONS = {name: Action(argv, slots, writes_ebay, needs_confirm)}

A row's `argv` is the command a human would type, with `{slot}` holes. The
browser sends only the action's name and its slot values; each value is
checked against its slot's kind before it goes anywhere near an argv, and the
command runs as `python -m <module> <argv>` from the repo root, without a
shell — the app never re-implements what the tool does.

Slice 3 is LOCAL ACTIONS ONLY: PREP stage flags, the hero montage, `listing
--record` and `--set-hero`. None of them calls eBay (`listing --record` and
`--set-hero` say "No creds" in lib/list_edit.py), so every row is
`writes_ebay=False, needs_confirm=False`, and `run()` refuses any row that
isn't — the dry-run-then-confirm path for eBay writes is slice 8's.

A job's params are the action name and the raw slots, never an argv: the
handler re-validates on the worker, so `POST /api/jobs {"type": "action"}`
can't smuggle in a command the table doesn't hold.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import webapp.jobs as _jobs             # noqa: E402

# Same ceiling as webapp/jobs.py's prep --auto: under the 600 s tool wall.
ACTION_TIMEOUT = 590

STAGES = ("orientation", "crop", "color")   # lib/photo_prep/stages.STAGES
DEGREES = ("0", "90", "180", "270")
LAYOUTS = ("auto", "inset", "split")

# A frame or photo name as the manifest and draft write them: plain file
# names, optionally under a folder. Never starts with "-" (it would read as a
# flag) and never holds "=" (the NAME=VALUE separator).
_NAME = re.compile(r"^(?!-)[A-Za-z0-9_. ()+-]+(?:/[A-Za-z0-9_. ()+-]+)*$")
_CROP = re.compile(r"^(?:off|on|pad(?:0?\.\d{1,3})?)$")
_PRESET = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_FRAMES = re.compile(r"^\d{1,3}(?:,\d{1,3}){0,2}$")
MAX_FRAMES = 200


class SlotError(ValueError):
    """A slot value the action can't take. The route answers 400 with it."""


def _name(v: str, what: str) -> str:
    if not isinstance(v, str) or not _NAME.match(v) or ".." in v.split("/"):
        raise SlotError(f"{what}: not a frame name: {v!r}")
    return v


def _slot_shoot(v) -> str:
    try:
        return str(_jobs._resolve_shoot_dir(v if isinstance(v, str) else ""))
    except ValueError as exc:
        raise SlotError(f"shoot: {exc}") from exc


def _slot_stage(v) -> str:
    if v not in STAGES:
        raise SlotError(f"stage: one of {', '.join(STAGES)}, not {v!r}")
    return v


def _pairs(v, check_value: Callable[[str], bool], what: str) -> list[str]:
    if not isinstance(v, list) or not v or len(v) > MAX_FRAMES:
        raise SlotError(f"{what}: a non-empty list of NAME=VALUE")
    out = []
    for item in v:
        if not isinstance(item, str) or item.count("=") != 1:
            raise SlotError(f"{what}: not NAME=VALUE: {item!r}")
        name, val = item.split("=")
        _name(name, what)
        if not check_value(val):
            raise SlotError(f"{what}: bad value for {name}: {val!r}")
        out.append(item)
    return out


def _slot_frame_deg(v) -> list[str]:
    return _pairs(v, lambda d: d in DEGREES, "frames")


def _slot_frame_crop(v) -> list[str]:
    return _pairs(v, lambda c: bool(_CROP.match(c)), "frames")


def _slot_preset(v) -> str:
    from lib.photo_prep import color as colormod
    if not isinstance(v, str) or not _PRESET.match(v) or (
            v not in colormod.PRESETS and v != "asshot"):
        raise SlotError(f"preset: not a colour look: {v!r}")
    return v


def _slot_photo(v) -> str:
    return _name(v, "photo")


def _slot_layout(v) -> str:
    if v not in LAYOUTS:
        raise SlotError(f"layout: one of {', '.join(LAYOUTS)}, not {v!r}")
    return v


def _slot_frame_numbers(v) -> str:
    if not isinstance(v, str) or not _FRAMES.match(v):
        raise SlotError(f"frames: like 1,4 (frame numbers), not {v!r}")
    return v


SLOT_KINDS: dict[str, Callable] = {
    "shoot": _slot_shoot,
    "stage": _slot_stage,
    "frame_deg": _slot_frame_deg,
    "frame_crop": _slot_frame_crop,
    "preset": _slot_preset,
    "photo": _slot_photo,
    "layout": _slot_layout,
    "frame_numbers": _slot_frame_numbers,
}


@dataclass(frozen=True)
class Action:
    label: str
    argv: tuple[str, ...]                       # "{slot}" = one value; a list slot expands in place
    slots: dict[str, str]                       # slot name -> kind (SLOT_KINDS)
    optional: dict[str, tuple[str, ...]] = field(default_factory=dict)  # slot -> argv it adds when given
    module: str = "lib.cli"
    writes_ebay: bool = False
    needs_confirm: bool = False


ACTIONS: dict[str, Action] = {
    # PREP stage flags (lib/photo_prep/prep.py) — the art director's calls,
    # the same flags the Frame Check page's Accept panel writes out.
    "prep-approve-stage": Action(
        "Approve this PREP stage as shown",
        ("prep", "{shoot}", "--approve-stage", "{stage}"),
        {"shoot": "shoot", "stage": "stage"}),
    "prep-set-rotate": Action(
        "Set frame rotations",
        ("prep", "{shoot}", "--set-rotate", "{frames}"),
        {"shoot": "shoot", "frames": "frame_deg"}),
    "prep-crop": Action(
        "Set frame crops",
        ("prep", "{shoot}", "--crop", "{frames}"),
        {"shoot": "shoot", "frames": "frame_crop"}),
    "prep-pick": Action(
        "Pick the colour look",
        ("prep", "{shoot}", "--pick", "{preset}"),
        {"shoot": "shoot", "preset": "preset"}),
    "prep-apply": Action(
        "Render listing/ and the review sheet",
        ("prep", "{shoot}", "--apply"),
        {"shoot": "shoot"}),
    # Hero montage (lib/photo_prep/hero_montage.py): propose writes a proposal
    # for review; apply puts it first in listing/.
    "hero-montage-propose": Action(
        "Propose a hero montage",
        ("propose", "{shoot}"),
        {"shoot": "shoot"},
        optional={"frames": ("--frames", "{frames}"), "layout": ("--layout", "{layout}")},
        module="lib.photo_prep.hero_montage"),
    "hero-montage-apply": Action(
        "Use the proposed hero montage",
        ("apply", "{shoot}", "--repoint"),
        {"shoot": "shoot"},
        optional={"frames": ("--frames", "{frames}"), "layout": ("--layout", "{layout}")},
        module="lib.photo_prep.hero_montage"),
    # LIST/EDIT, credential-free (lib/list_edit.py). The store is the draft's
    # own (stores.draft_store), so neither takes one from the page.
    "listing-record": Action(
        "Record the draft (SKU + ledger row)",
        ("listing", "--record", "{shoot}"),
        {"shoot": "shoot"}),
    "listing-set-hero": Action(
        "Make this photo the cover",
        ("listing", "--set-hero", "{shoot}", "{photo}"),
        {"shoot": "shoot", "photo": "photo"}),
}

OPTIONAL_KINDS = {"frames": "frame_numbers", "layout": "layout"}


def get(name: str) -> Action:
    if name not in ACTIONS:
        raise KeyError(f"no action {name!r}")
    return ACTIONS[name]


def validate(name: str, slots: dict) -> dict:
    """Every slot the action needs, each checked by its kind; nothing else.
    Raises KeyError for an unknown action, SlotError for a bad slot."""
    act = get(name)
    if not isinstance(slots, dict):
        raise SlotError("slots: an object")
    extra = set(slots) - set(act.slots) - set(act.optional)
    if extra:
        raise SlotError(f"{name} takes no {', '.join(sorted(extra))}")
    out = {}
    for slot, kind in act.slots.items():
        if slot not in slots:
            raise SlotError(f"{name} needs {slot}")
        out[slot] = SLOT_KINDS[kind](slots[slot])
    for slot in act.optional:
        if slots.get(slot) not in (None, ""):
            out[slot] = SLOT_KINDS[OPTIONAL_KINDS[slot]](slots[slot])
    return out


def _fill(template: tuple[str, ...], values: dict) -> list[str]:
    out: list[str] = []
    for tok in template:
        if tok.startswith("{") and tok.endswith("}"):
            v = values[tok[1:-1]]
            out.extend(v if isinstance(v, list) else [v])
        else:
            out.append(tok)
    return out


def build_argv(name: str, slots: dict) -> list[str]:
    """The full command, `[python, -m, module, ...]`, from validated slots."""
    act = get(name)
    values = validate(name, slots)
    argv = _fill(act.argv, values)
    for slot, extra in act.optional.items():
        if slot in values:
            argv += _fill(extra, values)
    return [sys.executable, "-m", act.module, *argv]


def run_job(params: dict) -> dict:
    """The queue handler for job type "action". Re-validates from the raw
    slots on the worker, then runs the command from the repo root."""
    name = params.get("action", "")
    act = get(name)
    if act.writes_ebay or act.needs_confirm:
        raise RuntimeError(f"{name} writes to eBay; that path is not built yet")
    argv = build_argv(name, params.get("slots") or {})
    proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True,
                          timeout=ACTION_TIMEOUT)
    return {
        "action": name,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def as_dict() -> list[dict]:
    """The table for GET /api/actions — names, labels and slot kinds only."""
    return [{"name": n, "label": a.label, "slots": dict(a.slots),
             "optional": sorted(a.optional), "writes_ebay": a.writes_ebay,
             "needs_confirm": a.needs_confirm} for n, a in ACTIONS.items()]
