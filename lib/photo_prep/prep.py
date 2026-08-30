"""PREP — one pass that makes a shoot's photos listing-ready, and one gate.

Replaces the hand-chained sequence (`no-exif/` -> `evened/` -> `trimmed/` ->
`cropped/`) that DRAFT used to describe. That chain was the problem: four
subdirectories that all look plausible, a lexicographic photo picker at the
end, and no record of what any step did. It is where 66 sideways photos hid
until buyers complained.

So this does three things per frame — orientation, crop, colour — and writes:

    <shoot>/listing/           the ONLY directory DRAFT reads
    <shoot>/.prep/prep.json    what was done to each frame, and the approval stamp
    <shoot>/.prep/prep_review.jpg   before | after contact sheet
    <shoot>/.prep/ask/         4-way panels for frames whose orientation is unresolved

Originals are never modified.

The gate is `approved` in the manifest, and it is meant to be enforced in code
(`assert_approved`), not only in a prompt. An unapproved shoot cannot reach
eBay Picture Services even if every prompt instruction is ignored — the same
defence-in-depth shape as `list_edit.py --confirm`.

Two passes, because orientation may need eyes:

    python -m lib.photo_prep.prep <shoot> --check
        Reads EXIF + runs OSD, segments, plans the crop and colour move for
        every frame. Writes the manifest, the rotation sheet and any 4-way
        panels. Renders nothing.

    python -m lib.photo_prep.prep <shoot> --rotate DSC_0212.JPG=90 …
        Records a looked-at answer for frames the objective sources could not
        resolve. Persisted in the manifest, so it survives re-runs.

    python -m lib.photo_prep.prep <shoot> --apply
        Renders EVERY frame through EVERY preset (studio / punch) into
        .prep/presets/, builds prep_presets.jpg — one row per frame, the
        original beside each look — and adopts the backdrop's default look into
        listing/ (punch on dark cloth, studio otherwise).

    python -m lib.photo_prep.prep <shoot> --pick studio
        Override that default. Copies the chosen look into listing/ and rebuilds
        the before|after sheet for it.

    python -m lib.photo_prep.prep <shoot> --approve
        Run ONLY on the operator's explicit yes at the gate. Refuses while any
        frame is unresolved or no preset has been picked.

Presets cost almost nothing to offer: segmentation dominates the runtime and is
shared, so both render from one mask and one crop box — which is also what makes
them comparable. Picking a look is a default, never an approval: the gate is
still a separate, explicit yes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from . import color as colormod
from . import decisions as decmod
from . import orientation as orientmod
from . import stages as stagemod
from . import categories as catmod
from . import subject as subjectmod
from . import unskew as skewmod
from .subject import mask_for
try:
    from ..verdict import emit as verdict_emit
except ImportError:          # lib/ itself on sys.path: photo_prep is top-level
    from verdict import emit as verdict_emit

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}
MANIFEST_VERSION = 1

MIN_AGREEMENT = 0.75     # detector bbox containment below which we do not crop
MIN_LONG_SIDE = 1400     # eBay wants >=1600 for zoom; never crop below this
DEFAULT_ASPECT = "1:1"
# How much backdrop a crop keeps around the item, as a fraction of the item's
# own box on each side. It was 0.12, which frames the goods handsomely and reads
# as aggressive on a listing: a magazine cropped to a 12% margin looks trimmed
# to the bleed, and any error in the mask lands ON the item. A crop should TRIM
# THE EDGES, not reframe the shot, so the margin is generous by default and
# `--crop NAME=padF` is there for the frame that wants it tight.
DEFAULT_PAD = 0.28

# And a floor under the whole box: a crop may not keep less than this much of
# the frame it came from. The pad is measured off the ITEM, so a small item in a
# big frame still yields a keyhole from a generous pad — 28% of a matchbook is
# nothing. This is the rule in the operator's words: trim a little around the
# edges, always leave some background.
MIN_FRAME_KEPT = 0.55
DEFAULT_SUBJECT = "auto"
DEFAULT_CATEGORY = catmod.DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def _load_bgr(path: Path) -> np.ndarray:
    """Decode anything in IMG_EXTS to a BGR array, HEIC included.

    Goes through PIL rather than cv2.imread so pillow-heif's opener applies and
    non-ASCII Windows paths work.
    """
    from PIL import Image
    # HEIC support is optional. The import used to be unconditional, so a
    # JPEG-only workflow — and CI, which has no reason to install a HEIC
    # library — died on `import pillow_heif` before it ever opened a file.
    # Register the opener when it is available and carry on when it is not:
    # every other format PIL handles natively is unaffected.
    try:
        import pillow_heif
    except ModuleNotFoundError:
        pass
    else:
        pillow_heif.register_heif_opener()
    with Image.open(path) as im:
        rgb = np.array(im.convert("RGB"))
    return rgb[:, :, ::-1].copy()


def _save_bgr(path: Path, bgr: np.ndarray, quality: int = 94) -> None:
    """Encode and write, atomically.

    Same tmp-and-rename shape `save_manifest` already uses, for the same
    reason: a killed process must never leave a plausible-looking truncated
    JPEG at the real path. `--resume`'s staleness check (docs/prep-resume-
    plan.md item 2) trusts "the file exists with the recorded hash" as proof
    a render finished; a straight write to the target path could instead
    leave a file that exists, matches nothing, but LOOKS like a finished
    render until something re-hashes it.
    """
    import cv2
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"could not encode {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        buf.tofile(str(tmp))
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _subject_region(bgr: np.ndarray, bbox: tuple, pad: float = 0.04) -> np.ndarray:
    """The subject's bbox with a little air — what OSD should actually read."""
    H, W = bgr.shape[:2]
    x, y, w, h = bbox
    px, py = int(w * pad), int(h * pad)
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(W, x + w + px), min(H, y + h + py)
    if x1 - x0 < 32 or y1 - y0 < 32:
        return bgr
    return bgr[y0:y1, x0:x1]


def _thumb(bgr: np.ndarray, long_side: int = 640) -> np.ndarray:
    """A small copy for the contact sheets — full-res frames held per shoot ran
    to hundreds of MB, and a 90-degree turn on a thumbnail is exact anyway."""
    import cv2
    h, w = bgr.shape[:2]
    s = min(1.0, long_side / max(h, w))
    return (cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if s < 1.0 else bgr)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _apply_settings_hash(aspect, pad: float, pop: str, subject: str,
                         category: str, only) -> str:
    """Fingerprint of the inputs that decide what `--apply` renders.

    Same truncated-sha256 convention as `_sha256()`/`_manifest_fingerprint()`
    -- 16 hex chars, not a full digest. Stored as `apply_run.settings_hash`
    (docs/prep-resume-plan.md item 2) so a later `--resume` can tell "this is
    an answer to the question I'm asking" from "this answered a different
    one" -- a changed aspect, pad, pop, subject, category or preset set means
    the old renders don't answer this invocation's question, exactly like a
    geometry/category change already invalidates crop/colour sign-off
    elsewhere in this file.
    """
    payload = json.dumps([aspect, pad, pop, subject, category,
                          sorted(only)], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Where a shoot's source frames may live when the originals are gone, in the
# order we would rather have them. `listing-photos/` and `no-exif/` hold derived
# copies from earlier workflows — EXIF already baked, otherwise full size — and
# for several live listings they are the ONLY surviving frames. Preferring the
# originals and falling back to these is what lets those listings be processed
# at all; refusing them just leaves the listing unfixed.
SOURCE_FALLBACKS = ("listing-photos", "no-exif")


REDACTED_SUFFIX = "_redacted"


def _superseded_by_redaction(paths: list[Path]) -> set:
    """Stems that must NOT be prepped because a redacted version exists.

    When a frame shows a mailing label, an invoice or anything else carrying a
    real person's details, the fix is a redacted copy saved alongside the
    original as `<stem>_REDACTED.jpg`. The draft then points at the redacted
    one — but PREP was preparing BOTH and writing both into `listing/`, which
    is the directory DRAFT reads and the uploader ships from. One lexicographic
    photo picker, one re-run of an older draft, and the un-redacted customer
    address goes public.

    The redacted copy is not an extra frame. It REPLACES its original, and the
    original has no business in the shipping directory at all.
    """
    stems = {p.stem.lower() for p in paths}
    return {s[:-len(REDACTED_SUFFIX)] for s in stems if s.endswith(REDACTED_SUFFIX)}


def find_images(shoot: Path) -> list[Path]:
    """The shoot's source frames. Top-level originals win.

    Never recurses into listing/ or .prep/ — those are our own output, and
    reading them back would compound a correction on top of a correction.

    A frame with a `_REDACTED` counterpart is dropped in favour of it.
    """
    candidates = [p for p in sorted(shoot.iterdir())
                  if p.is_file() and p.suffix.lower() in IMG_EXTS]
    for sub in SOURCE_FALLBACKS:
        d = shoot / sub
        if d.is_dir():
            candidates += [p for p in sorted(d.iterdir())
                           if p.is_file() and p.suffix.lower() in IMG_EXTS]
    redacted_away = _superseded_by_redaction(candidates)

    out, seen = [], set()
    for p in sorted(shoot.iterdir()):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            if p.stem.lower() in redacted_away:
                continue
            out.append(p)
            seen.add(p.stem.lower())

    # UNION, not either/or. j-crew/6 keeps 5 originals at the top level while
    # the other 7 frames of the same listing survive only in no-exif/ — an
    # all-or-nothing fallback silently processed 5 of 12 and the repoint then
    # refused, which is right but leaves the listing stuck. Add any fallback
    # frame whose stem is not already covered by an original.
    for sub in SOURCE_FALLBACKS:
        d = shoot / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if (p.is_file() and p.suffix.lower() in IMG_EXTS
                    and p.stem.lower() not in seen
                    and p.stem.lower() not in redacted_away):
                out.append(p)
                seen.add(p.stem.lower())
    return out


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def manifest_path(shoot: Path) -> Path:
    return shoot / ".prep" / "prep.json"


# What the manifest looked like when we read it. Stamped into the loaded dict
# and popped before writing, so it never reaches disk and never widens the
# format. Its only job is to let save_manifest tell "nobody touched this" from
# "someone did".
READ_FINGERPRINT = "_read_fingerprint"


class ManifestConflict(RuntimeError):
    """The manifest changed on disk between our read and our write.

    Raised rather than resolved. Whoever holds the stale copy cannot know which
    of the two sets of decisions the operator meant, and merging them silently
    is how a look got adopted that nobody picked.
    """


def _manifest_fingerprint(p: Path) -> Optional[str]:
    """A hash of the bytes on disk, or None when there are none."""
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def load_manifest(shoot: Path) -> dict:
    p = manifest_path(shoot)
    if p.exists():
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
            m[READ_FINGERPRINT] = _manifest_fingerprint(p)
            return m
        except Exception:
            pass
    return {
        "version": MANIFEST_VERSION,
        "shoot": shoot.name,
        "created": _now(),
        "updated": _now(),
        "approved": False,
        "approved_at": None,
        "settings": {},
        "photos": {},
        READ_FINGERPRINT: None,          # we read an absence; it must stay absent
    }


def subject_mode(m: dict) -> str:
    """Which detector this shoot believes — see subject.mask_for.

    Read from the manifest at every call site rather than threaded as an
    argument: it is a property of what is in front of the camera, and a frame
    re-segmented later (at --apply, or for one operator recrop) has to be
    segmented the same way the approved sheet was.
    """
    return m.get("settings", {}).get("subject", DEFAULT_SUBJECT)


def category_of(m: dict) -> str:
    """Which category this shoot declared — see photo_prep.categories.

    Read back from the manifest for the same reason `subject_mode` is: it is a
    statement about the goods, so a later pass that did not repeat the flag
    must not quietly fall back to the generic profile and render five looks
    nobody asked for.
    """
    return m.get("settings", {}).get("category", DEFAULT_CATEGORY)


def save_manifest(shoot: Path, m: dict, force: bool = False) -> None:
    """Write the manifest, refusing to clobber another writer.

    PREP is not run by one process at a time. Batches run in a pool, an
    operator runs a command against a shoot a background --apply is halfway
    through, and more than one agent works the same tree. This used to be a
    bare write_text: last writer won, silently.

    It cost three incidents in two days. Twice a background --apply's auto-pick
    landed after an operator's --pick, so the manifest named one look while
    listing/ held another and --pick reported success either way. Once a
    concurrent session overwrote a full set of eight orientation calls, which
    only surfaced because a contact sheet was rendered afterwards and looked
    wrong.

    So: compare-and-swap. A writer that read fingerprint X refuses to overwrite
    a manifest now at Y, and raises instead of merging — whoever holds the stale
    copy cannot know which of the two sets of decisions was meant. Callers
    re-read and retry; that is the whole protocol.

    The write itself is tmp-and-rename. It was not atomic either, so a crash
    mid-write left a truncated manifest rather than the previous one.

    `force` exists for the deliberate overwrite. It is never the default.
    """
    p = manifest_path(shoot)
    if not force and READ_FINGERPRINT in m:
        expected, actual = m[READ_FINGERPRINT], _manifest_fingerprint(p)
        if expected != actual:
            what = ("created by another writer" if expected is None else
                    "gone" if actual is None else "changed under us")
            raise ManifestConflict(
                f"{p} was {what} since it was read "
                f"(read {expected}, now {actual}). Re-read the manifest and "
                f"re-apply the change; nothing has been written.")

    m["updated"] = _now()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in m.items() if k != READ_FINGERPRINT}
    # Serialise BEFORE touching the disk, so an unserialisable value fails
    # without having created anything, and clean up the temp file if the write
    # itself dies — a stray .tmp never replaces the manifest, but it does make
    # the next person wonder whether it should have.
    text = json.dumps(body, indent=2)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    # Re-stamp, so a caller that saves the same dict twice is not told its own
    # write was somebody else's.
    if READ_FINGERPRINT in m:
        m[READ_FINGERPRINT] = _manifest_fingerprint(p)


