"""tests/test_tg7cam.py — tools/live/tg7cam, the OM TG-7 over Wi-Fi, with the
camera's HTTP API stubbed (cgi / get_file). The command shapes are the ones
a real TG-7 (firmware 1.1) accepted on 2026-10-21."""
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("tg7cam", str(ROOT / "tools" / "live" / "tg7cam"))
T = importlib.util.module_from_spec(importlib.util.spec_from_loader("tg7cam", _loader))
_loader.exec_module(T)

TOOK = ('<?xml version="1.0"?><response><take>ok</take><affocus>ok</affocus>'
        '<afframepoint>0284x0204</afframepoint><afframesize>0072x0072</afframesize></response>')
IMGLIST = ("VER_100\r\n/DCIM/100OMSYS,ZZ210005.JPG,5791603,0,23893,40372\r\n"
           "/DCIM/100OMSYS,ZZ210006.JPG,12,0,23893,40400\r\n"
           "/DCIM/100OMSYS,ZZ210007.MOV,99,0,23893,40500\r\n")


class Cam:
    """Records every cgi call; answers from `replies` by command name."""

    def __init__(self, monkeypatch, replies=None):
        self.calls = []
        self.replies = replies or {}
        monkeypatch.setattr(T, "cgi", self.cgi)
        monkeypatch.setattr(T.time, "sleep", lambda s: None)

    def cgi(self, name, body=None, timeout=5, raw=False, **params):
        self.calls.append((name, params))
        r = self.replies.get(params.get("com") or name, "")
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "STATE_DIR", tmp_path / "cfg")
    monkeypatch.setattr(T, "STATE", tmp_path / "cfg" / "state.json")


def test_points_are_percent_of_the_live_view():
    assert T.point(50, 50) == "0320x0240"  # 640x480 default
    assert T.point(0, 100) == "0000x0479"
    assert T.point(-5, 150) == "0000x0479"  # clamped
    T.save_state(lvqty="1280x0960")
    assert T.point(50, 50) == "0640x0480"


def test_a_new_live_view_size_goes_through_playback(monkeypatch):
    cam = Cam(monkeypatch)
    T.rec_mode("1280x0960")
    assert cam.calls == [("switch_cammode", {"mode": "play"}),
                         ("switch_cammode", {"mode": "rec", "lvqty": "1280x0960"})]
    assert T.load_state()["lvqty"] == "1280x0960"


class Card(Cam):
    """A TG-7 whose card gains a JPG when starttake fires: `fires` False is a
    take the camera answers "ok" without shooting, `lag` how many listings
    pass before the new file shows up."""

    def __init__(self, monkeypatch, fires=True, lag=0):
        super().__init__(monkeypatch, {"starttake": TOOK})
        self.files = {"ZZ220001.JPG": b"\xff\xd8old\xff\xd9"}
        self.fires, self.lag, self.pending, self.fetched = fires, lag, None, []
        monkeypatch.setattr(T, "get_file", self.get_file)

    def cgi(self, name, body=None, timeout=5, raw=False, **params):
        r = super().cgi(name, body, timeout, raw, **params)
        if params.get("com") == "starttake" and self.fires:
            self.pending = ("ZZ220002.JPG", b"\xff\xd8new\xff\xd9")
        if name == "get_imglist":
            if self.pending and self.lag == 0:
                self.files.update([self.pending])
                self.pending = None
            elif self.pending:
                self.lag -= 1
            return "VER_100\r\n" + "".join(f"{T.DCIM},{n},{len(d)},0,23893,40372\r\n"
                                             for n, d in self.files.items())
        return r

    def get_file(self, path, timeout=60):
        self.fetched.append(path)
        return self.files[path.rsplit("/", 1)[1]]


def test_shoot_uses_starttake_not_exec_shutter_and_saves_the_new_photo(monkeypatch, tmp_path):
    cam = Card(monkeypatch)
    out, focus = T.shoot(25, 75, tmp_path / "shots")
    takes = [p for n, p in cam.calls if n == "exec_takemotion"]
    assert takes == [{"com": "starttake", "point": "0160x0359"}, {"com": "stoptake"}]
    assert not any(p.get("com") == "exec_shutter" for _, p in cam.calls)
    assert cam.fetched == ["/DCIM/100OMSYS/ZZ220002.JPG"]
    assert out.parent == tmp_path / "shots" and out.read_bytes() == b"\xff\xd8new\xff\xd9"
    assert out.name.startswith("TG7_") and focus == "ok"


def test_shoot_waits_for_the_photo_to_reach_the_card(monkeypatch, tmp_path):
    # 2026-10-21: right after a shot the file isn't on the card for a few seconds
    cam = Card(monkeypatch, lag=3)
    out, _ = T.shoot(to=tmp_path)
    assert out.read_bytes() == b"\xff\xd8new\xff\xd9"
    assert [n for n, _ in cam.calls].count("get_imglist") == 5  # before, then 3 misses and the hit


