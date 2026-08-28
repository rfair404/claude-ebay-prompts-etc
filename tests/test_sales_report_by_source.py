"""sales_report — bucket ROI panel (#56).

`by_source()` is the whole point of the issue: cost basis and ROI per
context.txt-owned bucket, not per however-many-slashes-are-in-the-path. No
network, no real ledger — everything is built under tmp_path so it never
touches the gitignored business-data files this repo keeps locally.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import dir_context as dc                                          # noqa: E402
import sales_report as sr                                         # noqa: E402


def _row(shoot: str, gross: float, net: float) -> dict:
    return {"shoot": shoot, "gross": gross, "net": net}


def _inventory(tmp_path: Path) -> Path:
    root = tmp_path / "inventory"
    (root / "ESTATES/SCJ").mkdir(parents=True)
    (root / "ESTATES/SCJ/context.txt").write_text("kind: event\nspend: 575\n",
                                                   encoding="utf-8")
    (root / "ESTATES/MAR").mkdir(parents=True)
    (root / "ESTATES/MAR/context.txt").write_text("kind: event\n", encoding="utf-8")
    (root / "FREE").mkdir(parents=True)
    (root / "FREE/context.txt").write_text("kind: channel\n", encoding="utf-8")
    (root / "ESTATES/GIFT").mkdir(parents=True)
    (root / "ESTATES/GIFT/context.txt").write_text("kind: event\nspend: FREE\n",
                                                    encoding="utf-8")
    return root


def test_by_source_computes_cost_profit_roi(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "REPO", tmp_path)
    monkeypatch.setattr(dc, "INVENTORY", _inventory(tmp_path))

    rows = [
        _row("inventory/ESTATES/SCJ/silver/tray-1", 200, 180),
        _row("inventory/ESTATES/SCJ/silver/tray-2", 100, 90),
        _row("inventory/FREE/more-mags-444/lot-1", 50, 45),
        _row("inventory/ESTATES/MAR/item-1", 80, 70),
        _row("", 30, 25),
        _row("inventory/ESTATES/SCJ/_prepped/staged-item", 999, 999),
    ]
    out = sr.by_source(rows)
    by_bucket = {b["bucket"]: b for b in out["buckets"]}

    scj = by_bucket["ESTATES/SCJ"]
    assert scj["n"] == 2 and scj["gross"] == 300 and scj["net"] == 270
    assert scj["kind"] == "event" and scj["spend"] == 575 and not scj["basis_gap"]
    assert scj["profit"] == 270 - 575
    assert scj["roi"] == 270 / 575

    mar = by_bucket["ESTATES/MAR"]
    assert mar["kind"] == "event" and mar["spend"] is None and mar["basis_gap"]
    assert mar["profit"] is None and mar["roi"] is None

    free = by_bucket["FREE"]
    assert free["kind"] == "channel" and not free["basis_gap"]
    assert free["roi"] is None and free["profit"] is None       # never divide a channel

    # buckets ordered by gross, descending
    assert [b["bucket"] for b in out["buckets"]] == ["ESTATES/SCJ", "ESTATES/MAR", "FREE"]

    assert out["unattributed"] == {"n": 1, "gross": 30, "net": 25}
    assert out["excluded_backup"] == 1                            # never counted anywhere
    total = 300 + 80 + 50 + 30
    assert out["unattributed_pct"] == 30 / total * 100


def test_zero_spend_event_bucket_suppresses_roi_not_a_gap(tmp_path, monkeypatch):
    """spend: FREE on an event bucket is a real, recorded $0 basis — not a gap,
    and not a divide-by-zero: ROI is undefined, not infinite."""
    monkeypatch.setattr(sr, "REPO", tmp_path)
    root = tmp_path / "inventory" / "ESTATES" / "GIFT"
    root.mkdir(parents=True)
    (root / "context.txt").write_text("kind: event\nspend: FREE\n", encoding="utf-8")
    monkeypatch.setattr(dc, "INVENTORY", tmp_path / "inventory")

    out = sr.by_source([_row("inventory/ESTATES/GIFT/item-1", 50, 45)])
    b = out["buckets"][0]
    assert b["spend"] == 0.0 and not b["basis_gap"]
    assert b["profit"] == 45 and b["roi"] is None


def test_by_source_reorg_does_not_change_grain(tmp_path, monkeypatch):
    """A sub-lot without its own context.txt still rolls up to its bucket."""
    monkeypatch.setattr(sr, "REPO", tmp_path)
    root = _inventory(tmp_path)
    monkeypatch.setattr(dc, "INVENTORY", root)

    rows = [_row("inventory/FREE/more-mags-444/deep/nested/lot-9", 40, 35)]
    out = sr.by_source(rows)
    assert len(out["buckets"]) == 1
    assert out["buckets"][0]["bucket"] == "FREE"


def test_print_by_source_smoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sr, "REPO", tmp_path)
    monkeypatch.setattr(dc, "INVENTORY", _inventory(tmp_path))
    rows = [_row("inventory/ESTATES/SCJ/tray-1", 200, 180)]
    sr.print_by_source(sr.by_source(rows))
    out = capsys.readouterr().out
    assert "ESTATES/SCJ" in out and "unattributed" in out


def test_dashboard_renders_by_source_panel(tmp_path, monkeypatch):
    """Full gather() -> draw() wiring, from CSVs on disk to the HTML panel."""
    monkeypatch.setattr(sr, "REPO", tmp_path)
    monkeypatch.setattr(sr, "ADS_JSON", tmp_path / "reports" / "ebay_ads.json")
    monkeypatch.setattr(dc, "INVENTORY", _inventory(tmp_path))

    with (tmp_path / "sales_ledger.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "sold_at", "title", "listing_id", "shoot_dir", "gross", "ebay_fee",
            "net_before_postage", "listed_price", "pct_of_ask"])
        w.writeheader()
        w.writerow({"sold_at": "2026-07-01T00:00:00Z", "title": "Silver tray",
                    "listing_id": "111", "shoot_dir": "inventory/ESTATES/SCJ/silver/tray-1",
                    "gross": "200", "ebay_fee": "20", "net_before_postage": "180",
                    "listed_price": "220", "pct_of_ask": "91"})
        w.writerow({"sold_at": "2026-07-02T00:00:00Z", "title": "Gifted vase",
                    "listing_id": "222", "shoot_dir": "inventory/ESTATES/GIFT/item-1",
                    "gross": "50", "ebay_fee": "5", "net_before_postage": "45",
                    "listed_price": "60", "pct_of_ask": "83"})

    d = sr.gather(days=365)
    html = sr.draw(d)                                   # must not raise
    assert "Realised by source" in html
    assert "ESTATES/SCJ" in html
    assert "spend $575.00" in html                     # SCJ's recorded basis, not a gap
    assert "NET BEFORE POSTAGE" in html.upper()
    assert "ESTATES/GIFT" in html and "spend $0.00" in html    # $0 basis, still recorded
