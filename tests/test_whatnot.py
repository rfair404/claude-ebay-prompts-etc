#!/usr/bin/env python3
"""lib/whatnot_client.py, lib/whatnot_list.py, lib/publish.py — the Whatnot
channel and the side-by-side `ebz publish`, tested offline.

All GraphQL is faked by monkeypatching `whatnot_list.graphql` (same house
pattern as `list_edit.api_send` in tests/test_list_edit.py). No network, no
credentials, and the Whatnot ledger is redirected per test via
EBAYBIZ_WHATNOT_LEDGER.

Run:  python tests/test_whatnot.py
  or: pytest tests/test_whatnot.py
"""
import base64
import contextlib
import csv
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import publish as P                                            # noqa: E402
import whatnot_client as C                                     # noqa: E402
import whatnot_list as W                                       # noqa: E402
from config import ConfigError                                 # noqa: E402
from draft_io import parse_draft                               # noqa: E402

CREDS = C.WhatnotCredentials(environment="stage", access_token="wn_access_tk_test_x")
SETTINGS = {"attributes": {"condition": "ATTR-COND", "brand": "ATTR-BRAND"}}


@contextlib.contextmanager
def _patched(module, **attrs):
    sentinel = object()
    saved = {k: getattr(module, k, sentinel) for k in attrs}
    for k, v in attrs.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is sentinel:
                delattr(module, k)
            else:
                setattr(module, k, v)


@contextlib.contextmanager
def _env(**kv):
    prev = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _world(tmp: Path, responses: dict, settings: dict = SETTINGS):
    """Fake GraphQL ({substring of query: data-or-Exception}, first match wins),
    fixed settings, and a throwaway ledger. Yields (calls, ledger_path)."""
    calls = []

    def gql(query, variables=None, creds=None):
        calls.append((query, variables))
        for pat, resp in responses.items():
            if pat in query:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {}

    ledger = tmp / "whatnot_ledger.csv"
    with _patched(W, graphql=gql, whatnot_setting=lambda k, d=None: settings.get(k, d)), \
         _env(EBAYBIZ_WHATNOT_LEDGER=str(ledger)):
        yield calls, ledger


def _write_draft(tmp: Path, *, meta: str = "", whatnot: str = "  taxonomy_id: 574\n",
                 quantity: int = 1) -> Path:
    shoot = tmp / "widget-shoot"
    (shoot / "listing").mkdir(parents=True, exist_ok=True)
    (shoot / "listing" / "a.jpg").write_bytes(b"\xff\xd8\xff")
    path = shoot / "draft.md"
    path.write_text(
        "---\n"
        'title: "Vintage Widget MPN-100"\n'
        "price: 24.99\n"
        f"quantity: {quantity}\n"
        'condition: "USED_GOOD"\n'
        'condition_description: "Light shelf wear."\n'
        'category_id: "12345"\n'
        "item_specifics:\n"
        '  brand: "Acme"\n'
        "photos:\n"
        '  - "listing/a.jpg"\n'
        "shipping:\n"
        "  weight: { major_lb: 1, minor_oz: 4 }\n"
        "  package_in: { l: 10, w: 8, d: 4 }\n"
        "best_offer:\n"
        "  enabled: true\n"
        + ("whatnot:\n" + whatnot if whatnot else "")
        + "meta:\n" + meta +
        "---\n"
        "## About\n\n- **Solid** brass\n- See [eBay](https://www.ebay.com/itm/1)\n",
        encoding="utf-8")
    return path


def _created(listing_id="L-1", url="https://whatnot.com/l/1"):
    return {"productCreate": {"product": {"id": "P-1", "variants": {"edges": [{"node": {
        "id": "V-1", "sku": "x", "listings": {"edges": [{"node": {
            "id": listing_id, "url": url, "status": "INACTIVE", "published": False}}]}}}]}},
        "userErrors": []}}


def _rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------

