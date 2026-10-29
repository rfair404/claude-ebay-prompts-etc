"""tools/report_lot.py — which folders make up a lot, each item's status and
the money summary. Fake folders in tmp_path; the ledgers are stubbed, so
nothing reads the real inventory/ or calls eBay."""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import report_lot as RL  # noqa: E402


def _dir(root: Path, rel: str, context: str = "", files=()) -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    if context:
        (d / "context.txt").write_text(context, encoding="utf-8")
    for f in files:
        (d / f).write_text("x", encoding="utf-8")
    return d


@pytest.fixture
def estate(tmp_path, monkeypatch):
    inv = tmp_path / "inventory"
    monkeypatch.setattr(RL, "REPO", tmp_path)
    monkeypatch.setattr(RL, "INVENTORY", inv)
    monkeypatch.setattr(RL.SR, "REPO", tmp_path)
    monkeypatch.setattr(RL.SR, "INVENTORY", inv)
    lot = _dir(inv, "ES/BUY1", "kind: event\nspend: 150\nacquired: 2026-10-21\n")
    _dir(inv, "ES/BUY1/candlesticks", files=["draft.md", "a.jpg"])
    _dir(inv, "ES/BUY1/buckle", files=["a.jpg"])
    _dir(inv, "ES/BUY1/magazines", "spend: 600\n")
    _dir(inv, "ES/BUY1/magazines/issue-01", files=["draft.md"])
    _dir(inv, "ES/BUY1/tools", "spend: 75\n")
    _dir(inv, "ES/BUY1/tools/chainsaw")
    _dir(inv, "ES/BUY1/walkthrough", "spend: 0\nclient: K01\n", files=[])
    _dir(inv, "ES/BUY1/walkthrough/01-lamp", files=["a.jpg"])
    _dir(inv, "ES/BUY1/_returned/pen-box", files=["a.jpg"])
    _dir(inv, "scrap", "spend: 50\npart_of: ES/BUY1\nnote: mixed-source\n", files=["a.jpg"])
    _dir(inv, "other-buy", "spend: 99\n", files=["a.jpg"])
    return lot


def test_members_take_nested_and_part_of_but_not_consignments(estate):
    keep, left_out = RL.members(estate)
    assert [RL.label(d) for d in keep] == ["ES/BUY1", "ES/BUY1/magazines", "ES/BUY1/tools", "scrap"]
    assert [(RL.label(d), why) for d, why in left_out] == [
        ("ES/BUY1/walkthrough", "consignment, client K01")]


def test_items_status_and_summary(estate, monkeypatch):
    listings = [
        # stale main-store draft and the live junk listing for the same SKU:
        # the live one wins
        {"sku": "s1", "path": "inventory/ES/BUY1/magazines/issue-01/draft.md",
         "price": "80", "drafted_at": "2026-10-22", "status": "DRAFTED", "_store": "default"},
        {"sku": "s1", "path": "inventory/ES/BUY1/magazines/issue-01/draft.md",
         "price": "35", "published_at": "2026-10-24", "listing_id": "880",
         "status": "PUBLISHED", "_store": "junk", "title": "Issue 1"},
        {"sku": "s2", "path": "inventory/ES/BUY1/candlesticks/draft.md", "price": "127",
         "published_at": "2026-10-23", "listing_id": "206", "title": "Candlesticks"},
    ]
    sales = [
        {"sku": "s2", "listing_id": "206", "shoot_dir": "", "sold_at": "2026-10-25T01:00Z",
         "gross": "140", "net_before_postage": "120", "ad_fee": "5", "actual_postage": "10"},
    ]
    monkeypatch.setattr(RL, "_all_listings", lambda: listings)
    monkeypatch.setattr(RL, "_all_sales", lambda: sales)
    keep, left_out = RL.members(estate)
    items = {i["folder"]: i for i in RL.estate_items(estate, keep, left_out)}

    assert set(items) == {"ES/BUY1/candlesticks", "ES/BUY1/buckle",
                          "ES/BUY1/magazines/issue-01", "ES/BUY1/tools/chainsaw", "scrap"}
    assert items["ES/BUY1/candlesticks"]["status"] == "SOLD"
    assert items["ES/BUY1/candlesticks"]["net_kept"] == 105
    assert items["ES/BUY1/magazines/issue-01"]["status"] == "LIVE"
    assert items["ES/BUY1/magazines/issue-01"]["ask"] == 35
    assert items["ES/BUY1/magazines/issue-01"]["store"] == "junk"
    assert items["ES/BUY1/buckle"]["status"] == "PHOTOS ONLY"
    assert items["ES/BUY1/tools/chainsaw"]["status"] == "NOT SHOT"

    text = RL.estate_text(estate, keep, left_out, list(items.values()), date(2026, 10, 26))
    assert "Paid: $875.00" in text
    assert "Net kept (actuals in):  $105.00" in text
    assert "Recovered vs paid:      $-770.00" in text
    assert "note: mixed-source" in text


