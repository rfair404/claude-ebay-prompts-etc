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
OLD = (TODAY - timedelta(days=50)).isoformat()     # return window passed
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
        # paid 9.00; the books now say 8.10: the paid figure stays, flagged
        ("C008", {"inventory_folder": f("b/settled", sku="aaaa0008"), "client_share": "9.00",
                  "paid_to_client_date": "2026-11-20"}, "SETTLED", "adjust by $-0.90"),
        ("C009", {"inventory_folder": f("b/gone", sku="aaaa0009"), "state": "DISPOSED",
                  "state_note": "broken in transit, binned"}, "DISPOSED", ""),
        ("C010", {"inventory_folder": f("b/refunded", sku="aaaa0010"), "order_id": "9-9-9",
                  "client_share": "8.10", "paid_to_client_date": "2026-11-02"},
         "LISTED", "needs a negative adjustment"),
        # an ended listing is not LISTED: the row keeps its on-hand state, flagged
        ("C011", {"inventory_folder": f("b/ended", sku="aaaa0011")}, "DRAFTED",
         "listing aaaa0011 ended 2026-11-20: relist, return or dispose"),
        ("C012", {"state": "EXCLUDED", "buyout_price": "5.00"}, "EXCLUDED", ""),
        # PREP holds through SHOT/DRAFTED and gives way to LISTED
        ("C013", {"state": "PREP"}, "PREP", ""),
        ("C014", {"state": "PREP", "inventory_folder": f("b/prep", sku="")}, "PREP", ""),
        ("C015", {"state": "PREP", "inventory_folder": f("b/prepped", sku="aaaa0015")},
         "LISTED", ""),
        # LISTED means live on eBay: DRAFTED and SYNCED ledger rows are not
        ("C016", {"inventory_folder": f("b/ledger-drafted", sku="aaaa0016")}, "DRAFTED", ""),
        ("C017", {"inventory_folder": f("b/ledger-synced", sku="aaaa0017")}, "DRAFTED", ""),
        ("C018", {"state": "PREP", "inventory_folder": f("b/prep-synced", sku="aaaa0018")},
         "PREP", ""),
        ("C019", {"sku": "aaaa0019"}, "INTAKE", ""),
        ("C020", {"inventory_folder": f("b/shipped", sku="aaaa0020")}, "LISTED", ""),
        # DELETED after it was published is an ended listing; never published, never live
        ("C021", {"inventory_folder": f("b/deleted-live", sku="aaaa0021")}, "DRAFTED",
         "ended"),
        ("C022", {"inventory_folder": f("b/deleted-draft", sku="aaaa0022")}, "DRAFTED", ""),
        ("C023", {"inventory_folder": f("b/out-of-stock", sku="aaaa0023")}, "LISTED", ""),
        ("C024", {"state": "PREP", "inventory_folder": f("b/prep-ended", sku="aaaa0024")},
         "PREP", "ended"),
        # ended on junk, relisted on default: the live row wins
        ("C025", {"inventory_folder": f("b/relisted", sku="aaaa0025")}, "LISTED", ""),
    ]
    _csv(shop / "listings_ledger.csv",
         ["sku", "status", "price", "listing_id", "published_at", "ended_at", "updated_at"],
         [{"sku": f"aaaa{n:04d}", "status": "PUBLISHED", "price": "24.00"}
          for n in (4, 5, 6, 7, 8, 9, 15)]
         + [{"sku": "aaaa0010", "status": "SOLD"},
            {"sku": "aaaa0011", "status": "ENDED", "ended_at": "2026-11-20T18:00:00Z"},
            {"sku": "aaaa0016", "status": "DRAFTED", "price": "30.00"},
            {"sku": "aaaa0017", "status": "SYNCED", "price": "30.00"},
            {"sku": "aaaa0018", "status": "SYNCED"}, {"sku": "aaaa0019", "status": "DRAFTED"},
            {"sku": "aaaa0020", "status": "SHIPPED"},
            {"sku": "aaaa0021", "status": "DELETED", "published_at": "2026-10-01"},
            {"sku": "aaaa0022", "status": "DELETED"},
            {"sku": "aaaa0023", "status": "OUT_OF_STOCK"},
            {"sku": "aaaa0024", "status": "ENDED"},
            {"sku": "aaaa0025", "status": "PUBLISHED", "updated_at": "2026-11-01"}])
    _csv(shop / "listings_ledger-junk.csv", ["sku", "status", "updated_at"],
         [{"sku": "aaaa0025", "status": "ENDED", "updated_at": "2026-11-15"}])
    _csv(shop / "sales_ledger.csv", SALES_HEADER,
         [_sale("1-1-5", "aaaa0005", NEW), _sale("1-1-7", "aaaa0007", OLD, actual_postage=""),
          _sale("1-1-8", "aaaa0008", OLD)])
    # The junk store's sale is matched on shoot_dir alone (no SKU on the sale).
    _csv(shop / "sales_ledger-junk.csv", SALES_HEADER,
         [_sale("2-2-6", "", OLD, shoot_dir="inventory/b/cleared")])
    d = _intake(shop, [{"item_id": i, **extra} for i, extra, _, _ in table])

    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0

    rows = consign.read_ledger(d / "client_ledger.csv")[0]
    assert all(r["item_id"].startswith("K99-C") and r["client_code"] == "K99" for r in rows)
    got = {r["item_id"].removeprefix("K99-"): r for r in rows}
    for item_id, _, state, note in table:
        assert got[item_id]["state"] == state, item_id
        assert note in got[item_id]["state_note"], item_id
    assert got["C006"]["store"] == "junk"
    assert got["C025"]["store"] == "default"
    assert "ended" not in got["C025"]["state_note"]
    assert got["C006"]["cleared_on"] == (TODAY - timedelta(days=10)).isoformat()
    assert got["C008"]["client_share"] == "9.00"
    assert got["C012"]["client_share"] == "5.00"
    assert got["C007"]["actuals_complete"] == "no" and got["C007"]["net"] == ""
    assert got["C004"]["ask"] == "24.00"
    assert got["C017"]["ask"] == "20.00"           # the draft's price, not the ledger's
    assert got["C009"]["state_note"] == "broken in transit, binned"
    assert list((d).glob("client_ledger.backup-*.csv")), "no backup written"


@pytest.mark.parametrize("apply", [[], ["--apply"]])
def test_sync_refuses_to_run_outside_the_main_checkout(shop, tmp_path_factory,
                                                        monkeypatch, apply):
    d = _intake(shop, [{"item_id": "C001", "state": "SETTLED", "client_share": "8.10",
                        "paid_to_client_date": "2026-11-02"}])
    before = (d / "client_ledger.csv").read_bytes()
    main_checkout = tmp_path_factory.mktemp("main")
    monkeypatch.setattr(consign.offsite, "data_root", lambda start=None: main_checkout)
    with pytest.raises(SystemExit, match="from the main checkout"):
        consign.main(["sync", *apply])
    assert (d / "client_ledger.csv").read_bytes() == before
    assert consign.main(["report"]) == 0        # report still works there


def test_hand_edit_errors_block_the_sync(shop, capsys):
    shared = _folder(shop, "b/shared")
    d = _intake(shop, [{"item_id": "C001", "state": "LOST"},
                       {"item_id": "C002", "state": "DISPOSED"},
                       {"item_id": "C003", "ad_fee": "two dollars"},
                       {"item_id": "C004", "state": "EXCLUDED"},
                       {"item_id": "C005", "inventory_folder": shared},
                       {"item_id": "C006", "inventory_folder": shared},
                       {"item_id": "C007", "inventory_folder": shared, "state": "RETURNED"}])
    before = (d / "client_ledger.csv").read_text(encoding="utf-8")
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 1
    assert (d / "client_ledger.csv").read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    for want in ("C001: state 'LOST'", "C002: DISPOSED needs", "C003: ad_fee",
                 "C004: EXCLUDED needs a buyout_price", "C005, C006 share"):
        assert want in out, want


