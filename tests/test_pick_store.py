#!/usr/bin/env python3
"""lib/pick_store.py — the temp store behind a pick-sheet link (GH #151).

Every test drives a throwaway store directory, so nothing here can read,
serve or delete a real sheet (those hold buyer PII). No network: the one
function that would touch it, server_is_up(), is only exercised through its
failure path against a closed port.

Run:  python tests/test_pick_store.py
  or: pytest tests/test_pick_store.py
"""
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import pick_store  # noqa: E402

SHEET = "<!doctype html><html><body>Jamie Buyer, 12 Elm St</body></html>"


def _store() -> Path:
    return Path(tempfile.mkdtemp(prefix="pickstore-test-")) / ".served"


def test_publish_returns_a_link_not_a_path():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["03-1-2"], store_dir=store)
        assert sheet.url() == f"http://127.0.0.1:8770/pick/{sheet.token}"
        assert sheet.url(port=9000).startswith("http://127.0.0.1:9000/pick/")
        html, status = pick_store.fetch(sheet.token, store_dir=store)
        assert status == "ok"
        assert "Jamie Buyer" in html
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_token_is_unguessable_and_carries_no_order_id():
    store = _store()
    try:
        a = pick_store.publish(SHEET, order_ids=["03-11111-22222"], store_dir=store)
        b = pick_store.publish(SHEET, order_ids=["03-11111-22222"], store_dir=store)
        assert a.token != b.token
        # 32 random bytes, base64url -> 43 chars. The guardrail is the entropy,
        # not the exact length, so assert the floor.
        assert len(a.token) >= 40
        assert "03-11111-22222" not in a.token
        assert "11111" not in a.url()
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_expired_sheet_is_gone_and_reported_as_expired():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["old-1"],
                                   ttl_hours=-1, store_dir=store)  # already past
        html, status = pick_store.fetch(sheet.token, store_dir=store)
        assert status == "expired"
        assert html is None
        # deleted on read — expiry can't wait for a sweep that may never run
        assert not sheet.path.exists()
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "missing"
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_purge_expired_sweeps_old_sheets_and_orphan_html():
    store = _store()
    try:
        live = pick_store.publish(SHEET, order_ids=["live-1"], store_dir=store)
        dead = pick_store.publish(SHEET, order_ids=["dead-1"],
                                  ttl_hours=-1, store_dir=store)
        orphan = store / "orphan_token_with_no_meta.html"
        orphan.write_text(SHEET, encoding="utf-8")

        assert pick_store.purge_expired(store_dir=store) == 2
        assert not dead.path.exists()
        assert not orphan.exists()
        assert live.path.exists()
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_publish_sweeps_expired_sheets_on_the_way_in():
    store = _store()
    try:
        dead = pick_store.publish(SHEET, order_ids=["dead-1"],
                                  ttl_hours=-1, store_dir=store)
        pick_store.publish(SHEET, order_ids=["new-1"], store_dir=store)
        assert not dead.path.exists()
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_live_sheet_for_reuses_the_same_link_for_the_same_shipment():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["a-1", "a-2"], store_dir=store)
        again = pick_store.live_sheet_for(["a-2", "a-1"], store_dir=store)
        assert again is not None and again.token == sheet.token
        # a different shipment is a different sheet, never this one
        assert pick_store.live_sheet_for(["a-1"], store_dir=store) is None
        assert pick_store.live_sheet_for([], store_dir=store) is None
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_expired_sheet_is_not_offered_for_reuse():
    store = _store()
    try:
        pick_store.publish(SHEET, order_ids=["b-1"], ttl_hours=-1, store_dir=store)
        assert pick_store.live_sheet_for(["b-1"], store_dir=store) is None
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_revoke_by_order_id_and_by_token_kills_the_link():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["c-1"], store_dir=store)
        assert pick_store.revoke(order_id="c-1", store_dir=store) == [sheet.token]
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "missing"

        other = pick_store.publish(SHEET, order_ids=["c-2"], store_dir=store)
        assert pick_store.revoke(token=other.token, store_dir=store) == [other.token]
        assert pick_store.fetch(other.token, store_dir=store)[1] == "missing"

        assert pick_store.revoke(order_id="never-published", store_dir=store) == []
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_a_traversal_token_never_reaches_the_filesystem():
    store = _store()
    store.mkdir(parents=True, exist_ok=True)
    secret = store.parent / "secret.html"
    secret.write_text("SECRET", encoding="utf-8")
    try:
        for bad in ("../secret", "..\\secret", "a/b", "", "short", "a" * 200):
            assert pick_store.valid_token(bad) is False
            assert pick_store.fetch(bad, store_dir=store) == (None, "missing")
        assert secret.read_text(encoding="utf-8") == "SECRET"
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_sheet_without_a_parsable_expiry_is_served_not_guessed_at():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["d-1"], store_dir=store)
        meta = store / f"{sheet.token}.json"
        meta.write_text(meta.read_text(encoding="utf-8").replace(
            sheet.expires_at, "not-a-date"), encoding="utf-8")
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "ok"
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_ttl_is_recorded_on_the_sheet():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["e-1"], ttl_hours=2, store_dir=store)
        created = pick_store._parse_iso(sheet.created_at)
        expires = pick_store._parse_iso(sheet.expires_at)
        assert expires - created == timedelta(hours=2)
        assert sheet.is_expired() is False
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_server_is_up_is_false_when_nothing_is_listening():
    # port 1 on loopback: nothing serves there, and the call must answer
    # False rather than raise — the tool prints a warning off this.
    assert pick_store.server_is_up(port=1, timeout=0.5) is False


