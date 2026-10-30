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


DRAFT = ('---\ntitle: "old title"\nprice: "10.00"\nmeta:\n'
         '  ebay_inventory_sku: "{sku}"\n  ebay_listing_id: "{lid}"\n'
         '  notes: ""\n---\n')


class ApplyNeverWritesTheLedger(unittest.TestCase):
    """#345: --apply rewrites only drafts it owns and never the ledger."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def draft(self, name, sku, lid):
        p = self.tmp / name / "draft.md"
        p.parent.mkdir(parents=True)
        p.write_text(DRAFT.format(sku=sku, lid=lid), encoding="utf-8")
        return {"path": p, "dir": name, "title": "old title", "price": "10.00",
                "sku": sku, "listing_id": lid}

    def run_apply(self, offers, drafts):
        rows = live_audit.reconcile(offers, {}, drafts, [])
        for r in rows:
            if r["state"] == "LIVE":
                live_audit.apply_row(r, verbose=False)
        return rows

    def test_listing_id_match_is_rewritten(self):
        d = self.draft("own", "sku-own-1", "111")
        self.run_apply([dict(offer("sku-own-1", "111", price="25.00"),
                             title="new title")], [d])
        text = d["path"].read_text(encoding="utf-8")
        self.assertIn('price: "25.00"', text)
        self.assertIn('title: "new title"', text)

    def test_sku_only_match_is_left_byte_identical(self):
        for lid in ("", "999"):
            with self.subTest(draft_listing_id=lid):
                d = self.draft(f"reshoot{lid}", f"sku-shared-{lid}", lid)
                before = d["path"].read_bytes()
                self.run_apply([dict(offer(f"sku-shared-{lid}", "111",
                                           price="25.00"), title="new title")],
                               [d])
                self.assertEqual(d["path"].read_bytes(), before)

    def test_gone_row_draft_is_never_rewritten(self):
        d = self.draft("gone", "sku-gone-1", "111")
        before = d["path"].read_bytes()
        rows = live_audit.reconcile(
            [dict(offer("sku-gone-1", "111", listing_status="OUT_OF_STOCK",
                        price="25.00"), title="new title")], {}, [d], [])
        self.assertEqual(rows[0]["state"], "GONE")
        self.assertEqual(live_audit.apply_row(rows[0], verbose=False), [])
        self.assertEqual(d["path"].read_bytes(), before)

    def test_main_apply_leaves_the_ledger_byte_identical(self):
        import csv
        import json
        from unittest import mock
        led = self.tmp / "listings_ledger.csv"
        with led.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["sku", "status", "price", "title"])
            w.writeheader()
            w.writerow({"sku": "ge-tube-6sn7gtb", "status": "SOLD",
                        "price": "12.00", "title": "GE 6SN7GTB"})
            w.writerow({"sku": "sku-own-1", "status": "PUBLISHED",
                        "price": "10.00", "title": "old title"})
        before = led.read_bytes()
        offers = self.tmp / "o.json"
        offers.write_text(json.dumps([
            offer("ge-tube-6sn7gtb", "222", listing_status="OUT_OF_STOCK"),
            dict(offer("sku-own-1", "111", price="25.00"), title="new title"),
        ]), encoding="utf-8")
        actives = self.tmp / "a.json"
        actives.write_text("[]", encoding="utf-8")
        d = self.draft("own", "sku-own-1", "111")
        with mock.patch.object(live_audit, "ledger_path", lambda store=None: led), \
                mock.patch.object(live_audit, "scan_drafts",
                                  lambda store=None, ledger=None: [d]):
            self.assertEqual(live_audit.main(
                ["--offers", str(offers), "--actives", str(actives),
                 "--apply"]), 0)
        self.assertEqual(led.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()),
                         ["a.json", "listings_ledger.csv", "o.json", "own"])
        self.assertIn('price: "25.00"', d["path"].read_text(encoding="utf-8"))
        self.assertFalse(hasattr(live_audit, "apply_ledger"))


if __name__ == "__main__":
    unittest.main()
