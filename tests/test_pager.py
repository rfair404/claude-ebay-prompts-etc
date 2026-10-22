"""tests/test_pager.py — the live show's pager: tools/live/show_flow.py's
state()/go()/refocus() and webapp/pager.py's /api/pager routes over them.

OBS is a fake that records scene switches, and the D5100 refocus is stubbed
(or, for refocus_d5 itself, the feed and gphoto2 are), so nothing here needs
OBS, the camera, or a stream."""
import base64
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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
        self.scene = scene
        self.active = {"Stream": live, "Record": False}  # OBS output -> on air
        self.profile = "Ebay"
        self.params = {"RecType": "FFmpeg", "FFOutputToFile": "false",
                       "FFURL": "rtmp://live.twitch.tv/app/secret"}
        self.connects = True  # does a started output move bytes?
        self.service = "whip_custom"
        self.standin_shown = False
        self.switches: list[str] = []
        self.closed = False
        self.window_shown = None  # the window item's last enabled state
        self.window_scenes = {"Up Next"}
        self.capture = "62914622\r\nThe Fair Hand \u2014 Mozilla Firefox\r\nNavigator"
        self.missing = set()  # scenes OBS doesn't have
        self.shots: list[dict] = []  # GetSourceScreenshot requests

    def call(self, req, data=None):
        if (data or {}).get("sceneName") in self.missing:
            raise obsctl.ObsError(f"{req} failed: code 600 No source was found by the name of "
                                  f"`{data['sceneName']}`.")
        if req == "GetCurrentProgramScene":
            return {"currentProgramSceneName": self.scene}
        if req == "SetCurrentProgramScene":
            self.scene = data["sceneName"]
            self.switches.append(self.scene)
            return {}
        for kind in ("Stream", "Record"):
            if req == f"Get{kind}Status":
                on = self.active[kind]
                st = {"outputActive": on, "outputTimecode": "00:01:02.000" if on else "00:00:00.000",
                      "outputBytes": 375000 if on and self.connects else 0, "outputDuration": 1000}
                if kind == "Stream":
                    st.update(outputSkippedFrames=0, outputTotalFrames=30)
                return st
            if req == f"Start{kind}":
                self.active[kind] = True
                return {}
            if req == f"Stop{kind}":
                self.active[kind] = False
                return {}
        if req == "GetProfileList":
            return {"currentProfileName": self.profile}
        if req == "SetCurrentProfile":
            self.profile = data["profileName"]
            return {}
        if req == "GetSceneCollectionList":
            return {"currentSceneCollectionName": SF.COLLECTION}
        if req == "GetVideoSettings":
            return {"baseWidth": SF.W, "baseHeight": SF.H}
        if req == "GetStreamServiceSettings":
            return {"streamServiceType": self.service,
                    "streamServiceSettings": {"server": "https://whip.example/secret"}}
        if req == "GetProfileParameter":
            assert data["parameterCategory"] == "AdvOut"
            return {"parameterValue": self.params.get(data["parameterName"])}
        if req == "SetProfileParameter":
            self.params[data["parameterName"]] = data["parameterValue"]
            return {}
        if req == "GetSceneItemList":
            items = []
            if data["sceneName"] in self.window_scenes:
                items.append({"sourceName": SF.WINDOW, "sceneItemId": 7, "sceneItemEnabled": True})
            if data["sceneName"] == SF.PRODUCT:
                items.append({"sourceName": SF.STANDIN, "sceneItemId": 9,
                              "sceneItemEnabled": self.standin_shown})
            return {"sceneItems": items}
        if req == "SetSceneItemEnabled":
            if data["sceneItemId"] == 9:
                self.standin_shown = data["sceneItemEnabled"]
                return {}
            assert data["sceneName"] in self.window_scenes and data["sceneItemId"] == 7
            self.window_shown = data["sceneItemEnabled"]
            return {}
        if req == "GetInputSettings":
            return {"inputSettings": {"capture_window": self.capture}}
        if req == "GetSourceScreenshot":
            self.shots.append(data)
            return {"imageData": "data:image/jpg;base64," + base64.b64encode(b"\xff\xd8" + data["sourceName"].encode()).decode()}
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
    fake.title = "Pop's Games | eBay Stores \u2014 Mozilla Firefox"
    monkeypatch.setattr(SF, "window_title", lambda c: fake.title)
    fake.snap = tmp_path / "d5100_latest.jpg"  # the feed's 2 s frame
    monkeypatch.setattr(SF, "feed_snaps", lambda pids: [fake.snap])
    fake.refocused = refocused
    return fake


