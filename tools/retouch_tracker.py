#!/usr/bin/env python3
"""Resumable tracker for the retroactive photo re-touch.

One JSON file is the single source of truth for what has been done, so an
interrupted run can be restarted without redoing work or double-pushing to
eBay. Every state change is written to disk immediately — a crash mid-shoot
leaves that shoot marked `in_progress`, which is deliberately distinguishable
from both `pending` and `done` so the operator can see where it stopped.

States:
  pending      queued, untouched
  in_progress  started; if seen at startup, the previous run died here
  done         re-rendered, approved and pushed; carries before/after numbers
  skipped      deliberately not processed (with a reason)
  failed       attempted and errored (with the error)
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path

TRACKER = Path("_retouch_tracker.json")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def load() -> dict:
    if TRACKER.exists():
        return json.loads(TRACKER.read_text(encoding="utf-8"))
    return {"created": _now(), "updated": _now(), "shoots": {}}

def save(d: dict) -> None:
    d["updated"] = _now()
    TRACKER.write_text(json.dumps(d, indent=1), encoding="utf-8")

def seed(shoots: list[dict]) -> dict:
    d = load()
    added = 0
    for s in shoots:
        k = s["shoot"]
        if k in d["shoots"]:
            continue
        d["shoots"][k] = {"state": "pending", "queued_at": _now(), **s}
        added += 1
    save(d)
    print(f"seeded {added} new shoot(s); tracker holds {len(d['shoots'])}")
    return d

def mark(shoot: str, state: str, **extra) -> None:
    d = load()
    rec = d["shoots"].setdefault(shoot, {"queued_at": _now()})
    rec["state"] = state
    rec[f"{state}_at"] = _now()
    rec.update(extra)
    save(d)

def pending(limit: int | None = None) -> list[str]:
    d = load()
    out = [k for k, v in d["shoots"].items() if v.get("state") in ("pending", "in_progress")]
    out.sort(key=lambda k: -(d["shoots"][k].get("dclip") or 0))
    return out[:limit] if limit else out

def summary() -> None:
    d = load()
    from collections import Counter
    c = Counter(v.get("state") for v in d["shoots"].values())
    print(f"tracker: {len(d['shoots'])} shoot(s) — " + ", ".join(f"{k}={n}" for k, n in sorted(c.items())))
    stuck = [k for k, v in d["shoots"].items() if v.get("state") == "in_progress"]
    if stuck:
        print("  IN_PROGRESS (previous run stopped here):")
        for k in stuck: print("   ", k)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "summary": summary()
    elif cmd == "pending":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else None
        for k in pending(n): print(k)
    elif cmd == "mark": mark(sys.argv[2], sys.argv[3])
    else: print(__doc__)
