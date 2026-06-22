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

Index lives under kb/index/marbleconnection/ (gitignored — regenerable):
  meta.jsonl   one row per embedded image  {i, img, tid, turl, title}
  emb.npy      float32 [N, D] L2-normalised embeddings, row-aligned to meta
  state.json   crawl cursor + dedup sets + last_sync_utc

Model: CLIP ViT-B/32 via sentence-transformers (downloaded once, ~350 MB).
Honesty note: CLIP retrieval narrows the corpus to candidates by visual
similarity; it does NOT identify maker/era. Treat the top-K as "look here",
then judge each per the marbles specialization (method vs maker vs era).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# --- config -----------------------------------------------------------------
FORUM_BASE = "https://marbleconnection.com/forum/22-marble-ids/"
UA = "Mozilla/5.0 (compatible; ebaybiz-KB/1.0; personal collectables research)"
INDEX_DIR = Path(__file__).resolve().parent.parent / "kb" / "index" / "marbleconnection"
# Randomized polite pauses — (min, max) seconds, drawn uniformly per request so
# the traffic pattern isn't a fixed metronome. Tune to go gentler on the server.
PAGE_DELAY = (1.0, 2.5)   # between forum page / topic fetches
IMG_DELAY = (0.25, 0.8)   # between image downloads (sequential mode only)
EMBED_BATCH = 16

# Speed knob: MARBLE_IMG_WORKERS>1 downloads a topic's images CONCURRENTLY (they
# hit S3/image hosts, which are built to be fast) and switches to a short
# between-topic delay. Default 1 = the gentle sequential behaviour (unchanged).
IMG_WORKERS = max(1, int(os.environ.get("MARBLE_IMG_WORKERS", "1")))
FAST = IMG_WORKERS > 1
FAST_PAGE_DELAY = (0.1, 0.4)   # between forum topic-page fetches in fast mode
MODEL_NAME = "clip-ViT-B-32"
# image hosts that carry user-posted marble photos (not avatars / emoji / theme)
IMG_HOST_OK = ("s3mcinvision", "/uploads/monthly_")
IMG_SKIP = ("emoji", "avatar", "profile", "default_photo", "theme", ".svg",
            "rising", "set_resources", "/reactions/", "react_")

TOPIC_RE = re.compile(r"/topic/(\d+)-")


# --- pacing ------------------------------------------------------------------
def _nap(rng):
    """Sleep a random duration in the (min, max) seconds tuple — polite jitter."""
    time.sleep(random.uniform(rng[0], rng[1]))


# --- tiny http ---------------------------------------------------------------
def _get(url: str, *, binary: bool = False, tries: int = 3, timeout: int = 30):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return data if binary else data.decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {url} ({last})")


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
def _state_path():
    return INDEX_DIR / "state.json"


def load_state():
    p = _state_path()
    if p.exists():
        return json.loads(p.read_text())
    return {"indexed_topic_ids": [], "indexed_img_urls": [],
            "max_topic_id": 0, "count": 0, "last_sync_utc": None,
            "pages_indexed": 0, "backend": None}


def save_state(state):
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, indent=2))


