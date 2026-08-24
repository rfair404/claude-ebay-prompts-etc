#!/usr/bin/env python3
"""Run PREP over a queue of shoots — in-process and in parallel.

The obvious way to batch this is a shell loop calling the CLI per shoot, and it
is far slower than it looks: every invocation pays a fresh interpreter start and
reloads the u2net weights, and the whole queue runs on one core. On a 97-shoot
queue that is most of the wall clock spent on setup, single file.

This keeps one worker pool alive instead. Each worker imports the stack and
builds its segmentation session ONCE, then takes shoots off the queue, so the
model load is paid per worker rather than per shoot and the run uses the box.

Resumable by construction: `--check` skips a shoot that already has photos in
its manifest, `--apply` skips one that already has a preset adopted. A killed
run is restarted by running the same command again.

    python tools/prep_run.py --queue Q --check [--workers 6]
    python tools/prep_run.py --queue Q --apply
    python tools/prep_run.py --queue Q --status
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_queue(path: Path) -> list[Path]:
    return [Path(l.strip()) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _state(shoot: Path) -> dict:
    mf = shoot / ".prep" / "prep.json"
    if not mf.exists():
        return {}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _needs(shoot: Path, mode: str) -> bool:
    m = _state(shoot)
    if mode == "check":
        return not m.get("photos")
    return not m.get("chosen_preset")


# A manifest write refuses when someone else moved the file since it was read.
# In a pool that is expected rather than exceptional, so the worker re-reads and
# redoes the work instead of failing the shoot. Bounded, because a conflict that
# never clears is a bug and should surface as one.
MANIFEST_ATTEMPTS = 4
RETRY_BACKOFF = (0.4, 1.2, 3.0)      # seconds before attempts 2, 3 and 4


def _with_retry(fn, note_fn):
    """Run `fn`, re-running it from a fresh read if the manifest moved under us.

    Retrying is only correct because `run_check` and `run_apply` load the
    manifest themselves, so calling again IS the re-read — there is no stale
    state carried across an attempt. Anything that merged the two versions
    instead would be guessing which set of decisions the operator meant.

    The backoff is jittered so two workers that collide do not lock-step into
    colliding again on the same schedule.

    The attempt count is REPORTED, not swallowed. A retry that nobody sees would
    hide exactly the contention this was built to make visible — the point was
    never to make races quiet, only to make them survivable.
    """
    from lib.photo_prep import prep as P

    for attempt in range(1, MANIFEST_ATTEMPTS + 1):
        try:
            m = fn()
            note = note_fn(m)
            if attempt > 1:
                note += f"  [after {attempt} attempts — manifest contention]"
            return m, note
        except P.ManifestConflict:
            if attempt == MANIFEST_ATTEMPTS:
                raise
            pause = RETRY_BACKOFF[attempt - 1] * (0.5 + random.random())
            time.sleep(pause)


def _worker(job):
    """One shoot. Imports live at module scope in the worker after first call,
    so the model load is amortised across every shoot this worker handles."""
    shoot_s, mode, aspect, pad, pop = job
    shoot = Path(shoot_s)
    t0 = time.monotonic()
    try:
        sys.path.insert(0, str(ROOT))
        from lib.photo_prep import prep as P

        if mode == "check":
            def _ask(m):
                n = len(m.get("photos", {}))
                ask = sum(1 for r in m["photos"].values()
                          if r["orientation"]["needs_ask"])
                return f"{n} frames, {ask} ASK"
            m, note = _with_retry(
                lambda: P.run_check(shoot, aspect, pad, pop, quiet=True), _ask)
        else:
            m, note = _with_retry(
                lambda: P.run_apply(shoot, quiet=True),
                lambda m: f"{len(m.get('photos', {}))} frames, "
                          f"preset {m.get('chosen_preset')}")
        return dict(shoot=shoot_s, ok=True, note=note, secs=round(time.monotonic() - t0, 1))
    except Exception as e:                                    # noqa: BLE001
        return dict(shoot=shoot_s, ok=False,
                    note=f"{type(e).__name__}: {e}"[:180],
                    tb=traceback.format_exc()[-600:],
                    secs=round(time.monotonic() - t0, 1))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--aspect", default="1:1")
    ap.add_argument("--pad", type=float, default=0.12)
    ap.add_argument("--pop", default="gentle")
    ap.add_argument("--force", action="store_true", help="redo shoots already done")
    args = ap.parse_args()

    shoots = _load_queue(Path(args.queue))

    if args.status:
        done_c = sum(1 for s in shoots if _state(s).get("photos"))
        done_a = sum(1 for s in shoots if _state(s).get("chosen_preset"))
        ask = sum(sum(1 for r in _state(s).get("photos", {}).values()
                      if r["orientation"]["needs_ask"]) for s in shoots)
        pushed = sum(1 for s in shoots if _state(s).get("pushed_at"))
        print(f"{len(shoots)} shoots · checked {done_c} · rendered {done_a} · "
              f"awaiting orientation {ask} · pushed {pushed}")
        return 0

    mode = "check" if args.check else "apply" if args.apply else None
    if not mode:
        ap.error("pass --check, --apply or --status")

    todo = shoots if args.force else [s for s in shoots if _needs(s, mode)]
    print(f"{mode}: {len(todo)} of {len(shoots)} shoots to do "
          f"({args.workers} workers, {os.cpu_count()} cores)")
    if not todo:
        return 0

    jobs = [(str(s), mode, args.aspect, args.pad, args.pop) for s in todo]
    done = fail = 0
    t0 = time.monotonic()
    # 'spawn' is the only start method on Windows; maxtasksperchild keeps a
    # leaked ONNX session from growing across a long queue.
    with mp.get_context("spawn").Pool(processes=args.workers, maxtasksperchild=8) as pool:
        for r in pool.imap_unordered(_worker, jobs):
            done += 1
            if not r["ok"]:
                fail += 1
            flag = "ok  " if r["ok"] else "FAIL"
            print(f"[{done:3}/{len(jobs)}] {flag} {r['shoot'][:52]:54} "
                  f"{r['secs']:6.1f}s  {r['note'][:60]}")
            sys.stdout.flush()

    el = time.monotonic() - t0
    print(f"\n{mode} done: {done - fail} ok, {fail} failed, {el/60:.1f} min "
          f"({el/max(done,1):.1f}s per shoot)")
    return 1 if fail else 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