# ---------------------------------------------------------------------------
# The gate (importable — this is what other code should call)
# ---------------------------------------------------------------------------

class PrepGateError(RuntimeError):
    """Photos are not cleared to leave the machine."""


def assert_approved(shoot: Path) -> dict:
    """Raise unless this shoot's photos were prepped AND explicitly approved.

    Staleness counts as unapproved: if a source or an output changed since the
    approval, the thing the operator looked at is not the thing about to be
    uploaded, and the approval does not carry over.
    """
    shoot = Path(shoot)
    m = load_manifest(shoot)
    if not m.get("photos"):
        raise PrepGateError(
            f"{shoot.name}: photos have not been prepped — run "
            f"`python -m lib.photo_prep.prep {shoot} --check`")
    if not m.get("approved"):
        raise PrepGateError(
            f"{shoot.name}: photo prep is not approved (HARD gate). Review "
            f"{shoot / '.prep' / 'prep_review.jpg'}, then --approve.")

    # A CHANGED DECISION IS STALENESS, NOT JUST A CHANGED FILE.
    #
    # This function used to hash only sources and outputs, so a rotation edited
    # after sign-off left `approved: true` standing over frames the shipping
    # files had never been rendered with -- six of them, on paul-fredrick.
    # Comparing the decision record catches that class structurally (#21).
    dstale = decmod.stale_stages(m, stagemod.STAGES)
    if dstale:
        why = "; ".join(f"{stage}: {reasons[0]}" for stage, reasons in dstale[:3])
        raise PrepGateError(
            f"{shoot.name}: decisions changed since sign-off -- {why}"
            f"{' ...' if len(dstale) > 3 else ''}. Re-open the affected stage, "
            f"re-approve, and re-run --apply.")

    stale = []
    for name, rec in m["photos"].items():
        src = shoot / name
        if not src.exists() or _sha256(src) != rec.get("src_sha256"):
            stale.append(f"{name} (source changed)")
            continue
        if not rec.get("output"):
            stale.append(f"{name} (no preset picked)")
            continue
        out = shoot / rec["output"]
        if not out.exists() or _sha256(out) != rec.get("out_sha256"):
            stale.append(f"{name} (prepped file changed or missing)")
    if stale:
        raise PrepGateError(
            f"{shoot.name}: approval is stale — {'; '.join(stale[:4])}"
            f"{' …' if len(stale) > 4 else ''}. Re-run --apply and re-approve.")
    return m


# ---------------------------------------------------------------------------
# Per-frame planning
# ---------------------------------------------------------------------------

def _focal_dict(sm, shape) -> dict:
    """Adapt a SubjectMask to the dict `center_crop`'s guards already speak.

    Reusing `crop_warning` rather than reimplementing it keeps one copy of the
    safety rules — the ones tuned on the shoot where 20 of 38 frames were
    destroyed — instead of two that can drift.
    """
    H, W = shape[:2]
    x, y, w, h = sm.bbox
    m = sm.mask > 0
    total = int(m.sum())
    inside = int(m[y:y + h, x:x + w].sum())
    cx, cy = x + w / 2.0, y + h / 2.0
    short = float(min(H, W))
    return {
        "w": W, "h": H, "bbox": (x, y, w, h), "cx": cx, "cy": cy,
        "offset": (((cx - W / 2.0) ** 2 + (cy - H / 2.0) ** 2) ** 0.5) / short,
        "subject_frac": (w * h) / float(W * H),
        "capture": (inside / total) if total else 0.0,
        "box_aspect": (w / h) if h else float("inf"),
        "border_fg": sm.border_fg,
    }


def _fit_box(fp: dict, aspect: Optional[float], pad: float) -> tuple:
    """Smallest box of the target aspect that CONTAINS the padded subject,
    slid as close to frame-centre as the frame allows.

    `center_crop._crop_box` insists the crop stay exactly centred on the subject
    centroid, and shrinks the box whenever that would run off an edge. On a
    portrait magazine lying near the top of a landscape frame the shrink eats
    the item, the guard then refuses the crop, and nothing gets cropped at all —
    3 of 4 frames in the first real shoot.

    Containment is the requirement; centring is the preference. So: size the box
    to hold the subject, then TRANSLATE it into the frame rather than shrinking
    it. The subject stays whole, and the framing is as centred as geometry
    permits. Shrinking only happens when the box is larger than the frame
    itself, and `crop_warning` still gets the last word on the result.
    """
    W, H = fp["w"], fp["h"]
    x, y, bw, bh = fp["bbox"]

    pw, ph = bw * (1 + 2 * pad), bh * (1 + 2 * pad)
    if aspect is None:
        cw, ch = pw, ph
    elif pw / ph > aspect:
        cw, ch = pw, pw / aspect
    else:
        ch, cw = ph, ph * aspect

    # A crop trims edges; it does not reframe. Grow the box back out until it
    # keeps at least MIN_FRAME_KEPT of the frame — the pad alone cannot promise
    # that, because it is measured off the item, and a small item in a big frame
    # yields a keyhole whatever the pad says.
    if cw * ch < MIN_FRAME_KEPT * W * H:
        g = ((MIN_FRAME_KEPT * W * H) / (cw * ch)) ** 0.5
        cw, ch = cw * g, ch * g

    # Too big for the frame: shrink to fit, keeping the aspect.
    if cw > W or ch > H:
        s = min(W / cw, H / ch)
        cw, ch = cw * s, ch * s

    # Centre on the subject, then slide back inside the frame.
    cx, cy = x + bw / 2.0, y + bh / 2.0
    x0 = min(max(cx - cw / 2.0, 0.0), W - cw)
    y0 = min(max(cy - ch / 2.0, 0.0), H - ch)
    return (int(round(x0)), int(round(y0)),
            int(round(x0 + cw)), int(round(y0 + ch)))


def plan_crop(bgr: np.ndarray, sm, aspect: Optional[float], pad: float, stats,
              sweep: Optional[bool] = None) -> dict:
    """Decide the crop box, or the reason there isn't one.

    Beyond the inherited guards, three of our own:
      * no studio backdrop — see below;
      * detector disagreement — rembg and the LAB heuristic must land on
        roughly the same pixels, else we do not know where the item is;
      * resolution — a crop that lands under MIN_LONG_SIDE costs eBay's zoom,
        and upscaling to fake it is worse than a looser frame.
    """
    from .center_crop import crop_warning

    fp = _focal_dict(sm, bgr.shape)
    box = _fit_box(fp, aspect, pad)

    # A frame with no identifiable backdrop is a DETAIL shot — a macro of a
    # maker's mark, a chip, a serial stamp — and its framing was chosen on
    # purpose. Cropping it "to the subject" is not an improvement: on the
    # coke-tray shoot it zoomed a condition macro into the printed woman's face
    # and a serial-number macro into the number, throwing away the context that
    # made each shot worth taking. Segmentation cannot tell a subject from a
    # picture-within-the-picture, but the absence of a sweep is decisive: if
    # there is no background to trim, there is no crop to make.
    if not (stats.is_sweep if sweep is None else sweep):
        return dict(applied=False,
                    reason=f"no studio backdrop (luma {stats.bg_luma:.0f}, spread "
                           f"{stats.bg_iqr:.0f}) — reads as a detail frame",
                    box=None, offset=fp["offset"], agreement=sm.agreement)

    if sm.source == "rembg+lab" and sm.agreement < MIN_AGREEMENT:
        return dict(applied=False, reason=f"detectors disagree (IoU {sm.agreement:.2f})",
                    box=None, offset=fp["offset"], agreement=sm.agreement)

    why = crop_warning(fp, box)
    if why:
        return dict(applied=False, reason=why, box=None,
                    offset=fp["offset"], agreement=sm.agreement)

    x0, y0, x1, y1 = box
    if max(x1 - x0, y1 - y0) < MIN_LONG_SIDE:
        return dict(applied=False,
                    reason=f"crop would be {x1 - x0}x{y1 - y0}px — under the {MIN_LONG_SIDE}px floor",
                    box=None, offset=fp["offset"], agreement=sm.agreement)

    return dict(applied=True, reason="", box=[int(v) for v in box],
                offset=fp["offset"], agreement=sm.agreement)


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------

SHOOT_LUMA_TOL = 45.0    # how far a frame's backdrop may drift from the shoot's


def _corroborate_orientation(photos: dict, quiet: bool = False) -> list:
    """A lone OSD rotation is a proposal, not an answer.

    OSD is the only objective read of subject orientation, and on real shoots it
    is confidently wrong often enough that it cannot be trusted by itself. Two
    measured false positives:

      * a textless macro of a cast iron key — "180 degrees, conf 1.5, script
        conf 0.1", caught by the script bar;
      * a macro of a silver buckle on felt — "180 degrees, conf 3.2, script
        conf 4.3", with NO text anywhere in the frame. Tesseract read the fabric
        fuzz as writing, at higher confidence than the genuine magazine covers.

    No confidence threshold separates that from a true reading, so the
    corroboration has to come from elsewhere: items in one shoot are laid down
    the same way, so a real subject rotation shows up on more than one frame. An
    OSD angle that no other frame agrees with is downgraded to an ASK, with the
    proposal kept in the notes for whoever looks.

    Zero is exempt — "this frame is already upright" agreeing with nothing is
    not evidence of anything, and applying it changes no pixels.
    """
    votes: dict = {}
    for rec in photos.values():
        o = rec["orientation"]
        if o["subject_angle"] and o["source"] in ("osd", "exif+osd", "vision", "exif+vision"):
            votes[o["subject_angle"]] = votes.get(o["subject_angle"], 0) + 1

    downgraded = []
    for name, rec in photos.items():
        o = rec["orientation"]
        if o["source"] not in ("osd", "exif+osd") or not o["subject_angle"]:
            continue
        if votes.get(o["subject_angle"], 0) >= 2:
            continue
        o["notes"].append(
            f"OSD proposed {o['subject_angle']}° but no other frame in the shoot "
            f"agrees — needs a look")
        o["osd_proposal"] = o["subject_angle"]
        o["subject_angle"] = 0
        o["applied"] = o["exif_angle"] % 360
        o["source"] = "exif" if o["exif_angle"] else "unresolved"
        o["needs_ask"] = True
        rec["status"] = "ASK"
        downgraded.append(name)

    if downgraded and not quiet:
        print(f"  orientation: {len(downgraded)} uncorroborated OSD reading(s) "
              f"sent to ASK — {', '.join(downgraded)}")
    return downgraded