def test_report_owes_an_excluded_buyout_before_sync(shop, capsys):
    # Straight from buyout_price: client_share is still blank until sync runs.
    _intake(shop, [{"item_id": "C001", "state": "EXCLUDED", "buyout_price": "12.50"}])
    assert consign.main(["report"]) == 0
    assert "buyouts owed          $12.50" in capsys.readouterr().out


@pytest.mark.parametrize("fields, split, net, share, complete", [
    # net = net_before_postage - ad_fee - actual_postage - handling - other_costs
    ({"ad_fee": "1.00", "actual_postage": "4.50", "other_costs": "2.00"},
     Decimal("0.5"), "14.20", "7.10", "yes"),
    ({"ad_fee": "1.00", "actual_postage": "4.50", "handling": "2.00"},
     Decimal("0.5"), "14.20", "7.10", "yes"),
    ({"ad_fee": "1.00", "actual_postage": "4.50", "handling": "2.00", "other_costs": "3.00"},
     Decimal("0.5"), "11.20", "5.60", "yes"),
    # a blank handling (sold before the fee) is zero, not unknown
    ({"ad_fee": "1.00", "actual_postage": "4.50", "handling": ""},
     Decimal("0.5"), "16.20", "8.10", "yes"),
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
    assert [(r["item_id"], r["bin"], r["description"], r["intake"]) for r in rows] == [
        ("K99-C001", "01", "Brass bell, small", "2099-02-02"),
        ("K99-C002", "01", "Tin cup", "2099-02-02"),
        ("K99-C003", "03", "Wood spool", "2099-02-02")]
    assert (rows[0]["qty"], rows[0]["why"], rows[0]["photos"]) == ("1", "ring it", "ZZ1 ZZ2")
    assert (rows[2]["est_low"], rows[2]["est_high"], rows[2]["state"]) == ("4", "8", "INTAKE")


# --------------------------------------------------------------------------- #
# per-client ledgers, lots, settlements, locked splits (#284)
# --------------------------------------------------------------------------- #
def _ledger(root, name, rows, context="", header=None):
    """A CONSIGN-* folder with a ledger. `header` writes an older column set."""
    d = root / "inventory" / name
    d.mkdir(parents=True, exist_ok=True)
    if context:
        (d / "context.txt").write_text(context, encoding="utf-8")
    cols = header or consign.COLUMNS
    _csv(d / "client_ledger.csv", cols, [{c: r.get(c, "") for c in cols} for r in rows])
    return d


def _row(**kw):
    r = {c: "" for c in consign.COLUMNS}
    r.update(kw)
    return r


def _sold_item(shop, rel, sku, sold_at, order, **sale):
    """A folder whose draft is live on eBay and has sold."""
    folder = _folder(shop, rel, sku=sku)
    for name, header, row in (
            ("listings_ledger.csv", ["sku", "status", "price"],
             {"sku": sku, "status": "PUBLISHED", "price": "24.00"}),
            ("sales_ledger.csv", SALES_HEADER, _sale(order, sku, sold_at, **sale))):
        p = shop / name
        rows = list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []
        _csv(p, header, rows + [row])
    # its eBay fee lines, tying to the sale's ebay_fee and ad_fee
    fee, ad = Decimal(sale.get("ebay_fee", "3.30")), Decimal(sale.get("ad_fee", "1.00"))
    _fee_lines(shop, [_fl(order, sku, "FINAL_VALUE_FEE", fee - Decimal("0.40"), "25.00"),
                      _fl(order, sku, "FINAL_VALUE_FEE_FIXED_PER_ORDER", "0.40", "25.00")]
               + ([_fl(order, sku, "AD", ad)] if ad else []))
    return folder


def _fl(order, sku, fee_type, amount, basis="", **kw):
    return {"order_id": order, "line_item_id": f"L-{order}", "sku": sku, "date": OLD,
            "fee_type": fee_type, "amount": str(amount), "basis": basis, **kw}


def _fee_lines(shop, rows, name="fee_lines.csv"):
    p = shop / "reports" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    old = list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []
    _csv(p, SA.FEE_LINE_FIELDS, old + rows)


F, G = "inventory/b/one", "inventory/b/two"


@pytest.mark.parametrize("rows, want", [
    # one lot, one folder: allowed (a lot row may leave the folder blank)
    ([dict(item_id="C001", inventory_folder=F, lot_id="L1"),
      dict(item_id="C002", inventory_folder=F, lot_id="L1"),
      dict(item_id="C003", lot_id="L1")], None),
    # two lots, or a lot and a single, on one folder
    ([dict(item_id="C001", inventory_folder=F, lot_id="L1"),
      dict(item_id="C002", inventory_folder=F, lot_id="L2")], "C001, C002 share"),
    ([dict(item_id="C001", inventory_folder=F, lot_id="L1"),
      dict(item_id="C002", inventory_folder=F)], "C001, C002 share"),
    # one lot on two folders
    ([dict(item_id="C001", inventory_folder=F, lot_id="L1"),
      dict(item_id="C002", inventory_folder=G, lot_id="L1")], "lot L1 is on 2 folders"),
    # a returned lot row has left the lot
    ([dict(item_id="C001", inventory_folder=F, lot_id="L1"),
      dict(item_id="C002", inventory_folder=G, lot_id="L1", state="RETURNED")], None),
    # ids: another client's prefix, or the same id bare and prefixed
    ([dict(item_id="K98-C001")], "K98-C001: item_id is client K98's"),
    ([dict(item_id="C001"), dict(item_id="K99-C001")], "K99-C001: item_id used twice"),
    ([dict(item_id="C001", split_locked="1.5")], "split_locked '1.5'"),
    ([dict(item_id="C001", sold_at="2026-11-02", delivered_at="2026-11-01")],
     "delivered_at 2026-11-01 is before sold_at"),
    # a lot-mate (not the lowest id) that already sold or was paid
    *[([dict(item_id="C001", inventory_folder=F, lot_id="L1"),
        dict(item_id="C005", inventory_folder=F, lot_id="L1", **{field: value})],
       f"C005: in lot L1 but not its lead (C001) and has {field}: make the sold row "
       f"the lowest item_id in the lot, or take it out of the lot")
      for field, value in (("order_id", "1-1-5"), ("client_share", "8.10"),
                           ("settlement_id", "K99-S001"),
                           ("paid_to_client_date", "2026-11-20"))],
    # ...the same figures on the lead are fine
    ([dict(item_id="C001", inventory_folder=F, lot_id="L1", order_id="1-1-5",
           client_share="8.10"),
      dict(item_id="C005", inventory_folder=F, lot_id="L1")], None),
    # one order on two active rows; a returned row no longer counts
    ([dict(item_id="C001", order_id="1-1-5"), dict(item_id="C002", order_id="1-1-5")],
     "C001, C002 all carry order 1-1-5"),
    ([dict(item_id="C001", order_id="1-1-5"),
      dict(item_id="C002", order_id="1-1-5", state="RETURNED")], None),
    ([dict(item_id="C001", state="HOLD")], "C001: HOLD needs a state_note saying why"),
])
def test_validate_lots_and_ids(rows, want):
    errs = consign.validate([_row(**r) for r in rows], [], "K99")
    if want is None:
        assert errs == []
    else:
        assert any(want in e for e in errs), errs


def test_a_lot_sells_once_on_its_lead_row(shop, capsys):
    folder = _sold_item(shop, "b/lot", "cccc0001", OLD, "3-3-1")
    d = _intake(shop, [
        {"item_id": "C001", "lot_id": "L1", "est_low": "4", "est_high": "6"},
        {"item_id": "C002", "lot_id": "L1", "inventory_folder": folder,
         "ask": "30.00", "state_note": "second basket"},
        {"item_id": "C003", "inventory_folder": _folder(shop, "b/single")}])
    fee = {"K99": {"client_code": "K99", "handling_fee": "2.00"}}
    for _ in range(2):                           # the second sync changes nothing
        assert consign.sync_intake(d, consign.Books(), fee, apply=True, today=TODAY) == 0
        got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
        lead, mate = got["K99-C001"], got["K99-C002"]
        assert (lead["state"], lead["inventory_folder"], lead["client_share"]) == (
            "CLEARED", folder, "7.10")
        assert (mate["state"], mate["sku"], mate["ask"], mate["order_id"], mate["handling"]) == (
            "CLEARED", "cccc0001", "", "", "")      # one sale, one handling fee
        assert mate["state_note"] == "second basket | in lot L1 with K99-C001"
    assert consign.report([d], "K99", False, today=TODAY) == 0
    out = capsys.readouterr().out
    assert "sold       1  net $14.20" in out
    assert "handling kept         $2.00  (1 sale(s)" in out
    assert "client share owed     $7.10" in out


@pytest.mark.parametrize("ledgers, want", [
    # one folder from two ledgers
    ([("CONSIGN-20990101", "client: K99\n", [dict(item_id="C001", inventory_folder=F)]),
      ("CONSIGN-K98", "", [dict(item_id="K98-C001", inventory_folder=F)])],
     "inventory/b/one is claimed by K99-C001, K98-C001"),
    # ...unless it is one client's lot, across its old and new ledger
    ([("CONSIGN-20990101", "client: K99\n",
       [dict(item_id="C001", inventory_folder=F, lot_id="L1")]),
      ("CONSIGN-K99", "", [dict(item_id="K99-C002", inventory_folder=F, lot_id="L1")])],
     None),
    # two clients can't share a lot
    ([("CONSIGN-20990101", "client: K99\n",
       [dict(item_id="C001", inventory_folder=F, lot_id="L1")]),
      ("CONSIGN-K98", "", [dict(item_id="K98-C001", inventory_folder=F, lot_id="L1")])],
     "claimed by"),
    # a returned row no longer claims its folder
    ([("CONSIGN-20990101", "client: K99\n",
       [dict(item_id="C001", inventory_folder=F, state="RETURNED")]),
      ("CONSIGN-K98", "", [dict(item_id="K98-C001", inventory_folder=F)])], None),
    # one item id in a client's old and new ledger
    ([("CONSIGN-20990101", "client: K99\n", [dict(item_id="C001")]),
      ("CONSIGN-K99", "", [dict(item_id="K99-C001")])],
     "K99-C001 is in CONSIGN-20990101 and CONSIGN-K99"),
    # the folder name and context.txt disagree
    ([("CONSIGN-K99", "client: K98\n", [dict(item_id="K98-C001")])],
     "CONSIGN-K99: context.txt says client K98"),
])
def test_validate_across_ledgers(shop, ledgers, want):
    for name, ctx, rows in ledgers:
        _ledger(shop, name, rows, ctx)
    errs = consign.validate_all(consign.all_intakes())
    if want is None:
        assert errs == []
    else:
        assert any(want in e for e in errs), errs


def test_sync_refuses_a_cross_ledger_clash(shop, monkeypatch, capsys):
    monkeypatch.setattr(consign.offsite, "data_root", lambda start=None: shop)
    a = _ledger(shop, "CONSIGN-20990101", [dict(item_id="C001", inventory_folder=F)],
                "client: K99\n")
    _ledger(shop, "CONSIGN-K98", [dict(item_id="K98-C001", inventory_folder=F)])
    before = (a / "client_ledger.csv").read_bytes()
    assert consign.main(["sync", "--apply"]) == 1
    assert "is claimed by" in capsys.readouterr().out
    assert (a / "client_ledger.csv").read_bytes() == before


def test_split_is_locked_at_the_sale(shop):
    folder = _sold_item(shop, "b/sold", "dddd0001", OLD, "4-4-1")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder},
                       {"item_id": "C002", "inventory_folder": _folder(shop, "b/unsold")}])
    for split in ("0.5", "0.6"):                 # net 14.20; the 0.5 at the sale holds
        reg = {"K99": {"client_code": "K99", "split": split, "handling_fee": "2.00"}}
        assert consign.sync_intake(d, consign.Books(), reg, apply=True, today=TODAY) == 0
        got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
        assert (got["K99-C001"]["split_locked"], got["K99-C001"]["client_share"]) == (
            "0.5", "7.10")
        assert got["K99-C002"]["split_locked"] == ""


