#!/usr/bin/env python3
"""AMCREST — the Amcrest Wi-Fi camera (Dahua OEM) as an OBS Media Source.

    python tools/live/amcrest.py setup            # ask for the password once, add the source
    python tools/live/amcrest.py setup --password # ask again (password changed in the app)
    python tools/live/amcrest.py setup --sub      # the low-res sub stream instead of main
    python tools/live/amcrest.py url              # the RTSP URL, password redacted

`setup` stores the camera login in ~/.ebaybiz/amcrest.json (mode 600, outside
the repo, which is public) and creates or updates the OBS input "Amcrest" in
its own scene "Amcrest", so it never lands on a show page or changes what is
on air. Add it to a page in OBS, or `obsctl.py fit Amcrest --scene Amcrest`.

The source is ffmpeg over RTSP/TCP (no UDP packet loss on Wi-Fi), with
hardware decode, a 1 MB buffer, low-delay flags and a 2 s reconnect.

The file holds {"host", "user", "password", "port", "channel", "subtype"};
AMCREST_HOST / AMCREST_USER / AMCREST_PASSWORD override it. The host defaults
to 192.168.1.232; give the camera a DHCP reservation on the router so it
stays there, or change "host" in the file. The RTSP URL (with the password)
also ends up in OBS's own scene collection, under the Flatpak config dir;
never copy that collection back into tools/live/obs/scenes/.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402

NAME = "Amcrest"   # the OBS input
SCENE = "Amcrest"  # its own scene: setting it up never touches a show page
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
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except obsctl.ObsError as e:
        print(f"amcrest: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
