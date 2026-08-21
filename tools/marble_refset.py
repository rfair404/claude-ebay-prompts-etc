#!/usr/bin/env python3
"""Maker-LABELLED reference set from forum posts that show marbles in KNOWN
packaging (poly bags, boxes, header cards, NOS — the maker is confirmed by the
package), then match a subject marble against it by CLIP colour likeness.

Pipeline:
  build  → scan the forum meta for packaging threads whose expert answer names a
           maker → download each image → crop every marble out → keep the clean
           single-marble crops → embed + label with the maker → save the
           `known_makers` reference index (emb + meta + crop thumbs) + a
           `known_maker_refs.md` note.
  match  → tight-crop a subject marble, rank it against the labelled set, and
           report which known-maker sets it looks most like.

HONESTY (per the CLIP/forum-data policy): a match is "your marble looks most like
known-<maker> examples" — a COLOUR/visual LEAD to verify with tells, NOT a maker
verdict. CLIP ViT-B/32 matches colour + gross pattern, not the seams that name a
maker. Packaged posts are ground truth for the maker of THOSE marbles only.

  python tools/marble_refset.py build [--limit-threads N]
  python tools/marble_refset.py match <subject.jpg> [--top 20]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

from vindex import VIndex, embed_images, download_pil  # noqa: E402
import marble_crop  # noqa: E402
import reindex_forum as rf  # noqa: E402
try:
    from forum_replies import extract_maker  # noqa: E402
except ImportError:                          # narrow: don't mask a broken forum_replies
    def extract_maker(s):
        return None

PKG = re.compile(
    r'\b(header card|poly ?bag|mesh ?bag|net ?bag|original (bag|box|package|packag)'
    r'|carded|blister|cellophane|packet|unopened|still sealed|unpunched'
    r'|new old stock|\bNOS\b|original packaging|in the (original )?(bag|box|packag)'
    r'|marble king bag|akro .{0,12}box|vitro .{0,12}bag|master .{0,12}bag)\b', re.I)

REF = VIndex("known_makers")
CROPS = REF.dir / "crops"


def _packaging_threads(src="marbleconnection"):
    """-> {tid: {maker, title, turl, imgs:[url,...]}} for packaging threads whose
    answer names a maker."""
    meta = [json.loads(l) for l in
            VIndex(src).meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_tid = defaultdict(list)
    for m in meta:
        by_tid[m.get("tid")].append(m)
    out = {}
    for tid, rows in by_tid.items():
        blob = " ".join(str(r.get(k) or "") for r in rows
                        for k in ("title", "op_question", "answer"))
        if not PKG.search(blob):
            continue
        maker = None
        for r in rows:                       # first maker any answer names
            maker = extract_maker(r.get("answer") or "")
            if maker:
                break
        if not maker or maker in ("—", "?"):
            continue
        out[tid] = {"maker": maker, "title": rows[0].get("title"),
                    "turl": rows[0].get("turl"),
                    "imgs": [r["img"] for r in rows if r.get("img")]}
    return out


def cmd_build(a):
    threads = _packaging_threads()
    items = sorted(threads.items(), key=lambda kv: kv[0], reverse=True)
    if a.limit_threads:
        items = items[: a.limit_threads]
    CROPS.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / ".scratch" / "refset_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    import numpy as np

    embs, rows, tally = [], [], defaultdict(int)
    n_img = n_crop = 0
    for ti, (tid, info) in enumerate(items, 1):
        maker = info["maker"]
        for k, url in enumerate(info["imgs"]):
            try:
                orig = download_pil(url)
            except Exception:
                continue
            n_img += 1
            p = tmp / f"r_{tid}_{k}.jpg"
            try:
                orig.save(p, quality=92)
                # EVERY marble in the shot (a bag has many) — ungated, gate below
                pairs = marble_crop.detect_and_crop(str(p), gate=False)
            except Exception:
                continue
            finally:
                try:
                    p.unlink()
                except Exception:
                    pass
            for ci, (_, crop) in enumerate(pairs):
                emb = embed_images([crop])[0].astype("float32")
                if not rf.marble_ok_emb(emb):          # keep real marbles only
                    continue
                pur = rf.purity_of_emb(emb)
                if pur < a.purity_min:                 # keep clean crops only
                    continue
                cf = CROPS / f"{maker.replace('/', '-')}_{tid}_{k}_{ci}.jpg"
                crop.save(cf, quality=88)
                embs.append(emb)
                rows.append({"maker": maker, "tid": tid, "turl": info["turl"],
                             "title": info["title"], "img": url,
                             "crop": cf.name, "purity": round(float(pur), 4)})
                tally[maker] += 1
                n_crop += 1
        print(f"[{ti}/{len(items)}] t{tid} {maker}: {n_crop} crops so far", flush=True)

    if embs:
        REF.dir.mkdir(parents=True, exist_ok=True)
        np.save(REF.emb_p, np.stack(embs).astype("float32"))
        with REF.meta_p.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        st = {"count": len(rows), "makers": dict(tally)}
        REF.stamp(st, np.stack(embs).shape[1])
        REF.save_state(st)
        _write_note(threads, tally, len(rows))
    print(f"\n[built] {n_crop} labelled crops from {n_img} images / {len(items)} "
          f"known-maker packaged threads")
    print(f"  maker tally: {dict(sorted(tally.items(), key=lambda x: -x[1]))}")
    print(f"  index: {REF.dir}  |  note: {REF.dir / 'known_maker_refs.md'}")


def _write_note(threads, tally, n_crop):
    lines = ["# Known-maker reference posts (marbles in confirmed packaging)\n",
             f"Source: forum packaging threads with a maker named in the expert "
             f"answer. {len(threads)} threads → {n_crop} labelled marble crops.\n",
             "Colour-match reference only — a LEAD, not a maker verdict.\n",
             "| maker | crops | example thread |", "|---|---|---|"]
    ex = {}
    for tid, info in threads.items():
        ex.setdefault(info["maker"], info)
    for mk, n in sorted(tally.items(), key=lambda x: -x[1]):
        e = ex.get(mk, {})
        lines.append(f"| {mk} | {n} | [{(e.get('title') or '')[:40]}]({e.get('turl')}) |")
    (REF.dir / "known_maker_refs.md").write_text("\n".join(lines), encoding="utf-8")


def cmd_match(a):
    import numpy as np
    emb, meta = REF.load()
    cands, kind = rf.crop_candidates(a.image)
    crop = None
    for c in (cands or []):
        e = embed_images([c])[0]
        if rf.marble_ok_emb(e):
            crop, q = c, e.astype("float32")
            break
    if crop is None:
        from PIL import Image
        crop = Image.open(a.image).convert("RGB")
        q = embed_images([crop])[0].astype("float32")
    sims = emb @ q
    order = np.argsort(-sims)[: a.top]
    ranked = [(float(sims[i]), meta[i]) for i in order]

    # per-maker aggregate (mean of that maker's top-3 sims) — a LEAN, not a verdict
    per = defaultdict(list)
    for i in np.argsort(-sims):
        per[meta[i]["maker"]].append(float(sims[i]))
    lean = sorted(((mk, sum(v[:3]) / len(v[:3])) for mk, v in per.items()),
                  key=lambda x: -x[1])

    print(f"\nSubject vs known-maker packaged sets — COLOUR likeness only (a lead, "
          f"NOT a maker verdict). crop: {kind}")
    print(f"  best-3 mean similarity by maker: " +
          ", ".join(f"{mk} {s:.3f}" for mk, s in lean[:6]))
    print(f"\n  top {len(ranked)} individual matches:")
    for r, (sc, m) in enumerate(ranked, 1):
        print(f"  {r:2}. {sc:.3f}  [{m['maker']}]  {m['turl']}")

    if a.montage:
        _match_montage(crop, ranked, a.montage)
        print(f"\n  montage: {a.montage}")


def _match_montage(subject, ranked, out):
    from PIL import Image, ImageDraw
    import math
    cells = [("YOUR MARBLE", subject)]
    for sc, m in ranked:
        cf = CROPS / m["crop"]
        im = Image.open(cf).convert("RGB") if cf.exists() else None
        cells.append((f"{m['maker']} {sc:.3f}", im))
    cols, cell, lab = 6, 200, 18
    r = math.ceil(len(cells) / cols)
    sh = Image.new("RGB", (cols * (cell + 6) + 6, r * (cell + lab + 6) + 6), (22, 22, 26))
    d = ImageDraw.Draw(sh)
    for i, (label, im) in enumerate(cells):
        rr, cc = divmod(i, cols)
        x, y = 6 + cc * (cell + 6), 6 + rr * (cell + lab + 6)
        if im is not None:
            t = im.copy(); t.thumbnail((cell, cell))
            sh.paste(t, (x + (cell - t.width) // 2, y + lab + (cell - t.height) // 2))
        d.text((x + 3, y + 3), label, fill=(120, 235, 140) if i else (255, 210, 90))
    sh.save(out, quality=90)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="crop+label+embed the known-maker packaged sets")
    b.add_argument("--limit-threads", type=int, default=None)
    b.add_argument("--purity-min", type=float, default=0.008)
    b.set_defaults(func=cmd_build)
    m = sub.add_parser("match", help="match a subject marble against the known sets")
    m.add_argument("image")
    m.add_argument("--top", type=int, default=20)
    m.add_argument("--montage", default=None)
    m.set_defaults(func=cmd_match)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