@pytest.mark.parametrize("loss_rule, share", [
    ("", "-1.25"), ("split", "-1.25"), ("client_never_owes", "0.00")])
def test_loss_rule(loss_rule, share):
    terms = consign.client_terms({"K99": {"split": "0.5", "loss_rule": loss_rule}}, "K99")
    r = _row(item_price="5.00", net_before_postage="3.00", ad_fee="1.00",
             actual_postage="4.50")
    consign.settle_money(r, terms["split"], terms["loss_rule"])
    assert (r["net"], r["client_share"]) == ("-2.50", share)


@pytest.mark.parametrize("field, value, want", [
    ("loss_rule", "client_pays", "loss_rule"),
    ("w9_on_file", "maybe", "w9_on_file"),
    ("payout_method", "acct 0012345678", "last 4 only"),
    ("agreement_signed", "last week", "agreement_signed"),
    ("handling_fee", "two", "handling_fee"),
    ("handling_fee", "-1.00", "below zero"),
])
def test_registry_fields_are_checked(field, value, want):
    with pytest.raises(SystemExit, match=want):
        consign.client_terms({"K99": {field: value}}, "K99")
    consign.client_terms({"K99": {"payout_method": "bank 1234"}}, "K99")


def test_settle_writes_once_and_a_paid_settlement_settles_the_rows(shop, capsys):
    folder = _sold_item(shop, "b/sold", "eeee0001", OLD, "5-5-1")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder},
                       {"item_id": "C002", "state": "EXCLUDED", "buyout_price": "12.50"},
                       {"item_id": "C003", "state": "EXCLUDED", "buyout_price": "0.00"}])
    fee = {"K99": {"client_code": "K99", "handling_fee": "2.00"}}
    assert consign.sync_intake(d, consign.Books(), fee, apply=True, today=TODAY) == 0
    through = ["settle", "K99", "--through", TODAY.isoformat()]
    assert consign.main(through) == 0                       # dry run writes nothing
    assert not (shop / "reports" / "consign").exists() and not consign.read_settlements("K99")

    assert consign.main(through + ["--apply"]) == 0
    stmt = shop / "reports" / "consign" / "K99" / "K99-S001.csv"
    s = consign.read_settlements("K99")
    assert [(x["settlement_id"], x["item_ids"], x["net"], x["client_share"], x["paid_date"])
            for x in s] == [("K99-S001", "K99-C001 K99-C002", "14.20", "19.60", "")]
    assert s[0]["handling"] == "2.00"
    lines = list(csv.DictReader(stmt.open(encoding="utf-8")))
    assert [(x["line"], x["item_id"], x["handling"], x["amount"]) for x in lines] == [
        ("sale", "K99-C001", "2.00", "7.10"), ("fee", "K99-C001", "", ""),
        ("fee", "K99-C001", "", ""), ("fee", "K99-C001", "", ""),
        ("buyout", "K99-C002", "", "12.50"), ("total", "", "2.00", "19.60")]
    assert [(x["fee_type"], x["fee_amount"], x["fee_rate"], x["rate_source"])
            for x in lines if x["line"] == "fee"] == [
        ("FINAL_VALUE_FEE", "2.90", "11.6%", "DERIVED (fee / $25.00 base)"),
        ("FINAL_VALUE_FEE_FIXED_PER_ORDER", "0.40", "", "FLAT (fixed fee: amount only)"),
        ("AD", "1.00", "4.0%", "DERIVED (fee / $25.00 FVF base)")]
    # A fee row leaves ebay_fee and ad_fee blank, so summing either column
    # over the statement's lines gives the sale's own totals, counted once.
    total = next(x for x in lines if x["line"] == "total")
    for col, want in (("ebay_fee", "3.30"), ("ad_fee", "1.00")):
        summed = sum((Decimal(x[col]) for x in lines if x["line"] != "total" and x[col]),
                     Decimal(0))
        assert summed == Decimal(want) == Decimal(total[col])
    first = stmt.read_bytes()

    # Everything owed is on the open settlement now.
    assert consign.main(through + ["--apply"]) == 0
    assert "nothing to settle" in capsys.readouterr().out
    # A statement is never overwritten, even if settlements.csv lost its row.
    saved = consign.read_settlements("K99")
    consign.settlements_path("K99").unlink()
    with pytest.raises(SystemExit, match="never overwritten"):
        consign.main(through + ["--apply"])
    assert stmt.read_bytes() == first

    # The accountant records the payment; the next sync settles the rows.
    saved[0].update(paid_date="2026-11-28", amount_paid="19.60")
    _csv(consign.settlements_path("K99"), consign.SETTLEMENT_COLUMNS, saved)
    assert consign.sync_intake(d, consign.Books(), fee, apply=True, today=TODAY) == 0
    got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
    for iid, state in (("K99-C001", "SETTLED"), ("K99-C002", "EXCLUDED")):
        assert (got[iid]["state"], got[iid]["paid_to_client_date"],
                got[iid]["settlement_id"]) == (state, "2026-11-28", "K99-S001")
    assert got["K99-C003"]["paid_to_client_date"] == ""      # $0 buyout: nothing to pay
    capsys.readouterr()
    assert consign.report([d], "K99", False, today=TODAY) == 0
    out = capsys.readouterr().out
    assert "paid in 2026          $19.60" in out
    assert "buyouts owed          $0.00" in out
    assert "client share owed     $0.00" in out


