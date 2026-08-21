"""The decision record — what was DECIDED, separated from what was PRODUCED.

Issue #21. PREP's manifest carries both, mixed, and the only thing tying them
together is that the code usually runs in the right order. The symptoms all had
one shape:

  * a shoot sat at `approved: true` with six frames at a rotation the shipping
    files had never been rendered with;
  * `--pick asshot` reported success while `listing/` still held studio;
  * staleness was checked by hashing FILES, so a changed **decision**
    invalidated nothing — only a changed **file** did.

This module is the missing half. A decision record is:

  * **pure data** — no file paths, no pixels, no measurements that merely
    justified a decision;
  * **complete** — everything that changes the rendered bytes is in it;
  * **stable** — the same decisions serialise to the same digest, on any
    machine, in any key order.

Approvals attach to `digest(record)`. Any change to any decision therefore
invalidates every approval downstream automatically, without anyone remembering
to call an invalidator. That is the whole point: the old code had to REMEMBER to
drop sign-offs, and the places it forgot are the bug list above.

WHAT IS IN, AND WHY IT IS EXACTLY THIS

The rule is: **the digest covers what changes the output, and nothing else.**
Both directions of that matter.

Too little, and a real change slips past an approval — the paul-fredrick case.
Too much, and the record cries wolf: fold in OSD's confidence, or the backdrop
luma, or a timestamp, and re-running `--check` after a tesseract upgrade
invalidates a sign-off even though every rendered pixel is identical. An
invalidator that fires on nothing gets switched off.

So evidence stays OUT. Confidence scores, OSD notes, detector agreement,
backdrop statistics — all of that stays in the manifest, where it belongs and
where the review sheets read it. Evidence explains a decision; it is not one.

Provenance (`auto` vs `operator`) IS in, and deliberately: a box a human set and
an identical box the planner proposed are not the same decision, because
re-planning is allowed to overwrite one and not the other.

WHAT THIS MODULE DOES NOT DO

It does not render, and it does not decide. It reads the manifest and reports
what is already recorded there. Stage 2 of #21 is `render(source, decisions) ->
bytes`; this is stage 1, and the render path is deliberately untouched — it is
the hottest code in the repo and a rewrite there risks regressions that only
surface on a long batch.

    record = record_for(manifest)      # pure data
    digest(record)                     # 'sha256:...', stable
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

# Bumped when the SHAPE of the record changes in a way that should invalidate
# existing approvals. The digest covers it, so an old approval cannot silently
# match a new record that means something different.
RECORD_VERSION = 1

# Settings that reach the rendered pixels. `subject` picks which detector's mask
# the colour pass runs against and `pop` is the subject pass's strength, so both
# change bytes. `aspect` and `pad` are deliberately ABSENT: they are inputs to
# planning a crop box, and the box itself is already recorded per frame — a pad
# changed after an operator fixed a box changes nothing about what renders, and
# folding it in would invalidate that operator's sign-off for no reason.
RENDER_SETTINGS = ("subject", "pop")


# A STAGE'S APPROVAL ATTACHES TO THE DECISIONS UP TO THAT STAGE, NOT ALL OF THEM.
#
# The four stages depend on each other in one direction only, and approving one
# is what LICENSES the next to be planned -- `--approve-stage orientation` runs
# plan_geometry() itself, against the rotations just signed off. So hashing the
# whole record into the orientation approval would invalidate that approval the
# instant it was granted, by the very planning it authorised.
#
# Scoping the digest per stage gives the property that was actually wanted: a
# decision cannot change under an approval that DEPENDED on it, while a later
# stage is still free to be planned and re-planned above it.
STAGE_SCOPE = {
    "orientation": ("orientation",),
    "unskew": ("orientation", "unskew"),
    "crop": ("orientation", "unskew", "crop"),
    "color": ("orientation", "unskew", "crop"),
}

# `subject` chooses which detector's mask everything geometric is measured
# against, so it belongs from the unskew stage on. `pop` only reaches the colour
# pass. Scoping these the same way keeps the same promise: changing the detector
# invalidates the boxes measured with it, and changing the subject pass does not.
STAGE_SETTINGS = {
    "orientation": (),
    "unskew": ("subject",),
    "crop": ("subject",),
    "color": ("subject", "pop"),
}


def _orientation(rec: dict) -> dict:
    """The turn that will be applied, and who is answerable for it.

    `applied` rather than the exif and subject halves separately: it is the
    number the render uses, and recording both halves as well would let the
    record disagree with itself. The halves stay in the manifest for the sheet.
    """
    o = rec.get("orientation") or {}
    src = o.get("source") or "unresolved"
    return {
        "applied": int(o.get("applied") or 0),
        # A recorded look is an operator decision; EXIF and OSD are automatic.
        "by": "operator" if "vision" in src else "auto",
        "unresolved": bool(o.get("needs_ask")),
    }


def _unskew(rec: dict) -> dict:
    sk = rec.get("unskew") or {}
    if not sk.get("applied"):
        return {"applied": False, "by": "operator" if sk.get("operator") else "auto"}
    # The angle is rounded because it is a float from a fit, and a reserialise
    # on another machine must not produce a different digest for one decision.
    ang = sk.get("angle")
    return {
        "applied": True,
        "angle": round(float(ang), 4) if ang is not None else None,
        "quad": [[int(round(v)) for v in pt] for pt in (sk.get("quad") or [])],
        "by": "operator" if sk.get("operator") else "auto",
    }


def _crop(rec: dict) -> dict:
    c = rec.get("crop") or {}
    if not c.get("applied"):
        return {"applied": False, "by": "operator" if c.get("operator") else "auto"}
    return {
        "applied": True,
        "box": [int(v) for v in (c.get("box") or [])],
        "by": "operator" if c.get("operator") else "auto",
    }


def frame_decisions(rec: dict) -> dict:
    """Every decision for one frame. No paths, no pixels, no evidence."""
    return {
        "orientation": _orientation(rec),
        "unskew": _unskew(rec),
        "crop": _crop(rec),
    }


def record_for(m: dict, stage: Optional[str] = None) -> dict:
    """The shoot's decision record, as pure data.

    Frames are keyed by basename. That is identity, not a path — the record says
    nothing about where the shoot lives, so the same decisions taken on a copied
    directory produce the same digest.

    `stage` narrows the record to the decisions that stage depends on, so an
    approval can attach to exactly those. Without it you get the full record,
    which is what the final gate compares.
    """
    if stage is not None and stage not in STAGE_SCOPE:
        raise ValueError(f"unknown stage {stage!r}; use one of "
                         f"{', '.join(STAGE_SCOPE)}")
    fields = STAGE_SCOPE.get(stage) if stage else ("orientation", "unskew", "crop")
    keys = STAGE_SETTINGS.get(stage) if stage else RENDER_SETTINGS
    settings = m.get("settings") or {}
    rec_out = {
        "version": RECORD_VERSION,
        "stage": stage or "all",
        "settings": {k: settings.get(k) for k in keys},
        "frames": {name: {f: frame_decisions(rec)[f] for f in fields}
                   for name, rec in sorted((m.get("photos") or {}).items())},
    }

    # The look belongs to the colour stage and to the full record, and nowhere
    # earlier. Folding it into the geometry stages would mean that picking a
    # look invalidated the crop sign-off, which is backwards: the crop is what
    # the look is chosen ON TOP OF, and every preset renders from the same box.
    if stage in (None, "color"):
        # One decision for the whole shoot, which is what the manifest already
        # models and why --pick takes no frame argument.
        rec_out["look"] = {
            "preset": m.get("chosen_preset"),
            # An auto-adopted default and a deliberate --pick are different
            # decisions even when they name the same preset: only one of them
            # survives the next --apply. That distinction is precisely what the
            # auto-pick race destroyed.
            "by": "operator" if m.get("preset_picked_by_operator") else "auto",
        }
    return rec_out


def canonical(record: dict) -> str:
    """The exact bytes that get hashed. Sorted keys, no incidental whitespace."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def digest(record: dict) -> str:
    return "sha256:" + hashlib.sha256(
        canonical(record).encode("utf-8")).hexdigest()


