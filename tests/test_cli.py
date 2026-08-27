"""The ebz dispatcher: every registered command resolves, and dispatch works.

No pytest fixtures — runs under tests/run_all.py too.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cli import COMMANDS  # noqa: E402


def test_every_registered_module_resolves():
    for name, (mod, desc) in COMMANDS.items():
        assert importlib.util.find_spec(mod) is not None, f"{name} -> {mod}"
        assert desc


def test_no_args_prints_the_command_table():
    r = subprocess.run([sys.executable, "-m", "lib.cli"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    for name in COMMANDS:
        assert name in r.stdout


def test_unknown_command_exits_2():
    r = subprocess.run([sys.executable, "-m", "lib.cli", "no-such-thing"],
                       cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "unknown command" in r.stdout


def test_argv_passes_through():
    # voice with no target prints usage and exits 2 — proof the argv reached it.
    r = subprocess.run([sys.executable, "-m", "lib.cli", "voice"],
                       cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "usage" in r.stdout.lower()
