#!/usr/bin/env python3
"""tools/pick_list_html.py — the printed haiku block (GH #161).

render_html() is otherwise covered indirectly (it's the tool behind the
`/pick/{token}` route), but the haiku it now appends at the bottom of the
page has its own rule worth locking down: the haiku's own text must never
carry the buyer's name, only the item/region-themed lines from
tools/haiku.py's curated pools.

Run:  python tests/test_pick_list_html.py
  or: pytest tests/test_pick_list_html.py
"""
import html
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

pick_list_html = pytest.importorskip(
    "pick_list_html", reason="pick_list_html imports numpy/Pillow")
import haiku                                                       # noqa: E402


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


def _haiku_block(out_html: str) -> str:
    m = re.search(r'<div class="haiku">(.*?)</div>', out_html, re.S)
    assert m, "no <div class=\"haiku\"> block in rendered page"
    return html.unescape(m.group(1))


def test_render_html_includes_a_haiku_block():
    out = pick_list_html.render_html([_order()], [], [])
    assert 'class="haiku"' in out


def test_haiku_block_matches_the_generator():
    order = _order()
    out = pick_list_html.render_html([order], [], [])
    block = _haiku_block(out)
    expected = "<br>".join(haiku.generate_haiku(
        order["orderId"], order["lineItems"][0]["title"], "OH"))
    assert block == expected


def test_haiku_text_never_contains_buyer_name():
    order = _order(fullname="Jamie Buyer", city="Springfield", state="OH")
    out = pick_list_html.render_html([order], [], [])
    block = _haiku_block(out).lower()
    for forbidden in ("jamie", "buyer", "springfield"):
        assert forbidden not in block


def test_haiku_block_stable_across_rerenders():
    order = _order()
    first = _haiku_block(pick_list_html.render_html([order], [], []))
    second = _haiku_block(pick_list_html.render_html([order], [], []))
    assert first == second


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
