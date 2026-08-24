#!/usr/bin/env python3
"""HERO MONTAGE — compose the gallery frame from two or three prepped views.

The studied seller does this on roughly 18% of their listings (measured over
two random samples of their live heroes), and always for the same reason: one
frame cannot carry both what the thing IS and the one detail that sells it —
the maker's imprint, the piece open, the original box it came in. So the hero
carries both, and a scrolling buyer gets the whole story at thumbnail size.

Two forms, both taken from their listings:

  inset   the main view full-bleed, with a smaller panel dropped into a
          corner over it, white-bordered. Their choice when the second view is
          a DETAIL of the same object (a signature, a hallmark, a nib).
  split   two or three equal panels side by side. Their choice when the second
          view is a different STATE or a different OBJECT — knife open beside
          knife closed, pendant beside the box it came in.

Composes from `<shoot>/listing/` — the prepped, approved frames, never the
raw ones — so the montage inherits the orientation, crop and colour the
operator already signed off. Writes a proposal for review; `--apply` puts it
in `listing/` as the first file and (with --repoint) makes it the draft's
gallery image.

  propose <shoot> [--frames 1,4] [--layout auto|inset|split]
  apply   <shoot> [--frames ...] [--repoint]

Honesty note this tool cannot enforce: a montage must not imply the buyer
gets more than one item. Panels show ONE listing's contents. If two panels
look like two objects for sale, that is a misleading gallery image, and the
fix is a different pair of frames — see `prompts/prep.md`.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from PIL import Image, ImageOps

CANVAS = 1600                 # eBay serves the gallery image well above this
BG = (255, 255, 255)
HAIRLINE = (214, 214, 214)    # the faint edge their inset panels carry
MARGIN = 0.025                # canvas fraction: outer breathing room
GUTTER = 0.018                # canvas fraction: between split panels
INSET_W = 0.34                # canvas fraction: inset panel width
INSET_BORDER = 7              # px of white around an inset, then the hairline

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


# --------------------------------------------------------------------------
# frame measurement — which frames deserve the hero, and in what form
# --------------------------------------------------------------------------
def subject_fill(im: Image.Image) -> float:
    """Roughly how much of the frame the subject occupies, 0–100.

    Same border-reference trick the seller study uses: sample the outer band
    for the backdrop, then count central pixels that differ from it. A macro
    of a hallmark fills the frame; a piece centred on a sweep does not.
    """
    g = im.convert("RGB").resize((160, 160))
    px = g.load()
    band = 12
    border = [px[x, y] for y in range(160) for x in range(160)
              if x < band or x >= 160 - band or y < band or y >= 160 - band]
    bl = [0.299 * r + 0.587 * gg + 0.114 * b for r, gg, b in border]
    ref = statistics.fmean(bl)
    inner = [px[x, y] for y in range(band, 160 - band) for x in range(band, 160 - band)]
    il = [0.299 * r + 0.587 * gg + 0.114 * b for r, gg, b in inner]
    return 100.0 * sum(1 for v in il if abs(v - ref) > 28) / len(il)


def frame_geometry(path: Path) -> tuple[float, int]:
    """(how much of the frame the subject's box covers, how many edges it touches).

    Uses PREP's own subject detector rather than a luminance trick. The first
    version of this measured how much of the border band differed from the
    backdrop, which reads a busy tabletop as a clipped subject: on a shoot
    photographed on a rug it flagged every frame. The subject box answers the
    real question directly — is the object whole inside the picture, or does it
    run off the edge.
    """
    import cv2

    from . import subject as subj

    bgr = cv2.imread(str(path))
    if bgr is None:
        return 0.0, 0
    sm = subj.mask_for(bgr, mode="auto")
    x, y, w, h = sm.bbox
    H, W = bgr.shape[:2]
    touched = sum([x <= 2, y <= 2, x + w >= W - 2, y + h >= H - 2])
    return (w * h) / float(W * H), touched


def hero_is_clipped(frac: float, touched: int) -> bool:
    """A hero should show the whole object. Two or more edges cut through it,
    or a box that fills the frame, means the buyer cannot see what it is at
    thumbnail size."""
    return touched >= 2 or frac >= 0.95


def looks_whole(frac: float, touched: int) -> bool:
    """A frame that shows the object complete, with room around it."""
    return touched == 0 and 0.2 <= frac <= 0.9


def _signature(im: Image.Image) -> list[float]:
    """A tiny greyscale fingerprint, for telling near-duplicate frames apart."""
    g = im.convert("L").resize((16, 16))
    return [float(v) for v in g.tobytes()]


def _distance(a: list[float], b: list[float]) -> float:
    return statistics.fmean([abs(x - y) for x, y in zip(a, b)])


def list_frames(shoot: Path) -> list[Path]:
    listing = shoot / "listing"
    if not listing.is_dir():
        raise SystemExit(f"no prepped frames: {listing} does not exist — run PREP first")
    frames = sorted(p for p in listing.iterdir()
                    if p.suffix.lower() in IMAGE_EXT and not p.name.startswith("00_hero"))
    if not frames:
        raise SystemExit(f"no images in {listing}")
    return frames


def choose_frames(frames: list[Path], want: int = 2) -> tuple[list[Path], str]:
    """Pick the hero and the view(s) that add something to it.

    The hero is frame one — PREP and DRAFT both treat listing order as the
    operator's decision, and this tool does not get to overrule it. The
    companions are the frames that are (a) tightest on the subject, so they
    read at thumbnail size, and (b) least like the hero, so the montage says
    something the hero does not.
    """
    if len(frames) < 2:
        return frames[:1], "single"
    hero = frames[0]
    with Image.open(hero) as h:
        h_sig, h_fill = _signature(h), subject_fill(h)
    scored = []
    for p in frames[1:]:
        with Image.open(p) as im:
            fill, sig = subject_fill(im), _signature(im)
        # tight on the subject AND different from the hero
        scored.append((fill / 100.0 + _distance(h_sig, sig) / 128.0, fill, p))
    scored.sort(reverse=True)
    picks = [hero] + [p for _, _, p in scored[: want - 1]]
    # A companion much tighter than the hero is a DETAIL — inset it, the way
    # they inset a hallmark. Comparable framing means two views of equal
    # weight, which reads better as a split.
    detail = scored[0][1] > h_fill + 12
    return picks, ("inset" if detail and want == 2 else "split")


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------
def _panel(im: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Fit an image inside a panel, centred on white — never cropped.

    Cropping here would undo a crop the operator already approved, and on a
    montage it is exactly how a defect ends up outside the gallery image.
    """
    fitted = ImageOps.contain(im.convert("RGB"), (box_w, box_h), Image.LANCZOS)
    panel = Image.new("RGB", (box_w, box_h), BG)
    panel.paste(fitted, ((box_w - fitted.width) // 2, (box_h - fitted.height) // 2))
    return panel


def compose_split(paths: list[Path], canvas: int = CANVAS) -> Image.Image:
    """Two or three equal views, side by side. Three goes big-left, two-right."""
    out = Image.new("RGB", (canvas, canvas), BG)
    m, g = int(canvas * MARGIN), int(canvas * GUTTER)
    inner = canvas - 2 * m
    if len(paths) <= 2:
        pw = (inner - g) // 2
        for i, p in enumerate(paths[:2]):
            with Image.open(p) as im:
                out.paste(_panel(im, pw, inner), (m + i * (pw + g), m))
    else:
        lw = int((inner - g) * 0.6)
        rw = inner - g - lw
        rh = (inner - g) // 2
        with Image.open(paths[0]) as im:
            out.paste(_panel(im, lw, inner), (m, m))
        for i, p in enumerate(paths[1:3]):
            with Image.open(p) as im:
                out.paste(_panel(im, rw, rh), (m + lw + g, m + i * (rh + g)))
    return out


def quietest_corner(main: Image.Image, box: float = INSET_W) -> str:
    """Which corner of the hero has the least going on.

    An inset dropped on a fixed corner covers whatever happens to be there —
    on the goldstone earrings it landed squarely on the second earring. Score
    each corner by how far its pixels sit from the frame's backdrop reference
    and take the emptiest, so the panel lands on backdrop instead of on the
    item.
    """
    g = main.convert("RGB").resize((200, 200))
    px = g.load()
    band = 10
    ring = [px[x, y] for y in range(200) for x in range(200)
            if x < band or x >= 200 - band or y < band or y >= 200 - band]
    ref = statistics.median([0.299 * r + 0.587 * gg + 0.114 * b for r, gg, b in ring])
    side = int(200 * box) + 6
    spans = {
        "tl": (0, 0), "tr": (200 - side, 0),
        "bl": (0, 200 - side), "br": (200 - side, 200 - side),
    }
    busy = {}
    for name, (ox, oy) in spans.items():
        vals = [px[x, y] for y in range(oy, oy + side) for x in range(ox, ox + side)]
        lum = [0.299 * r + 0.587 * gg + 0.114 * b for r, gg, b in vals]
        busy[name] = sum(1 for v in lum if abs(v - ref) > 28)
    # ties go to bottom-right: it is where the seller puts it, and where a
    # buyer's eye lands last.
    return min(("br", "bl", "tr", "tl"), key=lambda c: busy[c])


def compose_inset(paths: list[Path], canvas: int = CANVAS,
                  corner: str | None = None) -> Image.Image:
    """Main view full-bleed with a bordered detail panel dropped in a corner."""
    m = int(canvas * MARGIN)
    with Image.open(paths[0]) as im:
        out = _panel(im, canvas, canvas)
        if corner is None:
            corner = quietest_corner(im)

    iw = int(canvas * INSET_W)
    with Image.open(paths[1]) as im:
        ins = ImageOps.contain(im.convert("RGB"), (iw, iw), Image.LANCZOS)
    framed = Image.new("RGB", (ins.width + 2 * INSET_BORDER,
                               ins.height + 2 * INSET_BORDER), BG)
    framed.paste(ins, (INSET_BORDER, INSET_BORDER))
    # one-pixel hairline so a white item does not float off a white panel
    edge = Image.new("RGB", (framed.width + 2, framed.height + 2), HAIRLINE)
    edge.paste(framed, (1, 1))

    x = m if corner in ("bl", "tl") else canvas - m - edge.width
    y = m if corner in ("tl", "tr") else canvas - m - edge.height
    out.paste(edge, (x, y))
    return out


def compose(paths: list[Path], layout: str = "auto") -> tuple[Image.Image, str]:
    if len(paths) < 2:
        raise SystemExit("a montage needs at least two frames")
    if layout == "auto":
        layout = "split"
    if layout == "inset":
        return compose_inset(paths), "inset"
    return compose_split(paths), "split"


# --------------------------------------------------------------------------
def _resolve(frames: list[Path], spec: str | None, want: int):
    """`--frames 1,4` is 1-based over listing/ order, the same numbering the
    review sheet and the draft's photos list use."""
    if not spec:
        return choose_frames(frames, want)
    idx = [int(x) for x in spec.replace(" ", "").split(",") if x]
    for i in idx:
        if not 1 <= i <= len(frames):
            raise SystemExit(f"frame {i} out of range (1–{len(frames)})")
    picks = [frames[i - 1] for i in idx]
    return picks, "auto"


def _proposal_sheet(montage: Image.Image, frames: list[Path], picks: list[Path],
                    out: Path) -> None:
    """The montage over a numbered strip of every frame it could have used.

    The auto-pick is a guess and it is allowed to be wrong: it reads "tight on
    the subject and unlike the hero", which finds a macro but cannot tell a
    hallmark from another angle of the same curve. Showing the alternatives
    numbered turns a bad pick into a one-word correction (`--frames 1,5`)
    instead of a re-run and a squint.
    """
    from PIL import ImageDraw

    cell, pad = 190, 10
    cols = max(1, min(len(frames), 8))
    rows = (len(frames) + cols - 1) // cols
    strip_h = rows * (cell + 26) + pad
    top = 760
    sheet = Image.new("RGB", (cols * (cell + pad) + pad, top + strip_h), (248, 248, 249))
    m = ImageOps.contain(montage, (top - 20, top - 20), Image.LANCZOS)
    sheet.paste(m, ((sheet.width - m.width) // 2, 10))
    d = ImageDraw.Draw(sheet)
    for i, f in enumerate(frames):
        x = pad + (i % cols) * (cell + pad)
        y = top + (i // cols) * (cell + 26)
        with Image.open(f) as im:
            t = ImageOps.contain(im.convert("RGB"), (cell, cell), Image.LANCZOS)
        box = Image.new("RGB", (cell, cell), (255, 255, 255))
        box.paste(t, ((cell - t.width) // 2, (cell - t.height) // 2))
        sheet.paste(box, (x, y))
        used = f in picks
        d.rectangle([x - 2, y - 2, x + cell + 1, y + cell + 1],
                    outline=(200, 60, 50) if used else (215, 215, 218), width=3 if used else 1)
        label = f"{i + 1}" + ("  IN HERO" if used else "")
        d.text((x + 2, y + cell + 5), label, fill=(40, 40, 40))
    sheet.save(out, quality=90)


def run(shoot: Path, spec: str | None, layout: str, want: int,
        apply: bool, repoint: bool) -> Path:
    frames = list_frames(shoot)
    picks, auto_layout = _resolve(frames, spec, want)
    if len(picks) < 2:
        raise SystemExit(f"{shoot.name}: only {len(frames)} prepped frame(s) — "
                         "a montage needs two. Shoot the detail, or ship the single hero.")
    frac, touched = frame_geometry(picks[0])
    if hero_is_clipped(frac, touched):
        whole = [str(i) for i, f in enumerate(frames[:12], 1)
                 if looks_whole(*frame_geometry(f))]
        alt = f" — frames {', '.join(whole[:4])} show it whole" if whole else ""
        print(f"  ! the hero frame cuts through the object "
              f"({touched} edge(s), box covers {frac:.0%} of the frame){alt}")
        print("    the gallery thumbnail is where a buyer decides what it is; "
              "reorder in PREP or pass --frames")

    chosen = auto_layout if layout == "auto" and auto_layout in ("inset", "split") else layout
    img, used = compose(picks, chosen)

    prep = shoot / ".prep"
    prep.mkdir(exist_ok=True)
    proposal = prep / "hero_proposal.jpg"
    img.save(proposal, quality=94, subsampling=1)
    _proposal_sheet(img, frames, picks, prep / "hero_review.jpg")
    names = ", ".join(f"{frames.index(p) + 1}:{p.name}" for p in picks)
    print(f"  layout {used} from frames {names}")
    print(f"  proposal: {proposal}")
    print(f"  review:   {prep / 'hero_review.jpg'}   (numbered — override with --frames)")

    if not apply:
        print("  (proposal only — look at it, then re-run with `apply` to put it in listing/)")
        return proposal

    dst = shoot / "listing" / "00_hero.jpg"
    img.save(dst, quality=94, subsampling=1)
    print(f"  hero -> {dst}")

    if repoint:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import draft_io
        draft = shoot / "draft.md"
        if not draft.exists():
            print("  no draft.md yet — re-run with --repoint after DRAFT, or let "
                  "DRAFT pick it up (00_hero sorts first)")
            return dst
        d = draft_io.parse_draft(draft)
        order = [n for n in (d.frontmatter.get("photos") or []) if Path(n).name != "00_hero.jpg"]
        draft_io.set_photo_order(draft, ["listing/00_hero.jpg"] + order)
        print(f"  draft.md photos: montage first, {len(order)} frames after it")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["propose", "apply"])
    ap.add_argument("shoot", type=Path)
    ap.add_argument("--frames", default=None,
                    help="1-based frame numbers over listing/ order, e.g. 1,4 "
                         "(default: hero + the tightest, least-redundant view)")
    ap.add_argument("--layout", default="auto", choices=["auto", "inset", "split"])
    ap.add_argument("--panels", type=int, default=2, choices=[2, 3])
    ap.add_argument("--repoint", action="store_true",
                    help="also make it the draft's gallery image")
    a = ap.parse_args()
    run(a.shoot, a.frames, a.layout, a.panels,
        apply=(a.cmd == "apply"), repoint=a.repoint)


if __name__ == "__main__":
    main()