def _unify_backdrop(photos: dict, quiet: bool = False) -> Optional[dict]:
    """Make one physical backdrop get one treatment across the whole shoot.

    Per-frame absolute thresholds split a single sweep down the middle. On the
    marble shoot the same navy felt metered from 49 to 104 depending on how each
    marble was lit; the frames under the `dark` cutoff went to near-black and
    three siblings stayed grey, in one listing. Nothing about the backdrop
    changed between those frames — only the exposure.

    So the shoot votes. Frames whose backdrop is smooth (`bg_iqr` within range —
    the test that a sweep exists at all) set the shoot's backdrop level; frames
    that are equally smooth and land within `SHOOT_LUMA_TOL` of it adopt the
    same class, whatever an absolute threshold would have said in isolation.
    Textured frames — the macros — never take part and are never promoted: they
    are not looking at the backdrop in the first place.
    """
    seed = [(n, r) for n, r in photos.items()
            if r["color_plan"]["bg_iqr"] <= colormod.BG_IQR_MAX]
    if len(seed) < 2:
        return None

    def _median(vals):
        v = sorted(vals)
        return v[len(v) // 2]

    shoot_luma = _median([r["color_plan"]["bg_luma"] for _, r in seed])
    seed_rough = _median([r["color_plan"].get("bg_rough", 0.0) for _, r in seed])
    if shoot_luma >= colormod.LIGHT_BG_MIN:
        shoot_class = "light"
    elif shoot_luma <= colormod.DARK_BG_MAX:
        shoot_class = "dark"
    else:
        return None

    # A frame joins the shoot's backdrop if it is looking at the same thing:
    # the same tone (exposure drifts, the cloth does not) and not dramatically
    # more textured than the frames that defined it. The roughness bound is
    # loose on purpose — its only job is to keep out gross outliers like a
    # contact-sheet panel, not to adjudicate lighting.
    rough_max = max(3.0 * seed_rough, seed_rough + 45.0)

    promoted = []
    for n, r in photos.items():
        cp = r["color_plan"]
        # An operator who has said "this frame is the item, not a backdrop"
        # outranks the vote. The shoot may not promote it back.
        if cp.get("operator"):
            continue
        if cp.get("is_sweep") and cp["bg_class"] == shoot_class:
            continue
        near = abs(cp["bg_luma"] - shoot_luma) <= SHOOT_LUMA_TOL
        smooth = cp.get("bg_rough", 999.0) <= rough_max
        # A frame may only join the shoot's backdrop if it would have been
        # ELIGIBLE to seed it. bg_rough catches high-frequency texture, but it
        # is blind to a BIMODAL background — an open box where the "backdrop"
        # is half cream card and half dark felt reads smooth yet has a huge
        # interquartile spread. Measured: floating-opal ZZ150038 had bg_iqr
        # 111.5 against a BG_IQR_MAX of 35, was promoted to "dark sweep"
        # anyway, and had its cream card normalised from luma 103 down to 22.
        # Re-applying the seed gate here is the whole fix.
        unimodal = cp["bg_iqr"] <= colormod.BG_IQR_MAX
        if near and smooth and unimodal:
            cp["bg_class_effective"] = shoot_class
            cp["is_sweep"] = True
            promoted.append(n)
    if promoted and not quiet:
        print(f"  backdrop: shoot reads {shoot_class} (luma {shoot_luma:.0f}); "
              f"{len(promoted)} frame(s) matched to it — {', '.join(promoted)}")
    return {"class": shoot_class, "luma": round(shoot_luma, 1), "promoted": promoted}


def plan_geometry(shoot: Path, quiet: bool = False,
                  provisional: bool = False) -> dict:
    """Plan the crop and the colour reading — on the APPROVED rotations.

    This used to run in the same loop as orientation, which meant every one of
    these measurements described a frame the operator had not confirmed yet. A
    crop box and a backdrop reading each describe the geometry they were
    computed on; turn the frame afterwards and they describe nothing. That is
    how six catalog spreads ended up with crops planned at 0 degrees and
    shipping files rendered at 0 degrees while the manifest said 270.

    So orientation is settled and signed off first, always, and only then is
    anything else measured. It costs a second decode per frame. It buys the
    guarantee that no downstream number was computed against a rotation that
    later changed.

    `provisional` is the ONE exception, and it is not a loosening: the auto
    first pass (`--auto`) resolves orientation itself and then plans the crop
    against exactly those rotations, in one breath, with nothing approved at
    either end. What the gate protects — no crop box measured against a rotation
    that later changed — is kept here by the sequencing rather than the stamp.
    Nothing is approved; the operator still decides both.
    """
    from .center_crop import _parse_aspect
    from . import stages as stagemod

    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit("nothing planned — run --check first")
    if not (provisional or stagemod.stage_state(m)["orientation"]["approved"]):
        raise SystemExit(
            "orientation is not approved yet — it is the first stage, and "
            "nothing else may be measured against a rotation that could still "
            "change.\n  open it with --stage orientation")

    aspect = _parse_aspect(m["settings"].get("aspect", DEFAULT_ASPECT))
    pad = float(m["settings"].get("pad", DEFAULT_PAD))
    smode = subject_mode(m)

    refused = []
    for key, rec in m["photos"].items():
        src = shoot / key
        if not src.exists():
            refused.append((key, "source file missing"))
            continue
        upright = orientmod.rotate_bgr(_load_bgr(src), rec["orientation"]["applied"])
        sm = mask_for(upright, smode)

        # A LEGACY warp, if this shoot was squared before the unskew stage was
        # removed. Nothing plans a new one; the crop box below was measured on
        # the squared pixels, so the replay has to happen before it.
        sk = skewmod.from_dict(rec.get("unskew"))
        upright, sm = _unskewed(upright, sm, sk)

        stats = colormod.analyze(upright, sm.mask)
        prior_crop = rec.get("crop") or {}
        crop = (prior_crop if prior_crop.get("operator")
                else plan_crop(upright, sm, aspect, pad, stats))

        rec["subject"] = {"source": sm.source, "agreement": round(sm.agreement, 3),
                          "mask_iou": round(sm.mask_iou, 3),
                          "coverage": round(sm.coverage, 4), "bbox": list(sm.bbox)}
        rec["crop"] = crop
        prev_cp = rec.get("color_plan") or {}
        measured = dict(bg_class=stats.bg_class, bg_luma=round(stats.bg_luma, 1),
                        bg_iqr=round(stats.bg_iqr, 1), bg_rough=round(stats.bg_rough, 1))
        rec["color_plan"] = (dict(prev_cp, **measured) if prev_cp.get("operator")
                             else dict(measured, is_sweep=stats.is_sweep,
                                       bg_class_effective=stats.bg_class))
        rec["status"] = "SHIP" if crop["applied"] else "PASSTHROUGH"
        rec.pop("pending_orientation", None)
        # An operator's crop-off is an answer, not a refusal — only the
        # pipeline's own refusals are exceptions worth a line.
        if not crop["applied"] and not crop.get("operator"):
            refused.append((key, "no crop: " + (crop.get("reason") or "")[:60]))

    m["backdrop"] = _unify_backdrop(m["photos"], quiet)
    save_manifest(shoot, m)
    if not quiet:
        verdict_emit(f"{shoot.name} geometry", len(m["photos"]), refused,
                     detail=shoot / ".prep" / "prep.json")
    return m


def run_check(shoot: Path, aspect_s: str, pad: float, pop: str,
              subject: Optional[str] = None,
              category: Optional[str] = None,
              quiet: bool = False,
              check_rotation: bool = False) -> dict:
    """Analyse every frame; write the manifest, rotation sheet and ask panels.

    `check_rotation` is opt-in. The shoot convention is now "shot right-side
    up", so by default no OSD pass runs at all — the most time-consuming part
    of `--check` on a multi-frame shoot — and every frame with no recorded
    look (`--rotate` / `orient.py`) is left at 0 with `source: assumed` (see
    `orientation.resolve`). Pass `check_rotation=True` (CLI: `--check-rotation`)
    for a shoot where that assumption might not hold — a mixed box of catalogs
    shot at whatever angle they landed, say — to get the old OSD + ask-panel
    behaviour back.
    """
    from .center_crop import _parse_aspect

    images = find_images(shoot)
    if not images:
        raise SystemExit(f"no images in {shoot}")

    aspect = _parse_aspect(aspect_s)
    m = load_manifest(shoot)
    # None means "whatever this shoot already declared" — a batch re-check
    # (tools/prep_run.py) must not silently demote a paper shoot back to auto.
    subject = subject or subject_mode(m)
    category = category or category_of(m)
    m["settings"] = dict(aspect=aspect_s, pad=pad, pop=pop, subject=subject,
                         category=category)
    prior = m.get("photos", {})
    photos: dict = {}

    osd_on = check_rotation and orientmod.osd_available()
    looks = orientmod.recorded_looks(shoot)
    thumbs = []
    ask_dir = shoot / ".prep" / "ask"
    ask_dir.mkdir(parents=True, exist_ok=True)

    for path in images:
        # Key by the path relative to the shoot, not the bare filename: when the
        # originals are gone the source lives in a subdirectory, and `shoot /
        # key` has to resolve back to the actual file at --apply and gate time.
        key = path.relative_to(shoot).as_posix()
        bgr = _load_bgr(path)
        exif_tag = orientmod.exif_orientation(path)

        # Camera half first, then segment, then ask the SUBJECT which way is up.
        # Order matters: OSD on the whole frame throws the item's type away
        # before reading it (see orient.osd_angle).
        cam = orientmod.rotate_bgr(bgr, orientmod.EXIF_ROT.get(exif_tag or 1, 0))
        sm = mask_for(cam, subject)
        if osd_on:
            osd = orientmod.osd_angle(_subject_region(cam, sm.bbox))
        else:
            note = ("tesseract not installed" if check_rotation else
                    "rotation check off — assumed upright (pass --check-rotation to verify)")
            osd = (None, 0.0, note)

        # A recorded look for this frame survives a re-run. The sibling
        # orient.py tool stores the same quantity (degrees CW on top of the EXIF
        # bake), so its manifest counts as an answer here — a rotation the user
        # already called there must not come back as a fresh ASK.
        prev = prior.get(key, {}) or prior.get(path.name, {})
        vision = prev.get("orientation", {}).get("vision_angle")
        if vision is None:
            vision = looks.get(key) or looks.get(path.name)
        v = orientmod.resolve(path.name, exif_tag, osd, vision,
                              assume_upright=not check_rotation)

        # Segmentation is rotation-invariant — turn the mask with the image.
        upright = orientmod.rotate_bgr(cam, v.subject_angle)
        sm = subjectmod.describe(orientmod.rotate_bgr(sm.mask, v.subject_angle),
                                 sm.source, sm.agreement, sm.mask_iou)

        # ORIENTATION IS FIRST, AND NOTHING ELSE IS MEASURED UNTIL IT IS SETTLED.
        #
        # A crop box and a backdrop reading each describe the geometry they were
        # computed on. Measure them here, before the operator has confirmed which
        # way is up, and a later rotation silently invalidates both — which is
        # how six catalog spreads got crops planned at 0 degrees while the
        # manifest ended up saying 270.
        #
        # `plan_geometry()` does that work, after --approve-stage orientation.
        stats = None
        crop = (prev.get("crop") if (prev.get("crop") or {}).get("operator")
                else {"applied": False, "reason": "not planned yet — orientation first",
                      "box": None, "offset": 0.0, "agreement": 0.0})

        if v.needs_ask:
            orientmod.four_way_panel(bgr, ask_dir / f"{path.stem}_rotation.jpg",
                                     f"{key} — which is upright?")

        photos[key] = {
            "src_sha256": _sha256(path),
            "src_size": [int(bgr.shape[1]), int(bgr.shape[0])],
            "orientation": {
                "exif_tag": v.exif_tag, "exif_angle": v.exif_angle,
                "osd_angle": v.osd_angle, "osd_conf": round(v.osd_conf, 2),
                "osd_note": v.osd_note, "vision_angle": v.vision_angle,
                "subject_angle": v.subject_angle,
                "applied": v.applied, "source": v.source,
                "needs_ask": v.needs_ask, "notes": v.notes,
            },
            "subject": {"source": sm.source, "agreement": round(sm.agreement, 3),
                        "mask_iou": round(sm.mask_iou, 3),
                        "coverage": round(sm.coverage, 4), "bbox": list(sm.bbox)},
            # Carried, never re-decided: the stage is gone, and only a shoot
            # squared under it still has anything here to replay.
            "unskew": prev.get("unskew") or {"applied": False,
                                             "reason": "unskew stage removed"},
            "crop": crop,
            # An operator's `--detail` call is an answer, not a proposal: it
            # survives a re-run the same way a recorded rotation does. Only the
            # measurements are refreshed; the sweep verdict stays theirs.
            "color_plan": prev.get("color_plan") or {},
            "pending_orientation": True,
            "status": "ASK" if v.needs_ask else "PENDING",
            "output": prev.get("output"),
            "out_sha256": prev.get("out_sha256"),
        }
        # Keep only the CAMERA-corrected thumbnail. The subject rotation is
        # applied when the sheet is built, AFTER corroboration has had its say —
        # capturing the rotated image here rendered a frame at an angle that was
        # subsequently downgraded to ASK and never applied, so the sheet showed a
        # rotation the manifest did not have. Whoever judges that sheet is then
        # confirming something that is not what will ship.
        thumbs.append((key, _thumb(cam)))

    m["photos"] = photos
    _corroborate_orientation(photos, quiet)
    # The backdrop verdict is a MEASUREMENT of the upright frame, so it belongs
    # with the rest of the geometry — after orientation is signed off, not here.
    # Any earlier approval is void: what the operator approved is not what the
    # manifest now describes.
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)

    _rotation_sheet(thumbs, photos, shoot / ".prep" / "rotation_sheet.jpg")
    if not quiet:
        flags = [(k, f"orientation ASK ({r['orientation']['source']})")
                 for k, r in photos.items() if r["orientation"]["needs_ask"]]
        verdict_emit(f"{shoot.name} check", len(photos), flags,
                     detail=shoot / ".prep" / "prep.json",
                     next_hint="--stage orientation")
    return m


def _already_listed(shoot: Path) -> bool:
    """Has this shoot ever been to eBay?

    `crisp` is the house default for NEW items only. An item already live was
    photographed, corrected and published under whatever look was current then,
    and re-rendering it into a different one silently changes the pictures a
    buyer may already have seen. Existing items keep their look unless someone
    picks a new one on purpose.

    A draft carrying an offer id is the evidence. No draft, or a draft that has
    never synced, means new.
    """
    draft = shoot / "draft.md"
    if not draft.exists():
        return True
    try:
        head = draft.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    for key in ("ebay_offer_id:", "ebay_inventory_sku:"):
        for line in head.splitlines():
            if line.strip().startswith(key):
                val = line.split(":", 1)[1].strip().strip('"').strip("'")
                if val and val.lower() not in ("null", "none", ""):
                    return True
    return False


def _frame_is_resumable(shoot: Path, name: str, rec: dict, only: tuple) -> bool:
    """Whether an already-rendered frame can be reused under `--resume`.

    Conservative by construction (docs/prep-resume-plan.md item 2): every
    condition below must hold, or the frame is treated as not done and
    re-rendered. Over-rendering wastes time; a stale render shipping wastes
    a lot more. The settings-hash check (whether THIS invocation is asking
    the same question the cached render answered) is the caller's job — it
    is per-run, not per-frame, so it is checked once and passed down as
    already-satisfied rather than re-checked here.
    """
    src = shoot / name
    if not src.exists() or _sha256(src) != rec.get("src_sha256"):
        return False
    presets = rec.get("presets") or {}
    for pname in only:
        entry = presets.get(pname)
        if not entry or not entry.get("path"):
            return False
        p_path = shoot / entry["path"]
        if not p_path.exists() or _sha256(p_path) != entry.get("sha256"):
            return False
    return True


