"""tests/test_webapp_actions.py — #311 slice 3: the action table, its routes,
the shell (store switcher on every app page) and the item pages it serves.

`subprocess.run` is stubbed everywhere: the tests assert on the argv the app
would run, never run it. Shoots live under tmp_path."""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import webapp.actions as A  # noqa: E402
import webapp.jobs as jobs  # noqa: E402
import webapp.server as server  # noqa: E402
import webapp.ui as ui  # noqa: E402
from webapp.queue import JobQueue  # noqa: E402

LOCAL = "http://127.0.0.1:8770"

DRAFT = """---
title: "Brass lamp"
price: "42.00"
condition: "Used"
quantity: 1
store: junk
photos:
  - listing/a.jpg
  - listing/b.jpg
shipping:
  primary_service: "USPSGroundAdvantage"
  weight: {major_lb: 1, minor_oz: 4}
  package_in: {length: 12, width: 9, depth: 2}
---
Body.
"""


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    inv = tmp_path / "inventory"
    shoot = inv / "fr" / "lamp"
    (shoot / "listing").mkdir(parents=True)
    (shoot / ".prep").mkdir()
    (shoot / ".prep" / "prep.json").write_text('{"photos": {}}', encoding="utf-8")
    from PIL import Image
    for n in ("a.jpg", "b.jpg"):
        Image.new("RGB", (40, 30), (200, 180, 160)).save(shoot / "listing" / n)
    (shoot / "draft.md").write_text(DRAFT, encoding="utf-8")
    monkeypatch.setattr(jobs, "INVENTORY", inv)
    return inv


