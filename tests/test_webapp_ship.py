"""tests/test_webapp_ship.py — /ship/box (webapp/ship.py): the shipping
station's size and weight entry, against a throwaway reports/shipping/ so no
test reads or writes the real queue or boxes.csv."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import webapp.server as server  # noqa: E402
import webapp.ship as ship  # noqa: E402

ORIGIN = {"origin": "http://127.0.0.1:8770"}
BOX = {"store": "default", "order_ids": ["03-11111-22222", "03-11111-33333"],
       "buyer": "Jamie B.", "city": "Springfield", "state": "OH",
       "prefill": {"length_in": 12.0, "width_in": 9.0, "depth_in": 4.0,
                   "weight_lb": 1, "weight_oz": 6.0}}
GOOD = {"store": "default", "order_ids": BOX["order_ids"], "length_in": 12.5,
        "width_in": 9, "depth_in": 4, "weight_lb": 2, "weight_oz": 3.5, "confirm": True}


@pytest.fixture
def ship_dir(tmp_path):
    (tmp_path / "ship_queue.json").write_text(json.dumps(
        {"version": 1, "generated_at": "2026-10-24T12:00:00Z", "boxes": [BOX]}))
    ship.set_ship_dir(tmp_path)
    yield tmp_path
    ship.set_ship_dir(None)


@pytest.fixture
def client(ship_dir):
    with TestClient(server.app, base_url="http://127.0.0.1") as c:
        yield c


def test_non_loopback_host_is_403(ship_dir):
    with TestClient(server.app, base_url="http://green.lan:8770") as c:
        assert c.get("/ship/box").status_code == 403
        assert c.post("/ship/box", json=GOOD, headers=ORIGIN).status_code == 403
    assert not (ship_dir / "boxes.csv").exists()


@pytest.mark.parametrize("headers", [{}, {"origin": "https://evil.example"}, {"origin": "null"}])
def test_write_without_a_loopback_origin_is_refused(client, ship_dir, headers):
    r = client.post("/ship/box", json=GOOD, headers=headers)
    assert r.status_code == 403
    assert not (ship_dir / "boxes.csv").exists()


@pytest.mark.parametrize("change, status", [
    ({"confirm": False}, 400),
    ({"length_in": 0}, 422),
    ({"width_in": -3}, 422),
    ({"depth_in": None}, 422),
    ({"weight_oz": 16}, 422),
    ({"weight_oz": -1}, 422),
    ({"weight_lb": 1.5}, 422),
    ({"weight_lb": 0, "weight_oz": 0}, 400),
    ({"order_ids": ["03-11111-22222"]}, 404),     # half a box is not a box
    ({"store": "junk"}, 404),
])
def test_bad_input_is_refused_and_writes_nothing(client, ship_dir, change, status):
    r = client.post("/ship/box", json={**GOOD, **change}, headers=ORIGIN)
    assert r.status_code == status, r.text
    assert not (ship_dir / "boxes.csv").exists()


class _FixedNow:
    @staticmethod
    def now(tz=None):
        from datetime import datetime, timezone
        return datetime(2026, 10, 24, 18, 5, 9, tzinfo=timezone.utc)


def test_happy_path_writes_the_exact_row_and_the_page_shows_it(client, ship_dir, monkeypatch):
    monkeypatch.setattr(ship, "datetime", _FixedNow)
    r = client.post("/ship/box", json=GOOD, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert (ship_dir / "boxes.csv").read_bytes().decode("utf-8") == (
        "recorded_at,store,order_ids,length_in,width_in,depth_in,weight_lb,weight_oz\r\n"
        "2026-10-24T18:05:09Z,default,03-11111-22222+03-11111-33333,12.5,9,4,2,3.5\r\n")
    page = client.get("/ship/box").text
    assert "1 of 1 recorded" in page and 'value="12.5"' in page
    # a second save is a correction: appended, and the page shows the latest
    client.post("/ship/box", json={**GOOD, "length_in": 13}, headers=ORIGIN)
    assert (ship_dir / "boxes.csv").read_text().count("\n") == 3
    assert 'value="13"' in client.get("/ship/box").text


def test_page_shows_the_queue_prefilled_in_seller_hub_order(client):
    r = client.get("/ship/box")
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("no-store")
    page = r.text
    assert "Jamie B." in page and "Springfield, OH" in page and "0 of 1 recorded" in page
    heads = [page.index(h) for h in ("Length (in)", "Width (in)", "Height (in)",
                                     "<th>lb</th>", "<th>oz</th>")]
    assert heads == sorted(heads)
    assert 'name="length_in" min="0" step="0.1" inputmode="decimal" value="12.0"' in page


def test_no_queue_yet_is_a_page_not_an_error(client, ship_dir):
    (ship_dir / "ship_queue.json").unlink()
    r = client.get("/ship/box")
    assert r.status_code == 200 and "No boxes in the queue" in r.text
    assert client.post("/ship/box", json=GOOD, headers=ORIGIN).status_code == 404


def test_pick_list_writes_the_queue_this_page_reads(client, ship_dir):
    """One poll is one store: it replaces that store's rows and keeps the
    others', and the path is the one the page reads."""
    import pick_list
    assert pick_list.SHIP_QUEUE == ship.SHIP_DIR / ship.QUEUE_NAME
    q = ship_dir / "ship_queue.json"

    def order(oid, store):
        return pick_list.tag_orders([{"orderId": oid, "lineItems": [],
            "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
                "fullName": "Ann Lee", "contactAddress": {"city": "Macon",
                                                          "stateOrProvince": "GA"}}}}]}],
            store)[0]
    pick_list.write_ship_queue([order("J-1", "junk")], [], [], "junk", q)
    pick_list.write_ship_queue([], [], [], "default", q)
    assert [b["order_ids"] for b in ship.load_queue()] == [["J-1"]]
    assert ship.load_queue()[0]["prefill"]["weight_lb"] is None   # no draft, no guess
    assert "Ann L." in client.get("/ship/box").text


def test_serve_starts_and_serves_the_page_with_no_config(tmp_path):
    """green has no config.yaml. A fresh process with the config pointed at a
    missing file must import the app, start it and serve /ship/box."""
    code = (
        "import sys, json; sys.path.insert(0, '.')\n"
        "from pathlib import Path\n"
        "import config; assert config.load_config() == {}, 'a config was found'\n"
        "import webapp.server as s\n"
        "from fastapi.testclient import TestClient\n"
        f"s._ship.set_ship_dir(Path({str(tmp_path)!r}))\n"
        "with TestClient(s.app, base_url='http://127.0.0.1') as c:\n"
        "    assert c.get('/healthz').json() == {'ok': True}\n"
        "    r = c.get('/ship/box'); assert r.status_code == 200, r.text\n"
        "print('ok')\n")
    env = {**os.environ, "EBAYBIZ_CONFIG": str(tmp_path / "missing.yaml"),
           "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "lib")])}
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and p.stdout.strip() == "ok", p.stderr[-2000:]