def test_pages_are_the_five_in_running_order():
    assert list(SF.PAGES) == ["intro", "item", "next", "desktop", "webcam"]
    assert SF.PAGES["webcam"] == "Webcam Only"
    assert SF.PAGES["item"] == SF.PRODUCT and SF.PAGES["next"] == SF.UPNEXT
    # the HTTP body schema names the same pages
    assert pager.PageRequest.model_fields["page"].annotation.__args__ == tuple(SF.PAGES)


def test_state_maps_scene_to_page(obs):
    obs.active["Stream"] = True
    obs.snap.write_bytes(b"jpeg")
    assert SF.state(obs) == {
        "page": "intro", "scene": "Intro", "pages": ["intro", "item", "next", "desktop", "webcam"],
        "live": True, "timecode": "00:01:02.000", "feed": True, "feed_status": "running",
        "streams": {"ebay": {"live": True, "timecode": "00:01:02.000"},
                    "twitch": {"live": False, "timecode": None}}, "standin": False,
        "refocusing": False}
    obs.scene = "Desktop"  # switched by hand in OBS
    assert SF.state(obs)["page"] is None


def test_going_to_item_is_one_scene_switch_with_no_camera_work(obs):
    obs.scene = "Up Next"
    r = SF.go(obs, "item")
    assert obs.refocused == [] and obs.switches == ["eBay Main"]
    assert r == {"page": "item", "changed": True, "focused": None, "refocus_s": None}


def test_item_with_refocus_refocuses_behind_the_current_page(obs):
    obs.scene = "Up Next"
    r = SF.go(obs, "item", refocus=True)
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


def test_a_page_switch_never_waits_for_a_refocus(obs, monkeypatch):
    monkeypatch.setattr(SF, "LOCK_WAIT", 0)
    with SF.exclusive():  # a refocus is running (this process or the shell)
        assert SF.state(obs)["refocusing"] is True
        assert SF.go(obs, "next")["changed"] is True  # on air at once
        assert SF.go(obs, "item")["changed"] is True
        with pytest.raises(SF.Busy, match="already running"):  # a second refocus isn't queued
            SF.refocus(obs)
        SF.go(obs, "next")
        with pytest.raises(SF.Busy):
            SF.go(obs, "item", refocus=True)
    assert obs.switches == ["Up Next", "eBay Main", "Up Next"] and obs.refocused == []
    assert SF.state(obs)["refocusing"] is False
    assert SF.refocus(obs)["focused"] is True  # released


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
    assert body["page"] == "item" and body["switch"]["focused"] is None
    assert body["refocusing"] is False and obs.refocused == []  # the default: instant
    obs.scene = "Up Next"
    body = client.post("/api/pager", json={"page": "item", "refocus": True}).json()
    assert body["switch"]["focused"] is True and obs.refocused == ["Up Next"]


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


def test_busy_refocus_is_409_but_a_page_click_still_goes_through(client, obs, monkeypatch):
    monkeypatch.setattr(SF, "LOCK_WAIT", 0)
    with SF.exclusive():
        r = client.post("/api/pager/refocus")
        assert r.status_code == 409 and "already running" in r.json()["detail"]
        r = client.post("/api/pager", json={"page": "next"})
        assert r.status_code == 200 and r.json()["refocusing"] is True
    assert obs.scene == "Up Next"


def test_camera_failure_is_502_and_stays_on_the_current_page(client, obs, monkeypatch):
    def stuck(c):
        raise obsctl.ObsError("D5100 frames didn't come back in OBS within 30s")
    monkeypatch.setattr(SF, "refocus_d5", stuck)
    r = client.post("/api/pager", json={"page": "item", "refocus": True})
    assert r.status_code == 502 and "D5100" in r.json()["detail"]
    assert obs.scene == "Intro"  # never cut to a dead camera
    assert client.post("/api/pager", json={"page": "item"}).status_code == 200  # a plain switch works


