#!/usr/bin/env python3
"""Visual-similarity index over the Marble Connection "Marble I.D.'s" forum.

Build a local CLIP-embedding index of the marble photos people post for ID, so
you can hand it a reference marble and get back the most visually-similar
threads ("the top few listings"). Pairs with specializations/marbles.md.

Pipeline:
  index    crawl forum listing pages (newest-first by thread creation), pull the
           original-poster's marble photos from each thread, embed them with
           CLIP, and append to the local index.
  refresh  re-crawl only NEW threads created since the last run and append them
           (the periodic sync tool). Dedups by thread id + image url.
  query    embed one or more reference images and return the top-K most similar
           threads, grouped by thread, each with its link + best-match score.
  status   print index size + last sync.

Embedding + http + storage now live in lib/vindex.py (the shared CLIP core);
this file is just the forum crawler + meta schema. Embedding is CLIP-only
(CLIP ViT-B/32) — the old phash backend and MARBLE_EMBED switch were removed in
the visual-index refactor (see VINDEX_REFACTOR.md).

Index lives under kb/index/marbleconnection/ (gitignored — regenerable):
  meta.jsonl   one row per embedded image  {i, img, tid, turl, title}
  emb.npy      float32 [N, D] L2-normalised embeddings, row-aligned to meta
  state.json   crawl cursor + dedup sets + {model, dim, count, last_sync_utc}

Honesty note: CLIP retrieval narrows the corpus to candidates by visual
similarity; it does NOT identify maker/era. Treat the top-K as "look here",
then judge each per the marbles specialization (method vs maker vs era).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vindex import (  # noqa: E402
    VIndex, http_get, download_pil, download_pils, nap, embed_images,
)

# --- config -----------------------------------------------------------------
FORUM_BASE = "https://marbleconnection.com/forum/22-marble-ids/"
IDX = VIndex("marbleconnection")
# Randomized polite pauses — (min, max) seconds, drawn uniformly per request so
# the traffic pattern isn't a fixed metronome. Tune to go gentler on the server.
PAGE_DELAY = (1.0, 2.5)   # between forum page / topic fetches
IMG_DELAY = (0.25, 0.8)   # between image downloads (sequential mode only)

# Speed knob: MARBLE_IMG_WORKERS>1 downloads a topic's images CONCURRENTLY (they
# hit S3/image hosts, which are built to be fast) and switches to a short
# between-topic delay. Default 1 = the gentle sequential behaviour (unchanged).
IMG_WORKERS = max(1, int(os.environ.get("MARBLE_IMG_WORKERS", "1")))
FAST = IMG_WORKERS > 1
FAST_PAGE_DELAY = (0.1, 0.4)   # between forum topic-page fetches in fast mode
# image hosts that carry user-posted marble photos (not avatars / emoji / theme)
IMG_HOST_OK = ("s3mcinvision", "/uploads/monthly_")
IMG_SKIP = ("emoji", "avatar", "profile", "default_photo", "theme", ".svg",
            "rising", "set_resources", "/reactions/", "react_")

TOPIC_RE = re.compile(r"/topic/(\d+)-")


# --- parsing (lxml + XPath) --------------------------------------------------
def _listing_url(page: int, sort_by_creation: bool) -> str:
    url = FORUM_BASE if page <= 1 else f"{FORUM_BASE}page/{page}/"
    if sort_by_creation:
        sep = "&" if "?" in url else "?"
        url += f"{sep}sortby=start_date&sortdirection=desc"
    return url


def parse_listing(htmltext: str, base: str):
    """Return [(topic_id:int, topic_url:str, title:str)] for one listing page."""
    from lxml import html as lhtml
    doc = lhtml.fromstring(htmltext)
    doc.make_links_absolute(base)
    seen, out = set(), []
    for a in doc.xpath('//a[contains(@href,"/topic/")]'):
        href = a.get("href") or ""
        m = TOPIC_RE.search(href)
        if not m:
            continue
        tid = int(m.group(1))
        if tid in seen:
            continue
        title = (a.text_content() or "").strip()
        # skip the bare date/"last reply" anchors; keep the real title anchor
        if len(title) < 5 or title[:1].isdigit() and "/" in title:
            continue
        seen.add(tid)
        out.append((tid, href.split("?")[0], title))
    return out


def parse_topic_images(htmltext: str, base: str):
    """Return the original poster's marble image URLs (full-size preferred)."""
    from lxml import html as lhtml
    doc = lhtml.fromstring(htmltext)
    doc.make_links_absolute(base)

    # the first post's content block (Invision Power Board)
    blocks = doc.xpath('(//div[contains(@class,"cPost_contentWrap")])[1]')
    scope = blocks[0] if blocks else doc

    urls = []
    # prefer <a> links to the full-size image, then <img> src as fallback
    for node in scope.xpath('.//a[@href]'):
        h = node.get("href") or ""
        if _img_ok(h):
            urls.append(h)
    for node in scope.xpath('.//img[@src]'):
        s = node.get("src") or node.get("data-src") or ""
        if _img_ok(s):
            urls.append(s)
    # dedup, drop obvious thumbnail dupes when a full-size sibling exists
    out, seen = [], set()
    for u in urls:
        key = u.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _img_ok(u: str) -> bool:
    if not u:
        return False
    lo = u.lower()
    if not lo.startswith("http"):
        return False
    if any(b in lo for b in IMG_SKIP):
        return False
    if not any(h in lo for h in IMG_HOST_OK):
        return False
    return lo.endswith((".jpg", ".jpeg", ".png"))


