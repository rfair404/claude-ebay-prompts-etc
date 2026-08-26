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
INSET_W = 0.34                # canvas fraction: single inset panel width
THUMB_W = 0.26                # canvas fraction: each thumbnail in the two-up column
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
    """A tiny COLOUR fingerprint, for telling near-duplicate frames apart.

    This was greyscale at 16x16 and it could not separate two photographs of
    the same yellow presentation box — so both thumbnails of a three-view hero
    came back showing the box. Colour is most of what distinguishes the views
    we actually shoot: dark felt, yellow box interior, white lid, metal.
    """
    g = im.convert("RGB").resize((12, 12))
    return [float(v) for v in g.tobytes()]


# A second thumbnail has to be at least this fraction as far from the first
# thumbnail as that one is from the main view. A fixed threshold cannot do this
# job: two shots of the same presentation box sat 28 apart while the main view
# was 50 away — plainly the same story, but above any floor low enough to be
# safe elsewhere. Judging the gap RELATIVE to the spread this shoot actually
# has is scale-free, and it is what rejects the duplicate box.
DISTINCT_ENOUGH = 0.6


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
    """Pick the main view and the companion view(s) that add something.

    The main view is frame one — PREP and DRAFT both treat listing order as
    the operator's decision, and this tool does not get to overrule it.

    Companions are chosen for DIVERSITY, not just for being unlike the main
    view. Scoring each candidate against the hero alone picked two photos of
    the same presentation box for both pairs of earrings: each was a fine
    answer to "unlike the macro", and together they said one thing twice. So
    the pick is greedy max-min — every companion has to be unlike the main
    view AND unlike every companion already chosen.
    """
    if len(frames) < 2:
        return frames[:1], "single"
    hero = frames[0]
    with Image.open(hero) as h:
        h_sig, h_fill = _signature(h), subject_fill(h)

    cand = []
    for p in frames[1:]:
        with Image.open(p) as im:
            cand.append({"path": p, "fill": subject_fill(im), "sig": _signature(im)})

    picks, chosen = [hero], [{"sig": h_sig}]
    while len(picks) < want and cand:
        best, best_score = None, None
        for c in cand:
            # distance to the NEAREST already-chosen frame: a candidate that
            # duplicates any of them scores low, however novel it is otherwise
            # Distinctness leads; tightness on the subject only breaks ties.
            # With fill weighted as heavily as spread, two big flat box shots
            # beat a screwback detail and a stone macro every time.
            spread = min(_distance(c["sig"], k["sig"]) for k in chosen) / 128.0
            score = spread * 3.0 + c["fill"] / 400.0
            if best_score is None or score > best_score:
                best, best_score = c, score
        if len(picks) >= 2:
            # `picks[1]` is the first thumbnail; the second must not simply
            # restate it.
            gap = _distance(best["sig"], chosen[1]["sig"])
            reach = _distance(chosen[1]["sig"], h_sig)
            if gap < DISTINCT_ENOUGH * reach:
                print(f"  note: no second view distinct enough to add "
                      f"(closest candidate is {gap:.0f} from thumb 1, "
                      f"which is itself {reach:.0f} from the main view) — "
                      f"offering the two-frame hero only")
                break
        picks.append(best["path"])
        chosen.append(best)
        cand.remove(best)

    with Image.open(picks[1]) as b:
        detail = subject_fill(b) > h_fill + 12
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
        # Two landscape frames side by side leave half the square empty: each
        # panel is tall and thin and the pictures shrink to fit its width. Two
        # landscapes stack; anything else sits side by side.
        landscape = []
        for p in paths[:2]:
            with Image.open(p) as im:
                landscape.append(im.width > im.height * 1.15)
        if all(landscape):
            ph = (inner - g) // 2
            for i, p in enumerate(paths[:2]):
                with Image.open(p) as im:
                    out.paste(_panel(im, inner, ph), (m, m + i * (ph + g)))
        else:
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
    side = min(int(200 * box) + 6, 150)
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


