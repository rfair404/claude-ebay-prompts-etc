# PREP `--resume` / `--jobs N` — implementation plan (#74 item 3)

**Status: all four steps of "Suggested follow-up PR shape" (below) are
implemented.** Steps 1–3 — atomic `_save_bgr` writes, incremental per-frame
checkpointing, and `--resume` with the settings-hash/staleness check — landed
first, all in `run_apply()`. Step 4 (`--jobs N`) landed in a follow-up PR: a
`ProcessPoolExecutor`, one frame per worker task, sharing the exact same
per-frame render function (`_render_frame`) the serial path calls, default
`jobs=1` (unchanged behaviour). It deliberately does NOT set a non-1 default
— the real peak-RSS measurement this doc calls for below was never gathered,
so `--jobs` stays a pure opt-in: pick a number for your own machine. The rest
of this document is left as originally written; only this status line and
the follow-up section's step numbering reflect what has since shipped.

## The problem, precisely

`run_apply()` (`lib/photo_prep/prep.py:956`) is the expensive pass: for every
frame in the manifest it loads the full-res source, re-segments, plans/applies
the crop, then renders **every preset in `only`** (`colormod.correct`, ~25–64s
per frame per preset on a 12MP source — see the function's own docstring).
That is all done inside one `for name, rec in m["photos"].items():` loop
(lines 1001–1086), and the manifest (`m`) is only written back to disk **once**,
after the loop finishes (`save_manifest(shoot, m)` at line 1090).

So a shoot with enough frames × presets to exceed a 600s command timeout gets
killed mid-loop, and:

- every rendered JPEG already written by `_save_bgr` under
  `.prep/presets/<preset>/<name>.jpg` is orphaned — the manifest on disk
  still has no `presets` entry for those frames, so nothing downstream
  (`--pick`, the presets sheet, `--resume` under this plan) knows they exist;
- re-running `--apply` starts the frame loop from `m["photos"].items()`
  again, frame 1, and re-renders everything already done — the wasted
  compute is the whole cost of the bug, not just the wait.

`run_check()` (the OSD/segment/crop-plan pass) has the same *shape* of risk
(one loop, one `save_manifest` at the end, line 916) but is much cheaper per
frame — no preset rendering — so it is not the priority here. The plan below
is written for `run_apply`; `run_check` can adopt the same checkpointing
mechanism later for free if it turns out to matter.

## Design: the manifest already IS the progress record

`prep.json` already carries a per-frame `presets` dict (which looks were
rendered, their path + sha256 + colour report) and a `src_sha256` per frame
(both truncated to the first 16 hex chars, matching `_sha256()`'s and
`_manifest_fingerprint()`'s existing convention — not a full 64-hex
digest). That is most of a progress record already — the fix is not a new
file, it's (a) writing it incrementally instead of once at the end, and
(b) a staleness check so a resume can trust what it reads. Concretely:

### 1. Checkpoint after every frame, not after the loop

Move `save_manifest(shoot, m)` from "after the loop" to "after each frame's
`rec` is filled in," inside the existing `for` loop (or inside the per-frame
worker call once `--jobs` lands — see below). `save_manifest` already
implements compare-and-swap with tmp-and-rename (its own docstring explains
why: concurrent writers already exist in this codebase). Reuse that exact
mechanism; do not invent a second write path. No extra bookkeeping is
needed for the CAS check across repeated checkpoints in the same process:
`save_manifest` already re-stamps `m[READ_FINGERPRINT]` on the same dict
object after a successful write (`lib/photo_prep/prep.py`, the
`# Re-stamp` comment at the end of the function), so the *next*
checkpoint's CAS check is automatically against the fingerprint this
process itself just wrote. A follow-up implementation should rely on that
existing behavior rather than re-deriving the fingerprint itself.

Cost: N manifest writes instead of 1, on a JSON file that's KB-sized per
shoot — negligible next to a 25–64s render.

### 2. A settings fingerprint, so `--resume` knows a checkpoint is trustworthy

Add one new top-level manifest field, written at the *start* of `--apply`:

```json
"apply_run": {
  "settings_hash": "sha256 of (aspect, pad, pop, subject, category, sorted(only)), truncated to 16 hex chars — same convention as _sha256()/_manifest_fingerprint(), not a full 64-hex digest",
  "started_at": "...",
  "jobs": 4
}
```