# --- storage -----------------------------------------------------------------
def _default_state():
    return {"indexed_topic_ids": [], "indexed_img_urls": [],
            "max_topic_id": 0, "count": 0, "last_sync_utc": None,
            "pages_indexed": 0, "backend": None, "model": None}


# --- crawl core --------------------------------------------------------------
def crawl_topics(topics, state, *, verbose=True):
    """Crawl a list of (tid, turl, title), embed OP images, append to index."""
    indexed_tids = set(state["indexed_topic_ids"])
    indexed_imgs = set(state["indexed_img_urls"])
    pil_batch, batch_meta = [], []
    added_imgs = 0
    dim_seen = 0

    def flush():
        nonlocal pil_batch, batch_meta, added_imgs, dim_seen
        if not pil_batch:
            return
        emb = embed_images(pil_batch)
        dim_seen = emb.shape[1]
        base_i = state["count"]
        rows = [dict(m, i=base_i + k) for k, m in enumerate(batch_meta)]
        IDX.append(rows, emb)
        state["count"] += len(rows)
        added_imgs += len(rows)
        pil_batch, batch_meta = [], []

    dl_workers = IMG_WORKERS
    dl_delay = None if FAST else IMG_DELAY
    for tid, turl, title in topics:
        if tid in indexed_tids:
            continue
        try:
            page = http_get(turl)
            imgs = parse_topic_images(page, turl)
        except Exception as e:
            if verbose:
                print(f"  ! topic {tid} fetch/parse failed: {e}", flush=True)
            nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
            continue
        kept = 0
        new_urls = [u for u in imgs if u not in indexed_imgs]
        for u, pil in download_pils(new_urls, workers=dl_workers, delay=dl_delay):
            pil_batch.append(pil)
            batch_meta.append({"img": u, "tid": tid, "turl": turl, "title": title})
            indexed_imgs.add(u)
            kept += 1
            if len(pil_batch) >= 16:
                flush()
        indexed_tids.add(tid)
        state["max_topic_id"] = max(state["max_topic_id"], tid)
        if verbose:
            print(f"  + topic {tid}: {kept} img  \"{title[:48]}\"", flush=True)
        nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)

    flush()
    state["indexed_topic_ids"] = sorted(indexed_tids)
    state["indexed_img_urls"] = sorted(indexed_imgs)
    if dim_seen:
        IDX.stamp(state, dim_seen)   # records backend/model/dim + last_sync
    IDX.save_state(state)
    return added_imgs


