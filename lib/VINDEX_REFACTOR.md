# Visual-index refactor — staged plan & status

Consolidating the three phash-based visual indexes onto one **CLIP** core
(`lib/vindex.py`). Goal: one embedding space, one model load, shared store, so a
single marble photo can fan across all three corpora (labeled ID → peer threads →
priced comps).

## Status

- [x] **Stage 1 — CLIP-only core + migrate `marble_index.py`.**
  - `lib/vindex.py`: CLIP-only embedding (`embed_images`, `embed_texts`, singleton
    model), http helpers (`http_get`, `download_pil`, `download_pils`, `nap`), and
    the `VIndex` store class (emb.npy + meta.jsonl + state.json, model/dim guard).
  - `lib/marble_index.py`: phash path + `MARBLE_EMBED` switch removed; index/query
    now CLIP-only via `vindex`.
  - **Transitional:** `ebay_visual.py` + `mcsa_index.py` still import the phash
    featurizer; it's quarantined in `lib/_phash_legacy.py` and re-exported from
    `marble_index` so those two keep running **unchanged** until Stage 2. Their
    OWN index/query is still phash until then — do not mix a phash index with a
    CLIP one in a unified query yet.
- [~] **Stage 2 — NEARLY DONE.** Done: `lib/reembed.py`; `mcsa_index.py` +
  `ebay_visual.py` both migrated to the CLIP core; `_phash_legacy.py` DELETED +
  re-exports removed from `marble_index.py` (nothing imports `_feat_phash` now).
  Reembedded to CLIP: `marblecollecting` (315, 0 drop), `ebay-sold` (1463, 0
  drop — verified: query returns sensible priced comps). VC++ installed →
  torch 2.12.1+cpu. **Remaining:** `marbleconnection` forum reembed (30,949) is
  RUNNING in background (.scratch/forum_reembed.log, ~1.5-2hr, 0 drops so far) —
  until it finishes, `marble_index query` fails the model guard by design.
  Then: `git add` the new files (`vindex`, `reembed`, `mcsa_index`,
  `marble_classify`, `marble_crop` still UNTRACKED); requirements.txt
  (sentence-transformers, torch, opencv-python-headless).
- [ ] **Stage 3 — text query, label prior, unified cross-index query.**

## Classifier (`lib/marble_classify.py`, new, UNTRACKED) — measured 2026-06-24

Hierarchical calibrated classifier over the MCSA CLIP embeddings: ensemble of
prototype + kNN heads (text head proven useless — 7% top-1 solo, default OFF) +
a **balanced 4-way family head** + risk-coverage abstention.
Leave-one-out on 315 MCSA labels (clean studio crops):
  - maker/type top-1 **49.5%**, top-3 **71.4%**
  - FAMILY (handmade/machine/transitional/nonglass) **81.6% balanced**
    (handmade 82 / machine 84 / nonglass 70 / transitional 71)
  - risk-coverage: most-confident 10% → 84%, 30% → 73%, 100% → 49.5%
**CRITICAL real-world caveat:** numbers are on CLEAN single-marble crops. On the
user's towel/caliper/multi-marble phone photos the family call goes WRONG (CLIP
embeds the whole frame). Proven live: cluttered group-10 shot → "nonglass";
tight Conqueror crop → correct "machine" + Vitro in candidates.
**Levers to reach "near-perfect, fast on real photos", in priority order:**
  1. [DONE] AUTO-CROP — `lib/marble_crop.py` (opencv-python-headless 4.13).
     Hough-gradient-alt ∪ saturation/brightness contour blobs, downscaled to
     short=700 for speed, white-masks the background, rejects paper labels +
     shadows via an interior-texture gate. Wired into the classifier as
     `marble_classify classify --detect IMG...`. PROVEN: green swirl flipped
     TRANSITIONAL(wrong, full photo) → MACHINE + WV-swirl candidates (right,
     cropped). Clean tight crops on separated/contrasting marbles.
     LIMITATIONS: touching marbles under-detect (Hough shares edges, blobs
     merge — needs watershed split, or just space them out); very pale marbles
     on the light towel under-detect (needs the dark-felt reshoot already on the
     lot-plan checklist). Both are largely photo-technique, not algorithm.
  2. Stronger embeddings: bump `vindex.MODEL_NAME` to `clip-ViT-L-14`, reembed
     MCSA, re-eval (lifts maker top-1; ~1.7GB dl, slower per embed).
  3. Forum weak labels: reembed the 30k forum to CLIP + mine thread titles
     ("Akro? .64") → ~30x more (noisy) training labels; lifts all heads.
  (Optional detector upgrade: watershed/distance-transform split for touching
   marbles; a small YOLO/SAM circle detector if classical proves too brittle.)

**Validation finding (2026-06-24):** the blue Vitro Conqueror query ranked
Vitro Agate only #4 in a tight 0.819–0.846 band (Marble King #1–2). CLIP ViT-B/32
gives a high-similarity *candidate cluster* but weak maker discrimination on
machine-made look-alikes — consistent with the "candidate-finder, not an ID"
contract. If we want sharper maker votes, bump `vindex.MODEL_NAME` to
`clip-ViT-L-14` and reembed (Stage 3 eval).

