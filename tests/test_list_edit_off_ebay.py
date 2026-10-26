"""Off-eBay drafts: `meta.ebay_eligible: false` and `meta.channel: "mall"`.

An item eBay won't carry (adult policy 4278 held the October 1966 Playboy,
listing 880046873226) is marked in its draft.md and sold elsewhere. The mark
must stop every list_edit.py path that writes to eBay before any API call and
before the draft or ledger is touched, and `status` must show it and name
"off eBay" as the next action. Read-only commands keep working, and an
eligible draft is unaffected.

Run:  pytest tests/test_list_edit_off_ebay.py
"""
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_list_edit import (L, _Creds, _ledger_rows, _patch_sync_collaborators,  # noqa: E402
                            _write_single_draft)
from test_status import _clean_shoot, _point_stores_at  # noqa: E402
import status as st  # noqa: E402
from draft_io import ebay_block_reason  # noqa: E402

REASON = "eBay 4278: Playmate documented as 17 when photographed"
INELIGIBLE = ["ebay_eligible: false", f'ebay_ineligible_reason: "{REASON}."']
MALL = ['channel: "mall"']


def _mark(draft: Path, lines) -> Path:
    """Add `lines` under the draft's meta: block."""
    text = draft.read_text(encoding="utf-8")
    add = "".join(f"  {ln}\n" for ln in lines)
    draft.write_text(text.replace("meta:\n", "meta:\n" + add, 1), encoding="utf-8")
    return draft


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "listings_ledger.csv"
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(path))
    return path


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

    def offers(sku, creds=None):
        calls.append(("GET", f"offers for {sku}"))
        raise L.EbayAPIError(404, "no offer yet")
    monkeypatch.setattr(L, "api_send", send)
    monkeypatch.setattr(L, "get_offers_for_sku", offers)
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


MARKS = {
    "ineligible": (INELIGIBLE, f"INELIGIBLE for eBay: {REASON}"),
    "channel-mall": (MALL, "channel is mall, not eBay"),
}
WRITE_CMDS = [
    ["--record"],
    ["--review"],
    ["--sync"],
    ["--publish"],
    ["--publish", "--confirm"],
    ["--list"],
    ["--list", "--confirm"],
    ["--update", "--fields", "price"],
    ["--update", "--fields", "price", "--dry-run"],
]


@pytest.mark.parametrize("mark", MARKS, ids=list(MARKS))
@pytest.mark.parametrize("cmd", WRITE_CMDS, ids=[" ".join(c) for c in WRITE_CMDS])
def test_an_off_ebay_draft_never_reaches_ebay(tmp_path, ledger, api, monkeypatch, capsys,
                                              mark, cmd):
    """Every eBay-writing command, dry run or not, refuses the draft with one
    line naming the reason, before any eBay call or local write."""
    lines, why = MARKS[mark]
    draft = _mark(_write_single_draft(tmp_path, offer_id="OFFER-1"), lines)
    before = draft.read_text(encoding="utf-8")

    with _patch_sync_collaborators():
        code, out, err = _cli(monkeypatch, capsys, cmd[0], str(draft.parent), *cmd[1:])

    assert code != 0
    assert "[OK]" not in out and "[LIVE]" not in out and "[DRY RUN]" not in out
    refusal = [ln for ln in err.splitlines() if "EbayIneligibleError" in ln]
    assert len(refusal) == 1, err
    assert why in refusal[0] and "nothing was sent to eBay" in refusal[0]
    assert api == [], f"no eBay call for an off-eBay draft, got {api}"
    assert draft.read_text(encoding="utf-8") == before
    assert not ledger.exists()


@pytest.mark.parametrize("call", ["sync", "publish", "update", "record", "review"])
def test_the_library_functions_refuse_without_the_cli(tmp_path, ledger, api, call):
    """Other tools call these directly, so each one checks for itself."""
    draft = _mark(_write_single_draft(tmp_path, offer_id="OFFER-1"), INELIGIBLE)
    run = {
        "sync": lambda: L.create_or_update_listing(draft, creds=_Creds()),
        "publish": lambda: L.publish_offer(draft, creds=_Creds(), confirm=True),
        "update": lambda: L.update_listing_fields(draft, ["price"], creds=_Creds()),
        "record": lambda: L.record_draft(draft),
        "review": lambda: L.build_review_card(draft, creds=_Creds()),
    }[call]
    with _patch_sync_collaborators(), pytest.raises(L.EbayIneligibleError, match="4278"):
        run()
    assert api == []
    assert not ledger.exists()