`--resume` refuses to reuse a frame's existing `presets` entry unless:

- `apply_run.settings_hash` on disk matches a hash of *this* invocation's
  settings (a geometry/category/preset-set change already invalidates
  crop/colour sign-off elsewhere in this file — line ~2310 — resume must
  honor the same rule: different settings means the old renders don't
  answer the question this run is asking);
- the frame's recorded `src_sha256` still matches the source file on disk
  (source changed underneath — reshoot, replaced file — never trust a stale
  render);
- **every** preset name in `only` is present in that frame's `presets` dict
  (a resume that only rendered `crisp` before must not treat a now-wider
  `only` as satisfied);
- the recorded preset's `path` exists on disk and its `sha256` matches a
  fresh hash of that file (catches a partial/truncated write from the kill
  itself — see the atomic-write note below, which should make this
  redundant in practice, but a cheap re-hash is worth it as a second check
  given what a false "resumed" positive costs: a bad photo shipping).

A frame that fails any of those checks is treated as **not done** and
re-rendered — resume is conservative by construction; it skips work only
when it can prove the old work still answers the current question, and it is
just as fine to over-render as it is to under-answer here (behind the
existing PREP approval gate either way).

### 3. Make the per-preset write itself crash-safe

`_save_bgr` (line 130) writes straight to the target path. Change it to
write to a sibling tmp file and `os.replace()` into place — the same
tmp-and-rename shape `save_manifest` already uses for the same reason. This
is what makes "the file exists with the recorded hash" in check 2 above a
reliable signal instead of a maybe-truncated JPEG that happens to have the
right name.

### 4. CLI surface

```
--resume     skip frames that check 2 already proves are done for THIS
             invocation's settings; render the rest
--jobs N     render up to N frames concurrently (default 1 = today's
             exact serial behavior and frame order)
```

Both are opt-in; omitting either preserves current behavior exactly. `main()`
(line 2395, the `if args.apply:` branch) passes them through to
`run_apply(shoot, quiet=..., only=only, resume=args.resume, jobs=args.jobs)`.

Operational note for `prompts/prep.md` / `RUN.md` once this ships: a
backgrounded `prep --apply` that got killed by a timeout should be re-invoked
with `--resume`, not restarted plain — that's the whole point, and it's an
easy thing to forget under "just re-run it."

## Design: `--jobs N` — what's safely parallel and what isn't

