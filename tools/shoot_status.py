#!/usr/bin/env python3
"""One-call status for a shoot: phase files, prep approval, ledger row, next action.

Replaces the ls/cat/grep chain a conductor used to run per item just to answer
"where is this shoot right now" (see RUN.md, "Concurrency and delegation").
Read-only; touches nothing.

    python -m lib.cli status <shoot-dir>
    python tools/shoot_status.py <shoot-dir> [--json]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

from draft_io import parse_draft                     # noqa: E402
from photo_prep.prep import find_images, load_manifest  # noqa: E402
from photo_prep.stages import STAGES, stage_state     # noqa: E402

PHASE_FILES = [
    ("identify", "identify.txt"),
    ("price", "price.txt"),
    ("investigate", "investigate.txt"),
    ("draft", "draft.md"),
    ("review", "review_card.md"),
]


def _ledger_row(sku: str) -> dict | None:
    if not sku:
        return None
    env = os.environ.get("EBAYBIZ_LISTINGS_LEDGER") or os.environ.get("EBAYBIZ_LISTINGS_LOG")
    path = Path(env) if env else ROOT / "listings_ledger.csv"
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("sku") == sku:
                return row
    return None


def _prep_state(shoot: Path) -> dict:
    m = load_manifest(shoot)
    st = stage_state(m)
    return {
        "frames": len(m.get("photos") or {}),
        "stages": {s: st[s]["approved"] for s in STAGES},
        "approved": bool(m.get("approved")),
    }


def _next_action(files: dict, prep: dict, ledger: dict | None) -> str:
    if not files["identify"]:
        return "run IDENTIFY"
    if not prep["approved"]:
        pending = next((s for s in STAGES if not prep["stages"][s]), None)
        if pending is None:
            return "run PREP --auto"
        return f"open PREP stage {pending}"
    if not files["price"]:
        return "run PRICE"
    if not files["investigate"]:
        return "run INVESTIGATE"
    if not files["draft"]:
        return "run DRAFT"
    if not files["review"]:
        return "run REVIEW"
    if ledger and ledger.get("status") == "PUBLISHED":
        return "done — live"
    return "awaiting REVIEW approval"


def status(shoot: Path) -> dict:
    files = {name: (shoot / fname).exists() for name, fname in PHASE_FILES}
    prep = _prep_state(shoot)
    sku = ""
    if files["draft"]:
        try:
            sku = str(parse_draft(shoot / "draft.md").get("meta.ebay_inventory_sku") or "")
        except Exception:
            sku = ""
    ledger = _ledger_row(sku)
    needs_review = shoot / "NEEDS_REVIEW.md"
    return {
        "shoot": shoot.name,
        "frame_count": len(find_images(shoot)),
        "files": files,
        "prep": prep,
        "sku": sku or None,
        "ledger": ledger,
        "needs_review_lines": needs_review.read_text(encoding="utf-8").count("\n")
        if needs_review.exists() else 0,
        "next_action": _next_action(files, prep, ledger),
    }


def render(s: dict) -> str:
    lines = [f"{s['shoot']}  ({s['frame_count']} frames)"]
    done = [name for name, ok in s["files"].items() if ok]
    lines.append(f"  files:  {', '.join(done) if done else 'none yet'}")
    prep = s["prep"]
    stages = ", ".join(f"{k}{'✓' if v else ''}" for k, v in prep["stages"].items())
    lines.append(f"  prep:   {stages}  (approved: {prep['approved']})")
    if s["sku"]:
        lg = s["ledger"]
        lines.append(f"  sku:    {s['sku']}  ledger: {lg.get('status') if lg else 'no row'}")
    if s["needs_review_lines"]:
        lines.append(f"  needs_review: {s['needs_review_lines']} entries")
    lines.append(f"  next:   {s['next_action']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shoot", help="shoot directory (e.g. inventory/sand-dollars)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    shoot = Path(args.shoot)
    if not shoot.is_dir():
        ap.error(f"not a directory: {shoot}")

    s = status(shoot)
    print(json.dumps(s, indent=2) if args.json else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
