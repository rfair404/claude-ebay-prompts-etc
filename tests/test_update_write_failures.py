#!/usr/bin/env python3
"""A failed eBay write ends `list_edit.py` with a non-zero exit, and `--update`
stops before writing to a listing eBay has on hold.

Written after 2026-10-25: `--update <folder> --fields description --store junk`
on junk 880048096663 (SKU 6cd62b26) got `PUT .../inventory_item/6cd62b26 ->
HTTP 500, errorId 25001` twice. The listing was ON_HOLD_FIXABLE, which only
Trading GetItem showed, and the Inventory API refuses revisions to a held
listing. The run was read through `| tail`, so its exit code was tail's 0.

Only urllib.request.urlopen is faked. Everything above it (config, OAuth token,
api_send retries, the CLI) is the real code.

Run:  pytest tests/test_update_write_failures.py
"""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import config                                                 # noqa: E402
import ebay_client                                            # noqa: E402
import list_edit as L                                         # noqa: E402

SKU, OFFER, LISTING = "6cd62b26", "9900112233", "880048096663"

# GetItem for junk 880048096663 on 2026-10-25, cut to the parts that matter.
HELD_GETITEM = f"""<?xml version="1.0" encoding="UTF-8"?>
<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
<Ack>Failure</Ack>
<Errors><ShortMessage>This listing violates our Offering to Buy or Sell Outside of eBay Policy. It&apos;s hidden until you can fix it.</ShortMessage><LongMessage>This listing violates our Offering to Buy or Sell Outside of eBay Policy. It&apos;s hidden until you can fix it.</LongMessage><ErrorCode>21920397</ErrorCode><SeverityCode>Error</SeverityCode><ErrorParameters ParamID="0"><Value>http://www.ebay.com/help/policies/payment-policies/offering-buy-sell-outside-ebay-policy?id=4272</Value></ErrorParameters><ErrorParameters ParamID="3"><Value>Offering to Buy or Sell Outside of eBay Policy</Value></ErrorParameters><ErrorParameters ParamID="4"><Value>ON_HOLD_FIXABLE</Value></ErrorParameters><ErrorClassification>RequestError</ErrorClassification></Errors>
<Item><ItemID>{LISTING}</ItemID><SellingStatus><BidCount>0</BidCount></SellingStatus></Item>
</GetItemResponse>"""

CLEAN_GETITEM = f"""<?xml version="1.0" encoding="UTF-8"?>
<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
<Ack>Success</Ack>
<Item><ItemID>{LISTING}</ItemID><SellingStatus><ListingStatus>Active</ListingStatus></SellingStatus></Item>
</GetItemResponse>"""

SYSTEM_ERROR = json.dumps({"errors": [{"errorId": 25001, "domain": "API_INVENTORY",
                                       "category": "APPLICATION",
                                       "message": "A system error has occurred."}]})