def _chip(path: Path, width: int) -> Image.Image:
    """One bordered thumbnail: white mat, then a hairline, so a pale item does
    not float off a pale main frame."""
    with Image.open(path) as im:
        t = ImageOps.contain(im.convert("RGB"), (width, width), Image.LANCZOS)
    framed = Image.new("RGB", (t.width + 2 * INSET_BORDER,
                               t.height + 2 * INSET_BORDER), BG)
    framed.paste(t, (INSET_BORDER, INSET_BORDER))
    edge = Image.new("RGB", (framed.width + 2, framed.height + 2), HAIRLINE)
    edge.paste(framed, (1, 1))
    return edge


def compose_inset(paths: list[Path], canvas: int = CANVAS,
                  corner: str | None = None) -> Image.Image:
    """Main view full-bleed with ONE bordered detail panel dropped in a corner."""
    m = int(canvas * MARGIN)
    with Image.open(paths[0]) as im:
        out = _panel(im, canvas, canvas)
        if corner is None:
            corner = quietest_corner(im)

    edge = _chip(paths[1], int(canvas * INSET_W))
    x = m if corner in ("bl", "tl") else canvas - m - edge.width
    y = m if corner in ("tl", "tr") else canvas - m - edge.height
    out.paste(edge, (x, y))
    return out


def compose_inset2(paths: list[Path], canvas: int = CANVAS,
                   corner: str | None = None) -> Image.Image:
    """Main view full-bleed with TWO thumbnails stacked down one side.

    The three-view hero: what it is, plus two supporting views — the mark and
    the reverse, the piece and its box, open and closed. Each thumbnail is
    smaller than the single-inset panel so the main view still reads at
    thumbnail size; two panels the size of one inset would leave the hero
    fighting its own supporting cast.
    """
    m = int(canvas * MARGIN)
    with Image.open(paths[0]) as im:
        out = _panel(im, canvas, canvas)
        if corner is None:
            # the column is tall, so judge the corner against a tall box
            corner = quietest_corner(im, box=THUMB_W * 2.2)

    chips = [_chip(p, int(canvas * THUMB_W)) for p in paths[1:3]]
    gap = int(canvas * 0.012)
    col_w = max(c.width for c in chips)
    col_h = sum(c.height for c in chips) + gap * (len(chips) - 1)
    x0 = m if corner in ("bl", "tl") else canvas - m - col_w
    y0 = m if corner in ("tl", "tr") else canvas - m - col_h
    y = y0
    for c in chips:
        out.paste(c, (x0 + (col_w - c.width), y))
        y += c.height + gap
    return out


def compose(paths: list[Path], layout: str = "auto") -> tuple[Image.Image, str]:
    if len(paths) < 2:
        raise SystemExit("a montage needs at least two frames")
    if layout == "auto":
        layout = "split"
    if layout == "inset":
        return compose_inset(paths), "inset"
    if layout == "inset2":
        if len(paths) < 3:
            raise SystemExit("the two-thumbnail hero needs three frames")
        return compose_inset2(paths), "inset2"
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
    # Explicit frames still get an inset/split judgement — the operator is
    # choosing WHICH views, not how they should sit together. A companion much
    # tighter than the main view is a detail, and a detail insets.
    if len(picks) >= 2:
        with Image.open(picks[0]) as a, Image.open(picks[1]) as b:
            detail = subject_fill(b) > subject_fill(a) + 12
        return picks, ("inset" if detail else "split")
    return picks, "auto"


