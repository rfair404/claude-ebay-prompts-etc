#!/usr/bin/env python3
"""tools/pick_list_html.py — what the rendered sheet does and doesn't carry.

render_html() is otherwise covered indirectly (it's the tool behind the
`/pick/{token}` route). These lock down two rules: the sheet carries no haiku
(GH #161's block was dropped from the template), and a same-buyer,
same-address group renders every order's items onto the one page.

Run:  python tests/test_pick_list_html.py
  or: pytest tests/test_pick_list_html.py
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

pick_list_html = pytest.importorskip(
    "pick_list_html", reason="pick_list_html imports numpy/Pillow")


def _money(v):
    return {"value": str(v), "currency": "USD"}


def _order(*, oid="03-11111-22222", title="Pokemon TCG Booster Box",
           fullname="Jamie Buyer", city="Springfield", state="OH"):
    return {
        "orderId": oid,
        "salesRecordReference": "1",
        "creationDate": "2026-08-20T12:00:00.000Z",
        "orderPaymentStatus": "PAID",
        "lineItems": [{
            "lineItemId": "11500017010", "legacyItemId": "206000000001",
            "sku": "abc123def4", "title": title, "quantity": 1,
            "lineItemCost": _money(24.99),
            "lineItemFulfillmentInstructions": {"shipByDate": "2026-08-24T00:00:00.000Z"},
        }],
        "fulfillmentStartInstructions": [{
            "shippingStep": {
                "shippingCarrierCode": "USPS", "shippingServiceCode": "USPSGround",
                "shipTo": {
                    "fullName": fullname,
                    "contactAddress": {
                        "addressLine1": "1 Test Way", "city": city,
                        "stateOrProvince": state, "postalCode": "45501",
                        "countryCode": "US",
                    },
                },
            },
        }],
        "pricingSummary": {"total": _money(24.99), "deliveryCost": _money(0)},
        "paymentSummary": {"totalDueSeller": _money(21.99)},
    }


def test_render_html_has_no_haiku():
    out = pick_list_html.render_html([_order()], [], [])
    assert "haiku" not in out.lower()


def test_same_buyer_orders_render_onto_one_page():
    a = _order(oid="03-11111-22222", title="Aristo MultiLog Slide Rule")
    b = _order(oid="03-33333-44444", title="Aristo Darmstadt Slide Rule")
    pick_list_html.assert_one_shipment([a, b])        # same buyer + address
    out = pick_list_html.render_html([a, b], [], [])
    assert "Aristo MultiLog Slide Rule" in out
    assert "Aristo Darmstadt Slide Rule" in out


def _photo(dir_: Path, listing_id: str) -> None:
    from PIL import Image
    dir_.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), (200, 120, 60)).save(dir_ / f"{listing_id}.jpg")


def test_no_local_folder_uses_the_cached_ebay_photo(tmp_path, monkeypatch):
    _photo(tmp_path, "206000000001")
    monkeypatch.setattr(pick_list_html, "EBAY_PHOTO_DIR", tmp_path)
    monkeypatch.setattr(pick_list_html, "EBAY_PHOTOS", True)
    out = pick_list_html.render_html([_order()], [], [])
    assert "data:image/jpeg;base64," in out
    assert "no photo" not in out


def test_ebay_photos_are_off_by_default_so_tests_stay_offline(tmp_path, monkeypatch):
    _photo(tmp_path, "206000000001")
    monkeypatch.setattr(pick_list_html, "EBAY_PHOTO_DIR", tmp_path)
    out = pick_list_html.render_html([_order()], [], [])
    assert "no photo" in out


def test_a_failed_ebay_lookup_still_renders_the_sheet(tmp_path, monkeypatch):
    import ebay_client

    def boom(*_a, **_k):
        raise OSError("offline")
    monkeypatch.setattr(ebay_client, "api_get", boom)
    monkeypatch.setattr(pick_list_html, "EBAY_PHOTO_DIR", tmp_path)
    monkeypatch.setattr(pick_list_html, "EBAY_PHOTOS", True)
    out = pick_list_html.render_html([_order()], [], [])
    assert "no photo" in out
    assert not any(tmp_path.iterdir())


def test_different_addresses_are_still_refused():
    a = _order(oid="03-11111-22222")
    b = _order(oid="03-33333-44444", city="Dayton")
    with pytest.raises(SystemExit):
        pick_list_html.assert_one_shipment([a, b])


# --------------------------------------------------------------------------- #
# Stores (#156 §1): the letterhead is the store's identity, and a named store
# never falls back to Pop's Games. Config is faked per test — never the real one.
# --------------------------------------------------------------------------- #
import contextlib                                                   # noqa: E402
import os                                                           # noqa: E402

import config as _config                                            # noqa: E402

MULTI = {
    "ebay": {"stores": {"junk": {}, "outlet": {}}},
    "store": {},                       # default store: nothing configured
    "storefronts": {
        "junk": {},                    # identity unset -> neutral sheet
        "outlet": {"display_name": "Outlet Bin", "tagline": "AS IS",
                   "storefront_url": "ebay.com/usr/outletbin"},
    },
}


@contextlib.contextmanager
def _cfg(cfg):
    real, saved_env = _config.load_config, os.environ.pop("EBAYBIZ_STORE", None)
    _config.load_config = lambda *a, **k: cfg
    try:
        yield
    finally:
        _config.load_config = real
        if saved_env is not None:
            os.environ["EBAYBIZ_STORE"] = saved_env


# Any trace of the default store's brand, in visible text OR page source.
_POPS = __import__("re").compile(r"pop(?:'|&#x27;|&#39;)?s[ -]?games", __import__("re").I)


def _tagged(store, **kw):
    o = _order(**kw)
    o["_store"] = store
    return o


def test_default_store_unconfigured_keeps_the_pops_games_letterhead():
    with _cfg(MULTI):
        out = pick_list_html.render_html([_order()], [], [], store="default")
    assert "POP&#x27;S GAMES" in out or "POP'S GAMES" in out
    assert "ebay.com/usr/popsgames" in out
    assert 'class="acct"' not in out          # no account notice for the default store


def test_named_store_with_no_identity_gets_a_neutral_sheet_never_pops_games():
    with _cfg(MULTI):
        out = pick_list_html.render_html([_tagged("junk")], [], [])
    assert not _POPS.search(out), _POPS.search(out)
    assert 'class="brand"' not in out         # no masthead at all
    # ...but the packer is told which account the label links need
    assert 'class="acct"' in out and "junk" in out


def test_named_store_prints_its_own_configured_letterhead():
    with _cfg(MULTI):
        out = pick_list_html.render_html([_tagged("outlet")], [], [])
    assert "Outlet Bin" in out and "ebay.com/usr/outletbin" in out
    assert not _POPS.search(out), _POPS.search(out)
    assert "the &#x27;outlet&#x27; store&#x27;s eBay account" in out         or "the 'outlet' store's eBay account" in out


def test_account_notice_is_screen_only_so_the_store_name_never_goes_in_the_box():
    with _cfg(MULTI):
        out = pick_list_html.render_html([_tagged("junk")], [], [])
    assert ".acct {{ display: none; }}".replace("{{", "{").replace("}}", "}") in out


def test_orders_from_two_stores_never_share_a_sheet():
    a = _tagged("default", oid="03-11111-22222")
    b = _tagged("junk", oid="03-33333-44444")      # same buyer, same address
    with pytest.raises(SystemExit):
        pick_list_html.assert_one_shipment([a, b])
    with _cfg(MULTI), pytest.raises(ValueError):
        pick_list_html.render_html([a, b], [], [])


def test_an_explicit_store_that_contradicts_the_orders_tag_is_refused():
    with _cfg(MULTI), pytest.raises(ValueError):
        pick_list_html.render_html([_tagged("junk")], [], [], store="outlet")


if __name__ == "__main__":
    import inspect

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)


# ---------------------------------------------------------------------------
# Letterhead follows the store the orders came from (GH #147). A junk-store
# box must never carry the main store's name, tagline or storefront URL —
# including on the "verify you're signed into ..." label-button warning.
# ---------------------------------------------------------------------------

_CFG = {
    "ebay": {},
    "store": {"display_name": "Pop's Games", "tagline": "BUY · SELL · TRADE",
              "storefront_url": "ebay.com/usr/popsgames"},
    "storefronts": {
        "junk": {"display_name": None, "returns": "none_as_is"},
        "named": {"display_name": "Bargain Bin", "storefront_url": "ebay.com/usr/bargainbin"},
    },
}


@pytest.fixture
def cfg(monkeypatch):
    import config as config_mod
    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: _CFG)
    monkeypatch.delenv("EBAYBIZ_STORE", raising=False)
    return monkeypatch


def test_default_store_letterhead_is_pops_games(cfg):
    out = pick_list_html.render_html([_order()], [], [])
    assert "Pop&#x27;s Games" in out
    assert "verify it is ebay.com/usr/popsgames" in out


def test_unnamed_junk_store_gets_no_letterhead_and_no_main_store_url(cfg):
    cfg.setenv("EBAYBIZ_STORE", "junk")
    out = pick_list_html.render_html([_order()], [], [])
    body = out[out.index("<body>"):]
    assert "popsgames" not in body.lower()
    assert "Pop&#x27;s" not in body and "POP&#x27;S" not in body
    assert 'class="brand"' not in body
    assert "the &#x27;junk&#x27; store" in body or "the 'junk' store" in body


def test_named_store_prints_its_own_identity_only(cfg):
    cfg.setenv("EBAYBIZ_STORE", "named")
    out = pick_list_html.render_html([_order()], [], [])
    body = out[out.index("<body>"):]
    assert "Bargain Bin" in body and "verify it is ebay.com/usr/bargainbin" in body
    assert "popsgames" not in body.lower()
    assert "BUY · SELL · TRADE" not in body   # tagline is identity: not inherited
