#!/usr/bin/env python3
"""OBSCTL — drive OBS Studio from the shell over obs-websocket v5 (no GUI).

Targets the Flatpak OBS (docs/live-streaming-setup.md) with its websocket
server on (Tools > WebSocket Server Settings, port 4455). This is the remote:
every command is one short connection, prints JSON or a short status line,
and exits non-zero on an OBS error.

    python tools/live/obsctl.py launch      # start OBS with no window (tray only)
    python tools/live/obsctl.py quit        # clean shutdown (refuses while live/recording)
    python tools/live/obsctl.py status
    python tools/live/obsctl.py scenes
    python tools/live/obsctl.py scene "Wide"
    python tools/live/obsctl.py inputs
    python tools/live/obsctl.py shot program -o /tmp/program.jpg --width 640
    python tools/live/obsctl.py shot "Video Capture Device (V4L2)"
    python tools/live/obsctl.py fit Webcam [--cover] [--scene NAME]
    python tools/live/obsctl.py stream start|stop|status
    python tools/live/obsctl.py record start|stop|status
    python tools/live/obsctl.py call GetVideoSettings
    python tools/live/obsctl.py call SetCurrentProgramScene '{"sceneName": "Wide"}'

`call` sends any request from the protocol
(github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)
and redacts stream keys/URLs from what it prints. The show's own looks
(Product / Up Next, refocus) are in tools/live/show_flow.py, built on this.

The password comes from OBS_WS_PASSWORD, else from the websocket config OBS
itself uses: $OBS_CONFIG_DIR/plugin_config/obs-websocket/config.json
(default ~/.var/app/com.obsproject.Studio/config/obs-studio, the Flatpak).
It is never printed. OBS_WS_HOST / OBS_WS_PORT override 127.0.0.1:4455.
OBS's websocket listens on every interface (the Flatpak has no bind option),
so the password is what keeps the LAN out.

Stdlib only, like the rest of the repo outside the visual indexes: a minimal
RFC 6455 client (text frames, fragmentation, ping/pong, close).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_CONFIG_DIR = Path.home() / ".var/app/com.obsproject.Studio/config/obs-studio"
FLATPAK_APP = "com.obsproject.Studio"
# No window: tray only. --disable-shutdown-check skips the "launch in safe
# mode?" dialog after an unclean exit, which would otherwise block headless.
LAUNCH_ARGS = ["--minimize-to-tray", "--disable-shutdown-check", "--disable-updater"]


class ObsError(RuntimeError):
    pass


def ws_config_path() -> Path:
    base = Path(os.environ.get("OBS_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    return base / "plugin_config" / "obs-websocket" / "config.json"


def load_password() -> str | None:
    if "OBS_WS_PASSWORD" in os.environ:
        return os.environ["OBS_WS_PASSWORD"]
    try:
        cfg = json.loads(ws_config_path().read_text())
    except (OSError, ValueError):
        return None
    return cfg.get("server_password") if cfg.get("auth_required", True) else None


def auth_string(password: str, salt: str, challenge: str) -> str:
    """obs-websocket v5: b64(sha256(b64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()


def _mask(data: bytes, key: bytes) -> bytes:
    n = len(data)
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


class ObsClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 4455,
                 password: str | None = None, timeout: float = 10.0):
        self.buf = b""
        try:
            self.sock = socket.create_connection((host, port), timeout)
        except OSError as e:
            raise ObsError(f"can't reach obs-websocket at {host}:{port} ({e}); "
                           "is OBS running with its websocket server on?") from e
        self._handshake(host, port)
        hello = self._recv_msg()
        if hello.get("op") != 0:
            raise ObsError(f"expected Hello, got {hello}")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            if not password:
                raise ObsError(f"OBS wants a password; set OBS_WS_PASSWORD or check {ws_config_path()}")
            ident["authentication"] = auth_string(password, auth["salt"], auth["challenge"])
        self._send_msg({"op": 1, "d": ident})
        msg = self._recv_msg()
        if msg.get("op") != 2:
            raise ObsError(f"identify failed: {msg}")

    # --- websocket framing -------------------------------------------------
    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise ObsError("connection closed by OBS (wrong password?)")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _handshake(self, host: str, port: int) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n"
        ).encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("connection closed during websocket handshake")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise ObsError(f"websocket upgrade refused: {lines[0]}")
        want = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        if headers.get("sec-websocket-accept") != want:
            raise ObsError("bad Sec-WebSocket-Accept from server")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 1 << 16:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        key = os.urandom(4)
        self.sock.sendall(head + key + _mask(payload, key))

    def _recv_msg(self) -> dict:
        parts: list[bytes] = []
        while True:
            b0, b1 = self._read(2)
            fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            key = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if key:
                payload = _mask(payload, key)
            if opcode == 0x8:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                raise ObsError(f"OBS closed the connection: {code} {payload[2:].decode(errors='replace')}")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if fin:
                return json.loads(b"".join(parts))

    def _send_msg(self, msg: dict) -> None:
        self._send_frame(0x1, json.dumps(msg).encode())

    # --- requests ----------------------------------------------------------
    def call(self, request_type: str, data: dict | None = None) -> dict:
        rid = uuid.uuid4().hex
        d = {"requestType": request_type, "requestId": rid}
        if data:
            d["requestData"] = data
        self._send_msg({"op": 6, "d": d})
        while True:
            msg = self._recv_msg()
            if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                status = msg["d"]["requestStatus"]
                if not status.get("result"):
                    raise ObsError(f"{request_type} failed: code {status.get('code')} "
                                   f"{status.get('comment', '')}".rstrip())
                return msg["d"].get("responseData") or {}

    def close(self) -> None:
        try:  # clean close handshake: send ours, wait briefly for OBS's echo
            self._send_frame(0x8, struct.pack(">H", 1000))
            self.sock.settimeout(1.0)
            while True:
                self._recv_msg()
        except (OSError, ObsError):
            pass
        self.sock.close()