def test_obs_going_quiet_mid_request_is_504_not_a_bare_500(client, monkeypatch):
    def hang(c):
        raise TimeoutError("timed out")
    monkeypatch.setattr(SF, "state", hang)
    r = client.get("/api/pager")
    assert r.status_code == 504 and "stopped answering" in r.json()["detail"]


def test_a_missing_scene_is_named(obs):
    obs.missing = {"Webcam Only"}
    with pytest.raises(obsctl.ObsError, match="no scene 'Webcam Only' for the webcam page.*setup"):
        SF.go(obs, "webcam")


def test_refocus_fails_fast_when_the_camera_is_off(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SF.subprocess.CompletedProcess(cmd, 0, stdout="Model  Port\n------\n", stderr="")
    monkeypatch.setattr(SF, "feed_pids", lambda: [])  # the feed was off
    monkeypatch.setattr(SF, "feed_stop", lambda: None)
    monkeypatch.setattr(SF.subprocess, "run", run)
    monkeypatch.setattr(SF, "feed_start", lambda: pytest.fail("started the feed with no camera"))
    with pytest.raises(obsctl.ObsError, match="not found on USB"):
        SF.refocus_d5(FakeObs())
    assert calls == [["gphoto2", "--auto-detect"]]


def test_preview_is_a_jpeg_of_the_program_scene(client, obs):
    obs.scene = "eBay Main"
    r = client.get("/api/pager/preview.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8eBay Main" and r.headers["cache-control"] == "no-store"
    assert obs.shots == [{"sourceName": "eBay Main", "imageFormat": "jpg",
                          "imageWidth": 360, "imageCompressionQuality": 60}]
    assert obs.switches == [] and obs.refocused == [] and obs.closed


def test_preview_never_waits_on_a_refocus(client, obs, monkeypatch):
    monkeypatch.setattr(SF, "LOCK_WAIT", 0)
    with SF.exclusive():
        assert client.get("/api/pager/preview.jpg").status_code == 200


def test_preview_is_local_only_and_says_when_obs_cannot_render(client, obs, monkeypatch):
    assert client.get("/api/pager/preview.jpg", headers={"host": "evil.example"}).status_code == 403
    def no_picture(c, data=None):
        raise obsctl.ObsError("GetSourceScreenshot failed: code 702 Failed to render screenshot.")
    monkeypatch.setattr(obs, "call", no_picture)
    r = client.get("/api/pager/preview.jpg")
    assert r.status_code == 502 and "702" in r.json()["detail"]


def test_control_page_has_a_button_per_page_and_calls_the_api(client):
    r = client.get("/pager")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    for page in SF.PAGES:
        assert f'data-page="{page}"' in r.text
    for dest in SF.DESTS:
        assert f'data-dest="{dest}"' in r.text
    assert "/api/pager/refocus" in r.text and "/api/pager/reconnect" in r.text
    assert "'5': 'webcam'" in r.text and "feed_status" in r.text
    assert client.get("/pager", headers={"host": "evil.example"}).status_code == 403
    # light polling, a deadline on every call, page clicks never wait on a refocus
    assert "setTimeout(poll, 1000)" in r.text and "document.hidden" in r.text
    assert "AbortController" in r.text and "refocuses first" not in r.text
    assert "{ page }" in r.text and "refocus: true" not in r.text  # the page never asks for a refocus switch
    assert "if (busy)" not in r.text
    # the On-air preview: one frame at a time, nothing while hidden or switched off
    assert "/api/pager/preview.jpg" in r.text and 'id="shottoggle"' in r.text
    assert "setTimeout(frame, 500)" in r.text and "previewOn && !document.hidden" in r.text


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
    # not on the allowlist (titles seen live on 2026-10-19)
    "Desktop @ QRect(0,0 1440x810)",                        # a full-screen capture
    "plasmashell",
    "Google Ads - Overview \u2014 Mozilla Firefox",
    "rfair404/claude-ebay-prompts-etc \u00b7 GitHub \u2014 Mozilla Firefox",
    "The Fair Hand \u2014 Mozilla Firefox",
    # on the allowlist, but vetoed
    "Seller Hub | eBay \u2014 Mozilla Firefox",
    "Pop's Games - Twitch \u2014 Mozilla Firefox",
])
def test_next_hides_a_window_that_is_not_safe_to_show(obs, title):
    obs.title = title
    r = SF.go(obs, "next")
    assert obs.window_shown is False and r["window"] == {"title": title, "shown": False}
    assert obs.scene == "Up Next"  # still switches: card + webcam