def test_sale_without_actuals_is_pending_not_kept(estate, monkeypatch):
    listings = [{"sku": "s2", "path": "inventory/ES/BUY1/candlesticks/draft.md",
                 "price": "127", "published_at": "2026-10-23", "listing_id": "206"}]
    sales = [{"sku": "s2", "listing_id": "206", "gross": "140",
              "net_before_postage": "120", "ad_fee": "", "actual_postage": ""}]
    monkeypatch.setattr(RL, "_all_listings", lambda: listings)
    monkeypatch.setattr(RL, "_all_sales", lambda: sales)
    keep, left_out = RL.members(estate)
    item = next(i for i in RL.estate_items(estate, keep, left_out)
                if i["folder"] == "ES/BUY1/candlesticks")
    assert item["actuals_pending"] and item["net_kept"] is None


def test_failed_lp_is_reported_and_exits_nonzero(estate, monkeypatch, capsys):
    import subprocess
    monkeypatch.setattr(RL, "OUT_DIR", estate.parent.parent.parent / "reports" / "lots")
    monkeypatch.setattr(RL, "_all_listings", lambda: [])
    monkeypatch.setattr(RL, "_all_sales", lambda: [])
    monkeypatch.setattr(RL.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="lp: The printer or class does not exist."))
    assert RL.main(["ES/BUY1", "--print"]) == 1
    out = capsys.readouterr()
    assert "PRINTED" not in out.out
    assert "FAILED print on CannonBW: lp exited 1" in out.err


def test_written_account_is_plain_ascii(estate, monkeypatch):
    # Ledger titles carry accents and degree signs; the .txt prints on lp and
    # the R2 link serves it with no charset, so both files are ASCII.
    listings = [{"sku": "s2", "path": "inventory/ES/BUY1/candlesticks/draft.md",
                 "price": "127", "published_at": "2026-10-23", "listing_id": "206",
                 "title": "Heidi Caillard La Poule Petit Modèle 7.3° Hen — France"}]
    monkeypatch.setattr(RL, "_all_listings", lambda: listings)
    monkeypatch.setattr(RL, "_all_sales", lambda: [])
    monkeypatch.setattr(RL, "OUT_DIR", estate.parent.parent.parent / "reports" / "lots")
    keep, left_out = RL.members(estate)
    items = RL.estate_items(estate, keep, left_out)
    text = RL.estate_text(estate, keep, left_out, items, date(2026, 10, 26))
    txt, csv_path = RL.write(estate, text, items, date(2026, 10, 26))
    body, sheet = txt.read_text(encoding="ascii"), csv_path.read_bytes()
    assert sheet.isascii() and b"Petit Modele 7.3 deg Hen -- France" in sheet
    lines = body.splitlines()
    header = next(ln for ln in lines if ln.startswith("#"))
    row = next(ln for ln in lines if "Petit Modele" in ln)
    assert row.index("LIVE") == header.index("STATUS")