def digest_for(m: dict, stage: Optional[str] = None) -> str:
    return digest(record_for(m, stage))


def stamp(m: dict, stage: Optional[str] = None) -> dict:
    """The approval payload for a stage: the decisions, and their digest.

    One function so the two can never drift apart -- a hash stored without the
    record it came from cannot explain itself later, and a record stored without
    its hash has to be re-serialised to be compared.
    """
    rec = record_for(m, stage)
    return {"decision_hash": digest(rec), "decisions": rec}


def stale_stages(m: dict, stages) -> list:
    """Approved stages whose decisions have changed since they were signed off.

    Returns [(stage, [what changed, ...])]. This is the invalidation the old
    manifest could not do: it compared FILES, so a rotation that changed after
    approval left `approved: true` standing over frames the shipping files had
    never been rendered with.

    A stage approved before decision hashes existed carries none, and is left
    alone rather than reported stale -- ~150 manifests predate this, and
    invalidating all of them at once would train everyone to ignore the
    message. They re-stamp on their next approval.
    """
    out = []
    for stage in stages:
        st = (m.get("stages") or {}).get(stage) or {}
        if not st.get("approved"):
            continue
        was = st.get("decision_hash")
        if not was:
            continue
        now_rec = record_for(m, stage)
        if was == digest(now_rec):
            continue
        # The record as approved is stored beside the hash precisely so this
        # can say WHAT moved. "approval is stale" with no reason is the message
        # that gets worked around; "IMG_4.jpg orientation 0 -> 90" gets fixed.
        then_rec = st.get("decisions")
        why = diff(then_rec, now_rec) if then_rec else []
        out.append((stage, why or ["decisions changed since sign-off"]))
    return out