@pytest.fixture
def ran(monkeypatch):
    """Every subprocess.run the actions make, by argv. Nothing really runs."""
    calls = []

    def fake_run(argv, **kw):
        calls.append({"argv": list(argv), **kw})
        return subprocess.CompletedProcess(argv, 0, stdout="[OK] fine\n", stderr="")
    monkeypatch.setattr(A.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def queue(tmp_path):
    q = JobQueue(tmp_path / "jobs.db", {"action": A.run_job})
    server.set_queue(q)
    yield q
    q.shutdown()
    server.set_queue(None)


@pytest.fixture
def client(queue, inventory, monkeypatch):
    monkeypatch.setattr(server._dashboard, "_store_names",
                        lambda: ["default", "junk", "estate"])
    with TestClient(server.app, base_url=LOCAL) as c:
        yield c


# --- the table: every row, one good call and one bad slot each --------------

SHOOT = "fr/lamp"

# name -> (good slots, the argv after `python -m <module>`, a bad slot set)
CASES = {
    "prep-approve-stage": ({"shoot": SHOOT, "stage": "crop"},
                           ["prep", "{dir}", "--approve-stage", "crop"],
                           {"shoot": SHOOT, "stage": "publish"}),
    "prep-set-rotate": ({"shoot": SHOOT, "frames": ["DSC_1.JPG=90", "raw/DSC_2.JPG=0"]},
                        ["prep", "{dir}", "--set-rotate", "DSC_1.JPG=90", "raw/DSC_2.JPG=0"],
                        {"shoot": SHOOT, "frames": ["DSC_1.JPG=45"]}),
    "prep-crop": ({"shoot": SHOOT, "frames": ["DSC_1.JPG=off", "DSC_2.JPG=pad0.08"]},
                  ["prep", "{dir}", "--crop", "DSC_1.JPG=off", "DSC_2.JPG=pad0.08"],
                  {"shoot": SHOOT, "frames": ["--apply=on"]}),
    "prep-pick": ({"shoot": SHOOT, "preset": "crisp"},
                  ["prep", "{dir}", "--pick", "crisp"],
                  {"shoot": SHOOT, "preset": "crisp; rm -rf /"}),
    "prep-apply": ({"shoot": SHOOT}, ["prep", "{dir}", "--apply"],
                   {"shoot": "../../etc"}),
    "hero-montage-propose": ({"shoot": SHOOT, "frames": "1,4", "layout": "inset"},
                             ["propose", "{dir}", "--frames", "1,4", "--layout", "inset"],
                             {"shoot": SHOOT, "layout": "sideways"}),
    "hero-montage-apply": ({"shoot": SHOOT},
                           ["apply", "{dir}", "--repoint"],
                           {"shoot": SHOOT, "frames": "1;4"}),
    "listing-record": ({"shoot": SHOOT}, ["listing", "--record", "{dir}"],
                       {"shoot": SHOOT, "store": "default"}),
    "listing-set-hero": ({"shoot": SHOOT, "photo": "listing/b.jpg"},
                         ["listing", "--set-hero", "{dir}", "listing/b.jpg"],
                         {"shoot": SHOOT, "photo": "--confirm"}),
}


def test_every_action_has_a_case_and_none_writes_ebay():
    assert set(CASES) == set(A.ACTIONS)
    for name, act in A.ACTIONS.items():
        assert not act.writes_ebay and not act.needs_confirm, name
        assert act.module in ("lib.cli", "lib.photo_prep.hero_montage"), name


def test_the_stage_names_match_prep():
    from lib.photo_prep import stages
    assert A.STAGES == tuple(stages.STAGES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_good_call_runs_exactly_the_tables_command(client, queue, inventory, ran, name):
    good, tail, _bad = CASES[name]
    r = client.post(f"/api/actions/{name}", json={"slots": good})
    assert r.status_code == 200, r.text
    job = queue.wait(r.json()["id"])
    assert job.status == "done" and job.result["ok"], job.error
    shoot_dir = str((inventory / "fr" / "lamp").resolve())
    want = [sys.executable, "-m", A.ACTIONS[name].module,
            *[shoot_dir if t == "{dir}" else t for t in tail]]
    assert [c["argv"] for c in ran] == [want]
    assert ran[0]["cwd"] == A.REPO and "shell" not in ran[0]


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_bad_slot_is_a_400_and_nothing_runs(client, queue, ran, name):
    _good, _tail, bad = CASES[name]
    r = client.post(f"/api/actions/{name}", json={"slots": bad})
    assert r.status_code == 400, r.text
    assert queue.list() == [] and ran == []


def test_a_missing_slot_is_a_400(client, queue, ran):
    r = client.post("/api/actions/prep-pick", json={"slots": {"shoot": SHOOT}})
    assert r.status_code == 400 and "needs preset" in r.json()["detail"]
    assert queue.list() == [] and ran == []


def test_an_unknown_action_is_a_404(client, queue, ran):
    r = client.post("/api/actions/listing-publish", json={"slots": {"shoot": SHOOT}})
    assert r.status_code == 404
    assert queue.list() == [] and ran == []


def test_a_job_queued_straight_onto_the_queue_is_revalidated(client, queue, ran):
    """POST /api/jobs {"type": "action"} can't carry an argv of its own: the
    worker rebuilds the command from the table and refuses a bad slot."""
    r = client.post("/api/jobs", json={"type": "action", "params": {
        "action": "listing-set-hero", "argv": ["listing", "--publish", "x", "--confirm"],
        "slots": {"shoot": SHOOT, "photo": "--confirm"}}})
    job = queue.wait(r.json()["id"])
    assert job.status == "error" and "SlotError" in job.error
    assert ran == []


def test_an_ebay_writing_row_is_refused_until_slice_8(client, queue, ran, monkeypatch):
    monkeypatch.setitem(A.ACTIONS, "fake-publish", A.Action(
        "Publish", ("listing", "--publish", "{shoot}"), {"shoot": "shoot"},
        writes_ebay=True, needs_confirm=True))
    r = client.post("/api/actions/fake-publish", json={"slots": {"shoot": SHOOT}})
    assert r.status_code == 400
    with pytest.raises(RuntimeError):
        A.run_job({"action": "fake-publish", "slots": {"shoot": SHOOT}})
    assert queue.list() == [] and ran == []


def test_action_routes_refuse_a_foreign_host_and_a_cross_origin_write(client, queue, ran):
    good = {"slots": {"shoot": SHOOT}}
    r = client.post("/api/actions/prep-apply", json=good, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/actions/prep-apply", json=good, headers={"host": "evil.example"})
    assert r.status_code == 403
    assert queue.list() == [] and ran == []


def test_the_table_is_listed_without_any_argv(client):
    rows = client.get("/api/actions").json()
    assert {r["name"] for r in rows} == set(A.ACTIONS)
    assert all("argv" not in r for r in rows)


# --- pages ------------------------------------------------------------------

def test_every_app_page_has_the_store_switcher(client, monkeypatch):
    monkeypatch.setattr(server._dashboard, "gather", lambda *a, **k: {})
    monkeypatch.setattr(server._dashboard, "draw", lambda d: '<meta charset="utf-8">\n<p>dash</p>')
    page = client.get("/").text
    for s in ("default", "junk", "estate"):
        assert f'href="/?store={s}"' in page
    assert 'href="/"' in page and "All stores" in page
    on_junk = client.get("/?store=junk").text
    assert 'href="/?store=junk" aria-current="page"' in on_junk


def test_the_review_page_carries_the_shell_and_a_cover_button(client):
    r = client.get(f"/review/{SHOOT}")
    assert r.status_code == 200
    page = r.text
    assert 'class="fh-bar"' in page and "Publishes to" in page and ">junk<" in page
    assert "Make the picked frame the cover" in page
    assert 'data-photo="listing/b.jpg"' in page
    assert "listing-set-hero" in page
    assert "Record the draft" in page           # no SKU yet
    assert page.index('class="fh-bar"') < page.index('class="card"')


def test_the_prep_page_runs_through_the_actions(client, monkeypatch):
    import tools.prep_sheet_html as P
    seen = {}

    def fake_render(shoot, **kw):
        seen.update(kw, shoot=shoot)
        return '<meta charset="utf-8">\n<title>Frame Check</title><main>sheet</main>'
    monkeypatch.setattr(P, "render", fake_render)
    r = client.get(f"/prep/{SHOOT}")
    assert r.status_code == 200
    assert seen["app_shoot"] == SHOOT
    assert 'class="fh-bar"' in r.text and "prep-apply" in r.text
    assert "hero-montage-propose" in r.text


def test_prep_pages_need_a_manifest_and_stay_in_inventory(client, inventory):
    (inventory / "bare").mkdir()
    assert client.get("/prep/bare").status_code == 404
    assert client.get("/prep/..%2F..%2Fetc").status_code == 404
    assert client.get("/prep/bare/card.jpg").status_code == 404


def test_card_jpg_is_served_and_built_when_missing(client, inventory, monkeypatch):
    import tools.prep_card as PC
    shoot = inventory / "fr" / "lamp"
    built = []

    def fake_build(s):
        built.append(s)
        out = s / ".prep" / "card.jpg"
        out.write_bytes(b"\xff\xd8\xff\xe0card")
        return out
    monkeypatch.setattr(PC, "build", fake_build)
    r = client.get(f"/prep/{SHOOT}/card.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content.endswith(b"card") and built == [shoot.resolve()]
    client.get(f"/prep/{SHOOT}/card.jpg")
    assert len(built) == 1                     # served from disk the second time
    client.get(f"/prep/{SHOOT}/card.jpg?refresh=1")
    assert len(built) == 2


# --- renderers: the app-only controls, and no third-party URLs --------------

def test_the_prep_sheet_offers_run_it_now_only_in_the_app():
    import tools.prep_sheet_html as P
    card = {"name": "Z.JPG", "options": [{"value": "0", "label": "+0", "img": None, "spin": 0}],
            "chosen": "0", "status": "held", "note": "", "why": ""}
    assert "Run it now" not in P._panel("orientation", [card], True, "", False, "/x")
    assert "Run it now" in P._panel("orientation", [card], True, "", False, "/x", app=True)
    for action in ("prep-approve-stage", "prep-set-rotate", "prep-crop", "prep-pick"):
        assert action in P.APP_SCRIPT and action in A.ACTIONS


def test_no_third_party_fonts_or_scripts_remain():
    for path in ("tools/review_card_html.py", "tools/prep_sheet_html.py", "webapp/ui.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "fonts.googleapis" not in src and "fonts.gstatic" not in src, path
        assert '<link rel="stylesheet" href="http' not in src, path
        assert '<script src="http' not in src, path


def test_the_shell_bar_is_one_nav_with_labels():
    bar = ui.bar(stores=["default", "junk"], store="junk", path="/")
    assert bar.count("<nav") == 1 and 'aria-label="The Fair Hand"' in bar
    assert 'role="status"' in bar
    assert "http://" not in bar and "https://" not in bar
    assert ui.wrap('<meta charset="utf-8">\n<p>x</p>', bar).startswith('<meta charset="utf-8">\n<style>')
