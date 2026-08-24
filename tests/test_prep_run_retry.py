#!/usr/bin/env python3
"""The batch runner's response to a manifest conflict.

`save_manifest` refuses when another writer moved the file since it was read.
In a worker pool that is expected rather than exceptional — several workers,
plus whatever else is running against the same tree — so a conflict must not
fail the shoot. The worker re-reads and redoes the work.

Two properties matter as much as the retry itself, and both are tested here:
it is BOUNDED (a conflict that never clears is a bug, and must surface as one),
and it is REPORTED (a retry nobody sees hides exactly the contention that
compare-and-swap was built to make visible).

Run:  python tests/test_prep_run_retry.py
  or: pytest tests/test_prep_run_retry.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import prep_run as R                                         # noqa: E402
from lib.photo_prep import prep as P                         # noqa: E402


def _flaky(n_conflicts: int):
    """A call that raises ManifestConflict n times, then succeeds."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= n_conflicts:
            raise P.ManifestConflict("moved under us")
        return {"photos": {"a": {}, "b": {}}, "chosen_preset": "crisp"}
    return fn, calls


def _no_sleep(monkey=None):
    R.time.sleep = lambda _s: None


def test_a_conflict_is_retried_and_succeeds():
    _no_sleep()
    fn, calls = _flaky(1)
    m, note = R._with_retry(fn, lambda m: f"{len(m['photos'])} frames")
    assert calls["n"] == 2, calls
    assert m["chosen_preset"] == "crisp"
    assert "2 frames" in note


def test_the_retry_is_reported_not_swallowed():
    """Contention has to stay visible. Silently absorbing it would hide the
    races compare-and-swap exists to expose."""
    _no_sleep()
    fn, _ = _flaky(2)
    _m, note = R._with_retry(fn, lambda m: "done")
    assert "attempts" in note and "contention" in note, note


def test_a_clean_run_says_nothing_about_retries():
    _no_sleep()
    fn, calls = _flaky(0)
    _m, note = R._with_retry(fn, lambda m: "done")
    assert calls["n"] == 1
    assert "attempt" not in note, note


def test_a_conflict_that_never_clears_is_raised():
    """Bounded on purpose. Retrying forever turns a bug into a hang."""
    _no_sleep()
    fn, calls = _flaky(999)
    try:
        R._with_retry(fn, lambda m: "done")
        raise AssertionError("looped without bound")
    except P.ManifestConflict:
        pass
    assert calls["n"] == R.MANIFEST_ATTEMPTS, calls


def test_only_conflicts_are_retried():
    """A real bug must fail on the first attempt, not be tried four times and
    reported as contention."""
    _no_sleep()
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("a real bug")

    try:
        R._with_retry(boom, lambda m: "done")
        raise AssertionError("the error was swallowed")
    except ValueError:
        pass
    assert calls["n"] == 1, f"a non-conflict error was retried {calls['n']} times"


def test_the_backoff_table_covers_every_retry():
    """One pause per retry — an attempt with no entry would raise IndexError
    inside the handler, turning a survivable conflict into a crash."""
    assert len(R.RETRY_BACKOFF) >= R.MANIFEST_ATTEMPTS - 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                               # noqa: BLE001
            bad += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
