#!/usr/bin/env python3
"""Visual library of eBay SOLD listings — a priced visual comp finder.

Given a reference photo, return the most visually-similar *sold* eBay listings
together with their realized prices. Complements lib/marble_index.py (forum
photos, no prices) with priced comps, and **reuses its pHash featurizer** so the
two libraries behave identically (same embedding method, separate indexes).

Why a separate ingester: eBay sold data comes from the Apify actor
`automation-lab/ebay-sold-scraper`, which is an **MCP tool the assistant runs**,
not something Python can call. So the flow is:

  1) assistant runs the scraper for some pattern queries (the same actor PRICE
     uses), and saves the dataset items to a JSON file, e.g.:
         [{ "itemId","title","soldPrice","soldPriceString","thumbnail",
            "url","soldDate","condition" }, ...]
  2) `python lib/ebay_visual.py add --from results1.json results2.json`
         -> download each listing's image, embed, append (dedup by itemId)
  3) `python lib/ebay_visual.py query <image-or-url> --top N`
         -> top-K visually-similar SOLD listings, each with price + link
  4) `python lib/ebay_visual.py status`

Index: kb/index/ebay-sold/ (gitignored, regenerable).
  meta.jsonl  one row per listing image {i,img,itemId,url,title,price,...}
  emb.npy     float32 [N,D] L2-normalised features, row-aligned to meta
  state.json  dedup sets + last_sync_utc + backend

Honesty contract (same as marble_index): similarity finds *candidates*, it does
NOT identify maker/era or guarantee a comp is truly comparable. Treat the
top-K as priced look-alikes to vet — condition, size, and authenticity still
decide whether a sold listing is a valid comp (see prompts/price.md).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse marble_index's tooling WITHOUT editing it (import is read-only/safe even
# while a marble crawl is running in another process).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from marble_index import _feat_phash, _download_pil, _nap, IMG_DELAY  # noqa: E402

INDEX_DIR = Path(__file__).resolve().parent.parent / "kb" / "index" / "ebay-sold"
EMBED_BATCH = 16


def _hires(url: str) -> str:
    """Bump an eBay thumbnail (…/s-l140.webp) to a larger but still-light size."""
    return re.sub(r"s-l\d+\.", "s-l500.", url) if url else url


# --- storage (same layout as marble_index, own dir) --------------------------
def _state_path():
    return INDEX_DIR / "state.json"


def load_state():
    p = _state_path()
    if p.exists():
        return json.loads(p.read_text())
    return {"indexed_item_ids": [], "indexed_img_urls": [],
            "count": 0, "last_sync_utc": None, "backend": "phash",
            "queries": []}


def save_state(state):
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, indent=2))


def append_index(rows, embeddings):
    import numpy as np
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    emb_p, meta_p = INDEX_DIR / "emb.npy", INDEX_DIR / "meta.jsonl"
    if emb_p.exists():
        embeddings = np.vstack([np.load(emb_p), embeddings]).astype("float32")
    np.save(emb_p, embeddings.astype("float32"))
    with meta_p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_index():
    import numpy as np
    emb_p, meta_p = INDEX_DIR / "emb.npy", INDEX_DIR / "meta.jsonl"
    if not emb_p.exists() or not meta_p.exists():
        raise SystemExit("Index is empty — run `add` first.")
    emb = np.load(emb_p)
    meta = [json.loads(l) for l in meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return emb, meta


def _load_items(paths):
    """Read Apify sold-listing items from one or more JSON files.

    Accepts a bare list, an Apify MCP dump ({items}), or — preferred — the
    standard saved-run JSON from lib/apify_ebay.py ({raw_items}). raw_items are
    full raw records (sellerName, thumbnail, soldPrice…), so a comp pull feeds
    the visual library directly with no field loss.
    """
    items = []
    for f in paths:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        if isinstance(data, list):
            items.extend(data)
        else:
            items.extend(data.get("raw_items") or data.get("items") or [])
    return items


# --- commands ----------------------------------------------------------------
def cmd_add(args):
    import numpy as np
    state = load_state()
    seen_items = set(state["indexed_item_ids"])
    seen_imgs = set(state["indexed_img_urls"])
    items = _load_items(args.from_)
    print(f"loaded {len(items)} listing rows from {len(args.from_)} file(s)", flush=True)

    pil_batch, batch_meta, added, skipped = [], [], 0, 0

    def flush():
        nonlocal added
        if not pil_batch:
            return
        emb = np.vstack([_feat_phash(p) for p in pil_batch]).astype("float32")
        base = state["count"]
        rows = [dict(m, i=base + k) for k, m in enumerate(batch_meta)]
        append_index(rows, emb)
        state["count"] += len(rows)
        added += len(rows)
        pil_batch.clear()
        batch_meta.clear()

    for it in items:
        iid = str(it.get("itemId") or it.get("url") or "")
        if not iid or iid in seen_items:
            continue
        seen_items.add(iid)
        url = _hires(it.get("thumbnail") or it.get("image") or "")
        if not url or url in seen_imgs:
            skipped += 1
            continue
        try:
            pil_batch.append(_download_pil(url))
        except Exception as e:
            print(f"  ! img skip {iid}: {e}", flush=True)
            skipped += 1
            continue
        seen_imgs.add(url)
        batch_meta.append({
            "img": url, "itemId": iid, "url": it.get("url"),
            "title": it.get("title"),
            "price": it.get("soldPrice"),
            "priceStr": it.get("soldPriceString"),
            "soldDate": it.get("soldDate"),
            "condition": it.get("condition"),
            "query": it.get("query") or args.label,
        })
        _nap(IMG_DELAY)
        if len(pil_batch) >= EMBED_BATCH:
            flush()
    flush()

    state["indexed_item_ids"] = sorted(seen_items)
    state["indexed_img_urls"] = sorted(seen_imgs)
    if args.label and args.label not in state["queries"]:
        state["queries"].append(args.label)
    state["last_sync_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    print(f"\n+{added} listings ({skipped} skipped). Index holds {state['count']} "
          f"sold listings.", flush=True)


def cmd_query(args):
    import numpy as np
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
    q = np.vstack([_feat_phash(p) for p in pil]).astype("float32")
    score = (emb @ q.T).max(axis=1)
    # best image per listing
    best = {}
    for i, m in enumerate(meta):
        iid = m["itemId"]
        if iid not in best or score[i] > best[iid][0]:
            best[iid] = (float(score[i]), m)
    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)[: args.top]
    prices = [m["price"] for _, m in ranked if isinstance(m.get("price"), (int, float))]
    print(f"\nTop {len(ranked)} visually-similar SOLD listings "
          f"(of {len(best)} listings / {len(meta)} images):\n")
    for rank, (sc, m) in enumerate(ranked, 1):
        pr = m.get("priceStr") or (f"${m['price']}" if m.get("price") is not None else "?")
        print(f"{rank:>2}. {sc:.3f}  {pr:>9}  {(m.get('title') or '')[:60]}")
        print(f"     {m.get('url')}  [{m.get('condition')}, sold {m.get('soldDate')}]")
    if prices:
        prices.sort()
        med = prices[len(prices) // 2]
        print(f"\nprice signal among top matches: n={len(prices)}  "
              f"min ${min(prices):.2f}  median ${med:.2f}  max ${max(prices):.2f}")
        print("(candidates to VET — confirm condition/size/authenticity per price.md)")
    if args.json:
        out = [{"rank": r, "score": round(sc, 4), **m} for r, (sc, m) in enumerate(ranked, 1)]
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.json}")


def cmd_status(args):
    state = load_state()
    print(json.dumps({
        "listings": state["count"],
        "distinct_item_ids": len(state["indexed_item_ids"]),
        "queries_ingested": state["queries"],
        "backend": state["backend"],
        "last_sync_utc": state["last_sync_utc"],
        "index_dir": str(INDEX_DIR),
    }, indent=2))


def main():
    # eBay titles carry emoji/unicode; the Windows console is cp1252 by default.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="ingest Apify ebay-sold dataset JSON file(s)")
    p.add_argument("--from", dest="from_", nargs="+", required=True,
                   help="JSON file(s) of Apify ebay-sold items")
    p.add_argument("--label", default=None,
                   help="optional pattern label to tag these listings with")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("query", help="top-K similar SOLD listings for reference image(s)")
    p.add_argument("images", nargs="+", help="local path(s) or URL(s)")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", help="also write ranked results to this JSON file")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("status", help="index size + ingested queries")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
