"""tests/test_show_flow.py — tools/live/show_flow.py's `setup`, window pick
and feed status, against a fake OBS that keeps real scene item stacks.

Nothing here needs OBS, the D5100, gphoto2 or X: the window's live title and
the feed's processes are stubbed. Pager switching is in tests/test_pager.py.
The camera watch (black panels) runs here on the same fake, one tick at a time."""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "live"))

import obsctl  # noqa: E402
import show_flow as SF  # noqa: E402

STORE = "Pop's Games | eBay Stores — Mozilla Firefox"
REAL_LIVE_TITLE = SF.live_title  # the fixture below stubs it
REAL_TG7_SOURCE = SF.tg7_source  # and this


class SceneObs:
    """Scenes as stacks, bottom first (OBS's sceneItemIndex order); a new
    scene item lands on top, enabled unless asked otherwise, as in OBS.
    `pictures[source]` is what each screenshot of a source shows, one per
    call, the last one repeating (None: OBS's code 702, no picture)."""

    def __init__(self, scenes=None, inputs=()):
        self.ids = iter(range(1, 1000))
        self.scenes = {}
        for name, items in (scenes or {}).items():
            self.scenes[name] = [self._new(src, en) for src, en in items]
        self.inputs = {SF.D5, SF.WEBCAM, SF.WINDOW, SF.MIC, *inputs}
        self.windows = [{"itemName": "Pop's Games | eBay Stores — Mozilla Firefox",
                         "itemValue": "111\r\nPop's Games | eBay Stores — Mozilla Firefox\r\nfirefox"}]
        self.settings = {}
        self.kinds = {}  # inputName -> inputKind, for inputs this fake created
        self.transforms = {}
        self.program = SF.INTRO
        self.pictures = {}
        self.shots = []  # sources screenshotted, in order
        self.writes = []  # SetSceneItemEnabled: (scene, source, enabled)
        self.reopens = []  # sources whose device_id was cleared and set back (reconnect)

    def _new(self, src, enabled=True):
        return {"sourceName": src, "sceneItemId": next(self.ids), "sceneItemEnabled": enabled}

    def _find(self, scene, item_id):
        return next(i for i in self.scenes[scene] if i["sceneItemId"] == item_id)

    def call(self, req, d=None):
        d = d or {}
        if req == "GetSceneCollectionList":
            return {"currentSceneCollectionName": SF.COLLECTION}
        if req == "GetInputList":
            return {"inputs": [{"inputName": n, "inputKind": self.kinds.get(n)} for n in sorted(self.inputs)]}
        if req == "RemoveInput":
            self.inputs.discard(d["inputName"])
            self.kinds.pop(d["inputName"], None)
            for stack in self.scenes.values():
                stack[:] = [i for i in stack if i["sourceName"] != d["inputName"]]
            return {}
        if req == "GetInputPropertiesListPropertyItems":
            return {"propertyItems": self.windows}
        if req == "SetInputSettings":
            if d["inputSettings"] == {"device_id": ""}:
                self.reopens.append(d["inputName"])
            self.settings.setdefault(d["inputName"], {}).update(d["inputSettings"])
            return {}
        if req == "GetInputSettings":
            return {"inputSettings": self.settings.get(d["inputName"], {})}
        if req == "CreateInput":
            assert d["inputName"] not in self.inputs, d
            self.inputs.add(d["inputName"])
            self.kinds[d["inputName"]] = d["inputKind"]
            self.settings[d["inputName"]] = dict(d.get("inputSettings") or {})
            it = self._new(d["inputName"], d.get("sceneItemEnabled", True))
            self.scenes[d["sceneName"]].append(it)
            return {"sceneItemId": it["sceneItemId"]}
        if req == "GetSceneList":
            return {"scenes": [{"sceneName": n} for n in self.scenes]}
        if req == "CreateScene":
            assert d["sceneName"] not in self.scenes
            self.scenes[d["sceneName"]] = []
            return {}
        if req == "GetSceneItemList":
            if d["sceneName"] not in self.scenes:
                raise obsctl.ObsError(f"{req} failed: code 600 No source was found by the name of "
                                      f"`{d['sceneName']}`.")
            return {"sceneItems": [{**i, "sceneItemIndex": n}
                                   for n, i in enumerate(self.scenes[d["sceneName"]])]}
        if req == "CreateSceneItem":
            assert d["sourceName"] in self.inputs, d
            it = self._new(d["sourceName"], d.get("sceneItemEnabled", True))
            self.scenes[d["sceneName"]].append(it)
            return {"sceneItemId": it["sceneItemId"]}
        if req == "SetSceneItemTransform":
            self.transforms[(d["sceneName"], self._find(d["sceneName"], d["sceneItemId"])["sourceName"])] = \
                d["sceneItemTransform"]
            return {}
        if req == "SetSceneItemEnabled":
            it = self._find(d["sceneName"], d["sceneItemId"])
            it["sceneItemEnabled"] = d["sceneItemEnabled"]
            self.writes.append((d["sceneName"], it["sourceName"], d["sceneItemEnabled"]))
            return {}
        if req == "SetSceneItemIndex":
            stack = self.scenes[d["sceneName"]]
            it = self._find(d["sceneName"], d["sceneItemId"])
            stack.remove(it)
            stack.insert(d["sceneItemIndex"], it)
            return {}
        if req == "GetCurrentProgramScene":
            return {"currentProgramSceneName": self.program}
        if req == "SetCurrentProgramScene":
            self.program = d["sceneName"]
            return {}
        if req == "GetSourceScreenshot":
            src = d["sourceName"]
            if src not in self.inputs:
                raise obsctl.ObsError(f"{req} failed: code 600 No source was found by the name of `{src}`.")
            self.shots.append(src)
            pics = self.pictures[src]
            pic = pics.pop(0) if len(pics) > 1 else pics[0]
            if pic is None:
                raise obsctl.ObsError(f"{req} failed: code 702 Failed to render screenshot.")
            return {"imageData": f"data:image/jpg;base64,{pic}"}
        if req in ("SetCurrentSceneTransition", "SetCurrentSceneTransitionDuration"):
            return {}
        raise AssertionError(f"unexpected request {req}")

    # what's on screen
    def order(self, scene):
        return [i["sourceName"] for i in self.scenes[scene]]

    def enabled(self, scene, src):
        return next(i["sceneItemEnabled"] for i in self.scenes[scene] if i["sourceName"] == src)

    def count(self, scene, src):
        return self.order(scene).count(src)

    def close(self):
        pass


