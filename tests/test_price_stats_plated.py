#!/usr/bin/env python3
"""Plated / filled / vermeil / gold-tone comps never reach the price stats.

2026-11-20: a CompsAPI pull for a solid 14K chain returned a $51 "14KGP"
and an $82 "14K Gold Filled" row. Plated metal is not solid metal.

Run:  pytest tests/test_price_stats_plated.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import price_stats as ps  # noqa: E402


def _row(title, price, i):
    return {"title": title, "sold_price": price, "total_price": price,
            "item_id": str(i), "condition": "used", "listing_type": "Buy It Now",
            "url": f"https://www.ebay.com/itm/{i}"}


def test_plated_rows_dropped_from_stats(tmp_path):
    rows = [_row("Vintage 24in 14KGP Gold Plated Herringbone Chain", 51, 1),
            _row("14K Gold Filled V Necklace Herringbone", 82, 2),
            _row("14k gold vermeil herringbone chain", 60, 3),
            _row("14k White Gold Rhodium Plated Herringbone Chain Italy", 900, 4),
            _row("Solid 14K White Gold Herringbone Chain Italy", 1000, 5),
            _row("Solid 14K White Gold Herringbone Necklace Italy", 1100, 6),
            _row("14K White Gold Herringbone Italy 20in", 1200, 7)]
    p = tmp_path / "run.json"
    p.write_text(json.dumps({"run_id": "t", "source": "t", "comps": rows}))
    rep = ps.price_from_runs(best_match_json=str(p), unit_type="single",
                             condition="used", require_tokens=["herringbone"])
    log = json.dumps(rep, default=str)
    assert "plated_not_solid" in log
    for bad in ("14KGP", "Gold Filled", "vermeil"):
        assert bad not in json.dumps(rep.get("kept_comps", rep.get("kept", [])), default=str)
    assert rep["distribution"]["median"] >= 900


def test_plated_item_keeps_plated_comps():
    c = ps.Comp(title="14K Gold Filled V Necklace", price=82, sold_price=82,
                total_price=82, url="u")
    ps.filter_plated([c], ["gold filled"])
    assert not c.dropped
    ps.filter_plated([c], ["necklace"])
    assert c.dropped
