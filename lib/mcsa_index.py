#!/usr/bin/env python3
"""Visual index over the MCSA "Online Marble ID Guide" reference photos.

Builds a local CLIP image-similarity index from marblecollecting.com's
authoritative ID-guide pages — like lib/marble_index.py (forum) but the photos
here are **labeled by the maker/type page they live on** (a confirmed ID), so a
query matches against KNOWN examples, not peer guesses. The strongest ID signal
of the three indexes — and the reason CLIP matters: pHash only finds near-dupes,
CLIP matches the marble across different background/lighting/angle.

robots.txt check (2026-06-24): /marble-reference/ is NOT disallowed (only
auction/members/donation paths are) — reference pages are crawlable. Society
site, so kept gentle (low concurrency, delays). Stores embeddings + source
URLs, not redistributed images — same as the forum index.

  discover  walk the ID-guide index → list of maker/type sub-page URLs
  crawl     fetch each page, pull its cc_images reference photos, CLIP-embed,
            tag with the page's maker label
  query     top-K similar MCSA reference photos for a marble image, with a
            maker tally over the top hits
  status    index size

Embedding + http + storage live in lib/vindex.py (shared CLIP core). Index:
kb/index/marblecollecting/ (gitignored). meta rows: {i,img,maker,page}.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vindex import VIndex, http_get, download_pil, download_pils, embed_images  # noqa: E402

GUIDE = "https://www.marblecollecting.com/marble-reference/online-marble-id-guide/"
IDX = VIndex("marblecollecting")
PAGE_DELAY = 1.0          # (unused once crawled; kept for re-crawl politeness)
IMG_WORKERS = 3           # low concurrency for image downloads (society site)
# reference photos are cc_images cache_<id>.jpg/png; skip nav/teaser/header graphics
IMG_RE = re.compile(r'https?://www\.marblecollecting\.com/s/cc_images/cache_\d+\.(?:jpe?g|png)', re.I)
SUBPAGE_RE = re.compile(r'https?://www\.marblecollecting\.com/marble-reference/online-marble-id-guide/[a-z0-9\-]+/?$', re.I)

# readable maker/type label from the URL slug
LABELS = {
    "akro-agate-co": "Akro Agate", "peltier-glass-co": "Peltier",
    "christensen-agate-co": "Christensen Agate", "m-f-christensen-son-co": "M.F. Christensen",
    "marble-king-inc": "Marble King", "vitro-agate-co": "Vitro Agate",
    "master-marble-glass-cos": "Master Marble", "lawrence-alley-cos": "Alley Agate",
    "heaton-bogard-jabo": "Heaton/Bogard/JABO", "other-u-s-companies": "Other U.S.",
    "foreign-manufacturers": "Foreign", "transitions": "Transitional",
    "end-of-days": "End of Day", "lutzes": "Lutz", "sulphides": "Sulphide",
    "clambroths-indians-etc": "Clambroth/Indian", "all-other-handmades": "Handmade (other)",
    "handmade-marbles": "Handmade", "core-swirls": "Core Swirl", "other-swirls": "Swirl",
    "machine-made-marbles": "Machine-made", "contemporary-marbles": "Contemporary",
}


def _slug(url):
    return url.rstrip("/").split("/")[-1].lower()


def label_for(url):
    s = _slug(url)
    return LABELS.get(s, s.replace("-", " ").title())


def _default_state():
    return {"pages": [], "img_urls": [], "count": 0,
            "last_sync_utc": None, "backend": None, "model": None}


# --- discover ----------------------------------------------------------------
def discover():
    """All maker/type sub-pages linked from the ID-guide index."""
    from lxml import html as lhtml
    doc = lhtml.fromstring(http_get(GUIDE))
    doc.make_links_absolute(GUIDE)
    pages = []
    for a in doc.xpath("//a[@href]"):
        h = (a.get("href") or "").split("?")[0].split("#")[0]
        if SUBPAGE_RE.match(h) and h.rstrip("/") != GUIDE.rstrip("/"):
            if h not in pages:
                pages.append(h)
    return pages


# --- crawl -------------------------------------------------------------------
def crawl(pages, verbose=True):
    import time
    state = IDX.load_state(_default_state())
    done_pages = set(state["pages"])
    seen = set(state["img_urls"])
    added = 0
    dim_seen = 0
    for page in pages:
        if page in done_pages:
            continue
        maker = label_for(page)
        try:
            html = http_get(page)
        except Exception as e:
            if verbose:
                print(f"  ! {page} failed: {e}", flush=True)
            continue
        imgs = [u for u in dict.fromkeys(IMG_RE.findall(html)) if u not in seen]
        pil, meta = [], []
        for u, p in download_pils(imgs, workers=IMG_WORKERS):
            pil.append(p)
            meta.append({"img": u, "maker": maker, "page": page})
            seen.add(u)
        if pil:
            emb = embed_images(pil)
            dim_seen = emb.shape[1]
            base = state["count"]
            rows = [dict(m, i=base + k) for k, m in enumerate(meta)]
            IDX.append(rows, emb)
            state["count"] += len(rows)
            added += len(rows)
        done_pages.add(page)
        if verbose:
            print(f"  + {maker:22} {len(pil):>3} img   {page}", flush=True)
        time.sleep(PAGE_DELAY)
    state["pages"] = sorted(done_pages)
    state["img_urls"] = sorted(seen)
    if dim_seen:
        IDX.stamp(state, dim_seen)
    IDX.save_state(state)
    if verbose:
        print(f"\nDone. +{added} reference images. Index holds {state['count']} "
              f"across {len(done_pages)} pages.", flush=True)
    return added


def cmd_query(args):
    import numpy as np
    state = IDX.load_state(_default_state())
    IDX.check_model(state)
    emb, meta = IDX.load()
    pil = []
    for ref in args.images:
        p = Path(ref)
        try:
            from PIL import Image
            pil.append(Image.open(p).convert("RGB") if p.exists() else download_pil(ref))
        except Exception as e:
            print(f"! {ref}: {e}", file=sys.stderr)
    if not pil:
        raise SystemExit("No usable reference image.")
    q = embed_images(pil)
    score = (emb @ q.T).max(axis=1)
    order = np.argsort(-score)[: args.top]
    print(f"\nTop {len(order)} similar MCSA reference photos (of {len(meta)}):\n")
    votes = Counter()
    for rank, i in enumerate(order, 1):
        m = meta[int(i)]
        votes[m["maker"]] += 1
        print(f"{rank:>2}. {score[i]:.3f}  [{m['maker']}]  {m['img'].split('/')[-1]}")
        print(f"     {m['page']}")
    print(f"\nmaker tally of top {len(order)}: " +
          ", ".join(f"{k} x{v}" for k, v in votes.most_common()))
    if args.json:
        out = [{"rank": r, "score": round(float(score[int(i)]), 4), **meta[int(i)]}
               for r, i in enumerate(order, 1)]
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.json}")


def cmd_status(args):
    s = IDX.load_state(_default_state())
    print(json.dumps({"images": s["count"], "pages": len(s["pages"]),
                      "model": s.get("model"), "last_sync_utc": s["last_sync_utc"],
                      "dir": str(IDX.dir)}, indent=2))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("discover", help="list ID-guide sub-pages")
    p.set_defaults(func=lambda a: print("\n".join(discover())))
    p = sub.add_parser("crawl", help="crawl all (or given) ID-guide pages")
    p.add_argument("--pages", nargs="*", help="specific page URLs (default: discover all)")
    p.set_defaults(func=lambda a: crawl(a.pages or discover()))
    p = sub.add_parser("query", help="top-K similar reference photos")
    p.add_argument("images", nargs="+")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--json", help="also write ranked results to this JSON file")
    p.set_defaults(func=cmd_query)
    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
