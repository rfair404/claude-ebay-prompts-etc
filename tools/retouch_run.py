#!/usr/bin/env python3
"""Re-render + re-push one batch of shoots whose backdrop was crushed.

Resumable: every shoot is marked `in_progress` in the tracker BEFORE any work
starts and `done`/`failed`/`needs_orientation` after, each written straight to
disk. Re-running picks up exactly where it stopped.

The one hard rule: this never answers an orientation question. If prep cannot
read which way up a frame belongs, the shoot is parked as `needs_orientation`
and left for a human. A crushed background is cosmetic; a listing photo rotated
90 degrees is not, and guessing to keep a batch moving is how that happens.

  python tools/retouch_run.py --limit 10          # dry run: show the batch
  python tools/retouch_run.py --limit 10 --apply  # render, approve, push
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import retouch_tracker as T

REPO = Path(__file__).resolve().parents[1]

def run(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def clip_profile(shoot: Path) -> dict:
    """Worst pure-black clipping across the shoot's current listing/ renders."""
    pj = shoot / ".prep" / "prep.json"
    if not pj.exists(): return {}
    d = json.loads(pj.read_text(encoding="utf-8"))
    worst = {"dclip": 0.0, "frame": None}
    for src, meta in d.get("photos", {}).items():
        out = meta.get("output")
        if not out: continue
        op, sp = shoot / out, shoot / src
        if not (op.exists() and sp.exists()): continue
        a = np.asarray(Image.open(sp).convert("L"), dtype=np.float32)
        b = np.asarray(Image.open(op).convert("L"), dtype=np.float32)
        d0, d1 = float(np.mean(a <= 2) * 100), float(np.mean(b <= 2) * 100)
        if d1 - d0 > worst["dclip"]:
            worst = {"dclip": round(d1 - d0, 2), "frame": src,
                     "before": round(d0, 2), "after": round(d1, 2)}
    return worst

def process(shoot_s: str, apply: bool) -> tuple[str, dict]:
    shoot = REPO / shoot_s
    if not shoot.exists():
        return "failed", {"error": "shoot directory missing"}
    before = clip_profile(shoot)

    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s, "--apply", "--quiet"])
    if rc != 0:
        return "failed", {"error": out.strip()[-300:], "stage": "apply"}
    if "awaiting an orientation answer" in out or "ASK:" in out:
        asked = [l.strip() for l in out.splitlines() if l.strip().startswith("ASK:")]
        return "needs_orientation", {"ask": asked[:1], "note": "parked — a human must answer; never guessed"}

    for st in ("orientation", "crop", "color"):
        rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s, "--approve-stage", st, "--quiet"])
        if rc != 0:
            return "failed", {"error": out.strip()[-200:], "stage": f"approve:{st}"}
    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s, "--approve", "--quiet"])
    if rc != 0:
        return "failed", {"error": out.strip()[-200:], "stage": "approve"}

    after = clip_profile(shoot)
    if not apply:
        return "pending", {"before": before, "after": after, "dry_run": True}

    rc, out = run([sys.executable, "lib/list_edit.py", "--update", shoot_s,
                   "--fields", "photos", "--confirm"])
    if rc != 0:
        return "failed", {"error": out.strip()[-300:], "stage": "push"}
    return "done", {"before": before, "after": after, "push": out.strip()[-120:]}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    batch = T.pending(a.limit)
    if not batch:
        print("nothing pending"); T.summary(); return
    print(f"batch of {len(batch)} ({'APPLY' if a.apply else 'DRY RUN'}):\n")
    for s in batch:
        T.mark(s, "in_progress")
        state, info = process(s, a.apply)
        if state == "pending":
            T.mark(s, "pending", **info)
        else:
            T.mark(s, state, **info)
        b = (info.get("before") or {}).get("dclip")
        c = (info.get("after") or {}).get("dclip")
        extra = f"  clip {b}pp -> {c}pp" if b is not None and c is not None else ""
        print(f"  [{state:<17}] {s}{extra}")
        if info.get("error"): print(f"       {info['error'][:150]}")
        if info.get("ask"):   print(f"       {info['ask']}")
    print(); T.summary()

if __name__ == "__main__":
    main()