def test_consign_row_shows_estimate_until_listed(tmp_path, monkeypatch):
    # The client reads the account: an unsold row shows its estimate, not
    # the push-high ask it lists at; a listed row with no estimate shows its
    # ask labelled as the list price.
    import consign as C
    monkeypatch.setattr(C, "REPO", tmp_path)
    monkeypatch.setattr(C, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(C, "load_registry", lambda: {"K01": {"name": "Client"}})
    monkeypatch.setattr(C, "read_settlements", lambda code: [])
    monkeypatch.setattr(C, "load_fee_lines", lambda: [])
    lot = _dir(tmp_path / "inventory", "CONSIGN-20261022", "client: K01\n")
    base = {k: "" for k in C.COLUMNS}
    rows = [dict(base, item_id="K01-C033", description="Sterling set", state="PREP",
                 est_low="1682.55", est_high="3365.09", ask="5995"),
            dict(base, item_id="K01-C040", description="Listed cup", state="LISTED",
                 est_low="90.00", est_high="90.00", ask="145.00"),
            dict(base, item_id="K01-C041", description="Asked only", state="PREP",
                 ask="24.99"),
            dict(base, item_id="K01-C042", description="Listed, no estimate", state="LISTED",
                 ask="35.00")]
    C.write_ledger(lot / C.LEDGER_NAME, rows)
    text, _ = RL.consign_account(lot, "K01", date(2026, 10, 25))
    line = {r["item_id"]: next(ln for ln in text.splitlines() if ln.startswith(r["item_id"]))
            for r in rows}
    assert "$1682.55-3365.09" in line["K01-C033"] and "5995" not in line["K01-C033"]
    assert "$90.00-90.00" in line["K01-C040"] and "145" not in line["K01-C040"]
    assert "list $35.00" in line["K01-C042"]
    assert "ask $24.99" in line["K01-C041"]


def test_consign_account_shows_each_sales_fee_breakdown(tmp_path, monkeypatch):
    # report_lot's consignment path keeps the ledger to one line per item and
    # explains each sale's fees in a section after it (consign's own
    # renderer), in the text and in the CSV.
    import consign as C
    monkeypatch.setattr(C, "REPO", tmp_path)
    monkeypatch.setattr(C, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(C, "load_registry", lambda: {"K01": {"name": "Client"}})
    monkeypatch.setattr(C, "read_settlements", lambda code: [])
    line = {"order_id": "1-1-1", "line_item_id": "L1", "listing_id": "", "sku": "s1",
            "date": "2026-10-20"}
    monkeypatch.setattr(C, "load_fee_lines", lambda: [
        dict(line, fee_type="FINAL_VALUE_FEE", amount="5.00", basis="40.00"),
        dict(line, fee_type="AD", amount="3.20", basis="", bid_pct="8.0",
             bid_pct_as_of="2026-10-25T19:12:31+00:00")])
    lot = _dir(tmp_path / "inventory", "CONSIGN-20261022", "client: K01\n")
    base = {k: "" for k in C.COLUMNS}
    C.write_ledger(lot / C.LEDGER_NAME, [
        dict(base, item_id="K01-C050", description="Sold bowl", state="CLEARED",
             order_id="1-1-1", sku="s1", item_price="40.00", ebay_fee="5.00",
             ad_fee="3.20", net="27.80", client_share="13.90"),
        dict(base, item_id="K01-C051", description="Unsold cup", state="LISTED",
             ask="20.00")])
    text, rows = RL.consign_account(lot, "K01", date(2026, 10, 25))
    ledger, _, fees = text.partition("SOLD ITEM FEES")
    sale = ledger.splitlines()
    i = next(n for n, ln in enumerate(sale) if ln.startswith("K01-C050"))
    assert sale[i + 1].startswith("K01-C051")          # no fee lines in the ledger
    assert "fee FINAL_VALUE_FEE" not in ledger and "fee AD" not in ledger
    f = fees.splitlines()
    j = next(n for n, ln in enumerate(f) if ln.startswith("K01-C050"))
    assert "sold $40.00" in f[j + 1] and "net $27.80" in f[j + 1] and "client $13.90" in f[j + 1]
    assert "fee FINAL_VALUE_FEE" in f[j + 2] and "12.5% DERIVED (fee / $40.00 base)" in f[j + 2]
    assert "fee AD" in f[j + 3] and "8.0% SOURCED (eBay campaign bid, read 2026-10-25)" in f[j + 3]
    assert "taken off gross before the split" in f[j + 3]
    assert "K01-C051" not in fees                      # no breakdown for an unsold item
    assert [(r["line"], r["item_id"], r["fee_type"], r["fee_amount"], r["fee_rate"],
             r["ebay_fee"], r["ad_fee"]) for r in rows] == [
        ("item", "K01-C050", "", "", "", "5.00", "3.20"),
        ("item", "K01-C051", "", "", "", "", ""),
        ("fee", "K01-C050", "FINAL_VALUE_FEE", "5.00", "12.5%", "", ""),   # counted once
        ("fee", "K01-C050", "AD", "3.20", "8.0%", "", "")]
    monkeypatch.setattr(RL, "OUT_DIR", tmp_path / "reports" / "lots")
    _, csv_path = RL.write(lot, text, rows, date(2026, 10, 25))
    assert b"DERIVED (fee / $40.00 base)" in csv_path.read_bytes()
