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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from . import color as colormod
from . import orientation as orientmod
from . import stages as stagemod
from . import subject as subjectmod
from .subject import mask_for

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}
MANIFEST_VERSION = 1

MIN_AGREEMENT = 0.75     # detector bbox containment below which we do not crop
MIN_LONG_SIDE = 1400     # eBay wants >=1600 for zoom; never crop below this
DEFAULT_ASPECT = "1:1"
DEFAULT_PAD = 0.12


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
    import cv2
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"could not encode {path}")
    buf.tofile(str(path))


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Where a shoot's source frames may live when the originals are gone, in the
# order we would rather have them. `listing-photos/` and `no-exif/` hold derived
# copies from earlier workflows — EXIF already baked, otherwise full size — and
# for several live listings they are the ONLY surviving frames. Preferring the
# originals and falling back to these is what lets those listings be processed
# at all; refusing them just leaves the listing unfixed.
SOURCE_FALLBACKS = ("listing-photos", "no-exif")


def find_images(shoot: Path) -> list[Path]:
    """The shoot's source frames. Top-level originals win.

    Never recurses into listing/ or .prep/ — those are our own output, and
    reading them back would compound a correction on top of a correction.
    """
    out, seen = [], set()
    for p in sorted(shoot.iterdir()):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
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
                    and p.stem.lower() not in seen):
                out.append(p)
                seen.add(p.stem.lower())
    return out


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def manifest_path(shoot: Path) -> Path:
    return shoot / ".prep" / "prep.json"


def load_manifest(shoot: Path) -> dict:
    p = manifest_path(shoot)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
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
    }


def save_manifest(shoot: Path, m: dict) -> None:
    m["updated"] = _now()
    p = manifest_path(shoot)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=2), encoding="utf-8")


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


def run_check(shoot: Path, aspect_s: str, pad: float, pop: str, quiet: bool = False) -> dict:
    """Analyse every frame; write the manifest, rotation sheet and ask panels."""
    from .center_crop import _parse_aspect

    images = find_images(shoot)
    if not images:
        raise SystemExit(f"no images in {shoot}")

    aspect = _parse_aspect(aspect_s)
    m = load_manifest(shoot)
    m["settings"] = dict(aspect=aspect_s, pad=pad, pop=pop)
    prior = m.get("photos", {})
    photos: dict = {}

    osd_on = orientmod.osd_available()
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
        sm = mask_for(cam)
        osd = (orientmod.osd_angle(_subject_region(cam, sm.bbox))
               if osd_on else (None, 0.0, "tesseract not installed"))

        # A recorded look for this frame survives a re-run. The sibling
        # orient.py tool stores the same quantity (degrees CW on top of the EXIF
        # bake), so its manifest counts as an answer here — a rotation the user
        # already called there must not come back as a fresh ASK.
        prev = prior.get(key, {}) or prior.get(path.name, {})
        vision = prev.get("orientation", {}).get("vision_angle")
        if vision is None:
            vision = looks.get(key) or looks.get(path.name)
        v = orientmod.resolve(path.name, exif_tag, osd, vision)

        # Segmentation is rotation-invariant — turn the mask with the image.
        upright = orientmod.rotate_bgr(cam, v.subject_angle)
        sm = subjectmod.describe(orientmod.rotate_bgr(sm.mask, v.subject_angle),
                                 sm.source, sm.agreement, sm.mask_iou)
        stats = colormod.analyze(upright, sm.mask)
        crop = plan_crop(upright, sm, aspect, pad, stats)

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
            "crop": crop,
            "color_plan": {"bg_class": stats.bg_class, "bg_luma": round(stats.bg_luma, 1),
                           "bg_iqr": round(stats.bg_iqr, 1), "bg_rough": round(stats.bg_rough, 1),
                           "is_sweep": stats.is_sweep,
                           "bg_class_effective": stats.bg_class},
            "status": "ASK" if v.needs_ask else ("SHIP" if crop["applied"] else "PASSTHROUGH"),
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

        if not quiet:
            rot = f"exif {v.exif_angle}+subj {v.subject_angle}={v.applied}"
            print(f"  {key:28} {rot:<26} {v.source:<12} "
                  f"{'crop' if crop['applied'] else 'no crop: ' + crop['reason'][:36]:<48} "
                  f"bg={stats.bg_class}" + ("  [ASK]" if v.needs_ask else ""))

    m["photos"] = photos
    _corroborate_orientation(photos, quiet)
    m["backdrop"] = _unify_backdrop(photos, quiet)
    # Any earlier approval is void: what the operator approved is not what the
    # manifest now describes.
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)

    _rotation_sheet(thumbs, photos, shoot / ".prep" / "rotation_sheet.jpg")
    return m


