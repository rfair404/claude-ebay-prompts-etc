"""tests/test_show_music.py — background music under the live show:
tools/live/show_music.py (mix plan, OBS setup, fades) and webapp/pager.py's
/api/pager/music over it.

OBS is a fake that records volume, mute and media actions, and sleeps are
stubbed, so nothing here needs OBS, ffmpeg or the music files."""
import json
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
import show_music as SM  # noqa: E402
import webapp.server as server  # noqa: E402


class FakeObs:
    def __init__(self, has_music=True):
        self.inputs = {"Mic/Aux"} | ({SM.MUSIC} if has_music else set())
        self.muted, self.db, self.media = True, SM.LEVEL_DB, "OBS_MEDIA_STATE_PAUSED"
        self.cursor = 0
        self.scenes = {s: [] for s in [*SF.PAGES.values(), SF.HOLD]}
        self.filters: dict[str, dict] = {}
        self.log: list[tuple] = []  # (request, value) for volume / mute / media

    def call(self, req, data=None):
        data = data or {}
        if req == "GetInputList":
            return {"inputs": [{"inputName": n} for n in sorted(self.inputs)]}
        if req == "CreateInput":
            self.inputs.add(data["inputName"])
            self.scenes[data["sceneName"]].append(data["inputName"])
            self.settings = data["inputSettings"]
            self.muted, self.db, self.media = False, 0.0, SM.PLAYING  # OBS's defaults
            return {"sceneItemId": 1}
        if req == "SetInputSettings":
            self.settings = data["inputSettings"]
            return {}
        if req == "GetSceneList":
            return {"scenes": [{"sceneName": s} for s in self.scenes]}
        if req == "GetSceneItemList":
            return {"sceneItems": [{"sourceName": n, "sceneItemId": i}
                                   for i, n in enumerate(self.scenes[data["sceneName"]])]}
        if req == "CreateSceneItem":
            self.scenes[data["sceneName"]].append(data["sourceName"])
            return {"sceneItemId": len(self.scenes[data["sceneName"]])}
        if req == "GetSourceFilterList":
            return {"filters": [{"filterName": n} for n in self.filters]}
        if req in ("CreateSourceFilter", "SetSourceFilterSettings"):
            self.filters[data["filterName"]] = data["filterSettings"]
            return {}
        if req == "GetInputMute":
            return {"inputMuted": self.muted}
        if req == "SetInputMute":
            self.muted = data["inputMuted"]
            self.log.append(("mute", self.muted))
            return {}
        if req == "GetInputVolume":
            return {"inputVolumeDb": self.db}
        if req == "SetInputVolume":
            self.db = data["inputVolumeDb"]
            self.log.append(("db", round(self.db, 2)))
            return {}
        if req == "GetMediaInputStatus":
            return {"mediaState": self.media, "mediaCursor": self.cursor, "mediaDuration": 2212866}
        if req == "TriggerMediaInputAction":
            act = data["mediaAction"].rsplit("_", 1)[1]
            self.media = SM.PLAYING if act == "PLAY" else "OBS_MEDIA_STATE_PAUSED"
            self.log.append(("media", act))
            return {}
        raise AssertionError(f"unexpected request {req}")

    def close(self):
        pass


@pytest.fixture
def obs(tmp_path, monkeypatch):
    monkeypatch.setattr(SM.time, "sleep", lambda s: None)
    monkeypatch.setattr(SM, "MUSIC_DIR", tmp_path)
    return FakeObs()


# --- the file ----------------------------------------------------------------
def test_titles_drop_number_artist_and_doubled_extension():
    assert SM.title_of(Path("03 HoliznaCC0 - Blue Skies.mp3.mp3")) == "Blue Skies"
    assert SM.title_of(Path("22 HoliznaCC0 - Happy, but a little off.mp3")) == "Happy, but a little off"


def test_plan_overlaps_tracks_by_the_crossfade():
    order = SM.plan([(Path("1 A - One.mp3"), 100.0), (Path("2 A - Two.mp3"), 50.0),
                     (Path("3 A - Three.mp3"), 60.0)], xfade=3)
    assert [(t["title"], t["start_s"]) for t in order] == [("One", 0), ("Two", 97), ("Three", 144)]


def test_ffmpeg_levels_each_track_and_chains_crossfades():
    args = SM.ffmpeg_args([Path("a.mp3"), Path("b.mp3"), Path("c.mp3")], 200, Path("out.ogg"))
    graph = args[args.index("-filter_complex") + 1]
    assert graph.count("loudnorm=") == 3 and graph.count("acrossfade=") == 2
    assert "afade=t=out:st=197.00:d=3[out]" in graph
    assert args[-1] == "out.ogg" and "libopus" in args


def test_tracks_take_the_mp3_of_each_pair(tmp_path):
    d = tmp_path / "src" / "chill_beats" / "Chill Beats"
    d.mkdir(parents=True)
    for ext in ("mp3", "ogg"):
        (d / f"01 HoliznaCC0 - City.{ext}").touch()
    found = SM.tracks(tmp_path)
    assert [p.name for p, _ in found] == ["01 HoliznaCC0 - City.mp3"]
    assert found[0][1]["page"].startswith("https://opengameart.org/")