def test_settle_carries_an_adjustment_once(shop, capsys):
    # Paid 9.00 before; the books now say 8.10.
    folder = _sold_item(shop, "b/sold", "ffff0001", OLD, "6-6-1")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder,
                        "client_share": "9.00", "paid_to_client_date": "2026-11-20"}])
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    note = consign.read_ledger(d / "client_ledger.csv")[0][0]["state_note"]
    assert "adjust by $-0.90" in note
    through = ["settle", "K99", "--through", TODAY.isoformat(), "--apply"]
    assert consign.main(through) == 0
    assert consign.read_settlements("K99")[0]["adjustments"] == "K99-C001=-0.90"
    assert consign.main(through) == 0
    assert "nothing to settle" in capsys.readouterr().out
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    row = consign.read_ledger(d / "client_ledger.csv")[0][0]
    assert (row["client_share"], row["state_note"]) == ("9.00", "")


def test_a_zero_buyout_closes_with_nothing_owed(shop, capsys):
    _intake(shop, [{"item_id": "C001", "state": "EXCLUDED", "buyout_price": "0.00"}])
    assert consign.main(["report"]) == 0
    assert "(EXCLUDED, Russell buys, not paid: 0)" in capsys.readouterr().out
    assert consign.main(["settle", "K99", "--through", TODAY.isoformat(), "--apply"]) == 0
    assert "nothing to settle" in capsys.readouterr().out
    assert not consign.settlements_path("K99").exists()


def test_clearing_waits_for_the_later_of_sale_and_delivery(shop):
    ago = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
    # (item, sold days ago, delivered days ago, state, cleared_on, "no delivery" flag)
    table = [
        ("C001", 50, None, "CLEARED", ago(10), True),        # sold + 40
        ("C002", 30, None, "SOLD", "", True),
        ("C003", 50, 25, "SOLD", "", False),                 # delivered + 30 still ahead
        ("C004", 50, 35, "CLEARED", ago(5), False),          # delivered + 30 is later
        ("C005", 50, 45, "CLEARED", ago(10), False),         # sold + 40 is later
    ]
    rows = []
    for n, (iid, sold, delivered, *_rest) in enumerate(table, start=1):
        folder = _sold_item(shop, f"b/{iid}", f"gggg{n:04d}", ago(sold), f"7-7-{n}")
        rows.append({"item_id": iid, "inventory_folder": folder,
                     "delivered_at": ago(delivered) if delivered else ""})
    d = _intake(shop, rows)
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
    for iid, _, _, state, cleared, flagged in table:
        r = got["K99-" + iid]
        assert (r["state"], r["cleared_on"]) == (state, cleared), iid
        assert ("no delivery date" in r["state_note"]) is flagged, iid


@pytest.mark.parametrize("draft_format, sold_format, flagged", [
    ("", "", False), ("AUCTION", "", True), ("", "AUCTION", True),
    ("FIXED_PRICE", "FIXED_PRICE", False)])
def test_an_auction_listing_is_flagged(shop, draft_format, sold_format, flagged):
    folder = _sold_item(shop, "b/auction", "hhhh0001", NEW, "8-8-1", sold_format=sold_format)
    draft = shop / folder / "draft.md"
    draft.write_text(draft.read_text(encoding="utf-8").replace(
        "---\n\nbody", f'format: "{draft_format}"\n---\n\nbody'), encoding="utf-8")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder}])
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    note = consign.read_ledger(d / "client_ledger.csv")[0][0]["state_note"]
    assert ("auction format" in note) is flagged


@pytest.mark.parametrize("context, intake", [
    ("kind: event\nacquired: 2098-12-30\nclient: K99\n", "2098-12-30"),
    ("kind: event\nclient: K99\n", "2099-01-01"),             # the folder's date
])
def test_an_older_ledger_loads_and_migrates_on_write(shop, capsys, context, intake):
    old_cols = consign.COLUMNS[:consign.COLUMNS.index("paid_to_client_date") + 1]
    d = _ledger(shop, "CONSIGN-20990101",
                [dict(item_id="C001", description="Tin cup"),
                 dict(item_id="C002", description="Brass bell", state="EXCLUDED",
                      buyout_price="3.00")], context, header=old_cols)
    assert consign.main(["report", "--client", "K99", "--csv"]) == 0
    assert "buyouts owed          $3.00" in capsys.readouterr().out
    export = next((shop / "reports" / "consign" / "K99").glob("K99-*.csv"))
    rows = list(csv.DictReader(export.open(encoding="utf-8")))
    assert [(x["item_id"], x["intake"]) for x in rows] == [
        ("K99-C001", intake), ("K99-C002", intake)]
    assert consign.read_ledger(d / "client_ledger.csv")[0][0]["item_id"] == "C001"

    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    with (d / "client_ledger.csv").open(encoding="utf-8") as f:
        assert next(csv.reader(f)) == consign.COLUMNS
    rows = consign.read_ledger(d / "client_ledger.csv")[0]
    assert [(r["item_id"], r["client_code"], r["intake"]) for r in rows] == [
        ("K99-C001", "K99", intake), ("K99-C002", "K99", intake)]