## Reconciliation with `.scratch/CLIP_UPGRADE_RUNBOOK.md`

That older runbook plans the same CLIP destination and agrees on: shared
embedder, backend-tagged state with a wrong-backend guard, MCSA as the prize,
commit-code-only. Two things it does BETTER than the original plan here — adopted
below:
  - **Migrate by re-embedding stored URLs, NOT re-crawling.** Every `meta.jsonl`
    row already carries the image `img` URL, so `lib/reembed.py` re-downloads +
    re-embeds with no HTML crawl. Use this to migrate the existing pHash indexes.
  - **Benchmark + acceptance test** (see Stage 2 step 4 / Stage-1 migration note).
One deliberate DIVERGENCE: the runbook keeps pHash as a switchable
`MARBLE_EMBED=phash|clip` backend; per the user's "ditch phash" instruction this
refactor is **CLIP-only** (pHash quarantined, no env switch). The runbook's
"make embedders backend-switchable" step is superseded by `vindex.embed_images`.

## Migrating the existing pHash indexes (the rebuild)

There are already pHash indexes on disk (forum: 30,949 imgs / 2,443 threads;
plus ebay-sold + marblecollecting). They fail the model guard → must be
re-embedded under CLIP. Do NOT re-crawl — write `lib/reembed.py`:
  - Read `<index>/meta.jsonl` (rows have `img` URLs).
  - `vindex.download_pils(urls, workers=16)` — S3/eBay/MCSA tolerate concurrency.
  - `vindex.embed_images(...)`; write a FRESH `emb.npy` (not `VIndex.append`'s
    vstack) aligned to meta; **drop 404'd rows and rewrite `meta.jsonl`** so
    emb/meta/state.count stay consistent; `IDX.stamp(state, dim)`.
  - CLI: `python lib/reembed.py <index-dir> [--workers 16]`.
  - Benchmark on MCSA (315 imgs) first → img/sec → forum estimate. Sequence:
    Phase 1 = MCSA + ebay-sold (~1,800, ~30–45 min); Phase 2 = forum (30k, ~1–2.5
    hr, background, CPU-bound).
  - **Acceptance test:** `mcsa query .scratch/marble-ids/t44662/m1.jpg m2 m4
    --top 8` must rank **Vitro Agate** top (the blue Conqueror's real maker) —
    it was noise (~0.7 Akro/Sulphide/Master) under pHash.

## Stage 2 — what's left (do this next)

1. Rewrite `lib/ebay_visual.py` to use `vindex` (drop `from marble_index import
   _feat_phash, _download_pil, _nap, IMG_DELAY`). Its crawl/ingest + `{price,
   url, soldDate, condition}` meta stay; embedding + store + query move to the
   core.
2. Rewrite `lib/mcsa_index.py` the same way (it currently imports `_feat_phash`).
   Keep its ID-guide crawler + `{maker, page}` meta and the maker-vote query.
   Then **`git add` it** — it's still untracked.
3. Write `lib/reembed.py` (above) and migrate all three indexes under CLIP.
4. Delete `lib/_phash_legacy.py` and the transitional re-exports from
   `marble_index.py` once nothing imports them. Grep `_feat_phash` to confirm zero
   hits before deleting.
5. Update `kb/README.md`: drop the `MARBLE_EMBED` phash/clip backend note
   (§"Embedding backend"), and the ebay_visual "phash today" line (~L208). Add a
   `requirements.txt` (none exists) pinning `sentence-transformers` + `torch`, and
   note the MS VC++ redist as a hard dep now, not optional.
6. Optional cleanup flagged in the design: the per-index dedup set
   (`indexed_img_urls` / `img_urls`) is a full-list rewrite on every crawl (O(N)).
   Move dedup into the meta inside `VIndex` while touching these files.

## Stage 3 — what's left (the payoff)

1. **Text query** — CLI like `mcsa query --text "Christensen Agate Guinea"` using
   `vindex.embed_texts`; returns labeled reference photos with no image. Add to
   `marble_index`/`ebay_visual` too (`--text` alongside image args).
2. **Zero-shot label prior** — embed the `LABELS` vocabulary
   (`mcsa_index.py:LABELS`) as text once; score a query photo against it as a
   cheap maker prior that complements the photo-NN maker vote in MCSA's query.
3. **Unified cross-index query** — one entry point (e.g. `lib/marble_lookup.py`
   or a `vindex query-all`) that embeds the marble ONCE and queries all three
   `VIndex`es, returning three aligned panels:
   - MCSA labeled match → *what is it* (maker/type, with the label vote)
   - forum threads → *who else asked* (peer second opinion)
   - eBay sold → *what it's worth* (priced comps: min/median/max)
   Sound only because Stage 1+2 put all three in the same CLIP space/dim — the
   `VIndex` model guard enforces it. Maps onto the IDENTIFY → PRICE pipeline.
4. Re-check the `specializations/marbles.md` honesty framing still holds: CLIP
   retrieval (and text/label scoring) is a *candidate finder*, never an ID —
   method ≠ origin ≠ era. Update the "● CLIP index" claims in `kb/README.md` once
   all three are genuinely CLIP.