@pytest.mark.parametrize("mark", MARKS, ids=list(MARKS))
def test_validate_reports_an_off_ebay_draft_as_a_hard_fail(tmp_path, monkeypatch, capsys, mark):
    lines, why = MARKS[mark]
    draft = _mark(_write_single_draft(tmp_path), lines)

    code, out, _ = _cli(monkeypatch, capsys, "--validate", str(draft))

    assert code == 1
    assert "[X]" in out and why in out
    assert L.validate_draft_for_sync(draft) and "sync-ready" not in out


@pytest.mark.parametrize("cmd", [["--status"], ["--end"]], ids=["status", "end"])
def test_read_only_and_ending_commands_still_work(tmp_path, ledger, api, monkeypatch,
                                                  capsys, cmd):
    """Looking an item up, or taking it off eBay, is what an off-eBay item needs."""
    draft = _mark(_write_single_draft(tmp_path, offer_id="OFFER-1"), INELIGIBLE + MALL)

    code, out, err = _cli(monkeypatch, capsys, cmd[0], str(draft), *cmd[1:])

    assert code == 0, err
    assert "EbayIneligibleError" not in err
    assert api, "the read-only command asked eBay"


@pytest.mark.parametrize("lines", [
    [],
    ["ebay_eligible: true", 'channel: ""'],
    ['ebay_eligible: "yes"', 'channel: "ebay"'],
], ids=["unmarked", "explicit-true", "channel-ebay"])
def test_an_eligible_draft_still_syncs(tmp_path, ledger, api, monkeypatch, capsys, lines):
    """Control for the refusals above: the same draft, marked for eBay, syncs."""
    draft = _mark(_write_single_draft(tmp_path), lines)

    with _patch_sync_collaborators():
        code, out, err = _cli(monkeypatch, capsys, "--sync", str(draft))

    assert code == 0, err
    assert ("POST", "/sell/inventory/v1/offer") in api
    assert {r["status"] for r in _ledger_rows(ledger)} == {"SYNCED"}


@pytest.mark.parametrize("value", ["false", '"false"', '"no"', "0", '"nope"'])
def test_ebay_eligible_fails_closed(value):
    """Anything that isn't missing or true-like keeps the item off eBay."""
    fm = {"meta": {"ebay_eligible": yaml.safe_load(value)}}
    assert ebay_block_reason(fm) == "INELIGIBLE for eBay: no reason given"


# --------------------------------------------------------------------------
# status: the mark shows, and the next action is off eBay
# --------------------------------------------------------------------------
def _status_shoot(tmp_path, monkeypatch, lines):
    _point_stores_at(monkeypatch, tmp_path)
    shoot = _clean_shoot(tmp_path, sku="82b186af", store="junk")
    _mark(shoot / "draft.md", lines)
    return shoot


def test_status_shows_ineligible_and_mall_as_next_action(tmp_path, monkeypatch):
    shoot = _status_shoot(tmp_path, monkeypatch, INELIGIBLE + MALL)

    state = st.gather(shoot)
    text = st.summary(state)

    assert state["ebay_eligible"] is False and state["channel"] == "mall"
    assert state["next_action"] == "off eBay (mall booth)"
    assert f"INELIGIBLE for eBay: {REASON}; channel: mall" in text
    assert text.splitlines()[-1] == "→ off eBay (mall booth)"


def test_status_channel_alone_is_off_ebay(tmp_path, monkeypatch):
    shoot = _status_shoot(tmp_path, monkeypatch, MALL)

    state = st.gather(shoot)

    assert state["ebay_eligible"] is True
    assert state["next_action"] == "off eBay (mall booth)"
    assert "OFF eBay; channel: mall" in st.summary(state)


def test_status_of_an_eligible_draft_is_unchanged(tmp_path, monkeypatch):
    shoot = _status_shoot(tmp_path, monkeypatch, [])

    state = st.gather(shoot)
    text = st.summary(state)

    assert state["ebay_eligible"] is True and state["channel"] is None
    assert not state["next_action"].startswith("off eBay")
    assert "INELIGIBLE" not in text and "OFF eBay" not in text