@pytest.mark.parametrize("title", [
    "Pop's Games | eBay Stores \u2014 Mozilla Firefox",
    "Vintage Nikon 7x35 Binoculars | eBay \u2014 Mozilla Firefox",
    "Pop\u2019s Games | eBay Stores \u2014 Mozilla Firefox",
    "popsgames on eBay \u2014 Mozilla Firefox",
])
def test_next_shows_a_public_ebay_page(obs, title):
    obs.title = title
    r = SF.go(obs, "next")
    assert obs.window_shown is True and r["window"] == {"title": title, "shown": True}


def test_safe_window_is_an_allowlist_with_a_veto():
    assert SF.window_safe("Vintage Nikon 7x35 Binoculars | eBay \u2014 Mozilla Firefox")
    assert not SF.window_safe("eBay Live \u2014 Mozilla Firefox")
    assert not SF.window_safe("Desktop @ QRect(0,0 1440x810)")
    assert not SF.window_safe("")
    assert not SF.window_safe(None)


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


def test_item_without_refocus_skips_the_camera(obs, client):
    obs.scene = "Intro"
    r = SF.go(obs, "item", refocus=False)
    assert r["changed"] and r["focused"] is None and obs.refocused == []
    obs.scene = "Intro"
    body = client.post("/api/pager", json={"page": "item", "refocus": False}).json()
    assert body["page"] == "item" and obs.refocused == []


def test_intro_never_shows_the_window_even_a_safe_one(obs):
    obs.scene = "Up Next"
    assert "window" not in SF.go(obs, "intro") and obs.window_shown is None  # plain card
    # a stray window capture in Intro, with a title the Up Next guard would show
    obs.window_scenes.add("Intro")
    obs.scene = "Up Next"
    r = SF.go(obs, "intro")
    assert "window" not in r and obs.window_shown is False and obs.scene == "Intro"


@pytest.mark.parametrize("page", ["item", "desktop", "webcam"])
def test_other_pages_only_ever_hide_the_window(obs, page):
    obs.window_scenes.add(SF.PAGES[page])
    SF.go(obs, page, refocus=False)
    assert obs.window_shown is False and obs.scene == SF.PAGES[page]


def test_webcam_page_is_the_fallback_and_never_touches_the_d5100(obs, client):
    obs.scene = "eBay Main"
    r = SF.go(obs, "webcam")
    assert r == {"page": "webcam", "changed": True, "focused": None, "refocus_s": None}
    assert obs.scene == "Webcam Only" and obs.refocused == []
    obs.scene = "Up Next"
    body = client.post("/api/pager", json={"page": "webcam"}).json()
    assert body["page"] == "webcam" and obs.refocused == []


# --- destinations ------------------------------------------------------------
def test_ebay_and_twitch_start_and_stop_independently(obs):
    r = SF.go_live(obs, "ebay", check=0)
    assert r == {"dest": "ebay", "changed": True, "kbps": 3000, "profile": "Ebay", "skipped": "0/30"}
    assert SF.go_live(obs, "twitch", check=0)["changed"] is True
    assert obs.active == {"Stream": True, "Record": True}
    SF.stop_dest(obs, "twitch")
    assert obs.active == {"Stream": True, "Record": False}  # eBay untouched
    assert SF.go_live(obs, "ebay", check=0)["changed"] is False