def run_apply(shoot: Path, quiet: bool = False, only: tuple = (),
              resume: bool = False) -> dict:
    """Render listing/ from the manifest's decisions + the review sheet.

    `only` restricts which presets are rendered. The default category now
    narrows this to `("crisp",)` — the house default for new items anyway
    (`color.default_preset_for`) — so a shoot renders and auto-picks ONE look
    unless something says otherwise: `--only NAME...` for specific looks
    (`--only asshot` to skip colour correction entirely), or `--filters` to
    render every preset in `color.PRESETS` for a side-by-side comparison. At
    ~64s per frame per preset, that comparison is expensive on purpose to skip
    by default — it is the difference between a 3-hour run and a 17-hour one on
    a batch that was never going to look at five of the six looks anyway.

    `resume` (default off; omitting it reproduces today's behaviour exactly)
    skips a frame's re-render only when the manifest's own record proves the
    existing render still answers THIS invocation's question — see
    docs/prep-resume-plan.md item 3. The manifest is also checkpointed after
    every frame instead of once at the end (item 1), so a run killed by a
    timeout leaves a consistent partial manifest a later `--resume` can trust,
    instead of orphaning every already-rendered JPEG.
    """
    from .center_crop import _parse_aspect

    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit("nothing planned — run --check first")

    aspect = _parse_aspect(m["settings"].get("aspect", DEFAULT_ASPECT))
    pad = float(m["settings"].get("pad", DEFAULT_PAD))
    # NOT forwarded to colormod.correct below, and that is deliberate rather
    # than the wart it looks like (#23). Every render here goes through a
    # PRESET, and every preset in color.PRESETS already bakes in its own `pop`
    # level -- `correct()`'s standalone `pop` argument is only ever read when
    # NO preset is given, which run_apply never does. A category re-exposing
    # this as a second knob would either be silently overridden by the
    # preset's own `pop` or have to fight it, so categories.py refuses to let
    # a profile set it at all (see its module docstring). This local stays
    # only so it keeps showing up in the manifest's `settings` for the
    # standalone `color.correct(..., preset=None)` path the unit tests use.
    pop = m["settings"].get("pop", "gentle")
    smode = subject_mode(m)

    # An explicit `only` still wins; otherwise the shoot's category decides
    # which looks are worth rendering. This is where the batch time actually
    # goes -- six looks at ~25s a frame on 12 MP, five of which a printed
    # shoot will never open. See photo_prep.categories.
    only = tuple(only) or catmod.looks_for(category_of(m))

    # A fingerprint of the inputs that decide what gets rendered, captured
    # against what was on disk BEFORE this call overwrites it -- so a
    # resumed run's own checkpoint can never trivially match itself, only a
    # PRIOR run's, under the same settings, can (docs/prep-resume-plan.md
    # item 2). `resume=False` (the default) never consults it: `resumable`
    # stays False and every frame renders exactly as it always has.
    settings_hash = _apply_settings_hash(aspect, pad, pop, smode,
                                         category_of(m), only)
    prior_settings_hash = (m.get("apply_run") or {}).get("settings_hash")
    resumable = resume and prior_settings_hash == settings_hash
    m["apply_run"] = {"settings_hash": settings_hash, "started_at": _now()}

    # An apply in flight -- or interrupted mid-flight -- is not an approved
    # one. Set this BEFORE the loop, not after, so every checkpoint below
    # already reflects it: a resumed frame's untouched `output`/`out_sha256`
    # would otherwise still look complete enough to slip past
    # `assert_approved` on a stale `approved: true` left over from the run
    # this one is replacing.
    m["approved"] = False
    m["approved_at"] = None

    # Persist BOTH of the above before rendering a single frame. Otherwise a
    # process killed during frame 1 (the main failure mode this whole change
    # targets) leaves the on-disk manifest showing a stale `apply_run` from
    # the PREVIOUS apply, and possibly `approved: true` -- exactly the state
    # the checks above exist to rule out, just not yet written down.
    save_manifest(shoot, m)

    out_dir = shoot / "listing"
    out_dir.mkdir(exist_ok=True)
    rows = []
    flags = []

    for name, rec in m["photos"].items():
        src = shoot / name
        if not src.exists():
            rec["status"] = "MISSING"
            flags.append((name, "source file MISSING"))
            continue

        # --resume: skip the (expensive) re-render only when the manifest's
        # own record proves the existing files still answer THIS
        # invocation's question. Conservative by construction -- any one
        # check failing means the frame is (re)rendered, never the reverse
        # (docs/prep-resume-plan.md item 2). The sheet still needs pixels for
        # this frame, so load the original and the already-rendered presets
        # back off disk rather than re-deriving them.
        if resumable and _frame_is_resumable(shoot, name, rec, only):
            before = _load_bgr(src)
            variants = {pname: _load_bgr(shoot / rec["presets"][pname]["path"])
                        for pname in only}
            rows.append((name, before, variants, rec))
            # A skipped frame's exceptions are exactly as real as a freshly
            # rendered one's -- reuse the same flag logic below, sourced from
            # the persisted record instead of a report just computed, so
            # verdict_emit doesn't read a resumed run as cleaner than it is.
            c = rec.get("color") or {}
            if c.get("subject_newly_clipped") or c.get("subject_newly_crushed"):
                flags.append((name, f"colour pass touched item pixels "
                                    f"(new-clip={c.get('subject_newly_clipped')}, "
                                    f"new-crush={c.get('subject_newly_crushed')})"))
            if rec["orientation"]["needs_ask"]:
                flags.append((name, "orientation still ASK"))
            continue

        before = _load_bgr(src)
        img = orientmod.rotate_bgr(before, rec["orientation"]["applied"])

        # A legacy warp from before the unskew stage was removed is a decision
        # already published, not a proposal — replay it, never re-derive it.
        # Everything below is measured on the frame the crop was planned on.
        sk = skewmod.from_dict(rec.get("unskew"))

        # Re-segment on the upright pixels: the crop box in the manifest was
        # planned there, and the colour pass needs the same mask.
        sm = mask_for(img, smode)
        img, sm = _unskewed(img, sm, sk)
        # Judged before the crop — a tight crop keeps too little backdrop to
        # tell a sweep from a textured surface (see color._backdrop_lut).
        pre_stats = colormod.analyze(img, sm.mask)
        # The check pass already reconciled this frame's backdrop against the
        # rest of the shoot; honour that rather than re-deciding it alone.
        cp = rec.get("color_plan") or {}
        sweep = bool(cp.get("is_sweep", pre_stats.is_sweep))
        eff_class = cp.get("bg_class_effective", pre_stats.bg_class)
        # An operator override is an answer, not a proposal — re-planning here
        # would silently throw away the call made at the crop stage, which is
        # the one the sheet was approved on.
        prior_crop = rec.get("crop") or {}
        crop = (prior_crop if prior_crop.get("operator")
                else plan_crop(img, sm, aspect, pad, pre_stats, sweep))
        rec["crop"] = crop
        if crop["applied"]:
            img, sm = _cropped(img, sm, crop["box"])

        # Every preset renders from the SAME mask and crop. Segmentation is the
        # expensive step by a wide margin, so offering three looks costs barely
        # more than offering one — and the comparison is only meaningful if the
        # geometry is identical across them.
        # How warm the item itself is, off the same mask the colour pass uses.
        # Cheap (a statistic on a 1000px copy) and it decides which look the
        # shoot defaults to — see color.WARM_SUBJECT_MIN_RB.
        rec["subject_warmth"] = colormod.subject_warmth(img, sm.mask)

        rec["presets"] = {}
        variants = {}
        for pname in (only or colormod.PRESETS):
            rendered, creport = colormod.correct(img, sm.mask, sweep=sweep,
                                                 bg_class=eff_class, preset=pname)
            p_dir = shoot / ".prep" / "presets" / pname
            p_dir.mkdir(parents=True, exist_ok=True)
            p_path = p_dir / (Path(name).stem + ".jpg")
            _save_bgr(p_path, rendered)
            rec["presets"][pname] = {
                "path": str(p_path.relative_to(shoot)).replace("\\", "/"),
                "sha256": _sha256(p_path),
                "report": creport,
            }
            variants[pname] = rendered

        # DEFAULT_PRESET is not necessarily rendered under `only`; fall back to
        # whatever was, so the manifest still carries a colour report.
        _rep_key = (colormod.DEFAULT_PRESET if colormod.DEFAULT_PRESET in rec["presets"]
                    else next(iter(rec["presets"])))
        creport = rec["presets"][_rep_key]["report"]
        rec["color"] = creport
        # `listing/` stays empty until a preset is picked — the whole point is
        # that the operator chooses the look, so nothing may default into the
        # directory DRAFT reads.
        rec["output"] = None
        rec["out_sha256"] = None
        rec["status"] = "ASK" if rec["orientation"]["needs_ask"] else "PICK"

        rows.append((name, before, variants, rec))
        # Exceptions only: the colour pass measuring its own output as having
        # damaged item pixels, or an orientation still unresolved. Routine
        # per-frame render stats live in the manifest.
        c = creport
        if c["subject_newly_clipped"] or c["subject_newly_crushed"]:
            flags.append((name, f"colour pass touched item pixels "
                                f"(new-clip={c['subject_newly_clipped']}, "
                                f"new-crush={c['subject_newly_crushed']})"))
        if rec["orientation"]["needs_ask"]:
            flags.append((name, "orientation still ASK"))

        # Checkpoint after every frame, not once at the end -- a killed
        # process must orphan at most the frame in flight, never the ones
        # already finished (docs/prep-resume-plan.md item 1). save_manifest
        # already does compare-and-swap and re-stamps m[READ_FINGERPRINT] on
        # this same dict after a successful write, so the next checkpoint's
        # CAS check is automatically against the fingerprint THIS process
        # just wrote -- nothing extra to track here.
        save_manifest(shoot, m)

    # Renders from an earlier pass that this one no longer names. Nothing can
    # read them again, so they go now rather than accruing until an audit finds
    # 3.5 GB of them. See _sweep_unreferenced_presets.
    _sweep_unreferenced_presets(shoot, m, quiet=quiet)
    _presets_sheet(rows, shoot / ".prep" / "prep_presets.jpg")
    if not quiet:
        verdict_emit(f"{shoot.name} apply", len(m["photos"]), flags,
                     detail=shoot / ".prep" / "prep.json")

    # Adopt the backdrop's default look so `listing/` is populated without a
    # separate step. Both looks stay rendered and `--pick` swaps them; the
    # approval gate is untouched, so this decides what gets SHOWN at the gate,
    # never what gets published.
    shoot_class = (m.get("backdrop") or {}).get("class")
    # One verdict for the shoot, not per frame: a macro and a full view of the
    # same brass key must not land on different looks. The median is the vote —
    # one frame whose mask caught mostly cloth cannot swing it.
    warmths = [float((r.get("subject_warmth") or {}).get("r_minus_b", 0.0))
               for _, _, _, r in rows
               if (r.get("subject_warmth") or {}).get("pixels", 0) >= 64]
    shoot_rb = float(np.median(warmths)) if warmths else 0.0
    warm = shoot_rb >= colormod.WARM_SUBJECT_MIN_RB
    m["subject_warmth"] = dict(median_r_minus_b=round(shoot_rb, 2),
                               frames=len(warmths), warm=warm)
    # A DELIBERATE PICK OUTRANKS THE DEFAULT, ALWAYS.
    #
    # This used to auto-pick unconditionally at the end of every render. Run
    # --apply while an operator's --pick is in flight and the auto-pick lands
    # last, so the manifest says one look and listing/ holds another. That
    # happened three times in one session: --pick printed "picked asshot",
    # reported success, and the shipping files were still studio. It is only
    # caught by hashing the files, which nobody does.
    #
    # An already-chosen look is a decision. Re-render its files, leave the
    # choice alone.
    prior = m.get("chosen_preset")
    if prior and prior in (only or colormod.PRESETS):
        save_manifest(shoot, m)
        m = run_pick(shoot, prior, quiet=True, auto=False)
        if not quiet:
            print(f"  kept the look already chosen for this shoot: {prior}")
        return m

    # A category's declared default_look (#23) replaces the backdrop/warmth
    # heuristic outright rather than competing with it -- "for this kind of
    # goods, THIS look is the one worth showing first" is the same authority
    # the heuristic already had (a default, subordinate to --pick and to the
    # approval gate either way), just stated per category instead of derived
    # per shoot. Only honoured when it was actually rendered under `only`;
    # otherwise fall through so a narrowed batch never adopts a look it never
    # produced.
    cat_default = catmod.default_look_for(category_of(m))
    if cat_default and (not only or cat_default in only):
        default = cat_default
    else:
        default = colormod.default_preset_for(
            shoot_class, warm_subject=warm, new_item=not _already_listed(shoot))
        # Nothing else was rendered, so the backdrop's usual default cannot be
        # shown or picked. Adopting it anyway would point listing/ at files
        # that do not exist.
        if only and default not in only:
            default = only[0]
    save_manifest(shoot, m)
    m = run_pick(shoot, default, quiet=True, auto=True)
    if not quiet:
        warm_note = (f", warm-metal subject (median R-B {shoot_rb:.0f})" if warm
                     else f" (median R-B {shoot_rb:.0f})")
        source = "category default" if default == cat_default and cat_default else f"{shoot_class or 'mixed'} backdrop"
        print(f"  default look for a {source}{warm_note}: "
              f"{default} (--pick {'|'.join(colormod.PRESETS)} to change)")
    return m



