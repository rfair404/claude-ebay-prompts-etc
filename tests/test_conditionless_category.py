"""Categories with no item-condition field (Antiques > Silver, e.g. 20099).

eBay's condition policy for 20099 is `itemConditions: []`. It accepts the
condition we send, stores it on the inventory item, and never shows it to a
buyer: all 12 popsgames flatware listings read "Item condition: Not provided"
in the 2026-10-15 Listing Quality Report. Our pipeline read that empty list as
"metadata unavailable — left as-is" and passed silently. These tests pin the
distinction and the publish-path BLOCKER.

Run:  python tests/test_conditionless_category.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_list_edit import (  # noqa: E402  (also puts lib/ on sys.path)
    L, _Creds, _fake_api, _ledger_at, _patch_sync_collaborators, _patched,
    _write_single_draft,
)
import ebay_client  # noqa: E402

# Shapes as returned live on 2026-10-16.
_ANTIQUES_20099 = {"itemConditionPolicies": [{
    "categoryTreeId": "0", "categoryId": "20099",
    "itemConditionRequired": False, "itemConditions": []}]}
_FLATWARE_SETS_131608 = {"itemConditionPolicies": [{
    "categoryId": "131608", "itemConditionRequired": True,
    "itemConditions": [{"conditionId": "1000"}, {"conditionId": "1500"},
                       {"conditionId": "3000"}]}]}


def _policy(resp):
    return lambda *a, **kw: resp


def test_empty_condition_list_means_no_conditions_not_unknown():
    with _patched(ebay_client, get_item_condition_policies=_policy(_ANTIQUES_20099)):
        assert ebay_client.category_uses_conditions("20099") is False
        # The old lookup can't tell this apart from missing metadata.
        assert ebay_client.get_allowed_condition_ids("20099") == (set(), False)


def test_missing_metadata_is_none():
    with _patched(ebay_client, get_item_condition_policies=_policy({})):
        assert ebay_client.category_uses_conditions("20099") is None


def test_category_with_conditions_is_true():
    with _patched(ebay_client, get_item_condition_policies=_policy(_FLATWARE_SETS_131608)):
        assert ebay_client.category_uses_conditions("131608") is True


def _strip_condition_description(draft_path: Path) -> None:
    text = draft_path.read_text(encoding="utf-8")
    draft_path.write_text(
        text.replace('condition_description: "Light shelf wear."\n', ""), encoding="utf-8")


def test_sync_blocks_conditionless_category_with_no_condition_description():
    """The drop: NEW_OTHER goes to eBay, the buyer sees nothing at all."""
    send, calls = _fake_api({"/offer?sku=": L.EbayAPIError(404, "no offer yet")})
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        _strip_condition_description(draft_path)
        with _patch_sync_collaborators(), \
                _patched(L, api_send=send, category_uses_conditions=lambda *a, **kw: False), \
                _ledger_at(Path(td)):
            try:
                L.create_or_update_listing(draft_path, creds=_Creds())
            except ValueError as e:
                assert "BLOCKER" in str(e) and "condition_description" in str(e)
            else:
                raise AssertionError("sync must refuse a draft whose buyer would see no condition")
    writes = [c for c in calls if c[0] in ("POST", "PUT")]
    assert not writes, f"the blocker must fire before any eBay write: {writes}"


def test_sync_allows_conditionless_category_with_condition_description():
    send, _calls = _fake_api({"/offer?sku=": L.EbayAPIError(404, "no offer yet"),
                              "/offer": {"offerId": "OFFER-1"}})
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        with _patch_sync_collaborators(), \
                _patched(L, api_send=send, category_uses_conditions=lambda *a, **kw: False), \
                _ledger_at(Path(td)):
            assert L.create_or_update_listing(draft_path, creds=_Creds()).operation == "created"


def test_check_says_what_buyer_sees():
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        draft = L.parse_draft(draft_path)
        with _patched(L, category_uses_conditions=lambda *a, **kw: False):
            line, blocking = L._conditionless_category_check(draft, "20099", _Creds())
        assert not blocking and "buyers see no condition" in line
        with _patched(L, category_uses_conditions=lambda *a, **kw: None):
            assert L._conditionless_category_check(draft, "20099", _Creds()) is None


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