# --- commands --------------------------------------------------------------
def _program_scene(c: ObsClient) -> str:
    return c.call("GetCurrentProgramScene")["currentProgramSceneName"]


def cmd_status(c, a):
    v = c.call("GetVersion")
    s = c.call("GetStats")
    vs = c.call("GetVideoSettings")
    st = c.call("GetStreamStatus")
    rec = c.call("GetRecordStatus")
    print(f"OBS {v['obsVersion']} (websocket {v['obsWebSocketVersion']})")
    print(f"canvas {vs['baseWidth']}x{vs['baseHeight']} -> {vs['outputWidth']}x{vs['outputHeight']} "
          f"@ {vs['fpsNumerator'] / vs['fpsDenominator']:g} fps")
    print(f"program scene: {_program_scene(c)}")
    print(f"stream: {'LIVE ' + st['outputTimecode'] if st['outputActive'] else 'off'}"
          f"{'  (reconnecting)' if st.get('outputReconnecting') else ''}")
    print(f"record: {'REC ' + rec['outputTimecode'] if rec['outputActive'] else 'off'}")
    print(f"cpu {s['cpuUsage']:.1f}%  fps {s['activeFps']:.1f}  "
          f"render skipped {s['renderSkippedFrames']}/{s['renderTotalFrames']}  "
          f"output skipped {s['outputSkippedFrames']}/{s['outputTotalFrames']}")


def cmd_scenes(c, a):
    r = c.call("GetSceneList")
    cur = r["currentProgramSceneName"]
    for sc in reversed(r["scenes"]):  # OBS lists bottom-up
        items = c.call("GetSceneItemList", {"sceneName": sc["sceneName"]})["sceneItems"]
        mark = "*" if sc["sceneName"] == cur else " "
        srcs = ", ".join(f"{i['sourceName']}{'' if i['sceneItemEnabled'] else ' (hidden)'}" for i in items)
        print(f"{mark} {sc['sceneName']}: {srcs or '(empty)'}")


def cmd_scene(c, a):
    c.call("SetCurrentProgramScene", {"sceneName": a.name})
    print(f"program scene: {_program_scene(c)}")


def cmd_inputs(c, a):
    for i in c.call("GetInputList")["inputs"]:
        print(f"{i['inputName']}  [{i['inputKind']}]")


def cmd_shot(c, a):
    source = _program_scene(c) if a.source == "program" else a.source
    req = {"sourceName": source, "imageFormat": "jpg", "imageCompressionQuality": a.quality}
    if a.width:
        req["imageWidth"] = a.width
    data = c.call("GetSourceScreenshot", req)["imageData"]
    out = Path(a.output or Path(tempfile.gettempdir()) /
               f"obs_{''.join(ch if ch.isalnum() else '_' for ch in source)}.jpg")
    out.write_bytes(base64.b64decode(data.split(",", 1)[1]))
    print(out)


