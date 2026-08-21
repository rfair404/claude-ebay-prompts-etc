#!/usr/bin/env python3
"""Re-crop + re-index the marbleconnection forum images — better crops this time.

The current `marbleconnection` index embeds each WHOLE posted forum photo
(background, multiple marbles, hands). This re-builds it from CLEAN single-marble
crops: re-download each image from its stored URL, run `detect_and_crop` (CLIP-
gated), and embed the crops instead of the raw frame.

Two modes:
  --verify-only (POC)  Process the N most recent threads, write crops + an
        original-vs-crop COMPARISON SHEET, print a summary, and TOUCH NO INDEX.
        Used to eyeball crop quality before committing to the full run.
  (full run, later)    Same pipeline over all threads, parallel, resumable,
        writing a NEW index dir. Not enabled until the POC crops pass review.

POC usage:
  python tools/reindex_forum.py --recent 10 --verify-only --out .scratch/reindex_poc
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from vindex import VIndex, download_pil  # noqa: E402
import marble_crop  # noqa: E402


def _recent_threads(meta, n):
    """Group meta rows by thread, return the N highest-tid threads (most recent),
    each as (tid, title, [rows]) — newest first."""
    by_tid = defaultdict(list)
    for m in meta:
        by_tid[m.get("tid")].append(m)
    tids = sorted((t for t in by_tid if t is not None), reverse=True)[:n]
    return [(t, by_tid[t][0].get("title", ""), by_tid[t]) for t in tids]


def _is_thumb(url: str) -> bool:
    return ".thumb." in url.lower()


def _square(im):
    """Center square crop (so a whole-frame fallback matches the detector's
    square-crop output shape)."""
    w, h = im.size
    s = min(w, h)
    return im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))


# CLIP ruler/caliper-frame detector. The marble photo protocol treats measurement
# frames as SIZE-only, not ID — and a marble in caliper jaws still passes the
# is_marble gate, so it needs its own check on the WHOLE frame. Calibrated: the
# caliper frame scores ruler-marble = +0.065; every real marble is negative
# (closest −0.006), so a +0.02 margin drops calipers with headroom, no false drop.
_RULER_POS = [
    "a digital caliper measuring an object", "a caliper with a digital number display",
    "a marble held in metal caliper jaws", "a measuring tool with a digital readout",
    "a ruler measuring a small object",
]
_RULER_MARBLE = [
    "a glass toy marble", "a round glass marble sphere",
    "a colorful swirled glass marble", "a translucent glass shooter marble",
]
_RULER = {}


def _is_ruler_frame(pil, *, margin=0.02):
    if "pos" not in _RULER:
        from vindex import embed_texts
        _RULER["pos"] = embed_texts(_RULER_POS)
        _RULER["marble"] = embed_texts(_RULER_MARBLE)
    from vindex import embed_images
    q = embed_images([pil])[0]
    s_ruler = float((_RULER["pos"] @ q).max())
    s_marble = float((_RULER["marble"] @ q).max())
    return (s_ruler - s_marble) >= margin


def _round_fallback(srcpath, *, pad=0.02, r_frac=0.47):
    """Frame-filling marble -> circle-masked PIL (white outside the marble).

    Detection found no circle precisely BECAUSE the marble fills the frame, so the
    only background is the corners. Mask with a centred inscribed circle
    (r = r_frac × side) — robust at any contrast (no segmentation to fail on a pale
    marble/pale towel), never drops a good marble. A marble that fills the frame is
    masked exactly; one with a small margin keeps at most a thin ring but loses the
    corners (the bulk of the background). Subject-with-a-neighbour frames are the
    known jar/pile minority handled by the largest/sharpest detect path, not here."""
    from PIL import Image
    sq = _square(Image.open(srcpath).convert("RGB"))
    import numpy as np
    bgr = np.asarray(sq)[:, :, ::-1].copy()              # RGB->BGR for crop_marble
    s = bgr.shape[0]
    c = s / 2.0
    return marble_crop.crop_marble(bgr, (c, c, s * r_frac), pad=pad, mask_bg=True)


def _sharpness(pil):
    """Variance of the Laplacian — higher = more in-focus. Used to pick the
    subject marble (in focus) over softer background-pile marbles."""
    import cv2
    import numpy as np
    g = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


# --- crop purity verifier ----------------------------------------------------
# CLIP is what samples the indexed crop, so let CLIP JUDGE the crop: score it
# against "clean single marble" vs the contaminants the POC surfaced (hand, pile,
# stand, towel, blur). purity = max(pos sim) - max(neg sim). Validated to sink the
# eye-flagged hand/pile/blur crops to the bottom of the ranking. The score reuses
# the crop's OWN image embedding — in the full run every kept crop is embedded for
# indexing anyway, so purity costs only a few dot products, not a second pass.
_PURITY_POS = [
    "a single glass marble on a white background", "one isolated glass marble sphere",
    "a close-up of a single swirled glass marble", "a single round glass shooter marble",
]
_PURITY_NEG = [
    "a hand holding a marble", "fingers holding a small marble",
    "a marble sitting on a metal display stand", "a marble on a chrome stand",
    "several glass marbles together", "a pile of many marbles", "a jar of marbles",
    "a cluttered background", "a blurry out-of-focus photo", "a fabric towel background",
]
_PURITY = {}


def _purity_vecs():
    if "pos" not in _PURITY:
        from vindex import embed_texts
        _PURITY["pos"] = embed_texts(_PURITY_POS)
        _PURITY["neg"] = embed_texts(_PURITY_NEG)
    return _PURITY["pos"], _PURITY["neg"]


def purity_of_emb(emb):
    """CLIP purity from a crop's L2-normalised image embedding ([D])."""
    pos, neg = _purity_vecs()
    return float((pos @ emb).max() - (neg @ emb).max())