def run_apply(shoot: Path, quiet: bool = False) -> dict:
    """Render listing/ from the manifest's decisions + the review sheet."""
    from .center_crop import _parse_aspect

    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit("nothing planned — run --check first")

    aspect = _parse_aspect(m["settings"].get("aspect", DEFAULT_ASPECT))
    pad = float(m["settings"].get("pad", DEFAULT_PAD))
    pop = m["settings"].get("pop", "gentle")

    out_dir = shoot / "listing"
    out_dir.mkdir(exist_ok=True)
    rows = []

    for name, rec in m["photos"].items():
        src = shoot / name
        if not src.exists():
            rec["status"] = "MISSING"
            continue

        before = _load_bgr(src)
        img = orientmod.rotate_bgr(before, rec["orientation"]["applied"])

        # Re-segment on the upright pixels: the crop box in the manifest was
        # planned there, and the colour pass needs the same mask.
        sm = mask_for(img)
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
            x0, y0, x1, y1 = crop["box"]
            img = img[y0:y1, x0:x1]
            sm = mask_for(img)

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
        for pname in colormod.PRESETS:
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

        creport = rec["presets"][colormod.DEFAULT_PRESET]["report"]
        rec["color"] = creport
        # `listing/` stays empty until a preset is picked — the whole point is
        # that the operator chooses the look, so nothing may default into the
        # directory DRAFT reads.
        rec["output"] = None
        rec["out_sha256"] = None
        rec["status"] = "ASK" if rec["orientation"]["needs_ask"] else "PICK"

        rows.append((name, before, variants, rec))
        if not quiet:
            c = creport
            print(f"  {name:28} {len(variants)} presets  "
                  f"bg {c['bg_luma_before']:.0f}->{c['bg_luma_after']:.0f} "
                  f"str={c['strength']} new-clip={c['subject_newly_clipped']} "
                  f"new-crush={c['subject_newly_crushed']}")

    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)
    _presets_sheet(rows, shoot / ".prep" / "prep_presets.jpg")

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
    default = colormod.default_preset_for(shoot_class, warm_subject=warm)
    save_manifest(shoot, m)
    m = run_pick(shoot, default, quiet=True, auto=True)
    if not quiet:
        warm_note = (f", warm-metal subject (median R-B {shoot_rb:.0f})" if warm
                     else f" (median R-B {shoot_rb:.0f})")
        print(f"  default look for a {shoot_class or 'mixed'} backdrop{warm_note}: "
              f"{default} (--pick {'|'.join(colormod.PRESETS)} to change)")
    return m


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


def run_approve_stage(shoot: Path, stage: str) -> dict:
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
    st[stage] = {"approved": True, "approved_at": _now()}
    # A later stage's approval cannot survive an earlier one being revisited.
    for later in stagemod.STAGES[stagemod.STAGES.index(stage) + 1:]:
        st[later] = {"approved": False, "approved_at": None}
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)
    nxt = stagemod.STAGES[stagemod.STAGES.index(stage) + 1:] or None
    print(f"APPROVED stage '{stage}' at {st[stage]['approved_at']}")
    print(f"  next: --stage {nxt[0]}" if nxt else "  all stages approved — --apply writes listing/")
    return m


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
    sm = mask_for(img)
    stats = colormod.analyze(img, sm.mask)
    crop = plan_crop(img, sm, aspect, pad, stats, sweep=True)
    crop["operator"] = True
    crop["_operator_pad"] = pad
    crop["reason"] = (f"operator: recrop at pad {pad}" if crop["applied"]
                      else f"operator recrop refused — {crop['reason']}")
    return crop


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
        print(f"  {key}: crop -> {val}")
    stagemod.stage_state(m)["crop"] = {"approved": False, "approved_at": None}
    m["approved"] = False
    save_manifest(shoot, m)
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


