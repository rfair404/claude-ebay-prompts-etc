"""ebz status — one-shot shoot-directory state (#61/#62 concurrency prep).

Replaces the `ls`/`cat`/`grep` sequence an operator runs by hand to answer
"where is this item": which phase files exist, PREP's approval/pending
state, the frame count, the ledger row if it's been drafted, and the next
action. Measured at 2,638 such calls costing 13.4h across 114 sessions
(#61) — this is one call instead of six.

Read-only: never writes, never calls eBay. Reuses `lib/single_pass.py`'s
own STAGE_OUTPUT/STAGE_CHECK so a stage only has one definition of "done"
anywhere in the codebase.

    python -m lib.cli status <shoot-dir>
    python -m lib.cli status <shoot-dir> --json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from single_pass import STAGE_CHECK, STAGE_ORDER, STAGE_OUTPUT  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "listings_ledger.csv"

_FRAME_EXT = {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".webp"}
_SKU_RE = re.compile(r'ebay_inventory_sku:\s*"?([0-9a-zA-Z\-]{6,})"?', re.M)


def _frame_count(shoot: Path) -> int:
    return sum(1 for p in shoot.iterdir() if p.is_file() and p.suffix.lower() in _FRAME_EXT)


def _sku_from_draft(shoot: Path) -> str:
    draft = shoot / "draft.md"
    if not draft.exists():
        return ""
    m = _SKU_RE.search(draft.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else ""


def _ledger_row(sku: str) -> Optional[dict]:
    if not sku or not LEDGER.exists():
        return None
    with LEDGER.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("sku") == sku:
                return row
    return None


def gather(shoot: Path) -> dict:
    """The whole state of one shoot dir, one read pass. Each stage's
    'pending' list is empty iff that stage is fully done — STAGE_CHECK
    itself is the single source of truth for that (unwritten file, an
    interactive gate's own HARD stop, everything)."""
    stages: dict = {}
    next_action = None
    for stage in STAGE_ORDER:
        pending = [a.detail for a in STAGE_CHECK[stage](shoot)]
        out_file = shoot / STAGE_OUTPUT[stage]
        file_repr = None
        if out_file.exists():
            try:
                file_repr = str(out_file.resolve().relative_to(REPO))
            except ValueError:
                file_repr = str(out_file)
        stages[stage] = {"file": file_repr, "pending": pending}
        if next_action is None and pending:
            next_action = f"{stage}: {pending[0]}"

    sku = _sku_from_draft(shoot)
    ledger = _ledger_row(sku)

    return {
        "shoot": str(shoot),
        "frames": _frame_count(shoot),
        "stages": stages,
        "sku": sku or None,
        "ledger_status": ledger.get("status") if ledger else None,
        "listing_id": (ledger or {}).get("listing_id") or None,
        "next_action": next_action or "all stages clear — ready for REVIEW",
    }


def summary(state: dict) -> str:
    lines = [f"{state['shoot']}  ({state['frames']} frame(s))"]
    for stage in STAGE_ORDER:
        s = state["stages"][stage]
        if not s["file"]:
            mark = "·"          # not started
        elif s["pending"]:
            mark = "⚠"          # written, but blocked on something
        else:
            mark = "✓"
        lines.append(f"  {mark} {stage}")
    if state["sku"]:
        lines.append(f"  sku {state['sku']}  ledger={state['ledger_status'] or '(no row)'}"
                      + (f"  {state['listing_id']}" if state["listing_id"] else ""))
    lines.append(f"→ {state['next_action']}")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="status",
        description="One-shot shoot-directory state: phase files, PREP's "
                     "gate, frame count, ledger row, next action.")
    ap.add_argument("shoot_dir", help="the shoot directory (inventory/<name>)")
    ap.add_argument("--json", action="store_true",
                     help="machine-readable result instead of the summary")
    a = ap.parse_args(argv)

    shoot = Path(a.shoot_dir)
    if not shoot.is_dir():
        ap.error(f"no such shoot directory: {shoot}")

    state = gather(shoot)
    print(json.dumps(state, indent=2) if a.json else summary(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