def test_twitch_refuses_until_set_up_and_never_leaks_the_url(obs):
    obs.params = {"RecType": "Standard"}
    obs.active["Stream"] = True  # eBay live: no fallback profile possible
    with pytest.raises(obsctl.ObsError, match="twitch-setup"):
        SF.go_live(obs, "twitch", check=0)
    obs.active["Stream"] = False
    SF.twitch_setup(obs)
    obs.active["Stream"] = True
    assert obs.params["RecType"] == "FFmpeg" and obs.params["FFFormat"] == "flv"
    with pytest.raises(obsctl.ObsError, match="paste rtmp") as e:
        SF.go_live(obs, "twitch", check=0)
    obs.params["FFURL"] = "rtmp://live.twitch.tv/app/secret"
    assert SF.go_live(obs, "twitch", check=0)["changed"]
    assert "secret" not in str(e.value)
    assert obs.active["Record"] is True and obs.active["Stream"] is True


def test_twitch_setup_refuses_while_anything_is_live(obs):
    obs.active["Stream"] = True
    with pytest.raises(obsctl.ObsError, match="stop both"):
        SF.twitch_setup(obs)


def test_go_live_switches_to_the_show_profile_but_not_mid_stream(obs):
    obs.profile = "Untitled"
    SF.go_live(obs, "ebay", check=0)
    assert obs.profile == "Ebay"
    obs.profile = "Untitled"  # Twitch live on its own profile
    with pytest.raises(obsctl.ObsError, match="Twitch is live on its own profile"):
        SF.go_live(obs, "ebay", check=0)


def test_twitch_without_a_url_falls_back_to_its_own_profile(obs):
    obs.params = {"RecType": "FFmpeg", "FFOutputToFile": "false"}  # no FFURL
    r = SF.go_live(obs, "twitch", check=0)
    assert r["profile"] == "Untitled" and obs.active == {"Stream": True, "Record": False}
    st = SF.streams(obs)
    assert st["twitch"]["live"] and not st["ebay"]["live"]  # not mislabelled as eBay
    SF.stop_dest(obs, "twitch")
    assert obs.active["Stream"] is False
    # with eBay live the fallback isn't possible: it says what's missing
    obs.profile = "Ebay"
    obs.active["Stream"] = True
    with pytest.raises(obsctl.ObsError, match="no Twitch URL"):
        SF.go_live(obs, "twitch", check=0)


def test_a_start_that_moves_no_bytes_is_an_error(obs):
    obs.connects = False
    with pytest.raises(obsctl.ObsError, match="didn't connect"):
        SF.go_live(obs, "ebay", check=0)


def test_stream_route(client, obs, monkeypatch):
    real = SF.go_live
    monkeypatch.setattr(SF, "go_live", lambda c, d: real(c, d, check=0))
    r = client.post("/api/pager/stream", json={"dest": "twitch", "action": "start"})
    assert r.status_code == 200
    body = r.json()
    assert body["streams"]["twitch"]["live"] and not body["streams"]["ebay"]["live"]
    assert body["stream"]["changed"] and body["live"]
    r = client.post("/api/pager/stream", json={"dest": "twitch", "action": "stop"})
    assert r.json()["streams"]["twitch"]["live"] is False
    assert client.post("/api/pager/stream", json={"dest": "youtube", "action": "start"}).status_code == 422
    assert client.post("/api/pager/stream", json={"dest": "ebay", "action": "start"},
                       headers={"origin": "https://evil.example"}).status_code == 403
    obs.params = {}
    obs.active["Stream"] = True  # eBay live, so Twitch can't fall back to its own profile
    r = client.post("/api/pager/stream", json={"dest": "twitch", "action": "start"})
    assert r.status_code == 502 and "twitch-setup" in r.json()["detail"]


def test_ebay_refuses_when_the_stream_service_is_not_whip(obs):
    obs.service = "rtmp_common"  # Twitch sign-in replaced it
    with pytest.raises(obsctl.ObsError, match="isn't eBay's WHIP") as e:
        SF.go_live(obs, "ebay", check=0)
    assert obs.active["Stream"] is False and "secret" not in str(e.value)


