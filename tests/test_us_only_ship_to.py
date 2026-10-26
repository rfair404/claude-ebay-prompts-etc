"""A US-only fulfillment policy must exclude the world, not just omit it.

Measured 2026-10-25 on the main store: policy 297194269014 had
`shipToLocations: {}` and listings on it still got an "eBay International
Shipping" option for GB, CA and DE buyers, because eIS is on account-wide.
Naming every region outside the US in `regionExcluded` stopped it.

These tests hold the three places that knowledge lives: the policy body
`create_calculated_shipping_policy` POSTs, `ship_to_warning`, and the
preflight message `_resolve_shipping_policy` emits for the US-only policy.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import ebay_client as ec                                      # noqa: E402
import list_edit as L                                         # noqa: E402
from draft_io import Draft                                    # noqa: E402
from us_only import US_ONLY_SHIP_TO, ship_to_warning          # noqa: E402

RESTRICTED_TITLE = "RUGER Mini 14 Factory 5 Round Magazine 223 5.56 Blued Steel OEM"


def _policy(fid, ship_to):
    return {"fulfillmentPolicyId": fid, "name": "US Only - Free USPS Ground (1 day)",
            "shipToLocations": ship_to,
            "shippingOptions": [{"optionType": "DOMESTIC", "shippingServices": [
                {"shippingServiceCode": "USPSParcel", "freeShipping": True}]}]}


def _read_back(ship_to):
    """What eBay returns after a PUT: regionIncluded US is dropped and the
    exclusions come back with regionName only."""
    return {"regionExcluded": [{"regionName": r["regionName"]}
                               for r in ship_to.get("regionExcluded", [])]}


# ------------------------------------------------------------- ship_to_warning
def test_empty_ship_to_warns():
    msg = ship_to_warning(_policy("297194269014", {}))
    assert "no shipToLocations exclusions" in msg
    assert "eBay International Shipping" in msg


def test_us_included_only_warns():
    msg = ship_to_warning(_policy("p", {"regionIncluded": [{"regionName": "US"}]}))
    assert "no shipToLocations exclusions" in msg


def test_worldwide_exclusion_alone_warns():
    # eBay stored this but it never reached the listings; eIS kept selling abroad.
    msg = ship_to_warning(_policy("p", {"regionExcluded": [{"regionName": "Worldwide"}]}))
    assert "does not exclude" in msg and "Europe" in msg and "CA" in msg


def test_partial_exclusion_names_what_is_missing():
    excl = [r for r in _read_back(US_ONLY_SHIP_TO)["regionExcluded"]
            if r["regionName"] not in ("GL", "PM")]
    msg = ship_to_warning(_policy("p", {"regionExcluded": excl}))
    assert msg.endswith("(lib/us_only.py).")
    assert "GL, PM" in msg and "Europe" not in msg


def test_full_exclusion_read_back_is_clean():
    assert ship_to_warning(_policy("p", _read_back(US_ONLY_SHIP_TO))) == ""


def test_us_is_never_excluded():
    names = {r["regionName"] for r in US_ONLY_SHIP_TO["regionExcluded"]}
    assert not names & {"US", "North America", "Worldwide"}
    assert US_ONLY_SHIP_TO["regionIncluded"] == [{"regionName": "US", "regionType": "COUNTRY"}]


# ------------------------------------------- create_calculated_shipping_policy
def test_calculated_policy_is_created_us_only(monkeypatch):
    sent = {}
    monkeypatch.setattr(ec, "get_fulfillment_policies", lambda *a, **k: [])

    def fake_send(method, path, body=None, **kw):
        sent.update(method=method, path=path, body=body)
        return {"fulfillmentPolicyId": "new"}

    monkeypatch.setattr(ec, "api_send", fake_send)
    ec.create_calculated_shipping_policy(creds=object())
    assert sent["method"] == "POST"
    assert sent["path"] == "/sell/account/v1/fulfillment_policy"
    assert sent["body"]["shipToLocations"] == US_ONLY_SHIP_TO
    assert ship_to_warning(sent["body"]) == ""
    # The body is a copy: a caller mutating it can't change the shared constant.
    assert sent["body"]["shipToLocations"] is not US_ONLY_SHIP_TO
    sent["body"]["shipToLocations"]["regionExcluded"].clear()
    assert US_ONLY_SHIP_TO["regionExcluded"]


# --------------------------------------------- preflight (_resolve_shipping_policy)
class _Creds:
    has_user = True
    store = "default"
    environment = "sandbox"


_POLICIES = {"fulfillment": "default-fid", "fulfillment_us_only": "usonly-fid"}


def _resolve(monkeypatch, title, us_only_ship_to):
    pols = [_policy("default-fid", {"regionIncluded": [{"regionName": "Worldwide"}]}),
            _policy("usonly-fid", us_only_ship_to)]
    monkeypatch.setattr(L, "get_fulfillment_policies", lambda creds=None: pols)
    draft = Draft(path=Path("inventory/example/draft.md"),
                  frontmatter={"title": title}, body="")
    return L._resolve_shipping_policy(draft, dict(_POLICIES), _Creds())


def test_preflight_warns_on_empty_us_only_ship_to(monkeypatch):
    chosen, msgs = _resolve(monkeypatch, RESTRICTED_TITLE, {})
    assert chosen == "usonly-fid"
    assert any("WARNING — US-only policy" in m for m in msgs)


def test_preflight_quiet_when_us_only_policy_excludes_world(monkeypatch):
    chosen, msgs = _resolve(monkeypatch, RESTRICTED_TITLE, _read_back(US_ONLY_SHIP_TO))
    assert chosen == "usonly-fid"
    assert not any("WARNING — US-only policy" in m for m in msgs)


def test_preflight_does_not_check_other_policies(monkeypatch):
    # An ordinary item on the Worldwide default is not a US-only problem.
    chosen, msgs = _resolve(monkeypatch, "Vintage Cast Iron Skillet No. 8", {})
    assert chosen == "default-fid"
    assert not any("WARNING — US-only policy" in m for m in msgs)
