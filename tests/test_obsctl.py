"""tests/test_obsctl.py — tools/live/obsctl.py's obs-websocket v5 client,
against a minimal in-process fake OBS (RFC 6455 handshake, Hello/Identify
auth, request/response, close). No real OBS needed."""
import base64
import hashlib
import json
import socket
import struct
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "live"))

import obsctl  # noqa: E402

PASSWORD, SALT, CHALLENGE = "hunter2", "c2FsdA==", "Y2hhbGxlbmdl"


def _recv_frame(conn, buf):
    def read(n):
        nonlocal buf
        while len(buf) < n:
            chunk = conn.recv(65536)
            if not chunk:
                raise ConnectionError
            buf += chunk
        out, buf = buf[:n], buf[n:]
        return out
    b0, b1 = read(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read(8))[0]
    key = read(4)  # clients must mask
    payload = bytes(b ^ key[i % 4] for i, b in enumerate(read(n)))
    return b0 & 0x0F, payload, buf


def _send(conn, opcode, payload: bytes):
    n = len(payload)
    head = bytes([0x80 | opcode])
    head += bytes([n]) if n < 126 else (bytes([126]) + struct.pack(">H", n) if n < 65536
                                        else bytes([127]) + struct.pack(">Q", n))
    conn.sendall(head + payload)


class FakeObs:
    """Accepts one client; answers GetVersion, a big screenshot, and fails
    anything else with code 600. Records what it saw."""

    def __init__(self, password=PASSWORD):
        self.password = password
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.seen = {"identified": False, "pong": None, "close_code": None}
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        conn, _ = self.srv.accept()
        with conn:
            req = b""
            while b"\r\n\r\n" not in req:
                req += conn.recv(4096)
            head, buf = req.split(b"\r\n\r\n", 1)
            key = [l.split(":", 1)[1].strip() for l in head.decode().split("\r\n")
                   if l.lower().startswith("sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1((key + obsctl.WS_GUID).encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            _send(conn, 1, json.dumps({"op": 0, "d": {"rpcVersion": 1, "authentication":
                                                      {"salt": SALT, "challenge": CHALLENGE}}}).encode())
            while True:
                try:
                    op, payload, buf = _recv_frame(conn, buf)
                except ConnectionError:
                    return
                if op == 0xA:
                    self.seen["pong"] = payload
                    continue
                if op == 0x8:
                    self.seen["close_code"] = struct.unpack(">H", payload[:2])[0]
                    _send(conn, 0x8, payload[:2])
                    return
                msg = json.loads(payload)
                if msg["op"] == 1:
                    want = obsctl.auth_string(self.password, SALT, CHALLENGE)
                    if msg["d"].get("authentication") != want:
                        _send(conn, 0x8, struct.pack(">H", 4009) + b"Authentication failed.")
                        return
                    self.seen["identified"] = True
                    _send(conn, 0x9, b"ping")  # client must answer and carry on
                    _send(conn, 1, json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}).encode())
                elif msg["op"] == 6:
                    d = msg["d"]
                    rt = d["requestType"]
                    if rt == "GetVersion":
                        status, data = {"result": True, "code": 100}, {"obsVersion": "32.2.2"}
                    elif rt == "GetSourceScreenshot":
                        status, data = {"result": True, "code": 100}, {"imageData": "x" * 200_000}
                    else:
                        status, data = {"result": False, "code": 600, "comment": "No source"}, None
                    resp = {"op": 7, "d": {"requestType": rt, "requestId": d["requestId"],
                                           "requestStatus": status, "responseData": data}}
                    _send(conn, 1, json.dumps(resp).encode())


def test_auth_string_matches_protocol_recipe():
    secret = base64.b64encode(hashlib.sha256((PASSWORD + SALT).encode()).digest())
    want = base64.b64encode(hashlib.sha256(secret + CHALLENGE.encode()).digest()).decode()
    assert obsctl.auth_string(PASSWORD, SALT, CHALLENGE) == want


def test_request_roundtrip_large_frame_and_clean_close():
    fake = FakeObs()
    c = obsctl.ObsClient("127.0.0.1", fake.port, PASSWORD)
    assert c.call("GetVersion") == {"obsVersion": "32.2.2"}
    assert len(c.call("GetSourceScreenshot")["imageData"]) == 200_000  # 64-bit length frame
    with pytest.raises(obsctl.ObsError, match="code 600 No source"):
        c.call("SetCurrentProgramScene", {"sceneName": "nope"})
    c.close()
    fake.t.join(2)
    assert fake.seen == {"identified": True, "pong": b"ping", "close_code": 1000}


def test_wrong_password_is_an_error_not_a_hang():
    fake = FakeObs(password="other")
    with pytest.raises(obsctl.ObsError):
        obsctl.ObsClient("127.0.0.1", fake.port, PASSWORD, timeout=3)


def test_unreachable_obs_says_so():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    with pytest.raises(obsctl.ObsError, match="can't reach obs-websocket"):
        obsctl.ObsClient("127.0.0.1", port, PASSWORD, timeout=1)


def test_redact_hides_stream_secrets_keeps_the_rest():
    resp = {"streamServiceType": "whip_custom", "streamServiceSettings": {
        "server": "https://stream.example/stream/SECRETTOKEN?direction=whip", "key": "live_abc",
        "bearer_token": "", "service": "WHIP", "use_auth": False}}
    out = json.dumps(obsctl.redact(resp))
    assert "SECRETTOKEN" not in out and "live_abc" not in out
    assert '"service": "WHIP"' in out and '"bearer_token": ""' in out
    assert obsctl.redact({"server": "auto"}) == {"server": "auto"}  # Twitch's auto-ingest isn't secret


def test_password_read_from_obs_config(tmp_path, monkeypatch):
    cfg = tmp_path / "plugin_config" / "obs-websocket" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"auth_required": True, "server_password": "pw"}))
    monkeypatch.delenv("OBS_WS_PASSWORD", raising=False)
    monkeypatch.setenv("OBS_CONFIG_DIR", str(tmp_path))
    assert obsctl.load_password() == "pw"
    cfg.write_text(json.dumps({"auth_required": False, "server_password": "pw"}))
    assert obsctl.load_password() is None
