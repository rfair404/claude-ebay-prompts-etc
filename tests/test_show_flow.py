"""tests/test_show_flow.py — tools/live/show_flow.py's `setup`, window pick
and feed status, against a fake OBS that keeps real scene item stacks.

Nothing here needs OBS, the D5100, gphoto2 or X: the window's live title and
the feed's processes are stubbed. Pager switching is in tests/test_pager.py."""
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


class SceneObs:
    """Scenes as stacks, bottom first (OBS's sceneItemIndex order); a new
    scene item lands on top and enabled, as in OBS."""

    def __init__(self, scenes=None, inputs=()):
        self.ids = iter(range(1, 1000))
        self.scenes = {}
        for name, items in (scenes or {}).items():
            self.scenes[name] = [self._new(src, en) for src, en in items]
        self.inputs = {SF.D5, SF.WEBCAM, SF.WINDOW, SF.MIC, *inputs}
        self.windows = [{"itemName": "Pop's Games | eBay Stores — Mozilla Firefox",
                         "itemValue": "111\r\nPop's Games | eBay Stores — Mozilla Firefox\r\nfirefox"}]
        self.settings = {}
        self.transforms = {}
        self.program = SF.INTRO

    def _new(self, src, enabled=True):
        return {"sourceName": src, "sceneItemId": next(self.ids), "sceneItemEnabled": enabled}

    def _find(self, scene, item_id):
        return next(i for i in self.scenes[scene] if i["sceneItemId"] == item_id)

    def call(self, req, d=None):
        d = d or {}
        if req == "GetSceneCollectionList":
            return {"currentSceneCollectionName": SF.COLLECTION}
        if req == "GetInputList":
            return {"inputs": [{"inputName": n} for n in sorted(self.inputs)]}
        if req == "GetInputPropertiesListPropertyItems":
            return {"propertyItems": self.windows}
        if req == "SetInputSettings":
            self.settings.setdefault(d["inputName"], {}).update(d["inputSettings"])
            return {}
        if req == "GetInputSettings":
            return {"inputSettings": self.settings.get(d["inputName"], {})}
        if req == "CreateInput":
            self.inputs.add(d["inputName"])
            it = self._new(d["inputName"])
            self.scenes[d["sceneName"]].append(it)
            return {"sceneItemId": it["sceneItemId"]}
        if req == "GetSceneList":
            return {"scenes": [{"sceneName": n} for n in self.scenes]}
        if req == "CreateScene":
            assert d["sceneName"] not in self.scenes
            self.scenes[d["sceneName"]] = []
            return {}
        if req == "GetSceneItemList":
            return {"sceneItems": [{**i, "sceneItemIndex": n}
                                   for n, i in enumerate(self.scenes[d["sceneName"]])]}
        if req == "CreateSceneItem":
            assert d["sourceName"] in self.inputs, d
            it = self._new(d["sourceName"])
            self.scenes[d["sceneName"]].append(it)
            return {"sceneItemId": it["sceneItemId"]}
        if req == "SetSceneItemTransform":
            self.transforms[(d["sceneName"], self._find(d["sceneName"], d["sceneItemId"])["sourceName"])] = \
                d["sceneItemTransform"]
            return {}
        if req == "SetSceneItemEnabled":
            self._find(d["sceneName"], d["sceneItemId"])["sceneItemEnabled"] = d["sceneItemEnabled"]
            return {}
        if req == "SetSceneItemIndex":
            stack = self.scenes[d["sceneName"]]
            it = self._find(d["sceneName"], d["sceneItemId"])
            stack.remove(it)
            stack.insert(d["sceneItemIndex"], it)
            return {}
        if req == "GetCurrentProgramScene":
            return {"currentProgramSceneName": self.program}
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
                              "desktop_crop_left": None, "d5_rotation": 270, **kw})


@pytest.fixture(autouse=True)
def no_x(monkeypatch):
    titles = {"111": STORE}
    monkeypatch.setattr(SF, "live_title", lambda v: titles.get((v or "").split("\r\n")[0]))
    monkeypatch.setattr(SF.time, "sleep", lambda s: None)
    return titles


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
    assert order[0] == SF.BG and order[-1] == SF.WEBCAM  # dark card under, webcam on top
    assert sorted(order) == sorted([SF.BG, SF.WEBCAM, SF.MIC])  # nothing doubled
    assert c.transforms[(SF.WEBCAM_ONLY, SF.WEBCAM)] == {
        "positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_OUTER",
        "boundsWidth": 1080, "boundsHeight": 1350, "cropToBounds": True}
    assert all(c.enabled(SF.WEBCAM_ONLY, s) for s in order)


def test_setup_creates_the_webcam_page_when_missing():
    c = live_obs()
    del c.scenes[SF.WEBCAM_ONLY]
    SF.cmd_setup(c, args())
    assert c.order(SF.WEBCAM_ONLY) == [SF.BG, SF.MIC, SF.WEBCAM]


def test_webcam_page_on_the_cli(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(SF, "connect", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(SF, "go", lambda c, page, refocus=True: seen.append((page, refocus)) or
                        {"page": page, "changed": True, "focused": None, "refocus_s": None})
    assert SF.main(["page", "webcam"]) == 0
    assert seen == [("webcam", True)] and "on air: webcam" in capsys.readouterr().out


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
