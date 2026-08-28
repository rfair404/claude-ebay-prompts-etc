"""Tests for the same-day comp cache wired into lib/ebay_sold_browse.py
(V4_PLAN Phase 4, #30) — read_cache.py's generic primitive is covered
separately in tests/test_read_cache.py; this file covers the CLI-level
wiring: cache-miss populates, cache-hit skips the browse instructions,
`--fresh` bypasses and repopulates, and query/day keying.

No network, no Chrome, no claude-in-chrome MCP: `--ingest-json` reads a
plain fixture JSON file, exactly as it would a real EXTRACTOR_JS capture.
Every run writes its output under `--save-dir <tmp_path>`, never the real
`apify_runs/`, and every cache read/write is redirected via READ_CACHE_DIR.

Run:  pytest tests/test_ebay_sold_browse_cache.py
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import ebay_sold_browse as esb  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own cache dir — no test can see another's writes,
    and none can touch the real .cache/."""
    monkeypatch.setenv("READ_CACHE_DIR", str(tmp_path / ".cache"))
    return tmp_path


def _rows_file(tmp_path, name="rows.json", n=3, price=50.0):
    rows = [{"title": f"Item {i}", "sold_price": price + i, "url": f"https://x/{i}",
            "item_id": str(1000000000 + i)} for i in range(n)]
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["ebay_sold_browse.py"] + argv)
    esb._cli()


# ---------------------------------------------------------------------------
# Unit level: the two cache helper functions directly
# ---------------------------------------------------------------------------

def test_lookup_is_empty_before_anything_is_ingested():
    assert esb._comp_cache_lookup("gorham bowl", None) == {}


def test_record_then_lookup_round_trips():
    esb._comp_cache_record("gorham bowl", None, "best_match", 1, "run1.json", 5)
    got = esb._comp_cache_lookup("gorham bowl", None)
    assert got == {"best_match": {"page": 1, "n": 5, "path": "run1.json"}}


def test_record_accumulates_both_sorts_without_clobbering():
    esb._comp_cache_record("gorham bowl", None, "best_match", 1, "run1.json", 5)
    esb._comp_cache_record("gorham bowl", None, "price_high", 1, "run2.json", 4)
    got = esb._comp_cache_lookup("gorham bowl", None)
    assert set(got) == {"best_match", "price_high"}
    assert got["best_match"]["path"] == "run1.json"
    assert got["price_high"]["path"] == "run2.json"


def test_lookup_is_normalized_on_whitespace_and_case():
    esb._comp_cache_record("Gorham Bowl", None, "best_match", 1, "run1.json", 5)
    assert esb._comp_cache_lookup("  gorham bowl  ", None) != {}


def test_different_query_does_not_share_a_cache_entry():
    esb._comp_cache_record("gorham bowl", None, "best_match", 1, "run1.json", 5)
    assert esb._comp_cache_lookup("reed barton tray", None) == {}


def test_condition_partitions_the_cache():
    esb._comp_cache_record("gorham bowl", "used", "best_match", 1, "run1.json", 5)
    assert esb._comp_cache_lookup("gorham bowl", "new") == {}
    assert esb._comp_cache_lookup("gorham bowl", "used") != {}


def test_malformed_cache_file_on_disk_is_treated_as_a_miss(tmp_path, monkeypatch):
    import read_cache
    parts = esb._cache_parts("gorham bowl", None)
    key = read_cache.cache_key(esb._CACHE_NS, *parts)
    bad = Path(tmp_path / ".cache") / esb._CACHE_NS / f"{key}.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    assert esb._comp_cache_lookup("gorham bowl", None) == {}


# ---------------------------------------------------------------------------
# CLI level: miss -> browse instructions; ingest populates; hit skips browse
# ---------------------------------------------------------------------------

def test_default_action_with_no_cache_prints_browse_instructions(monkeypatch, capsys):
    _run_cli(monkeypatch, ["gorham sterling bowl"])
    out = capsys.readouterr().out
    assert "claude-in-chrome" in out
    assert "OK cache hit" not in out


