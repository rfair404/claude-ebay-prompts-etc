#!/usr/bin/env python3
"""`_resolve_shipping_policy(strict=True)` must fail fast, not fall back.

Copilot flagged this on PR #49: when an item is US-export-restricted and no
`fulfillment_policy_id_us_only` is configured, the write paths (--sync, the
`policies` field update) chose the DEFAULT policy anyway and only logged a
"BLOCKER" string. The default policy ships Worldwide, so that default choice
is exactly the eIS-eligibility trap this module exists to close — and if eBay
ever stopped catching it at publish time, it would mean actually exporting a
restricted item. `strict=True` (used by every caller that is about to WRITE
the resolved policy into an offer) now raises instead.

`preflight_listing` stays non-strict on purpose: it is read-only and its job
is to SHOW the operator this exact problem before they decide to fix it, so
raising there would break the "check without touching anything" workflow.

Run:  python tests/test_shipping_policy_strict.py
  or: pytest tests/test_shipping_policy_strict.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import list_edit as L                                         # noqa: E402
from draft_io import Draft                                    # noqa: E402


class _Creds:
    has_user = True


def _draft(frontmatter):
    return Draft(path=Path("inventory/example/draft.md"), frontmatter=frontmatter, body="")


RESTRICTED_TITLE = "RUGER Mini 14 Factory 5 Round Magazine 223 5.56 Blued Steel OEM"
ORDINARY_TITLE = "Vintage Cast Iron Skillet No. 8 Smooth Bottom"

_BASE_POLICIES = {
    "fulfillment": "default-fid",
    "fulfillment_media": "media-fid",
    "fulfillment_local_pickup": "pickup-fid",
    "fulfillment_international": "intl-fid",
}


def _fulfillment_policies_stub(ids):
    """Fake get_fulfillment_policies(): a parcel-shipping policy per id."""
    return [{"fulfillmentPolicyId": fid,
             "shippingOptions": [{"shippingServices": [{"shippingServiceCode": "USPSGround"}]}]}
            for fid in ids]


# --------------------------------------------------------- strict=True (write paths)
def test_strict_raises_when_us_only_policy_missing():
    draft = _draft({"title": RESTRICTED_TITLE})
    try:
        L._resolve_shipping_policy(draft, dict(_BASE_POLICIES), _Creds(), strict=True)
        raise AssertionError("expected ValueError — US-restricted item with no US-only policy")
    except ValueError as e:
        assert "BLOCKER" in str(e)
        assert "US-export-restricted" in str(e)


def test_strict_does_not_raise_when_us_only_policy_configured():
    policies = {**_BASE_POLICIES, "fulfillment_us_only": "usonly-fid"}
    draft = _draft({"title": RESTRICTED_TITLE})
    L.get_fulfillment_policies = lambda creds=None: _fulfillment_policies_stub(
        list(policies.values()))
    chosen, msgs = L._resolve_shipping_policy(draft, policies, _Creds(), strict=True)
    assert chosen == "usonly-fid"
    assert any("US-ONLY" in m for m in msgs)


def test_strict_does_not_raise_for_ordinary_item():
    draft = _draft({"title": ORDINARY_TITLE})
    L.get_fulfillment_policies = lambda creds=None: _fulfillment_policies_stub(
        list(_BASE_POLICIES.values()))
    chosen, _msgs = L._resolve_shipping_policy(draft, dict(_BASE_POLICIES), _Creds(), strict=True)
    assert chosen == _BASE_POLICIES["fulfillment"]


# --------------------------------------------------------- strict=False (preflight, advisory)
def test_non_strict_falls_back_with_blocker_message():
    draft = _draft({"title": RESTRICTED_TITLE})
    L.get_fulfillment_policies = lambda creds=None: _fulfillment_policies_stub(
        list(_BASE_POLICIES.values()))
    chosen, msgs = L._resolve_shipping_policy(draft, dict(_BASE_POLICIES), _Creds())
    assert chosen == _BASE_POLICIES["fulfillment"]
    assert any("BLOCKER" in m for m in msgs)


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception:
            bad += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
