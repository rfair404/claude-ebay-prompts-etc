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


# --- PTZ (CGI calls recorded, no camera) ------------------------------------

CAPS_841B = "caps.Pan=true\r\ncaps.Tile=true\r\ncaps.Zoom=false\r\ncaps.PresetMin=1\r\ncaps.PresetMax=255\r\n"


@pytest.fixture
def cam(monkeypatch):
    calls = []

    def fake_cgi(cfg, script, params, timeout=5):
        calls.append((script, dict(params)))
        if params.get("action") == "getCurrentProtocolCaps":
            return CAPS_841B
        if params.get("action") == "getDeviceType":
            return "type=IP2M-841B\r\n"
        return "OK"
    monkeypatch.setattr(amcrest, "cgi", fake_cgi)
    return calls


CFG = {**amcrest.DEFAULTS, "password": "pw"}


def test_move_is_a_timed_nudge_that_always_stops(cam):
    waited = []
    amcrest.ptz_move(CFG, "left", secs=0.3, speed=2, sleep=waited.append)
    assert waited == [0.3]
    assert [(p["action"], p["code"], p["arg2"]) for _, p in cam] == [("start", "Left", 2), ("stop", "Left", 0)]
    assert all(s == "ptz.cgi" and p["channel"] == 0 for s, p in cam)

    cam.clear()
    def boom(_):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        amcrest.ptz_move(CFG, "up", sleep=boom)
    assert [p["action"] for _, p in cam] == ["start", "stop"]


def test_move_rejects_bad_input_without_calling_the_camera(cam):
    for kw in ({"direction": "sideways"}, {"direction": "left", "speed": 9}, {"direction": "left", "secs": 10}):
        with pytest.raises(obsctl.ObsError):
            amcrest.ptz_move(CFG, **kw)
    assert cam == []


def test_zoom_refused_on_fixed_lens(cam):
    with pytest.raises(obsctl.ObsError, match="no optical zoom"):
        amcrest.ptz_move(CFG, "in")
    assert [p["action"] for _, p in cam] == ["getCurrentProtocolCaps"]


def test_presets_and_info(cam):
    amcrest.ptz_preset(CFG, "goto", 2)
    amcrest.ptz_preset(CFG, "save", 3)
    assert [(p["code"], p["arg2"]) for _, p in cam] == [("GotoPreset", 2), ("SetPreset", 3)]
    with pytest.raises(obsctl.ObsError):
        amcrest.ptz_preset(CFG, "goto", 0)
    assert amcrest.ptz_info(CFG) == "IP2M-841B: pan yes, tilt yes, zoom no, presets 1-255"


def test_cgi_401_says_rerun_setup_and_hides_password(monkeypatch):
    import urllib.error

    class Opener:
        def open(self, url, timeout):
            assert "pw" not in url
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(amcrest, "_opener", lambda cfg: Opener())
    with pytest.raises(obsctl.ObsError, match="setup --password") as e:
        amcrest.cgi(CFG, "ptz.cgi", {"action": "stop"})
    assert "pw" not in str(e.value).replace("--password", "")


def test_dock_serves_page_and_needs_its_header(cam):
    import threading
    import urllib.error
    import urllib.request
    srv = amcrest.ThreadingHTTPServer(("127.0.0.1", 0), amcrest.dock_handler(CFG))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "X-Amcrest-Dock" in page and "pw" not in page

        def post(body, header=True):
            req = urllib.request.Request(base + "/ptz", json.dumps(body).encode(),
                                         {"X-Amcrest-Dock": "1"} if header else {})
            try:
                r = urllib.request.urlopen(req)
            except urllib.error.HTTPError as e:
                return e.code, e.read()
            return r.status, json.loads(r.read())
        assert post({"cmd": "goto", "n": 1}) == (200, {"ok": True, "msg": "goto 1"})
        assert post({"cmd": "goto", "n": 1}, header=False)[0] == 403
        code, out = post({"cmd": "in"})
        assert code == 400 and "no optical zoom" in json.loads(out)["msg"]
    finally:
        srv.shutdown()
        srv.server_close()