def test_republishing_a_shipment_replaces_its_live_link():
    # One shipment, one live URL. A re-render supersedes the last link
    # instead of leaving two working sheets for the same box.
    store = _store()
    try:
        first = pick_store.publish(SHEET, order_ids=["r-1"], store_dir=store)
        second = pick_store.publish(SHEET, order_ids=["r-1"], store_dir=store)
        assert first.token != second.token
        assert pick_store.fetch(first.token, store_dir=store)[1] == "missing"
        assert pick_store.fetch(second.token, store_dir=store)[1] == "ok"
        assert len(pick_store.list_sheets(store_dir=store)) == 1
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_a_grouped_sheet_supersedes_the_single_sheets_it_covers():
    store = _store()
    try:
        solo = pick_store.publish(SHEET, order_ids=["g-1"], store_dir=store)
        group = pick_store.publish(SHEET, order_ids=["g-1", "g-2"], store_dir=store)
        assert pick_store.fetch(solo.token, store_dir=store)[1] == "missing"
        assert pick_store.fetch(group.token, store_dir=store)[1] == "ok"
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


# --------------------------------------------------------------------------- #
# publish(source=...) — the sheet lives in the item's folder under inventory/
# and is served FROM there, instead of from a second copy in the store.
# --------------------------------------------------------------------------- #
# Source files have to sit inside ROOT to pass publish()'s containment check,
# so they go under pick_lists/ — already gitignored, so a test that dies
# mid-run leaves nothing that can show up in git status or be committed.
SRC_DIR = ROOT / "pick_lists" / ".pickstore-test-sources"


def _sourced(store: Path, body: str = SHEET, **kw):
    """A sheet whose real file sits inside ROOT (as an inventory/ copy does),
    published by reference. Returns (sheet, source_path)."""
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    src = SRC_DIR / f"sheet-{next(_counter)}.html"
    src.write_text(body, encoding="utf-8")
    sheet = pick_store.publish("IGNORED COPY", order_ids=["src-1"],
                               store_dir=store, source=src, **kw)
    return sheet, src


_counter = iter(range(1, 10_000))