def live_obs():
    """The eBay Live collection as the mixer found it on 2026-10-19."""
    return SceneObs({
        SF.PRODUCT: [(SF.D5, True), (SF.WEBCAM, True), (SF.WINDOW, False)],
        SF.UPNEXT: [],
        SF.INTRO: [(SF.WINDOW, False)],  # the stray, hidden Window Capture
        # hand-built: the webcam with the page's transform, and the mic
        SF.WEBCAM_ONLY: [(SF.WEBCAM, True), (SF.MIC, True)],
    })


def args(**kw):
    return SimpleNamespace(**{"window": None, "intro_text": "Starting\\nsoon", "desktop_window": None,
                              "desktop_crop_left": None, "d5_rotation": 270, "tg7_rotation": 0, **kw})


@pytest.fixture(autouse=True)
def no_x(monkeypatch):
    titles = {"111": STORE}
    monkeypatch.setattr(SF, "live_title", lambda v: titles.get((v or "").split("\r\n")[0]))
    monkeypatch.setattr(SF.time, "sleep", lambda s: None)
    monkeypatch.setattr(SF, "tg7_source", lambda: ("v4l2_input", {"device_id": "/dev/video11"}))
    return titles


@pytest.fixture(autouse=True)
def show_files(tmp_path, monkeypatch):
    """The refocus lock and the camera watch's files in tmp_path, never a running show's."""
    for const, name in (("LOCK", "show_flow.lock"), ("WATCH_LOCK", "show_flow.watch.lock"),
                        ("CAMS_FILE", "show_flow.cams.json"), ("WATCH_LOG", "show_flow_watch.log")):
        monkeypatch.setattr(SF, const, tmp_path / name)


# --- bug: setup with the stand-in on -----------------------------------------
def test_setup_keeps_the_standin_on_under_the_webcam():
    c = live_obs()
    SF.standin(c, True)
    SF.cmd_setup(c, args())
    assert SF.standin_on(c) is True
    assert c.enabled(SF.PRODUCT, SF.D5) is False  # not re-enabled
    order = c.order(SF.PRODUCT)
    assert order.index(SF.STANDIN) < order.index(SF.D5) < order.index(SF.WEBCAM)
    visible_on_top = [s for s in reversed(order) if c.enabled(SF.PRODUCT, s)]
    assert visible_on_top[0] == SF.WEBCAM  # face cam over the white panel


def test_setup_with_the_standin_off_shows_the_d5():
    c = live_obs()
    SF.standin(c, True)
    SF.standin(c, False)
    SF.cmd_setup(c, args())
    assert c.enabled(SF.PRODUCT, SF.D5) is True and c.enabled(SF.PRODUCT, SF.STANDIN) is False
    order = c.order(SF.PRODUCT)
    assert order.index(SF.STANDIN) < order.index(SF.D5) < order.index(SF.WEBCAM)


def test_setup_never_adds_a_standin_to_the_item_view():
    c = live_obs()
    c.inputs.add(SF.STANDIN)  # exists for the desktop page's white background
    SF.cmd_setup(c, args())
    assert SF.STANDIN not in c.order(SF.PRODUCT) and c.enabled(SF.PRODUCT, SF.D5)


# --- bug: windows outside Up Next ---------------------------------------------
def test_setup_keeps_the_window_out_of_every_page_but_up_next():
    c = live_obs()
    c.scenes[SF.INTRO][0]["sceneItemEnabled"] = True  # the stray capture, shown
    c.settings[SF.WINDOW] = {"capture_window": "111\r\nx\r\ny"}
    SF.cmd_setup(c, args())
    for scene in (SF.INTRO, SF.PRODUCT):
        assert c.enabled(scene, SF.WINDOW) is False
    assert SF.WINDOW not in c.order(SF.WEBCAM_ONLY)
    assert c.enabled(SF.UPNEXT, SF.WINDOW) is True  # a store page: Up Next may show it


def test_setup_hides_an_unsafe_window_in_up_next(no_x):
    c = live_obs()
    no_x["222"] = "eBay Live — Mozilla Firefox"
    c.settings[SF.WINDOW] = {"capture_window": "222\r\neBay Live\r\nfirefox"}
    SF.cmd_setup(c, args())
    assert c.enabled(SF.UPNEXT, SF.WINDOW) is False


