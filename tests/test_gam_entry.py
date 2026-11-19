"""gam_entry against a fake CDP tab: no Chrome, no network beyond 127.0.0.1.

Run:  pytest tests/test_gam_entry.py
"""
import json
import sys
import threading
from pathlib import Path

import pytest
from websockets.sync.server import serve

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gam_entry as G  # noqa: E402


def _draft(tmp, name, channel="mall", price="45.00", **extra):
    d = tmp / name
    d.mkdir()
    (d / "draft.md").write_text(
        "---\ntemplate_version: v1\nmeta:\n  item_id: x\n"
        + (f"  channel: {channel}\n" if channel else "")
        + f'title: "Silverplate Spoons"\nprice: "{price}"\nquantity: 2\ncondition: USED_EXCELLENT\n'
        "item_specifics:\n  brand: Unbranded\n  color: Silver\n---\nbody\n", encoding="utf-8")
    return d


def test_row_from_draft_maps_fields(tmp_path):
    row = G.row_from_draft(_draft(tmp_path, "a") / "draft.md")
    assert row["name"] == "Silverplate Spoons"
    assert (row["quantity"], row["retail"], row["used"]) == ("2", "45.00", True)
    assert row["brandText"] == "" and row["color"] == "Silver"  # "Unbranded" is not a brand


def test_collect_all_skips_ebay_drafts_and_named_ebay_item_is_refused(tmp_path):
    _draft(tmp_path, "mallitem")
    ebay = _draft(tmp_path, "ebayitem", channel="")
    assert [r["source"] for r in G.collect([], str(tmp_path))] == ["mallitem"]
    with pytest.raises(G.GamError, match="not a mall item"):
        G.collect([str(ebay)], None)


def test_missing_price_is_an_error(tmp_path):
    with pytest.raises(G.GamError, match="title and a price"):
        G.row_from_draft(_draft(tmp_path, "b", price="") / "draft.md")


def test_the_only_click_is_a_save_button_never_submit():
    assert G.SAVE_LABEL.startswith("Save")
    for js in (G.SAVE_JS, G.fill_js({"name": "x", "retail": "1.00"}), G.category_js("Jewelry")):
        assert "Submit" not in js


class _Portal:
    """Fake portal tab: counts saves, answers each script by what it asks."""
    def __init__(self, logged_in=True, fields_ok=True):
        self.saves, self.logged_in, self.fields_ok, self.url = 0, logged_in, fields_ok, G.LIST_URL

    def answer(self, expr):
        if "Page.navigate" in expr:
            return None
        if expr == "document.readyState":
            return "complete"
        if expr == "location.href":
            return self.url if self.logged_in else PORTAL_LOGIN
        if "setTimeout" in expr and "Event(" not in expr:
            return None
        if "missing" in expr:
            return json.dumps({"ok": self.fields_ok, "missing": [] if self.fields_ok else ["retail"]})
        if G.SAVE_LABEL in expr:
            self.saves += 1
            return json.dumps({"ok": True})
        if "errors" in expr:
            return json.dumps({"url": self.url, "errors": []})
        if "total" in expr:
            return json.dumps({"total": self.saves})
        raise AssertionError(expr[:80])


PORTAL_LOGIN = G.PORTAL + "/login"


def _serve(portal):
    def process_request(conn, request):
        if request.path.startswith("/json"):
            port = conn.socket.getsockname()[1]
            return conn.respond(200, json.dumps([{
                "type": "page", "url": G.PORTAL + "/dashboard",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/p"}]))

    def handler(ws):
        for raw in ws:
            msg = json.loads(raw)
            p = msg.get("params", {})
            if msg["method"] == "Page.navigate":
                portal.url = p["url"]
                ws.send(json.dumps({"id": msg["id"], "result": {}}))
                continue
            v = portal.answer(p.get("expression", ""))
            ws.send(json.dumps({"id": msg["id"], "result": {"result": {"type": "string", "value": v}}}))

    server = serve(handler, "127.0.0.1", 0, process_request=process_request)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"127.0.0.1:{server.socket.getsockname()[1]}"


ROWS = [{"name": "A", "quantity": "1", "retail": "5.00", "used": True, "source": "a"},
        {"name": "B", "quantity": "1", "retail": "6.00", "used": False, "source": "b"}]


def test_run_queues_each_row_and_checks_the_count():
    portal = _Portal()
    server, ep = _serve(portal)
    try:
        res = G.run(ROWS, ep)
    finally:
        server.shutdown()
    assert [o["ok"] for o in res] == [True, True] and portal.saves == 2


def test_signed_out_stops_the_run_without_saving():
    portal = _Portal(logged_in=False)
    server, ep = _serve(portal)
    try:
        with pytest.raises(G.GamError, match="signed out"):
            G.run(ROWS, ep)
    finally:
        server.shutdown()
    assert portal.saves == 0


def test_missing_form_field_is_reported_not_saved():
    portal = _Portal(fields_ok=False)
    server, ep = _serve(portal)
    try:
        res = G.run(ROWS[:1], ep)
    finally:
        server.shutdown()
    assert res[0]["ok"] is False and "retail" in res[0]["note"] and portal.saves == 0


def test_no_tunnel_names_the_fix():
    with pytest.raises(G.GamError, match="ssh -N -L 127.0.0.1:9223"):
        G.run(ROWS, "127.0.0.1:1")
