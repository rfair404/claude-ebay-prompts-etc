"""webapp/jobs.py — the Phase-2 job-type whitelist (#31,
docs/webapp-architecture.md's "What" list for Phase 2): exactly the two
secret-free pipeline pieces the doc names for this phase —

  * PREP's `--auto` pass (orientation + crop, no operator judgement)
  * PRICE's tier math over an already-saved comp JSON

Nothing in this module imports `lib/ebay_client.py` or `lib/apify_ebay.py`,
and nothing here writes outside `inventory/` or shells out beyond `ebz`
itself. Publish, offers, policy sweep, and Apify comp pulls all stay
chat-only until Phase 3's secrets story lands (see the module-mapping table
in the architecture doc) — adding a job type here is exactly the line where
that boundary would be crossed, so review any addition against that table
first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import price_stats as _price_stats     # noqa: E402
from tools.dashboard import INVENTORY  # noqa: E402

# Kept under the Bash tool's 600s hard ceiling on purpose — #74 item 3 (the
# 590-640s timeout-ceiling audit) is exactly the failure mode this avoids:
# a killed prep run should die with time to spare and report it as an error,
# not vanish at the same wall the tool itself would have hit.
PREP_AUTO_TIMEOUT = 590


def _resolve_shoot_dir(name: str) -> Path:
    """A shoot is a bare directory name directly under inventory/. Reject
    anything that looks like a path — this is the one thing standing
    between an HTTP request body and reading/writing outside inventory/."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"invalid shoot name: {name!r}")
    inventory = INVENTORY.resolve()
    shoot_dir = (inventory / name).resolve()
    if shoot_dir.parent != inventory or not shoot_dir.is_dir():
        raise ValueError(f"unknown shoot: {name!r}")
    return shoot_dir


def _resolve_under_inventory(path_str: str) -> Path:
    """Comp JSON paths must resolve inside inventory/ — same reasoning as
    `_resolve_shoot_dir`, for a job type that takes a file path instead of
    a bare name."""
    p = Path(path_str)
    p = (REPO / p).resolve() if not p.is_absolute() else p.resolve()
    inventory = INVENTORY.resolve()
    if p != inventory and inventory not in p.parents:
        raise ValueError(f"path must be under inventory/: {path_str!r}")
    if not p.is_file():
        raise ValueError(f"file not found: {path_str!r}")
    return p


def prep_auto(params: dict) -> dict:
    """PREP's `--auto` pass — no network, no credentials, no operator
    judgement call. Shells to the same `ebz prep` a human runs
    (docs/webapp-architecture.md's "Job runner" recommendation: "a
    subprocess per job, shelling out through lib/cli.py exactly as a human
    would"), so this wrapper never re-implements PREP's own gating."""
    shoot_dir = _resolve_shoot_dir(params.get("shoot", ""))
    proc = subprocess.run(
        [sys.executable, "-m", "lib.cli", "prep", str(shoot_dir), "--auto"],
        cwd=REPO, capture_output=True, text=True, timeout=PREP_AUTO_TIMEOUT,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def price_stats(params: dict) -> dict:
    """PRICE's tier math over already-saved comp JSON — a pure, stdlib-only
    function (`lib/price_stats.py`'s `price_from_runs`), called in-process
    rather than shelled out: it needs no subprocess isolation and no CLI
    wraps it today."""
    kwargs: dict = {}
    if params.get("best_match_json"):
        kwargs["best_match_json"] = _resolve_under_inventory(params["best_match_json"])
    if params.get("price_high_json"):
        kwargs["price_high_json"] = _resolve_under_inventory(params["price_high_json"])
    for key in ("unit_type", "condition", "price_field"):
        if params.get(key) is not None:
            kwargs[key] = params[key]
    if params.get("require_tokens"):
        kwargs["require_tokens"] = list(params["require_tokens"])
    return _price_stats.price_from_runs(**kwargs)


JOB_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "prep-auto": prep_auto,
    "price-stats": price_stats,
}
