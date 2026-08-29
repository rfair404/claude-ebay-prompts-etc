"""lib/read_cache.py — on-disk cache for repeated comp runs + eBay reads (V4_PLAN
Phase 4, #30).

## Why this exists

The session observer's first real read (PR #47) found PRICE is the cost
center: 61 asks but 2,134 turns and 2.7M output tokens across a week — more
than PREP + IDENTIFY + DRAFT combined. Most of that is round-trips through
Chrome to re-run a comp search that was already run, sometimes in the SAME
session, when a query gets re-checked or a broader ladder rung repeats work
a narrower one already covered. This module is the generic cache primitive;
`lib/ebay_sold_browse.py` is the one caller wired up so far (its comp-ingest
path — see that module for the `--fresh` flag and the cache-hit/miss output).

## What is NOT cached here, on purpose

Only read-only, idempotent GETs whose staleness costs a wasted round trip,
never a wrong outcome, belong behind this cache. `lib/ebay_client.py`'s
reads (fulfillment/payment/return policies, live inventory/offers, category
aspects, condition policies) are deliberately left OUT: a stale policy id
or a stale live-offer state is exactly the failure class PR #50 fixed for
`sync_actuals` (stale `shoot_dir` silently mismatching real sales), and
`get_condition_names` in particular feeds condition disclosure, which the
V4 ground rules say no phase touches. Comp prices go stale in a way that is
visible (a bad price gets caught at the REVIEW gate); a stale policy id or a
stale "is this still live" read can silently misinform a decision with real
money behind it. When in doubt, the answer here is: don't cache it.

## Key granularity — query + UTC calendar date

A cache entry is valid for the rest of the UTC day it was written on. Comps
are a same-day snapshot already (PRICE reads "sold TODAY through the last
N days", not a live price), so re-serving one from earlier today changes
nothing about the distribution; a session that crosses UTC midnight just
sees a miss and repopulates, which costs one extra live round trip, not a
wrong price. Coarser (weekly) keying would risk serving Monday's thin
cohort into Friday's PRICE call for no real savings — comp runs are cheap
to repopulate but expensive to accidentally under-count.

## On-disk layout

    <cache_dir>/<namespace>/<key>.json      key = sha256(namespace|date|parts)[:24]

`READ_CACHE_DIR` overrides `<repo-root>/.cache` (same env-var-override
pattern as `comps_core.default_runs_dir` / `COMPS_RUNS_DIR`). The directory
is gitignored (`.cache/`, alongside `.scratch/`) — every entry is
regenerable from a live query, never source.

A malformed or unreadable cache file is always treated as a miss, never
raised — a corrupt cache must never break a live read that would otherwise
succeed.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def default_cache_dir() -> Path:
    """<repo-root>/.cache, overridable with READ_CACHE_DIR (tests use this)."""
    env = os.environ.get("READ_CACHE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / ".cache"


def today_utc() -> str:
    """UTC calendar date — see the module docstring for why UTC, not local."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def cache_key(namespace: str, *parts: Any, date: Optional[str] = None) -> str:
    """Stable key for (namespace, date, *parts). Same inputs -> same key;
    a different query, condition, or day -> a different one."""
    date = date or today_utc()
    raw = "\x1f".join([namespace, date, *(str(p) for p in parts)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _path_for(namespace: str, key: str, cache_dir: Optional[Path]) -> Path:
    base = cache_dir or default_cache_dir()
    return base / namespace / f"{key}.json"


@dataclass
class CacheResult:
    hit: bool
    value: Any = None
    path: Optional[Path] = None


def get(namespace: str, *parts: Any, cache_dir: Optional[Path] = None,
        date: Optional[str] = None) -> CacheResult:
    """Look up one entry. Missing, corrupt, or malformed-shape files are all
    a miss — never raises."""
    key = cache_key(namespace, *parts, date=date)
    path = _path_for(namespace, key, cache_dir)
    if not path.exists():
        return CacheResult(False, path=path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return CacheResult(False, path=path)
    if not isinstance(payload, dict) or "value" not in payload:
        return CacheResult(False, path=path)
    return CacheResult(True, value=payload["value"], path=path)


def put(namespace: str, *parts: Any, value: Any, cache_dir: Optional[Path] = None,
        date: Optional[str] = None) -> Path:
    """Write one entry (full overwrite of that key). Returns the file path."""
    date = date or today_utc()
    key = cache_key(namespace, *parts, date=date)
    path = _path_for(namespace, key, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "namespace": namespace,
        "key_parts": [str(p) for p in parts],
        "date": date,
        "cached_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "value": value,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def cached_call(namespace: str, *parts: Any, fetch: Callable[[], Any],
                fresh: bool = False, cache_dir: Optional[Path] = None,
                date: Optional[str] = None) -> tuple[Any, bool]:
    """Run `fetch()` unless a same-day entry exists; returns (value, was_hit).

    `fresh=True` always calls fetch() and overwrites the cache with its
    result — the `--fresh` bypass every caller should expose.
    """
    if not fresh:
        found = get(namespace, *parts, cache_dir=cache_dir, date=date)
        if found.hit:
            return found.value, True
    value = fetch()
    put(namespace, *parts, value=value, cache_dir=cache_dir, date=date)
    return value, False