def test_standin_makes_item_skip_the_camera_and_refocus_refuse(obs):
    obs.standin_shown = True
    r = SF.go(obs, "item")
    assert r["changed"] and r["focused"] is None and obs.refocused == []
    with pytest.raises(obsctl.ObsError, match="stand-in is on"):
        SF.refocus(obs)
    assert SF.state(obs)["standin"] is True


def test_standin_route(client, obs, monkeypatch):
    calls = []
    monkeypatch.setattr(SF, "standin", lambda c, on: calls.append(on))
    assert client.post("/api/pager/standin", json={"on": True}).status_code == 200
    assert calls == [True]


def test_desktop_page_switches_without_camera_or_window_guard(obs):
    obs.scene = "eBay Main"
    r = SF.go(obs, "desktop")
    assert obs.scene == "Desktop with Face Cam" and r["focused"] is None
    assert "window" not in r and obs.refocused == []


# --- refocus vs. the host's own switches -----------------------------------
def test_a_failed_refocus_on_item_fades_back_to_item(obs, monkeypatch):
    def fail(c):
        raise obsctl.ObsError("gphoto2 --set-config autofocusdrive=1 hung for 15s")
    monkeypatch.setattr(SF, "refocus_d5", fail)
    obs.scene = "eBay Main"
    with pytest.raises(obsctl.ObsError, match="^refocus failed: gphoto2"):
        SF.refocus(obs)
    assert obs.switches == ["Up Next", "eBay Main"]


def test_a_switch_made_during_a_refocus_wins(obs, monkeypatch):
    def host_clicks_intro(c):
        c.scene = "Intro"  # another caller's switch lands mid-refocus
        return True, 5.0
    monkeypatch.setattr(SF, "refocus_d5", host_clicks_intro)
    obs.scene = "eBay Main"
    SF.refocus(obs)
    assert obs.scene == "Intro"  # not yanked back to item
    obs.scene = "Up Next"
    r = SF.go(obs, "item", refocus=True)
    assert r["changed"] is False and r["superseded_by"] == "intro" and obs.scene == "Intro"


def test_next_asked_for_during_a_refocus_stays_on_next(obs, monkeypatch):
    # Up Next is already on air (the refocus hides behind it), so the host's
    # "next item" changes nothing in OBS; the refocus must still not fade to item
    def host_clicks_next(c):
        assert SF.go(c, "next")["changed"] is False
        return True, 5.0
    monkeypatch.setattr(SF, "refocus_d5", host_clicks_next)
    obs.scene = "eBay Main"
    SF.refocus(obs)
    assert obs.scene == "Up Next" and obs.switches == ["Up Next"]
    r = SF.go(obs, "item", refocus=True)  # from Up Next: the host asks for Next again
    assert r["changed"] is False and r["superseded_by"] == "next" and obs.scene == "Up Next"


def test_a_refocus_with_no_switch_meanwhile_fades_back_to_item(obs, monkeypatch):
    SF.go(obs, "intro")  # an earlier switch's mark doesn't count
    obs.scene = "eBay Main"
    SF.refocus(obs)
    assert obs.scene == "eBay Main"


# --- the D5100: a refocus never strands the feed; OBS reopens the source ----
class CamObs(FakeObs):
    """FakeObs plus the v4l2 cameras: what the D5's screenshots show (None:
    OBS's code 702) and each camera's device_id, which a reopen clears and
    sets back."""

    def __init__(self, pictures=("x",), after_reopen=None):
        super().__init__(scene="eBay Main")
        self.pictures = list(pictures)  # one per screenshot; the last one repeats
        self.after_reopen = after_reopen
        self.device_ids = {SF.D5: "/dev/video10", SF.WEBCAM: "/dev/video0"}
        self.kinds = {}  # input -> inputKind, if not v4l2_input
        self.sets = []  # (input, device_id) OBS was handed, in order

    @property
    def devices(self):  # what the D5 was handed
        return [d for n, d in self.sets if n == SF.D5]

    def call(self, req, data=None):
        if req == "GetSourceScreenshot":
            assert data["sourceName"] == SF.D5
            pic = self.pictures.pop(0) if len(self.pictures) > 1 else self.pictures[0]
            if pic is None:
                raise obsctl.ObsError("GetSourceScreenshot failed: code 702 Failed to render screenshot")
            return {"imageData": pic}
        name = (data or {}).get("inputName")
        if req == "GetInputSettings" and name in self.device_ids:
            return {"inputKind": self.kinds.get(name, "v4l2_input"),
                    "inputSettings": {"device_id": self.device_ids[name]}}
        if req == "SetInputSettings" and name in self.device_ids:
            dev = self.device_ids[name] = data["inputSettings"]["device_id"]
            self.sets.append((name, dev))
            if name == SF.D5 and dev and self.after_reopen is not None:
                self.pictures = list(self.after_reopen)
            return {}
        return super().call(req, data)