# --- bug: setup --window and closed windows -----------------------------------
def test_setup_window_refuses_a_window_that_has_closed(capsys):
    c = live_obs()
    c.windows = [{"itemName": "The Fair Hand — Mozilla Firefox",
                  "itemValue": "999\r\nThe Fair Hand — Mozilla Firefox\r\nfirefox"}]
    with pytest.raises(obsctl.ObsError, match="window is closed"):
        SF.cmd_setup(c, args(window="Fair Hand"))
    assert SF.WINDOW not in c.settings


def test_setup_window_skips_a_closed_match_for_an_open_one(capsys):
    c = live_obs()
    c.windows.insert(0, {"itemName": "Pop's Games | old tab", "itemValue": "999\r\nPop's Games\r\nfirefox"})
    SF.cmd_setup(c, args(window="pop's games"))
    assert c.settings[SF.WINDOW]["capture_window"].startswith("111\r\n")
    out = capsys.readouterr().out
    assert f"window: {STORE}" in out and "will hide" not in out


def test_setup_window_warns_when_up_next_will_hide_it(no_x, capsys):
    c = live_obs()
    c.windows = [{"itemName": "Google Ads", "itemValue": "333\r\nGoogle Ads\r\nfirefox"}]
    no_x["333"] = "Google Ads - Overview — Mozilla Firefox"
    SF.cmd_setup(c, args(window="google"))
    assert "Up Next will hide it" in capsys.readouterr().out


def test_window_title_check_survives_missing_xprop(monkeypatch):
    def run(cmd, **kw):
        raise FileNotFoundError("xprop")
    monkeypatch.setattr(SF.subprocess, "run", run)
    assert REAL_LIVE_TITLE("62914622\r\nx\r\ny") is None


