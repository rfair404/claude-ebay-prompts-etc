"""
Test that all ledger writes go through ledger_store.

GH #411: Prevent direct CSV writes that bypass the centralized ledger writer.
Every ledger file write must go through lib/ledger_store.py's atomic_write().
"""

import os
import re
from pathlib import Path


def find_python_files(start_path="."):
    """Find all .py files in lib/ and tools/, exclude tests."""
    root = Path(start_path)
    for py_file in root.glob("**/*.py"):
        # Skip test files, __pycache__, and worktrees
        if "test_" in py_file.name or "__pycache__" in str(py_file):
            continue
        if ".claude/worktrees" in str(py_file):
            continue
        if py_file.parent.name in ("tests", "__pycache__"):
            continue
        # Only scan lib/ and tools/
        if "lib/" in str(py_file) or "tools/" in str(py_file):
            yield py_file


def check_for_direct_writes(file_path):
    """
    Scan a Python file for direct ledger writes.

    Flags patterns like:
    - open(...'ledgers/...'...'w')
    - df.to_csv(...'ledgers/...')
    - write(...) to ledger paths

    Returns list of (line_num, pattern) violations.
    """
    violations = []
    ledger_patterns = [
        r"['\"].*ledgers/.*['\"]",  # Any ledgers/ path
        r"['\"].*_ledger\.csv['\"]",  # *_ledger.csv files
        r"['\"].*clients_ledger.*['\"]",  # Consign ledgers
    ]

    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
    except (UnicodeDecodeError, IOError):
        return violations

    for i, line in enumerate(lines, 1):
        # Skip comments and empty lines
        if line.strip().startswith("#"):
            continue

        # Check for open(...'ledgers/...', 'w')
        if "open(" in line and "'w'" in line:
            for pattern in ledger_patterns:
                if re.search(pattern, line):
                    violations.append((i, "open(...'w') to ledger file"))
                    break

        # Check for .to_csv(...'ledgers/...')
        if ".to_csv(" in line:
            for pattern in ledger_patterns:
                if re.search(pattern, line):
                    violations.append((i, "df.to_csv() to ledger file"))
                    break

        # Check for write_csv or similar functions
        if re.search(r"write.*csv|csv.*write", line, re.IGNORECASE):
            for pattern in ledger_patterns:
                if re.search(pattern, line):
                    violations.append((i, "write_csv-like call to ledger"))
                    break

    return violations


def test_no_direct_ledger_writes():
    """CI gate: All ledger files must be written through ledger_store."""
    violations = {}

    for py_file in find_python_files():
        issues = check_for_direct_writes(py_file)
        if issues:
            violations[str(py_file)] = issues

    if violations:
        msg = "Direct ledger writes found outside ledger_store:\n"
        for file_path, issues in violations.items():
            msg += f"\n{file_path}:\n"
            for line_num, pattern in issues:
                msg += f"  Line {line_num}: {pattern}\n"
        msg += "\nAll ledger writes must go through lib/ledger_store.py's atomic_write().\n"
        msg += "See GH #411 and #401/#398/#408 for details."
        assert False, msg

    # Pass: no violations found
    assert True


def test_ledger_store_is_only_writer():
    """Verify that ledger_store.py is not circumvented by other modules."""
    ledger_store_path = Path("lib/ledger_store.py")

    # Check that ledger_store.py exists and has atomic_write
    assert ledger_store_path.exists(), f"ledger_store.py not found at {ledger_store_path}"

    with open(ledger_store_path, "r") as f:
        content = f.read()

    assert "def atomic_write(" in content, \
        "ledger_store.py must have atomic_write() method"
    assert "def to_csv(" in content, \
        "ledger_store.py must have to_csv() helper"


if __name__ == "__main__":
    test_no_direct_ledger_writes()
    test_ledger_store_is_only_writer()
    print("✓ All ledger writes are centralized through ledger_store")
