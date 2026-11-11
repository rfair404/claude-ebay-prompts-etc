"""tests/test_amcrest.py — tools/live/amcrest.py: the camera login file, the
RTSP URL, the OBS source settings, and that the login never reaches git or
obsctl's printed output. No camera or OBS needed."""
import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "live"))

import amcrest  # noqa: E402
import obsctl  # noqa: E402


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "ebaybiz" / "amcrest.json"
    monkeypatch.setenv("AMCREST_CONFIG", str(path))
    for env in amcrest.ENV.values():
        monkeypatch.delenv(env, raising=False)
    return path


def test_defaults_without_a_file(cfg_file):
    cfg = amcrest.load()
    assert cfg["host"] == "192.168.1.232" and cfg["user"] == "admin"
    assert "password" not in cfg
    with pytest.raises(obsctl.ObsError, match="setup"):
        amcrest.rtsp_url(cfg)


def test_url_main_and_sub_with_encoded_password(cfg_file):
    cfg = {**amcrest.DEFAULTS, "password": "p@ss:w/rd#1"}
    assert amcrest.rtsp_url(cfg) == (
        "rtsp://admin:p%40ss%3Aw%2Frd%231@192.168.1.232:554/cam/realmonitor?channel=1&subtype=0")
    assert amcrest.rtsp_url(cfg, subtype=1).endswith("channel=1&subtype=1")
    assert "p%40ss" not in amcrest.rtsp_url(cfg, redact=True)
    with pytest.raises(obsctl.ObsError):
        amcrest.rtsp_url(cfg, subtype=2)


def test_file_then_env_override_host(cfg_file, monkeypatch):
    amcrest.save({"host": "192.168.1.50", "password": "pw"})
    assert amcrest.load()["host"] == "192.168.1.50"
    monkeypatch.setenv("AMCREST_HOST", "cam.lan")
    assert "@cam.lan:554/" in amcrest.rtsp_url(amcrest.load())


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX file modes")
def test_save_is_owner_only(cfg_file):
    path = amcrest.save({"password": "pw"})
    assert path == cfg_file
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"password": "pw"}


def test_source_settings_low_latency_tcp_reconnect():
    s = amcrest.source_settings("rtsp://x")
    assert s["is_local_file"] is False and s["input"] == "rtsp://x"
    assert "rtsp_transport=tcp" in s["ffmpeg_options"]
    assert s["hw_decode"] is True and s["reconnect_delay_sec"] > 0


class FakeClient:
    def __init__(self, scenes=(), inputs=None):
        self.scenes, self.inputs, self.calls = list(scenes), dict(inputs or {}), []

    def call(self, req, data=None):
        self.calls.append((req, data))
        if req == "GetSceneList":
            return {"scenes": [{"sceneName": s} for s in self.scenes]}
        if req == "GetInputList":
            return {"inputs": [{"inputName": n, "inputKind": k} for n, k in self.inputs.items()]}
        return {}


def test_add_source_creates_own_scene_then_updates():
    c = FakeClient(scenes=["Product"])
    assert amcrest.add_source(c, "rtsp://u:p@h/x") == "added"
    reqs = [r for r, _ in c.calls]
    assert ("CreateScene", {"sceneName": "Amcrest camera"}) in c.calls
    create = next(d for r, d in c.calls if r == "CreateInput")
    assert create["sceneName"] == "Amcrest camera" and create["inputKind"] == "ffmpeg_source"
    assert "SetCurrentProgramScene" not in reqs  # never changes what is on air

    c = FakeClient(scenes=["Amcrest camera"], inputs={"Amcrest": "ffmpeg_source"})
    assert amcrest.add_source(c, "rtsp://u:p@h/x") == "updated"
    assert [r for r, _ in c.calls if r.startswith("Create")] == []


def test_add_source_refuses_a_same_named_other_kind():
    c = FakeClient(scenes=["Amcrest camera"], inputs={"Amcrest": "v4l2_input"})
    with pytest.raises(obsctl.ObsError, match="v4l2_input"):
        amcrest.add_source(c, "rtsp://u:p@h/x")


def test_obsctl_redacts_url_logins():
    out = obsctl.redact({"inputSettings": {"input": "rtsp://admin:s3cr%40t@192.168.1.232:554/cam"}})
    assert out["inputSettings"]["input"] == "rtsp://admin:<redacted>@192.168.1.232:554/cam"
    assert obsctl.redact({"local_file": "/home/x/a.mp4"}) == {"local_file": "/home/x/a.mp4"}


def test_tracked_scene_template_has_no_url_login():
    for f in (ROOT / "tools/live/obs").rglob("*.json"):
        assert not obsctl.URL_LOGIN.search(f.read_text()), f
