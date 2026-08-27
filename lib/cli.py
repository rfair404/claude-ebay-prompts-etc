"""ebz — the one entry point for the ops tools (V4_PLAN Phase 3, #30).

    python -m lib.cli                      # list the commands
    python -m lib.cli <command> [args...]  # run one

Every command dispatches to the module that owns it with argv passed
through untouched, so `python -m lib.cli reconcile --apply` is exactly
`python tools/ledger_reconcile.py --apply`. The dispatcher pins the repo
root and lib/ onto sys.path once, which is the whole "shared bootstrap" —
config and credentials keep loading lazily inside the tools themselves.

Adding a command is one registry line; the module just has to be runnable
as a script (a __main__ guard or top-level CLI both work — dispatch is
runpy, not an import contract).
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

#            command          module                    one-line purpose
COMMANDS = {
    "reconcile":    ("tools.ledger_reconcile",
                     "reconcile listings_ledger.csv against the Sell API — eBay wins"),
    "live-audit":   ("tools.live_audit",
                     "reconcile local drafts + ledger against live eBay state (--apply)"),
    "pick-list":    ("tools.pick_list",
                     "orders awaiting shipment -> what to pull, where it goes, by when"),
    "policy-sweep": ("tools.policy_sweep",
                     "survey/repair the return+fulfillment policy on every offer"),
    "price-audit":  ("tools.price_audit",
                     "live listings still asking above their own comp evidence"),
    "sales-report": ("tools.sales_report",
                     "sales / fees / promotion dashboard"),
    "promote":      ("tools.promote",
                     "paid-placement planner — proposes; every write needs --confirm"),
    "voice":        ("lib.voice_check",
                     "in-hand voice linter (draft or --audit tree) — GH #40"),
    "listing":      ("lib.list_edit",
                     "LIST/EDIT: --validate --status --review --sync --publish ..."),
    "prep":         ("lib.photo_prep.prep",
                     "PREP photo pipeline: --auto --check --apply --approve ..."),
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("help", "-h", "--help"):
        width = max(len(n) for n in COMMANDS)
        print("ebz — python -m lib.cli <command> [args...]")
        for name, (_, desc) in COMMANDS.items():
            print(f"  {name:<{width}}  {desc}")
        return 0
    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f"ebz: unknown command {name!r} — one of: {', '.join(COMMANDS)}")
        return 2
    mod = COMMANDS[name][0]
    sys.argv = [f"ebz {name}"] + rest
    runpy.run_module(mod, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