class _Resp:
    def __init__(self, body):
        self._raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeEbay:
    """Stands in for urlopen: routes each request by method + URL."""

    def __init__(self, getitem=CLEAN_GETITEM, offer_listing=None, put_item=None,
                 publish=None, offer_status="PUBLISHED"):
        self.getitem = getitem
        self.offer_listing = {"listingId": LISTING, "listingStatus": "ACTIVE",
                              **(offer_listing or {})}
        self.put_item = put_item          # None = 204, else (code, body)
        self.publish = publish
        self.offer_status = offer_status
        self.requests = []                # (method, url)

    def writes(self):
        """Every request that changes something on eBay (GetItem is a read)."""
        return [(m, u) for m, u in self.requests
                if m in ("PUT", "POST", "DELETE")
                and "oauth2/token" not in u and "ws/api.dll" not in u]

    def __call__(self, req, timeout=None):
        m, url = req.get_method(), req.full_url
        self.requests.append((m, url))
        if "oauth2/token" in url:
            return _Resp({"access_token": "tok", "expires_in": 7200})
        if "ws/api.dll" in url:
            return _Resp(self.getitem)
        if "/offer?sku=" in url:
            return _Resp({"offers": [{"offerId": OFFER, "sku": SKU,
                                      "status": self.offer_status,
                                      "availableQuantity": 1,
                                      "listing": self.offer_listing}]})
        if url.endswith(f"/inventory_item/{SKU}") and m == "GET":
            return _Resp({"availability": {"shipToLocationAvailability": {"quantity": 1}},
                          "condition": "USED_EXCELLENT",
                          "product": {"title": "2005 Playboy full year",
                                      "description": "old", "imageUrls": ["https://i/1.jpg"]}})
        if url.endswith(f"/inventory_item/{SKU}") and m == "PUT":
            if self.put_item:
                code, body = self.put_item
                raise urllib.error.HTTPError(url, code, f"HTTP {code}", None,
                                             io.BytesIO(body.encode()))
            return _Resp("")
        if url.endswith(f"/offer/{OFFER}") and m == "GET":
            return _Resp({"offerId": OFFER, "sku": SKU, "status": self.offer_status,
                          "pricingSummary": {"price": {"value": "39.99", "currency": "USD"}},
                          "listingDescription": "old"})
        if url.endswith(f"/offer/{OFFER}") and m == "PUT":
            return _Resp("")
        if url.endswith(f"/offer/{OFFER}/publish"):
            return _Resp(self.publish if self.publish is not None else {"listingId": LISTING})
        raise AssertionError(f"unexpected eBay request: {m} {url}")


