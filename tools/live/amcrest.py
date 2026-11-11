#!/usr/bin/env python3
"""AMCREST — the Amcrest Wi-Fi camera (Dahua OEM) as an OBS Media Source.

    python tools/live/amcrest.py setup            # ask for the password once, add the source
    python tools/live/amcrest.py setup --password # ask again (password changed in the app)
    python tools/live/amcrest.py setup --sub      # the low-res sub stream instead of main
    python tools/live/amcrest.py url              # the RTSP URL, password redacted
    python tools/live/amcrest.py ptz info         # model and pan/tilt/zoom/preset caps
    python tools/live/amcrest.py ptz left|right|up|down [--secs 0.4] [--speed 4]
    python tools/live/amcrest.py ptz in|out       # zoom (only if the camera has optical zoom)
    python tools/live/amcrest.py ptz stop
    python tools/live/amcrest.py ptz goto 1 | save 1   # presets
    python tools/live/amcrest.py ptz dock         # buttons for an OBS Custom Browser Dock

`setup` stores the camera login in ~/.ebaybiz/amcrest.json (mode 600, outside
the repo, which is public) and creates or updates the OBS input "Amcrest" in
its own scene "Amcrest camera", so it never lands on a show page or changes what is
on air. Add it to a page in OBS, or `obsctl.py fit Amcrest --scene "Amcrest camera"`.

The source is ffmpeg over RTSP/TCP (no UDP packet loss on Wi-Fi), with
hardware decode, a 1 MB buffer, low-delay flags and a 2 s reconnect.

The file holds {"host", "user", "password", "port", "channel", "subtype"};
AMCREST_HOST / AMCREST_USER / AMCREST_PASSWORD override it. The host defaults
to 192.168.1.232; give the camera a DHCP reservation on the router so it
stays there, or change "host" in the file. The RTSP URL (with the password)
also ends up in OBS's own scene collection, under the Flatpak config dir;
never copy that collection back into tools/live/obs/scenes/.

PTZ goes over the camera's HTTP CGI (Dahua/Amcrest HTTP API, ptz.cgi), with
the same login. The IP2M-841B pans and tilts and has numbered presets; its
lens is fixed, so `in`/`out` refuse unless the camera reports zoom. Moves are
timed nudges (start, wait, stop), so a lost click never leaves it turning.
`ptz dock` serves a button page on 127.0.0.1 only; the login stays in this
process and never reaches the page or OBS.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402

NAME = "Amcrest"   # the OBS input
SCENE = "Amcrest camera"  # its own scene (OBS scenes and inputs share one namespace): setting it up never touches a show page
DEFAULTS = {"host": "192.168.1.232", "user": "admin", "port": 554, "channel": 1, "subtype": 0}
ENV = {"host": "AMCREST_HOST", "user": "AMCREST_USER", "password": "AMCREST_PASSWORD"}


def config_path() -> Path:
    """~/.ebaybiz/amcrest.json, next to the user-global config.yaml (lib/config.py)."""
    if os.environ.get("AMCREST_CONFIG"):
        return Path(os.environ["AMCREST_CONFIG"])
    return Path.home() / ".ebaybiz" / "amcrest.json"


def load(path: Path | None = None) -> dict:
    path = path or config_path()
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(path.read_text()))
    except FileNotFoundError:
        pass
    except ValueError as e:
        raise obsctl.ObsError(f"{path} isn't valid JSON ({e})") from e
    for key, env in ENV.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def save(cfg: dict, path: Path | None = None) -> Path:
    """Write the login, readable by the owner only (created 600, never wider)."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(path, 0o600)
    return path


def rtsp_url(cfg: dict, subtype: int | None = None, redact: bool = False) -> str:
    """rtsp://user:pass@host:port/cam/realmonitor?channel=N&subtype=0|1.
    The login is percent-encoded, so a password with @ : / # still works."""
    if not cfg.get("password") and not redact:
        raise obsctl.ObsError(f"no Amcrest password; run `python {sys.argv[0]} setup`")
    sub = cfg["subtype"] if subtype is None else subtype
    if int(sub) not in (0, 1):
        raise obsctl.ObsError(f"subtype must be 0 (main) or 1 (sub), not {sub!r}")
    pw = "<redacted>" if redact else quote(str(cfg["password"]), safe="")
    return (f"rtsp://{quote(str(cfg['user']), safe='')}:{pw}@{cfg['host']}:{int(cfg['port'])}"
            f"/cam/realmonitor?channel={int(cfg['channel'])}&subtype={int(sub)}")


def source_settings(url: str) -> dict:
    """ffmpeg_source settings: RTSP over TCP, hw decode, low delay, reconnect."""
    return {"is_local_file": False, "input": url, "input_format": "rtsp",
            "hw_decode": True, "buffering_mb": 1, "reconnect_delay_sec": 2,
            "restart_on_activate": False, "close_when_inactive": False,
            "clear_on_media_end": False,
            "ffmpeg_options": "rtsp_transport=tcp fflags=nobuffer flags=low_delay"}