# --- commands ----------------------------------------------------------------
def cmd_index(args):
    state = IDX.load_state(_default_state())
    total = 0
    for page in range(args.start_page, args.start_page + args.max_pages):
        url = _listing_url(page, sort_by_creation=True)
        try:
            topics = parse_listing(http_get(url), url)
        except Exception as e:
            print(f"[page {page}] listing failed: {e}", flush=True)
            break
        if not topics:
            print(f"[page {page}] no topics — stopping.", flush=True)
            break
        print(f"[page {page}] {len(topics)} topics", flush=True)
        total += crawl_topics(topics, state)
        state["pages_indexed"] = max(state["pages_indexed"], page)
        nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
    print(f"\nDone. +{total} images this run. Index now holds {state['count']} images "
          f"across {len(state['indexed_topic_ids'])} threads.", flush=True)


def cmd_refresh(args):
    """Sync only NEW threads created since last run (periodic update)."""
    state = IDX.load_state(_default_state())
    before = state["count"]
    known = set(state["indexed_topic_ids"])
    total = 0
    for page in range(1, args.max_new_pages + 1):
        url = _listing_url(page, sort_by_creation=True)
        try:
            topics = parse_listing(http_get(url), url)
        except Exception as e:
            print(f"[refresh p{page}] listing failed: {e}", flush=True)
            break
        fresh = [t for t in topics if t[0] not in known]
        print(f"[refresh p{page}] {len(fresh)} new / {len(topics)} topics", flush=True)
        if not fresh:
            print("Reached already-indexed threads — sync complete.", flush=True)
            break
        total += crawl_topics(fresh, state)
        known |= {t[0] for t in fresh}
        nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
    print(f"\nRefresh done. +{total} images ({before} -> {state['count']}).", flush=True)


def cmd_query(args):
    state = IDX.load_state(_default_state())
    IDX.check_model(state)
    emb, meta = IDX.load()
    pil = []
    for ref in args.images:
        p = Path(ref)
        try:
            if p.exists():
                from PIL import Image
                pil.append(Image.open(p).convert("RGB"))
            else:
                pil.append(download_pil(ref))
        except Exception as e:
            print(f"! could not load reference {ref}: {e}", file=sys.stderr)
    if not pil:
        raise SystemExit("No usable reference image.")
    q = embed_images(pil)                       # [r, D] normalised
    sims = emb @ q.T                            # [N, r] cosine
    score = sims.max(axis=1)                    # best across reference angles
    # best image per thread
    best = {}
    for i, m in enumerate(meta):
        tid = m["tid"]
        if tid not in best or score[i] > best[tid][0]:
            best[tid] = (float(score[i]), m)
    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)[: args.top]
    print(f"\nTop {len(ranked)} visually-similar threads "
          f"(of {len(best)} threads / {len(meta)} images):\n")
    for rank, (sc, m) in enumerate(ranked, 1):
        print(f"{rank:>2}. {sc:.3f}  {m['title']}")
        print(f"     {m['turl']}")
        print(f"     match img: {m['img']}")
    if args.json:
        out = [{"rank": r, "score": round(sc, 4), **m}
               for r, (sc, m) in enumerate(ranked, 1)]
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.json}")


def cmd_status(args):
    state = IDX.load_state(_default_state())
    print(json.dumps({
        "images": state["count"],
        "threads": len(state["indexed_topic_ids"]),
        "max_topic_id": state["max_topic_id"],
        "model": state.get("model"),
        "pages_indexed": state["pages_indexed"],
        "last_sync_utc": state["last_sync_utc"],
        "index_dir": str(IDX.dir),
    }, indent=2))


def main():
    # Forum titles can carry emoji; the Windows console / redirected log defaults
    # to cp1252 and a bare print() of such a title raises UnicodeEncodeError
    # (which would kill a long crawl). Force utf-8 so progress prints never crash.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="crawl + embed forum threads (newest-first)")
    p.add_argument("--max-pages", type=int, default=10)
    p.add_argument("--start-page", type=int, default=1,
                   help="start deeper to index older history (dedups by thread id)")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("refresh", help="sync only new threads since last run")
    p.add_argument("--max-new-pages", type=int, default=10)
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("query", help="find top-K similar threads for reference image(s)")
    p.add_argument("images", nargs="+", help="local path(s) or URL(s) of the marble")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", help="also write ranked results to this JSON file")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("status", help="index size + last sync")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