def purity_of_image(pil):
    from vindex import embed_images
    return purity_of_emb(embed_images([pil])[0])


def marble_ok_emb(emb, *, margin=0.0):
    """is_marble() but from a precomputed crop embedding — so the production run
    gates marble-ness, purity, AND indexes off ONE embed instead of three."""
    pos, neg = marble_crop._gate_vecs()
    return (float((pos @ emb).max()) - float((neg @ emb).max())) >= margin


def crop_candidates(srcpath, **kw):
    """Lean crop for the production reindex: drop ruler frames (whole-frame
    context), else return the detection crops SHARPEST-FIRST (no per-candidate
    CLIP gate), or the single whole-frame fallback. -> ([crop_PIL...], kind).

    The caller embeds candidates in order and takes the FIRST that passes the
    marble gate — so a clean single-marble image costs ONE embed (gate + purity +
    index all reuse it), while a messy frame only pays extra embeds until it finds
    the real marble (instead of losing the image to a sharp non-marble pick)."""
    from PIL import Image
    whole = _square(Image.open(srcpath).convert("RGB"))
    if _is_ruler_frame(whole):
        return [], "ruler"
    pairs = marble_crop.detect_and_crop(str(srcpath), gate=False, **kw)
    if pairs:
        return [c for _, c in sorted(pairs, key=lambda p: -_sharpness(p[1]))], "detect"
    return [_round_fallback(srcpath)], "fallback"


