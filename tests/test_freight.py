#!/usr/bin/env python3
"""lib/freight — LTL quote/book/track, tested offline.

Booking freight spends real money, so as with tests/test_easypost_client.py
the first job here is proving the gate: no provider's book endpoint is ever
called without confirm=True, a confirmed book is never retried on a network
error, and the web app's book route needs an explicit confirm too.

All HTTP is faked (urlopen patched); no network, no keys.
"""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from freight import http as FH                                          # noqa: E402
from freight import service as svc                                      # noqa: E402
from freight.models import (BookResult, FreightAddress, FreightQuote,   # noqa: E402
                            FreightRequest, HandlingUnit,
                            estimate_freight_class)
from freight.providers import Provider, ProviderNotReady                # noqa: E402
from freight.providers.shipengine import ShipEngine, _option_code      # noqa: E402
from freight.providers.warp import Warp                                 # noqa: E402
from freight.store import FreightStore                                  # noqa: E402


def _req(**kw) -> FreightRequest:
    d = dict(origin=FreightAddress(zip="60120", name="Me", street1="9 Ship St",
                                   city="Elgin", state="IL", phone="8475550100"),
             destination=FreightAddress(zip="30301", name="Jane", street1="1 Main St",
                                        city="Atlanta", state="GA", phone="4045550100"),
             pickup_date="2026-10-14",
             units=[HandlingUnit(length_in=48, width_in=40, height_in=50, weight_lbs=320)],
             accessorials=["liftgate_delivery", "residential_delivery"],
             commodity="antique sewing machine")
    d.update(kw)
    return FreightRequest(**d)


# --------------------------------------------------------------------- fakes

