"""tools/consign.py — the consignment ledger: state derivation, the money
rules and bulk import. Fake data in tmp_path only; nothing reads the real
inventory/ or ledgers, and nothing calls eBay."""
import csv
import io
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

SA = pytest.importorskip("sync_actuals", reason="sync_actuals imports ebay_client")
import consign  # noqa: E402
import stores  # noqa: E402

TODAY = date(2026, 12, 1)
OLD = (TODAY - timedelta(days=40)).isoformat()     # return window passed
NEW = (TODAY - timedelta(days=5)).isoformat()      # return window open
SALES_HEADER = ("order_id,sold_at,listing_id,sku,title,quantity,sold_format,item_price,"
                "buyer_shipping,refunded,gross,ebay_fee,net_before_postage,listed_price,"
                "pct_of_ask,shoot_dir,matched_by,ad_fee,actual_postage").split(",")


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """Two stores (default + junk) and an empty inventory/ under tmp_path."""
    monkeypatch.setattr(stores, "REPO", tmp_path)
    monkeypatch.setattr(stores, "load_config", lambda: {"storefronts": {"junk": {}}})
    for var in ("EBAYBIZ_STORE", "EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG"):
        monkeypatch.delenv(var, raising=False)
    for mod in (SA, consign):
        monkeypatch.setattr(mod, "REPO", tmp_path)
        monkeypatch.setattr(mod, "INVENTORY", tmp_path / "inventory")
    (tmp_path / "inventory").mkdir()
    return tmp_path


def _csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _folder(root, rel, sku=None):
    d = root / "inventory" / rel
    d.mkdir(parents=True)
    if sku is not None:
        (d / "draft.md").write_text(
            f'---\ntitle: "Thing {rel}"\nprice: "20.00"\nebay_inventory_sku: "{sku}"\n'
            f'---\n\nbody\n', encoding="utf-8")
    return f"inventory/{rel}"


def _sale(order, sku, sold_at, **kw):
    row = {"order_id": order, "sold_at": sold_at, "sku": sku, "title": "Thing",
           "item_price": "20.00", "buyer_shipping": "5.00", "refunded": "0.00",
           "ebay_fee": "3.30", "net_before_postage": "21.70", "ad_fee": "1.00",
           "actual_postage": "4.50", "matched_by": "sku"}
    row.update(kw)
    return row


def _intake(root, rows, client="K99"):
    d = root / "inventory" / "CONSIGN-20990101"
    d.mkdir()
    (d / "context.txt").write_text(f"kind: event\nclient: {client}\n", encoding="utf-8")
    _csv(d / "client_ledger.csv", consign.COLUMNS,
         [{c: r.get(c, "") for c in consign.COLUMNS} for r in rows])
    return d


def test_sync_derives_each_state_and_flags_a_vanished_sale(shop):
    # (item_id, extra ledger fields, expected state, text expected in state_note)
    f = lambda rel, sku=None: _folder(shop, rel, sku)  # noqa: E731
    table = [
        ("C001", {}, "INTAKE", ""),
        ("C002", {"inventory_folder": f("b/shot")}, "SHOT", ""),
        ("C003", {"inventory_folder": f("b/drafted", sku="")}, "DRAFTED", ""),
        ("C004", {"inventory_folder": f("b/listed", sku="aaaa0004")}, "LISTED", ""),
        ("C005", {"inventory_folder": f("b/sold", sku="aaaa0005")}, "SOLD", ""),
        ("C006", {"inventory_folder": f("b/cleared", sku="aaaa0006")}, "CLEARED", ""),
        ("C007", {"inventory_folder": f("b/nopostage", sku="aaaa0007")}, "SOLD", ""),
        ("C008", {"inventory_folder": f("b/settled", sku="aaaa0008"),
                  "paid_to_client_date": "2026-11-20"}, "SETTLED", ""),
        ("C009", {"inventory_folder": f("b/gone", sku="aaaa0009"), "state": "DISPOSED",
                  "state_note": "broken in transit, binned"}, "DISPOSED", ""),
        ("C010", {"inventory_folder": f("b/refunded", sku="aaaa0010"), "order_id": "9-9-9",
                  "client_share": "8.10", "paid_to_client_date": "2026-11-02"},
         "LISTED", "needs a negative adjustment"),
        ("C011", {"inventory_folder": f("b/ended", sku="aaaa0011")}, "LISTED", "ended unsold"),
    ]
    _csv(shop / "listings_ledger.csv", ["sku", "status", "price", "listing_id"],
         [{"sku": f"aaaa{n:04d}", "status": "PUBLISHED", "price": "24.00"}
          for n in (4, 5, 6, 7, 8, 9)]
         + [{"sku": "aaaa0010", "status": "SOLD"}, {"sku": "aaaa0011", "status": "ENDED"}])
    _csv(shop / "sales_ledger.csv", SALES_HEADER,
         [_sale("1-1-5", "aaaa0005", NEW), _sale("1-1-7", "aaaa0007", OLD, actual_postage=""),
          _sale("1-1-8", "aaaa0008", OLD)])
    # The junk store's sale is matched on shoot_dir alone (no SKU on the sale).
    _csv(shop / "sales_ledger-junk.csv", SALES_HEADER,
         [_sale("2-2-6", "", OLD, shoot_dir="inventory/b/cleared")])
    d = _intake(shop, [{"item_id": i, **extra} for i, extra, _, _ in table])

    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0

    got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
    for item_id, _, state, note in table:
        assert got[item_id]["state"] == state, item_id
        assert note in got[item_id]["state_note"], item_id
    assert got["C006"]["store"] == "junk"
    assert got["C006"]["cleared_on"] == (TODAY - timedelta(days=10)).isoformat()
    assert got["C007"]["actuals_complete"] == "no" and got["C007"]["net"] == ""
    assert got["C004"]["ask"] == "24.00"
    assert got["C009"]["state_note"] == "broken in transit, binned"
    assert list((d).glob("client_ledger.backup-*.csv")), "no backup written"


