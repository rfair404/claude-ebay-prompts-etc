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

Per store (#156): a shoot's ledger row lives in the ledger of the store its
draft belongs to — the draft's own `store:` field (stores.draft_store, via
stores.draft_store_from_text), never the ambient store — so `status` needs no
--store: the draft already says. `--store` is accepted (ebz forwards it) and
only overrides that for a draft whose field is missing or wrong.
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
import stores  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# No module-level LEDGER: the file is stores.paths(<draft's store>).listings_ledger,
# resolved per shoot (#156).

_FRAME_EXT = {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".webp"}
_SKU_RE = re.compile(r'ebay_inventory_sku:\s*"?([0-9a-zA-Z\-]{6,})"?', re.M)


def _frame_count(shoot: Path) -> int:
    return sum(1 for p in shoot.iterdir() if p.is_file() and p.suffix.lower() in _FRAME_EXT)


def _draft_text(shoot: Path) -> str:
    draft = shoot / "draft.md"
    if not draft.exists():
        return ""
    return draft.read_text(encoding="utf-8", errors="ignore")


def _sku_from_draft(shoot: Path) -> str:
    m = _SKU_RE.search(_draft_text(shoot))
    return m.group(1) if m else ""


def _store_from_draft(shoot: Path) -> str:
    """The store this shoot's draft belongs to: its frontmatter `store:`
    (stores.draft_store_from_text — the one parser for it), "default" if absent.
    An invalid name is returned raw; it matches no ledger file."""
    return stores.draft_store_from_text(_draft_text(shoot))


def _ledger_row(sku: str, ledger_by_sku: Optional[dict] = None,
                store: str = stores.DEFAULT_STORE) -> Optional[dict]:
    if not sku:
        return None
    if ledger_by_sku is not None:
        return ledger_by_sku.get(sku)
    try:
        ledger = stores.paths(store).listings_ledger
    except ValueError:                        # not a valid store name
        return None
    if not ledger.exists():
        return None
    with ledger.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("sku") == sku:
                return row
    return None


def gather(shoot: Path, ledger_by_sku: Optional[dict] = None,
           store: Optional[str] = None,
           ledger_store: str = stores.DEFAULT_STORE) -> dict:
    """The whole state of one shoot dir, one read pass. Each stage's
    'pending' list is empty iff that stage is fully done — STAGE_CHECK
    itself is the single source of truth for that (unwritten file, an
    interactive gate's own HARD stop, everything).

    `ledger_by_sku` lets a caller looking at many shoots in one run (e.g.
    the dashboard's backlog view) preload `listings_ledger.csv` once —
    `{sku: row}` — instead of this function re-scanning the whole file per
    shoot. Omitted (the default), it reads the file itself exactly as
    before; every existing single-shoot caller is unaffected.

    Per store (#156): the ledger consulted is the one for the shoot's store —
    `store` if given, else the draft's own `store:` field. `ledger_by_sku`
    is `ledger_store`'s preload (the default store's, as it always was), so
    it is used only for shoots of that store; any other store's shoot reads
    its own ledger file instead of silently missing in the wrong map."""
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
    shoot_store = store or _store_from_draft(shoot)
    preload = ledger_by_sku if shoot_store == ledger_store else None
    ledger = _ledger_row(sku, preload, shoot_store)

    return {
        "shoot": str(shoot),
        "frames": _frame_count(shoot),
        "stages": stages,
        "sku": sku or None,
        "store": shoot_store,
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
    if not stores.is_default(state.get("store")):
        lines.append(f"  store {state['store']}")
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
    stores.add_store_args(ap, help_extra="Default here: the draft's own store: field.")
    a = ap.parse_args(argv)

    shoot = Path(a.shoot_dir)
    if not shoot.is_dir():
        ap.error(f"no such shoot directory: {shoot}")

    state = gather(shoot, store=stores.resolve_store_name(a.store) if a.store else None)
    print(json.dumps(state, indent=2) if a.json else summary(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