class Camera:
    """The feed loop and gphoto2, stubbed; `log` is what was done to them."""

    def __init__(self, monkeypatch, snap):
        self.running, self.on_usb, self.frames, self.snap = True, True, True, snap
        self.af_hangs = self.stop_fails = False
        self.log = []
        monkeypatch.setattr(SF, "feed_pids", lambda: [42] if self.running else [])
        monkeypatch.setattr(SF, "feed_stop", self.stop)
        monkeypatch.setattr(SF, "feed_start", self.start)
        monkeypatch.setattr(SF, "feed_snaps", lambda pids: [snap])
        monkeypatch.setattr(SF.subprocess, "run", self.run)

    def stop(self):
        self.log.append("stop")
        self.running = False
        if self.stop_fails:
            raise obsctl.ObsError("gphoto2 didn't release the camera within 10s")

    def start(self):
        self.log.append("start")
        started, self.running = not self.running, True
        if self.frames:  # the feed writes its first frame (FEED_SNAP)
            self.snap.write_bytes(b"jpeg")
            later = time.time() + 1
            os.utime(self.snap, (later, later))
        return started

    def run(self, cmd, **kw):
        assert cmd[0] == "gphoto2" and kw["timeout"] == SF.GPHOTO_TIMEOUT
        self.log.append(cmd[1])
        if cmd[1] == "--auto-detect":
            port = "Nikon DSC D5100 (PTP mode)  usb:001,005\n" if self.on_usb else ""
            return SF.subprocess.CompletedProcess(cmd, 0, stdout="Model  Port\n------\n" + port)
        if self.af_hangs:
            raise SF.subprocess.TimeoutExpired(cmd, kw["timeout"])
        return SF.subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")


@pytest.fixture
def camera(tmp_path, monkeypatch):
    monkeypatch.setattr(SF, "LOCK", tmp_path / "show_flow.lock")
    monkeypatch.setattr(SF.time, "sleep", lambda s: None)
    monkeypatch.setattr(SF, "FRAMES_WAIT", 0.3)
    monkeypatch.setattr(SF, "FEED_START_WAIT", 0.3)
    monkeypatch.setattr(SF, "REOPEN_GRACE", 0)
    monkeypatch.setattr(SF, "window_title", lambda c: "Pop's Games | eBay Stores")
    return Camera(monkeypatch, tmp_path / "d5100_latest.jpg")


def test_refocus_survives_obs_702_right_after_the_standin_comes_off(camera):
    # 2026-10-19: no picture to compare (702) used to end the command with the feed stopped
    c = CamObs([None, None, "a", "b"])
    r = SF.refocus(c)
    assert r["focused"] is True
    assert camera.running and camera.log[:2] == ["stop", "--auto-detect"] and "start" in camera.log
    assert c.switches == ["Up Next", "eBay Main"]  # hidden for the freeze, then back on item


def test_obs_picking_the_feed_up_by_itself_needs_no_reopen(camera):
    c = CamObs(["old", "new"])
    assert SF.refocus_d5(c)[0] is True
    assert c.devices == []


def test_feed_back_but_obs_stuck_reopens_the_source(camera):
    c = CamObs(["black"], after_reopen=["m1", "m2"])
    assert SF.refocus_d5(c)[0] is True
    assert c.devices == ["", "/dev/video10"]  # cleared, then set back: OBS reopens it