def diff(a: dict, b: dict) -> list:
    """What changed between two records, in words.

    Exists so an invalidation can say WHY. "approval is stale" with no reason is
    the message that gets worked around; "IMG_4.jpg orientation 0 -> 90" is the
    one that gets fixed.
    """
    out = []
    if a.get("version") != b.get("version"):
        out.append(f"record version {a.get('version')} -> {b.get('version')}")
    for k in RENDER_SETTINGS:
        av = (a.get("settings") or {}).get(k)
        bv = (b.get("settings") or {}).get(k)
        if av != bv:
            out.append(f"settings.{k} {av!r} -> {bv!r}")
    al, bl = a.get("look") or {}, b.get("look") or {}
    if al != bl and (al or bl):
        out.append(f"look {al.get('preset')!r} ({al.get('by')}) -> "
                   f"{bl.get('preset')!r} ({bl.get('by')})")

    af, bf = a.get("frames") or {}, b.get("frames") or {}
    for name in sorted(set(af) | set(bf)):
        if name not in af:
            out.append(f"{name}: added")
            continue
        if name not in bf:
            out.append(f"{name}: removed")
            continue
        for stage in ("orientation", "unskew", "crop"):
            x, y = af[name].get(stage), bf[name].get(stage)
            if x != y:
                out.append(f"{name} {stage}: {_short(x)} -> {_short(y)}")
    return out


def _short(d: Optional[dict]) -> str:
    if not d:
        return "none"
    if d.get("applied") is False:
        return f"off ({d.get('by')})"
    if "box" in d:
        return f"box {d['box']} ({d.get('by')})"
    if "angle" in d:
        return f"{d.get('angle')}deg ({d.get('by')})"
    if "applied" in d:
        return f"{d['applied']} ({d.get('by')})"
    return str(d)
