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