def _audit_sheet(items, out_path, *, cell=200, cols=6):
    """items = [(label, purity, passed, crop_PIL)], lay out LOWEST-purity first so
    the most-suspect crops lead. DROP cells get a red border, PASS green — so a
    glance down the first rows shows exactly what the gate caught and what's
    borderline. This is the audit: purity is measured + surfaced for every crop."""
    from PIL import Image, ImageDraw
    items = sorted(items, key=lambda it: it[1])
    pad = 5
    rows = (len(items) + cols - 1) // cols
    W = cols * (cell + pad) + pad
    H = rows * (cell + pad) + pad
    sh = Image.new("RGB", (W, H), (18, 18, 18))
    d = ImageDraw.Draw(sh)
    for i, (label, pu, ok, im) in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + pad)
        d.rectangle([x, y, x + cell, y + cell], fill=(60, 200, 90) if ok else (225, 65, 65))
        t = im.copy()
        t.thumbnail((cell - 8, cell - 8))
        cc = Image.new("RGB", (cell - 8, cell - 8), (30, 30, 30))
        cc.paste(t, ((cell - 8 - t.width) // 2, (cell - 8 - t.height) // 2))
        sh.paste(cc, (x + 4, y + 4))
        d.text((x + 7, y + 6), f"{pu:+.3f}", fill=(255, 255, 0))
        d.text((x + 7, y + cell - 14), ("DROP " if not ok else "") + label, fill=(255, 255, 255))
    sh.save(out_path, quality=88)
    return out_path


def crop_forum(srcpath, *, gate=True, **kw):
    """Forum-image crop -> ([(circle_or_None, PIL)], kind).

    Policy (per user decisions for the re-index):
      1. RULER/caliper frames are dropped whole (measurement = size, not ID).
      2. LARGEST marble only — `detect_marbles` returns largest-first, and the
         thread's subject is almost always the biggest/most-foreground marble, so
         keep just that one and drop jar/pile background marbles.
      3. If nothing is detected (a marble FILLING the frame — no background margin
         for the circle finder), fall back to the gated center-squared WHOLE
         FRAME. Recovers the ~40% the dark-felt detector drops to zero.
    kind ∈ {'detect','fallback','ruler','not-marble'}."""
    from PIL import Image
    whole = _square(Image.open(srcpath).convert("RGB"))
    if gate and _is_ruler_frame(whole):
        return [], "ruler"
    pairs = marble_crop.detect_and_crop(str(srcpath), gate=gate, **kw)
    if pairs:
        # ONE subject marble per image. "Largest" works for a marble on a plain
        # background, but in a JAR/PILE photo a cluster of background marbles reads
        # as the biggest blob — so pick the SHARPEST (in-focus) crop instead: the
        # thread's subject is the one in focus; the background pile is softer.
        # (For a single-marble image there's one crop, so this is a no-op.)
        # detect_and_crop already circle-masked these (crop_marble mask_bg=True).
        best = max(pairs, key=lambda p: _sharpness(p[1]))
        return [best], "detect"
    # Frame-filling marble: inscribed-circle mask (corners -> white).
    masked = _round_fallback(srcpath)
    if gate and not marble_crop.is_marble(masked)[0]:
        return [], "not-marble"
    return [(None, masked)], "fallback"


def _comparison_sheet(rows, out_path, cell=190, pad=6):
    """rows = [(label, original_PIL, [crop_PIL,...])]. One row each:
    [original | crop1 | crop2 ...]. Saves a single contact-style sheet."""
    from PIL import Image, ImageDraw
    if not rows:
        return None
    max_crops = max((len(cr) for _, _, cr in rows), default=0)
    cols = 1 + max(1, max_crops)               # original + crop columns
    W = cols * (cell + pad) + pad
    H = len(rows) * (cell + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)

    def _fit(im):
        im = im.copy()
        im.thumbnail((cell, cell))
        c = Image.new("RGB", (cell, cell), (40, 40, 40))
        c.paste(im, ((cell - im.width) // 2, (cell - im.height) // 2))
        return c

    for r, (label, orig, crops) in enumerate(rows):
        y = pad + r * (cell + pad)
        sheet.paste(_fit(orig), (pad, y))
        draw.text((pad + 3, y + 3), label, fill=(255, 230, 120))
        draw.text((pad + 3, y + cell - 12), "ORIGINAL", fill=(150, 150, 150))
        if not crops:
            draw.text((pad + cell + pad + 6, y + cell // 2),
                      "NO CROP (gate rejected all)", fill=(255, 110, 110))
        for k, cim in enumerate(crops):
            x = pad + (1 + k) * (cell + pad)
            if x + cell > W:
                break
            sheet.paste(_fit(cim), (x, y))
            draw.text((x + 3, y + cell - 12), f"crop {k+1}", fill=(120, 220, 140))
    sheet.save(out_path, quality=90)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=10,
                    help="process the N most recent threads (by topic id)")
    ap.add_argument("--verify-only", action="store_true",
                    help="POC: write crops + comparison sheet, touch no index")
    ap.add_argument("--out", default=".scratch/reindex_poc")
    ap.add_argument("--index", default="marbleconnection")
    ap.add_argument("--no-gate", action="store_true",
                    help="skip the CLIP is-marble gate (see raw detector output)")
    ap.add_argument("--min-frac", type=float, default=0.05)
    ap.add_argument("--max-frac", type=float, default=0.6)
    ap.add_argument("--circ", type=float, default=0.40)
    ap.add_argument("--purity-min", type=float, default=0.008,
                    help="drop crops whose CLIP purity score is below this "
                         "(hand/pile/blur/heavy-bg). Validated on the POC set.")
    ap.add_argument("--no-purity", action="store_true",
                    help="score purity but DON'T gate on it (audit-only)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not args.verify_only:
        raise SystemExit("Full run is not enabled yet — run with --verify-only "
                         "until the POC crops are reviewed.")

    idx = VIndex(args.index)
    meta = [json.loads(l) for l in
            idx.meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    threads = _recent_threads(meta, args.recent)
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    audit = []          # (label, purity, passed, crop_PIL) for the audit sheet
    n_thumb = 0
    kinds = {}
    for tid, title, rows in threads:
        tkept = 0
        for m in rows:
            url = m["img"]
            if _is_thumb(url):
                n_thumb += 1
            try:
                orig = download_pil(url)
            except Exception as e:
                kinds["download-fail"] = kinds.get("download-fail", 0) + 1
                continue
            srcp = out / f"src_{tid}_{m.get('i',0)}.jpg"
            orig.save(srcp, quality=92)
            try:
                pairs, kind = crop_forum(
                    srcp, gate=not args.no_gate,
                    min_frac=args.min_frac, max_frac=args.max_frac, circ=args.circ)
            except Exception as e:
                kinds["crop-fail"] = kinds.get("crop-fail", 0) + 1
                continue
            if not pairs:                       # ruler / not-marble — no crop made
                kinds[kind] = kinds.get(kind, 0) + 1
                continue
            crop_im = pairs[0][1]
            purity = purity_of_image(crop_im)
            passed = args.no_purity or purity >= args.purity_min
            if not passed:
                kind = "impure"
            kinds[kind] = kinds.get(kind, 0) + 1
            label = f"t{tid}.{m.get('i',0)} {kind}"
            audit.append((label, purity, passed, crop_im))
            if passed:
                crop_im.save(out / f"crop_{tid}_{m.get('i',0)}.jpg", quality=92)
                tkept += 1
        print(f"t{tid}  {len(rows)} img → {tkept} kept   {title[:46]}", flush=True)

    # --- audit + stats -------------------------------------------------------
    import statistics
    asheet = _audit_sheet(audit, out / "audit.jpg")
    pvals = [pu for _, pu, _, _ in audit]
    kept = [a for a in audit if a[2]]
    dropped = [a for a in audit if not a[2]]
    print("\n=== PURITY AUDIT (most recent %d threads) ===" % args.recent)
    print(f"crops scored: {len(audit)} | KEPT {len(kept)} | "
          f"DROPPED(impure) {len(dropped)} | gate cutoff {args.purity_min:+.3f}")
    if pvals:
        print(f"purity: min {min(pvals):+.3f}  median {statistics.median(pvals):+.3f}  "
              f"max {max(pvals):+.3f}")
    print("kind tally:", {k: kinds[k] for k in sorted(kinds)})
    print(f"audit sheet (lowest-purity first): {asheet}")
    print("\nDROPPED as impure:")
    for label, pu, _, _ in sorted(dropped, key=lambda a: a[1]):
        print(f"  {pu:+.3f}  {label}")
    print("\nLOWEST-purity KEPT (borderline — eyeball these):")
    for label, pu, _, _ in sorted(kept, key=lambda a: a[1])[:8]:
        print(f"  {pu:+.3f}  {label}")


if __name__ == "__main__":
    main()
