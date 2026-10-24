"""A failed listings-ledger write must never pass as success.

On 2026-10-23 a sandboxed session had the ledger mounted read-only.
upsert_listing() swallowed the OSError, and `list_edit.py --record` still
printed "[OK] recorded DRAFTED" with no row written. These tests drive the
real CLI against a ledger path that can't be written and check what the
operator sees: --record, --review, --sync and --list print
"[FAIL] ledger write: <path>: <error>" and exit non-zero with no [OK];
--sync names the eBay offer it made; --list stops before it
publishes; --publish shows the live listing, then fails.

Run:  pytest tests/test_ledger_write_fail.py
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_list_edit import L, _POLICIES, _write_single_draft  # noqa: E402  (also puts lib/ on sys.path)
from ebay_client import EbayAPIError  # noqa: E402

ROOT = HERE.parent


class _Creds:
    has_user = True
    store = "default"
    environment = "sandbox"


@pytest.fixture
def ro_ledger(tmp_path, monkeypatch):
    """Point $EBAYBIZ_LISTINGS_LEDGER at a path that can't be written: a file
    in a read-only directory, or under a regular file when running as root
    (root ignores mode bits). Yields the ledger path."""
    as_root = getattr(os, "geteuid", lambda: 0)() == 0
    parent = tmp_path / ("not-a-dir" if as_root else "ro")
    if as_root:
        parent.write_text("")
    else:
        parent.mkdir()
        parent.chmod(0o555)
    path = parent / "listings_ledger.csv"
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(path))
    yield path
    if parent.is_dir():
        parent.chmod(0o755)


def _run_cli(monkeypatch, capsys, *argv):
    """Run list_edit's CLI in-process. Returns (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["list_edit.py", *argv])
    code = 0
    try:
        L._cli()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out = capsys.readouterr()
    return code, out.out, out.err


def _fake_api(monkeypatch, responses):
    calls = []

    def send(method, path, body=None, creds=None, **kw):
        calls.append((method, path))
        for pat, resp in responses.items():
            if pat in path:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        if method == "POST" and path.endswith("/offer"):
            return {"offerId": "OFFER-9"}
        return {}
    monkeypatch.setattr(L, "api_send", send)
    return calls


def _fake_creds(monkeypatch):
    monkeypatch.setattr(L, "load_credentials", lambda store=None, **kw: _Creds())


def _fake_sync_collaborators(monkeypatch):
    """Everything create_or_update_listing() needs besides api_send and the
    ledger (the same stand-ins as tests/test_list_edit.py)."""
    _fake_creds(monkeypatch)
    for name, fn in {
        "_resolve_policies_and_location": lambda creds: (dict(_POLICIES), "LOC-1"),
        "get_allowed_condition_ids": lambda *a, **kw: (set(), False),
        "category_uses_conditions": lambda *a, **kw: None,
        "_resolve_shipping_policy": lambda draft, policies, creds, strict=False: (policies["fulfillment"], []),
        "_assert_photos_cleared": lambda paths: None,
        "upload_site_hosted_picture": lambda data, picture_name=None, creds=None: f"https://eps/{picture_name}.jpg",
    }.items():
        monkeypatch.setattr(L, name, fn)


def _assert_failed(code, out, err, ledger):
    assert code != 0, f"expected a non-zero exit, got {code}\nstdout:\n{out}"
    assert "[OK]" not in out, f"a failed ledger write must not print [OK]:\n{out}"
    assert f"[FAIL] ledger write: {ledger}: " in err, err
    assert not ledger.exists()


def test_record_cli_fails_when_ledger_is_unwritable(tmp_path, ro_ledger):
    """The command from the incident, run the way the pipeline runs it."""
    draft = _write_single_draft(tmp_path)
    res = subprocess.run([sys.executable, str(ROOT / "lib" / "list_edit.py"),
                          "--record", str(draft.parent)],
                         capture_output=True, text=True)
    _assert_failed(res.returncode, res.stdout, res.stderr, ro_ledger)


def test_record_cli_still_reports_ok_when_ledger_is_writable(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "listings_ledger.csv"
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))
    draft = _write_single_draft(tmp_path)
    code, out, err = _run_cli(monkeypatch, capsys, "--record", str(draft))
    assert code == 0, err
    assert "[OK] recorded DRAFTED" in out
    assert f"ledger: {ledger}" in out
    assert "[FAIL]" not in err and "[WARN]" not in err


def test_review_cli_fails_when_ledger_is_unwritable(tmp_path, ro_ledger, monkeypatch, capsys):
    _fake_creds(monkeypatch)
    draft = _write_single_draft(tmp_path)
    code, out, err = _run_cli(monkeypatch, capsys, "--review", str(draft))
    _assert_failed(code, out, err, ro_ledger)
    assert "[review_card]" not in out


def test_list_cli_fails_before_publishing_when_ledger_is_unwritable(
        tmp_path, ro_ledger, monkeypatch, capsys):
    _fake_sync_collaborators(monkeypatch)
    calls = _fake_api(monkeypatch, {"/offer?sku=": EbayAPIError(404, "no offer yet"),
                                    "/publish": {"listingId": "999888777"}})
    draft = _write_single_draft(tmp_path)
    code, out, err = _run_cli(monkeypatch, capsys, "--list", str(draft), "--confirm")
    _assert_failed(code, out, err, ro_ledger)
    assert "[LIVE]" not in out
    assert not [c for c in calls if c[1].endswith("/publish")], (
        "--list must not publish a listing the ledger can't record")


def test_publish_cli_shows_live_listing_then_fails_when_published_row_is_unwritable(
        tmp_path, ro_ledger, monkeypatch, capsys):
    """Once eBay has published, the listing is live whatever the ledger says:
    show the operator the listing, then fail on the missing row."""
    _fake_creds(monkeypatch)
    _fake_api(monkeypatch, {
        "/offer/OFFER-1/publish": {"listingId": "999888777"},
        "/offer/OFFER-1": {"status": "UNPUBLISHED", "sku": "abc12300",
                           "pricingSummary": {"price": {"value": "24.99"}}},
    })
    draft = _write_single_draft(tmp_path, sku="abc12300", offer_id="OFFER-1")
    code, out, err = _run_cli(monkeypatch, capsys, "--publish", str(draft), "--confirm")
    _assert_failed(code, out, err, ro_ledger)
    assert "[LIVE] Published offer OFFER-1 -> listing 999888777" in out


def test_sync_cli_fails_and_names_the_offer_when_ledger_is_unwritable(
        tmp_path, ro_ledger, monkeypatch, capsys):
    """The eBay offer exists by the time the SYNCED row fails: fail,
    and say which offer so the operator knows it is on eBay."""
    _fake_sync_collaborators(monkeypatch)
    _fake_api(monkeypatch, {"/offer?sku=": EbayAPIError(404, "no offer yet")})
    draft = _write_single_draft(tmp_path)
    code, out, err = _run_cli(monkeypatch, capsys, "--sync", str(draft))
    _assert_failed(code, out, err, ro_ledger)
    assert "eBay offer OFFER-9 " in err, err