# ---------------------------------------------------------------- cleanup
#
# PREP WAS PURELY ADDITIVE, AND IT SHOWED.
#
# Every pass wrote and nothing ever removed, so `inventory/` reached 27.3 GB
# with 9.2 GB of it in `.prep/presets/`. Two distinct leaks, and they want
# different treatment:
#
#   1. UNREFERENCED renders. `--apply` narrows the preset set (`--only`, or the
#      shoot's category via categories.looks_for). Re-run a shoot that was once
#      rendered at six looks with a two-look category and the other four sit
#      there forever -- the manifest does not mention them, `--pick` cannot
#      reach them, nothing can ever read them again. Measured: 171 dirs and
#      1366 files, 3.5 GB. This is not a decision anyone made, it is a leak, so
#      run_apply now sweeps it automatically at the end of every render.
#
#   2. SUPERSEDED renders. The looks that WERE rendered this run but lost the
#      pick, plus the `.prep/ask/` panels for frames whose orientation has since
#      been answered. Those are real artifacts of a real decision and the
#      comparison sheet is the whole point of the gate, so they are never swept
#      automatically -- only by an explicit `--gc`, and only once the shoot is
#      approved. Both are regenerable with `--apply`.
#
# `--gc` prints and removes nothing; `--gc --gc-force` removes. There is no
# `git checkout` behind `inventory/` -- it is gitignored -- so the dry run is
# the default in the one place where that asymmetry actually bites.


def _dir_bytes(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def _mb(b: int) -> str:
    """Render sizes so a sweep of a few stray frames does not read as '0 MB'."""
    return f"{b / 1048576:.1f} MB" if b >= 1048576 else f"{b / 1024:.0f} KB"


def _sweep_unreferenced_presets(shoot: Path, m: dict, quiet: bool = False) -> int:
    """Delete preset renders no manifest entry points at. Returns bytes freed.

    Safe by construction: a file the manifest does not name cannot be picked,
    cannot be copied to `listing/`, and cannot be shown on the comparison sheet.
    """
    import shutil

    p_root = shoot / ".prep" / "presets"
    if not p_root.is_dir():
        return 0

    named, referenced = set(), set()
    for rec in (m.get("photos") or {}).values():
        for pname, entry in (rec.get("presets") or {}).items():
            named.add(pname)
            referenced.add((shoot / entry["path"]).resolve())

    freed = 0
    for pdir in sorted(p_root.iterdir()):
        if not pdir.is_dir():
            continue
        if pdir.name not in named:
            freed += _dir_bytes(pdir)
            shutil.rmtree(pdir)
            continue
        # The look survived but individual frames may not have: a photo removed
        # from the shoot leaves its render behind under a preset still in use.
        for f in pdir.rglob("*"):
            if f.is_file() and f.resolve() not in referenced:
                freed += f.stat().st_size
                f.unlink()

    if freed and not quiet:
        print(f"  swept {_mb(freed)} of unreferenced preset renders")
    return freed


def run_gc(shoot: Path, force: bool = False) -> int:
    """Drop regenerable byproducts of an APPROVED shoot. Returns bytes freed.

    Refuses on an unapproved shoot: before approval the unchosen looks are the
    comparison the operator has not made yet, and the ask panels are questions
    nobody has answered.
    """
    import shutil

    m = load_manifest(shoot)
    if not m.get("photos"):
        print(f"{shoot}: nothing prepped")
        return 0
    if not m.get("approved"):
        print(f"{shoot}: not approved — keeping every look and every ask panel. "
              f"--gc is for shoots that are done.")
        return 0

    chosen = m.get("chosen_preset")
    targets: list[tuple[Path, str]] = []

    p_root = shoot / ".prep" / "presets"
    if p_root.is_dir():
        for pdir in sorted(p_root.iterdir()):
            if pdir.is_dir() and pdir.name != chosen:
                targets.append((pdir, f"unchosen look (chosen: {chosen})"))

    ask = shoot / ".prep" / "ask"
    if ask.is_dir():
        unresolved = [n for n, r in m["photos"].items()
                      if (r.get("orientation") or {}).get("needs_ask")]
        if unresolved:
            print(f"  keeping .prep/ask/ — still unanswered: {', '.join(unresolved[:5])}")
        else:
            targets.append((ask, "orientation panels, all answered"))

    if not targets:
        print(f"{shoot}: already clean")
        return 0

    total = 0
    for d, why in targets:
        b = _dir_bytes(d)
        total += b
        print(f"  {'rm' if force else 'would rm'} {str(d.relative_to(shoot)):24} "
              f"{_mb(b):>10}   {why}")
        if force:
            shutil.rmtree(d)

    verb = "freed" if force else "would free"
    print(f"{shoot}: {verb} {_mb(total)}  "
          f"(regenerate with `--apply`)")
    if not force:
        print("  dry run — add --gc-force to actually remove")
    return total


def run_pick(shoot: Path, preset: str, quiet: bool = False,
             auto: bool = False) -> dict:
    """Adopt one preset for the whole shoot: copy it into `listing/`.

    Kept separate from rendering so the choice is always a deliberate act. It
    also re-renders the before|after sheet for the chosen look, which is what
    the approval gate is then shown.
    """
    import shutil

    if preset not in colormod.PRESETS:
        raise SystemExit(f"unknown preset {preset!r}; choose from "
                         f"{', '.join(colormod.PRESETS)}")
    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit("nothing rendered — run --apply first")

    out_dir = shoot / "listing"
    out_dir.mkdir(exist_ok=True)
    rows = []
    for name, rec in m["photos"].items():
        entry = (rec.get("presets") or {}).get(preset)
        if not entry:
            raise SystemExit(f"{name}: preset {preset!r} was not rendered — re-run --apply")
        src = shoot / entry["path"]
        dst = out_dir / (Path(name).stem + ".jpg")
        shutil.copyfile(src, dst)
        rec["output"] = str(dst.relative_to(shoot)).replace("\\", "/")
        rec["out_sha256"] = _sha256(dst)
        rec["color"] = entry["report"]
        rec["status"] = "ASK" if rec["orientation"]["needs_ask"] else "SHIP"
        if (shoot / name).exists():
            rows.append((name, _load_bgr(shoot / name), _load_bgr(dst), rec))

    m["chosen_preset"] = preset
    # WHO chose it, not just what. An auto-adopted default and a deliberate
    # --pick are different decisions even when they name the same preset:
    # only one of them survives the next --apply. See decisions.record_for.
    m["preset_picked_by_operator"] = not auto
    m["preset_source"] = "default" if auto else "picked"
    # A different look is a different set of photos; whatever was approved
    # before was approved for images that are no longer the ones on disk.
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)
    _review_sheet(rows, shoot / ".prep" / "prep_review.jpg")
    if not quiet:
        print(f"  {'default' if auto else 'picked'} '{preset}' — "
              f"{len(rows)} photos copied to {out_dir}")
        print(f"  review: {shoot / '.prep' / 'prep_review.jpg'}")
    return m


def run_sheet(shoot: Path, quiet: bool = False) -> dict:
    """Rebuild the rotation sheet from the manifest. No segmentation.

    Cheap enough to re-run over a whole batch, which matters because the sheet is
    the artefact a human judges from: if it ever disagrees with the manifest, the
    answer they give is an answer to the wrong question.
    """
    m = load_manifest(shoot)
    photos = m.get("photos") or {}
    if not photos:
        raise SystemExit("nothing planned — run --check first")
    thumbs = []
    for name, rec in photos.items():
        src = shoot / name
        if not src.exists():
            continue
        cam = orientmod.rotate_bgr(_load_bgr(src),
                                   rec["orientation"].get("exif_angle", 0))
        thumbs.append((name, _thumb(cam)))
    out = shoot / ".prep" / "rotation_sheet.jpg"
    _rotation_sheet(thumbs, photos, out)
    if not quiet:
        asks = [n for n, r in photos.items() if r["orientation"]["needs_ask"]]
        print(f"  {shoot.name}: sheet rebuilt ({len(thumbs)} frames, {len(asks)} ASK) -> {out}")
    return m


def match_prepped(entry: str, photos: dict) -> Optional[str]:
    """Find the prepped counterpart of one of a draft's photo entries.

    Draft photo lists carry the naming of whatever workflow produced them, and
    three conventions are live in this repo at once:

        "DSC_0050.JPG"                     the source frame
        "listing-photos/01_P8140022.jpg"   an earlier prepped set, index-prefixed
        "no-exif/ZZ150038r.JPG"            orient.py's rotated variant, 'r' suffix

    All three name the same source frame. Matching the exact stem alone refuses
    the last two — 9 of 33 drafts in the first fan-out — and "no prepped file"
    is a confusing way to say "this draft uses the older naming".
    """
    import re
    stems = {Path(n).stem.lower(): n for n in photos}
    raw = Path(entry).stem
    seen = set()
    for cand in (raw,
                 re.sub(r"^\d+[_-]", "", raw),                        # 01_X -> X
                 re.sub(r"r$", "", raw),                              # Xr   -> X
                 re.sub(r"r$", "", re.sub(r"^\d+[_-]", "", raw))):
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        if key in stems:
            return stems[key]
    return None


def run_repoint_draft(shoot: Path, apply: bool = False) -> list:
    """Point the draft's `photos:` list at the prepped files, IN THE SAME ORDER.

    Order is not cosmetic: entry one is the eBay gallery image, and a draft's
    list is frequently not lexicographic — the sterling buckle listing leads
    with its fifth file by name. Rewriting the list by globbing `listing/` would
    silently change the gallery image on a live listing, so each existing entry
    is mapped to its own prepped counterpart and the sequence is preserved.

    Both YAML list styles appear in these drafts (block `- "x"` and flow
    `["a", "b"]`); the rewrite keeps whichever the draft already used, so the
    diff is the paths and nothing else.

    Dry run unless `apply`.
    """
    import re

    draft = shoot / "draft.md"
    if not draft.exists():
        raise SystemExit(f"no draft.md in {shoot}")
    m = load_manifest(shoot)
    if not m.get("chosen_preset"):
        raise SystemExit("no preset adopted yet — run --apply first")

    text = draft.read_text(encoding="utf-8")
    # A block entry may carry a trailing comment, and those comments document
    # what each frame IS ("# hero — full-form ornament, front"). They are the
    # only record of why the order is what it is, so they are matched, kept, and
    # re-emitted beside the new path rather than dropped.
    # Entries may be quoted or bare — both are live in these drafts, and a
    # parser that only reads quoted ones reports "could not find a photos:
    # list", which reads as a missing key rather than a quoting difference.
    # That refused 6 listings on the second fan-out.
    block = re.search(
        r'^photos:[ \t]*\n((?:[ \t]*-[ \t]*(?:"[^"]+"|[^\s#][^\n#]*?)[ \t]*(?:#[^\n]*)?\n)+)',
        text, re.M)
    flow = None if block else re.search(r'^photos:[ \t]*\[([^\]]*)\][ \t]*$', text, re.M)
    if not block and not flow:
        raise SystemExit("could not find a photos: list in draft.md")

    if block:
        entries = []
        for line in block.group(1).splitlines():
            m2 = re.match(r'[ \t]*-[ \t]*(?:"([^"]+)"|([^\s#][^\n#]*?))[ \t]*(#[^\n]*)?$', line)
            if m2:
                entries.append((m2.group(1) or m2.group(2), m2.group(3) or "",
                                bool(m2.group(1))))
    else:
        entries = [(p, "", True) for p in re.findall(r'"([^"]+)"', flow.group(1))]
    if not entries:
        raise SystemExit("photos: list is empty")

    mapping, missing = [], []
    for e, comment, quoted in entries:
        key = match_prepped(e, m["photos"])
        out = (m["photos"].get(key) or {}).get("output") if key else None
        if not out:
            missing.append(e)
        mapping.append((e, out, comment, quoted))

    if missing:
        raise SystemExit(f"no prepped file for: {', '.join(missing)}")

    if block:
        indent = re.match(r'[ \t]*', block.group(1)).group(0) or "  "
        rendered = "photos:\n" + "".join(
            f'{indent}- ' + (f'"{o}"' if q else o) + (f'  {c}' if c else '') + '\n'
            for _e, o, c, q in mapping)
        updated = text[:block.start()] + rendered + text[block.end():]
    else:
        joined = ", ".join(f'"{o}"' for _e, o, _c, _q in mapping)
        updated = text[:flow.start()] + f"photos: [{joined}]" + text[flow.end():]

    for old, new, _c, _q in mapping:
        print(f"  {old:40} -> {new}")
    if apply:
        draft.write_text(updated, encoding="utf-8")
        print(f"  draft.md repointed ({len(mapping)} photos, order preserved)")
    else:
        print("  DRY RUN — pass --apply-repoint to write draft.md")
    return mapping