**Unit of parallelism = one frame**, not one preset. The code already leans
on this: segmentation and crop are computed once per frame and shared across
every preset rendered for it (the function's docstring: "segmentation
dominates the runtime... offering three looks costs barely more than
offering one"). Splitting a single frame's presets across workers would
duplicate the expensive part and race two workers over the same crop
decision; splitting frames across workers duplicates nothing and each
frame's write targets (`.prep/presets/<preset>/<name>.jpg`) are already
disjoint from every other frame's.

- **Pool type: `concurrent.futures.ProcessPoolExecutor`, not threads.**
  Segmentation + colour correction are CPU-bound OpenCV/numpy work; threads
  fight the GIL for the parts that aren't inside a released-GIL numpy/cv2
  call, processes don't. This also matches how the rest of this codebase
  isolates per-shoot state (each worker gets its own imports, no shared
  mutable module state to reason about).
- **Worker function is a pure per-frame transform.** Given `(shoot, name,
  rec_snapshot, settings)` it does exactly what the current loop body does
  for one frame (load, segment, crop, render each preset in `only`, save
  files) and **returns** the updated `rec` dict rather than mutating shared
  state — processes can't share the `m` dict anyway, and this keeps the
  parent process as the single writer of `prep.json`, which is what the
  compare-and-swap protocol already assumes ("PREP is not run by one process
  at a time" is about *separate invocations*, not about workers inside one
  `--apply` fighting each other).
- **Parent process is the only thing that calls `save_manifest`.** It
  collects each worker's result via `as_completed`, folds it into
  `m["photos"][name]`, and checkpoints (item 1 above) — so `--jobs N` and
  the resume checkpoint are the same mechanism, just fed by a pool instead
  of a serial loop. A manifest conflict raised by a concurrent *external*
  session editing the same shoot mid-run should still raise and stop, exactly
  as it does today — `--jobs` does not change who else is allowed to touch
  this shoot while it runs, only how the internal work is scheduled.
- **Per-frame exception isolation.** Today an unhandled exception anywhere
  in the frame loop kills the whole `--apply`. With a pool, a worker's
  exception surfaces when the parent calls `.result()` on its future — catch
  it there, record `rec["status"] = "ERROR"` + a flag with the exception
  text, and keep processing the other futures. This is worth doing
  independent of `--jobs` (a single corrupt source file shouldn't currently
  be able to blow up a 40-frame batch either), but it becomes necessary once
  frames run concurrently: one bad frame must not orphan the workers already
  in flight for the others.
- **Ordering for `_presets_sheet`.** The sheet-builder wants `rows` in the
  manifest's frame order (`m["photos"]` insertion order), not completion
  order. Reassemble `rows` from `m["photos"].items()` after all futures
  (and all resumed/skipped frames) are folded in, rather than appending as
  each future completes.
- **`--jobs` default stays 1.** Serial, in-order, byte-for-byte today's
  behavior — `--jobs` is an opt-in speedup, not a new default that changes
  output ordering or timing characteristics nobody asked for.
- **Memory is the real ceiling, not CPU.** Each worker holds a full-res
  source decode plus N rendered variants at once; on a 12MP source that's not
  small. Document `--jobs` as "tune down on a memory-constrained box" rather
  than defaulting to `os.cpu_count()` — a reasonable starting default is
  something like `min(4, os.cpu_count() or 1)`, but this wants a real
  measurement (peak RSS at `--jobs 4` on a representative shoot) before
  picking a number, not a guess baked into the follow-up PR.

## What this plan deliberately does NOT do

- It does not add a second progress-tracking file alongside `prep.json` —
  the manifest already has the right shape (per-frame, keyed the same way
  everything else in this file keys frames), and a second file would just be
  a second place for the two to disagree.
- It does not change what `--resume` means for `run_check` (the OSD/rotation
  pass) — that pass is comparatively cheap and the 600s-kill problem is
  specifically an `--apply` problem. Worth revisiting if a very large batch
  ever makes `--check` itself timeout-prone.
- It does not touch the approval gate. Resumed or freshly-rendered, nothing
  in `listing/` changes and nothing ships without the same three-stage
  sign-off `prep.md` already requires — `--resume`/`--jobs` are pure
  performance/reliability changes to how the renders get produced, not to
  what gets approved.

## Suggested follow-up PR shape

1. ✅ Atomic `_save_bgr` write (small, low-risk, independently useful even
   without the rest).
2. ✅ Incremental checkpointing in `run_apply` (move `save_manifest` inside the
   loop) — makes a killed `--apply` at least leave a *consistent* partial
   manifest, before `--resume` exists to exploit it.
3. ✅ `--resume` flag + the settings-hash/staleness check.
4. ✅ `--jobs N` + `ProcessPoolExecutor`. The frame-render body was pulled out
   of `run_apply`'s loop into a standalone `_render_frame(shoot, name, rec,
   aspect, pad, smode, only)` — pure with respect to the manifest, returns the
   new `rec` plus the in-memory pixels the sheet wants — so the serial
   (`--jobs 1`) path and the pool path call *the same function*, never two
   implementations that could drift. A pool worker (`_apply_worker`) discards
   the returned pixels and sends back only the small `rec` dict, to keep the
   pool's IPC cheap; the parent reloads a pool-rendered frame's pixels off
   disk for the sheet afterwards, the same trick `--resume` already used for
   a skipped frame. `--resume` is checked before a frame is ever queued as a
   pool task, so a resumed frame never occupies a worker slot. Each
   completed future is folded into the manifest and checkpointed
   individually (item 2's guarantee, extended to the pool path), and a
   worker's exception is caught and turned into a per-frame `ERROR` status +
   flag rather than sinking every other frame's future already in flight.
   Default stays `jobs=1` — the memory measurement this doc originally asked
   for to justify a higher default was never gathered, so `--jobs` ships as a
   pure opt-in with no baked-in guess; see the follow-up PR's "Judgment
   calls" for the reasoning.

Each step is independently mergeable and independently useful — 1–2 alone
fix the "partial manifest is inconsistent with partial renders" half of the
bug even before `--resume` exists to take advantage of it, and 1–3 already
shipped as their own PR before 4 landed.