def test_import_numbers_per_client_across_its_ledgers(shop):
    _ledger(shop, "CONSIGN-20990101", [dict(item_id="C001"), dict(item_id="C007")],
            "client: K99\n")
    roster = shop / "roster.csv"
    roster.write_text("description,bin\nWood spool,03\n", encoding="utf-8")
    assert consign.main(["import", "CONSIGN-K99", str(roster), "--intake", "2099-03-01"]) == 0
    rows = consign.read_ledger(shop / "inventory" / "CONSIGN-K99" / "client_ledger.csv")[0]
    assert [(r["item_id"], r["client_code"], r["intake"]) for r in rows] == [
        ("K99-C008", "K99", "2099-03-01")]
    assert not (shop / "inventory" / "CONSIGN-K99" / "context.txt").exists()
    with pytest.raises(SystemExit, match="already belongs to client K99"):
        consign.main(["import", "CONSIGN-K99", str(roster), "--client", "K98"])


def test_a_paid_sale_put_behind_a_lead_is_refused_not_paid_twice(shop, monkeypatch, capsys):
    # TD's reproduction: C005 sold and was paid; putting it in a lot behind
    # C001 would hand the sale to C001 as CLEARED and unpaid.
    monkeypatch.setattr(consign.offsite, "data_root", lambda start=None: shop)
    folder = _sold_item(shop, "b/paid", "jjjj0005", OLD, "1-1-5")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder}])
    _csv(d / "client_ledger.csv", consign.COLUMNS, [
        _row(item_id="K99-C001", inventory_folder=folder, lot_id="L1", state="PREP"),
        _row(item_id="K99-C005", inventory_folder=folder, lot_id="L1", state="SETTLED",
             sku="jjjj0005", order_id="1-1-5", client_share="8.10",
             settlement_id="K99-S001", paid_to_client_date="2026-11-20")])
    before = (d / "client_ledger.csv").read_bytes()
    assert consign.main(["sync", "--apply"]) == 1
    assert "make the sold row the lowest item_id in the lot" in capsys.readouterr().out
    assert (d / "client_ledger.csv").read_bytes() == before
    assert not list(d.glob("client_ledger.backup-*.csv"))


def test_settle_blocks_only_on_its_own_clients_errors(shop, capsys):
    # K98's two ledgers clash with each other; K99's payout isn't held up by it.
    _ledger(shop, "CONSIGN-K98", [dict(item_id="K98-C001", inventory_folder=F)])
    _ledger(shop, "CONSIGN-20990303", [dict(item_id="C002", inventory_folder=F)],
            "client: K98\n")
    _intake(shop, [{"item_id": "C001", "state": "EXCLUDED", "buyout_price": "4.00"}])
    through = ["settle", "K99", "--through", TODAY.isoformat()]
    assert consign.main(through) == 0
    out = capsys.readouterr().out
    assert "! inventory/b/one is claimed by" in out and "(not K99's" in out
    assert "amount due $4.00" in out
    assert consign.main(["settle", "K98", "--through", TODAY.isoformat()]) == 1
    assert "[X] inventory/b/one is claimed by" in capsys.readouterr().out


@pytest.mark.parametrize("reg, typed, paid, want", [
    ({}, "", "", "0.00"),                                   # opt-in: blank is no fee
    ({"handling_fee": "2.00"}, "", "", "2.00"),             # the agreement's fee
    ({"handling_fee": "1.00"}, "", "", "1.00"),             # the client's own fee
    ({"handling_fee": "0"}, "", "", "0.00"),                # waived
    ({}, "6.50", "", "6.50"),                               # oversize box, typed by hand
    ({}, "", "2026-11-28", ""),                             # paid before the fee: untouched
])
def test_handling_is_stamped_at_the_sale_and_kept(shop, reg, typed, paid, want):
    folder = _sold_item(shop, "b/sold", "ffff0001", OLD, "6-6-1")
    row = {"item_id": "C001", "inventory_folder": folder, "handling": typed}
    if paid:
        row.update(order_id="6-6-1", paid_to_client_date=paid, client_share="8.10")
    d = _intake(shop, [row, {"item_id": "C002", "inventory_folder": _folder(shop, "b/unsold")}])
    registry = {"K99": {"client_code": "K99", **reg}}
    for fee in (None, "3.00"):                   # a later change to the fee doesn't move it
        if fee:
            registry["K99"]["handling_fee"] = fee
        assert consign.sync_intake(d, consign.Books(), registry, apply=True, today=TODAY) == 0
        got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
        assert got["K99-C001"]["handling"] == want
        assert got["K99-C002"]["handling"] == ""              # unsold: no fee
    if not paid:                                 # net 16.20 before handling, split 0.5
        net = Decimal("16.20") - Decimal(want)
        assert got["K99-C001"]["net"] == consign.money(net)
        assert got["K99-C001"]["client_share"] == consign.money(net / 2)
    else:
        assert got["K99-C001"]["client_share"] == "8.10"      # frozen, no adjustment
        assert "adjust" not in got["K99-C001"]["state_note"]


# --------------------------------------------------------------------------- #
# Set B terms (#286) and the antique-mall booth
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("card, fee, net, share, complete", [
    # net = 100 - fee - 2.00 handling
    ("0.00", "10.00", "88.00", "44.00", "yes"),     # cash: 10% commission only
    ("100.00", "13.00", "85.00", "42.50", "yes"),   # all on a card: + 3%
    ("40.00", "11.20", "86.80", "43.40", "yes"),    # split payment: 3% of the card part
    ("", "", "", "", "no"),                         # not typed yet: unknown, not cash
])
def test_mall_fee_is_worked_out_with_no_postage(card, fee, net, share, complete):
    r = _row(channel="mall", item_price="100.00", card_paid=card, handling="2.00",
             actual_postage="")
    consign.settle_money(r, Decimal("0.5"))
    assert (r["ebay_fee"], r["net"], r["client_share"], r["actuals_complete"]) == (
        fee, net, share, complete)
    assert (r["actual_postage"], r["handling"], r["ad_fee"]) == ("0.00", "2.00", "0.00")


PAID_IN = (TODAY - timedelta(days=35)).isoformat()      # funds received 35 days ago


