"""live_audit decides purchasability from listingStatus, not Browse presence.

The regression this pins: Browse takes one category per call, so `fetch_actives`
only sees listings in the categories it thought to ask for. A listing outside
that list is absent from the results, and absent looked exactly like ended —
19 live listings were reported GONE on 2026-09-22 for that reason alone.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "lib"), str(ROOT / "tools")]

import live_audit  # noqa: E402


def offer(sku, lid, status="PUBLISHED", listing_status="ACTIVE", price="10.00"):
    return {"sku": sku, "listing_id": lid, "status": status,
            "listing_status": listing_status, "price": price, "title": "t"}


def states(offers, actives):
    rows = live_audit.reconcile(offers, actives, [], [])
    return {r["sku"]: r["state"] for r in rows}


class PurchasableFromListingStatus(unittest.TestCase):
    def test_active_offer_missing_from_browse_is_still_live(self):
        """The actual bug: Browse never searched this listing's category."""
        got = states([offer("a", "1")], actives={})
        self.assertEqual(got["a"], "LIVE")

    def test_out_of_stock_is_gone_even_when_browse_still_lists_it(self):
        got = states([offer("a", "1", listing_status="OUT_OF_STOCK")],
                     actives={"1": {"askingPrice": "10.00"}})
        self.assertEqual(got["a"], "GONE")

    def test_ended_is_gone(self):
        got = states([offer("a", "1", listing_status="ENDED")], actives={})
        self.assertEqual(got["a"], "GONE")

    def test_gone_reason_names_the_cause(self):
        rows = live_audit.reconcile(
            [offer("a", "1", listing_status="OUT_OF_STOCK")], {}, [], [])
        self.assertIn("it sold", " ".join(rows[0]["issues"]))
        rows = live_audit.reconcile(
            [offer("b", "2", listing_status="ENDED")], {}, [], [])
        self.assertIn("it was ended", " ".join(rows[0]["issues"]))

    def test_unpublished_offer_keeps_its_own_status(self):
        got = states([offer("a", "1", status="UNPUBLISHED")], actives={})
        self.assertEqual(got["a"], "UNPUBLISHED")

    def test_falls_back_to_browse_when_listing_status_absent(self):
        """A cached offers JSON written before this change has no field."""
        stale = offer("a", "1")
        del stale["listing_status"]
        self.assertEqual(states([stale], actives={})["a"], "GONE")
        self.assertEqual(states([stale], actives={"1": {}})["a"], "LIVE")


if __name__ == "__main__":
    unittest.main()