def test_product_input_golden_shape():
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {}):
        d = parse_draft(_write_draft(Path(td)))
        inp = W.build_product_input(d, ["https://i.ebayimg.com/a.jpg"])

    assert inp["title"] == "Vintage Widget MPN-100"
    assert inp["productCategory"] == {
        "taxonomyId": base64.b64encode(b"ProductTaxonomyNode:574").decode(),
        "externalCategoryID": "12345", "externalCategorySource": "ebay"}
    v = inp["variants"][0]
    assert v["price"] == {"amount": 2499, "currencyCode": "USD"}
    assert v["mediaSources"] == ["https://i.ebayimg.com/a.jpg"]
    assert len(v["sku"]) == 8, "reuses the eBay canonical sku"
    assert v["listings"] == [{"buyItNow": {"price": {"amount": 2499, "currencyCode": "USD"},
                                           "offerable": True},
                              "inventoryLevel": {"quantity": 1}, "published": False}]
    assert inp["weight"] == 20.0 and inp["weightUnit"] == "OUNCE"
    assert inp["autoCreateShippingProfile"] is True
    assert inp["dimensions"] == {"length": 10.0, "width": 8.0, "height": 4.0, "unit": "INCH"}
    assert {"id": "ATTR-COND", "value": "Used - Good"} in inp["attributes"]
    assert {"id": "ATTR-BRAND", "value": "Acme"} in inp["attributes"]


def test_description_is_plain_text_and_leads_with_condition():
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {}):
        desc = W._plain_description(parse_draft(_write_draft(Path(td))))
    assert desc.startswith("Condition: Light shelf wear.")
    assert "##" not in desc and "**" not in desc
    assert "• Solid brass" in desc
    assert "ebay.com" not in desc, "eBay links must not be carried onto Whatnot"


def test_configured_shipping_profile_replaces_weight():
    settings = {**SETTINGS, "shipping_profile_id": "SP-9"}
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {}, settings):
        inp = W.build_product_input(parse_draft(_write_draft(Path(td))), [])
    assert inp["shippingProfileId"] == "SP-9"
    assert "weight" not in inp and "autoCreateShippingProfile" not in inp


def test_auction_format():
    wn = "  taxonomy_id: 574\n  format: auction\n  starting_price: 5\n"
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {}):
        d = parse_draft(_write_draft(Path(td), whatnot=wn))
        inp = W.build_product_input(d, [])
    listing = inp["variants"][0]["listings"][0]
    assert listing["auction"] == {"startingPrice": {"amount": 500, "currencyCode": "USD"}}
    assert "buyItNow" not in listing and "price" not in inp["variants"][0]


def test_validate_requires_a_whatnot_category():
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {}):
        issues = W.validate_draft_for_whatnot(_write_draft(Path(td), whatnot=""))
    assert any("whatnot.taxonomy_id" in i for i in issues)


# ---------------------------------------------------------------------------
# Sync / publish / end
# ---------------------------------------------------------------------------

def test_sync_creates_unpublished_and_writes_meta_and_ledger():
    meta = '  whatnot_media_urls: "https://cdn/a.jpg"\n'   # cached: no EPS upload
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {"productCreate": _created()}) \
            as (calls, ledger):
        path = _write_draft(Path(td), meta=meta)
        r = W.sync_to_whatnot(path, creds=CREDS)
        d = parse_draft(path)
        rows = _rows(ledger)

    assert (r.operation, r.product_id, r.listing_id) == ("created", "P-1", "L-1")
    (query, variables), = calls
    assert "productCreate" in query and "listingPublish" not in query
    assert variables["media"] == [{"source": "https://cdn/a.jpg", "mediaContentType": "IMAGE",
                                   "alt": "Vintage Widget MPN-100"}]
    assert variables["input"]["variants"][0]["listings"][0]["published"] is False
    assert d.get("meta.whatnot_product_id") == "P-1"
    assert d.get("meta.whatnot_listing_id") == "L-1"
    assert d.get("meta.whatnot_environment") == "stage"
    assert [(x["status"], x["listing_id"]) for x in rows] == [("SYNCED", "L-1")]