def test_ingest_populates_cache_for_that_sort_only(monkeypatch, tmp_path, capsys):
    rows_path = _rows_file(tmp_path)
    _run_cli(monkeypatch, ["gorham sterling bowl", "--ingest-json", rows_path,
                          "--sort", "best_match", "--save-dir", str(tmp_path / "runs")])
    capsys.readouterr()  # drain
    # one sort only -> still a miss overall (PRICE needs both dual sorts)
    _run_cli(monkeypatch, ["gorham sterling bowl"])
    out = capsys.readouterr().out
    assert "OK cache hit" not in out
    assert "[cached: n=3" in out          # the covered sort is annotated
    assert "[price_high]" in out          # the missing sort still gets a URL


def test_both_sorts_ingested_then_default_action_is_a_cache_hit(monkeypatch, tmp_path, capsys):
    for sort in ("best_match", "price_high"):
        rows_path = _rows_file(tmp_path, name=f"{sort}.json")
        _run_cli(monkeypatch, ["gorham sterling bowl", "--ingest-json", rows_path,
                              "--sort", sort, "--save-dir", str(tmp_path / "runs")])
    capsys.readouterr()  # drain the two ingest prints

    _run_cli(monkeypatch, ["gorham sterling bowl"])
    out = capsys.readouterr().out
    assert "OK cache hit" in out
    assert "claude-in-chrome" not in out   # the live-browse path was skipped
    assert "best_match" in out and "price_high" in out


def test_fresh_bypasses_a_complete_cache_and_shows_live_instructions(monkeypatch, tmp_path, capsys):
    for sort in ("best_match", "price_high"):
        rows_path = _rows_file(tmp_path, name=f"{sort}.json")
        _run_cli(monkeypatch, ["gorham sterling bowl", "--ingest-json", rows_path,
                              "--sort", sort, "--save-dir", str(tmp_path / "runs")])
    capsys.readouterr()

    _run_cli(monkeypatch, ["gorham sterling bowl", "--fresh"])
    out = capsys.readouterr().out
    assert "OK cache hit" not in out
    assert "claude-in-chrome" in out


def test_fresh_ingest_afterward_repopulates_cache(monkeypatch, tmp_path, capsys):
    for sort in ("best_match", "price_high"):
        rows_path = _rows_file(tmp_path, name=f"{sort}.json", price=50.0)
        _run_cli(monkeypatch, ["gorham sterling bowl", "--ingest-json", rows_path,
                              "--sort", sort, "--save-dir", str(tmp_path / "runs")])
    capsys.readouterr()

    # --fresh re-browse, then a fresh ingest with a different n, overwrites
    # that sort's cached entry
    rows_path2 = _rows_file(tmp_path, name="fresh_best_match.json", n=7, price=99.0)
    _run_cli(monkeypatch, ["gorham sterling bowl", "--fresh", "--ingest-json", rows_path2,
                          "--sort", "best_match", "--save-dir", str(tmp_path / "runs")])
    capsys.readouterr()

    got = esb._comp_cache_lookup("gorham sterling bowl", None)
    assert got["best_match"]["n"] == 7
    assert got["price_high"]["n"] == 3  # untouched by the re-ingest of the other sort


def test_different_query_is_still_a_miss_after_another_querys_ingest(monkeypatch, tmp_path, capsys):
    for sort in ("best_match", "price_high"):
        rows_path = _rows_file(tmp_path, name=f"{sort}.json")
        _run_cli(monkeypatch, ["gorham sterling bowl", "--ingest-json", rows_path,
                              "--sort", sort, "--save-dir", str(tmp_path / "runs")])
    capsys.readouterr()

    _run_cli(monkeypatch, ["reed and barton tray"])
    out = capsys.readouterr().out
    assert "OK cache hit" not in out
    assert "claude-in-chrome" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