def test_live_title_reads_x_and_treats_a_bad_window_as_closed(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        ok = cmd[2] == "111"
        return SF.subprocess.CompletedProcess(
            cmd, 0 if ok else 1, stdout=f'_NET_WM_NAME(UTF8_STRING) = "{STORE}"\n' if ok else "",
            stderr="" if ok else "X Error: BadWindow")
    monkeypatch.setattr(SF.subprocess, "run", run)
    assert REAL_LIVE_TITLE("111\r\nold title\r\nfirefox") == STORE
    assert REAL_LIVE_TITLE("999\r\nThe Fair Hand\r\nfirefox") is None
    assert REAL_LIVE_TITLE("") is None
    assert seen == [["xprop", "-id", "111", "_NET_WM_NAME"], ["xprop", "-id", "999", "_NET_WM_NAME"]]


# --- the webcam page ----------------------------------------------------------
def test_setup_builds_the_webcam_page_idempotently():
    c = live_obs()
    SF.cmd_setup(c, args())
    SF.cmd_setup(c, args())
    order = c.order(SF.WEBCAM_ONLY)
    # dark card under, webcam on top, its (hidden) black panel over it
    assert order[0] == SF.BG and order[-2:] == [SF.WEBCAM, "Webcam Down"]
    assert sorted(order) == sorted([SF.BG, SF.WEBCAM, SF.MIC, "Webcam Down"])  # nothing doubled
    assert c.transforms[(SF.WEBCAM_ONLY, SF.WEBCAM)] == {
        "positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_OUTER",
        "boundsWidth": 1080, "boundsHeight": 1350, "cropToBounds": True}
    assert all(c.enabled(SF.WEBCAM_ONLY, s) for s in order if s != "Webcam Down")


def test_setup_creates_the_webcam_page_when_missing():
    c = live_obs()
    del c.scenes[SF.WEBCAM_ONLY]
    SF.cmd_setup(c, args())
    assert c.order(SF.WEBCAM_ONLY) == [SF.BG, SF.MIC, SF.WEBCAM, "Webcam Down"]


def test_webcam_page_on_the_cli(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(SF, "connect", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(SF, "go", lambda c, page, refocus=False: seen.append((page, refocus)) or
                        {"page": page, "changed": True, "focused": None, "refocus_s": None})
    assert SF.main(["page", "webcam"]) == 0
    assert seen == [("webcam", False)] and "on air: webcam" in capsys.readouterr().out


def test_page_item_is_a_plain_switch_unless_refocus_is_asked_for(monkeypatch):
    seen = []
    monkeypatch.setattr(SF, "connect", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(SF, "go", lambda c, page, refocus=False: seen.append((page, refocus)) or
                        {"page": page, "changed": True, "focused": None, "refocus_s": None})
    for argv in (["page", "item"], ["back"], ["page", "item", "--no-refocus"], ["page", "item", "--refocus"]):
        assert SF.main(argv) == 0
    assert seen == [("item", False), ("item", False), ("item", False), ("item", True)]


def test_a_dropped_obs_connection_is_an_error_line_not_a_traceback(monkeypatch, capsys):
    def boom(c, a):
        raise ConnectionResetError("reset by peer")
    monkeypatch.setattr(SF, "connect", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(SF, "cmd_status", boom)
    assert SF.main(["status"]) == 1
    assert "lost the connection to OBS" in capsys.readouterr().err


# --- feed status ---------------------------------------------------------------
def test_feed_status_reports_frame_freshness(tmp_path, monkeypatch):
    snap = tmp_path / "d5100_latest.jpg"
    monkeypatch.setattr(SF, "feed_snaps", lambda pids: [snap])
    assert SF.feed_status([]) == "stopped"
    assert SF.feed_status([42]) == "running, no frames yet"
    snap.write_bytes(b"jpeg")
    assert SF.feed_status([42]) == "running"
    old = time.time() - 47
    os.utime(snap, (old, old))
    assert SF.feed_status([42]) == "running, no frames for 47s"


def test_feed_snaps_follow_the_feed_process_environment(monkeypatch):
    envs = {1: {"TMPDIR": "/run/user/1000/t"}, 2: {"SNAP": "/x/snap.jpg"}, 3: {}}
    monkeypatch.setattr(SF, "_proc_env", lambda pid: envs[pid])
    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.setattr(SF.tempfile, "gettempdir", lambda: "/tmp/mine")
    assert SF.feed_snaps([1, 2, 3]) == [Path("/run/user/1000/t/d5100_latest.jpg"), Path("/x/snap.jpg"),
                                        Path("/tmp/d5100_latest.jpg"), Path("/tmp/mine/d5100_latest.jpg")]


@pytest.mark.skipif(not Path("/proc/self/environ").exists(), reason="needs /proc")
def test_proc_env_reads_a_process_environment():
    assert "PATH" in SF._proc_env(os.getpid())
    assert SF._proc_env(-1) == {}


def test_feed_status_cli_never_starts_the_feed(monkeypatch, capsys):
    monkeypatch.setattr(SF, "feed_pids", lambda: [42])
    monkeypatch.setattr(SF, "feed_status", lambda pids=None: "running, no frames for 12s")
    monkeypatch.setattr(SF, "feed_start", lambda: pytest.fail("started the feed"))
    monkeypatch.setattr(SF, "feed_stop", lambda: pytest.fail("stopped the feed"))
    SF.cmd_feed(None, SimpleNamespace(action="status"))
    assert "D5100 feed: running, no frames for 12s" in capsys.readouterr().out


# --- the multi page: D5100 + webcam + desktop window ----------------------------
def test_setup_without_a_desktop_window_builds_no_multi_page():
    c = live_obs()
    SF.cmd_setup(c, args())
    assert SF.MULTI not in c.scenes and SF.DESKTOP not in c.scenes


def test_setup_builds_the_multi_page_idempotently():
    c = live_obs()
    SF.cmd_setup(c, args(desktop_window="Pop's Games", desktop_crop_left=216))
    SF.cmd_setup(c, args())  # later runs keep it, the window already picked
    assert c.order(SF.MULTI) == [SF.BG, SF.D5, "D5 Down", SF.WEBCAM, "Webcam Down", SF.DESKTOP_WIN, SF.MIC]
    assert all(c.enabled(SF.MULTI, s) for s in c.order(SF.MULTI) if s not in SF.DOWN.values())
    d5, cam = c.transforms[(SF.MULTI, SF.D5)], c.transforms[(SF.MULTI, SF.WEBCAM)]
    # side by side in the top row, the D5 on its side: its bounds turn with it
    assert (d5["positionX"], d5["positionY"], d5["rotation"]) == (270, 405, 270)
    assert (d5["boundsWidth"], d5["boundsHeight"]) == (SF.MULTI_ROW, SF.W / 2) and d5["cropToBounds"]
    assert (cam["positionX"], cam["positionY"]) == (810, 405)
    assert (cam["boundsWidth"], cam["boundsHeight"]) == (SF.W / 2, SF.MULTI_ROW) and cam["cropToBounds"]
    win = c.transforms[(SF.MULTI, SF.DESKTOP_WIN)]
    assert (win["positionY"], win["boundsHeight"]) == (SF.MULTI_ROW, SF.H - SF.MULTI_ROW)
    assert win["alignment"] == win["boundsAlignment"] == 4  # hung from the row
    assert "cropLeft" not in win  # the second run didn't pass a crop: the first one's stays in OBS
    SF.cmd_setup(c, args(desktop_crop_left=100))
    assert c.transforms[(SF.MULTI, SF.DESKTOP_WIN)]["cropLeft"] == 100
    assert c.count(SF.MULTI, SF.D5) == 1 and c.count(SF.MULTI, SF.WEBCAM) == 1


def test_the_standin_darkens_the_multi_pages_d5_too():
    c = live_obs()
    SF.cmd_setup(c, args(desktop_window="Pop's Games"))
    SF.standin(c, True)
    assert c.enabled(SF.MULTI, SF.D5) is False and c.enabled(SF.PRODUCT, SF.D5) is False
    SF.cmd_setup(c, args())  # setup keeps it that way
    assert c.enabled(SF.MULTI, SF.D5) is False
    SF.standin(c, False)
    assert c.enabled(SF.MULTI, SF.D5) is True


def test_multi_page_keeps_the_up_next_window_out():
    c = live_obs()
    c.scenes[SF.MULTI] = [c._new(SF.WINDOW, True)]  # stray, as one once turned up in Intro
    SF.cmd_setup(c, args(desktop_window="Pop's Games"))
    assert c.enabled(SF.MULTI, SF.WINDOW) is False


# --- the close-up page and the hold still ---------------------------------------
def test_setup_builds_the_closeup_and_hold_pages_idempotently():
    c = live_obs()
    SF.cmd_setup(c, args())
    SF.cmd_setup(c, args())
    # webcam over the TG-7, each camera's (hidden) black panel right over it
    assert c.order(SF.CLOSEUP) == [SF.BG, SF.TG7, "TG-7 Down", SF.WEBCAM, "Webcam Down", SF.MIC]
    assert c.kinds[SF.TG7] == "v4l2_input" and c.settings[SF.TG7] == {"device_id": "/dev/video11"}
    box = c.transforms[(SF.CLOSEUP, SF.TG7)]
    # below the webcam strip, cover-cropped
    assert (box["boundsWidth"], box["boundsHeight"]) == (SF.W, SF.H - SF.TG7_TOP)
    assert box["positionY"] == SF.TG7_TOP + (SF.H - SF.TG7_TOP) / 2 and box["cropToBounds"] is True
    assert c.transforms[(SF.CLOSEUP, SF.WEBCAM)] == SF.WEBCAM_BOX
    # Hold: the still under the live webcam strip, which lands where it sat in the still
    assert c.order(SF.HOLD) == [SF.HOLD_FRAME, SF.WEBCAM, "Webcam Down", SF.MIC]
    assert c.kinds[SF.HOLD_FRAME] == "image_source"
    assert c.transforms[(SF.HOLD, SF.WEBCAM)] == SF.WEBCAM_BOX
    assert c.transforms[(SF.HOLD, SF.HOLD_FRAME)]["boundsWidth"] == SF.W
    assert all(c.enabled(SF.HOLD, s) and c.enabled(SF.CLOSEUP, s) for s in (SF.WEBCAM, SF.MIC))


def test_setup_turns_the_tg7_bounds_with_its_rotation():
    c = live_obs()
    SF.cmd_setup(c, args(tg7_rotation=270))
    box = c.transforms[(SF.CLOSEUP, SF.TG7)]
    assert box["rotation"] == 270 and (box["boundsWidth"], box["boundsHeight"]) == (SF.H - SF.TG7_TOP, SF.W)


def test_setup_swaps_the_tg7_source_when_the_feed_output_changes(monkeypatch):
    c = live_obs()
    monkeypatch.setattr(SF, "tg7_source", lambda: ("ffmpeg_source", {"input": "udp://127.0.0.1:5011"}))
    SF.cmd_setup(c, args())
    assert c.kinds[SF.TG7] == "ffmpeg_source"
    # the loopback device appears (sudo modprobe): the next setup makes it a v4l2 camera
    monkeypatch.setattr(SF, "tg7_source", lambda: ("v4l2_input", {"device_id": "/dev/video11"}))
    SF.cmd_setup(c, args())
    assert c.kinds[SF.TG7] == "v4l2_input" and c.count(SF.CLOSEUP, SF.TG7) == 1


def test_tg7_source_follows_the_feed_output(monkeypatch):
    monkeypatch.setattr(SF.tg7cam(), "default_out", lambda: "/dev/video11")
    assert REAL_TG7_SOURCE() == ("v4l2_input", {"device_id": "/dev/video11"})
    monkeypatch.setattr(SF.tg7cam(), "default_out", lambda: "udp://127.0.0.1:5011")
    kind, settings = REAL_TG7_SOURCE()
    assert kind == "ffmpeg_source" and settings["input"] == "udp://127.0.0.1:5011"
    assert settings["is_local_file"] is False and settings["input_format"] == "mpegts"


def test_setup_keeps_the_window_out_of_the_hold_page():
    c = live_obs()
    SF.cmd_setup(c, args())
    c.scenes[SF.HOLD].append(c._new(SF.WINDOW, True))  # added by hand in OBS
    SF.cmd_setup(c, args())
    assert c.enabled(SF.HOLD, SF.WINDOW) is False


def test_tg7_feed_start_is_detached_and_stop_asks_it_to_finish(monkeypatch):
    started, killed, pids = [], [], []
    monkeypatch.setattr(SF, "tg7_pids", lambda: list(pids))
    monkeypatch.setattr(SF.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    assert SF.tg7_start() is True
    (cmd, kw), = started
    assert cmd[1:] == [str(SF.TG7_CAM), "stream", "--size", SF.TG7_SIZE] and kw["start_new_session"]
    pids.append(4242)
    assert SF.tg7_start() is False and len(started) == 1  # one feed only

    def kill(pid, sig):
        killed.append((pid, sig))
        pids.clear()  # it stops the camera's live view and exits on Ctrl+C
    monkeypatch.setattr(SF.os, "kill", kill)
    SF.tg7_stop()
    assert killed == [(4242, 2)]


def test_tg7_pattern_matches_the_feed_and_nothing_else():
    import re
    pat = re.compile(SF.TG7_PATTERN)
    assert pat.search("/usr/bin/python3 /home/russ/EBAYBIZ/tools/live/tg7cam stream --size 0640x0480")
    assert pat.search("python3 /home/russ/bin/tg7cam stream")
    assert not pat.search("python3 /home/russ/bin/tg7cam shoot")
    assert not pat.search("bash -c tg7cam stream")


# --- the camera watch: a black panel over a camera that drops -------------------
PANELS = list(SF.DOWN.values())


def built():
    """The show as setup leaves it, the desktop and multi pages included."""
    c = live_obs()
    SF.cmd_setup(c, args(desktop_window="Pop's Games"))
    return c


def test_setup_puts_a_hidden_black_panel_over_each_camera_on_every_page_that_shows_it():
    c = built()
    SF.cmd_setup(c, args())  # twice: nothing doubles
    shows = {SF.D5: {SF.PRODUCT, SF.MULTI}, SF.TG7: {SF.CLOSEUP},
             SF.WEBCAM: {SF.PRODUCT, SF.MULTI, SF.CLOSEUP, SF.UPNEXT, SF.DESKTOP, SF.HOLD, SF.WEBCAM_ONLY}}
    for cam, panel in SF.DOWN.items():
        assert c.kinds[panel] == "color_source_v3" and c.settings[panel]["color"] == 0xFF000000
        assert {s for s in c.scenes if panel in c.order(s)} == shows[cam]
        for scene in shows[cam]:
            order = c.order(scene)
            assert order.count(panel) == 1 and c.enabled(scene, panel) is False
            assert order.index(panel) == order.index(cam) + 1  # directly over its camera
            box = c.transforms[(scene, panel)]
            assert (box["positionX"], box["positionY"], box["boundsWidth"], box["boundsHeight"]) == \
                SF.SLOTS[scene][cam]
            assert box["alignment"] == 5 and box["boundsType"] == "OBS_BOUNDS_STRETCH"
    # the webcam strip still covers the D5's panel; nothing in Intro
    assert c.order(SF.PRODUCT) == [SF.D5, "D5 Down", SF.WEBCAM, "Webcam Down", SF.WINDOW]
    assert c.transforms[(SF.WEBCAM_ONLY, "Webcam Down")]["boundsHeight"] == 1350
    assert c.order(SF.WEBCAM_ONLY)[-2:] == [SF.WEBCAM, "Webcam Down"]
    assert not set(PANELS) & set(c.order(SF.INTRO))
    assert not any(on for _, src, on in c.writes if src in PANELS)  # never a flash of black


def test_a_panel_below_its_camera_moves_to_directly_above_it():
    # moved by hand in OBS: each panel under its camera, more items above
    c = SceneObs({SF.PRODUCT: [("D5 Down", False), (SF.D5, True), ("Webcam Down", False),
                               (SF.WEBCAM, True), (SF.WINDOW, False)]}, inputs=PANELS)
    SF._seat_panels(c, SF.PRODUCT)
    assert c.order(SF.PRODUCT) == [SF.D5, "D5 Down", SF.WEBCAM, "Webcam Down", SF.WINDOW]


def test_the_standin_keeps_the_panels_over_their_cameras_and_turns_the_d5s_off():
    c = built()
    SF.set_panel(c, SF.D5, True)
    SF.standin(c, True)
    assert c.order(SF.PRODUCT)[:5] == [SF.STANDIN, SF.D5, "D5 Down", SF.WEBCAM, "Webcam Down"]
    assert c.enabled(SF.PRODUCT, "D5 Down") is False and c.enabled(SF.MULTI, "D5 Down") is False
    SF.standin(c, False)
    assert c.order(SF.PRODUCT)[:5] == [SF.STANDIN, SF.D5, "D5 Down", SF.WEBCAM, "Webcam Down"]


def test_setup_keeps_each_panel_as_it_was_but_the_standin_wins():
    c = built()
    SF.set_panel(c, SF.D5, True)
    SF.set_panel(c, SF.WEBCAM, True)
    SF.cmd_setup(c, args())
    for scene in (SF.PRODUCT, SF.MULTI):
        assert c.enabled(scene, "D5 Down") and c.enabled(scene, "Webcam Down")
    assert c.enabled(SF.WEBCAM_ONLY, "Webcam Down") and not c.enabled(SF.CLOSEUP, "TG-7 Down")
    c.scenes[SF.PRODUCT].append(c._new(SF.STANDIN, True))  # stand-in on, D5 Down switched on by hand
    c.inputs.add(SF.STANDIN)
    SF.cmd_setup(c, args())
    assert not c.enabled(SF.PRODUCT, "D5 Down") and not c.enabled(SF.MULTI, "D5 Down")
    assert c.enabled(SF.PRODUCT, "Webcam Down")


def moving(tag):
    return [f"{tag}{i}" for i in range(300)]


class Watch:
    """A built show, the cameras as the watch sees them (both feeds running,
    the D5's frames fresh, every picture moving) and the watch's clock."""

    def __init__(self, monkeypatch, tmp_path):
        self.c = built()
        self.c.writes.clear()
        self.c.pictures = {SF.D5: moving("d5-"), SF.WEBCAM: moving("cam-"), SF.TG7: moving("tg7-")}
        self.snap = tmp_path / "d5100_latest.jpg"
        self.snap.write_bytes(b"jpeg")
        self.feed, self.tg7, self.fresh = [42], [7], True
        monkeypatch.setattr(SF, "feed_pids", lambda: list(self.feed))
        monkeypatch.setattr(SF, "feed_snaps", lambda pids: [self.snap])
        monkeypatch.setattr(SF, "tg7_pids", lambda: list(self.tg7))
        self.mem, self.t = {}, time.time()

    def tick(self, n=1):
        for _ in range(n):
            if self.fresh:
                os.utime(self.snap, (self.t, self.t))  # the feed's 2 s frame
            cams = SF.watch_tick(self.c, self.mem, self.t)
            self.t += 1
        return cams

    def black(self, scene, cam):
        return self.c.enabled(scene, SF.DOWN[cam])

    def panel_writes(self, cam):
        return [(scene, on) for scene, src, on in self.c.writes if src == SF.DOWN[cam]]


@pytest.fixture
def watch(monkeypatch, tmp_path):
    return Watch(monkeypatch, tmp_path)


def test_three_looks_with_no_d5_picture_black_out_its_slot_on_every_page(watch):
    watch.c.pictures[SF.D5] = [None]
    watch.tick(2)
    assert not watch.black(SF.PRODUCT, SF.D5)
    cams = watch.tick()
    assert cams["d5"] == {"state": "down", "reason": "OBS has no picture", "since": watch.t - 1}
    assert watch.black(SF.PRODUCT, SF.D5) and watch.black(SF.MULTI, SF.D5)
    assert {src for _, src, _ in watch.c.writes} == {"D5 Down"}  # the other panels untouched


def test_two_bad_looks_and_a_good_one_never_black_out(watch):
    watch.c.pictures[SF.D5] = ["a", "a", "a", "b", "b", "b", "c"]  # first, bad, bad, ok, bad, bad, ok
    watch.tick(7)
    assert watch.panel_writes(SF.D5) == []


def test_alternating_looks_never_flip_the_panel_either_way(watch):
    flicker = [p for i in range(30) for p in (f"x{i}", f"x{i}")]  # ok, bad, ok, bad, ...
    watch.c.pictures[SF.D5] = list(flicker)
    assert watch.tick(40)["d5"]["state"] == "unknown" and watch.panel_writes(SF.D5) == []
    watch.c.pictures[SF.D5] = [None]
    watch.tick(3)
    assert watch.black(SF.PRODUCT, SF.D5)
    watch.c.pictures[SF.D5] = list(flicker)
    watch.tick(40)
    assert watch.black(SF.PRODUCT, SF.D5)


def test_coming_back_takes_five_moving_looks(watch, capsys):
    watch.c.pictures[SF.D5] = ["a"] * 4 + moving("m")  # first look, then frozen 3 times
    watch.tick(4)
    assert watch.black(SF.PRODUCT, SF.D5)
    watch.tick(4)
    assert watch.black(SF.PRODUCT, SF.D5)
    cams = watch.tick()
    assert not watch.black(SF.PRODUCT, SF.D5) and not watch.black(SF.MULTI, SF.D5)
    assert cams["d5"]["state"] == "live"
    log = capsys.readouterr().out
    assert "D5100 down (picture frozen in OBS (the camera is sending)); black on" in log
    assert "D5100 live; black off after 5s" in log


def test_a_frozen_picture_blacks_out_and_a_moving_one_never_does(watch):
    watch.c.pictures[SF.WEBCAM] = ["same"]
    watch.tick(3)
    assert not watch.black(SF.PRODUCT, SF.WEBCAM)
    cams = watch.tick()
    assert cams["webcam"]["state"] == "down" and cams["webcam"]["reason"] == "picture frozen in OBS"
    assert all(watch.black(s, SF.WEBCAM) for s in SF.SLOTS)
    cams = watch.tick(20)
    assert cams["d5"]["state"] == cams["tg7"]["state"] == "live"
    assert watch.panel_writes(SF.D5) == watch.panel_writes(SF.TG7) == []


def test_the_d5_is_left_alone_while_busy_and_never_compared_with_before(watch, monkeypatch):
    watch.c.pictures[SF.D5] = ["same"]
    watch.tick()  # its picture before the refocus
    real = SF.exclusive
    monkeypatch.setattr(SF, "exclusive", lambda: pytest.fail("the watch took the camera lock"))
    with real():  # a refocus, WB change or still holds the lock
        for _ in range(10):
            assert watch.tick()["d5"]["state"] == "busy"
    assert watch.panel_writes(SF.D5) == [] and watch.c.shots.count(SF.D5) == 1
    # the same picture after it is a first look, not "frozen" against the one from before
    watch.tick(3)
    assert not watch.black(SF.PRODUCT, SF.D5)
    watch.tick()
    assert watch.black(SF.PRODUCT, SF.D5)


def test_the_standin_wins_over_the_watch(watch):
    SF.standin(watch.c, True)
    SF.set_panel(watch.c, SF.D5, True)  # switched on by hand
    watch.c.shots.clear()
    cams = watch.tick(5)
    assert cams["d5"]["state"] == "standin"
    assert not watch.black(SF.PRODUCT, SF.D5) and not watch.black(SF.MULTI, SF.D5)
    assert SF.D5 not in watch.c.shots  # no screenshot of a camera that isn't on air


def test_a_stopped_feed_is_off_and_blacked_out(watch):
    watch.feed, watch.tg7 = [], []
    watch.tick(2)
    assert not watch.black(SF.PRODUCT, SF.D5)
    cams = watch.tick()
    assert (cams["d5"]["state"], cams["d5"]["reason"]) == ("off", "D5100 feed stopped")
    assert (cams["tg7"]["state"], cams["tg7"]["reason"]) == ("off", "TG-7 feed stopped")
    assert watch.black(SF.PRODUCT, SF.D5) and watch.black(SF.CLOSEUP, SF.TG7)
    assert SF.D5 not in watch.c.shots and SF.TG7 not in watch.c.shots


def test_the_watch_reopens_a_camera_with_frames_once_its_slot_is_black(watch):
    watch.c.pictures[SF.D5] = [None]  # the feed has frames, OBS has none
    watch.tick(3)
    assert watch.black(SF.PRODUCT, SF.D5) and watch.c.reopens == []  # not before the panel is on
    watch.tick()
    assert watch.c.reopens == [SF.D5]
    watch.tick(14)
    assert watch.c.reopens == [SF.D5]  # not again within 15 s
    watch.tick()
    assert watch.c.reopens == [SF.D5, SF.D5] and watch.black(SF.PRODUCT, SF.D5)


def test_no_reopen_without_frames_from_the_camera_or_while_busy(watch):
    watch.fresh = False
    os.utime(watch.snap, (watch.t - 60, watch.t - 60))
    cams = watch.tick(20)
    assert cams["d5"]["state"] == "down" and cams["d5"]["reason"].startswith("no frames from the camera for")
    assert watch.c.reopens == []
    watch.fresh = True
    watch.c.pictures[SF.D5] = [None]
    with SF.exclusive():
        watch.tick(20)
    assert watch.c.reopens == []


@pytest.mark.parametrize("marked", [-30, -90])  # seconds from now: after / before the last frame
def test_a_clock_reset_in_this_outage_is_the_d5s_reason(watch, marked):
    watch.fresh = False
    os.utime(watch.snap, (watch.t - 60, watch.t - 60))  # the last frame, a minute ago
    mark = SF.clock_marker(watch.snap)  # the feed script's note beside its frame
    mark.write_text("2026-10-22 01:02:03\t2011-01-01 00:00:05\n")
    os.utime(mark, (watch.t + marked, watch.t + marked))
    cams = watch.tick(3)
    assert cams["d5"]["state"] == "down" and watch.black(SF.PRODUCT, SF.D5)
    if marked > -60:
        assert cams["d5"]["reason"] == "clock reset (camera said 2011-01-01 00:00:05)"
        watch.fresh = True  # the feed set the clock and live view is back: a first look, then 5 good
        assert watch.tick(6)["d5"] == {"state": "live", "reason": "", "since": watch.t - 1}
        assert not watch.black(SF.PRODUCT, SF.D5)
    else:  # an old reset, from before frames last flowed
        assert cams["d5"]["reason"].startswith("no frames from the camera for")


def test_the_watch_reopens_a_frozen_webcam_only_when_its_device_is_there(watch, tmp_path):
    dev = tmp_path / "video0"
    dev.write_bytes(b"")
    watch.c.settings[SF.WEBCAM] = {"device_id": str(dev)}
    watch.c.pictures[SF.WEBCAM] = ["same"]
    watch.tick(5)  # first look, 3 frozen: black; then the reopen
    assert watch.c.reopens == [SF.WEBCAM]
    watch.mem.clear()
    SF.clear_panels(watch.c)
    watch.c.settings[SF.WEBCAM] = {"device_id": str(tmp_path / "video9")}  # unplugged
    cams = watch.tick(30)
    assert cams["webcam"]["reason"] == f"device {tmp_path / 'video9'} missing"
    assert watch.black(SF.PRODUCT, SF.WEBCAM) and watch.c.reopens == [SF.WEBCAM]


def test_no_tg7_source_leaves_the_other_cameras_judged(watch):
    watch.c.inputs.discard(SF.TG7)  # setup never ran with the TG-7
    watch.c.pictures[SF.D5] = [None]
    cams = watch.tick(6)
    assert (cams["tg7"]["state"], cams["tg7"]["reason"]) == ("unknown", "no TG-7 source in OBS; run setup")
    assert cams["d5"]["state"] == "down" and cams["webcam"]["state"] == "live"


def test_the_watch_clears_every_panel_when_it_ends(watch, monkeypatch):
    for cam in SF.DOWN:
        SF.set_panel(watch.c, cam, True)
    monkeypatch.setattr(SF, "connect", lambda: watch.c)
    seen = []

    def sleep(s):
        seen.append(json.loads(SF.CAMS_FILE.read_text()))
        raise KeyboardInterrupt  # Ctrl+C; `watch stop`'s SIGTERM raises the same
    monkeypatch.setattr(SF.time, "sleep", sleep)
    assert SF.main(["watch", "run"]) == 0
    assert seen[0]["pid"] == os.getpid() and set(seen[0]["cameras"]) == set(SF.CAM_IDS)
    assert not any(c["sceneItemEnabled"] for items in watch.c.scenes.values() for c in items
                   if c["sourceName"] in PANELS)
    assert not SF.CAMS_FILE.exists()
    with open(SF.WATCH_LOCK, "w") as f:  # the lock went with it
        SF._lock_nb(f)


def test_watch_stop_with_no_watch_running_still_clears_the_panels(watch, monkeypatch, capsys):
    for cam in SF.DOWN:
        SF.set_panel(watch.c, cam, True)
    monkeypatch.setattr(SF, "watch_pids", lambda: [])
    monkeypatch.setattr(SF.os, "kill", lambda *a: pytest.fail("signalled a process"))
    monkeypatch.setattr(SF, "connect", lambda: watch.c)
    assert SF.main(["watch", "stop"]) == 0
    assert not any(watch.black(s, cam) for s, slots in SF.SLOTS.items() for cam in slots)
    assert "every black panel off" in capsys.readouterr().out


def test_a_second_watch_exits_at_once(monkeypatch, capsys):
    monkeypatch.setattr(SF, "connect", lambda: pytest.fail("connected to OBS"))
    with open(SF.WATCH_LOCK, "w") as f:
        SF._lock_nb(f)  # a watch is running
        assert SF.main(["watch", "run"]) == 0
    assert "camera watch already running" in capsys.readouterr().out