def cmd_fit(c, a):
    """Scale a source to fill the canvas (crop-free letterbox, or --cover to crop-fill)."""
    scene = a.scene or _program_scene(c)
    vs = c.call("GetVideoSettings")
    item = c.call("GetSceneItemId", {"sceneName": scene, "sourceName": a.source})["sceneItemId"]
    c.call("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": item, "sceneItemTransform": {
        "positionX": 0, "positionY": 0, "alignment": 5, "rotation": 0,
        "boundsType": "OBS_BOUNDS_SCALE_OUTER" if a.cover else "OBS_BOUNDS_SCALE_INNER",
        "boundsAlignment": 0, "boundsWidth": vs["baseWidth"], "boundsHeight": vs["baseHeight"],
        "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0}})
    print(f"{a.source} fit to {vs['baseWidth']}x{vs['baseHeight']} in {scene}"
          f"{' (cover)' if a.cover else ''}")


def _output_cmd(kind: str):
    def run(c, a):
        if a.action != "status":
            c.call(f"{a.action.capitalize()}{kind}")
        want = {"start": True, "stop": False}.get(a.action)
        deadline = time.monotonic() + 10  # Start/Stop return before the output flips
        while True:
            st = c.call(f"Get{kind}Status")
            if want is None or st["outputActive"] == want or time.monotonic() > deadline:
                break
            time.sleep(0.25)
        print(json.dumps(st, indent=2))
    return run


# Stream settings carry secrets: eBay's WHIP URL has the key in its path, and
# Twitch/YouTube have a stream key. Never print them.
SECRET_FIELDS = {"key", "server", "bearer_token", "password", "username"}


def redact(obj):
    if isinstance(obj, dict):
        return {k: (f"<redacted, {len(v)} chars>" if k in SECRET_FIELDS and isinstance(v, str)
                    and v and not v.startswith("auto") else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def cmd_call(c, a):
    data = json.loads(a.data) if a.data else None
    print(json.dumps(redact(c.call(a.request, data)), indent=2))


# --- lifecycle (no websocket needed to start; obs-websocket has no Quit) ----
def _obs_pids() -> list[int]:
    r = subprocess.run(["pgrep", "-x", "obs"], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def _connect(host, port, timeout=10.0) -> ObsClient:
    return ObsClient(host, port, load_password(), timeout)


def cmd_launch(host, port, a) -> None:
    if _obs_pids():
        print(f"OBS already running (pid {', '.join(map(str, _obs_pids()))})")
        return
    log = Path(a.log)
    with open(log, "ab") as f:
        subprocess.Popen(["flatpak", "run", FLATPAK_APP, *LAUNCH_ARGS],
                         stdin=subprocess.DEVNULL, stdout=f, stderr=f,
                         cwd=Path.home(), start_new_session=True)
    deadline = time.monotonic() + a.wait
    while time.monotonic() < deadline:
        time.sleep(1)
        try:
            c = _connect(host, port, timeout=3)
        except ObsError:
            continue
        v = c.call("GetVersion")
        c.close()
        print(f"OBS {v['obsVersion']} up, no window (pid {', '.join(map(str, _obs_pids()))}); log {log}")
        return
    raise ObsError(f"OBS didn't answer on {host}:{port} within {a.wait}s; see {log}")


def cmd_quit(host, port, a) -> None:
    if not _obs_pids():
        print("OBS not running")
        return
    if not a.force:
        try:
            c = _connect(host, port, timeout=3)
            live = [k for k in ("Stream", "Record")
                    if c.call(f"Get{k}Status").get("outputActive")]
            c.close()
        except ObsError:
            live = []
        if live:
            raise ObsError(f"{' and '.join(live).lower()} still active; stop it first or pass --force")
    subprocess.run(["pkill", "-TERM", "-x", "obs"])  # OBS exits cleanly on SIGTERM
    deadline = time.monotonic() + 20
    while _obs_pids() and time.monotonic() < deadline:
        time.sleep(0.5)
    if _obs_pids():
        raise ObsError("OBS still running 20s after SIGTERM")
    print("OBS stopped")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("scenes").set_defaults(fn=cmd_scenes)
    s = sub.add_parser("scene"); s.add_argument("name"); s.set_defaults(fn=cmd_scene)
    sub.add_parser("inputs").set_defaults(fn=cmd_inputs)
    s = sub.add_parser("shot", help="screenshot a source, or 'program'")
    s.add_argument("source")
    s.add_argument("-o", "--output")
    s.add_argument("--width", type=int, default=0)
    s.add_argument("--quality", type=int, default=85)
    s.set_defaults(fn=cmd_shot)
    s = sub.add_parser("fit", help="scale a source to the canvas")
    s.add_argument("source")
    s.add_argument("--scene", help="default: current program scene")
    s.add_argument("--cover", action="store_true", help="fill the canvas, cropping overflow")
    s.set_defaults(fn=cmd_fit)
    for kind in ("stream", "record"):
        s = sub.add_parser(kind)
        s.add_argument("action", choices=["start", "stop", "status"])
        s.set_defaults(fn=_output_cmd(kind.capitalize()))
    s = sub.add_parser("call", help="any obs-websocket request")
    s.add_argument("request")
    s.add_argument("data", nargs="?", help="requestData as JSON")
    s.set_defaults(fn=cmd_call)
    s = sub.add_parser("launch", help="start OBS with no window")
    s.add_argument("--wait", type=int, default=45, help="seconds to wait for the websocket")
    s.add_argument("--log", default=str(Path(tempfile.gettempdir()) / "obs-flatpak.out"))
    s.set_defaults(lifecycle=cmd_launch)
    s = sub.add_parser("quit", help="shut OBS down cleanly")
    s.add_argument("--force", action="store_true", help="quit even if streaming/recording")
    s.set_defaults(lifecycle=cmd_quit)
    a = p.parse_args(argv)

    host = os.environ.get("OBS_WS_HOST", "127.0.0.1")
    port = int(os.environ.get("OBS_WS_PORT", "4455"))
    try:
        if getattr(a, "lifecycle", None):
            a.lifecycle(host, port, a)
            return 0
        c = _connect(host, port)
        try:
            a.fn(c, a)
        finally:
            c.close()
    except ObsError as e:
        print(f"obsctl: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