def _comparison_sheet(options: list[tuple[str, Image.Image]], frames: list[Path],
                      picks: list[Path], out: Path) -> None:
    """Both candidate heroes side by side, over a numbered strip of every frame.

    The decision is never "is this montage acceptable" on its own — it is
    "which of these, or none". Showing one at a time invites a yes, so both
    are rendered together and skipping is always on the table.

    The frame strip stays because the auto-pick is a guess: it reads "tight on
    the subject and unlike the hero", which finds a macro but cannot tell a
    hallmark from another angle of the same curve. A bad pick costs one word,
    `--frames 1,6,3`, instead of a re-run and a squint.
    """
    from PIL import ImageDraw

    big, gap, pad = 620, 28, 10
    cell = 170
    cols = max(1, min(len(frames), 8))
    rows = (len(frames) + cols - 1) // cols
    width = max(len(options) * big + (len(options) + 1) * gap,
                cols * (cell + pad) + pad)
    top = big + 74
    sheet = Image.new("RGB", (width, top + rows * (cell + 26) + pad), (248, 248, 249))
    d = ImageDraw.Draw(sheet)

    for i, (label, img) in enumerate(options):
        x = gap + i * (big + gap)
        thumb = ImageOps.contain(img, (big, big), Image.LANCZOS)
        sheet.paste(thumb, (x + (big - thumb.width) // 2, 46))
        d.rectangle([x - 1, 45, x + big + 1, 46 + big + 1], outline=(205, 205, 210))
        d.text((x + 2, 22), label, fill=(25, 25, 25))

    d.text((pad, top - 22), "frames (1-based) - override with --frames", fill=(90, 90, 90))
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
                    outline=(200, 60, 50) if used else (215, 215, 218),
                    width=3 if used else 1)
        role = ""
        if used:
            role = "  MAIN" if f == picks[0] else f"  THUMB {picks.index(f)}"
        d.text((x + 2, y + cell + 5), f"{i + 1}{role}", fill=(40, 40, 40))
    sheet.save(out, quality=90)


def build_options(picks: list[Path], auto_layout: str) -> list[tuple[str, Image.Image]]:
    """The two candidate heroes: one supporting view, and two."""
    out = []
    one = "inset" if auto_layout == "inset" else "split"
    img1, _ = compose(picks[:2], one)
    out.append((f"MONTAGE 1  -  main + 1 ({one})", img1))
    if len(picks) >= 3:
        out.append(("MONTAGE 2  -  main + 2 thumbnails", compose_inset2(picks[:3])))
    return out


def run(shoot: Path, spec: str | None, layout: str, want: int,
        apply: bool, repoint: bool, style: str = "m1") -> Path:
    frames = list_frames(shoot)
    picks, auto_layout = _resolve(frames, spec, max(want, 3))
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

    prep = shoot / ".prep"
    prep.mkdir(exist_ok=True)

    if layout != "auto":
        img, used = compose(picks, layout)
        options = [(f"MONTAGE ({used})", img)]
    else:
        options = build_options(picks, auto_layout)

    paths = {}
    for i, (_, img) in enumerate(options, 1):
        f = prep / f"hero_m{i}.jpg"
        img.save(f, quality=94, subsampling=1)
        paths[f"m{i}"] = f
    _comparison_sheet(options, frames, picks, prep / "hero_review.jpg")

    names = ", ".join(f"{frames.index(p) + 1}:{p.name}" for p in picks[:3])
    print(f"  frames {names}")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {label}  ->  {paths[f'm{i}']}")
    print(f"  review:   {prep / 'hero_review.jpg'}   (both side by side, frames numbered)")

    if not apply:
        print("  (proposal only — decide, then `apply --style m1|m2`, or skip the montage "
              "entirely and ship the clean frame)")
        return prep / "hero_review.jpg"

    if style not in paths:
        raise SystemExit(f"no {style} was rendered for this shoot "
                         f"(available: {', '.join(paths)})")
    dst = shoot / "listing" / "00_hero.jpg"
    Image.open(paths[style]).save(dst, quality=94, subsampling=1)
    print(f"  {style} -> {dst}")

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
    ap.add_argument("--layout", default="auto",
                    choices=["auto", "inset", "split", "inset2"],
                    help="auto renders BOTH candidate styles for the review")
    ap.add_argument("--style", default="m1", choices=["m1", "m2"],
                    help="which reviewed montage to apply")
    ap.add_argument("--panels", type=int, default=3, choices=[2, 3])
    ap.add_argument("--repoint", action="store_true",
                    help="also make it the draft's gallery image")
    a = ap.parse_args()
    run(a.shoot, a.frames, a.layout, a.panels,
        apply=(a.cmd == "apply"), repoint=a.repoint, style=a.style)


if __name__ == "__main__":
    main()