def add_source(c, url: str) -> str:
    """Create or update the Amcrest input in its own scene; returns what it did."""
    if SCENE not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": SCENE})
    have = {i["inputName"]: i.get("inputKind") for i in c.call("GetInputList")["inputs"]}
    settings = source_settings(url)
    if NAME in have and have[NAME] != "ffmpeg_source":
        raise obsctl.ObsError(f"an OBS input named {NAME!r} exists but is {have[NAME]}; rename it first")
    if NAME in have:
        c.call("SetInputSettings", {"inputName": NAME, "inputSettings": settings})
        return "updated"
    c.call("CreateInput", {"sceneName": SCENE, "inputName": NAME,
                           "inputKind": "ffmpeg_source", "inputSettings": settings})
    return "added"


def cmd_setup(a) -> None:
    cfg = load()
    if a.password or not cfg.get("password"):
        pw = getpass.getpass(f"Amcrest password for {cfg['user']}@{cfg['host']}: ")
        if not pw:
            raise obsctl.ObsError("no password entered; nothing saved")
        stored = load_file_only()
        stored["password"] = pw
        print(f"saved to {save(stored)}")
        cfg["password"] = pw
    url = rtsp_url(cfg, 1 if a.sub else None)
    host = os.environ.get("OBS_WS_HOST", "127.0.0.1")
    port = int(os.environ.get("OBS_WS_PORT", "4455"))
    c = obsctl.ObsClient(host, port, obsctl.load_password())
    try:
        did = add_source(c, url)
    finally:
        c.close()
    print(f"{NAME} {did} in scene {SCENE!r}: {rtsp_url(cfg, 1 if a.sub else None, redact=True)}")


def load_file_only(path: Path | None = None) -> dict:
    """What the file holds (no env overrides), so saving never writes env values in."""
    path = path or config_path()
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def cmd_url(a) -> None:
    print(rtsp_url(load(), 1 if a.sub else None, redact=True))


# --- PTZ over the HTTP CGI ---------------------------------------------------

MOVES = {"left": "Left", "right": "Right", "up": "Up", "down": "Down",
         "in": "ZoomTele", "out": "ZoomWide"}
DOCK_PORT = 8457
DOCK_HTML = Path(__file__).resolve().parent / "amcrest_dock.html"


def _opener(cfg: dict):
    """Basic or Digest, whichever the camera asks for (old firmware: Basic)."""
    if not cfg.get("password"):
        raise obsctl.ObsError(f"no Amcrest password; run `python {sys.argv[0]} setup`")
    pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, f"http://{cfg['host']}/", str(cfg["user"]), str(cfg["password"]))
    return urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm),
                                       urllib.request.HTTPBasicAuthHandler(pm))


def cgi(cfg: dict, script: str, params: dict, timeout: float = 5) -> str:
    """GET /cgi-bin/<script>?<params>; the body text. One try per call: a wrong
    password is never retried (the camera locks the account after a few)."""
    url = f"http://{cfg['host']}/cgi-bin/{script}?{urlencode(params)}"
    try:
        with _opener(cfg).open(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise obsctl.ObsError(
                f"camera {cfg['host']} refused the login for {cfg['user']!r}; run "
                f"`python {sys.argv[0]} setup --password` with the password of the camera's "
                f"web page (http://{cfg['host']}/)") from e
        raise obsctl.ObsError(f"camera {script} {params.get('action')}: HTTP {e.code}") from e
    except OSError as e:
        raise obsctl.ObsError(f"camera {cfg['host']} unreachable ({e})") from e
    if body.strip().lower().startswith("error"):
        raise obsctl.ObsError(f"camera {script} {params.get('action')}: {body.strip()}")
    return body


def parse_kv(body: str) -> dict:
    """'caps.Pan=true\r\ncaps.Zoom=false' -> {'Pan': 'true', 'Zoom': 'false'}."""
    out = {}
    for line in body.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip().split(".", 1)[-1]] = v.strip()
    return out


def ptz_channel(cfg: dict) -> int:
    """ptz.cgi counts channels from 0; the RTSP stream counts from 1. Channel 1
    sent to ptz.cgi answers OK and moves nothing (IP2M-841B, 2026-11-11)."""
    return int(cfg["channel"]) - 1


def ptz_caps(cfg: dict) -> dict:
    return parse_kv(cgi(cfg, "ptz.cgi", {"action": "getCurrentProtocolCaps",
                                         "channel": ptz_channel(cfg)}))


def has_zoom(caps: dict) -> bool:
    return caps.get("Zoom", "").lower() == "true"


def ptz_info(cfg: dict) -> str:
    model = parse_kv(cgi(cfg, "magicBox.cgi", {"action": "getDeviceType"})).get("type", "?")
    caps = ptz_caps(cfg)
    yes = lambda k: "yes" if caps.get(k, "").lower() == "true" else "no"  # noqa: E731
    # "Tile" is the camera's own spelling of tilt
    return (f"{model}: pan {yes('Pan')}, tilt {yes('Tile')}, optical zoom {yes('Zoom')}, "
            f"presets {caps.get('PresetMin', '?')}-{caps.get('PresetMax', '?')}")


