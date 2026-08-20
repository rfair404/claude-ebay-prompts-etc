#!/usr/bin/env python3
"""Run every tests/test_*.py in-process and report a combined pass/fail.

No pytest dependency — this discovers the `test_*` functions in each module and
runs them with a tiny PASS/FAIL harness (so both the pytest-style suites and the
older gate test run uniformly). The CLIP-dependent gate test loads the model once
(cached after first download); skip it with --fast.

Usage:  python tests/run_all.py            # all suites
        python tests/run_all.py --fast     # skip the CLIP-loading suites
"""
import importlib
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAST = "--fast" in sys.argv
SLOW = {"test_marble_gate"}            # loads CLIP


def _needs_fixtures(fn) -> bool:
    """True for a pytest-style test that takes a fixture (monkeypatch, tmp_path).

    This runner calls tests with no arguments. Such a test raised TypeError and
    was reported as a FAILURE, which is a lie — the test is fine, this runner
    just cannot supply what it asks for. Two of them sat red in every run.
    """
    import inspect

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return any(p.default is inspect.Parameter.empty
               and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)
               for p in sig.parameters.values())


def _run_module(mod):
    fns = [v for k, v in sorted(vars(mod).items())
           if k.startswith("test_") and callable(v)]
    failed = skipped = 0
    for fn in fns:
        if _needs_fixtures(fn):
            skipped += 1
            print(f"SKIP  {fn.__name__} (needs a pytest fixture — run under pytest)")
            continue
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    tail = f", {skipped} skipped" if skipped else ""
    print(f"{len(fns) - failed - skipped}/{len(fns) - skipped} passed{tail}")
    return failed


def main():
    mods = sorted(p.stem for p in HERE.glob("test_*.py"))
    total_fail = 0
    for name in mods:
        if FAST and name in SLOW:
            print(f"\n==== {name} (SKIPPED --fast) ====")
            continue
        print(f"\n==== {name} ====")
        total_fail += _run_module(importlib.import_module(name))
    print(f"\n{'=' * 40}\n"
          f"{'ALL SUITES PASSED' if not total_fail else f'{total_fail} FAILURE(S)'}")
    return total_fail


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
