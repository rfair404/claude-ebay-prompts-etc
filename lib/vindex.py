#!/usr/bin/env python3
"""Shared CLIP visual-index core for the marble/eBay similarity indexes.

One embedding space, one model load, one on-disk store layout — so the forum
look-alike index, the MCSA labeled-reference index, and the eBay priced-comp
index can all be built and queried the same way (and, later, queried together).

What lives here:
  - http helpers      http_get / download_pil / download_pils / nap
  - embedding (CLIP)  embed_images / embed_texts  (image+text share one space)
  - the store         class VIndex  (emb.npy + meta.jsonl + state.json per index)

Embedding is **CLIP only** (CLIP ViT-B/32 via sentence-transformers). Unlike the
old phash featurizer, CLIP puts images AND text in the same space, which unlocks
text queries and zero-shot label scoring (see VINDEX_REFACTOR.md, Stage 3).

Honesty note (unchanged from the phash era): similarity retrieval is a CANDIDATE
finder, not an identification. Treat top-K as "look here", then judge per
specializations/marbles.md (method != origin != era).

Dependency: sentence-transformers + torch (+ the MS VC++ Redistributable on
Windows). The first run downloads the model (~350 MB) into the HF cache.
"""
from __future__ import annotations

import io
import json
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# --- config -----------------------------------------------------------------
UA = "Mozilla/5.0 (compatible; ebaybiz-KB/1.0; personal collectables research)"
INDEX_ROOT = Path(__file__).resolve().parent.parent / "kb" / "index"
MODEL_NAME = "clip-ViT-B-32"   # single config point — bump (e.g. clip-ViT-L-14)
                               # to upgrade every index together (forces rebuild)
EMBED_BATCH = 16


# --- pacing ------------------------------------------------------------------
def nap(rng):
    """Sleep a uniform-random duration in the (min, max) seconds tuple."""
    time.sleep(random.uniform(rng[0], rng[1]))


# --- tiny http ---------------------------------------------------------------
def http_get(url: str, *, binary: bool = False, tries: int = 3, timeout: int = 30):
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


def download_pil(url):
    from PIL import Image
    raw = http_get(url, binary=True)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def download_pils(urls, *, workers: int = 1, delay=None):
    """Download urls -> [(url, PIL)] for successes.

    Parallel when workers>1 (image hosts tolerate it); else sequential with an
    optional polite (min,max) `delay` nap between downloads.
    """
    out = []
    if not urls:
        return out
    if workers <= 1:
        for u in urls:
            try:
                out.append((u, download_pil(u)))
            except Exception:
                pass
            if delay:
                nap(delay)
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(download_pil, u): u for u in urls}
        for f in as_completed(futs):
            try:
                out.append((futs[f], f.result()))
            except Exception:
                pass
    return out


# --- embedding (CLIP only) ---------------------------------------------------
_MODEL = None


def get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        print(f"[model] loading {MODEL_NAME} (first run downloads it)…", flush=True)
        _MODEL = SentenceTransformer(MODEL_NAME)
    return _MODEL


def embed_images(pil_images):
    """[PIL] -> float32 [N, D] L2-normalised CLIP image embeddings."""
    model = get_model()
    emb = model.encode(pil_images, batch_size=EMBED_BATCH, convert_to_numpy=True,
                       normalize_embeddings=True, show_progress_bar=False)
    return emb.astype("float32")


def embed_texts(strings):
    """[str] -> float32 [N, D] L2-normalised CLIP text embeddings.

    Same space as embed_images — so a text prompt can query an image index, and
    a label vocabulary can be scored against a photo (Stage 3 features).
    """
    model = get_model()
    emb = model.encode(list(strings), batch_size=EMBED_BATCH, convert_to_numpy=True,
                       normalize_embeddings=True, show_progress_bar=False)
    return emb.astype("float32")


def _now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- store -------------------------------------------------------------------
class VIndex:
    """One on-disk visual index under kb/index/<name>/.

    Files (all gitignored / regenerable):
      emb.npy      float32 [N, D] L2-normalised, row-aligned to meta
      meta.jsonl   one JSON row per embedded image (schema is the caller's)
      state.json   crawl cursor + dedup sets + {model, dim, count, last_sync_utc}

    The store owns embeddings + meta + the generic state fields; the crawler owns
    its own source-specific state keys (e.g. indexed_topic_ids) in the same dict.
    `model`/`dim` are stamped on write and checked on query so a stale index
    (different model, or the old phash backend) fails loud instead of returning
    garbage cosine scores across mismatched spaces.
    """

    def __init__(self, name: str):
        self.dir = INDEX_ROOT / name
        self.emb_p = self.dir / "emb.npy"
        self.meta_p = self.dir / "meta.jsonl"
        self.state_p = self.dir / "state.json"

    # -- state --
    def load_state(self, default: dict) -> dict:
        if self.state_p.exists():
            return json.loads(self.state_p.read_text())
        return dict(default)

    def save_state(self, state: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_p.write_text(json.dumps(state, indent=2))

    def stamp(self, state: dict, dim: int):
        """Record the embedding identity + sync time after a write."""
        state["backend"] = "clip"
        state["model"] = MODEL_NAME
        state["dim"] = int(dim)
        state["last_sync_utc"] = _now_utc()

    def check_model(self, state: dict):
        """Raise if the index wasn't built with the current CLIP model."""
        m = state.get("model")
        if m != MODEL_NAME:
            built = m or f"legacy/{state.get('backend', 'unknown')}"
            raise SystemExit(
                f"Index '{self.dir.name}' was built with '{built}' but this code "
                f"uses CLIP '{MODEL_NAME}'. Rebuild the index (it's regenerable).")

    # -- data --
    def append(self, rows, emb):
        """Append meta rows + embeddings [n, D] (already normalised)."""
        import numpy as np
        self.dir.mkdir(parents=True, exist_ok=True)
        emb = np.asarray(emb, dtype="float32")
        if self.emb_p.exists():
            emb = np.vstack([np.load(self.emb_p), emb]).astype("float32")
        np.save(self.emb_p, emb)
        with self.meta_p.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def load(self):
        """-> (emb [N, D], meta list). Raises if the index is empty."""
        import numpy as np
        if not self.emb_p.exists() or not self.meta_p.exists():
            raise SystemExit(f"Index '{self.dir.name}' is empty — build it first.")
        emb = np.load(self.emb_p)
        meta = [json.loads(l) for l in
                self.meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
        return emb, meta
