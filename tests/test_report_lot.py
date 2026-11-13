import re
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


@pytest.fixture(autouse=True)
def _no_ebay(monkeypatch):
    # no test asks eBay for photos unless it says so
    monkeypatch.setattr(RL, "_ebay_photo_url", lambda row: "")


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
    money = text.split("LOT MONEY")[1].split("INVENTORY LEDGER")[0]
    assert re.search(r"Net kept \(actuals in\) +105\.00 ", money)
    assert re.search(r"Recovered vs paid +-770\.00", money)
    assert "note: mixed-source" in text.split("MEMBERS")[1]


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


def test_html_turns_the_ledger_into_a_table_and_keeps_the_figures(estate, monkeypatch):
    listings = [{"sku": "s2", "path": "inventory/ES/BUY1/candlesticks/draft.md",
                 "price": "1250", "published_at": "2026-10-23", "listing_id": "206",
                 "title": "Pair of <Brass> Candlesticks, PHOTOS ONLY lookalike"}]
    monkeypatch.setattr(RL, "_all_listings", lambda: listings)
    monkeypatch.setattr(RL, "_all_sales", lambda: [])
    keep, left_out = RL.members(estate)
    items = RL.estate_items(estate, keep, left_out)
    page = RL.html_report(RL.estate_text(estate, keep, left_out, items, date(2026, 10, 26)))
    assert "<h2>OVERVIEW</h2>" in page and "<h2>INVENTORY LEDGER</h2>" in page
    ledger = page.split("<h2>INVENTORY LEDGER</h2>")[1].split("</table>")[0]
    assert ledger.count("<tr>") == len(items) + 1
    assert "<td class=t>Pair of &lt;Brass&gt; Candlesticks, PHOTOS ONLY lookalike</td>" in ledger
    assert '<td class=n>1,250.00</td>' in page and "<td>LIVE</td>" in page


def test_pdf_failure_leaves_text_csv_and_html(estate, monkeypatch, capsys):
    import subprocess
    monkeypatch.setattr(RL, "OUT_DIR", estate.parent.parent.parent / "reports" / "lots")
    monkeypatch.setattr(RL, "_all_listings", lambda: [])
    monkeypatch.setattr(RL, "_all_sales", lambda: [])
    monkeypatch.setattr(RL.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="no chromium"))
    assert RL.main(["ES/BUY1"]) == 0
    out = capsys.readouterr()
    wrote = next(ln for ln in out.out.splitlines() if ln.startswith("WROTE"))
    assert wrote.count(".txt") == wrote.count(".csv") == wrote.count(".html") == 1
    assert ".pdf" not in wrote and "FAILED pdf: no chromium" in out.err