def test_a_sale_typed_by_hand_is_kept_and_clears_at_month_end_once_paid(shop):
    d = _intake(shop, [
        {"item_id": "C001", "channel": "mall", "sold_at": OLD, "item_price": "100.00",
         "card_paid": "0.00", "funds_received": PAID_IN},
        {"item_id": "C002", "channel": "dealer", "sold_at": OLD, "item_price": "50.00",
         "ebay_fee": "5.00", "actual_postage": "0.00", "order_id": "INV-7",
         "funds_received": PAID_IN},
        {"item_id": "C003", "channel": "dealer", "sold_at": NEW, "item_price": "50.00"},
        {"item_id": "C004", "channel": "mall", "sold_at": NEW, "item_price": "30.00"},
        # money all typed but not received yet: stays SOLD, however old the sale
        {"item_id": "C005", "channel": "dealer", "sold_at": OLD, "item_price": "50.00",
         "ebay_fee": "5.00", "actual_postage": "0.00"},
        # sold and paid today: clears when this month ends, not before
        {"item_id": "C006", "channel": "mall", "sold_at": TODAY.isoformat(),
         "item_price": "20.00", "card_paid": "0.00", "funds_received": TODAY.isoformat()}])
    fee = {"K99": {"client_code": "K99", "handling_fee": "2.00"}}
    for _ in range(2):                           # nothing typed is overwritten or lost
        assert consign.sync_intake(d, consign.Books(), fee, apply=True, today=TODAY) == 0
        got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
        mall, dealer = got["K99-C001"], got["K99-C002"]
        cleared = consign.month_end(date.fromisoformat(OLD)).isoformat()  # month of sale
        assert (mall["state"], mall["cleared_on"], mall["ebay_fee"], mall["handling"],
                mall["client_share"]) == ("CLEARED", cleared, "10.00", "2.00", "44.00")
        assert (dealer["state"], dealer["order_id"], dealer["net"], dealer["handling"]) == (
            "CLEARED", "INV-7", "43.00", "2.00")  # every channel pays handling
        assert "no longer in the sales ledger" not in dealer["state_note"]
        assert got["K99-C003"]["state"] == "SOLD"
        assert "dealer sale: type item_price, ebay_fee" in got["K99-C003"]["state_note"]
        assert "mall sale: type item_price and card_paid" in got["K99-C004"]["state_note"]
        assert got["K99-C005"]["state"] == "SOLD"
        assert "dealer sale: type funds_received so it can clear" in got["K99-C005"]["state_note"]
        assert (got["K99-C006"]["state"], got["K99-C006"]["cleared_on"]) == ("SOLD", "")
        assert "funds_received" not in mall["state_note"]


def test_an_ebay_sale_on_a_channel_row_is_flagged_not_taken(shop):
    folder = _sold_item(shop, "b/sold", "gggg0001", OLD, "7-7-1")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder, "channel": "mall",
                        "sold_at": OLD, "item_price": "40.00", "card_paid": "0.00"}])
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    r = consign.read_ledger(d / "client_ledger.csv")[0][0]
    assert (r["order_id"], r["item_price"]) == ("", "40.00")
    assert "eBay sale 7-7-1 matches this item: one item sells once" in r["state_note"]


def test_a_mall_placement_is_listed_and_its_gam_sku_never_matched_on_ebay(shop, capsys):
    placed = (TODAY - timedelta(days=10)).isoformat()
    long_ago = (TODAY - timedelta(days=200)).isoformat()
    # eBay ledger rows that carry the GAM SKU as a listing id: matching them
    # would flag an ended listing and an eBay sale on a mall row.
    _csv(shop / "listings_ledger.csv", ["sku", "status", "price", "listing_id", "ended_at"],
         [{"sku": "zzzz0001", "status": "ENDED", "price": "99.00", "listing_id": "TW963H",
           "ended_at": OLD}])
    _csv(shop / "sales_ledger.csv", SALES_HEADER, [_sale("5-5-1", "zzzz0001", OLD,
                                                         listing_id="TW963H")])
    d = _intake(shop, [
        {"item_id": "C001", "state": "PREP", "channel": "mall", "ask": "12.00",
         "listing_id": "TW963H", "placed_at": placed, "split_locked": "0.625"},
        # tagged but not yet on the floor: stays PREP
        {"item_id": "C002", "state": "PREP", "channel": "mall", "ask": "9.99"},
        # on the floor past listing_days
        {"item_id": "C003", "state": "PREP", "channel": "mall", "ask": "20.00",
         "listing_id": "4Q9BEV", "placed_at": long_ago},
        # placed, then sold at the booth
        {"item_id": "C004", "channel": "mall", "ask": "30.00", "listing_id": "38AKZ8",
         "placed_at": long_ago, "sold_at": NEW, "item_price": "30.00", "card_paid": "0.00"}])
    for _ in range(2):                           # a second sync keeps it on the floor
        assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
        got = {r["item_id"]: r for r in consign.read_ledger(d / "client_ledger.csv")[0]}
        on_floor = got["K99-C001"]
        assert (on_floor["state"], on_floor["listing_id"], on_floor["store"],
                on_floor["ask"], on_floor["order_id"], on_floor["state_note"]) == (
            "LISTED", "TW963H", "", "12.00", "", "")
        assert got["K99-C002"]["state"] == "PREP"
        assert got["K99-C003"]["state"] == "LISTED"
        assert (f"listing window over: end it and notify client (live since {long_ago}"
                in got["K99-C003"]["state_note"])
        assert (got["K99-C004"]["state"], got["K99-C004"]["listing_id"]) == ("SOLD", "38AKZ8")
        assert "listing window" not in got["K99-C004"]["state_note"]

    capsys.readouterr()
    assert consign.main(["report", "--client", "K99"]) == 0
    out = capsys.readouterr().out
    # C001 + C003, the booth tags: no estimate, so nothing is quoted, and the
    # tags show only as the list price
    assert "listed     2  est $0.00-$0.00  (list price $32.00)  (no estimate 2)" in out
    assert "on hand    1 unit(s)" in out              # C002 only


def test_a_listed_row_is_quoted_at_its_estimate_not_its_ask():
    # The client is quoted Conservative-Recommended; the Push-high ask is only
    # the list price and never enters the quoted value.
    base = {k: "" for k in consign.COLUMNS}
    rows = [dict(base, item_id="K01-C001", state="LISTED", ask="99.00",
                 est_low="40.00", est_high="60.00"),
            dict(base, item_id="K01-C002", state="LISTED", ask="30.00", est_high="20.00"),
            dict(base, item_id="K01-C003", state="LISTED", ask="50.00")]
    out = consign.client_summary([("i", r) for r in rows], Decimal("0.6"), today=TODAY)
    listed = next(ln for ln in out if ln.strip().startswith("listed"))
    assert listed.split("listed", 1)[1].split() == [
        "3", "est", "$60.00-$80.00", "(list", "price", "$179.00)", "(no", "estimate", "1)"]


@pytest.mark.parametrize("row, state, flag", [
    # its listing ended: no ended flag, and the ended row fills in nothing
    (dict(sku="hhhh0001", store="", listing_id="", ask="18.00"), "HOLD", None),
    # a live listing matches: still HOLD, flagged
    (dict(sku="hhhh0002"), "HOLD", "on hold but listing 222 is live: end it, or "
                                   "clear the hold"),
    (dict(channel="mall", listing_id="TW963H", placed_at=NEW), "HOLD",
     "on hold but listing TW963H is live"),
    # a sale ends the hold
    (dict(sku="hhhh0004"), "SOLD", None),
    # a folder and a draft don't move it
    (dict(inventory_folder="draft"), "HOLD", None),
])
def test_hold_stays_on_hand_until_a_sale(shop, row, state, flag):
    _csv(shop / "listings_ledger.csv", ["sku", "status", "price", "listing_id"],
         [{"sku": "hhhh0002", "status": "PUBLISHED", "price": "30.00", "listing_id": "222"},
          {"sku": "hhhh0004", "status": "SOLD", "price": "30.00", "listing_id": "444"}])
    _csv(shop / "listings_ledger-junk.csv",
         ["sku", "status", "price", "listing_id", "ended_at"],
         [{"sku": "hhhh0001", "status": "ENDED", "price": "9.99", "listing_id": "111",
           "ended_at": OLD}])
    _csv(shop / "sales_ledger.csv", SALES_HEADER, [_sale("4-4-4", "hhhh0004", NEW)])
    if row.get("inventory_folder"):
        row["inventory_folder"] = _folder(shop, "b/held", sku="hhhh0005")
    r = _row(item_id="K99-C001", state="HOLD", state_note="re-photograph first", **row)
    typed = {k: r[k] for k in ("store", "listing_id", "ask")}

    flags = consign.sync_row(r, consign.Books(), Decimal("0.5"), TODAY)

    assert r["state"] == state
    held = [f for f in flags if "on hold" in f or "ended" in f]
    if flag is None:
        assert held == [], flags
    else:
        assert len(held) == 1 and flag in held[0], flags
    if state == "HOLD":
        assert r["state_note"].startswith("re-photograph first")
    if row.get("sku") == "hhhh0001":
        assert (flags, {k: r[k] for k in typed}) == ([], typed)


