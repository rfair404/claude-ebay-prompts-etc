"""tests/test_pager.py — the live show's pager: tools/live/show_flow.py's
state()/go()/refocus() and webapp/pager.py's /api/pager routes over them.

OBS is a fake that records scene switches, and the D5100 refocus is stubbed,
so nothing here needs OBS, the camera, or a stream."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools" / "live"))

import obsctl  # noqa: E402
import show_flow as SF  # noqa: E402
import webapp.pager as pager  # noqa: E402
import webapp.server as server  # noqa: E402


class FakeObs:
    def __init__(self, scene="Intro", live=False):
        self.scene, self.live = scene, live
        self.switches: list[str] = []
        self.closed = False
        self.window_shown = None  # Up Next window item's last enabled state
        self.capture = "62914622\r\nThe Fair Hand \u2014 Mozilla Firefox\r\nNavigator"

    def call(self, req, data=None):
        if req == "GetCurrentProgramScene":
            return {"currentProgramSceneName": self.scene}
        if req == "SetCurrentProgramScene":
            self.scene = data["sceneName"]
            self.switches.append(self.scene)
            return {}
        if req == "GetStreamStatus":
            return {"outputActive": self.live, "outputTimecode": "00:01:02.000"}
        if req == "GetSceneItemList":
            return {"sceneItems": [{"sourceName": SF.WINDOW, "sceneItemId": 7}]}
        if req == "SetSceneItemEnabled":
            assert data["sceneName"] == SF.UPNEXT and data["sceneItemId"] == 7
            self.window_shown = data["sceneItemEnabled"]
            return {}
        if req == "GetInputSettings":
            return {"inputSettings": {"capture_window": self.capture}}
        raise AssertionError(f"unexpected request {req}")

    def close(self):
        self.closed = True


@pytest.fixture
def obs(tmp_path, monkeypatch):
    fake = FakeObs()
    refocused = []

    def fake_refocus(c):
        refocused.append(c.scene)  # what was on air while the camera froze
        return True, 5.04

    monkeypatch.setattr(SF, "refocus_d5", fake_refocus)
    monkeypatch.setattr(SF, "feed_pids", lambda: [123])
    monkeypatch.setattr(SF, "LOCK", tmp_path / "show_flow.lock")
    monkeypatch.setattr(SF.time, "sleep", lambda s: None)
    fake.title = "The Fair Hand \u2014 Mozilla Firefox"
    monkeypatch.setattr(SF, "window_title", lambda c: fake.title)
    fake.refocused = refocused
    return fake


def test_pages_are_the_three_in_running_order():
    assert list(SF.PAGES) == ["intro", "item", "next"]
    assert SF.PAGES["item"] == SF.PRODUCT and SF.PAGES["next"] == SF.UPNEXT
    # the HTTP body schema names the same pages
    assert pager.PageRequest.model_fields["page"].annotation.__args__ == tuple(SF.PAGES)


def test_state_maps_scene_to_page(obs):
    obs.live = True
    assert SF.state(obs) == {"page": "intro", "scene": "Intro", "pages": ["intro", "item", "next"],
                             "live": True, "timecode": "00:01:02.000", "feed": True}
    obs.scene = "Desktop"  # switched by hand in OBS
    assert SF.state(obs)["page"] is None


def test_going_to_item_refocuses_behind_the_current_page(obs):
    obs.scene = "Up Next"
    r = SF.go(obs, "item")
    assert obs.refocused == ["Up Next"]
    assert obs.switches == ["eBay Main"]
    assert r == {"page": "item", "changed": True, "focused": True, "refocus_s": 5.0}


def test_going_to_intro_or_next_does_not_touch_the_camera(obs):
    obs.scene = "eBay Main"
    assert SF.go(obs, "next")["focused"] is None
    SF.go(obs, "intro")
    assert obs.switches == ["Up Next", "Intro"] and obs.refocused == []


def test_page_already_on_air_is_left_alone(obs):
    obs.scene = "eBay Main"
    assert SF.go(obs, "item")["changed"] is False
    assert obs.switches == [] and obs.refocused == []


def test_unknown_page_is_refused(obs):
    with pytest.raises(ValueError):
        SF.go(obs, "outro")


def test_refocus_on_item_hides_behind_up_next(obs):
    obs.scene = "eBay Main"
    assert SF.refocus(obs) == {"focused": True, "refocus_s": 5.0}
    assert obs.refocused == ["Up Next"]
    assert obs.switches == ["Up Next", "eBay Main"]


def test_refocus_off_the_item_page_stays_put(obs):
    SF.refocus(obs)
    assert obs.refocused == ["Intro"] and obs.switches == []


def test_second_caller_gets_busy_while_the_lock_is_held(obs, monkeypatch):
    monkeypatch.setattr(SF, "LOCK_WAIT", 0)
    with SF.exclusive():
        with pytest.raises(SF.Busy):
            SF.go(obs, "next")
    assert SF.go(obs, "next")["changed"] is True  # released


# --- /api/pager ------------------------------------------------------------
@pytest.fixture
def client(obs, monkeypatch):
    monkeypatch.setattr(SF, "connect", lambda: obs)
    with TestClient(server.app, base_url="http://127.0.0.1:8770") as c:
        yield c


def test_get_reports_what_is_on_air(client, obs):
    r = client.get("/api/pager")
    assert r.status_code == 200 and r.json()["page"] == "intro"
    assert obs.closed


def test_post_switches_and_returns_state_plus_switch(client, obs):
    obs.scene = "Up Next"
    r = client.post("/api/pager", json={"page": "item"})
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == "item" and body["switch"]["focused"] is True
    assert obs.refocused == ["Up Next"]


def test_post_refocus(client, obs):
    obs.scene = "eBay Main"
    r = client.post("/api/pager/refocus")
    assert r.status_code == 200 and r.json()["refocus"]["focused"] is True
    assert r.json()["page"] == "item"


def test_bad_page_is_422(client, obs):
    assert client.post("/api/pager", json={"page": "outro"}).status_code == 422
    assert obs.switches == []


def test_foreign_host_and_cross_origin_writes_are_refused(client, obs):
    assert client.get("/api/pager", headers={"host": "evil.example"}).status_code == 403
    r = client.post("/api/pager", json={"page": "next"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and obs.switches == []
    r = client.post("/api/pager", json={"page": "next"}, headers={"origin": "http://localhost:5173"})
    assert r.status_code == 200


def test_obs_down_is_503(client, monkeypatch):
    def down():
        raise obsctl.ObsError("can't reach obs-websocket")
    monkeypatch.setattr(SF, "connect", down)
    assert client.get("/api/pager").status_code == 503


def test_busy_is_409_and_camera_failure_is_502(client, obs, monkeypatch):
    monkeypatch.setattr(SF, "LOCK_WAIT", 0)
    with SF.exclusive():
        assert client.post("/api/pager", json={"page": "next"}).status_code == 409

    def stuck(c):
        raise obsctl.ObsError("D5100 feed didn't come back within 30s")
    monkeypatch.setattr(SF, "refocus_d5", stuck)
    r = client.post("/api/pager", json={"page": "item"})
    assert r.status_code == 502 and "D5100" in r.json()["detail"]
    assert obs.scene == "Intro"  # never cut to a dead camera


def test_refocus_fails_fast_when_the_camera_is_off(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SF.subprocess.CompletedProcess(cmd, 0, stdout="Model  Port\n------\n", stderr="")
    monkeypatch.setattr(SF, "feed_stop", lambda: None)
    monkeypatch.setattr(SF.subprocess, "run", run)
    monkeypatch.setattr(SF, "feed_start", lambda: pytest.fail("restarted the feed with no camera"))
    with pytest.raises(obsctl.ObsError, match="not found on USB"):
        SF.refocus_d5(FakeObs())
    assert calls == [["gphoto2", "--auto-detect"]]


def test_control_page_has_a_button_per_page_and_calls_the_api(client):
    r = client.get("/pager")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    for page in SF.PAGES:
        assert f'data-page="{page}"' in r.text
    assert "/api/pager/refocus" in r.text
    assert client.get("/pager", headers={"host": "evil.example"}).status_code == 403


# --- the Up Next window guard ----------------------------------------------
@pytest.mark.parametrize("title", [
    "Twitch \u2014 Mozilla Firefox",                       # the stream itself: a loop
    "popebasement - Twitch \u2014 Mozilla Firefox",
    "eBay Live \u2014 Mozilla Firefox",                   # Stream manager plays our video
    "Bids & offers | My eBay | eBay \u2014 Mozilla Firefox Private Browsing",
    "Shipped Orders | ShopGoodwill.com \u2014 Mozilla Firefox",
    "Inbox (3) - Gmail \u2014 Mozilla Firefox",
    "~ : bash \u2014 Konsole",
    "Show Pager \u2014 Mozilla Firefox",
    None,                                                   # window closed
])
def test_next_hides_a_window_that_is_not_safe_to_show(obs, title):
    obs.title = title
    r = SF.go(obs, "next")
    assert obs.window_shown is False and r["window"] == {"title": title, "shown": False}
    assert obs.scene == "Up Next"  # still switches: card + webcam


def test_next_shows_a_store_page(obs):
    r = SF.go(obs, "next")
    assert obs.window_shown is True and r["window"]["shown"] is True


def test_refocus_checks_the_window_before_hiding_behind_up_next(obs):
    obs.scene, obs.title = "eBay Main", "Twitch \u2014 Mozilla Firefox"
    SF.refocus(obs)
    assert obs.window_shown is False


def test_window_title_reads_the_live_title_from_x(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return SF.subprocess.CompletedProcess(
            cmd, 0, stdout='_NET_WM_NAME(UTF8_STRING) = "Twitch \u2014 Mozilla Firefox"\n', stderr="")
    monkeypatch.setattr(SF.subprocess, "run", run)
    assert SF.window_title(FakeObs()) == "Twitch \u2014 Mozilla Firefox"
    assert seen == [["xprop", "-id", "62914622", "_NET_WM_NAME"]]
    gone = FakeObs()
    gone.capture = ""
    assert SF.window_title(gone) is None