def append_index(rows, embeddings):
    """rows: list of dict meta; embeddings: np.ndarray [n, D] (normalised)."""
    import numpy as np
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    emb_p = INDEX_DIR / "emb.npy"
    meta_p = INDEX_DIR / "meta.jsonl"
    if emb_p.exists():
        old = np.load(emb_p)
        embeddings = np.vstack([old, embeddings]).astype("float32")
    np.save(emb_p, embeddings.astype("float32"))
    with meta_p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_index():
    import numpy as np
    emb_p = INDEX_DIR / "emb.npy"
    meta_p = INDEX_DIR / "meta.jsonl"
    if not emb_p.exists() or not meta_p.exists():
        raise SystemExit("Index is empty — run `index` first.")
    emb = np.load(emb_p)
    meta = [json.loads(l) for l in meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return emb, meta


# --- embedding backend -------------------------------------------------------
# "phash" = no-torch perceptual+colour featurizer (works today).
# "clip"  = CLIP ViT-B/32 via sentence-transformers (needs torch + the MS VC++
#           Redistributable). Switch with the MARBLE_EMBED env var or the flag.
# An index is tied to the backend that built it (query must match) — recorded in
# state.json. To move phash -> clip later: rebuild the index with clip.
import os
EMBED_BACKEND = os.environ.get("MARBLE_EMBED", "phash").lower()
_MODEL = None


def get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        print(f"[model] loading {MODEL_NAME} (first run downloads it)…", flush=True)
        _MODEL = SentenceTransformer(MODEL_NAME)
    return _MODEL


def _embed_clip(pil_images):
    model = get_model()
    emb = model.encode(pil_images, batch_size=EMBED_BATCH, convert_to_numpy=True,
                       normalize_embeddings=True, show_progress_bar=False)
    return emb.astype("float32")


def _feat_phash(pil):
    """One image -> normalised feature vector (colour-weighted + structure).

    No ML model. Captures what drives marble look-alikes: a saturation-weighted
    hue/sat colour histogram (so the marble's glass colour dominates the neutral
    towel/hand background) + a dHash structural signature + a brightness profile.
    Weaker than CLIP, but solid for near-duplicates and same colour/pattern.
    """
    import numpy as np
    HB, SB = 12, 4                                   # hue, saturation bins
    hsv = np.asarray(pil.convert("HSV").resize((64, 64))).astype("float32")
    h, s, v = hsv[..., 0] / 255.0, hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    hi = np.clip((h * HB).astype(int), 0, HB - 1)
    si = np.clip((s * SB).astype(int), 0, SB - 1)
    hist = np.zeros((HB, SB), dtype="float32")
    np.add.at(hist, (hi, si), s)                     # weight by saturation
    hist = hist.flatten()
    if hist.sum() > 0:
        hist /= hist.sum()
    g = np.asarray(pil.convert("L").resize((9, 8))).astype("float32")
    dh = (g[:, 1:] > g[:, :-1]).astype("float32").flatten()   # 64-bit dHash
    vh, _ = np.histogram(v, bins=8, range=(0, 1))
    vh = vh.astype("float32")
    if vh.sum() > 0:
        vh /= vh.sum()
    feat = np.concatenate([hist * 3.0, dh * 1.0, vh * 1.0])   # colour weighted up
    n = float(np.linalg.norm(feat))
    return (feat / n).astype("float32") if n > 0 else feat.astype("float32")


def _embed_phash(pil_images):
    import numpy as np
    return np.vstack([_feat_phash(p) for p in pil_images]).astype("float32")


def embed_images(pil_images):
    if EMBED_BACKEND == "clip":
        return _embed_clip(pil_images)
    return _embed_phash(pil_images)


def _download_pil(url):
    from PIL import Image
    raw = _get(url, binary=True)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _download_pils(urls):
    """Download urls -> [(url, PIL)] for successes. Parallel when IMG_WORKERS>1
    (image hosts tolerate it); else sequential with the polite IMG_DELAY nap."""
    out = []
    if not urls:
        return out
    if IMG_WORKERS <= 1:
        for u in urls:
            try:
                out.append((u, _download_pil(u)))
            except Exception:
                pass
            _nap(IMG_DELAY)
        return out
    with ThreadPoolExecutor(max_workers=IMG_WORKERS) as ex:
        futs = {ex.submit(_download_pil, u): u for u in urls}
        for f in as_completed(futs):
            try:
                out.append((futs[f], f.result()))
            except Exception:
                pass
    return out


# --- crawl core --------------------------------------------------------------
def crawl_topics(topics, state, *, verbose=True):
    """Crawl a list of (tid, turl, title), embed OP images, append to index."""
    indexed_tids = set(state["indexed_topic_ids"])
    indexed_imgs = set(state["indexed_img_urls"])
    new_rows, pil_batch, batch_meta = [], [], []
    added_imgs = 0

    def flush():
        nonlocal pil_batch, batch_meta, added_imgs
        if not pil_batch:
            return
        emb = embed_images(pil_batch)
        base_i = state["count"]
        rows = []
        for k, m in enumerate(batch_meta):
            m = dict(m, i=base_i + k)
            rows.append(m)
        append_index(rows, emb)
        state["count"] += len(rows)
        added_imgs += len(rows)
        pil_batch, batch_meta = [], []

    for tid, turl, title in topics:
        if tid in indexed_tids:
            continue
        try:
            page = _get(turl)
            imgs = parse_topic_images(page, turl)
        except Exception as e:
            if verbose:
                print(f"  ! topic {tid} fetch/parse failed: {e}", flush=True)
            _nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
            continue
        kept = 0
        new_urls = [u for u in imgs if u not in indexed_imgs]
        for u, pil in _download_pils(new_urls):
            pil_batch.append(pil)
            batch_meta.append({"img": u, "tid": tid, "turl": turl, "title": title})
            indexed_imgs.add(u)
            kept += 1
            if len(pil_batch) >= EMBED_BATCH:
                flush()
        indexed_tids.add(tid)
        state["max_topic_id"] = max(state["max_topic_id"], tid)
        if verbose:
            print(f"  + topic {tid}: {kept} img  \"{title[:48]}\"", flush=True)
        _nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)

    flush()
    state["indexed_topic_ids"] = sorted(indexed_tids)
    state["indexed_img_urls"] = sorted(indexed_imgs)
    state["backend"] = EMBED_BACKEND
    state["last_sync_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    return added_imgs


# --- commands ----------------------------------------------------------------
def cmd_index(args):
    state = load_state()
    total = 0
    for page in range(args.start_page, args.start_page + args.max_pages):
        url = _listing_url(page, sort_by_creation=True)
        try:
            topics = parse_listing(_get(url), url)
        except Exception as e:
            print(f"[page {page}] listing failed: {e}", flush=True)
            break
        if not topics:
            print(f"[page {page}] no topics — stopping.", flush=True)
            break
        print(f"[page {page}] {len(topics)} topics", flush=True)
        total += crawl_topics(topics, state)
        state["pages_indexed"] = max(state["pages_indexed"], page)
        _nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
    print(f"\nDone. +{total} images this run. Index now holds {state['count']} images "
          f"across {len(state['indexed_topic_ids'])} threads.", flush=True)


def cmd_refresh(args):
    """Sync only NEW threads created since last run (periodic update)."""
    state = load_state()
    before = state["count"]
    known = set(state["indexed_topic_ids"])
    total = 0
    for page in range(1, args.max_new_pages + 1):
        url = _listing_url(page, sort_by_creation=True)
        try:
            topics = parse_listing(_get(url), url)
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
        _nap(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
    print(f"\nRefresh done. +{total} images ({before} -> {state['count']}).", flush=True)


def cmd_query(args):
    import numpy as np
    state = load_state()
    if state.get("backend") and state["backend"] != EMBED_BACKEND:
        raise SystemExit(
            f"Index was built with backend '{state['backend']}' but query is using "
            f"'{EMBED_BACKEND}'. Set MARBLE_EMBED={state['backend']} or rebuild the index.")
    emb, meta = load_index()
    pil = []
    for ref in args.images:
        p = Path(ref)
        try:
            if p.exists():
                from PIL import Image
                pil.append(Image.open(p).convert("RGB"))
            else:
                pil.append(_download_pil(ref))
        except Exception as e:
            print(f"! could not load reference {ref}: {e}", file=sys.stderr)
    if not pil:
        raise SystemExit("No usable reference image.")
    q = embed_images(pil)                      # [r, D] normalised
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
    state = load_state()
    print(json.dumps({
        "images": state["count"],
        "threads": len(state["indexed_topic_ids"]),
        "max_topic_id": state["max_topic_id"],
        "pages_indexed": state["pages_indexed"],
        "last_sync_utc": state["last_sync_utc"],
        "index_dir": str(INDEX_DIR),
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
