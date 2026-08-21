#!/usr/bin/env python3
"""One-shot marble triage — the fast batch pipeline for sorting shoots.

Loads the CLIP model + all three indexes ONCE, then for every photo (or every
auto-cropped marble in it) embeds the query a SINGLE time and fans that one
embedding out to all three signals:

  - classifier  (lib/marble_classify, estate prior) -> family + Good/Better/Best
                maker candidates + family_soft flag
  - forum       (marbleconnection CLIP) -> reputation-ranked expert ANSWERS
  - comps       (ebay-sold CLIP)        -> priced sold look-alikes + median

vs. running marble_classify + marble_index + ebay_visual separately (3 model
loads, 3 re-embeds), this is one load + one embed per marble. Use it to grind a
whole night's shoots into structured records, then curate them into the registry
(inventory/MARBLE_REGISTRY.md).

  python tools/marble_triage.py <shoot-dir>                 # all JPGs in the dir
  python tools/marble_triage.py img1.jpg img2.jpg
  python tools/marble_triage.py <dir> --no-detect           # whole frame, no crop
  python tools/marble_triage.py <dir> --age old-collection  # dial the prior
  python tools/marble_triage.py <dir> --json out.json       # default: <dir>/triage.json

Each record is per-FRAME (or per-crop). Multiple frames of the SAME physical
marble are NOT collapsed here — that judgement (which frames = one marble) is the
human/registry step. The tool gives the raw signal; the registry curates it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import statistics  # noqa: E402
from collections import Counter  # noqa: E402
from vindex import VIndex, embed_images, download_pil  # noqa: E402
from marble_classify import Classifier, topk_floor, TIER  # noqa: E402
from forum_replies import extract_maker  # noqa: E402

# Forum-match policy (user rule): use the image match for the GROSS SORT only.
# A specific MAKER is asserted ONLY on a near-unanimous forum cluster — >=
# SLAM_MIN of the top-10 answers name the SAME maker with ZERO dissent.
# Anything less = "show the user, let them decide".
SLAM_MIN = 8

IMG_EXTS = (".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG")


def _load_pil(ref):
    from PIL import Image
    p = Path(ref)
    return Image.open(p).convert("RGB") if p.exists() else download_pil(ref)


def _best_per(meta, sims, key):
    """Best (score, meta) per meta[key] — dedupes a multi-image listing/thread."""
    best = {}
    for i, m in enumerate(meta):
        k = m.get(key, i)
        if k not in best or sims[i] > best[k][0]:
            best[k] = (float(sims[i]), m)
    return sorted(best.values(), key=lambda x: x[0], reverse=True)


# forum-answer keywords that mean "this might be a PULL — look closer"
UPSIDE_KW = ("oxblood", "royal", "conqueror", "tiger eye", "indian", "lutz",
             "sulphide", "slag", "popeye", "corkscrew", "nlr", "guinea",
             "not american", "comic", "banana", "flame", "superman",
             "christmas tree", "peewee", "shooter", "veiligglas", "wire pull")


def reading_order(items):
    """Sort [(circle=(x,y,r), im)] into reading order: rows top→bottom, then
    left→right within a row. Matches the shootN-mK locator convention."""
    if len(items) <= 1:
        return items
    import statistics
    rad = statistics.median(c[2] for c, _ in items)
    rowtol = rad * 1.4
    s = sorted(items, key=lambda it: it[0][1])               # by y
    rows = [[s[0]]]
    for it in s[1:]:
        if it[0][1] - rows[-1][-1][0][1] > rowtol:
            rows.append([it])
        else:
            rows[-1].append(it)
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda it: it[0][0]))     # by x
    return out


def contact_sheet(numbered, out_path, cell=170, cols=8):
    """numbered: [(k, PIL.Image)] -> one numbered montage jpg, so you can verify
    the crop count and see which physical marble each m-number is."""
    import math
    from PIL import Image, ImageDraw
    n = len(numbered)
    if not n:
        return
    cols = min(cols, n)
    rows = math.ceil(n / cols)
    lab, pad = 22, 6
    W = cols * (cell + pad) + pad
    H = rows * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), (28, 28, 30))
    d = ImageDraw.Draw(sheet)
    for i, (k, im) in enumerate(numbered):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + lab + pad)
        th = im.copy()
        th.thumbnail((cell, cell))
        sheet.paste(th, (x + (cell - th.width) // 2, y + lab + (cell - th.height) // 2))
        d.text((x + 3, y + 3), f"m{k:02d}", fill=(255, 215, 0))
    sheet.save(out_path, quality=90)


def triage_one(q, clf, forum, ebay, age, top, close=0.04):
    """One query embedding q [D] -> a structured triage record."""
    rec = {}
    # --- classifier (with estate prior) ---
    res = clf.predict(q, age=age)
    fams = res.get("families_adj") or res["families"]
    rec["family"] = [{"f": f, "p": round(p, 3)} for f, p in fams[:4]]
    rec["family_clip"] = [{"f": f, "p": round(p, 3)} for f, p in res["families"][:3]]
    rec["family_soft"] = res["family_soft"]
    shown, nhid, _ = topk_floor(res["top"])
    rec["makers"] = [{"label": l, "p": round(p, 3),
                      "tier": TIER.get(l, ("?", False))[0]}
                     for l, p in shown]
    rec["makers_hidden"] = nhid
    # --- forum (expert answers) — optional ---
    rec["forum_on"] = forum is not None
    if forum is None:
        rec.update(forum_top=0.0, forum_corrob=0, forum=[], forum_makers={},
                   maker_slam_dunk=False, maker_verdict=None)
        return _comps(rec, q, ebay, top)
    fe, fm = forum
    fs = fe @ q
    franked = _best_per(fm, fs, "tid")
    ftop = franked[: top]
    ftopscore = franked[0][0] if franked else 0.0
    fclose = sum(1 for sc, _ in franked if sc >= ftopscore - close) - 1
    rec["forum_top"] = round(ftopscore, 3) if franked else 0.0
    rec["forum_corrob"] = max(0, fclose)
    rec["forum"] = [{"sim": round(sc, 3), "answer": m.get("answer"),
                     "by": m.get("answer_by"), "rep": m.get("answer_rep"),
                     "title": m.get("title"), "url": m.get("turl")}
                    for sc, m in ftop]
    # maker convergence over the top-10 (the slam-dunk test)
    votes = Counter()
    for sc, m in franked[:10]:
        mk = extract_maker(m.get("answer") or "")
        if mk:
            votes[mk] += 1
    rec["forum_makers"] = dict(votes.most_common())
    slam, verdict = False, None
    if votes:
        tm, tn = votes.most_common(1)[0]
        if tn >= SLAM_MIN and len(votes) == 1:        # near-unanimous, no dissent
            slam, verdict = True, tm
    rec["maker_slam_dunk"] = slam
    rec["maker_verdict"] = verdict
    return _comps(rec, q, ebay, top)


def _comps(rec, q, ebay, top):
    """Attach eBay priced-comp fields to rec (optional — ebay None skips it)."""
    if ebay is None:
        rec.update(comps_median=None, comps_range=None, comps=[])
        return rec
    ee, em = ebay
    es = ee @ q
    eranked = _best_per(em, es, "itemId")[: top]
    prices = sorted(m["price"] for _, m in eranked
                    if isinstance(m.get("price"), (int, float))
                    and not isinstance(m.get("price"), bool))
    rec["comps_median"] = round(statistics.median(prices), 2) if prices else None
    rec["comps_range"] = [min(prices), max(prices)] if prices else None
    rec["comps"] = [{"sim": round(sc, 3), "price": m.get("price"),
                     "title": (m.get("title") or "")[:70], "url": m.get("url")}
                    for sc, m in eranked]
    return rec


def fmt(rec, tag):
    fam = "  ".join(f"{d['f']} {d['p']:.0%}" for d in rec["family"][:3])
    clip = "  ".join(f"{d['f']} {d['p']:.0%}" for d in rec["family_clip"][:2])
    soft = "  ⚠SOFT" if rec["family_soft"] else ""
    mk = " | ".join(f"{d['label']} {d['p']:.0%}" for d in rec["makers"])
    lines = [f"[{tag}]",
             f"  family: {fam}{soft}   (clip: {clip})",
             f"  makers: {mk}" + (f"  (+{rec['makers_hidden']})" if rec['makers_hidden'] else "")]
    if rec["forum"]:
        f0 = rec["forum"][0]
        lines.append(f"  forum:  {rec['forum_top']:.3f} ({rec['forum_corrob']} corrob) "
                     f"→ {f0['by']}: \"{(f0['answer'] or '')[:70]}\"")
    if rec.get("forum_on"):
        fmk = rec.get("forum_makers") or {}
        if rec.get("maker_slam_dunk"):
            lines.append(f"  MAKER: ✅ {rec['maker_verdict']} — SLAM DUNK ({fmk})")
        elif fmk:
            tally = " ".join(f"{k}:{v}" for k, v in fmk.items())
            lines.append(f"  MAKER: ❓ split [{tally}] → gross-sort only; SHOW USER to decide")
        else:
            lines.append("  MAKER: ❓ no forum maker votes → gross-sort only; SHOW USER")
    if rec["comps_median"] is not None:
        c0 = rec["comps"][0]
        lines.append(f"  comps:  median ${rec['comps_median']:.2f} "
                     f"range ${rec['comps_range'][0]:.2f}-${rec['comps_range'][1]:.2f}  "
                     f"| top: {c0['title']} ${c0['price']}")
    return "\n".join(lines)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="shoot dir(s) and/or image file(s)")
    ap.add_argument("--age", default="estate-typical", help="prior age-confidence (marble_prior)")
    ap.add_argument("--no-detect", action="store_true", help="skip auto-crop; use whole frame")
    ap.add_argument("--no-crops", action="store_true",
                    help="don't save per-marble crop jpgs + the numbered contact sheet")
    ap.add_argument("--min-frac", type=float, default=0.05,
                    help="min marble radius as frac of short side. LOWER for an "
                         "overview of many small marbles (try 0.02-0.03).")
    ap.add_argument("--max-frac", type=float, default=0.6)
    ap.add_argument("--circ", type=float, default=0.40, help="circularity 0..1 (higher=stricter)")
    ap.add_argument("--max-n", type=int, default=80, help="max marbles per frame")
    ap.add_argument("--no-forum", action="store_true",
                    help="skip the forum CLIP image-match (it's noisy; off = classifier + comps only)")
    ap.add_argument("--no-comps", action="store_true",
                    help="skip the eBay priced-comp match")
    ap.add_argument("--crops-only", action="store_true",
                    help="CROP + contact sheet ONLY (skip classify/forum/comps) — the "
                         "fast verify-the-crops-first step before any sort")
    ap.add_argument("--expect", type=int, default=None,
                    help="expected marble count per frame; flags a MISMATCH loudly")
    ap.add_argument("--top", type=int, default=5, help="top-K forum/comps per marble")
    ap.add_argument("--json", default=None, help="write records here (default <dir>/triage.json)")
    a = ap.parse_args()

    # expand dirs -> image lists
    imgs = []
    out_dir = None
    for p in a.paths:
        pp = Path(p)
        if pp.is_dir():
            out_dir = out_dir or pp
            imgs += sorted(str(f) for f in pp.iterdir() if f.suffix in IMG_EXTS)
        else:
            imgs.append(p)
    if not imgs:
        raise SystemExit("No images found.")

    clf = forum = ebay = age = None
    if not a.crops_only:
        print(f"[triage] loading model + indexes (once)…", flush=True)
        clf = Classifier()
        if not a.no_forum:
            forum = VIndex("marbleconnection").load()
        if not a.no_comps:
            ebay = VIndex("ebay-sold").load()
        age = None if a.age.lower() in ("none", "raw", "off") else a.age
    print(f"[triage] {len(imgs)} frame(s); detect={'off' if a.no_detect else 'on'}; "
          f"{'CROPS-ONLY' if a.crops_only else f'age={age}'}\n", flush=True)

    records, pulls, count_by_frame = [], [], {}
    crop_dir = (out_dir / "crops") if out_dir else Path(imgs[0]).parent / "crops"
    for ref in imgs:
        name = Path(ref).name
        stem = Path(ref).stem
        try:
            if a.no_detect:
                crops = [(None, _load_pil(ref))]
            else:
                from marble_crop import detect_and_crop
                crops = detect_and_crop(ref, min_frac=a.min_frac, max_frac=a.max_frac,
                                        circ=a.circ, max_n=a.max_n) or []
                if not crops:                       # detector found nothing -> whole frame
                    crops = [(None, _load_pil(ref))]
                else:
                    crops = reading_order(crops)
            multi = len(crops) > 1
            if multi and not a.no_crops:
                crop_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            count_by_frame[stem] = len(crops)
            for k, (_c, im) in enumerate(crops, 1):
                if multi and not a.no_crops:
                    im.save(crop_dir / f"{stem}_m{k:02d}.jpg", quality=92)
                    saved.append((k, im))
                if a.crops_only:
                    continue
                q = embed_images([im])[0]
                tag = f"{stem}-m{k}" if multi else name
                rec = triage_one(q, clf, forum, ebay, age, a.top)
                lead = rec["family"][0]["f"]
                fa = (rec["forum"][0]["answer"] if rec["forum"] else "") or ""
                kw = next((w for w in UPSIDE_KW if w in fa.lower()), None)
                rec["flag"] = bool(rec["family_soft"] or lead in ("handmade", "transitional") or kw)
                rec["flag_reason"] = " ".join(filter(None, [
                    "soft" if rec["family_soft"] else "",
                    f"fam:{lead}" if lead in ("handmade", "transitional") else "",
                    f"kw:{kw}" if kw else ""]))
                rec.update(frame=name, crop=k, tag=tag)
                records.append(rec)
                if rec["flag"]:
                    pulls.append(rec)
                print(fmt(rec, tag) + (f"   ⚑LOOK [{rec['flag_reason']}]" if rec["flag"] else ""),
                      flush=True)
                print(flush=True)
            if saved:
                cs = crop_dir / f"{stem}_contact.jpg"
                contact_sheet(saved, cs)
                print(f"  → contact sheet ({len(saved)} crops): {cs}\n", flush=True)
        except Exception as e:
            print(f"! {name}: {e}", file=sys.stderr)

    # --- CROP CHECK gate (verify crops BEFORE trusting any sort) ---
    print("\n" + "-" * 64)
    print("CROP CHECK — verify before trusting the sort:")
    ok = True
    for fr, n in count_by_frame.items():
        bad = a.expect is not None and n != a.expect
        ok = ok and not bad
        print(f"   {fr}: {n} crop(s)" + (f"   ⚠ MISMATCH — expected {a.expect}" if bad else ""))
    print("   → open the *_contact.jpg: each cell must be ONE clean marble, all present.")
    if a.expect is not None and not ok:
        print("   ⛔ COUNT MISMATCH — do NOT sort. Fix crops (tune --min-frac / re-shoot) "
              "or confirm what's acceptable first.")
    print("-" * 64 + "\n")

    if pulls:
        print("=" * 64)
        print(f"⚑ LOOK / possible PULLS ({len(pulls)}) — judge these for the HIGH bucket:")
        for r in pulls:
            fa = (r["forum"][0]["answer"] if r["forum"] else "") or ""
            print(f"   {r['tag']:16} [{r['flag_reason']}]  forum {r.get('forum_top')}: {fa[:48]}")
        print("=" * 64)

    out = Path(a.json) if a.json else (out_dir / "triage.json" if out_dir else Path("triage.json"))
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[triage] wrote {len(records)} record(s) -> {out}")


if __name__ == "__main__":
    main()