@pytest.mark.parametrize("row, want", [
    (dict(card_paid="5.00", item_price="20.00"), "card_paid is for a mall sale only"),
    (dict(channel="mall", card_paid="25.00", item_price="20.00"), "between 0.00 and item_price"),
    (dict(channel="mall", card_paid="-1.00", item_price="20.00"), "between 0.00 and item_price"),
    (dict(channel="the mall"), "is not one word"),
    (dict(channel="mall", card_paid="20.00", item_price="20.00"), None),
    (dict(placed_at="2026-10-25", listing_id="206653314165"), "placed_at is for an off-eBay"),
    (dict(channel="mall", placed_at="2026-10-25"), "placed_at needs the mall item id"),
    (dict(channel="mall", placed_at="Oct 25", listing_id="TW963H"), "not a YYYY-MM-DD"),
    (dict(channel="mall", placed_at="2026-10-25", listing_id="TW963H"), None),
])
def test_validate_channel_and_card_paid(row, want):
    errs = consign.validate([_row(item_id="K99-C001", **row)], [], "K99")
    assert (want is None and not errs) or any(want in e for e in errs), errs


@pytest.mark.parametrize("net, rule, share", [
    ("-2.50", "client_bears_loss", "-2.50"),    # the whole loss, not half
    ("10.00", "client_bears_loss", "5.00"),     # a profit still splits
    ("-2.50", "split", "-1.25"),
    ("-2.50", "client_never_owes", "0.00"),
])
def test_loss_rules_share(net, rule, share):
    assert consign.money(consign.share_of(Decimal(net), Decimal("0.5"), rule)) == share


@pytest.mark.parametrize("field, value, want", [
    ("min_payout", "-5", "min_payout"),
    ("min_payout", "lots", "min_payout"),
    ("listing_days", "six months", "listing_days"),
    ("pickup_days", "0", "pickup_days"),
])
def test_set_b_registry_fields_are_checked(field, value, want):
    with pytest.raises(SystemExit, match=want):
        consign.client_terms({"K99": {field: value}}, "K99")
    terms = consign.client_terms({"K99": {"min_payout": "500", "loss_rule":
                                          "client_bears_loss"}}, "K99")
    assert (terms["min_payout"], terms["listing_days"], terms["pickup_days"],
            terms["loss_rule"]) == (Decimal("500"), 180, 30, "client_bears_loss")


def _listed(shop, rel, sku, price, published_at=""):
    folder = _folder(shop, rel, sku=sku)
    p = shop / "listings_ledger.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []
    _csv(p, ["sku", "status", "price", "published_at"],
         rows + [{"sku": sku, "status": "PUBLISHED", "price": price,
                  "published_at": published_at}])
    return folder


def test_set_b_flags_floors_listing_window_and_pickup(shop):
    long_ago = (TODAY - timedelta(days=200)).isoformat()
    recent = (TODAY - timedelta(days=10)).isoformat()
    sold = _sold_item(shop, "b/sold", "hhhh0004", OLD, "8-8-1")  # rewrites the ledger: first
    d = _intake(shop, [
        # ask under the floor, live 200 days
        {"item_id": "C001", "inventory_folder": _listed(shop, "b/low", "hhhh0001", "24.00",
                                                       long_ago), "floor": "30.00"},
        # a lot's floor is its rows' total: 10 + 10 = 20, ask 24 is fine
        {"item_id": "C002", "lot_id": "L1", "floor": "10.00",
         "inventory_folder": _listed(shop, "b/lot", "hhhh0002", "24.00", recent)},
        {"item_id": "C003", "lot_id": "L1", "floor": "10.00"},
        # sold under its floor (the sale is 20.00)
        {"item_id": "C004", "inventory_folder": sold, "floor": "25.00"},
        # told to pick it up 40 days ago; 30 days to do it
        {"item_id": "C005", "return_notice_sent": (TODAY - timedelta(days=40)).isoformat()},
        # notice sent, still inside the window
        {"item_id": "C006", "return_notice_sent": recent}])
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    note = {r["item_id"]: r["state_note"]
            for r in consign.read_ledger(d / "client_ledger.csv")[0]}
    assert "ask $24.00 is below the client's floor $30.00" in note["K99-C001"]
    assert "listing window over: end it and notify client" in note["K99-C001"]
    assert "floor" not in note["K99-C002"] and "window" not in note["K99-C002"]
    assert "sold at $20.00, below the client's floor $25.00" in note["K99-C004"]
    assert "pickup deadline passed" in note["K99-C005"]
    assert "pickup" not in note["K99-C006"]


def test_settle_holds_a_balance_under_min_payout_until_final(shop, capsys):
    folder = _sold_item(shop, "b/sold", "iiii0001", OLD, "9-9-1")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder}])
    reg = shop / "inventory" / "_consign"
    reg.mkdir()
    _csv(reg / "clients.csv", consign.REGISTRY_COLUMNS,
         [{"client_code": "K99", "split": "0.5", "min_payout": "500", "handling_fee": "2.00"}])
    assert consign.sync_intake(d, consign.Books(), consign.load_registry(), apply=True,
                               today=TODAY) == 0
    through = ["settle", "K99", "--through", TODAY.isoformat(), "--apply"]
    assert consign.main(through) == 0
    assert "under the $500.00 minimum payout" in capsys.readouterr().out
    assert not consign.read_settlements("K99") and not (shop / "reports" / "consign").exists()

    assert consign.main(through + ["--final"]) == 0       # the agreement has ended
    assert "FINAL" in capsys.readouterr().out
    s = consign.read_settlements("K99")
    assert [(x["item_ids"], x["client_share"]) for x in s] == [("K99-C001", "7.10")]


def test_a_buyout_is_paid_under_min_payout_and_the_sales_carry(shop, capsys):
    folder = _sold_item(shop, "b/sold", "jjjj0001", OLD, "10-10-1")
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder},
                       {"item_id": "C002", "state": "EXCLUDED", "buyout_price": "60.00"}])
    reg = shop / "inventory" / "_consign"
    reg.mkdir()
    _csv(reg / "clients.csv", consign.REGISTRY_COLUMNS,
         [{"client_code": "K99", "split": "0.5", "min_payout": "500"}])
    assert consign.sync_intake(d, consign.Books(), consign.load_registry(), apply=True,
                               today=TODAY) == 0
    through = ["settle", "K99", "--through", TODAY.isoformat(), "--apply"]
    assert consign.main(through) == 0                       # $68.10 owed, under $500
    assert "buyout(s) are paid now" in capsys.readouterr().out
    s = consign.read_settlements("K99")
    assert [(x["item_ids"], x["client_share"]) for x in s] == [("K99-C002", "60.00")]
    assert consign.main(through) == 0                       # the sale still waits
    assert "carried forward, nothing written" in capsys.readouterr().out
    assert len(consign.read_settlements("K99")) == 1