def test_resync_updates_without_resending_media():
    meta = ('  whatnot_product_id: "P-1"\n  whatnot_variant_id: "V-1"\n'
            '  whatnot_listing_id: "L-1"\n  whatnot_environment: "stage"\n')
    upd = {"productUpdate": _created()["productCreate"]}
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {"productUpdate": upd}) as (calls, _):
        r = W.sync_to_whatnot(_write_draft(Path(td), meta=meta), creds=CREDS)

    (query, variables), = calls
    assert r.operation == "updated" and "productUpdate" in query
    assert "media" not in variables
    inp = variables["input"]
    assert inp["id"] == "P-1"
    v = inp["variants"][0]
    assert v["id"] == "V-1" and "mediaSources" not in v
    assert v["listings"][0]["id"] == "L-1" and "published" not in v["listings"][0], \
        "a re-sync must never flip a live listing back to unpublished"


def test_sync_refuses_ids_from_the_other_environment():
    meta = ('  whatnot_product_id: "P-1"\n  whatnot_listing_id: "L-1"\n'
            '  whatnot_environment: "production"\n')
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {}) as (calls, _):
        try:
            W.sync_to_whatnot(_write_draft(Path(td), meta=meta), creds=CREDS)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "production" in str(e)
    assert calls == []


def test_user_errors_raise():
    bad = {"productCreate": {"product": None,
                             "userErrors": [{"field": ["title"], "message": "too long"}]}}
    meta = '  whatnot_media_urls: "https://cdn/a.jpg"\n'
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {"productCreate": bad}):
        try:
            W.sync_to_whatnot(_write_draft(Path(td), meta=meta), creds=CREDS)
            raise AssertionError("expected WhatnotAPIError")
        except C.WhatnotAPIError as e:
            assert "title: too long" in str(e)


_SYNCED = '  whatnot_listing_id: "L-1"\n  whatnot_environment: "stage"\n'
_INACTIVE = {"listing": {"id": "L-1", "status": "INACTIVE", "published": False,
                         "url": "https://whatnot.com/l/1"}}


def test_publish_dry_run_never_calls_listing_publish():
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {"listing(": _INACTIVE}) as (calls, _):
        r = W.publish_to_whatnot(_write_draft(Path(td), meta=_SYNCED), creds=CREDS)
    assert r.dry_run is True and r.price == "24.99"
    assert not [q for q, _ in calls if "listingPublish" in q]


def test_publish_confirm_publishes_and_records():
    resp = {"listingPublish": {"listing": {"id": "L-1", "url": "https://whatnot.com/l/1"},
                               "userErrors": []},
            "listing(": _INACTIVE}
    with tempfile.TemporaryDirectory() as td, _world(Path(td), resp) as (calls, ledger):
        path = _write_draft(Path(td), meta=_SYNCED)
        r = W.publish_to_whatnot(path, creds=CREDS, confirm=True)
        d, rows = parse_draft(path), _rows(ledger)
    assert r.dry_run is False and r.listing_url == "https://whatnot.com/l/1"
    pub = [v for q, v in calls if "listingPublish" in q]
    assert pub == [{"input": {"id": "L-1"}}]
    assert d.get("meta.whatnot_published_at")
    assert rows[0]["status"] == "PUBLISHED"


def test_publish_refuses_qty1_item_live_on_ebay_without_crosslist():
    meta = _SYNCED + '  ebay_listing_id: "999"\n'
    with tempfile.TemporaryDirectory() as td, _world(Path(td), {"listing(": _INACTIVE}) as (calls, _):
        path = _write_draft(Path(td), meta=meta)
        try:
            W.publish_to_whatnot(path, creds=CREDS, confirm=True)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "--crosslist" in str(e)
        assert not [q for q, _ in calls if "listingPublish" in q]


