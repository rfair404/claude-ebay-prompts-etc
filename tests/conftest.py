"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True)
def on_air_flag(tmp_path, monkeypatch):
    """Point the show-mode flag (show_flow.py writes it, bg_guard.py reads it)
    at a temp file, so a test that goes live with a fake OBS never marks the
    real show on air and blocks the next pytest run."""
    flag = tmp_path / "ON_AIR"
    monkeypatch.setenv("SHOW_ON_AIR_FLAG", str(flag))
    return flag


@pytest.fixture(autouse=True)
def empty_consignment_ledgers(tmp_path_factory, monkeypatch):
    """Point the consignment lookup (tools/consign.py, which lib/auto_promote
    asks before any ad) at an empty inventory/. No test reads Russell's real
    client ledgers, and a CI box or worktree without inventory/ doesn't make
    every promotion fail closed. A test that needs lots sets its own."""
    import sys
    from pathlib import Path
    tools = str(Path(__file__).resolve().parent.parent / "tools")
    if tools not in sys.path:
        sys.path.append(tools)
    import consign
    monkeypatch.setattr(consign, "INVENTORY", tmp_path_factory.mktemp("inventory"))