def test_consign_held_items_are_marked_and_listed(tmp_path, monkeypatch):
    import consign as C
    monkeypatch.setattr(C, "REPO", tmp_path)
    monkeypatch.setattr(C, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(C, "load_registry", lambda: {"K01": {"name": "Client"}})
    monkeypatch.setattr(C, "read_settlements", lambda code: [])
    monkeypatch.setattr(C, "load_fee_lines", lambda: [])
    lot = _dir(tmp_path / "inventory", "CONSIGN-20261022", "client: K01\n")
    base = {k: "" for k in C.COLUMNS}
    rows = [dict(base, item_id="K01-C001", description="Gold chain", state="SHOT",
                 why="possible value [HOLD]: gold, needs recovery check"),
            dict(base, item_id="K01-C002", description="Plain cup", state="SHOT")]
    C.write_ledger(lot / C.LEDGER_NAME, rows)
    text, out = RL.consign_account(lot, "K01", date(2026, 11, 2))
    ledger, sec = text.split("INVENTORY LEDGER")[1].split("HELD BACK")
    assert "K01-C002" in ledger and "K01-C001" not in ledger
    # held rows come last, one short line each: id and title, no reason
    assert sec.strip().splitlines()[1:] == ["  K01-C001  Gold chain"]
    assert [(r["item_id"], r["hold"]) for r in out] == [("K01-C002", ""), ("K01-C001", "HOLD")]


def test_consign_case_items_show_available_locally_and_fb_flag(tmp_path, monkeypatch):
    import consign as C
    monkeypatch.setattr(C, "REPO", tmp_path)
    monkeypatch.setattr(C, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(C, "load_registry", lambda: {"K01": {"name": "Client"}})
    monkeypatch.setattr(C, "read_settlements", lambda code: [])
    monkeypatch.setattr(C, "load_fee_lines", lambda: [])
    lot = _dir(tmp_path / "inventory", "CONSIGN-20261022", "client: K01\n")
    base = {k: "" for k in C.COLUMNS}
    rows = [dict(base, item_id="K01-C001", description="Brass bell", state="LISTED", channel="mall",
                 listing_id="BAXW4F", placed_at="2026-10-25", ask="20.00", fb_promoted="2026-11-01"),
            dict(base, item_id="K01-C002", description="Tin sign", state="LISTED", listing_id="206", ask="30.00"),
            dict(base, item_id="K01-C003", description="Oak box", state="LISTED", channel="mall", ask="15.00")]
    C.write_ledger(lot / C.LEDGER_NAME, rows)
    text, out = RL.consign_account(lot, "K01", date(2026, 11, 3))
    line = {ln[:8]: ln for ln in text.splitlines() if ln.startswith("K01-C00")}
    assert "AVAILABLE LOCALLY" in line["K01-C001"] and line["K01-C001"].endswith("yes")
    # on eBay, or in the mall row without a GAM SKU and placed_at: plain LISTED, no FB
    for k in ("K01-C002", "K01-C003"):
        assert "AVAILABLE LOCALLY" not in line[k] and "LISTED" in line[k] and line[k].endswith("-")
    overview = text.split("OVERVIEW")[1].split("CLIENT MONEY")[0]
    assert re.search(r"Available locally +1 ", overview) and re.search(r"Listed on eBay +2 ", overview)
    assert "Russell" not in text
    # listed on eBay sorts ahead of available locally
    assert [(r["item_id"], r["available_locally"], r["fb_promoted"]) for r in out] == [
        ("K01-C002", "", ""), ("K01-C003", "", ""), ("K01-C001", "yes", "2026-11-01")]


def test_page_shows_the_draft_title_whole_and_a_thumbnail_per_item(estate, monkeypatch, tmp_path):
    # The page names each item by its draft's title, uncut, even when the
    # ledger kept an older one. An item with no local photo gets its first
    # eBay photo from the draft's meta.ebay_image_urls.
    from PIL import Image
    long = "Pair of antique brass candlesticks with hand chased bases and removable bobeches"
    (estate / "candlesticks" / "draft.md").write_text(f"---\ntitle: {long}\n---\nbody\n", encoding="utf-8")
    (estate / "tools" / "chainsaw" / "draft.md").write_text(
        "---\ntitle: Chainsaw\nmeta:\n  ebay_image_urls:\n"
        "  - \"https://i.ebayimg.com/00/s/x/z/abc/$_12.JPG?set_id=1\"\n---\nbody\n", encoding="utf-8")
    monkeypatch.setattr(RL, "_all_listings", lambda: [
        {"sku": "s2", "path": "inventory/ES/BUY1/candlesticks/draft.md", "price": "127",
         "published_at": "2026-10-23", "listing_id": "206", "title": "Old ledger title"}])
    monkeypatch.setattr(RL, "_all_sales", lambda: [])
    Image.new("RGB", (400, 300), "red").save(estate / "candlesticks" / "a.jpg")
    remote = tmp_path / "remote.jpg"
    Image.new("RGB", (300, 225), "blue").save(remote)
    asked = []
    monkeypatch.setattr(RL, "_fetched", lambda url: asked.append(url) or (remote if url else None))
    keep, left_out = RL.members(estate)
    items = RL.estate_items(estate, keep, left_out)
    text = RL.estate_text(estate, keep, left_out, items, date(2026, 11, 2))
    assert long not in text and RL._short(long, 52) in text
    page = RL.html_report(text, [{k: ("" if v is None else v) for k, v in i.items()} for i in items])
    assert f"<td class=t>{long}</td>" in page and "Old ledger title" not in page
    assert [u for u in asked if u] == ["https://i.ebayimg.com/00/s/x/z/abc/$_12.JPG?set_id=1"]
    assert page.count('<img src="data:image/jpeg;base64,') == 2


def test_trash_folders_are_never_members(estate):
    inv = estate.parent.parent
    _dir(inv, "_trash/20261103/arrowhead", "spend: 0\npart_of: ES/BUY1\n")
    _dir(inv, "_trash/20261103/arrowhead/01-point")
    _dir(inv, "_rebuilt/abc123", "part_of: ES/BUY1\n")
    keep, _ = RL.members(estate)
    assert not any("_trash" in RL.label(d) for d in keep)
    assert any("_rebuilt/abc123" in RL.label(d) for d in keep)


def test_estate_account_reads_like_the_consignment_one(estate, monkeypatch):
    # Header, OVERVIEW and LOT MONEY tables first, then the ledger, and the
    # long MEMBERS list last. A mall draft counts as available locally.
    (estate / "buckle" / "draft.md").write_text(
        "---\ntitle: Brass buckle\nmeta:\n  channel: mall\n---\nbody\n", encoding="utf-8")
    listings = [
        {"sku": "s1", "path": "inventory/ES/BUY1/magazines/issue-01/draft.md", "price": "35",
         "published_at": "2026-10-24", "listing_id": "880", "status": "PUBLISHED", "title": "Issue 1"},
        {"sku": "s2", "path": "inventory/ES/BUY1/candlesticks/draft.md", "price": "127",
         "published_at": "2026-10-23", "listing_id": "206", "title": "Candlesticks"},
        {"sku": "s3", "path": "inventory/ES/BUY1/buckle/draft.md", "price": "20",
         "drafted_at": "2026-10-23", "title": "Brass buckle"},
    ]
    sales = [{"sku": "s2", "listing_id": "206", "gross": "140", "net_before_postage": "120",
              "ad_fee": "", "actual_postage": ""}]
    monkeypatch.setattr(RL, "_all_listings", lambda: listings)
    monkeypatch.setattr(RL, "_all_sales", lambda: sales)
    keep, left_out = RL.members(estate)
    items = RL.estate_items(estate, keep, left_out)
    text = RL.estate_text(estate, keep, left_out, items, date(2026, 11, 9))
    lines = text.splitlines()
    assert lines[0] == "LOT ACCOUNT - ES/BUY1   estate   (as of 2026-11-09)"
    assert lines[1] == "Lot ES/BUY1" and lines[2] == "Acquired: 2026-10-21   Paid: $875.00"
    order = [text.index(h) for h in ("\nOVERVIEW\n", "\nLOT MONEY\n", "\nINVENTORY LEDGER\n", "\nMEMBERS")]
    assert order == sorted(order)
    overview = text.split("OVERVIEW")[1].split("LOT MONEY")[0]
    assert "GROUP" in overview and "LIST PRICE" in overview and "NET KEPT" in overview
    assert re.search(r"Sold +1 +- +140\.00 +0\.00", overview)
    assert re.search(r"Live on eBay +1 +35\.00 +- +-", overview)
    assert re.search(r"Available locally +1 +20\.00 ", overview)
    assert re.search(r"Not shot +1 +- +- +-", overview) and "Ended" not in overview
    money = text.split("LOT MONEY")[1].split("INVENTORY LEDGER")[0]
    assert re.search(r"Paid +875\.00 ", money)
    assert re.search(r"Sales with actuals pending +120\.00  1 sales", money)
    ledger = text.split("INVENTORY LEDGER")[1].split("MEMBERS")[0]
    assert [re.split(r" {2,}", ln)[2] for ln in ledger.splitlines()[3:6]] == ["SOLD", "LIVE", "AVAILABLE LOCALLY"]
    page = RL.html_report(text, [{k: ("" if v is None else v) for k, v in i.items()} for i in items])
    assert page.index("<h2>OVERVIEW</h2>") < page.index("<h2>LOT MONEY</h2>") < page.index("<h2>MEMBERS")
    assert page.count("<table>") == 3


def _consign_lot(tmp_path, monkeypatch, rows):
    import consign as C
    monkeypatch.setattr(C, "REPO", tmp_path)
    monkeypatch.setattr(C, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(C, "load_registry", lambda: {"K01": {"name": "Client Name"}})
    monkeypatch.setattr(C, "read_settlements", lambda code: [])
    monkeypatch.setattr(C, "load_fee_lines", lambda: [])
    lot = _dir(tmp_path / "inventory", "CONSIGN-20261022", "client: K01\n")
    base = {k: "" for k in C.COLUMNS}
    C.write_ledger(lot / C.LEDGER_NAME, [dict(base, **r) for r in rows])
    return lot


SORT_ROWS = [
    {"item_id": "K01-C001", "description": "Shot cup", "state": "SHOT", "est_low": "10.00"},
    {"item_id": "K01-C002", "description": "Drafted bowl", "state": "DRAFTED", "ask": "40.00"},
    {"item_id": "K01-C003", "description": "Booth bell", "state": "LISTED", "channel": "mall",
     "listing_id": "BAX", "placed_at": "2026-10-25", "ask": "20.00"},
    {"item_id": "K01-C004", "description": "Listed sign", "state": "LISTED", "listing_id": "206",
     "ask": "30.00"},
    {"item_id": "K01-C005", "description": "Sold saw", "state": "SOLD", "item_price": "75.00",
     "net": "60.00", "client_share": "30.00"},
    {"item_id": "K01-C006", "description": "Stapler", "state": "EXCLUDED", "buyout_price": "5.00",
     "qty": "in C006 line"},
    {"item_id": "K01-C007", "description": "Box of odds", "state": "INTAKE",
     "qty": "~10 (count at PRICE)", "bin": "bin-03"},
]


def test_consign_ledgers_sort_sold_listed_local_drafted_shot_then_the_rest(tmp_path, monkeypatch):
    # Russell 2026-11-11: sold first, then listed, available locally, drafted, shot, the rest last
    lot = _consign_lot(tmp_path, monkeypatch, list(reversed(SORT_ROWS)))
    want = ["K01-C005", "K01-C004", "K01-C003", "K01-C002", "K01-C001", "K01-C007", "K01-C006"]
    _, out = RL.consign_account(lot, "K01", date(2026, 11, 11))
    assert [r["item_id"] for r in out if r["line"] == "item"] == want
    _, client_rows = RL.consign_client(lot, "K01", date(2026, 11, 11))
    assert [r["item_id"] for r in client_rows] == want


def test_client_copy_is_one_table_with_ask_sold_and_credit(tmp_path, monkeypatch):
    # Russell 2026-11-12: one table with ask, sold and credited; "Kept"
    lot = _consign_lot(tmp_path, monkeypatch, SORT_ROWS)
    text, rows = RL.consign_client(lot, "K01", date(2026, 11, 11))
    assert text.startswith("Client Name: consignment report\n")
    assert "SUMMARY" not in text and text.count("YOUR ITEMS") == 1
    for word in ("split", "Split", "sync", "owed", "fee", "net", "estimate", "PRICE",
                 "LOT ACCOUNT", "LISTED", "EXCLUDED", "in C006", "Kept by", "handling"):
        assert word not in text, word
    hdr = next(ln for ln in text.splitlines() if ln.startswith("#"))
    assert re.search(r"STATUS +ASK +SOLD +CREDITED$", hdr)
    line = {ln[:8]: ln for ln in text.splitlines() if ln.startswith("K01-C00")}
    assert re.search(r"Sold +\$75 +\$30$", line["K01-C005"])          # sold, client_share
    assert re.search(r"Kept +\$5$", line["K01-C006"])                  # buyout is the credit
    assert re.search(r"At our booth +\$20$", line["K01-C003"])          # ask only
    pend = [dict(SORT_ROWS[4], item_id="K01-C009", client_share="", net="")]
    text2, _ = RL.consign_client(_consign_lot(tmp_path, monkeypatch, pend), "K01", date(2026, 11, 11))
    assert re.search(r"Sold +\$75 +pending$", text2.splitlines()[7])
    assert RL._whole("-1.03") == "-$1.03" and RL._whole("1234.50") == "$1,234.50"
    assert line["K01-C001"].endswith("Photographed")                    # estimate stays out
    assert "~10" in line["K01-C007"] and "bin-03" in line["K01-C007"]
    total = next(ln for ln in text.splitlines() if ln.startswith("TOTAL"))
    assert re.search(r"\$90 +\$75 +\$35$", total)
    page = RL.html_report(text, rows, client=True)
    assert page.count("<table>") == 1 and "<th class=n>CREDITED</th>" in page


def test_every_copy_opens_with_the_lot_code_and_address(tmp_path, monkeypatch):
    # Russell 2026-11-12: lot code and property address at the top; read from
    # the lot's context.txt, left out when it has no address line
    lot = _consign_lot(tmp_path, monkeypatch, SORT_ROWS)
    assert RL.consign_client(lot, "K01", date(2026, 11, 11))[0].splitlines()[1] == "Lot K01"
    (lot / "context.txt").write_text("client: K01\naddress: 12 Example Lane\n", encoding="utf-8")
    text, rows = RL.consign_client(lot, "K01", date(2026, 11, 11))
    account, _ = RL.consign_account(lot, "K01", date(2026, 11, 11))
    assert text.splitlines()[1] == account.splitlines()[1] == "Lot K01 \u00b7 12 Example Lane"
    assert "Lot K01 \u00b7 12 Example Lane" in RL.html_report(text, rows, client=True)


def test_estate_heading_uses_its_label_and_any_address(estate, monkeypatch):
    assert RL.lot_heading(estate) == "Lot ES/BUY1"
    (estate / "context.txt").write_text("kind: event\naddress: 9 Sample Road\n", encoding="utf-8")
    assert RL.lot_heading(estate) == "Lot ES/BUY1 \u00b7 9 Sample Road"


def test_consign_run_shares_the_client_copy_and_keeps_the_account_aside(tmp_path, monkeypatch, capsys):
    lot = _consign_lot(tmp_path, monkeypatch, SORT_ROWS)
    monkeypatch.setattr(RL, "REPO", tmp_path)
    monkeypatch.setattr(RL, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(RL, "OUT_DIR", tmp_path / "reports" / "lots")
    monkeypatch.setattr(RL, "render_pdf", lambda page, pdf: "no chromium in tests")
    assert RL.main([str(lot)]) == 0
    out = capsys.readouterr().out
    assert "CLIENT MONEY" in out                       # the session still sees the money
    stem = tmp_path / "reports" / "lots" / f"CONSIGN-20261022-{date.today().isoformat()}"
    shared = stem.with_suffix(".txt").read_text() + stem.with_suffix(".html").read_text()
    assert "consignment report" in shared and "CLIENT MONEY" not in shared and "split" not in shared
    assert "CLIENT MONEY" in stem.with_name(stem.name + "-account.txt").read_text()


def test_thumbnail_falls_back_to_a_named_frame_or_its_review_crop(tmp_path, monkeypatch):
    from PIL import Image
    inv = tmp_path / "inventory"
    (inv / "_review" / "crops").mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(inv / "_review" / "crops" / "ZZ220246_t.jpg")
    Image.new("RGB", (10, 10)).save(inv / "ZZ220250.JPG")
    monkeypatch.setattr(RL, "INVENTORY", inv)
    monkeypatch.setattr(RL, "REPO", tmp_path)
    pics = RL._thumbs([{"photos": "ZZ220246"}, {"photos": "ZZ220250.JPG"}, {"photos": "ZZ999999"},
                       {"listing_id": "206656147219"}])
    # a frame named with or without its extension, or its crop; eBay is stubbed out
    assert [bool(p) for p in pics] == [True, True, False, False]


def test_ebay_photo_url_asks_ebay_once_per_item(tmp_path, monkeypatch):
    # A sold item with no draft left still has its photos on eBay: the
    # inventory item by SKU, else GetItem by listing id (a Live lot has no
    # SKU). The URL is cached so a rerun doesn't ask again.
    import types
    monkeypatch.undo()
    monkeypatch.setattr(RL, "_URL_CACHE", tmp_path / "ebay_urls.json")
    monkeypatch.setattr(RL, "_EBAY_DOWN", False)
    calls = []

    class Missing(Exception):
        status = 404

    def api_send(method, path, creds=None, marketplace=None):
        calls.append((path, creds))
        if path.endswith("/gone"):
            raise Missing("HTTP 404")
        return {"product": {"imageUrls": ["https://i.ebayimg.com/a/$_12.JPG", "https://i.ebayimg.com/b"]}}

    def pictures(lid, creds):
        calls.append((lid, creds))
        return ["https://i.ebayimg.com/live/$_12.JPG"] if lid == "206" else []

    fake = types.SimpleNamespace(api_send=api_send, get_item_picture_urls=pictures,
                                 load_credentials=lambda store: f"creds:{store}")
    monkeypatch.setitem(sys.modules, "ebay_client", fake)
    row = {"sku": "eb5df699", "store": "main"}
    assert RL._ebay_photo_url(row) == "https://i.ebayimg.com/a/$_12.JPG"
    assert RL._ebay_photo_url(row) == "https://i.ebayimg.com/a/$_12.JPG"
    assert RL._ebay_photo_url({"sku": "", "listing_id": "206", "store": "default"}) \
        == "https://i.ebayimg.com/live/$_12.JPG"
    assert RL._ebay_photo_url({"sku": "gone", "listing_id": "880", "store": "junk"}) == ""
    assert RL._ebay_photo_url({"sku": "", "store": "junk"}) == ""
    assert RL._ebay_photo_url({"sku": "", "listing_id": "4Q9BEV", "store": "default"}) == ""
    assert calls == [("/sell/inventory/v1/inventory_item/eb5df699", "creds:default"),
                     ("206", "creds:default"),
                     ("/sell/inventory/v1/inventory_item/gone", "creds:junk"),
                     ("880", "creds:junk")]


def test_all_puts_every_lot_on_one_page_and_names_a_failed_one(estate, monkeypatch, capsys):
    _dir(estate.parent.parent, "CONSIGN-BAD", "client: BAD\n")
    out_dir = estate.parent.parent.parent / "reports" / "lots"
    monkeypatch.setattr(RL, "OUT_DIR", out_dir)
    monkeypatch.setattr(RL, "_all_listings", lambda: [])
    monkeypatch.setattr(RL, "_all_sales", lambda: [])

    def broken(*a):
        raise ValueError("ledger missing")
    monkeypatch.setattr(RL, "consign_account", broken)
    assert RL.main(["--all"]) == 1
    page = next(out_dir.glob("lots-*.html")).read_text(encoding="utf-8")
    # consignments first, one picker option and one section per lot
    assert re.findall(r'<option value="([^"]+)"', page) == ["CONSIGN-BAD", "ES_BUY1"]
    assert re.findall(r'<section class="lot" id="([^"]+)"', page) == ["CONSIGN-BAD", "ES_BUY1"]
    assert "FAILED: ValueError(&#x27;ledger missing&#x27;)" in page
    assert "<h2>INVENTORY LEDGER</h2>" in page.split('id="ES_BUY1"')[1]
    assert "FAILED CONSIGN-BAD" in capsys.readouterr().err