@pytest.mark.parametrize("fault, message", [
    ("no_frames", "didn't come back in OBS"),
    ("af_hangs", "hung for 15s"),
    ("stop_fails", "didn't release"),
    ("off_usb", "not found on USB"),
])
def test_a_failed_refocus_leaves_the_feed_running(camera, fault, message):
    c = CamObs(["frozen"])
    camera.frames = fault != "no_frames"
    camera.af_hangs = fault == "af_hangs"
    camera.stop_fails = fault == "stop_fails"
    camera.on_usb = fault != "off_usb"
    with pytest.raises(obsctl.ObsError, match=message):
        SF.refocus(c)
    assert camera.running and camera.log[-1] == "start"
    assert c.scene == "eBay Main"  # the item view is back, not stranded on Up Next


def test_feed_start_makes_obs_reopen_the_source(camera, capsys):
    # 2026-10-19: feed restarted, /dev/video10 had frames, OBS's source stayed black
    camera.running = False
    c = CamObs(["black"], after_reopen=["m1", "m2"])
    SF.cmd_feed(c, SimpleNamespace(action="start"))
    assert camera.log == ["start"] and c.devices == ["", "/dev/video10"]
    assert "D5100 feed started" in capsys.readouterr().out


def test_feed_start_when_running_touches_nothing(camera):
    c = CamObs(["x"])
    assert SF.feed_up(c) == "already running"
    assert camera.log == [] and c.devices == []


def test_feed_start_with_the_camera_off_says_so(camera):
    camera.running, camera.frames = False, False
    assert SF.feed_up(CamObs(["x"])).startswith("started, but no frames")
    assert camera.running


def test_reconnect_reopens_both_cameras_and_never_touches_the_feed(camera):
    # the webcam froze on one frame mid-stream (2026-10-19); the same reopen fixed it
    c = CamObs()
    r = SF.reconnect(c)
    assert r == {SF.D5: {"ok": True, "device": "/dev/video10"},
                 SF.WEBCAM: {"ok": True, "device": "/dev/video0"}}
    assert c.sets == [(SF.D5, ""), (SF.D5, "/dev/video10"), (SF.WEBCAM, ""), (SF.WEBCAM, "/dev/video0")]
    assert camera.log == [] and c.switches == []  # no feed restart, no page change


def test_reconnect_reports_each_camera_on_its_own(camera):
    c = CamObs()
    c.device_ids[SF.D5] = ""  # left cleared: the feed's device
    c.device_ids[SF.WEBCAM] = ""  # no safe default for the webcam
    r = SF.reconnect(c)
    assert r[SF.D5] == {"ok": True, "device": "/dev/video10"}
    assert r[SF.WEBCAM]["ok"] is False and "no device set" in r[SF.WEBCAM]["error"]
    c.kinds[SF.WEBCAM] = "ffmpeg_source"
    assert "isn't a v4l2 camera" in SF.reconnect(c, [SF.WEBCAM])[SF.WEBCAM]["error"]
    assert (SF.WEBCAM, "") not in c.sets  # never handed a non-v4l2 source a device_id


def test_reconnect_route_and_cli(client, camera, monkeypatch, capsys):
    c = CamObs()
    monkeypatch.setattr(SF, "connect", lambda: c)
    with SF.exclusive():  # a refocus running doesn't hold it up
        body = client.post("/api/pager/reconnect").json()
    assert body["reconnect"][SF.WEBCAM] == {"ok": True, "device": "/dev/video0"}
    assert body["page"] == "item"
    assert client.post("/api/pager/reconnect", headers={"origin": "https://evil.example"}).status_code == 403
    c.sets.clear()
    assert SF.main(["reconnect", "webcam"]) == 0
    assert c.sets == [(SF.WEBCAM, ""), (SF.WEBCAM, "/dev/video0")]
    assert "Webcam: reopened /dev/video0" in capsys.readouterr().out
    c.device_ids[SF.WEBCAM] = ""
    assert SF.main(["reconnect"]) == 1  # one failed: exit 1, after reopening the other
    assert c.sets[-1] == (SF.D5, "/dev/video10")
