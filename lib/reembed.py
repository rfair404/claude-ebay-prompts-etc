#!/usr/bin/env python3
"""Re-embed an existing visual index under the current CLIP model — NO re-crawl.

Every index stores image URLs in meta.jsonl, so migrating a pHash index to CLIP
(or to a newer CLIP model) just means re-downloading the images and re-embedding
— skipping all the HTML crawling. Streams in chunks so it scales from the MCSA
index (~315 imgs) to the forum (~30k).

  python lib/reembed.py <index-name> [--workers 16] [--chunk 256]
    e.g. python lib/reembed.py marblecollecting

Rewrites <index>/emb.npy + meta.jsonl + state.json consistently:
  - drops rows whose image 404s (so a dead URL doesn't desync emb/meta)
  - re-stamps state with the current model/dim (count = surviving rows)
  - preserves the crawler's own state fields (pages, indexed_topic_ids, …)

See VINDEX_REFACTOR.md. Validation: after re-embedding marblecollecting, the
blue Vitro Conqueror (.scratch/marble-ids/t44662/) should rank Vitro Agate top.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vindex import VIndex, download_pils, embed_images  # noqa: E402


def reembed(name: str, *, workers: int = 16, chunk: int = 256, verbose: bool = True):
    import numpy as np
    vi = VIndex(name)
    if not vi.meta_p.exists():
        raise SystemExit(f"No meta.jsonl under {vi.dir} — nothing to re-embed.")
    meta = [json.loads(l) for l in
            vi.meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if verbose:
        print(f"[reembed] {name}: {len(meta)} rows -> CLIP, "
              f"workers={workers} chunk={chunk}", flush=True)

    kept_rows, emb_parts = [], []
    dropped = 0
    for start in range(0, len(meta), chunk):
        block = meta[start:start + chunk]
        urls = [r["img"] for r in block]
        got = dict(download_pils(urls, workers=workers))   # url -> PIL (successes)
        pils = [got[r["img"]] for r in block if r["img"] in got]
        rows = [r for r in block if r["img"] in got]
        dropped += len(block) - len(rows)
        if pils:
            emb_parts.append(embed_images(pils))
            kept_rows.extend(rows)
        if verbose:
            print(f"  {start + len(block):>6}/{len(meta)}  "
                  f"kept {len(kept_rows)}  dropped {dropped}", flush=True)

    if not kept_rows:
        raise SystemExit("No images survived download — aborting (index untouched).")

    emb = np.vstack(emb_parts).astype("float32")
    rows = [dict(r, i=k) for k, r in enumerate(kept_rows)]   # re-index, drop stale i

    # write fresh (overwrite, not append) so emb/meta stay aligned
    vi.dir.mkdir(parents=True, exist_ok=True)
    np.save(vi.emb_p, emb)
    with vi.meta_p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    state = vi.load_state({})
    state["count"] = len(rows)
    vi.stamp(state, emb.shape[1])          # backend=clip, model, dim, last_sync
    vi.save_state(state)
    if verbose:
        print(f"\n[reembed] done: {len(rows)} imgs @ dim {emb.shape[1]} "
              f"({dropped} dropped). model={state['model']}", flush=True)
    return len(rows), dropped


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="index dir name under kb/index/ (e.g. marblecollecting)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--chunk", type=int, default=256)
    args = ap.parse_args()
    reembed(args.name, workers=args.workers, chunk=args.chunk)


if __name__ == "__main__":
    main()