def test_client_report_and_lot_account_are_plain_ascii(shop, capsys, monkeypatch):
    # The R2 share link serves text/plain with no charset and the lists also go
    # to lp, so the report is ASCII even when the ledger text is not.
    import report_lot as RL
    d = _intake(shop, [
        {"item_id": "C001", "description": "Petit Modèle hen — 7.3° “boxed”", "qty": "—",
         "est_low": "10.00", "est_high": "20.00"},
        {"item_id": "C002", "state": "EXCLUDED", "buyout_price": "5.00", "bin": "—",
         "description": "Stapler · gray"},
        {"item_id": "C003", "state": "PREP", "state_note": f"{consign.FLAG} check it"},
    ])
    assert consign.main(["report", "--csv"]) == 0
    out = capsys.readouterr().out
    assert out.isascii(), [c for c in out if not c.isascii()]
    assert "by state: INTAKE 1 | PREP 1 | EXCLUDED 1" in out
    assert "est $10.00-$20.00" in out
    export = next((shop / "reports").rglob("K99-*.csv")).read_bytes()
    assert export.isascii() and b"Petit Modele hen -- 7.3 deg" in export

    monkeypatch.setattr(RL, "OUT_DIR", shop / "reports" / "lots")
    text, rows = RL.consign_account(d, "K99", TODAY)
    txt, csv_path = RL.write(d, text, rows, TODAY)
    body = txt.read_bytes()
    assert body.isascii() and csv_path.read_bytes().isascii()
    assert b"-- see `consign sync`" in body
    line = next(ln for ln in body.decode().splitlines() if ln.startswith("K99-C001"))
    header = next(ln for ln in body.decode().splitlines() if ln.startswith("#"))
    assert line.index("$10.00-20.00") == header.index("EST / ASK")   # columns still line up


# --------------------------------------------------------------------------
# fee breakdown: fee_breakdown is the one renderer behind all three outputs
# --------------------------------------------------------------------------
AS_OF = "2026-10-25T19:12:31+00:00"
SOURCED_BID = "SOURCED (eBay campaign bid, read 2026-10-25)"
DERIVED_AD = "DERIVED (fee / $40.00 FVF base)"


@pytest.mark.parametrize("bid, rate, source, flagged", [
    ("", "8.0%", DERIVED_AD, False),                 # no bid on file: derived only
    ("8.0", "8.0%", SOURCED_BID, False),
    ("8.1", "8.1%", SOURCED_BID, False),             # 0.1 points apart: not flagged
    ("7.5", "7.5% / 8.0%", f"{SOURCED_BID} / {DERIVED_AD}", True),
])
def test_fee_breakdown_labels_each_rate_by_its_source(bid, rate, source, flagged):
    lines = [_fl("7-7-1", "kkkk0001", "FINAL_VALUE_FEE", "5.00", "40.00"),
             _fl("7-7-1", "kkkk0001", "FINAL_VALUE_FEE_FIXED_PER_ORDER", "0.40", "40.00"),
             _fl("7-7-1", "kkkk0001", "AD", "3.20", bid_pct=bid,
                 bid_pct_as_of=AS_OF if bid else ""),
             _fl("8-8-8", "other001", "FINAL_VALUE_FEE", "9.99", "50.00")]   # another sale
    r = _row(state="CLEARED", order_id="7-7-1", sku="kkkk0001", item_price="40.00",
             ebay_fee="5.40", ad_fee="3.20")
    bd = consign.fee_breakdown(r, lines)
    assert bd["status"] == "OK"
    fvf, fixed, ad = bd["lines"]
    assert (fvf["rate"], fvf["source"]) == ("12.5%", "DERIVED (fee / $40.00 base)")
    assert (fixed["rate"], fixed["source"]) == ("", "FLAT (fixed fee: amount only)")
    assert (ad["rate"], ad["source"], ad["flag"]) == (rate, source, flagged)
    text = "\n".join(consign.fee_text(bd))
    assert "eBay's rate" not in text
    assert "AD" in text and "selling fee, taken off gross before the split" in text
    assert ("FLAG: SOURCED and DERIVED differ" in text) == flagged


@pytest.mark.parametrize("fees, status", [
    ([], consign.FEES_MISSING),
    ([("FINAL_VALUE_FEE", "2.90"), ("FINAL_VALUE_FEE_FIXED_PER_ORDER", "0.40")],
     consign.FEES_DONT_TIE),                         # the $1.00 ad fee has no line
    ([("FINAL_VALUE_FEE", "2.80"), ("FINAL_VALUE_FEE_FIXED_PER_ORDER", "0.40"),
      ("AD", "1.00")], consign.FEES_DONT_TIE),       # 10 cents short of ebay_fee
])
def test_settle_holds_a_sale_whose_fee_lines_are_missing_or_dont_tie(shop, capsys,
                                                                     fees, status):
    folder = _sold_item(shop, "b/sold", "llll0001", OLD, "11-11-1")
    (shop / "reports" / "fee_lines.csv").unlink()
    _fee_lines(shop, [_fl("11-11-1", "llll0001", t, a, "25.00") for t, a in fees])
    d = _intake(shop, [{"item_id": "C001", "inventory_folder": folder}])
    assert consign.sync_intake(d, consign.Books(), {}, apply=True, today=TODAY) == 0
    assert consign.main(["settle", "K99", "--through", TODAY.isoformat(), "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"held K99-C001: {status}" in out and "nothing to settle" in out
    assert not consign.read_settlements("K99")
    assert consign.main(["report", "--csv"]) == 0
    assert status in capsys.readouterr().out
    export = list(csv.DictReader(next((shop / "reports" / "consign").rglob("K99-*.csv"))
                                 .open(encoding="utf-8")))
    assert [x["rate_source"] for x in export if x["line"] == "fee"][-1] == status


def test_mall_fee_rates_are_the_constants_cited_to_the_agreement():
    r = _row(state="CLEARED", channel="mall", item_price="100.00", card_paid="40.00",
             ebay_fee="11.20")
    bd = consign.fee_breakdown(r, [])
    cite = "SOURCED (consignment agreement sec. 4.2(b))"
    assert bd["status"] == "OK"
    assert [(x["fee_type"], _amt(x), _base(x), x["rate"], x["source"])
            for x in bd["lines"]] == [
        ("MALL_COMMISSION", "10.00", "100.00", "10.0%", cite),
        ("MALL_CARD_FEE", "1.20", "40.00", "3.0%", cite)]
    # a ledger fee worked out at other rates doesn't tie, and settle holds it
    assert consign.fee_breakdown(dict(r, ebay_fee="13.00"), [])["status"] == \
        consign.FEES_DONT_TIE
    # card_paid unknown: the fee as an amount, no %
    bd = consign.fee_breakdown(dict(r, card_paid=""), [])
    assert [(x["fee_type"], _amt(x), x["rate"]) for x in bd["lines"]] == [
        ("MALL_FEE", "11.20", "")]


def test_agreement_states_the_mall_rates_the_constants_hold():
    # The breakdown labels the mall % from MALL_COMMISSION / MALL_CARD_RATE.
    # If the agreement is reworded or its rates change, this fails, so the
    # constants and the contract are changed together.
    import re
    text = (Path(__file__).resolve().parents[1] / "docs" / "client-onboarding"
            / "consignment-agreement.md").read_text(encoding="utf-8")
    para = re.search(r"^\(b\) for a sale at our antique-mall booth.*$", text, re.M)
    assert para, "agreement sec. 4.2(b) mall paragraph not found"
    com = re.search(r"commission of (\d+(?:\.\d+)?)%", para.group(0))
    card = re.search(r"card fee of (\d+(?:\.\d+)?)%", para.group(0))
    assert com and card, "sec. 4.2(b) no longer states a commission % and a card fee %"
    assert Decimal(com.group(1)) / 100 == consign.MALL_COMMISSION
    assert Decimal(card.group(1)) / 100 == consign.MALL_CARD_RATE


def _amt(x):
    return consign.money(x["amount"])


def _base(x):
    return consign.money(x["base"])