def test_a_take_that_didnt_fire_is_an_error_not_a_copy_of_the_last_photo(monkeypatch, tmp_path):
    # 2026-10-22: take=ok, but getlastjpg handed back the previous photo (byte-identical, twice)
    monkeypatch.setattr(T, "CARD_WAIT", 0)
    cam = Card(monkeypatch, fires=False)
    with pytest.raises(T.CamError, match="didn't take a new photo; the last one on the card is unchanged"):
        T.shoot(to=tmp_path)
    assert cam.fetched == [] and list(tmp_path.iterdir()) == []


def test_a_cut_short_download_is_an_error(monkeypatch, tmp_path):
    cam = Card(monkeypatch)
    real = cam.get_file
    monkeypatch.setattr(T, "get_file", lambda path, timeout=60: real(path)[:4])
    with pytest.raises(T.CamError, match="cut short"):
        T.shoot(to=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_shot_still_lets_go_of_the_shutter(monkeypatch, tmp_path):
    cam = Cam(monkeypatch, {"starttake": T.CamError("camera refused exec_takemotion: HTTP 520")})
    with pytest.raises(T.CamError, match="HTTP 520"):
        T.shoot(to=tmp_path)
    assert cam.calls[-1] == ("exec_takemotion", {"com": "stoptake"})
    assert list(tmp_path.iterdir()) == []


def test_a_shot_the_camera_didnt_take_is_an_error(monkeypatch, tmp_path):
    Cam(monkeypatch, {"starttake": TOOK.replace("<take>ok", "<take>ng")})
    with pytest.raises(T.CamError, match="didn't take the shot"):
        T.shoot(to=tmp_path)


def test_autofocus_is_a_half_press_at_the_point(monkeypatch):
    cam = Cam(monkeypatch)
    T.autofocus(10, 20)
    assert cam.calls == [("exec_takemotion", {"com": "takeready", "point": "0064x0096"})]


def test_light(monkeypatch):
    cam = Cam(monkeypatch)
    T.light(True)
    T.light(False)
    assert [p["switch"] for _, p in cam.calls] == ["on", "off"]


def test_sync_copies_only_new_or_partial_photos(monkeypatch, tmp_path):
    Cam(monkeypatch, {"get_imglist": IMGLIST})
    fetched = []
    monkeypatch.setattr(T, "get_file", lambda path: fetched.append(path) or b"x" * 12)
    (tmp_path / "ZZ210005.JPG").write_bytes(b"x" * 5791603)  # already here, same size
    got = T.sync(tmp_path)
    assert fetched == ["/DCIM/100OMSYS/ZZ210006.JPG"]  # not the one we have, not the movie
    assert [p.name for p in got] == ["ZZ210006.JPG"] and not list(tmp_path.glob("*.part"))
    assert T.sync(tmp_path) == [] and len(fetched) == 1  # second run: nothing new


def test_card_photos_parses_the_image_list(monkeypatch):
    Cam(monkeypatch, {"get_imglist": IMGLIST})
    assert T.card_photos() == [("ZZ210005.JPG", 5791603), ("ZZ210006.JPG", 12), ("ZZ210007.MOV", 99)]


def test_camera_errors_are_one_line_not_a_traceback(monkeypatch, capsys):
    Cam(monkeypatch, {"takeready": T.Unreachable("can't reach the camera at 192.168.0.10")})
    assert T.main(["af"]) == 1
    assert capsys.readouterr().err.startswith("tg7cam: can't reach the camera")


def test_stream_defaults_to_udp_without_the_loopback_device(monkeypatch):
    monkeypatch.setattr(T.os.path, "exists", lambda p: False)
    assert T.default_out() == T.DEFAULT_UDP
    monkeypatch.setattr(T.os.path, "exists", lambda p: p == T.DEFAULT_DEV)
    assert T.default_out() == T.DEFAULT_DEV


def test_stream_waits_for_an_unreachable_camera(monkeypatch, capsys):
    tries = []

    def rec_mode(size):
        tries.append(size)
        if len(tries) < 3:
            raise T.Unreachable("can't reach the camera")
    cam = Cam(monkeypatch)
    monkeypatch.setattr(T, "rec_mode", rec_mode)
    T.start_liveview("0640x0480", 40000)
    assert len(tries) == 3 and cam.calls == [("exec_takemisc", {"com": "startliveview", "port": 40000})]
    assert capsys.readouterr().err.count("waiting for the camera") == 1


def test_connect_pins_never_default_on_the_connection_actually_used(monkeypatch, capsys):
    runs = []

    def run(cmd, **kw):
        runs.append(cmd)
        out = {"-o": "default via 192.168.1.1 dev enx4c56 proto dhcp\n",
               "-t": "enx4c56:ethernet\nwlp0s20f3:wifi\n",
               "-g": "TG-7-P-BJPB37542\n"}.get(cmd[1], "")
        return SimpleNamespace(stdout=out, returncode=0)
    monkeypatch.setattr(T.subprocess, "run", run)
    monkeypatch.setattr(T.shutil, "which", lambda n: "/usr/bin/nmcli")
    T.cmd_connect(SimpleNamespace(ssid="034096", ifname=None, force=False))
    modify = next(c for c in runs if c[1:3] == ["connection", "modify"])
    assert modify[3] == "TG-7-P-BJPB37542" and "ipv4.never-default" in modify