def test_publish_refuses_a_sold_listing():
    sold = {"listing(": {"listing": {"id": "L-1", "status": "SOLD", "published": True}}}
    with tempfile.TemporaryDirectory() as td, _world(Path(td), sold):
        try:
            W.publish_to_whatnot(_write_draft(Path(td), meta=_SYNCED), creds=CREDS, confirm=True)
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "SOLD" in str(e)


def test_end_unpublishes_and_clears_live_marker():
    meta = _SYNCED + '  whatnot_published_at: "2026-10-01T00:00:00Z"\n'
    resp = {"listingUnpublish": {"userErrors": []}}
    with tempfile.TemporaryDirectory() as td, _world(Path(td), resp) as (calls, ledger):
        path = _write_draft(Path(td), meta=meta)
        dry = W.end_on_whatnot(path, creds=CREDS)
        assert dry.dry_run and calls == []
        W.end_on_whatnot(path, creds=CREDS, confirm=True)
        d, rows = parse_draft(path), _rows(ledger)
    assert not W.is_live_on_whatnot(d)
    assert rows[0]["status"] == "ENDED"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_credentials_refuse_a_token_for_the_wrong_environment():
    with _patched(C, load_config=lambda: {}), \
         _env(WHATNOT_ACCESS_TOKEN="wn_access_tk_test_abc", WHATNOT_ENVIRONMENT="production"):
        try:
            C.load_credentials()
            raise AssertionError("expected ConfigError")
        except ConfigError as e:
            assert "staging" in str(e)


def test_credentials_missing_token_explains_setup():
    with _patched(C, load_config=lambda: {}), \
         _env(WHATNOT_ACCESS_TOKEN=None, WHATNOT_ENVIRONMENT=None):
        try:
            C.load_credentials()
            raise AssertionError("expected ConfigError")
        except ConfigError as e:
            assert "access_token" in str(e)


def test_taxonomy_id_encoding():
    assert C.taxonomy_global_id(574) == base64.b64encode(b"ProductTaxonomyNode:574").decode()
    assert C.taxonomy_global_id("UHJvZHVjdA==") == "UHJvZHVjdA=="


# ---------------------------------------------------------------------------
# Side-by-side dispatcher
# ---------------------------------------------------------------------------

def test_parse_channels():
    assert P.parse_channels("both") == ["ebay", "whatnot"]
    assert P.parse_channels("whatnot,ebay") == ["ebay", "whatnot"]
    assert P.parse_channels("whatnot") == ["whatnot"]
    try:
        P.parse_channels("etsy")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_crosslist_blockers():
    with tempfile.TemporaryDirectory() as td:
        fresh = _write_draft(Path(td))
        assert P.crosslist_blockers(fresh, ["ebay", "whatnot"], False)
        assert P.crosslist_blockers(fresh, ["ebay", "whatnot"], True) == []
        assert P.crosslist_blockers(fresh, ["whatnot"], False) == []
    with tempfile.TemporaryDirectory() as td:
        live_ebay = _write_draft(Path(td), meta='  ebay_listing_id: "999"\n')
        assert P.crosslist_blockers(live_ebay, ["whatnot"], False)
        assert P.crosslist_blockers(live_ebay, ["ebay"], False) == []
    with tempfile.TemporaryDirectory() as td:
        multi = _write_draft(Path(td), quantity=3)
        assert P.crosslist_blockers(multi, ["ebay", "whatnot"], False) == []


def test_publish_runs_each_channel_and_isolates_failures():
    seen = []

    def ebay(path, store, confirm):
        seen.append(("ebay", confirm))
        raise RuntimeError("eBay down")

    def whatnot(path, confirm):
        seen.append(("whatnot", confirm))
        return "DRY RUN"

    with tempfile.TemporaryDirectory() as td, \
         _patched(P, _publish_ebay=ebay, _publish_whatnot=whatnot):
        res = P.publish(_write_draft(Path(td)).parent, ["ebay", "whatnot"], crosslist=True)
    assert seen == [("ebay", False), ("whatnot", False)]
    assert res["ebay"][0] is False and "eBay down" in res["ebay"][1]
    assert res["whatnot"] == (True, "DRY RUN")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