def test_license_record_names_source_license_and_tracks(tmp_path):
    pack = SM.PACKS["chill_beats.zip"]
    SM.write_license(tmp_path, [(Path("01 HoliznaCC0 - City.mp3"), pack)])
    text = (tmp_path / "LICENSE.md").read_text()
    assert pack["page"] in text and "CC0" in text and "- City" in text
    assert "Happy Lo-Fi" not in text  # a pack with no tracks in the mix isn't listed


def test_now_playing_follows_the_cursor_and_loops(tmp_path):
    (tmp_path / SM.TRACKLIST).write_text(json.dumps({"total_s": 200, "tracks": [
        {"title": "One", "start_s": 0}, {"title": "Two", "start_s": 97}, {"title": "Three", "start_s": 144}]}))
    assert SM.now_playing(50_000, tmp_path) == "One"
    assert SM.now_playing(150_000, tmp_path) == "Three"
    assert SM.now_playing(250_000, tmp_path) == "One"     # second time round the loop
    assert SM.now_playing(None, tmp_path) is None
    assert SM.now_playing(1000, tmp_path / "missing") is None


# --- OBS -----------------------------------------------------------------------
def test_setup_puts_one_muted_paused_source_in_every_page(obs, tmp_path):
    obs.inputs.discard(SM.MUSIC)
    (tmp_path / SM.MIX).touch()
    SM.setup(obs, tmp_path)
    assert all(SM.MUSIC in items for items in obs.scenes.values())  # Hold too: H never goes silent
    assert obs.muted and obs.db == SM.LEVEL_DB and obs.media != SM.PLAYING  # never on air from setup
    assert obs.settings["looping"] and not obs.settings["close_when_inactive"]
    assert obs.filters[SM.DUCK]["sidechain_source"] == SF.MIC
    SM.setup(obs, tmp_path)  # again: nothing doubled
    assert all(items.count(SM.MUSIC) == 1 for items in obs.scenes.values())


def test_setup_needs_the_built_file(obs, tmp_path):
    with pytest.raises(obsctl.ObsError, match="build"):
        SM.setup(obs, tmp_path)


def test_fade_in_unmutes_from_silence_and_ramps_to_the_fader(obs):
    obs.db = -14.0  # turned up in OBS: that's where the fade lands
    assert SM.set_on(obs, True) == {"on": True, "changed": True}
    assert obs.log[:3] == [("db", -100.0), ("mute", False), ("media", "PLAY")]
    ramp = [v for k, v in obs.log[3:] if k == "db"]
    assert len(ramp) == SM.FADE_STEPS and ramp == sorted(ramp)
    assert ramp[0] > SM.FLOOR_DB and ramp[-1] == -14.0
    assert SM.state(obs)["on"]


def test_fade_out_ramps_down_then_mutes_pauses_and_restores_the_fader(obs):
    obs.muted, obs.media = False, SM.PLAYING
    assert SM.set_on(obs, False) == {"on": False, "changed": True}
    ramp = [v for k, v in obs.log[:-3] if k == "db"]
    assert ramp == sorted(ramp, reverse=True) and ramp[-1] == SM.FLOOR_DB
    assert obs.log[-3:] == [("mute", True), ("media", "PAUSE"), ("db", SM.LEVEL_DB)]


def test_a_fade_cut_off_midway_doesnt_leave_the_level_low(obs):
    obs.db = -44.9 + -10  # below the floor: an interrupted fade-out
    SM.set_on(obs, True)
    assert obs.db == SM.LEVEL_DB


def test_already_on_is_a_no_op(obs):
    obs.muted, obs.media = False, SM.PLAYING
    assert SM.set_on(obs, True) == {"on": True, "changed": False}
    assert obs.log == []


def test_muted_but_playing_counts_as_off(obs):
    obs.media = SM.PLAYING  # muted by hand in OBS
    assert not SM.state(obs)["on"]


def test_no_source_is_not_ready_and_on_says_run_setup():
    fake = FakeObs(has_music=False)
    assert SM.state(fake) == {"ready": False, "on": False, "db": None, "track": None}
    with pytest.raises(obsctl.ObsError, match="setup"):
        SM.set_on(fake, True)


# --- /api/pager/music -----------------------------------------------------------
@pytest.fixture
def client(obs, monkeypatch):
    monkeypatch.setattr(SF, "connect", lambda: obs)
    with TestClient(server.app, base_url="http://127.0.0.1:8770") as c:
        yield c


def test_api_music_state_and_fades(client, obs):
    assert not SM.state(obs)["on"]  # GET /api/pager carries it: test_pager.py
    r = client.post("/api/pager/music", json={"on": True}).json()
    assert r["on"] and r["fade"] == {"on": True, "changed": True}
    r = client.post("/api/pager/music", json={"on": False}).json()
    assert not r["on"] and obs.muted


def test_api_music_without_setup_is_502(client, obs):
    obs.inputs.discard(SM.MUSIC)
    r = client.post("/api/pager/music", json={"on": True})
    assert r.status_code == 502 and "setup" in r.json()["detail"]
