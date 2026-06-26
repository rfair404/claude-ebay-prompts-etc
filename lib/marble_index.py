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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vindex import (  # noqa: E402
    VIndex, http_get, download_pil, download_pils, nap, embed_images,
)
from forum_replies import parse_posts, best_answer, clean_text  # noqa: E402  ANSWERS not titles

# --- config -----------------------------------------------------------------
FORUM_BASE = "https://marbleconnection.com/forum/22-marble-ids/"
IDX = VIndex("marbleconnection")
# "Nice to the server" rate limiter — OPT-IN. By default the crawl runs at full
# speed with NO inter-request pauses (the forum easily tolerates the modest
# volume of a page-range sync). Turn the polite pauses ON only when you ask for
# them: `--polite` on index/refresh, or MARBLE_POLITE=1. The delays below are
# applied ONLY in polite mode; they're randomized (min,max) per request so the
# traffic pattern isn't a fixed metronome.
POLITE = os.environ.get("MARBLE_POLITE", "").strip().lower() in ("1", "true", "yes", "on")
PAGE_DELAY = (1.0, 2.5)   # between forum page / topic fetches (polite mode only)
IMG_DELAY = (0.25, 0.8)   # between image downloads (polite + sequential only)

# Speed knob: MARBLE_IMG_WORKERS downloads a topic's images CONCURRENTLY (they
# hit S3/image hosts, which are built to be fast). Default 8 (concurrent); set
# MARBLE_IMG_WORKERS=1 for the old sequential behaviour. Note the per-page model
# load is ~17s on CPU — to amortize it, prefer ONE multi-page run (e.g.
# `--start-page 24 --max-pages 6`) over many single-page runs.
IMG_WORKERS = max(1, int(os.environ.get("MARBLE_IMG_WORKERS", "8")))
FAST = IMG_WORKERS > 1
FAST_PAGE_DELAY = (0.1, 0.4)   # shorter between-topic pause used in polite+fast mode


def pace(delay):
    """Inter-request pause — a no-op unless the opt-in polite rate limiter is on."""
    if POLITE:
        nap(delay)


# Quiet/heartbeat mode: suppress the chatty per-topic lines and instead print a
# single "." every few seconds so a long run never looks hung. ASCII only.
QUIET = os.environ.get("MARBLE_QUIET", "").strip().lower() in ("1", "true", "yes", "on")


class _Heartbeat:
    """Background thread that prints a bare '.' every `every` seconds (flushed),
    covering the silent model-load / download / embed gaps. stop() ends the line."""
    def __init__(self, every=3.0):
        self._stop = threading.Event()
        self._every = every
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self._every):
            print(".", end="", flush=True)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        try:
            self._t.join(timeout=2)
        except Exception:
            pass
        print("", flush=True)   # newline so following output starts clean
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
            "pages_indexed": 0, "backend": None, "model": None,
            "auto_next_page": 1, "auto_stop_page": None, "last_page_fp": None}


def _read_thread(page, turl, title):
    """Parse a thread's posts -> (thread_meta, full_posts). The title is the OP's
    GUESS; the value is the reputation-ranked expert reply. Same HTML we already
    fetched for images, so no extra request."""
    try:
        posts = parse_posts(page)
    except Exception:
        posts = []
    op_q = clean_text(posts[0]["text"])[:300] if posts else title
    ans = best_answer(posts[1:])               # highest-rep SUBSTANTIVE reply
    meta = {
        "op_question": op_q,
        "answer": clean_text(ans["text"])[:300] if ans else None,
        "answer_by": ans["author"] if ans else None,
        "answer_group": ans["group"] if ans else None,
        "answer_rep": round(ans["rep"], 2) if ans else 0.0,
        "n_replies": max(0, len(posts) - 1),
    }
    return meta, posts