class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Fake:
    """urlopen stand-in: routes by (method, path suffix) and records calls."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), url, body, dict(req.header_items())))
        for (m, frag), resp in self.routes.items():
            if m == req.get_method() and frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return _Resp(resp)
        raise AssertionError(f"unexpected call {req.get_method()} {url}")


@pytest.fixture
def fake(monkeypatch):
    def install(routes):
        f = _Fake(routes)
        monkeypatch.setattr(urllib.request, "urlopen", f)
        monkeypatch.setattr(FH.time, "sleep", lambda s: None)
        return f
    return install


DEV_QUOTE = {"quote_id": "W01Q", "price": {"amount": 294.1853005, "currency_code": "USD"},
             "charges": [{"code": "TOTAL", "description": "Total rate", "amount": 294.1853005},
                         {"code": "SERVICE_OPTION", "description": "liftgate-pickup", "amount": 0},
                         {"code": "TRANSIT", "description": "Transit Cost", "amount": 244.66}],
             "transit_time": 3, "expiration_time_utc": 1792110146, "status": "ACCEPT",
             "insurance": {"insuranceQuoteId": "INS1", "total": 66}}
PUBLIC_QUOTE = {"quote_id": "PRICING_1", "price_usd": 249.07, "transit_days": 4,
                "quote_tier": "firm",
                "service": {"included_at_no_charge": ["liftgate", "residential delivery"]}}
WARP_MARKET = {"market_options": [
    {"rank": 1, "price_usd": 446.0, "transit_days": 2, "carrier_name": "DAYLIGHT",
     "is_warp": False, "bookable": False},
    {"rank": 2, "price_usd": 250.0, "carrier_name": "Warp", "is_warp": True}]}
SANDBOX = {"sandbox_key": "sbx", "market_options": True}


# --------------------------------------------------------------------- models

def test_validate_reports_every_problem_at_once():
    r = _req(pickup_date="tomorrow", accessorials=["teleport"],
             destination=FreightAddress(zip="3030"))
    with pytest.raises(ValueError) as e:
        r.validate()
    msg = str(e.value)
    assert "3030" in msg and "pickup_date" in msg and "teleport" in msg


def test_density_class_estimate_uses_2025_scale():
    assert estimate_freight_class(0.5) == "400"
    assert estimate_freight_class(9.9) == "100"
    assert estimate_freight_class(22.5) == "65"     # boundary goes to the denser tier
    assert estimate_freight_class(60) == "50"
    u = HandlingUnit(length_in=48, width_in=40, height_in=50, weight_lbs=320)
    assert round(u.density_pcf, 2) == 5.76 and u.effective_class() == "175"
    u.freight_class = "85"
    assert u.effective_class() == "85"


def test_request_round_trips_through_dict():
    r = _req()
    assert FreightRequest.from_dict(json.loads(json.dumps(r.to_dict()))) == r


# --------------------------------------------------------------------- warp

def test_warp_with_sandbox_key_quotes_on_staging_with_apikey_header(fake):
    f = fake({("POST", "stg.wearewarp.com/api/v1/freights/quote"): DEV_QUOTE,
              ("POST", "/ltl/market-options"): WARP_MARKET})
    qs = Warp(SANDBOX).quote(_req(declared_value=475))
    method, url, body, headers = f.calls[0]
    assert url == "https://stg.wearewarp.com/api/v1/freights/quote"
    assert headers["Apikey"] == "sbx"
    assert body["deliveryServices"] == [{"quantity": 1, "service": "liftgate-delivery"},
                                        {"quantity": 1, "service": "residential-delivery"}]
    assert body["listItems"][0]["totalWeight"] == 320 and body["listItems"][0]["packaging"] == "Pallet"
    assert body["insuranceInfo"]["insuredValue"] == 475
    q = qs[0]
    assert (q.carrier, q.price, q.bookable, q.quote_id, q.extra["mode"]) == \
        ("Warp", 294.19, True, "W01Q", "sandbox")
    assert q.expires_at.startswith("2026-10-1") and q.transit_days == 3
    assert [c["code"] for c in q.extra["charges"]] == ["SERVICE_OPTION", "TRANSIT"]
    assert "Authorization" not in f.calls[1][3] and "Apikey" not in f.calls[1][3]  # spread keyless
    assert [(x.carrier, x.bookable) for x in qs[1:]] == [("DAYLIGHT", False)]


def test_warp_live_mode_uses_gw_and_live_key(fake):
    f = fake({("POST", "gw.wearewarp.com/api/v1/freights/quote"): DEV_QUOTE})
    Warp({"mode": "live", "live_key": "lv", "sandbox_key": "sbx",
          "market_options": False}).quote(_req())
    assert f.calls[0][1].startswith("https://gw.wearewarp.com") and f.calls[0][3]["Apikey"] == "lv"


def test_warp_without_key_quotes_publicly_and_is_not_bookable(fake):
    f = fake({("POST", "www.wearewarp.com/api/v1/ltl/quote"): PUBLIC_QUOTE,
              ("POST", "/ltl/market-options"): WARP_MARKET})
    qs = Warp({}).quote(_req())
    assert f.calls[0][2]["accessorials"]["delivery"] == ["liftgate-delivery", "residential-delivery"]
    assert qs[0].price == 249.07 and not qs[0].bookable
    with pytest.raises(ProviderNotReady, match="can't be booked"):
        Warp({}).book(qs[0], _req())


def test_warp_market_failure_keeps_the_firm_quote(fake):
    fake({("POST", "/freights/quote"): DEV_QUOTE,
          ("POST", "/ltl/market-options"): urllib.error.HTTPError(
              "u", 400, "x", None, io.BytesIO(b"bad"))})
    qs = Warp(SANDBOX).quote(_req())
    assert len(qs) == 1 and any("market spread unavailable" in w for w in qs[0].warnings)


def _dev_q(mode="sandbox"):
    return FreightQuote(provider="warp", carrier="Warp", price=294.19, quote_id="W01Q",
                        bookable=True, tier="firm",
                        extra={"mode": mode, "insurance": {"insuranceQuoteId": "INS1", "total": 66}})


def test_warp_book_dry_run_makes_no_call(fake):
    f = fake({})
    r = Warp(SANDBOX).book(_dev_q(), _req(), confirm=False)
    assert r.dry_run and f.calls == [] and "SANDBOX" in r.warnings[0]


def test_warp_sandbox_quote_never_books_live(fake):
    f = fake({})
    with pytest.raises(ProviderNotReady, match="came from sandbox"):
        Warp({"mode": "live", "live_key": "lv"}).book(_dev_q("sandbox"), _req(), confirm=True)
    assert f.calls == []


def test_warp_book_needs_full_addresses(fake):
    fake({})
    with pytest.raises(ValueError, match="destination.street1"):
        Warp(SANDBOX).book(_dev_q(), _req(destination=FreightAddress(zip="30301")))


def test_warp_confirmed_book_posts_once_and_never_retries(fake):
    f = fake({("POST", "/freights/booking"): urllib.error.URLError("reset")})
    with pytest.raises(FH.FreightAPIError):
        Warp(SANDBOX).book(_dev_q(), _req(), confirm=True)
    assert len([c for c in f.calls if c[1].endswith("/freights/booking")]) == 1


def test_warp_confirmed_book_sends_stops_window_insurance_and_flags_unpaid(fake):
    f = fake({("POST", "stg.wearewarp.com/api/v1/freights/booking"): {
        "message": "Success", "trackingNumber": "2E2E", "orderId": "O1", "shipmentId": "S1",
        "insuranceStatus": "failed", "paymentLink": "https://customer.wearewarp.com/pay"}})
    r = Warp(SANDBOX).book(_dev_q(), _req(reference="12-345", declared_value=475), confirm=True)
    body = f.calls[0][2]
    assert body["quoteId"] == "W01Q" and body["refNum"] == "12-345"
    assert body["insuranceQuoteId"] == "INS1"
    assert body["deliveryInfo"]["address"]["zipcode"] == "30301"
    assert body["deliveryInfo"]["contactName"] == "Jane"
    assert body["pickupInfo"]["windowTime"]["from"].startswith("2026-10-1")
    assert body["listItems"][0]["totalWeight"] == 320
    assert (r.booking_id, r.tracking_number, r.dry_run) == ("O1", "2E2E", False)
    assert r.payment_link.endswith("/pay") and r.raw["mode"] == "sandbox"
    assert any("FAILED" in w for w in r.warnings)


def test_warp_track_and_documents_use_the_booking_environment(fake):
    f = fake({("POST", "stg.wearewarp.com/api/v1/freights/tracking"): [
                  {"trackingNumber": "2E2E", "statusInfo": {"status": "complete"}}],
              ("GET", "/freights/events/S1"): [{"when": "t", "message": "Delivered"}],
              ("GET", "/freights/documents/O1"): {"data": [
                  {"type": "bol", "url": "https://x/bol.pdf"}, {"type": "pod"}]}})
    booking = {"tracking_number": "2E2E", "booking_id": "O1",
               "raw": {"mode": "sandbox", "shipmentId": "S1"}}
    w = Warp({"mode": "live", "live_key": "lv", "sandbox_key": "sbx"})
    t = w.track(booking)
    assert t.delivered and t.events[0].status == "Delivered"
    assert f.calls[0][2] == {"trackingNumbers": ["2E2E"]} and f.calls[0][3]["Apikey"] == "sbx"
    assert w.documents(booking) == [{"type": "bol", "podType": None, "url": "https://x/bol.pdf"}]


# --------------------------------------------------------------------- shipengine

CARRIER = {"carrier_id": "c1", "name": "Test LTL", "scac": "TEST",
           "features": ["quote", "scheduled_pickup", "tracking"],
           "options": [{"code": "lftp", "name": "Lift gate required at pickup",
                        "features": ["quote"]},
                       {"code": "lftd", "name": "Lift gate required at delivery",
                        "features": ["quote"]}],
           "packages": [{"code": "skd", "name": "Skid"}, {"code": "pal", "name": "Pail"}]}


def test_shipengine_matches_accessorials_by_name_not_guessed_code():
    assert _option_code(CARRIER, "liftgate_delivery") == "lftd"
    assert _option_code(CARRIER, "liftgate_pickup") == "lftp"
    assert _option_code(CARRIER, "residential_delivery") is None


def test_shipengine_quote_warns_on_unmapped_accessorial(fake):
    f = fake({("GET", "/ltl/carriers"): {"carriers": [CARRIER]},
              ("POST", "/ltl/quotes/c1"): {
                  "quote_id": "q1", "estimated_delivery_days": 3,
                  "service": {"carrier_description": "Standard"},
                  "charges": [{"type": "total",
                               "amount": {"currency": "USD", "value": "306.71"}}]}})
    qs = ShipEngine({"api_key": "k"}).quote(_req())
    body = f.calls[1][2]["shipment"]
    assert body["options"] == [{"code": "lftd"}]
    assert body["packages"][0]["code"] == "skd"            # pallet -> Skid, not Pail
    assert f.calls[1][3]["Api-key"] == "k"
    q = qs[0]
    assert (q.price, q.carrier, q.quote_id, q.extra["scac"]) == (306.71, "Test LTL", "q1", "TEST")
    assert any("Residential delivery not offered" in w for w in q.warnings)


def test_shipengine_without_key_is_not_ready_and_sends_nothing(fake):
    f = fake({})
    assert ShipEngine({}).status()[0] is False
    with pytest.raises(ProviderNotReady):
        ShipEngine({}).quote(_req())
    assert f.calls == []


def test_shipengine_pickup_defaults_to_carrier_test_mode(fake):
    f = fake({("POST", "/quotes/q1/pickup"): {"pickup_id": "p1", "pro_number": "PRO9"}})
    q = FreightQuote(provider="shipengine", carrier="Test LTL", price=1, quote_id="q1",
                     bookable=True)
    r = ShipEngine({"api_key": "k"}).book(q, _req(), confirm=True)
    assert f.calls[0][2]["carrier"]["test"] is True
    assert r.tracking_number == "PRO9" and "TEST" in r.warnings[0]


# --------------------------------------------------------------------- service

class _FakeProvider(Provider):
    def __init__(self, name, quotes=None, error=None):
        super().__init__({})
        self.name, self._quotes, self._error = name, quotes or [], error
        self.booked = []

    def quote(self, req):
        if self._error:
            raise self._error
        return list(self._quotes)

    def book(self, quote, req, confirm=False):
        self.booked.append(confirm)
        return BookResult(dry_run=not confirm, provider=self.name, quote_id=quote.quote_id,
                          price=quote.price, carrier=quote.carrier,
                          tracking_number="PRO1" if confirm else "")


@pytest.fixture
def world(monkeypatch, tmp_path):
    a = _FakeProvider("alpha", [FreightQuote("alpha", "A Lines", 300.0, quote_id="a1",
                                             bookable=True)])
    b = _FakeProvider("beta", [FreightQuote("beta", "B Freight", 250.0, quote_id="b1",
                                            bookable=True)])
    c = _FakeProvider("gamma", error=FH.FreightAPIError("gamma", 500, "down"))
    provs = {"alpha": a, "beta": b, "gamma": c}
    monkeypatch.setattr(svc, "providers",
                        lambda only=None: {k: v for k, v in provs.items()
                                           if not only or k in only})
    return FreightStore(tmp_path / "freight.db"), provs


def test_quote_all_ranks_and_survives_a_failing_provider(world):
    _, _ = world
    quotes, errors = svc.quote_all(_req())
    assert [q.carrier for q in quotes] == ["B Freight", "A Lines"]
    assert "down" in errors["gamma"]


def test_shipment_lifecycle_and_manual_quotes_survive_requote(world):
    fs, provs = world
    sid = fs.create(_req(), store="default", order_id="12-345", title="Pfaff 463",
                    buyer_paid=400)
    s = svc.quote_shipment(fs, sid)
    assert s.status == "quoted" and len(s.quotes) == 2
    fs.add_manual_quote(sid, FreightQuote("uship", "Estes via uShip", 199.0))
    s = svc.quote_shipment(fs, sid)
    assert [q.carrier for q in s.quotes][0] == "Estes via uShip"   # kept, and cheapest
    assert s.quotes[0].tier == "manual" and not s.quotes[0].bookable

    with pytest.raises(ProviderNotReady, match="mark-booked"):
        svc.book(fs, sid, 1, confirm=True)                        # by hand only

    r = svc.book(fs, sid, 2, confirm=False)                        # B Freight, dry run
    assert r.dry_run and fs.require(sid).status == "quoted" and provs["beta"].booked == [False]

    svc.book(fs, sid, 2, confirm=True)
    s = fs.require(sid)
    assert s.status == "booked" and s.booking["tracking_number"] == "PRO1"
    assert s.cost == 250.0 and s.margin == 150.0
    with pytest.raises(ValueError):
        svc.book(fs, sid, 2, confirm=True)                         # never twice
    with pytest.raises(ValueError):
        fs.update_request(sid, _req())                             # frozen once booked

    fs.set_status(sid, "delivered")
    with pytest.raises(ValueError, match="final"):
        fs.set_status(sid, "in_transit")


def test_changing_destination_clears_every_quote(world):
    fs, _ = world
    sid = fs.create(_req())
    svc.quote_shipment(fs, sid)
    fs.add_manual_quote(sid, FreightQuote("uship", "X", 1.0))
    fs.update_request(sid, _req(destination=FreightAddress(zip="10001")))
    s = fs.require(sid)
    assert s.quotes == [] and s.status == "draft"


def test_adding_contact_details_keeps_quotes(world):
    fs, _ = world
    sid = fs.create(_req(destination=FreightAddress(zip="30301")))
    svc.quote_shipment(fs, sid)
    fs.update_request(sid, _req())          # same zip, now with street/name/phone
    s = fs.require(sid)
    assert len(s.quotes) == 2 and s.status == "quoted" and s.request.destination.name == "Jane"


def test_mark_booked_and_status_needs_a_booking(world):
    fs, _ = world
    sid = fs.create(_req())
    fs.add_manual_quote(sid, FreightQuote("uship", "Estes", 199.0))
    with pytest.raises(ValueError, match="no booking"):
        fs.set_status(sid, "picked_up")
    s = svc.mark_booked(fs, sid, 1, tracking="PRO7", price=210.0)
    assert s.status == "booked" and s.booking["manual"] and s.cost == 210.0
    with pytest.raises(ProviderNotReady, match="by hand"):
        svc.track(fs, sid)


def test_status_from_tracking_text():
    assert svc._status_from("Pickup successful") == "picked_up"
    assert svc._status_from("in-transit") == "in_transit"
    assert svc._status_from("Out for delivery") == "in_transit"
    assert svc._status_from("delivered") == "delivered"
    assert svc._status_from("label created") is None


# --------------------------------------------------------------------- CLI

def test_cli_quote_prints_table_and_saves_nothing(world, capsys, monkeypatch):
    import tools.freight as cli
    monkeypatch.setattr(svc, "store_origin", lambda s: FreightAddress(zip="60120"))
    rc = cli.main(["quote", "--to-zip", "30301", "--length", "48", "--width", "40",
                   "--height", "50", "--weight", "320", "--liftgate-delivery"])
    out = capsys.readouterr().out
    assert rc == 0 and "B Freight" in out and "Nothing saved" in out and "gamma" in out


def test_cli_book_is_dry_run_without_confirm(world, capsys, monkeypatch):
    import tools.freight as cli
    fs, provs = world
    monkeypatch.setattr(svc, "open_store", lambda path=None: fs)
    sid = fs.create(_req())
    svc.quote_shipment(fs, sid)
    assert cli.main(["book", str(sid), "--quote", "1"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert fs.require(sid).status == "quoted" and provs["beta"].booked == [False]


# --------------------------------------------------------------------- web app

@pytest.fixture
def client(world, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import webapp.freight as wf
    import webapp.server as server
    fs, _ = world
    wf.set_store(fs)
    monkeypatch.setattr(svc, "store_origin", lambda s: FreightAddress(zip="60120"))
    monkeypatch.setattr(svc, "provider_status", lambda: [
        {"provider": "alpha", "ready": True, "note": ""}])
    monkeypatch.setattr(wf, "get_freight_config", lambda: {})
    with TestClient(server.app) as c:
        yield c, fs, wf, monkeypatch
    wf.set_store(None)


PAYLOAD = {"destination": {"zip": "30301", "name": "Jane", "street1": "1 Main St",
                           "city": "Atlanta", "state": "GA", "phone": "4045550100"},
           "units": [{"length_in": 48, "width_in": 40, "height_in": 50, "weight_lbs": 320}],
           "accessorials": ["liftgate_delivery"], "pickup_date": "2026-10-14",
           "title": "Pfaff 463", "order_id": "12-345"}


def test_web_pages_render_with_pii_headers(client):
    c, fs, _, _ = client
    r = c.get("/freight")
    assert r.status_code == 200 and "New freight quote" in r.text
    assert "no-store" in r.headers["cache-control"]
    sid = c.post("/api/freight/shipments", json=PAYLOAD).json()["id"]
    r = c.get(f"/freight/{sid}")
    assert r.status_code == 200 and "B Freight" in r.text and "Book…" in r.text
    assert c.get("/freight/9999").status_code == 404


def test_web_adhoc_quote_and_bad_input(client):
    c, fs, _, _ = client
    r = c.post("/api/freight/quote", json=PAYLOAD)
    assert r.status_code == 200 and r.json()["quotes"][0]["carrier"] == "B Freight"
    assert fs.list() == []                                          # nothing saved
    bad = dict(PAYLOAD, destination={"zip": "nope"})
    assert c.post("/api/freight/quote", json=bad).status_code == 400


def test_web_book_needs_explicit_confirm(client):
    c, fs, _, _ = client
    sid = c.post("/api/freight/shipments", json=PAYLOAD).json()["id"]
    r = c.post(f"/api/freight/shipments/{sid}/book", json={"quote": 1})
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert fs.require(sid).status == "quoted"
    r = c.post(f"/api/freight/shipments/{sid}/book", json={"quote": 1, "confirm": True})
    assert r.status_code == 200 and r.json()["dry_run"] is False
    assert fs.require(sid).status == "booked"


def test_web_booking_switch_off_refuses_confirm_only(client):
    c, fs, wf, mp = client
    mp.setattr(wf, "get_freight_config", lambda: {"web_booking": False})
    sid = c.post("/api/freight/shipments", json=PAYLOAD).json()["id"]
    assert c.post(f"/api/freight/shipments/{sid}/book", json={"quote": 1}).status_code == 200
    r = c.post(f"/api/freight/shipments/{sid}/book", json={"quote": 1, "confirm": True})
    assert r.status_code == 403 and "--confirm" in r.json()["detail"]
    assert fs.require(sid).status == "quoted"


def test_web_manual_quote_patch_and_status(client):
    c, fs, _, _ = client
    sid = c.post("/api/freight/shipments", json=dict(PAYLOAD, quote=False)).json()["id"]
    r = c.post(f"/api/freight/shipments/{sid}/quotes",
               json={"provider": "uship", "carrier": "Estes", "price": 199})
    assert r.json()["quotes"][0]["tier"] == "manual"
    r = c.patch(f"/api/freight/shipments/{sid}", json={"buyer_paid": 300, "notes": "call first"})
    assert r.json()["buyer_paid"] == 300
    r = c.patch(f"/api/freight/shipments/{sid}",
                json={"destination": {"zip": "10001"}})
    assert r.json()["quotes"] == []
    assert c.post(f"/api/freight/shipments/{sid}/status",
                  json={"status": "cancelled"}).json()["status"] == "cancelled"