def test_a_sourced_sheet_is_served_from_its_file_not_from_a_stored_copy():
    store = _store()
    sheet, src = _sourced(store)
    try:
        html, status = pick_store.fetch(sheet.token, store_dir=store)
        assert status == "ok"
        assert "Jamie Buyer" in html          # the source file's content
        assert "IGNORED COPY" not in html     # not the html passed to publish()
        # nothing was copied into the store; only the meta file is there
        assert not (store / f"{sheet.token}.html").exists()
        assert (store / f"{sheet.token}.json").exists()
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_editing_the_source_file_changes_what_the_link_serves():
    """Re-rendering the sheet in the item's folder updates the live link
    without republishing it — the store holds a pointer, not a snapshot."""
    store = _store()
    sheet, src = _sourced(store)
    try:
        src.write_text("<html>re-rendered, Greensboro NC</html>", encoding="utf-8")
        html, status = pick_store.fetch(sheet.token, store_dir=store)
        assert status == "ok" and "re-rendered" in html
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_expiry_stops_the_link_but_never_deletes_the_sellers_file():
    store = _store()
    sheet, src = _sourced(store, ttl_hours=-1)
    try:
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "expired"
        assert src.exists(), "expiry deleted the item folder's own sheet"
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_revoke_stops_the_link_but_never_deletes_the_sellers_file():
    store = _store()
    sheet, src = _sourced(store)
    try:
        assert pick_store.revoke(token=sheet.token, store_dir=store) == [sheet.token]
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "missing"
        assert src.exists(), "revoke deleted the item folder's own sheet"
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_purge_expired_never_touches_a_sourced_sheets_file():
    store = _store()
    sheet, src = _sourced(store, ttl_hours=-1)
    try:
        assert pick_store.purge_expired(store_dir=store) == 1
        assert src.exists(), "the orphan sweep reached outside the store"
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_a_source_that_escapes_the_repo_is_refused_and_falls_back_to_a_copy():
    """The one field in the store that is a path, checked on the way in. An
    outside path is not served from; the passed html is stored instead, so the
    link still answers rather than becoming a 404 with extra steps."""
    store = _store()
    outside = Path(tempfile.mkdtemp(prefix="pickstore-outside-")) / "sheet.html"
    outside.write_text("<html>OUTSIDE THE REPO</html>", encoding="utf-8")
    try:
        sheet = pick_store.publish(SHEET, order_ids=["esc-1"],
                                   store_dir=store, source=outside)
        assert sheet.source is None
        html, status = pick_store.fetch(sheet.token, store_dir=store)
        assert status == "ok"
        assert "Jamie Buyer" in html
        assert "OUTSIDE THE REPO" not in html
    finally:
        shutil.rmtree(outside.parent, ignore_errors=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_a_source_that_does_not_exist_falls_back_to_a_stored_copy():
    store = _store()
    try:
        sheet = pick_store.publish(SHEET, order_ids=["gone-1"], store_dir=store,
                                   source=ROOT / "inventory" / "nope" / "x.html")
        assert sheet.source is None
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "ok"
    finally:
        shutil.rmtree(store.parent, ignore_errors=True)


def test_a_sourced_sheet_whose_file_vanished_reads_as_missing():
    store = _store()
    sheet, src = _sourced(store)
    try:
        src.unlink()
        assert pick_store.fetch(sheet.token, store_dir=store)[1] == "missing"
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_a_sourced_sheet_is_still_reused_and_superseded_by_order_id():
    """source changes where the bytes come from, not the one-live-link-per-
    shipment rule."""
    store = _store()
    sheet, src = _sourced(store)
    try:
        assert pick_store.live_sheet_for(["src-1"], store_dir=store).token == sheet.token
        second, src2 = _sourced(store)
        try:
            assert pick_store.fetch(sheet.token, store_dir=store)[1] == "missing"
            assert pick_store.fetch(second.token, store_dir=store)[1] == "ok"
        finally:
            src2.unlink(missing_ok=True)
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_root_is_read_at_call_time_not_captured_at_import():
    """Regression: _resolve_source() used to take `root` as a default argument,
    which froze ROOT at import while _rel_to_root() read the global at call
    time. One binding early and the other late meant a repointed ROOT wrote a
    meta file it could not then read back, and the sheet silently fell back to
    a stored copy instead of being served from its file."""
    store = _store()
    other_root = Path(tempfile.mkdtemp(prefix="pickstore-root-"))
    src = other_root / "inventory" / "thing" / "pick_1-2-3.html"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("<html>from the other root</html>", encoding="utf-8")
    real_root = pick_store.ROOT
    try:
        pick_store.ROOT = other_root
        sheet = pick_store.publish("FALLBACK COPY", order_ids=["root-1"],
                                   store_dir=store, source=src)
        assert sheet.source == src.resolve(), "source rejected under a repointed ROOT"
        html, status = pick_store.fetch(sheet.token, store_dir=store)
        assert status == "ok"
        assert "from the other root" in html
        assert "FALLBACK COPY" not in html
    finally:
        pick_store.ROOT = real_root
        shutil.rmtree(other_root, ignore_errors=True)
        shutil.rmtree(store.parent, ignore_errors=True)


def test_the_meta_file_records_the_source_repo_relative():
    """So the pointer survives the checkout moving."""
    import json
    store = _store()
    sheet, src = _sourced(store)
    try:
        meta = json.loads((store / f"{sheet.token}.json").read_text(encoding="utf-8"))
        assert not Path(meta["source"]).is_absolute()
        assert meta["source"] == src.resolve().relative_to(ROOT).as_posix()
        assert sheet.source == src.resolve()
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(store.parent, ignore_errors=True)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