def _dump_thread(tid, turl, title, posts):
    """Append a compact per-thread reply record to threads.jsonl (sidecar) so the
    responder registry can be (re)built and answers re-ranked WITHOUT re-crawling."""
    IDX.dir.mkdir(parents=True, exist_ok=True)
    rec = {"tid": tid, "turl": turl, "title": title,
           "op_question": posts[0]["text"][:300] if posts else title,
           "replies": [{"author": r["author"], "group": r["group"],
                        "posts": r["posts"], "rep": round(r["rep"], 2),
                        "text": r["text"][:240]} for r in posts[1:]]}
    with (IDX.dir / "threads.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# Topic-preparation concurrency: how many topics are fetched + image-downloaded
# in parallel (network) while the main thread embeds (CPU). The whole point of
# the pipeline is to hide network latency behind the ~9 img/s CPU embedding, so a
# small pool is plenty. Off in polite mode (gentle = strictly serial).
PREP_WORKERS = max(1, int(os.environ.get("MARBLE_PREP_WORKERS", "3")))


# --- crawl core --------------------------------------------------------------
def crawl_topics(topics, state, *, verbose=True):
    """Crawl a list of (tid, turl, title), embed OP images, append to index.

    Pipeline: a small pool of producer threads fetches each topic's HTML and
    downloads its images (network-bound) and feeds ready batches to the main
    thread, which embeds them with CLIP (CPU-bound) and appends to the index.
    Because torch releases the GIL during the heavy encode, the producers keep
    fetching while we embed — so per-page wall-clock ≈ max(network, embedding)
    instead of their sum. Embedding + index append stay single-threaded (main).
    Polite mode (`--polite`) bypasses the pipeline and runs strictly serial with
    the inter-request naps. Quiet mode (`--quiet`/MARBLE_QUIET) replaces the
    per-topic lines with a '.' heartbeat."""
    verbose = verbose and not QUIET
    indexed_tids = set(state["indexed_topic_ids"])
    indexed_imgs = set(state["indexed_img_urls"])
    todo = [(tid, turl, title) for (tid, turl, title) in topics
            if tid not in indexed_tids]
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
    dl_delay = IMG_DELAY if (POLITE and not FAST) else None
    # snapshot for producers: dedup against what was indexed before this run.
    # cross-topic url dups within a run are negligible (each post has its own
    # uploads) and the consumer re-checks `indexed_imgs` before embedding anyway.
    seen0 = frozenset(indexed_imgs)

    def prepare(item):
        """Producer (runs in a worker thread): network only — fetch + download."""
        tid, turl, title = item
        page = http_get(turl)
        imgs = parse_topic_images(page, turl)
        thread_meta, posts = _read_thread(page, turl, title)
        new_urls = [u for u in imgs if u not in seen0]
        pairs = download_pils(new_urls, workers=dl_workers, delay=dl_delay)
        return {"tid": tid, "turl": turl, "title": title,
                "thread_meta": thread_meta, "posts": posts, "pairs": pairs}

    def consume(prep):
        """Main thread only: dedup, embed (via flush at 16), append, dump."""
        nonlocal pil_batch, batch_meta
        tid, turl, title = prep["tid"], prep["turl"], prep["title"]
        thread_meta = prep["thread_meta"]
        _dump_thread(tid, turl, title, prep["posts"])
        kept = 0
        for u, pil in prep["pairs"]:
            if u in indexed_imgs:
                continue
            pil_batch.append(pil)
            batch_meta.append({"img": u, "tid": tid, "turl": turl,
                               "title": title, **thread_meta})
            indexed_imgs.add(u)
            kept += 1
            if len(pil_batch) >= 16:
                flush()
        indexed_tids.add(tid)
        state["max_topic_id"] = max(state["max_topic_id"], tid)
        if verbose:
            ans = thread_meta["answer"]
            tag = f'➜ {thread_meta["answer_by"]}: {ans[:40]}' if ans else "(no replies)"
            print(f"  + topic {tid}: {kept} img  {tag}", flush=True)

    hb = _Heartbeat().start() if QUIET else None
    try:
        if POLITE or PREP_WORKERS <= 1:
            # gentle / serial: prepare and consume one topic at a time, with naps.
            for item in todo:
                try:
                    prep = prepare(item)
                except Exception as e:
                    if verbose:
                        print(f"  ! topic {item[0]} fetch/parse failed: {e}", flush=True)
                    pace(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
                    continue
                consume(prep)
                pace(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
        else:
            # fast: windowed producer pool (backpressure-bounded so at most ~2×
            # PREP_WORKERS topics' images are buffered in memory at once) overlapping
            # the main-thread embed.
            it = iter(todo)
            window = PREP_WORKERS * 2
            with ThreadPoolExecutor(max_workers=PREP_WORKERS) as ex:
                inflight = {}
                for _ in range(window):
                    item = next(it, None)
                    if item is None:
                        break
                    inflight[ex.submit(prepare, item)] = item
                while inflight:
                    fut = next(as_completed(list(inflight)))
                    item = inflight.pop(fut)
                    try:
                        consume(fut.result())
                    except Exception as e:
                        if verbose:
                            print(f"  ! topic {item[0]} fetch/parse failed: {e}",
                                  flush=True)
                    nxt = next(it, None)
                    if nxt is not None:
                        inflight[ex.submit(prepare, nxt)] = nxt
        flush()
    finally:
        if hb:
            hb.stop()
    state["indexed_topic_ids"] = sorted(indexed_tids)
    state["indexed_img_urls"] = sorted(indexed_imgs)
    if dim_seen:
        IDX.stamp(state, dim_seen)   # records backend/model/dim + last_sync
    IDX.save_state(state)
    return added_imgs


# End-of-forum detection (no hard-coded page cap — that becomes a false floor as
# the forum grows). The forum CLAMPS an out-of-range page number to a default
# listing instead of returning an empty page, so "no topics -> end" never fires
# past the real last page. But the clamp serves the SAME listing for every
# over-range page, so a page whose topic ids are IDENTICAL to the previous page's
# means the listing has stopped advancing — i.e. we've run off the end. Real
# pages are always distinct (even an already-indexed *middle* page has unique
# ids), so this never false-stops a back-fill, and it self-calibrates to whatever
# the true last page currently is.
def _page_fp(topics):
    """A listing page's identity = its ordered topic ids."""
    return [t[0] for t in topics]


def _is_repeat(topics, prev_fp):
    """True if this page duplicates the previous page's listing (clamp => end)."""
    return bool(prev_fp) and _page_fp(topics) == prev_fp


# --- commands ----------------------------------------------------------------
def cmd_index(args):
    with _single_writer("index") as ok:
        if not ok:
            return
        state = IDX.load_state(_default_state())
        total = 0
        prev_fp = None
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
            if _is_repeat(topics, prev_fp):
                print(f"[page {page}] listing repeats the previous page — reached "
                      f"the end of the forum; stopping.", flush=True)
                break
            print(f"[page {page}] {len(topics)} topics", flush=True)
            total += crawl_topics(topics, state)
            prev_fp = _page_fp(topics)
            state["last_page_fp"] = prev_fp
            state["pages_indexed"] = max(state["pages_indexed"], page)
            pace(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
        print(f"\nDone. +{total} images this run. Index now holds {state['count']} images "
              f"across {len(state['indexed_topic_ids'])} threads.", flush=True)


def _acquire_lock(stale_s=900):
    """Single-writer guard for the scheduled `next` task. Returns the lock Path,
    or None if a fresh run already holds it (so an overlapping tick skips instead
    of corrupting the index with a concurrent writer). A lock older than stale_s
    is treated as a crashed run and taken over."""
    import time
    IDX.dir.mkdir(parents=True, exist_ok=True)
    lock = IDX.dir / ".next.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > stale_s:
                lock.unlink()
                return _acquire_lock(stale_s)
        except OSError:
            pass
        return None


@contextmanager
def _single_writer(label):
    """Only ONE crawl may write the index at a time — `next`, `index` and
    `refresh` all share this lock so a manual run can't collide with the
    once-a-minute scheduled task (that collision is what corrupted the index
    before). Skips (yields False) if another crawl already holds it."""
    lock = _acquire_lock()
    if lock is None:
        print(f"[{label}] another crawl is in progress — skipping.", flush=True)
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _disable_self():
    """Best-effort: disable the Windows scheduled task so it stops firing once the
    crawl is finished (the stop page or the end of the forum is reached)."""
    import subprocess
    name = os.environ.get("MARBLE_TASK_NAME", "MarbleIndexNext")
    try:
        subprocess.run(["schtasks", "/Change", "/TN", name, "/DISABLE"],
                       capture_output=True, timeout=30)
        print(f"[next] disabled scheduled task '{name}'.", flush=True)
    except Exception as e:
        print(f"[next] could not auto-disable task '{name}': {e} "
              f"(stop it with: schtasks /Change /TN {name} /DISABLE)", flush=True)


def cmd_next(args):
    """Crawl exactly ONE page, then advance a saved cursor — built for a
    once-a-minute scheduled task. Keeping each run to a single page lets the
    thermally-limited laptop CPU cool between runs (sustained batches throttle).
    The scheduler can call this with no args; the cursor lives in state.json.
    Use --set-start to (re)position the cursor, --step to change direction,
    --stop-page to set the last page to crawl (after which the task self-disables).
    The shared single-writer lock makes overlapping runs skip rather than
    double-write the index."""
    with _single_writer("next") as ok:
        if not ok:
            return
        state = IDX.load_state(_default_state())
        if args.set_start is not None:
            state["auto_next_page"] = args.set_start
        if args.stop_page is not None:
            state["auto_stop_page"] = args.stop_page
        page = state.get("auto_next_page") or 1
        stop = state.get("auto_stop_page")
        prev_fp = state.get("last_page_fp")
        if stop is not None and page > stop:
            print(f"[next] indexing already complete — cursor {page} is past stop "
                  f"page {stop}; nothing to do.", flush=True)
            IDX.save_state(state)
            _disable_self()
            return
        url = _listing_url(page, sort_by_creation=True)
        try:
            topics = parse_listing(http_get(url), url)
        except Exception as e:
            print(f"[next p{page}] listing failed: {e} — cursor unchanged.", flush=True)
            return
        if not topics or _is_repeat(topics, prev_fp):
            # past the last real page (empty OR the over-range clamp repeating the
            # previous page): done, even if below the stop page.
            why = "no topics" if not topics else "listing repeats previous page"
            print(f"[next p{page}] {why} — reached the end of the forum; "
                  f"cursor held at {page}.", flush=True)
            IDX.save_state(state)
            _disable_self()
            return
        print(f"[next p{page}] {len(topics)} topics", flush=True)
        added = crawl_topics(topics, state)
        state["last_page_fp"] = _page_fp(topics)
        state["pages_indexed"] = max(state["pages_indexed"], page)
        state["auto_next_page"] = page + args.step
        IDX.save_state(state)
        done = stop is not None and state["auto_next_page"] > stop
        nxt = f"page {state['auto_next_page']}" if not done else f"DONE (stop page {stop} reached)"
        print(f"\nPage {page} done. +{added} images. Index holds {state['count']} "
              f"images / {len(state['indexed_topic_ids'])} threads. Next run -> {nxt}.",
              flush=True)
        if done:
            print(f"\nReached stop page {stop} — crawled through page {page}. "
                  f"Indexing complete.", flush=True)
            _disable_self()


def cmd_refresh(args):
    """Catch up on NEW threads at the FRONT of the forum: crawl from page 1
    (newest-first) and STOP as soon as a page contains no un-indexed topics —
    i.e. once we've reached already-indexed territory. This is the cheap, run-it-
    anytime sync for new posts (the `next` cursor only walks DEEP history, so it
    won't pick up brand-new threads on page 1). `--max-new-pages` caps how far it
    will scan before giving up if every page keeps showing new threads."""
    with _single_writer("refresh") as ok:
        if not ok:
            return
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
            pace(FAST_PAGE_DELAY if FAST else PAGE_DELAY)
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
    all_ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)
    top_score = all_ranked[0][0] if all_ranked else 0.0
    # "close" = within args.close cosine of the best match (corroboration signal)
    n_close = sum(1 for sc, _ in best.values() if sc >= top_score - args.close)
    others = n_close - 1
    corrob = (f"{others} other close match(es) within {args.close:.2f} sim"
              if others > 0 else "NO other close matches — lone look-alike, weak signal")
    ranked = all_ranked[: args.top]
    print(f"\nForum visual comps (of {len(best)} threads / {len(meta)} images).")
    print(f"CLOSEST comp: sim {top_score:.3f}.  Corroboration: {corrob}.\n")
    print("(title = OP's GUESS; trust the expert ANSWER below it)\n")
    for rank, (sc, m) in enumerate(ranked, 1):
        ans = m.get("answer")
        tag = "◆ CLOSEST" if rank == 1 else ("· close" if sc >= top_score - args.close else "·")
        print(f"{rank:>2}. {sc:.3f} {tag}  asked: \"{m.get('op_question') or m.get('title')}\"")
        if ans:
            print(f"     ANSWER ➜ {m.get('answer_by')} "
                  f"({m.get('answer_group')}, rep {m.get('answer_rep')}): {ans}")
        else:
            print("     ANSWER ➜ (no replies yet — unresolved)")
        print(f"     {m['turl']}")
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
        "auto_next_page": state.get("auto_next_page", 1),
        "auto_stop_page": state.get("auto_stop_page"),
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
    p.add_argument("--polite", action="store_true",
                   help="enable the 'nice to the server' rate limiter "
                        "(off by default; also via MARBLE_POLITE=1)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-topic lines; print a '.' heartbeat instead")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("next", help="crawl ONE page + advance a cursor "
                                    "(for a once-a-minute scheduled task)")
    p.add_argument("--set-start", type=int, default=None,
                   help="(re)position the page cursor before this run")
    p.add_argument("--step", type=int, default=1,
                   help="cursor advance per run (default 1; use -1 to walk back)")
    p.add_argument("--stop-page", type=int, default=None,
                   help="last page to crawl; once passed, the task self-disables")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-topic lines; print a '.' heartbeat instead")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("refresh", help="sync only new threads since last run")
    p.add_argument("--max-new-pages", type=int, default=10)
    p.add_argument("--polite", action="store_true",
                   help="enable the 'nice to the server' rate limiter "
                        "(off by default; also via MARBLE_POLITE=1)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-topic lines; print a '.' heartbeat instead")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("query", help="find top-K similar threads for reference image(s)")
    p.add_argument("images", nargs="+", help="local path(s) or URL(s) of the marble")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--close", type=float, default=0.04,
                   help="cosine window below the best score that counts as a 'close' "
                        "match (corroboration count)")
    p.add_argument("--json", help="also write ranked results to this JSON file")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("status", help="index size + last sync")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    # CLI --polite turns the opt-in rate limiter on (env MARBLE_POLITE already may);
    # default stays off.
    if getattr(args, "polite", False):
        globals()["POLITE"] = True
    if getattr(args, "quiet", False):
        globals()["QUIET"] = True
    args.func(args)


if __name__ == "__main__":
    main()
