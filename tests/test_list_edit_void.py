"""`list_edit.py --void <sku>`: a superseded DRAFTED row can be retired.

A draft that was replaced before it reached eBay still has a DRAFTED row, and
the old draft.md still carries its SKU. --void marks the row VOIDED (dry run
unless --confirm); after that --record, --sync and --list refuse the SKU
before anything reaches eBay. A row with an eBay offer/listing id, or one past
DRAFTED, is a real eBay object and is refused.

Run:  pytest tests/test_list_edit_void.py
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_list_edit import (L, _Creds, _ledger_rows, _patch_sync_collaborators,  # noqa: E402
                            _write_single_draft)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "listings_ledger.csv"
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(path))
    return path


def _no_offer(sku, creds=None):
    raise L.EbayAPIError(404, "This Offer is not available.")


@pytest.fixture(autouse=True)
def ebay_offers(monkeypatch):
    """--void asks eBay for the SKU's offers. Default: eBay has none (404)."""
    monkeypatch.setattr(L, "get_offers_for_sku", _no_offer)
    monkeypatch.setattr(L, "load_credentials", lambda store=None: _Creds())
    return lambda fn: monkeypatch.setattr(L, "get_offers_for_sku", fn)


@pytest.fixture
def api(monkeypatch):
    """Fake eBay that records every call; a new SKU has no offer yet."""
    calls = []

    def send(method, path, body=None, creds=None, **kw):
        calls.append((method, path))
        if "/offer?sku=" in path:
            raise L.EbayAPIError(404, "no offer yet")
        if method == "POST" and path.endswith("/offer"):
            return {"offerId": "OFFER-9"}
        return {}
    monkeypatch.setattr(L, "api_send", send)
    monkeypatch.setattr(L, "load_credentials", lambda store=None: _Creds())
    return calls


def _cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["list_edit.py", *argv])
    code = 0
    try:
        L._cli()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out = capsys.readouterr()
    return code, out.out, out.err


def _row(ledger, sku):
    return next(r for r in _ledger_rows(ledger) if r["sku"] == sku)


def test_void_a_drafted_row_dry_run_then_confirm(tmp_path, ledger, monkeypatch, capsys):
    sku, _ = L.record_draft(_write_single_draft(tmp_path))
    before = _row(ledger, sku)

    code, out, err = _cli(monkeypatch, capsys, "--void", sku)
    assert code == 0, err
    assert "[DRY RUN]" in out and "[OK]" not in out
    assert _row(ledger, sku) == before

    code, out, err = _cli(monkeypatch, capsys, "--void", sku, "--confirm")
    assert code == 0, err
    assert f"[OK] voided sku {sku}" in out
    after = _row(ledger, sku)
    assert after["status"] == "VOIDED"
    assert (after["title"], after["price"], after["drafted_at"]) == \
        (before["title"], before["price"], before["drafted_at"])
    assert len(_ledger_rows(ledger)) == 1


@pytest.mark.parametrize("status,ids", [
    ("PUBLISHED", {"offer_id": "OFFER-1", "listing_id": "LISTING-1"}),
    ("SYNCED", {"offer_id": "OFFER-1"}),
    ("DRAFTED", {"offer_id": "OFFER-1"}),
    ("ENDED", {}),
])
def test_void_refuses_a_row_that_is_on_ebay_or_past_drafted(
        tmp_path, ledger, monkeypatch, capsys, status, ids):
    sku = "ab12cd34"
    L.upsert_listing(sku, status, title="Vintage Widget", **ids)
    before = _row(ledger, sku)

    code, out, err = _cli(monkeypatch, capsys, "--void", sku, "--confirm")
    assert code != 0
    assert "[OK]" not in out
    assert "refusing to void" in err
    assert _row(ledger, sku) == before


def test_void_refuses_an_unknown_sku(ledger, monkeypatch, capsys):
    code, out, err = _cli(monkeypatch, capsys, "--void", "deadbeef", "--confirm")
    assert code != 0 and "[OK]" not in out
    assert "no row for sku deadbeef" in err
    assert not ledger.exists()


@pytest.mark.parametrize("cmd", [["--sync"], ["--list", "--confirm"], ["--record"]])
def test_a_voided_sku_never_reaches_ebay(tmp_path, ledger, api, monkeypatch, capsys, cmd):
    """The stray draft.md still carries the voided SKU; every path that could
    create an offer or revive the row refuses it before calling eBay."""
    draft = _write_single_draft(tmp_path)
    sku, _ = L.record_draft(draft)
    L.void_listing(sku, confirm=True)
    draft_before = draft.read_text(encoding="utf-8")

    with _patch_sync_collaborators():
        code, out, err = _cli(monkeypatch, capsys, cmd[0], str(draft), *cmd[1:])

    assert code != 0
    assert "[OK]" not in out and "[LIVE]" not in out
    assert f"sku {sku} is VOIDED" in err
    assert api == [], f"no eBay call for a voided SKU, got {api}"
    assert _row(ledger, sku)["status"] == "VOIDED"
    assert draft.read_text(encoding="utf-8") == draft_before


def test_sync_still_creates_an_offer_for_a_sku_that_is_not_voided(
        tmp_path, ledger, api, monkeypatch, capsys):
    """Control for the test above: the same draft syncs when nothing is voided."""
    draft = _write_single_draft(tmp_path)
    sku, _ = L.record_draft(draft)
    L.upsert_listing("other000", "VOIDED")

    with _patch_sync_collaborators():
        code, out, err = _cli(monkeypatch, capsys, "--sync", str(draft))

    assert code == 0, err
    assert ("POST", "/sell/inventory/v1/offer") in api
    assert _row(ledger, sku)["status"] == "SYNCED"


def _offers(*offers):
    return lambda sku, creds=None: list(offers)


def _raises(exc):
    def get(sku, creds=None):
        raise exc
    return get


@pytest.mark.parametrize("get_offers,confirm,voided,err_has", [
    # The ledger row is clean, but eBay has an offer the ledger never saw.
    (_offers({"offerId": "OFFER-7", "status": "UNPUBLISHED"}), True, False,
     "OFFER-7 (UNPUBLISHED)"),
    (_offers({"offerId": "OFFER-7", "status": "PUBLISHED"}), False, False,
     "OFFER-7 (PUBLISHED)"),
    # eBay's 404 means no offer exists: a true local-only draft.
    (_no_offer, True, True, ""),
    # Any other answer means we don't know, so nothing is written.
    (_raises(L.EbayAPIError(500, "Internal error")), True, False, "Internal error"),
    (_raises(L.EbayAPIError(401, "Invalid access token")), True, False, "Invalid access token"),
    (_raises(L.EbayAuthError("no refresh token")), True, False, "no refresh token"),
], ids=["offer-confirm", "offer-dry-run", "404-voids", "500", "401", "auth"])
def test_void_checks_ebay_for_offers_before_writing(
        ledger, ebay_offers, monkeypatch, capsys, get_offers, confirm, voided, err_has):
    sku = "75af8857"
    L.upsert_listing(sku, "DRAFTED", title="Salt Glaze Candlestick", store="junk")
    before = _row(ledger, sku)
    ebay_offers(get_offers)

    argv = ["--void", sku, "--store", "junk"] + (["--confirm"] if confirm else [])
    code, out, err = _cli(monkeypatch, capsys, *argv)

    if voided:
        assert code == 0, err
        assert f"[OK] voided sku {sku}" in out
        assert _row(ledger, sku)["status"] == "VOIDED"
    else:
        assert code != 0
        assert "[OK]" not in out and "[DRY RUN]" not in out
        assert err_has in err
        assert _row(ledger, sku) == before
