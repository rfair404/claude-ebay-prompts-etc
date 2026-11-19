"""Headless comp runner (lib/ebay_sold_browse.headless_multi) against a fake
CDP endpoint: a local websockets server that answers /json/list over HTTP and
Runtime.evaluate over the socket. No Chrome, no network beyond 127.0.0.1.

Run:  pytest tests/test_ebay_sold_headless.py
"""
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("websockets", minversion="13.0")  # not in CI's pip line yet
from websockets.sync.server import serve  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import ebay_sold_browse as esb  # noqa: E402

PLAN = [{"label": "L1|best_match", "rung": "L1", "query": "fenton vase",
         "sort": "best_match", "url": esb.build_search_url("fenton vase")}]
ROW = {"title": "Fenton Hobnail Vase", "sold_price": 30.0, "shipping_cost": 8.0,
       "url": "https://www.ebay.com/itm/123456789012"}


def _value(ok=True, challenge=False, error=None):
    full = [] if challenge else [dict(PLAN[0], ok=True, n=1, rows=[ROW])]
    summ = [{"label": PLAN[0]["label"], "ok": not challenge,
             "challenge": challenge, "n": 0 if challenge else 1}]
    return json.dumps({"ok": ok, "error": error, "saved": None,
                       "queries": full, "summary": summ})


def _fake_cdp(url, value, seen):
    def process_request(conn, request):
        if request.path.startswith("/json"):
            port = conn.socket.getsockname()[1]
            return conn.respond(200, json.dumps([{
                "type": "page", "url": url,
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/1"}]))
        return None

    def handler(ws):
        msg = json.loads(ws.recv())
        seen.append(msg)
        ws.send(json.dumps({"id": 99, "method": "Runtime.consoleAPICalled"}))
        ws.send(json.dumps({"id": msg["id"], "result": {
            "result": {"type": "string", "value": value}}}))

    server = serve(handler, "127.0.0.1", 0, process_request=process_request)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


CASES = [
    # id,          tab url,                     page value,               expect
    ("rows",       "https://www.ebay.com/",     _value(),                 None),
    ("challenge",  "https://www.ebay.com/",     _value(challenge=True),   "challenge"),
    ("not-ok",     "https://www.ebay.com/",     _value(ok=False, error="boom"), "boom"),
    ("no-ebay-tab", "https://example.com/",     _value(),                 "no ebay.com tab"),
    ("no-tunnel",  None,                        None,                     "ssh -N -L"),
]


@pytest.mark.parametrize("case,url,value,expect", CASES, ids=[c[0] for c in CASES])
def test_headless_multi(case, url, value, expect):
    seen = []
    server = None
    if url is None:
        endpoint = f"127.0.0.1:{_free_port()}"
    else:
        server = _fake_cdp(url, value, seen)
        endpoint = f"127.0.0.1:{server.socket.getsockname()[1]}"
    try:
        if expect is None:
            data = esb.headless_multi(PLAN, endpoint, timeout=5)
            rows, label = esb.pick_multi_rows(data, "L1|best_match")
            assert rows == [ROW] and label == "L1|best_match"
            params = seen[0]["params"]
            assert seen[0]["method"] == "Runtime.evaluate"
            assert params["awaitPromise"] and params["returnByValue"]
            assert "summary:out" in params["expression"]
        else:
            with pytest.raises(esb.HeadlessError, match=expect):
                esb.headless_multi(PLAN, endpoint, timeout=5)
    finally:
        if server:
            server.shutdown()
