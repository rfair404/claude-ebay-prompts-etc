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

Stores (GH #156). Every command that touches an eBay account or per-store
data takes `--store NAME` itself (lib/stores.add_store_args). The dispatcher
also accepts it BEFORE the command, and adds a loop:

    python -m lib.cli --store junk reconcile      # == reconcile --store junk
    python -m lib.cli --all-stores pick-list --poll   # once per store

The flag is forwarded as a real `--store` argument, never smuggled through
$EBAYBIZ_STORE, so it shows in the tool's own argv and in scrollback. A
store-neutral command (voice, prep, ...) refuses a store rather than
silently ignoring it; `--all-stores` is refused on commands that bulk-write
to eBay, where "every store at once" should never be one keystroke.
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
                     "orders awaiting shipment -> pick sheet; --poll drops each new "
                     "sheet in the sold item's inventory folder and hands back a link "
                     "per shipment (#151), --record-tracking writes a tracking # back "
                     "(#32)"),
    "policy-sweep": ("tools.policy_sweep",
                     "survey/repair the return+fulfillment policy on every offer"),
    "price-audit":  ("tools.price_audit",
                     "live listings still asking above their own comp evidence"),
    "comps-board":  ("tools.comps_board",
                     "comp JSON -> the thumbnail board (_shared.md hard rule)"),
    "sales-report": ("tools.sales_report",
                     "sales / fees / promotion dashboard"),
    "dashboard":    ("tools.dashboard",
                     "backlog by stage, drafts awaiting review, live/ledger drift (#31 Phase 1)"),
    "serve":        ("webapp.server",
                     "local web app: live dashboard + secret-free job queue, 127.0.0.1 only (#31 Phase 2)"),
    "report":       ("lib.source_report",
                     "cross-directory bucket ROI — report --by-source [--html] (#56)"),
    "context":      ("lib.context_write",
                     "write kind:/spend:/spend_unit:/acquired: into a bucket's "
                     "context.txt, preserving prose (#118)"),
    "promote":      ("tools.promote",
                     "paid-placement planner — proposes; every write needs --confirm"),
    "voice":        ("lib.voice_check",
                     "in-hand voice linter (draft or --audit tree) — GH #40"),
    "listing":      ("lib.list_edit",
                     "LIST/EDIT: --validate --status --review --sync --publish ..."),
    "observe":      ("tools.session_observer",
                     "session transcripts -> friction report (#36, read-only)"),
    "prep":         ("lib.photo_prep.prep",
                     "PREP photo pipeline: --auto --check --apply --approve ..."),
    "single-pass":  ("lib.single_pass",
                     "gate-check IDENTIFY->PREP->PRICE->INVESTIGATE->DRAFT; one card when clean"),
    "status":       ("lib.status",
                     "one-shot shoot state: phase files, PREP gate, frames, ledger, next action (#61)"),
    "ship-quote":   ("tools.ship_quote",
                     "EasyPost shipping-rate quotes (#80) — free, no confirm needed"),
    "ship-buy":     ("tools.ship_buy",
                     "buy a label via EasyPost (#80) — DRY RUN unless --confirm"),
    "probe":        ("tools.probe",
                     "per-image PIL metadata + subject bbox/coverage, read-only (#74 item 4)"),
}


# Commands that take --store (GH #156). Everything else is store-neutral:
# it reads photos, text or comps and never an account or a per-store file.
STORE_AWARE = {
    "reconcile", "live-audit", "pick-list", "policy-sweep", "price-audit",
    "sales-report", "dashboard", "report", "promote", "listing", "status",
    "ship-quote", "ship-buy",
}
# Store-aware, but refuse --all-stores: each run can bulk-write to eBay or
# spend money, so the store has to be typed, once, by name.
SINGLE_STORE_ONLY = {"policy-sweep", "listing", "promote", "ship-buy", "ship-quote"}


def _pop_store_flags(args: list[str]) -> tuple[str | None, bool, list[str]]:
    """Strip leading `--store NAME` / `--store=NAME` / `--all-stores`."""
    store, all_stores = None, False
    while args:
        a = args[0]
        if a == "--all-stores":
            all_stores, args = True, args[1:]
        elif a == "--store" and len(args) > 1:
            store, args = args[1], args[2:]
        elif a.startswith("--store="):
            store, args = a.split("=", 1)[1], args[1:]
        else:
            break
    return store, all_stores, args


def _run(name: str, rest: list[str]) -> int:
    sys.argv = [f"ebz {name}"] + rest
    try:
        runpy.run_module(COMMANDS[name][0], run_name="__main__")
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    store, all_stores, args = _pop_store_flags(args)
    if not args or args[0] in ("help", "-h", "--help"):
        width = max(len(n) for n in COMMANDS)
        print("ebz — python -m lib.cli [--store NAME | --all-stores] <command> [args...]")
        for name, (_, desc) in COMMANDS.items():
            tag = "  [store]" if name in STORE_AWARE else ""
            print(f"  {name:<{width}}  {desc}{tag}")
        return 0
    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f"ebz: unknown command {name!r} — one of: {', '.join(COMMANDS)}")
        return 2
    if store is None and not all_stores:
        return _run(name, rest)

    if name not in STORE_AWARE:
        print(f"ebz: {name!r} is store-neutral — it takes no --store/--all-stores")
        return 2
    if "--store" in rest or any(a.startswith("--store=") for a in rest):
        print("ebz: give --store once — before the command or after it, not both")
        return 2
    if store is not None and all_stores:
        print("ebz: --store and --all-stores are mutually exclusive")
        return 2
    if store is not None:
        return _run(name, rest + ["--store", store])

    if name in SINGLE_STORE_ONLY:
        print(f"ebz: {name!r} writes to eBay per store — name one with --store, "
              f"not --all-stores")
        return 2
    from stores import configured_stores
    worst = 0
    for s in configured_stores():
        print(f"\n===== ebz {name} --store {s} =====", flush=True)
        worst = max(worst, _run(name, rest + ["--store", s]))
    return worst


if __name__ == "__main__":
    sys.exit(main())