def run_stage(shoot: Path, stage: str, quiet: bool = False) -> dict:
    """Open one review stage: build its sheet and report what it is waiting on.

    Refuses to open a stage whose predecessor is not approved. That ordering is
    the point of the split — a crop judged on a frame that is still going to be
    rotated is a judgement about an image that will not exist.
    """
    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit("nothing planned — run --check first")
    blocked = stagemod.stage_blocker(m, stage)
    if blocked:
        raise SystemExit(f"cannot open '{stage}': {blocked}")

    if stage == "color" and not any((r.get("presets") or {})
                                    for r in m["photos"].values()):
        raise SystemExit("colour has not been rendered yet — run --apply-color first")

    out = shoot / ".prep" / f"stage_{stagemod.STAGES.index(stage) + 1}_{stage}.jpg"
    stagemod.SHEET_BUILDERS[stage](shoot, m, out)
    pending = stagemod.unresolved_for(m, stage)
    save_manifest(shoot, m)
    if not quiet:
        st = stagemod.stage_state(m)[stage]
        print("")
        print(stagemod.STAGE_LABEL[stage])
        print(f"  sheet: {out}")
        print(f"  approved: {st['approved']}")
        if pending:
            print(f"  waiting on {len(pending)}:")
            for x in pending[:12]:
                print(f"     {x}")
        else:
            print("  nothing outstanding — approve with "
                  f"--approve-stage {stage} once the sheet looks right")
    return m


def run_auto(shoot: Path, aspect_s: str, pad: float, pop: str,
             subject: Optional[str] = None, category: Optional[str] = None,
             quiet: bool = False, check_rotation: bool = False) -> dict:
    """The FIRST PASS: turn every frame and plan every crop, asking nothing.

    The staged review is what PREP is for, and it is also a lot of the
    operator's attention spent on frames where the answer was never in doubt.
    So the run now starts with a best attempt at the two geometric stages,
    made in one breath and approved by nobody:

        --auto  ->  the operator looks once  ->  approve, or open the stages

    Two rules make this safe to do unasked.

    ORIENTATION IS GUESSED HERE, AND SAYS SO. A frame the resolver cannot read
    would normally become an ASK and stop the run. In this pass it takes the
    best signal available — the OSD proposal if there is one, otherwise 0 — and
    records `guessed: True` against it. That flag is the whole point: it is what
    the card and the widget mark, and it names exactly the frames worth a human
    look. A guess presented as a resolution is the failure mode this pass could
    have, and the flag is what keeps it from happening silently.

    THE CROP IS DELIBERATELY LOOSE. `MIN_FRAME_KEPT` and the wider default pad
    mean the first pass trims edges and leaves backdrop around the item. A crop
    that is too generous costs nothing but a second look; one that is too tight
    has already thrown pixels away, and on an item nobody re-shoots that is not
    recoverable.

    Nothing is approved and nothing is rendered. `listing/` is still written
    only after the gate, and the gate still wants a human.
    """
    m = run_check(shoot, aspect_s, pad, pop, subject=subject,
                  category=category, quiet=quiet, check_rotation=check_rotation)

    guessed = []
    for key, rec in m["photos"].items():
        o = rec["orientation"]
        if not o.get("needs_ask"):
            continue
        prop = o.get("osd_proposal")
        if prop is None:
            prop = o.get("osd_angle") if o.get("osd_angle") else 0
        prop = int(prop or 0) % 360
        o["subject_angle"] = prop
        o["applied"] = (o.get("exif_angle", 0) + prop) % 360
        o["needs_ask"] = False
        o["guessed"] = True
        o["source"] = (o.get("source") or "auto") + "+guess"
        o["notes"] = list(o.get("notes") or []) + [
            f"auto first pass: nothing legible to read, took {prop} deg "
            f"(OSD proposal {o.get('osd_proposal')}, conf {o.get('osd_conf')})"]
        rec["status"] = "PENDING"
        guessed.append(key)
    save_manifest(shoot, m)

    # Straight into the crop, against the rotations just recorded. Nothing else
    # may run between these two calls: that adjacency is what makes planning
    # against unapproved rotations safe here.
    m = plan_geometry(shoot, quiet=quiet, provisional=True)
    m["auto"] = {"at": _now(), "guessed": guessed, "pad": pad,
                 "min_frame_kept": MIN_FRAME_KEPT}
    save_manifest(shoot, m)

    photos = m.get("photos") or {}
    cropped = [n for n, r in photos.items() if (r.get("crop") or {}).get("applied")]
    print(f"\n{shoot.name}: auto first pass — {len(cropped)}/{len(photos)} "
          f"cropped, nothing approved")
    verdict_emit("auto", len(photos),
                 [(k, "orientation guessed — worth a look") for k in guessed],
                 detail=shoot / ".prep" / "prep.json",
                 next_hint="--approve-auto takes it as it stands; "
                           "--stage orientation opens the review")
    return m


def run_approve_auto(shoot: Path) -> dict:
    """Take the auto first pass as it stands — both geometric stages at once.

    This is the operator saying yes to what they were shown, so it is a real
    approval and stamps like one: the same per-stage digest over the same
    decisions, so any later edit invalidates it exactly as it would a stage
    approved on its own sheet. The only thing being compressed is the number of
    times they have to say yes to a page they have already read.
    """
    m = load_manifest(shoot)
    if not (m.get("auto") or {}).get("at"):
        raise SystemExit("no auto pass to approve — run --auto first")
    for stage in ("orientation", "crop"):
        m = run_approve_stage(shoot, stage)
    return m


def run_approve_stage(shoot: Path, stage: str) -> dict:
    """Sign off one stage. Approving ORIENTATION also plans everything that
    depends on it, so the operator never has to remember a second command."""
    """Record the operator's sign-off on ONE stage."""
    m = load_manifest(shoot)
    blocked = stagemod.stage_blocker(m, stage)
    if blocked:
        raise SystemExit(f"cannot approve '{stage}': {blocked}")
    pending = stagemod.unresolved_for(m, stage)
    if pending:
        raise SystemExit(
            f"cannot approve '{stage}' - {len(pending)} outstanding: "
            + "; ".join(pending[:12]))
    st = stagemod.stage_state(m)
    # The sign-off attaches to the DECISIONS it was given, not merely to a
    # timestamp. Any later change to any decision moves the digest, so the
    # approval stops matching without anyone remembering to invalidate it --
    # which is what let six frames sit at approved:true at a rotation the
    # shipping files had never been rendered with (issue #21).
    st[stage] = dict(approved=True, approved_at=_now(),
                     **decmod.stamp(m, stage))
    # A later stage's approval cannot survive an earlier one being revisited.
    for later in stagemod.STAGES[stagemod.STAGES.index(stage) + 1:]:
        st[later] = {"approved": False, "approved_at": None}
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)
    if stage == "orientation":
        # Everything downstream is a measurement of the upright frame. Plan it
        # now, against the rotations that were just approved, so no crop box or
        # backdrop reading can describe a frame that has since been turned.
        plan_geometry(shoot, quiet=True)
        m = load_manifest(shoot)
        print("  planned the crop and the colour reading on those rotations")
    nxt = stagemod.STAGES[stagemod.STAGES.index(stage) + 1:] or None
    print(f"APPROVED stage '{stage}' at {st[stage]['approved_at']}")
    print(f"  next: --stage {nxt[0]}" if nxt else "  all stages approved — --apply writes listing/")
    return m


def _unskewed(img: np.ndarray, sm, sk) -> tuple:
    """Replay a LEGACY unskew onto a frame and its mask, or return them as they
    are. One place, so every consumer — the check pass, the apply pass, the stage
    sheets — is looking at identical pixels.

    The unskew stage is gone. This exists so the frames that were published with
    a warp recorded re-render to the pixels a buyer is already looking at.

    Deliberately does not consult `categories.unskew_applies_for` (#23). That
    field says whether a category's goods HAVE a rectangle worth squaring, were
    the stage ever revived -- it says nothing about a warp a shoot was already
    PUBLISHED with. A category declaring itself unskew-inapplicable must not be
    able to retroactively un-publish a real historical decision; only the
    per-frame recorded `Skew` decides whether this replays anything."""
    if not getattr(sk, "applied", False):
        return img, sm
    return (skewmod.apply(img, sk),
            subjectmod.describe(skewmod.apply_mask(sm.mask, sk),
                                sm.source, sm.agreement, sm.mask_iou))


def _cropped(img: np.ndarray, sm, box) -> tuple:
    """Carry a frame and its mask through the planned crop.

    The mask is CUT, not re-measured -- the same rule `_unskewed` follows, and
    for the same two reasons.

    It is cheaper, though modestly so: a second segmentation costs ~2.7s a
    frame once u2net is loaded. (A cold call looks like ~19s; that is the model
    load, paid once per process, and attributing it per frame overstates this.)

    More importantly it is the only version that is correct. The crop box was
    planned against THIS mask; re-segmenting the cropped pixels answers a
    different question, because both detectors read context the crop just threw
    away -- u2net sees a new composition, and the LAB detector samples a border
    ring that is now the item's own edge rather than the sweep. So the colour
    pass could run against a mask that never agreed with the box it was cropped
    to, which is the shape of the fairy-doll frame that shipped with its magenta
    wings neutralised to grey. Cutting the mask cannot drift from the decision.
    """
    x0, y0, x1, y1 = box
    return (img[y0:y1, x0:x1],
            subjectmod.describe(sm.mask[y0:y1, x0:x1],
                                sm.source, sm.agreement, sm.mask_iou))


def prepared(shoot: Path, name: str, rec: dict) -> np.ndarray:
    """A frame as the crop and colour stages see it: upright, plus any legacy
    unskew this shoot was published with."""
    img = orientmod.rotate_bgr(_load_bgr(shoot / name), rec["orientation"]["applied"])
    sk = skewmod.from_dict(rec.get("unskew"))
    return skewmod.apply(img, sk) if sk.applied else img


def _replan_crop(shoot: Path, m: dict, name: str, rec: dict, pad: float) -> dict:
    """Recompute ONE frame's crop box at the operator's pad.

    The sweep test is what refused the crop in the first place, and an operator
    override is precisely the statement that this frame does have a backdrop to
    trim — a black cloth lit unevenly reads as "no sweep" (high IQR) even when
    there is nothing behind the item but cloth. So the sweep guard is the one
    that yields. Every other guard still stands: the override says the frame is
    worth reframing, not that a crop which would cut the item, split the
    detectors or land under the resolution floor is acceptable.
    """
    from .center_crop import _parse_aspect

    aspect = _parse_aspect(m["settings"].get("aspect", DEFAULT_ASPECT))
    img = orientmod.rotate_bgr(_load_bgr(shoot / name), rec["orientation"]["applied"])
    sm = mask_for(img, subject_mode(m))
    img, sm = _unskewed(img, sm, skewmod.from_dict(rec.get("unskew")))
    stats = colormod.analyze(img, sm.mask)
    crop = plan_crop(img, sm, aspect, pad, stats, sweep=True)
    crop["operator"] = True
    crop["_operator_pad"] = pad
    crop["reason"] = (f"operator: recrop at pad {pad}" if crop["applied"]
                      else f"operator recrop refused — {crop['reason']}")
    return crop


def _match_photo(m: dict, name: str) -> str:
    key = next((k for k in m["photos"] if k.lower() == name.lower()
                or Path(k).stem.lower() == name.lower()), None)
    if key is None:
        raise SystemExit(f"no such photo in the manifest: {name}")
    return key


def run_set_crop(shoot: Path, pairs: list) -> dict:
    """Override the crop decision for named frames: off | on | pad<float>."""
    m = load_manifest(shoot)
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--crop wants NAME=off|on|pad0.20, got {pair!r}")
        name, val = pair.rsplit("=", 1)
        key = next((k for k in m["photos"] if k.lower() == name.lower()
                    or Path(k).stem.lower() == name.lower()), None)
        if key is None:
            raise SystemExit(f"no such photo in the manifest: {name}")
        rec = m["photos"][key]
        val = val.strip().lower()
        if val == "off":
            rec["crop"] = {"applied": False, "reason": "operator: keep as shot",
                           "box": None, "operator": True}
        elif val == "on" or val.startswith("pad"):
            pad = float(val[3:]) if val.startswith("pad") and val[3:] else                 float(m["settings"].get("pad", DEFAULT_PAD))
            rec["crop"] = _replan_crop(shoot, m, key, rec, pad)
        else:
            raise SystemExit(f"{name}: expected off|on|pad<float>, got {val!r}")
        # Report the OUTCOME, not the request. "crop -> on" was printed even
        # when the safety guard refused the recrop, so two frames read as
        # cropped in the log and shipped uncropped.
        c = rec["crop"]
        if c.get("applied"):
            print(f"  {key}: crop -> {val}  [box {c.get('box')}]")
        else:
            print(f"  {key}: crop -> REFUSED — {c.get('reason') or 'no reason recorded'}")
    stagemod.stage_state(m)["crop"] = {"approved": False, "approved_at": None}
    m["approved"] = False
    save_manifest(shoot, m)
    return m


