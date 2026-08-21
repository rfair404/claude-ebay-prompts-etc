#!/usr/bin/env python3
"""Tight-crop ONE marble and list the top-N COLOUR/VISUAL look-alike forum listings.

PURE CLIP visual-likeness search — explicitly NOT a maker ID. It carefully crops
the single marble out of your photo (the same clean-crop pipeline as the reindex),
embeds it, and ranks the forum index by cosine similarity. Output is a "closely
colour-matched forum listings" panel: thumbnail + the poster's title + link +
similarity. Nothing is inferred about maker/type.

WHY it's colour-match only (honest by construction): CLIP ViT-B/32 sees a 224px,
49-patch image, so its similarity is dominated by COLOUR + gross pattern and does
NOT resolve seams / cutlines / ribbon-count / surface grade. So this is a "these
LOOK alike" list, full stop — do not read a maker off it (that's what the
human + forum-answer path is for).

  python tools/marble_colormatch.py <marble.jpg> [--top 25]
      [--index marbleconnection_v2] [--html out.html] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

from vindex import VIndex, embed_images, download_pil  # noqa: E402
import reindex_forum as rf  # noqa: E402


def _load_index(name):
    """Load a clean-crop index: the merged <name>, else concatenate its shards
    <name>_s*, else fall back to the live 'marbleconnection' (whole-image, less
    precise for colour match). -> (emb [N,D], meta list, label)."""
    import numpy as np
    base = VIndex(name)
    if base.emb_p.exists() and base.meta_p.exists():
        e, m = base.load()
        return e, m, name
    shard_dirs = sorted(base.dir.parent.glob(f"{name}_s*"))
    es, ms = [], []
    for sd in shard_dirs:
        ep, mp = sd / "emb.npy", sd / "meta.jsonl"
        if ep.exists() and mp.exists():
            es.append(np.load(ep))
            ms += [json.loads(l) for l in mp.read_text(encoding="utf-8").splitlines() if l.strip()]
    if es:
        return np.vstack(es), ms, f"{name} shards×{len(es)} (partial rebuild)"
    live = VIndex("marbleconnection")
    if live.emb_p.exists():
        e, m = live.load()
        print("! clean index not built yet — using live 'marbleconnection' "
              "(whole-image; background dilutes the colour match).", file=sys.stderr)
        return e, m, "marbleconnection (live/whole-image)"
    raise SystemExit(f"no index found for '{name}' (no merged dir, no shards, no live)")


def tight_crop(path):
    """Carefully crop the single marble: sharpest detected marble (or whole-frame
    fallback), circle-masked, gated. -> (crop_PIL, how)."""
    from PIL import Image
    cands, kind = rf.crop_candidates(path)
    if not cands:
        return Image.open(path).convert("RGB"), f"{kind}->whole-frame"
    for c in cands:
        if rf.marble_ok_emb(embed_images([c])[0]):
            return c, kind
    return cands[0], f"{kind} (ungated)"


def _montage(query_crop, ranked, out, *, cell=190, cols=6):
    """Grid: [YOUR CROP | match #1 … #N], each labelled with similarity only
    (no maker). Downloads each match thumbnail. Pure look-alike panel."""
    from PIL import Image, ImageDraw
    import math
    cells = [("YOUR MARBLE", query_crop)]
    for r, (sc, m) in enumerate(ranked, 1):
        try:
            im = download_pil(m["img"])
        except Exception:
            im = None
        cells.append((f"#{r}  sim {sc:.3f}", im))
    lab = 18
    rows = math.ceil(len(cells) / cols)
    W = cols * (cell + 6) + 6
    H = rows * (cell + lab + 6) + 6
    sh = Image.new("RGB", (W, H), (22, 22, 26))
    d = ImageDraw.Draw(sh)
    for i, (label, im) in enumerate(cells):
        rr, cc = divmod(i, cols)
        x, y = 6 + cc * (cell + 6), 6 + rr * (cell + lab + 6)
        if im is not None:
            t = im.copy(); t.thumbnail((cell, cell))
            sh.paste(t, (x + (cell - t.width) // 2, y + lab + (cell - t.height) // 2))
        else:
            d.rectangle([x, y + lab, x + cell, y + lab + cell], fill=(60, 40, 40))
            d.text((x + 10, y + lab + cell // 2), "img n/a", fill=(220, 220, 220))
        col = (120, 235, 140) if i else (255, 210, 90)
        d.text((x + 3, y + 3), label, fill=col)
    sh.save(out, quality=90)
    return out


def _html(query_crop, cropn, ranked, label, out):
    import base64
    import io
    b = io.BytesIO()
    query_crop.copy().convert("RGB").save(b, format="JPEG", quality=85)
    q64 = base64.b64encode(b.getvalue()).decode()
    rows = []
    for r, (sc, m) in enumerate(ranked, 1):
        title = (m.get("title") or "").replace("<", "&lt;")[:100] or m.get("turl")
        rows.append(
            f'<tr><td class=r>{r}</td><td class=s>{sc:.3f}</td>'
            f'<td><a href="{m.get("turl")}" target=_blank>'
            f'<img src="{m.get("img")}" loading="lazy" alt=""></a></td>'
            f'<td><a href="{m.get("turl")}" target=_blank>{title}</a></td></tr>')
    Path(out).write_text(
        "<!doctype html><meta charset=utf-8><title>Colour-matched marbles</title>"
        "<style>body{font:14px system-ui;background:#141414;color:#eee;margin:20px;max-width:760px}"
        "h2{margin:0 0 2px;font-weight:500}.note{color:#9a9a9a;margin:0 0 16px;font-size:13px}"
        ".q{display:flex;gap:14px;align-items:center;margin:0 0 18px}"
        ".q img{width:120px;height:120px;object-fit:cover;border-radius:10px}"
        ".q b{color:#ffd54a}table{border-collapse:collapse;width:100%}"
        "td{padding:6px 10px;border-bottom:1px solid #2b2b2b;vertical-align:middle}"
        ".r{color:#777;width:24px}.s{color:#ffd54a;font-variant-numeric:tabular-nums;width:52px}"
        "td img{height:88px;width:88px;object-fit:cover;border-radius:8px;display:block;background:#222}"
        "a{color:#8fd3ff;text-decoration:none}a:hover{text-decoration:underline}</style>"
        "<h2>Colour-matched forum listings</h2>"
        "<p class=note>CLIP visual likeness only — NOT a maker ID. Ranked by colour + "
        f"gross pattern. Titles are the poster's own words. Index: {label}.</p>"
        f'<div class=q><img src="data:image/jpeg;base64,{q64}">'
        f"<div>your marble<br><b>{cropn}</b><br>top {len(ranked)} look-alikes below</div></div>"
        f"<table>{''.join(rows)}</table>", encoding="utf-8")
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="a photo containing one marble (path or url)")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--index", default="marbleconnection_v2",
                    help="index to search (default: the clean-crop rebuild)")
    ap.add_argument("--html", default=None,
                    help="HTML output path (default: <image>_colormatch.html)")
    ap.add_argument("--no-html", action="store_true", help="skip the default HTML")
    ap.add_argument("--open", action="store_true", help="open the HTML in a browser")
    ap.add_argument("--montage", default=None, help="also write a look-alike montage JPG here")
    ap.add_argument("--json", default=None, help="also write ranked results JSON here")
    a = ap.parse_args()

    # 1. carefully crop the one marble
    local = Path(a.image)
    src = str(local) if local.exists() else a.image
    if not local.exists():                     # url -> temp file for the cropper
        tmp = ROOT / ".scratch" / "colormatch_q.jpg"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        download_pil(a.image).save(tmp, quality=92)
        src = str(tmp)
    crop, how = tight_crop(src)
    q = embed_images([crop])[0]

    # 2. rank the index by pure cosine similarity, best per thread
    emb, meta, label = _load_index(a.index)
    if emb.shape[1] != q.shape[0]:             # stale index built under a different model
        raise SystemExit(
            f"index '{label}' has dim {emb.shape[1]} but the query embeds to "
            f"{q.shape[0]} — it was built with a different CLIP model. "
            f"Rebuild it (it's regenerable).")
    sims = emb @ q
    best = {}
    for i, m in enumerate(meta):
        t = m.get("tid", i)
        if t not in best or sims[i] > best[t][0]:
            best[t] = (float(sims[i]), m)
    ranked = sorted(best.values(), key=lambda x: -x[0])[: a.top]

    print(f"\nTop {len(ranked)} COLOUR-matched forum listings (CLIP likeness only — "
          f"NOT a maker ID)")
    print(f"  query crop: {how} | index: {label} | {len(best)} threads searched\n")
    for r, (sc, m) in enumerate(ranked, 1):
        print(f"{r:2}. sim {sc:.3f}  {(m.get('title') or '').strip()[:70]}")
        print(f"     {m.get('turl')}")

    if a.json:
        Path(a.json).write_text(json.dumps(
            [{"rank": r, "sim": round(sc, 4), "title": m.get("title"),
              "turl": m.get("turl"), "img": m.get("img"), "tid": m.get("tid")}
             for r, (sc, m) in enumerate(ranked, 1)], indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    if not a.no_html:
        html_out = a.html or (
            str(local.with_name(local.stem + "_colormatch.html")) if local.exists()
            else str(ROOT / ".scratch" / "colormatch.html"))
        _html(crop, Path(a.image).name, ranked, label, html_out)
        uri = Path(html_out).resolve().as_uri()
        print(f"\nHTML (open in a browser): {html_out}\n  {uri}")
        if a.open:
            import webbrowser
            webbrowser.open(uri)
    if a.montage:
        print(f"wrote montage {_montage(crop, ranked, a.montage)}")


if __name__ == "__main__":
    main()
