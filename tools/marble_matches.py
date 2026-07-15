#!/usr/bin/env python3
"""Show the top-N CLIP forum matches for one marble — the "let me help decide" panel.

Per the forum-match policy: CLIP image-match is for the GROSS SORT only. A specific
MAKER is asserted ONLY when the top-10 forum cluster is near-unanimous (>= SLAM_MIN
name the same maker, zero dissent). When it isn't, run this to SHOW the user the
actual matched forum photos + the maker tally, and let them decide.

Builds a montage: [your marble | match #1 … #N], each labelled with similarity,
the responder, and the maker that answer names. Prints the maker tally + the
slam-dunk verdict.

  python tools/marble_matches.py <marble-closeup.jpg> [--top 10] [--out panel.jpg]
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from vindex import VIndex, embed_images, download_pil  # noqa: E402
from marble_crop import detect_and_crop  # noqa: E402
from forum_replies import extract_maker  # noqa: E402

SLAM_MIN = 8   # keep in sync with tools/marble_triage.py


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="a focused single-marble close-up")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default=None, help="montage path (default <image>_matches.jpg)")
    a = ap.parse_args()

    from PIL import Image, ImageDraw
    crops = detect_and_crop(a.image)
    mcrop = crops[0][1] if crops else Image.open(a.image).convert("RGB")
    q = embed_images([mcrop])[0]
    emb, meta = VIndex("marbleconnection").load()
    sims = emb @ q
    best = {}
    for i, m in enumerate(meta):
        t = m.get("tid", i)
        if t not in best or sims[i] > best[t][0]:
            best[t] = (float(sims[i]), m)
    ranked = sorted(best.values(), key=lambda x: -x[0])[: a.top]

    # tally + verdict
    votes = Counter()
    print(f"\nTOP {len(ranked)} forum matches for {Path(a.image).name}:")
    cells = [("YOUR MARBLE", mcrop, None)]
    for rank, (sc, m) in enumerate(ranked, 1):
        ans = (m.get("answer") or "").replace("\n", " ")
        mk = extract_maker(ans)
        if mk:
            votes[mk] += 1
        print(f"{rank:2}. sim {sc:.3f}  [{mk or '—'}]  {m.get('answer_by')}: {ans[:64]}")
        print(f"     {m.get('turl')}")
        try:
            im = download_pil(m["img"])
        except Exception:
            im = None
        cells.append((f"#{rank} {sc:.3f} [{mk or '?'}]", im, (m.get("answer_by"), ans)))

    tally = dict(votes.most_common())
    slam = bool(votes) and votes.most_common(1)[0][1] >= SLAM_MIN and len(votes) == 1
    print(f"\nMAKER TALLY (top-{len(ranked)}): {tally or 'none'}")
    if slam:
        print(f"  ✅ SLAM DUNK → {votes.most_common(1)[0][0]} (assert it)")
    else:
        print(f"  ❓ NOT unanimous → gross-sort only; SHOW the user, let them decide.")

    # montage
    cell, cols, lab, pad = 250, 4, 30, 6
    rows = math.ceil(len(cells) / cols)
    W = cols * (cell + pad) + pad
    H = rows * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, (label, im, ans) in enumerate(cells):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + lab + pad)
        if im is not None:
            th = im.copy(); th.thumbnail((cell, cell))
            sheet.paste(th, (x + (cell - th.width) // 2, y + lab + (cell - th.height) // 2))
        else:
            d.rectangle([x, y + lab, x + cell, y + lab + cell], fill=(60, 40, 40))
            d.text((x + 12, y + lab + cell // 2), "img n/a", fill=(210, 210, 210))
        d.text((x + 3, y + 2), label, fill=(255, 215, 0))
        if ans and ans[1]:
            d.text((x + 3, y + 16), f"{ans[0]}: {ans[1][:30]}", fill=(170, 205, 255))
    out = Path(a.out) if a.out else Path(a.image).with_name(Path(a.image).stem + "_matches.jpg")
    sheet.save(out, quality=90)
    print(f"\npanel -> {out}")


if __name__ == "__main__":
    main()
