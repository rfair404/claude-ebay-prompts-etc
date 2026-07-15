#!/usr/bin/env python3
"""Production re-crop + re-embed of the WHOLE marbleconnection forum index.

Parallel (multiprocessing), resumable (per-topic checkpoint), and non-destructive:
writes a NEW index `marbleconnection_v2` while the live `marbleconnection` index
is untouched until you verify v2 and swap.

Per source image, in a worker:
  download → crop_forum (ruler-drop · detect/sharpest or whole-frame fallback ·
  is_marble gate · circle-mask) → embed the clean crop → PURITY GATE (drop
  hand/pile/blur/heavy-bg below --purity-min) → keep (meta row + crop embedding).

The crop's index embedding and its purity score come from the SAME single embed,
so purity verification is essentially free. Every batch re-emits an AUDIT SHEET of
the lowest-purity crops (red=dropped, green=kept) for a final human glance.

Smoke-test first:   python tools/reindex_full.py --limit 20
Full overnight run:  python tools/reindex_full.py            (resumes if restarted)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
import multiprocessing as mp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

# --- worker ------------------------------------------------------------------
_W = {}


def _init_worker(threads, scratch):
    import torch
    torch.set_num_threads(max(1, threads))
    import vindex
    import marble_crop
    import reindex_forum
    vindex.get_model()                       # load CLIP once per worker
    reindex_forum._purity_vecs()             # warm the text-vector caches
    marble_crop._gate_vecs()
    _W.update(vindex=vindex, rf=reindex_forum, scratch=Path(scratch))


def _thumb_bytes(pil, px=120):
    t = pil.copy()
    t.thumbnail((px, px))
    b = io.BytesIO()
    t.save(b, format="JPEG", quality=70)
    return b.getvalue()


def _process_topic(payload):
    """-> (tid, kept[(row, emb)], audit[(purity, passed, thumb|None, label)],
           counts, n_img). Robust: one bad image never kills the worker."""
    tid, rows, purity_min, audit_below = payload
    vindex = _W["vindex"]; rf = _W["rf"]; scratch = _W["scratch"]
    kept, audit, counts, n_img = [], [], defaultdict(int), 0
    for m in rows:
        url = m.get("img")
        try:
            orig = vindex.download_pil(url)
        except Exception:
            counts["download-fail"] += 1
            continue
        n_img += 1
        tmp = scratch / f"w_{tid}_{m.get('i', 0)}.jpg"
        try:
            orig.save(tmp, quality=92)
            cands, kind = rf.crop_candidates(str(tmp))   # sharpest-first, ungated
        except Exception:
            counts["crop-fail"] += 1
            continue
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass
        if not cands:                        # ruler frame
            counts[kind] += 1
            continue
        # embed candidates in sharpness order; take the first real marble
        crop = emb = None
        try:
            for cand in cands:
                e = vindex.embed_images([cand])[0].astype("float32")
                if rf.marble_ok_emb(e):
                    crop, emb = cand, e
                    break
        except Exception:
            counts["embed-fail"] += 1
            continue
        if emb is None:                      # no candidate was a marble
            counts["not-marble"] += 1
            continue
        purity = rf.purity_of_emb(emb)       # purity from the same embed
        thumb = _thumb_bytes(crop) if purity < audit_below else None
        label = f"t{tid}.{m.get('i', 0)} {kind}"
        if purity < purity_min:
            counts["impure"] += 1
            audit.append((float(purity), False, thumb, label))
            continue
        counts[kind] += 1
        row = dict(m)
        row["purity"] = round(float(purity), 4)
        row["crop_kind"] = kind
        kept.append((row, emb))
        audit.append((float(purity), True, thumb, label))
    return tid, kept, audit, dict(counts), n_img


# --- main process: accumulate, checkpoint, audit -----------------------------
def _emit_audit(entries, path, *, cols=6, cell=190):
    """entries = [(purity, passed, thumb_bytes, label)] -> sheet, lowest first."""
    from PIL import Image, ImageDraw
    items = sorted((e for e in entries if e[2] is not None), key=lambda e: e[0])
    if not items:
        return None
    pad = 5
    rows = (len(items) + cols - 1) // cols
    sh = Image.new("RGB", (cols * (cell + pad) + pad, rows * (cell + pad) + pad), (18, 18, 18))
    d = ImageDraw.Draw(sh)
    for i, (pu, ok, tb, label) in enumerate(items):
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), pad + r * (cell + pad)
        d.rectangle([x, y, x + cell, y + cell], fill=(60, 200, 90) if ok else (225, 65, 65))
        im = Image.open(io.BytesIO(tb)).convert("RGB")
        im.thumbnail((cell - 8, cell - 8))
        cc = Image.new("RGB", (cell - 8, cell - 8), (30, 30, 30))
        cc.paste(im, ((cell - 8 - im.width) // 2, (cell - 8 - im.height) // 2))
        sh.paste(cc, (x + 4, y + 4))
        d.text((x + 7, y + 6), f"{pu:+.3f}", fill=(255, 255, 0))
        d.text((x + 7, y + cell - 14), ("DROP " if not ok else "") + label, fill=(255, 255, 255))
    sh.save(path, quality=85)
    return path


def _flush(dst, emb_all, meta_all, done, stats):
    import numpy as np
    dst.dir.mkdir(parents=True, exist_ok=True)
    arr = np.stack(emb_all).astype("float32") if emb_all else np.zeros((0, 512), "float32")
    tmp_e = dst.emb_p.with_name("emb.tmp.npy")   # ends in .npy so np.save won't re-suffix
    np.save(tmp_e, arr)
    tmp_e.replace(dst.emb_p)
    tmp_m = dst.meta_p.with_name(dst.meta_p.name + ".tmp")
    with tmp_m.open("w", encoding="utf-8") as f:
        for r in meta_all:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp_m.replace(dst.meta_p)
    state = {"done_tids": sorted(done), "count": len(meta_all),
             "kept": len(meta_all), **stats}
    dst.stamp(state, arr.shape[1] if arr.size else 512)
    dst.save_state(state)


def _merge_shards(VIndex, dst_name, nshards):
    """Concatenate <dst>_s0.._s<N-1> into <dst> (shards own disjoint topics, so a
    plain concat is correct — emb rows stay aligned to meta rows)."""
    import numpy as np
    dst = VIndex(dst_name)
    embs, metas, done = [], [], set()
    for i in range(nshards):
        sh = VIndex(f"{dst_name}_s{i}")
        if not (sh.emb_p.exists() and sh.meta_p.exists()):
            print(f"  shard {i}: MISSING — skipped")
            continue
        e = np.load(sh.emb_p)
        m = [json.loads(l) for l in sh.meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
        st = sh.load_state({"done_tids": []})
        if e.shape[0] != len(m):
            raise SystemExit(f"shard {i} misaligned: {e.shape[0]} emb vs {len(m)} meta")
        embs.append(e); metas.extend(m); done |= set(st.get("done_tids", []))
        print(f"  shard {i}: {len(m)} crops / {len(st.get('done_tids', []))} topics")
    if not embs:
        raise SystemExit("no shards found to merge")
    allemb = np.vstack(embs).astype("float32")
    dst.dir.mkdir(parents=True, exist_ok=True)
    np.save(dst.emb_p, allemb)
    with dst.meta_p.open("w", encoding="utf-8") as f:
        for r in metas:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    state = {"done_tids": sorted(done), "count": len(metas)}
    dst.stamp(state, allemb.shape[1])
    dst.save_state(state)
    print(f"[merged] {len(metas)} crops from {nshards} shards -> {dst.dir} "
          f"({len(done)} topics)")


def main():
    from vindex import VIndex
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="marbleconnection")
    ap.add_argument("--dst", default="marbleconnection_v2")
    import os
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    ap.add_argument("--threads", type=int, default=2, help="torch threads PER worker")
    ap.add_argument("--purity-min", type=float, default=0.008)
    ap.add_argument("--audit-below", type=float, default=0.040,
                    help="keep an audit thumbnail for crops below this purity")
    ap.add_argument("--audit-keep", type=int, default=240)
    ap.add_argument("--flush", type=int, default=200, help="checkpoint every N topics")
    ap.add_argument("--limit", type=int, default=None, help="process at most N topics (smoke test)")
    ap.add_argument("--nshards", type=int, default=1,
                    help="run as 1 of N INDEPENDENT shard processes (no Pool — "
                         "each writes <dst>_s<shard>, robust + detachable). Merge after.")
    ap.add_argument("--shard", type=int, default=0, help="this shard's id (0..nshards-1)")
    ap.add_argument("--merge", action="store_true",
                    help="merge <dst>_s0..s<N-1> into <dst> and exit")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import numpy as np
    if args.merge:
        _merge_shards(VIndex, args.dst, args.nshards)
        return

    src = VIndex(args.src)
    dst_name = args.dst if args.nshards <= 1 else f"{args.dst}_s{args.shard}"
    dst = VIndex(dst_name)
    scratch = ROOT / ".scratch" / "reindex_full_tmp" / f"s{args.shard}"
    scratch.mkdir(parents=True, exist_ok=True)

    meta = [json.loads(l) for l in
            src.meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_tid = defaultdict(list)
    for m in meta:
        by_tid[m.get("tid")].append(m)
    topics = sorted((t for t in by_tid if t is not None), reverse=True)
    if args.nshards > 1:                      # this shard owns a disjoint slice
        topics = [t for i, t in enumerate(topics) if i % args.nshards == args.shard]

    # resume: reload this shard's accumulated index + done set
    state = dst.load_state({"done_tids": []})
    done = set(state.get("done_tids", []))
    emb_all, meta_all = [], []
    if dst.emb_p.exists() and dst.meta_p.exists() and done:
        emb_all = [row for row in np.load(dst.emb_p)]
        meta_all = [json.loads(l) for l in
                    dst.meta_p.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"[resume] shard {args.shard}: {len(done)} topics done, "
              f"{len(meta_all)} crops indexed", flush=True)

    todo = [t for t in topics if t not in done]
    if args.limit:
        todo = todo[:args.limit]
    payloads = [(t, by_tid[t], args.purity_min, args.audit_below) for t in todo]
    total = len(payloads)
    serial = args.workers <= 1 or args.nshards > 1
    print(f"[run] shard {args.shard}/{args.nshards} | {total} topics | "
          f"{'serial' if serial else f'{args.workers}x{args.threads} pool'} | "
          f"purity-min {args.purity_min:+.3f} | dst {dst.dir.name}", flush=True)
    if not total:
        print("nothing to do (all topics already done).")
        return

    audit_low, all_purity, counts = [], [], defaultdict(int)
    t0, done_now, pool = time.time(), 0, None
    if serial:
        _init_worker(args.threads, str(scratch))   # set up _W in THIS process
        results = (_process_topic(p) for p in payloads)
    else:
        pool = mp.Pool(args.workers, _init_worker, (args.threads, str(scratch)))
        results = pool.imap_unordered(_process_topic, payloads)
    try:
        for tid, kept, audit, c, n_img in results:
            for row, emb in kept:
                meta_all.append(row); emb_all.append(emb)
                all_purity.append(row["purity"])
            audit_low.extend(audit)
            for k, v in c.items():
                counts[k] += v
            done.add(tid); done_now += 1
            if done_now % args.flush == 0:
                audit_low = sorted((e for e in audit_low if e[2]), key=lambda e: e[0])[:args.audit_keep]
                _flush(dst, emb_all, meta_all, done, {"kinds": dict(counts)})
                _emit_audit(audit_low, dst.dir / "audit.jpg")
                rate = done_now / max(1e-9, time.time() - t0)
                eta_h = (total - done_now) / max(1e-9, rate) / 3600
                med = float(np.median(all_purity)) if all_purity else 0.0
                print(f"  [s{args.shard} {done_now}/{total}] {len(meta_all)} kept | "
                      f"{rate*60:.1f} t/min | ETA {eta_h:.1f}h | purity med {med:+.3f} | "
                      f"impure {counts.get('impure',0)}", flush=True)
    finally:
        if pool is not None:
            pool.close(); pool.join()

    audit_low = sorted((e for e in audit_low if e[2]), key=lambda e: e[0])[:args.audit_keep]
    _flush(dst, emb_all, meta_all, done, {"kinds": dict(counts)})
    _emit_audit(audit_low, dst.dir / "audit.jpg")
    print(f"\n[done] shard {args.shard}: {len(meta_all)} crops from {len(done)} topics "
          f"into {dst.dir}", flush=True)
    print(f"  kinds: {dict(counts)}")
    if args.nshards > 1:
        print(f"  When ALL shards finish: python tools/reindex_full.py --merge "
              f"--dst {args.dst} --nshards {args.nshards}")


if __name__ == "__main__":
    main()