def run_set_detail(shoot: Path, pairs: list[str]) -> dict:
    """Override whether named frames are looking at a BACKDROP or at the item.

    `is_sweep` decides whether the colour pass may re-tone, neutralise and blur
    everything outside the subject mask. That is right when the non-subject
    pixels are a studio cloth and catastrophic when they are the item's own
    mount: an NOS bracelet still stapled to its printed card reads as "smooth
    dark surround", gets promoted to the shoot's backdrop, and has the card's
    colour neutralised out of it. Measured on heart-bracelet P8140023: the
    salmon card went from saturation 90 to 4 — the frame shipped as greyscale.

    No preset can undo this. All four carry `bg_neutralize=1.0`; `crisp` only
    drops the white-balance gain, which is a different knob. The backdrop pass
    is gated on `is_sweep` alone, so this is the switch.

    `NAME=on` marks the frame a detail macro (no backdrop work, the setting the
    tool already reaches on its own for a true close-up). `NAME=off` hands it
    back to the backdrop treatment.
    """
    m = load_manifest(shoot)
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--detail wants NAME=on|off, got {pair!r}")
        name, val = pair.rsplit("=", 1)
        key = next((k for k in m["photos"] if k.lower() == name.lower()
                    or Path(k).stem.lower() == name.lower()), None)
        if key is None:
            raise SystemExit(f"no such photo in the manifest: {name}")
        val = val.strip().lower()
        if val not in ("on", "off"):
            raise SystemExit(f"{name}: expected on|off, got {val!r}")
        cp = m["photos"][key].setdefault("color_plan", {})
        cp["is_sweep"] = (val == "off")
        cp["operator"] = True
        # The crop planner reads the same sweep test, so a frame re-labelled as
        # a detail macro must not keep a crop box that was justified by a
        # backdrop it no longer has.
        if val == "on":
            cp["bg_class_effective"] = "other"
        print(f"  {key}: detail -> {val} (is_sweep={cp['is_sweep']})")
    # A different backdrop decision is a different set of pixels: whatever was
    # rendered and approved before was approved for images that no longer stand.
    stagemod.stage_state(m)["color"] = {"approved": False, "approved_at": None}
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)
    print("  re-run --apply to render with the new backdrop decision")
    return m


def run_approve(shoot: Path) -> dict:
    """Stamp approval. Refuses while anything is unresolved."""
    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit("nothing to approve — run --check/--apply first")

    # Ordered by what the operator has to go and do, most fundamental first.
    # "not rendered yet" last, because before a preset is picked that is true of
    # every frame and says nothing useful.
    asks = [n for n, r in m["photos"].items() if r["orientation"]["needs_ask"]]
    if asks:
        raise SystemExit(
            "cannot approve — orientation unresolved for: " + ", ".join(asks) +
            "\nsee .prep/ask/ and record the answer with --rotate NAME=DEG")

    unapproved = [st for st in stagemod.STAGES
                  if not stagemod.stage_state(m)[st]["approved"]]
    if unapproved:
        raise SystemExit(
            "these review stages are not approved yet: " + ", ".join(unapproved)
            + "  open the first one with --stage "
            + unapproved[0])

    if not m.get("chosen_preset"):
        raise SystemExit(
            "no preset picked yet — compare .prep/prep_presets.jpg, then "
            f"--pick <{'|'.join(colormod.PRESETS)}>")

    unrendered = [n for n, r in m["photos"].items() if not r.get("out_sha256")]
    if unrendered:
        raise SystemExit(f"not rendered yet: {', '.join(unrendered[:5])} — run --apply")

    m["approved"] = True
    m["approved_at"] = _now()
    save_manifest(shoot, m)
    return m


def _invalidate_from(m: dict, stage: str) -> None:
    """A decision changed, so everything downstream of it is no longer agreed.

    PREP already treats a changed FILE as stale. It did not treat a changed
    DECISION that way, and the two are equally dangerous: paul-fredrick sat at
    `approved: true` with every stage signed off while six frames carried a
    rotation the shipping files had never been rendered with. The manifest said
    270 degrees; listing/ held the portrait frame it had before. Nothing in the
    gate noticed, because no file had changed.

    So recording an answer clears the publish stamp and every stage approval
    from that stage onward. Re-approving is cheap; shipping a frame nobody
    approved in its current form is not.
    """
    from . import stages as stagemod

    st = stagemod.stage_state(m)
    order = list(stagemod.STAGES)
    for later in order[order.index(stage):]:
        st[later] = {"approved": False, "approved_at": None}
    m["approved"] = False
    m["approved_at"] = None


def run_rotate(shoot: Path, pairs: list[str], absolute: bool = False) -> dict:
    """Record looked-at orientation answers (`NAME=90`).

    Two forms, and the difference matters:

      --rotate      DEG is RELATIVE to what the sheet currently shows. That is
                    the frame the answer was given against, so a human reading a
                    sheet does no arithmetic.
      --set-rotate  DEG is the ABSOLUTE subject angle. This is what a generated
                    command must use: a relative command is not idempotent, and
                    a page that hands the operator `NAME=270` twice moves the
                    frame twice. That happened — the same pasted line took a
                    catalog spread from applied 0 to 270 on its second run.
    """
    m = load_manifest(shoot)
    touched = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--rotate wants NAME=DEG, got {pair!r}")
        name, deg_s = pair.rsplit("=", 1)
        deg = int(deg_s) % 360
        if deg not in (0, 90, 180, 270):
            raise SystemExit(f"{name}: rotation must be 0/90/180/270, got {deg_s}")

        key = next((k for k in m["photos"] if k.lower() == name.lower()
                    or Path(k).stem.lower() == name.lower()), None)
        if key is None:
            raise SystemExit(f"no such photo in the manifest: {name}")

        o = m["photos"][key]["orientation"]
        # DEG is relative to what the sheet currently SHOWS — that is the frame
        # the answer was given against. Absolute-from-original would mean doing
        # the arithmetic by hand against a rotation you cannot see, which is a
        # good way to record a confident wrong number. 0 is a real answer here:
        # "looked at it, it is upright."
        subject = deg if absolute else ((o.get("subject_angle") or 0) + deg) % 360
        o["vision_angle"] = subject
        o["subject_angle"] = subject
        o["applied"] = ((o.get("exif_angle") or 0) + subject) % 360
        o["source"] = "exif+vision" if o.get("exif_angle") else "vision"
        o["needs_ask"] = False
        m["photos"][key]["status"] = "SHIP"
        touched.append(key)
        print(f"  {key}: {'=' if absolute else '+'}{deg}° → subject {subject}°, "
              f"total applied {o['applied']}° (recorded look)")

    # A rotation change invalidates the rendered files and any approval.
    m["approved"] = False
    m["approved_at"] = None
    _invalidate_from(m, "orientation")
    for key in touched:
        o = m["photos"][key]["orientation"]
        # The frame HAS now been looked at, by whoever recorded this answer. A
        # note saying it needs a look outlives the look otherwise, and every
        # sheet and card downstream keeps flagging a frame that was answered.
        o.pop("guessed", None)
        o["notes"] = [n for n in (o.get("notes") or [])
                      if "needs a look" not in n and "auto first pass" not in n]
    save_manifest(shoot, m)
    _sync_orientation_json(shoot, m)

    # A shoot still sitting in the auto first pass has crop boxes measured on
    # the rotations that just changed. Re-plan them now, in the same breath, or
    # the widget and the card the operator is about to be shown would picture a
    # crop from a frame that no longer exists — the exact failure the staged
    # order exists to prevent, arriving through the back door.
    if (m.get("auto") or {}).get("at") and not stagemod.stage_state(m)["orientation"]["approved"]:
        m = plan_geometry(shoot, quiet=True, provisional=True)
        print("  re-planned the crop on the corrected rotations")
    return m


def _sync_orientation_json(shoot: Path, m: dict) -> None:
    """Mirror recorded rotations into the sibling `orient.py` manifest.

    `orientation.json` and `.prep/prep.json` record the SAME quantity — degrees
    clockwise applied to the subject on top of the EXIF bake — and two files
    holding one fact is how they come to disagree. PREP already reads
    orientation.json as an answer; writing back closes the loop, so a call
    recorded in either place is the call in both.

    Only non-zero angles are written, matching `orient.py`'s own convention
    where an absent entry means "no rotation" (its `--set ok` deletes the key).
    A confirmed-upright zero still lives in prep.json, which is what the gate
    reads.
    """
    angles = {n: r["orientation"]["subject_angle"]
              for n, r in m.get("photos", {}).items()
              if r.get("orientation", {}).get("subject_angle")}
    path = shoot / "orientation.json"
    if not angles and not path.exists():
        return
    try:
        path.write_text(json.dumps(dict(sorted(angles.items())), indent=2),
                        encoding="utf-8")
    except OSError:
        pass          # the record in prep.json is authoritative; this is a mirror


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------

def _fit(img: np.ndarray, cell: int) -> np.ndarray:
    import cv2
    h, w = img.shape[:2]
    s = cell / max(h, w)
    r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    canvas = np.full((cell, cell, 3), 30, np.uint8)
    yo, xo = (cell - r.shape[0]) // 2, (cell - r.shape[1]) // 2
    canvas[yo:yo + r.shape[0], xo:xo + r.shape[1]] = r
    return canvas


def _label(width: int, text: str, colour=(235, 235, 235), h: int = 24) -> np.ndarray:
    """A caption bar that grows to fit rather than cutting the text off.

    The per-frame caption IS the audit trail — rotation source, crop verdict and
    reason, backdrop move, strength. Truncating it at a fixed width hid exactly
    the parts that say why something was skipped, which is the half worth
    reading. So: shrink the font to fit, and wrap onto a second line if that is
    not enough.
    """
    import cv2
    font, thick = cv2.FONT_HERSHEY_SIMPLEX, 1
    scale = 0.45
    avail = width - 12
    while scale > 0.30 and cv2.getTextSize(text, font, scale, thick)[0][0] > avail:
        scale -= 0.02

    lines = [text]
    if cv2.getTextSize(text, font, scale, thick)[0][0] > avail:
        # Break on a separator so a field never splits across lines.
        parts, cur = text.split("  |  "), ""
        lines = []
        for p in parts:
            trial = f"{cur}  |  {p}" if cur else p
            if cv2.getTextSize(trial, font, scale, thick)[0][0] > avail and cur:
                lines.append(cur)
                cur = p
            else:
                cur = trial
        if cur:
            lines.append(cur)

    row_h = max(16, h - 6)
    bar = np.full((row_h * len(lines) + 8, width, 3), 20, np.uint8)
    for i, line in enumerate(lines):
        cv2.putText(bar, line, (6, row_h * (i + 1)), font, scale, colour, thick, cv2.LINE_AA)
    return bar


def _rotation_sheet(thumbs, photos: dict, out_path: Path,
                    cell: int = 300, cols: int = 4) -> None:
    """Every frame exactly as the manifest says it will be rotated.

    Reads the FINAL state rather than the in-loop verdict, so a reading that
    corroboration downgraded shows un-rotated and flagged, not applied. The
    label carries the downgraded proposal so the reason is visible.
    """
    import cv2
    if not thumbs:
        return
    tiles = []
    for name, cam in thumbs:
        o = (photos.get(name) or {}).get("orientation", {})
        img = orientmod.rotate_bgr(cam, o.get("subject_angle", 0))
        ask = o.get("needs_ask")
        colour = (120, 200, 255) if ask else (200, 235, 200)
        if ask:
            prop = o.get("osd_proposal")
            tag = "ASK" + (f" (OSD guessed {prop}deg, not corroborated)" if prop else "")
        else:
            tag = f"{o.get('applied', 0)}deg {o.get('source', '?')}"
        tiles.append(np.vstack([_label(cell, f"{name}  {tag}", colour), _fit(img, cell)]))
    # Captions wrap, so tiles differ in height; pad to the row's tallest before
    # hstack (which would otherwise raise).
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        while len(row) < cols:
            row.append(np.full((cell, cell, 3), 15, np.uint8))
        tall = max(t.shape[0] for t in row)
        row = [np.vstack([t, np.full((tall - t.shape[0], t.shape[1], 3), 15, np.uint8)])
               if t.shape[0] < tall else t for t in row]
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        buf.tofile(str(out_path))


def _presets_sheet(rows, out_path: Path, cell: int = 340) -> None:
    """One row per frame: the original, then every preset, labelled.

    This is the sheet the choice is made from, so the presets sit side by side
    at the same size against the same original — a look picked from separate
    sheets is picked on memory, not comparison.
    """
    import cv2
    if not rows:
        return
    names = [p for p in colormod.PRESETS if p in (rows[0][2] if rows else {})] or list(colormod.PRESETS)
    header = [_label(cell, "ORIGINAL", (170, 170, 170), 26)]
    for p in names:
        header.append(_label(cell, p.upper(), (160, 235, 170), 26))
    strips = [np.hstack(header)]

    for name, before, variants, rec in rows:
        o = rec["orientation"]
        c = rec["crop"]
        bits = [f"rot {o['applied']}deg ({o['source']})",
                "crop yes" if c["applied"] else f"crop no ({c['reason'][:38]})"]
        colour = (120, 200, 255) if o["needs_ask"] else (200, 235, 200)
        strips.append(_label(cell * (len(names) + 1), f"{name}   " + "  |  ".join(bits),
                             colour, 26))

        tiles = [_fit(before, cell)]
        for p in names:
            tiles.append(_fit(variants[p], cell))
        strips.append(np.hstack(tiles))

    sheet = np.vstack(strips)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        buf.tofile(str(out_path))