def run_rotate(shoot: Path, pairs: list[str]) -> dict:
    """Record looked-at orientation answers (`NAME=90`)."""
    m = load_manifest(shoot)
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
        subject = ((o.get("subject_angle") or 0) + deg) % 360
        o["vision_angle"] = subject
        o["subject_angle"] = subject
        o["applied"] = ((o.get("exif_angle") or 0) + subject) % 360
        o["source"] = "exif+vision" if o.get("exif_angle") else "vision"
        o["needs_ask"] = False
        m["photos"][key]["status"] = "SHIP"
        print(f"  {key}: +{deg}° → subject {subject}°, total applied "
              f"{o['applied']}° (recorded look)")

    # A rotation change invalidates the rendered files and any approval.
    m["approved"] = False
    m["approved_at"] = None
    save_manifest(shoot, m)
    _sync_orientation_json(shoot, m)
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
    names = list(colormod.PRESETS)
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
    cropped = sum(1 for r in photos.values() if r["crop"]["applied"])
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


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="PREP — orientation, crop and colour for one shoot.")
    ap.add_argument("shoot_dir")
    ap.add_argument("--check", action="store_true", help="analyse + plan; render nothing")
    ap.add_argument("--apply", action="store_true", help="render listing/ + the review sheet")
    ap.add_argument("--pick", metavar="PRESET",
                    help="adopt a preset for the shoot (copies it into listing/)")
    ap.add_argument("--stage", metavar="NAME",
                    help="open a review stage: orientation | crop | color")
    ap.add_argument("--approve-stage", metavar="NAME",
                    help="sign off ONE stage (orientation | crop | color)")
    ap.add_argument("--crop", nargs="+", metavar="NAME=off|on|padF",
                    help="override the crop decision for named frames")
    ap.add_argument("--sheet", action="store_true",
                    help="rebuild the rotation sheet from the manifest (no segmentation)")
    ap.add_argument("--repoint-draft", action="store_true",
                    help="point draft.md's photos: list at listing/ (order preserved). Dry run unless --apply-repoint.")
    ap.add_argument("--apply-repoint", action="store_true",
                    help="actually write draft.md for --repoint-draft")
    ap.add_argument("--approve", action="store_true", help="stamp approval (explicit operator yes only)")
    ap.add_argument("--rotate", nargs="+", metavar="NAME=DEG", help="record a looked-at orientation answer")
    ap.add_argument("--status", action="store_true", help="print the manifest summary")
    ap.add_argument("--aspect", default=DEFAULT_ASPECT, help="target aspect W:H (default 1:1; 'orig' to keep)")
    ap.add_argument("--pad", type=float, default=DEFAULT_PAD, help="margin around the subject (default 0.12)")
    ap.add_argument("--pop", default="gentle", choices=["off", "gentle", "strong"],
                    help="subject pass: saturation/contrast/unsharp (default gentle)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    shoot = Path(args.shoot_dir)
    if not shoot.is_dir():
        ap.error(f"not a directory: {shoot}")

    if args.rotate:
        m = run_rotate(shoot, args.rotate)
        _print_status(shoot, m)
        return 0
    if args.stage:
        run_stage(shoot, args.stage, quiet=args.quiet)
        return 0
    if args.approve_stage:
        run_approve_stage(shoot, args.approve_stage)
        return 0
    if args.crop:
        run_set_crop(shoot, args.crop)
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
    if args.approve:
        m = run_approve(shoot)
        print(f"APPROVED {shoot.name} at {m['approved_at']} — "
              f"{len(m['photos'])} photos cleared for DRAFT.")
        return 0
    if args.status:
        _print_status(shoot, load_manifest(shoot))
        return 0

    if args.check or not args.apply:
        print(f"PREP check — {shoot}")
        m = run_check(shoot, args.aspect, args.pad, args.pop, quiet=args.quiet)
        _print_status(shoot, m)
        print(f"  rotation sheet: {shoot / '.prep' / 'rotation_sheet.jpg'}")
    if args.apply:
        print(f"PREP apply — {shoot}")
        if not load_manifest(shoot).get("photos"):
            run_check(shoot, args.aspect, args.pad, args.pop, quiet=True)
        m = run_apply(shoot, quiet=args.quiet)
        _print_status(shoot, m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