def _action(cfg: dict, action: str, code: str, arg2: int) -> None:
    cgi(cfg, "ptz.cgi", {"action": action, "channel": ptz_channel(cfg), "code": code,
                         "arg1": 0, "arg2": int(arg2), "arg3": 0})


def ptz_move(cfg: dict, direction: str, secs: float = 0.4, speed: int = 4, sleep=time.sleep) -> None:
    """A nudge: start, wait secs, stop. Stop is sent even if the wait is interrupted."""
    if direction not in MOVES:
        raise obsctl.ObsError(f"unknown move {direction!r}; one of {', '.join(MOVES)}")
    if not 1 <= int(speed) <= 8:
        raise obsctl.ObsError("speed is 1 (slow) to 8 (fast)")
    if not 0 < float(secs) <= 5:
        raise obsctl.ObsError("secs is more than 0 and at most 5")
    if direction in ("in", "out") and not has_zoom(ptz_caps(cfg)):
        raise obsctl.ObsError("this camera has no optical zoom (fixed lens); "
                              "crop in OBS instead")
    code = MOVES[direction]
    _action(cfg, "start", code, speed)
    try:
        sleep(float(secs))
    finally:
        _action(cfg, "stop", code, 0)


def ptz_stop(cfg: dict) -> None:
    for code in ("Left", "Up"):  # stop needs a code; either halts pan and tilt
        _action(cfg, "stop", code, 0)


def ptz_preset(cfg: dict, op: str, n: int) -> None:
    if not 1 <= int(n) <= 255:
        raise obsctl.ObsError("preset number is 1 to 255")
    _action(cfg, "start", {"goto": "GotoPreset", "save": "SetPreset"}[op], n)


def dock_request(cfg: dict, req: dict) -> str:
    """One dock button press: {"cmd": "left"|...|"stop"|"goto"|"save"|"info", "n": N}."""
    cmd = str(req.get("cmd", ""))
    if cmd in MOVES:
        ptz_move(cfg, cmd, float(req.get("secs", 0.4)), int(req.get("speed", 4)))
        return cmd
    if cmd == "stop":
        ptz_stop(cfg)
        return "stopped"
    if cmd in ("goto", "save"):
        ptz_preset(cfg, cmd, int(req.get("n", 0)))
        return f"{cmd} {int(req['n'])}"
    if cmd == "info":
        return ptz_info(cfg)
    raise obsctl.ObsError(f"unknown command {cmd!r}")


def dock_handler(cfg: dict):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, DOCK_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # The custom header makes any other site's request a CORS preflight,
            # which this server never answers: only the dock page can drive it.
            if self.path != "/ptz" or self.headers.get("X-Amcrest-Dock") != "1":
                self._send(403, b"forbidden", "text/plain")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                msg, code = dock_request(cfg, json.loads(self.rfile.read(n) or b"{}")), 200
            except (obsctl.ObsError, ValueError, KeyError, TypeError) as e:
                msg, code = str(e), 400
            self._send(code, json.dumps({"ok": code == 200, "msg": msg}).encode(), "application/json")

        def log_message(self, fmt, *args):  # quiet; errors go back to the page
            pass
    return Handler


def cmd_ptz(a) -> None:
    cfg = load()
    if a.op == "info":
        print(ptz_info(cfg))
    elif a.op in MOVES:
        ptz_move(cfg, a.op, a.secs, a.speed)
    elif a.op == "stop":
        ptz_stop(cfg)
    elif a.op in ("goto", "save"):
        if a.n is None:
            raise obsctl.ObsError(f"ptz {a.op} needs a preset number, e.g. `ptz {a.op} 1`")
        ptz_preset(cfg, a.op, a.n)
        print(f"preset {a.n} {'reached' if a.op == 'goto' else 'saved'}")
    elif a.op == "dock":
        _opener(cfg)  # fail now if there is no password, not on the first click
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), dock_handler(cfg))
        print(f"Amcrest dock on http://127.0.0.1:{a.port}/ (OBS: Docks > Custom Browser Docks); "
              "Ctrl+C stops it")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="store the login (asks once) and add the OBS source")
    s.add_argument("--password", action="store_true", help="ask for the password again")
    s.add_argument("--sub", action="store_true", help="use the sub stream (subtype=1)")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("url", help="print the RTSP URL, password redacted")
    s.add_argument("--sub", action="store_true")
    s.set_defaults(fn=cmd_url)
    s = sub.add_parser("ptz", help="pan/tilt/zoom, presets, and the OBS dock")
    s.add_argument("op", choices=["info", *MOVES, "stop", "goto", "save", "dock"])
    s.add_argument("n", nargs="?", type=int, help="preset number for goto/save")
    s.add_argument("--secs", type=float, default=0.4, help="how long a nudge runs (default 0.4)")
    s.add_argument("--speed", type=int, default=4, help="1 slow to 8 fast (default 4)")
    s.add_argument("--port", type=int, default=DOCK_PORT, help=f"dock port (default {DOCK_PORT})")
    s.set_defaults(fn=cmd_ptz)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except obsctl.ObsError as e:
        print(f"amcrest: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