def test_hand_edit_errors_block_the_sync(shop):
    d = _intake(shop, [{"item_id": "C001", "state": "LOST"},
                       {"item_id": "C002", "state": "DISPOSED"},
                       {"item_id": "C003", "ad_fee": "two dollars"}])
    before = (d / "client_ledger.csv").read_text(encoding="utf-8")
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 1
    assert (d / "client_ledger.csv").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("fields, split, net, share, complete", [
    # net = net_before_postage - ad_fee - actual_postage - other_costs
    ({"ad_fee": "1.00", "actual_postage": "4.50", "other_costs": "2.00"},
     Decimal("0.5"), "14.20", "7.10", "yes"),
    # zero ad fee is a known zero; a blank one is unknown
    ({"ad_fee": "0.00", "actual_postage": "4.50"}, Decimal("0.5"), "17.20", "8.60", "yes"),
    ({"ad_fee": "", "actual_postage": "4.50"}, Decimal("0.5"), "", "", "no"),
    ({"ad_fee": "1.00", "actual_postage": ""}, Decimal("0.5"), "", "", "no"),
    # half-up to cents: 16.25 * 0.5 = 8.125 -> 8.13; 16.25 * 0.6 = 9.75
    ({"ad_fee": "0.95", "actual_postage": "4.50"}, Decimal("0.5"), "16.25", "8.13", "yes"),
    ({"ad_fee": "0.95", "actual_postage": "4.50"}, Decimal("0.6"), "16.25", "9.75", "yes"),
])
def test_money_rules(fields, split, net, share, complete):
    r = {c: "" for c in consign.COLUMNS}
    r.update(item_price="20.00", buyer_shipping="5.00", refunded="1.50",
             net_before_postage="21.70", **fields)
    consign.settle_money(r, split)
    assert r["gross"] == "23.50"                       # 20 + 5 - 1.50
    assert (r["net"], r["client_share"], r["actuals_complete"]) == (net, share, complete)


def test_import_roster_and_stdin_tsv(shop, monkeypatch, capsys):
    roster = shop / "roster.csv"
    roster.write_text(
        "bin,container,line,item,qty,note,photos\n"
        "01,Tote,01.1,\"Brass bell, small\",1,ring it,ZZ1 ZZ2\n"
        "01,Tote,01.2,Tin cup,2,,ZZ3\n"
        "02,Box,02.1,,1,blank description is skipped,\n", encoding="utf-8")
    assert consign.main(["import", "CONSIGN-20990202", str(roster), "--client", "K99"]) == 0
    d = shop / "inventory" / "CONSIGN-20990202"
    assert consign.intake_client(d) == "K99"

    tsv = "description\tbin\test_low\test_high\nTin cup\t01\t\t\nWood spool\t03\t4\t8\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(tsv))
    assert consign.main(["import", "CONSIGN-20990202", "-"]) == 0
    assert "added 1 row(s), skipped 1 duplicate(s)" in capsys.readouterr().out

    rows = consign.read_ledger(d / "client_ledger.csv")[0]
    assert [(r["item_id"], r["bin"], r["description"]) for r in rows] == [
        ("C001", "01", "Brass bell, small"), ("C002", "01", "Tin cup"),
        ("C003", "03", "Wood spool")]
    assert (rows[0]["qty"], rows[0]["why"], rows[0]["photos"]) == ("1", "ring it", "ZZ1 ZZ2")
    assert (rows[2]["est_low"], rows[2]["est_high"], rows[2]["state"]) == ("4", "8", "INTAKE")
