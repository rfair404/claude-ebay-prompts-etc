"""tests/test_webapp_security.py — #311 slice 1: the localhost guard runs on
every route (webapp/security.py), a restart never re-runs a job, and the
suite-wide conftest guards (no network, no real config) are on."""
import os
import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import webapp.server as server  # noqa: E402
from webapp.queue import INTERRUPTED, JobQueue  # noqa: E402
from webapp.security import PII_HEADERS  # noqa: E402

LOCAL = "http://127.0.0.1:8770"


@pytest.fixture
def queue(tmp_path):
    q = JobQueue(tmp_path / "jobs.db", {"noop": lambda p: {"ok": True}})
    server.set_queue(q)
    yield q
    q.shutdown()
    server.set_queue(None)


@pytest.fixture
def client(queue):
    with TestClient(server.app, base_url=LOCAL) as c:
        yield c


@pytest.fixture
def foreign(queue):
    """The same app, reached by a name that isn't loopback (DNS rebinding)."""
    with TestClient(server.app, base_url="http://evil.example") as c:
        yield c


@pytest.mark.parametrize("method,path,body", [
    ("get", "/", None),
    ("get", "/healthz", None),
    ("get", "/api/jobs", None),
    ("post", "/api/jobs", {"type": "noop", "params": {}}),
    ("post", "/live/demo/state", {"lot": 1}),
    ("get", "/review/anything", None),
])
def test_a_non_loopback_host_is_refused_on_every_route(foreign, queue, method, path, body):
    kw = {"json": body} if body is not None else {}
    r = getattr(foreign, method)(path, **kw)
    assert r.status_code == 403 and "localhost only" in r.json()["detail"]
    assert queue.list() == []          # nothing was enqueued


@pytest.mark.parametrize("path,body", [
    ("/api/jobs", {"type": "noop", "params": {}}),
    ("/live/demo/state", {"lot": 1}),
])
def test_a_cross_origin_write_is_refused(client, queue, path, body):
    r = client.post(path, json=body, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and "cross-origin" in r.json()["detail"]
    assert queue.list() == []


def test_a_browser_write_with_no_origin_is_refused(client, queue):
    r = client.post("/api/jobs", json={"type": "noop", "params": {}},
                    headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors"})
    assert r.status_code == 403 and "no Origin" in r.json()["detail"]
    assert queue.list() == []


def test_a_loopback_write_goes_through(client, queue):
    r = client.post("/api/jobs", json={"type": "noop", "params": {}},
                    headers={"origin": LOCAL, "sec-fetch-site": "same-origin"})
    assert r.status_code == 200
    assert queue.wait(r.json()["id"]).status == "done"


def test_a_non_browser_client_with_no_origin_still_works(client, queue):
    """curl or an agent driving /api/pager mid-show sends no Origin and no
    Sec-Fetch headers; no web page can make it send anything, so it isn't
    a CSRF path and it keeps working."""
    r = client.post("/api/jobs", json={"type": "noop", "params": {}})
    assert r.status_code == 200


def test_every_answer_carries_the_no_store_headers(client):
    r = client.get("/healthz")
    for k, v in PII_HEADERS.items():
        assert r.headers[k] == v


@pytest.mark.parametrize("host", ["127.0.0.1:8770", "localhost:8770", "[::1]:8770", "localhost"])
def test_loopback_host_spellings_are_accepted(client, host):
    assert client.get("/healthz", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1.evil.example", "localhost.evil.example:8770", ""])
def test_lookalike_hosts_are_refused(client, host):
    assert client.get("/healthz", headers={"host": host}).status_code == 403


# --- the queue never re-runs a job after a restart -------------------------

def test_a_restart_closes_unfinished_jobs_and_never_reruns_them(tmp_path):
    db = tmp_path / "jobs.db"
    ran = []
    q = JobQueue(db, {"noop": lambda p: ran.append(p) or {}})
    with q._lock:   # two rows a crash left behind, and one that finished
        for status in ("queued", "running", "done"):
            q._conn.execute("INSERT INTO jobs (type, params, status, created_at) "
                            "VALUES ('noop', '{}', ?, 0)", (status,))
        q._conn.commit()
    q.shutdown()

    q2 = JobQueue(db, {"noop": lambda p: ran.append(p) or {}})
    try:
        jobs = {j.id: j for j in q2.list()}
        assert jobs[1].status == jobs[2].status == "error"
        assert jobs[1].error == jobs[2].error == INTERRUPTED
        assert jobs[1].finished_at and jobs[2].finished_at
        assert jobs[3].status == "done" and jobs[3].error is None
        assert ran == []
    finally:
        q2.shutdown()


# --- the conftest guards ---------------------------------------------------

def test_the_network_is_off_in_tests():
    import conftest
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(conftest.NetworkBlocked):
            s.connect(("93.184.216.34", 80))
        with pytest.raises(conftest.NetworkBlocked):
            s.connect(("example.com", 80))   # a hostname is refused unresolved
    finally:
        s.close()


def test_loopback_stays_open_in_tests():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        c.connect(srv.getsockname())
    finally:
        c.close()
        srv.close()


def test_tests_never_see_real_config_or_credentials(hermetic_config):
    assert os.environ["EBAYBIZ_CONFIG"] == str(hermetic_config)
    assert not [k for k in os.environ if k.startswith(("EBAY_", "APIFY_"))]
    import config
    assert config.config_path() == hermetic_config.resolve()
    assert config.load_config() == {}
