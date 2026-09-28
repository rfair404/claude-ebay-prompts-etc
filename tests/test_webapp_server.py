"""tests/test_webapp_server.py — webapp/server.py's HTTP surface (#31
Phase 2): the live dashboard route and the job-queue API, against a
throwaway SQLite file and fake handlers so this file never touches real
inventory/ or shells out to prep.

tools/dashboard.py's own gather()/draw() logic is tests/test_dashboard.py's
job — here we only check the route wires them together and returns HTML.
"""
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import pick_store  # noqa: E402
import webapp.server as server  # noqa: E402
from webapp.queue import JobQueue  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    queue = JobQueue(tmp_path / "jobs.db", {
        "noop": lambda params: {"ok": True, "params": params},
    })
    server.set_queue(queue)
    monkeypatch.setattr(server._dashboard, "gather",
                         lambda: {"stub": "backlog+drafts+drift"})
    monkeypatch.setattr(server._dashboard, "draw",
                         lambda d: f"<html><body>{d['stub']}</body></html>")
    with TestClient(server.app) as c:
        yield c
    queue.shutdown()
    server.set_queue(None)


def test_dashboard_route_renders_via_gather_and_draw(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "backlog+drafts+drift" in r.text


def test_enqueue_unknown_job_type_is_400(client):
    r = client.post("/api/jobs", json={"type": "no-such-job", "params": {}})
    assert r.status_code == 400
    assert "unknown job type" in r.json()["detail"]


def test_enqueue_and_fetch_a_job_reaches_done(client):
    r = client.post("/api/jobs", json={"type": "noop", "params": {"n": 1}})
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert r.json()["status"] == "queued"

    queue = server.get_queue()
    queue.wait(job_id, timeout=5)

    r2 = client.get(f"/api/jobs/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "done"
    assert body["result"] == {"ok": True, "params": {"n": 1}}


def test_get_unknown_job_is_404(client):
    r = client.get("/api/jobs/999999")
    assert r.status_code == 404


def test_list_jobs_returns_newest_first(client):
    ids = []
    for i in range(3):
        r = client.post("/api/jobs", json={"type": "noop", "params": {"i": i}})
        ids.append(r.json()["id"])
    queue = server.get_queue()
    for i in ids:
        queue.wait(i, timeout=5)

    r = client.get("/api/jobs")
    assert r.status_code == 200
    listed_ids = [row["id"] for row in r.json()]
    assert listed_ids[:3] == list(reversed(ids))


# ---------------------------------------------------------------------------
# /review/{shoot} — live REVIEW gate page (#31 Phase 2 follow-up)
# ---------------------------------------------------------------------------

@pytest.fixture
def inventory_client(client, tmp_path, monkeypatch):
    """The `client` fixture plus a real (throwaway) inventory/ dir wired
    into webapp.jobs, which /review/{shoot} resolves shoot names against."""
    import webapp.jobs as jobs
    inv = tmp_path / "inventory"
    inv.mkdir()
    monkeypatch.setattr(jobs, "INVENTORY", inv)
    return client, inv


def _write_shoot(inv, name, draft=True):
    from PIL import Image
    shoot = inv / name
    (shoot / "listing").mkdir(parents=True)
    Image.new("RGB", (40, 30), (200, 180, 160)).save(shoot / "listing" / "a.jpg")
    if draft:
        (shoot / "draft.md").write_text(
            '---\ntitle: "Test Item"\nprice: "10.00"\n'
            'meta:\n  ebay_inventory_sku: "SKU-1"\n---\n'
            '# Description\nbody.\n', encoding="utf-8")
    return shoot


def test_review_route_renders_the_live_review_page(inventory_client):
    client, inv = inventory_client
    _write_shoot(inv, "item-1")
    r = client.get("/review/item-1")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Test Item" in r.text
    assert "$10.00" in r.text


def test_review_route_404s_for_an_unknown_shoot(inventory_client):
    client, _inv = inventory_client
    r = client.get("/review/never-shot-this")
    assert r.status_code == 404


def test_review_route_404s_when_draft_has_not_run_yet(inventory_client):
    client, inv = inventory_client
    _write_shoot(inv, "item-1", draft=False)
    r = client.get("/review/item-1")
    assert r.status_code == 404
    assert "draft.md" in r.json()["detail"]


def test_review_route_rejects_a_path_traversal_shoot_name(inventory_client):
    client, inv = inventory_client
    _write_shoot(inv, "item-1")
    r = client.get("/review/..%2Fitem-1")
    assert r.status_code in (404, 400)


def test_review_route_never_calls_any_ebay_or_apify_client(inventory_client, monkeypatch):
    # The route must stay strictly local/secret-free (docs/webapp-
    # architecture.md's Phase-2 boundary) — assert no eBay/Apify module
    # attribute is even touched by patching both to explode on any access.
    client, inv = inventory_client
    _write_shoot(inv, "item-1")

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected eBay/Apify access: {name}")

    import sys
    monkeypatch.setitem(sys.modules, "ebay_client", _Boom())
    r = client.get("/review/item-1")
    assert r.status_code == 200


def test_serve_binds_loopback_only():
    # Locks down the one non-negotiable from docs/webapp-architecture.md's
    # "Hosting" answer: no phase before Phase 3 puts this on a
    # network-reachable interface. A regression here (e.g. someone changing
    # host="0.0.0.0" for "convenience") should fail loudly in CI, not get
    # noticed live.
    import inspect
    src = inspect.getsource(server.main)
    assert 'host="127.0.0.1"' in src
    assert "0.0.0.0" not in src


# --------------------------------------------------------------------------- #
# /pick/{token} — the pick sheet as a link (#151)
# --------------------------------------------------------------------------- #
@pytest.fixture
def pick_client(client, tmp_path):
    """The normal client, pointed at a throwaway pick store. Nothing in this
    file may read or delete a real sheet — they hold buyer PII."""
    store = tmp_path / "served"
    server.set_pick_store_dir(store)
    yield client, store
    server.set_pick_store_dir(None)


def test_healthz_is_cheap_and_says_up(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_pick_route_serves_the_published_sheet_by_token(pick_client):
    client, store = pick_client
    sheet = pick_store.publish("<html><body>Jamie Buyer</body></html>",
                               order_ids=["03-1-2"], store_dir=store)
    r = client.get(f"/pick/{sheet.token}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Jamie Buyer" in r.text


def test_pick_route_serves_a_sheet_that_lives_in_an_item_folder(pick_client):
    """A sold order's sheet is written into the item's folder under inventory/
    and served FROM there (publish(source=...)), so the route has to answer for
    a sheet the store holds no copy of. The file goes under pick_lists/ here --
    inside the repo, so it passes the containment check, and gitignored, so a
    crashed test leaves nothing committable."""
    client, store = pick_client
    src_dir = ROOT / "pick_lists" / ".webapp-test-sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "pick_03-9-9.html"
    src.write_text("<html><body>Mike H. &middot; Greensboro, NC</body></html>",
                   encoding="utf-8")
    try:
        sheet = pick_store.publish("NOT THIS COPY", order_ids=["03-9-9"],
                                   store_dir=store, source=src)
        assert not (store / f"{sheet.token}.html").exists()

        r = client.get(f"/pick/{sheet.token}")
        assert r.status_code == 200
        assert "Greensboro, NC" in r.text
        assert "NOT THIS COPY" not in r.text
        # same guardrails as any other sheet
        assert "no-store" in r.headers["cache-control"]
        assert "noindex" in r.headers["x-robots-tag"]

        # the file is the live page: re-render in place, no republish needed
        src.write_text("<html><body>re-rendered</body></html>", encoding="utf-8")
        assert "re-rendered" in client.get(f"/pick/{sheet.token}").text

        # and the route losing its file is a 404, not a traceback
        src.unlink()
        assert client.get(f"/pick/{sheet.token}").status_code == 404
    finally:
        shutil.rmtree(src_dir, ignore_errors=True)


def test_pick_route_sends_no_store_and_noindex(pick_client):
    client, store = pick_client
    sheet = pick_store.publish("<html>x</html>", order_ids=["03-1-2"], store_dir=store)
    r = client.get(f"/pick/{sheet.token}")
    # buyer PII: not cached, not indexed, not leaked as a referrer
    assert "no-store" in r.headers["cache-control"]
    assert "noindex" in r.headers["x-robots-tag"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_unknown_token_is_404_and_expired_is_410(pick_client):
    client, store = pick_client
    assert client.get("/pick/" + "z" * 43).status_code == 404

    dead = pick_store.publish("<html>x</html>", order_ids=["old-1"],
                              ttl_hours=-1, store_dir=store)
    r = client.get(f"/pick/{dead.token}")
    assert r.status_code == 410
    assert "expired" in r.json()["detail"]
    # and serving it deleted it, so the next hit is a plain 404
    assert client.get(f"/pick/{dead.token}").status_code == 404


def test_pick_route_cannot_be_walked_out_of_the_store(pick_client):
    client, store = pick_client
    store.mkdir(parents=True, exist_ok=True)
    (store.parent / "secret.html").write_text("SECRET", encoding="utf-8")
    for bad in ("../secret", "..%2Fsecret", "secret.html", "a"):
        r = client.get(f"/pick/{bad}")
        assert r.status_code in (404, 410)
        assert "SECRET" not in r.text


def test_no_route_lists_the_store(pick_client):
    client, store = pick_client
    sheet = pick_store.publish("<html>Jamie Buyer</html>",
                               order_ids=["03-1-2"], store_dir=store)
    # the token is the only way in: nothing enumerates what has been published
    for path in ("/pick", "/pick/", "/api/pick", "/api/picks"):
        assert client.get(path).status_code in (404, 405)
    assert sheet.token not in client.get("/").text