@pytest.fixture
def ebay(tmp_path, monkeypatch):
    """A real config file for a junk store, and urlopen pointed at FakeEbay."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "ebay:\n"
        "  environment: sandbox\n"
        "  stores:\n"
        "    junk:\n"
        "      environment: production\n"
        "      app_id: app-x\n"
        "      cert_id: cert-x\n"
        "      dev_id: dev-x\n"
        "      user_refresh_token: refresh-x\n", encoding="utf-8")
    monkeypatch.setenv("EBAYBIZ_CONFIG", str(cfg))
    monkeypatch.setattr(config, "_cached_config", None)
    for cache in (ebay_client._app_caches, ebay_client._user_caches,
                  ebay_client._user_scopes_by_store):
        cache.clear()
    # Other suites assign fakes straight onto list_edit; put the real seams back.
    for name in ("api_send", "get_listing_hold", "load_credentials"):
        monkeypatch.setattr(L, name, getattr(ebay_client, name))
    monkeypatch.setattr(ebay_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)

    def install(**kw):
        fake = FakeEbay(**kw)
        monkeypatch.setattr(ebay_client.urllib.request, "urlopen", fake)
        return fake
    yield install
    for cache in (ebay_client._app_caches, ebay_client._user_caches,
                  ebay_client._user_scopes_by_store):
        cache.clear()


def _draft(tmp_path, offer_id=""):
    d = tmp_path / "03-playboy-2005-full-year"
    d.mkdir()
    (d / "draft.md").write_text(
        "---\n"
        "title: \"2005 Playboy Magazine Full Year\"\n"
        "store: junk\n"
        "meta:\n"
        f"  ebay_inventory_sku: \"{SKU}\"\n"
        + (f"  ebay_offer_id: \"{offer_id}\"\n" if offer_id else "")
        + "---\n\n## Markings\n\nEach spine is printed with its month and year.\n",
        encoding="utf-8")
    (d / "price.txt").write_text("OPERATOR PRICE: test fixture (2026-10-25)\n", encoding="utf-8")
    return d


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["list_edit.py", *argv])
    try:
        L._cli()
    except SystemExit as e:
        return e.code or 0
    return 0


# ---------------------------------------------------------------------------
# A failed write exits non-zero
# ---------------------------------------------------------------------------

def test_update_exits_nonzero_when_the_inventory_item_put_fails(ebay, tmp_path, monkeypatch, capsys):
    fake = ebay(put_item=(500, SYSTEM_ERROR))
    d = _draft(tmp_path)

    code = _run(monkeypatch, "--update", str(d), "--fields", "description", "--store", "junk")

    err = capsys.readouterr().err
    assert code != 0, "a 500 on the inventory_item PUT must not exit 0"
    assert "EbayAPIError" in err and "25001" in err
    assert "last_synced" not in (d / "draft.md").read_text(encoding="utf-8"), \
        "a failed update must not stamp the draft as synced"
    assert [w for w in fake.writes() if "/offer/" in w[1]] == [], \
        "the offer half must not run after the item PUT failed"


def test_update_exits_zero_when_every_write_lands(ebay, tmp_path, monkeypatch, capsys):
    fake = ebay()
    d = _draft(tmp_path)

    code = _run(monkeypatch, "--update", str(d), "--fields", "description", "--store", "junk")

    assert code == 0, capsys.readouterr()
    assert ("PUT", f"https://api.ebay.com/sell/inventory/v1/inventory_item/{SKU}") in fake.writes()
    assert "[OK] updated description" in capsys.readouterr().out


def test_publish_without_a_listing_id_exits_nonzero(ebay, tmp_path, monkeypatch, capsys):
    ebay(publish={}, offer_status="UNPUBLISHED")
    d = _draft(tmp_path, offer_id=OFFER)

    code = _run(monkeypatch, "--publish", str(d), "--confirm", "--store", "junk")

    out = capsys.readouterr()
    assert code != 0, "a publish that returned no listing id is not a success"
    assert "[LIVE]" not in out.out
    assert "no listingId" in out.err


# ---------------------------------------------------------------------------
# An on-hold listing stops before any write
# ---------------------------------------------------------------------------

def test_update_stops_before_any_write_when_getitem_shows_a_hold(ebay, tmp_path, monkeypatch, capsys):
    fake = ebay(getitem=HELD_GETITEM)
    d = _draft(tmp_path)

    code = _run(monkeypatch, "--update", str(d), "--fields", "description", "--store", "junk")

    err = capsys.readouterr().err
    assert code not in (0, None)
    assert fake.writes() == [], f"a held listing must get no writes: {fake.writes()}"
    assert "ON_HOLD_FIXABLE" in err
    assert f"https://www.ebay.com/sl/list?mode=ReviseItem&itemId={LISTING}" in err
    assert "last_synced" not in (d / "draft.md").read_text(encoding="utf-8")


def test_update_stops_when_the_offer_reports_listing_on_hold(ebay, tmp_path, monkeypatch, capsys):
    fake = ebay(offer_listing={"listingOnHold": True})
    d = _draft(tmp_path)

    code = _run(monkeypatch, "--update", str(d), "--fields", "description", "--store", "junk")

    assert code not in (0, None)
    assert fake.writes() == []
    assert "ReviseItem" in capsys.readouterr().err


def test_dry_run_on_a_held_listing_warns_and_sends_nothing(ebay, tmp_path, monkeypatch, capsys):
    fake = ebay(getitem=HELD_GETITEM)
    d = _draft(tmp_path)

    code = _run(monkeypatch, "--update", str(d), "--fields", "description",
                "--store", "junk", "--dry-run")

    out = capsys.readouterr()
    assert code == 0
    assert fake.writes() == []
    assert "[HOLD]" in out.err and "ReviseItem" in out.err
    assert "[DRY RUN] nothing sent" in out.out


def test_update_fails_closed_when_getitem_cannot_answer(ebay, tmp_path, monkeypatch, capsys):
    fake = ebay(getitem="<GetItemResponse><Ack>Failure</Ack><Errors><ErrorCode>10007</ErrorCode>"
                        "<LongMessage>Internal error</LongMessage></Errors></GetItemResponse>")
    d = _draft(tmp_path)

    code = _run(monkeypatch, "--update", str(d), "--fields", "description", "--store", "junk")

    assert code != 0
    assert fake.writes() == []
    assert "GetItem" in capsys.readouterr().err