def _review_sheet(rows, out_path: Path, cell: int = 340) -> None:
    """before | after per frame, labelled with what actually changed."""
    import cv2
    if not rows:
        return
    strips = []
    for name, before, after, rec in rows:
        o, c, col = rec["orientation"], rec["crop"], rec.get("color", {})
        bits = [f"rot {o['applied']}deg ({o['source']})"]
        bits.append("crop yes" if c["applied"] else f"crop no ({c['reason'][:34]})")
        if col:
            bits.append(f"bg {col['bg_luma_before']:.0f}->{col['bg_luma_after']:.0f}")
            bits.append(f"str {col['strength']}")
            if col["subject_newly_clipped"] or col["subject_newly_crushed"]:
                bits.append(f"rails +{col['subject_newly_clipped']}/"
                            f"{col['subject_newly_crushed']}")
        colour = (120, 200, 255) if o["needs_ask"] else (200, 235, 200)
        pair = np.hstack([_fit(before, cell), np.full((cell, 6, 3), 60, np.uint8), _fit(after, cell)])
        strips.append(np.vstack([
            _label(pair.shape[1], f"{name}   " + "  |  ".join(bits), colour, 26),
            pair,
        ]))
    sheet = np.vstack(strips)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        buf.tofile(str(out_path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_status(shoot: Path, m: dict) -> None:
    photos = m.get("photos", {})
    asks = [n for n, r in photos.items() if r["orientation"]["needs_ask"]]
    cropped = sum(1 for r in photos.values() if (r.get("crop") or {}).get("applied"))
    print(f"\n{shoot.name}: {len(photos)} photos · {cropped} cropped · "
          f"{len(asks)} awaiting an orientation answer")
    chosen = m.get("chosen_preset")
    src = m.get("preset_source")
    print(f"preset:   {chosen or 'none'}"
          + (f" ({src}; compare .prep/prep_presets.jpg, --pick to change)" if chosen else ""))
    print(f"approved: {m.get('approved')}"
          + (f" ({m['approved_at']})" if m.get("approved") else ""))
    if asks:
        print("  ASK: " + ", ".join(asks))
        print(f"  panels: {shoot / '.prep' / 'ask'}")
    print(f"  review: {shoot / '.prep' / 'prep_review.jpg'}")


# EVERY OCCURRENCE OF A PER-FRAME FLAG COUNTS, NOT JUST THE LAST ONE.
#
# These flags take NAME=VALUE pairs, and a generated command routinely repeats
# the flag once per frame rather than listing thirteen values after one. With a
# plain nargs="+" argparse keeps only the final occurrence and drops the rest
# in silence: `--crop A=off --crop B=off` set B and left A cropped, with the log
# showing one line and the operator believing both had been set. `action=
# "append"` keeps them all; this flattens the list-of-lists back into the flat
# pair list every run_* helper already expects.
def _pairs(v) -> list:
    """Flatten an appended nargs list-of-lists into one list (None -> [])."""
    if not v:
        return []
    return [x for group in v for x in group]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="PREP — orientation, crop and colour for one shoot.")
    ap.add_argument("shoot_dir")
    ap.add_argument("--check", action="store_true", help="analyse + plan; render nothing")
    ap.add_argument("--auto", action="store_true",
                    help="first pass: resolve orientation AND plan the crop in one go, "
                         "asking nothing and approving nothing (guessed frames are flagged)")
    ap.add_argument("--approve-auto", action="store_true", dest="approve_auto",
                    help="sign off the auto pass as it stands (orientation + crop together)")
    ap.add_argument("--apply", action="store_true", help="render listing/ + the review sheet")
    ap.add_argument("--pick", metavar="PRESET",
                    help="adopt a preset for the shoot (copies it into listing/)")
    ap.add_argument("--stage", metavar="NAME",
                    help="open a review stage: orientation | crop | color")
    ap.add_argument("--approve-stage", metavar="NAME",
                    help="sign off ONE stage (orientation | crop | color)")
    ap.add_argument("--detail", action="append", nargs="+", metavar="NAME=on|off",
                    help="mark named frames as detail macros (on) so the colour pass leaves "
                         "the non-subject pixels alone — use when the 'backdrop' is the item's "
                         "own card, box or mount; off hands them back to backdrop treatment")
    ap.add_argument("--crop", action="append", nargs="+", metavar="NAME=off|on|padF",
                    help="override the crop decision for named frames")
    ap.add_argument("--sheet", action="store_true",
                    help="rebuild the rotation sheet from the manifest (no segmentation)")
    ap.add_argument("--repoint-draft", action="store_true",
                    help="point draft.md's photos: list at listing/ (order preserved). Dry run unless --apply-repoint.")
    ap.add_argument("--apply-repoint", action="store_true",
                    help="actually write draft.md for --repoint-draft")
    ap.add_argument("--approve", action="store_true", help="stamp approval (explicit operator yes only)")
    ap.add_argument("--rotate", action="append", nargs="+", metavar="NAME=DEG", help="record a looked-at orientation answer (DEG is RELATIVE to what the sheet shows)")
    ap.add_argument("--only", action="append", nargs="+", metavar="PRESET", help="render ONLY these presets (default: just `crisp` — the house default; --filters for every preset)")
    ap.add_argument("--filters", action="store_true",
                    help="render every preset in color.PRESETS for a side-by-side "
                         "comparison, instead of just `crisp` (default). Overridden "
                         "by an explicit --only.")
    ap.add_argument("--resume", action="store_true",
                    help="with --apply: skip a frame's re-render when the manifest "
                         "proves the existing render still answers this run's "
                         "settings (same aspect/pad/pop/subject/category/presets, "
                         "source unchanged, preset files present and unmodified). "
                         "Any one check failing re-renders the frame. Re-invoke a "
                         "backgrounded --apply that got killed by a timeout with "
                         "this flag rather than restarting it plain -- that is the "
                         "whole point (default: off)")
    ap.add_argument("--set-rotate", action="append", nargs="+", metavar="NAME=DEG", dest="set_rotate", help="record the ABSOLUTE subject angle — idempotent, use this in generated commands")
    ap.add_argument("--gc", action="store_true",
                    help="list the regenerable byproducts an APPROVED shoot is still holding "
                         "(unchosen preset renders, answered ask panels); removes nothing")
    ap.add_argument("--gc-force", action="store_true", dest="gc_force",
                    help="with --gc: actually delete them (inventory/ is gitignored — there is no undo)")
    ap.add_argument("--status", action="store_true", help="print the manifest summary")
    ap.add_argument("--decisions", action="store_true",
                    help="print the decision record and its digest, and report "
                         "any stage whose decisions have changed since sign-off")
    # No hardcoded default here any more: None means "nothing explicit", and
    # main() below resolves that to the CATEGORY's aspect/pad (categories.py
    # #23) before falling back to DEFAULT_ASPECT/DEFAULT_PAD -- so an explicit
    # flag still outranks the category, exactly like --subject already does.
    ap.add_argument("--aspect", default=None,
                    help=f"target aspect W:H (default {DEFAULT_ASPECT}, or the "
                         f"category's own if it sets one; 'orig' to keep)")
    ap.add_argument("--pad", type=float, default=None,
                    help=f"margin around the subject, as a fraction of its own "
                         f"box (default {DEFAULT_PAD}, or the category's own "
                         f"if it sets one)")
    ap.add_argument("--category", default=None, choices=list(catmod.names()),
                    help="what KIND of goods this shoot is: sets the subject "
                         "detector and which looks are rendered, in one flag. "
                         "Persists in the manifest. See photo_prep/categories.py")
    ap.add_argument("--subject", default=None, choices=["auto", "paper"],
                    help="what the item IS: 'paper' for flat printed goods "
                         "(magazines, catalogs, sleeves) where the salient "
                         "object is the picture printed on the item, not the "
                         "item. Persists in the manifest.")
    ap.add_argument("--pop", default="gentle", choices=["off", "gentle", "strong"],
                    help="subject pass: saturation/contrast/unsharp (default gentle)")
    ap.add_argument("--check-rotation", action="store_true", dest="check_rotation",
                    help="run the OSD rotation check + ask panel for frames it "
                         "can't read (default: OFF — items are shot right-side "
                         "up, so every frame with no recorded --rotate answer "
                         "is assumed upright and no OSD pass runs at all)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    shoot = Path(args.shoot_dir)
    if not shoot.is_dir():
        ap.error(f"not a directory: {shoot}")

    # WHAT IS BEING PHOTOGRAPHED IS RECORDED ONCE, AND READ BACK BY EVERY PASS.
    #
    # The category and the detector it implies are properties of the ITEM, not
    # of this invocation, so they persist in the manifest. A later `--check` or
    # `--apply` that does not repeat the flag has to get the same answer --
    # otherwise a batch re-check silently demotes a paper shoot back to `auto`
    # and re-measures every box against a mask of the cover model.
    #
    # `--subject` stays available on its own as the escape hatch for a shoot its
    # category gets wrong, and it outranks the category when both are given.
    _m0 = load_manifest(shoot)
    stored_cat = category_of(_m0)
    stored_subject = _m0.get("settings", {}).get("subject", DEFAULT_SUBJECT)

    category = args.category or stored_cat
    subject = args.subject or (catmod.subject_for(args.category) if args.category
                               else stored_subject)

    # Framing is category-shaped too (#23): a flat catalog and a ring do not
    # want the same margin. Same precedence as --subject above -- an explicit
    # flag always outranks the category, which is the one place a category may
    # feed a MEASUREMENT (plan_crop's aspect/pad inputs), never its result.
    cat_pad = catmod.pad_for(category)
    aspect_s = args.aspect if args.aspect is not None else (
        catmod.aspect_for(category) or DEFAULT_ASPECT)
    pad = args.pad if args.pad is not None else (
        cat_pad if cat_pad is not None else DEFAULT_PAD)

    # Changing either one re-measures every box downstream, so the sign-offs
    # those boxes earned are dropped HERE rather than silently carried over onto
    # different numbers. This is the invalidation issue #21 wants to make
    # structural; until the decision record lands it is done explicitly, at the
    # one place both settings can change.
    if category != stored_cat or subject != stored_subject:
        m = load_manifest(shoot)
        m.setdefault("settings", {})["category"] = category
        m["settings"]["subject"] = subject
        for st in ("crop", "color"):
            if m.get("stages", {}).get(st):
                m["stages"][st] = {"approved": False, "approved_at": None}
        m["approved"] = False
        m["approved_at"] = None
        save_manifest(shoot, m)
        print(f"category: {stored_cat} -> {category}  "
              f"(subject {stored_subject} -> {subject}; "
              f"crop/colour sign-off dropped; re-run --check)")
        print(f"  {catmod.describe(category)}")

    if args.rotate:
        m = run_rotate(shoot, _pairs(args.rotate))
    if getattr(args, "set_rotate", None):
        m = run_rotate(shoot, _pairs(args.set_rotate), absolute=True)
        _print_status(shoot, m)
        return 0
    if args.auto:
        run_auto(shoot, aspect_s, pad, args.pop,
                 subject=subject, category=category, quiet=args.quiet,
                 check_rotation=args.check_rotation)
        return 0
    if args.approve_auto:
        run_approve_auto(shoot)
        return 0
    if args.stage:
        run_stage(shoot, args.stage, quiet=args.quiet)
        return 0
    if args.approve_stage:
        run_approve_stage(shoot, args.approve_stage)
        return 0
    if args.crop:
        run_set_crop(shoot, _pairs(args.crop))
        return 0
    if args.detail:
        run_set_detail(shoot, _pairs(args.detail))
        return 0
    if args.sheet:
        run_sheet(shoot, quiet=args.quiet)
        return 0
    if args.repoint_draft:
        run_repoint_draft(shoot, apply=args.apply_repoint)
        return 0
    if args.pick:
        m = run_pick(shoot, args.pick, quiet=args.quiet)
        _print_status(shoot, m)
        return 0
    if args.gc:
        run_gc(shoot, force=args.gc_force)
        return 0
    if args.approve:
        m = run_approve(shoot)
        print(f"APPROVED {shoot.name} at {m['approved_at']} — "
              f"{len(m['photos'])} photos cleared for DRAFT.")
        return 0
    if args.decisions:
        m = load_manifest(shoot)
        rec = decmod.record_for(m)
        print(json.dumps(rec, indent=2, sort_keys=True))
        print(f"digest: {decmod.digest(rec)}")
        stale = decmod.stale_stages(m, stagemod.STAGES)
        if stale:
            print()
            print("DECISIONS CHANGED SINCE SIGN-OFF:")
            for stage, why in stale:
                print(f"  {stage}:")
                for w in why:
                    print(f"    {w}")
        else:
            print("every approved stage still matches its decisions")
        return 0
    if args.status:
        _print_status(shoot, load_manifest(shoot))
        return 0

    if args.check or not args.apply:
        print(f"PREP check — {shoot}")
        m = run_check(shoot, aspect_s, pad, args.pop, subject, category,
                      quiet=args.quiet, check_rotation=args.check_rotation)
        _print_status(shoot, m)
        print(f"  rotation sheet: {shoot / '.prep' / 'rotation_sheet.jpg'}")
    if args.apply:
        print(f"PREP apply — {shoot}")
        if not load_manifest(shoot).get("photos"):
            run_check(shoot, aspect_s, pad, args.pop, subject, category,
                      quiet=True, check_rotation=args.check_rotation)
        # An explicit --only always wins. --filters is the "show me every look"
        # escape hatch for a shoot whose category would otherwise narrow to one.
        only = tuple(_pairs(getattr(args, 'only', None)))
        if not only and args.filters:
            only = tuple(colormod.PRESETS)
        m = run_apply(shoot, quiet=args.quiet, only=only, resume=args.resume)
        _print_status(shoot, m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
