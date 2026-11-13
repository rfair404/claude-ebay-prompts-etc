"""CompsAPI as PRICE's SECONDARY source: merged after the Chrome run.

Behaviour under test (compsapi_sold.py --with-chrome):
  * CompsAPI rows already in the Chrome run (same item_id) are dropped.
  * Both sources are cut to exact matches with price_stats' own filters;
    a CompsAPI row with no condition can't be verified and is dropped
    (live test: parts for a Pfaff 463 came back with condition null).
  * A used-up quota or missing key leaves the Chrome rows standing.
No network: the CompsAPI client is a fake opener.
"""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import compsapi_sold as cs  # noqa: E402
import price_stats  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "compsapi"


def _row(iid, title, price, cond=None, ship=0.0):
    return {"item_id": iid, "title": title, "sold_price": price,
            "total_price": price + ship, "shipping_cost": ship, "condition": cond,
            "url": f"https://www.ebay.com/itm/{iid}", "bo_accepted": False}


CHROME_RUN = {
    "run_id": "browse-best_match-p1-x", "queries": ["pfaff 463 sewing machine"],
    "comps": [
        _row("100000000001", "Pfaff 463 Industrial Sewing Machine Head", 400.0),
        _row("100000000002", "Pfaff 463 Sewing Machine with Table Motor", 450.0),
        _row("100000000003", "Pfaff 463 Sewing Machine Lot of 2", 700.0),
        _row("100000000004", "Pfaff 463 Sewing Machine Complete", 425.0),
    ],
}

API_ROWS = [
    _row("100000000002", "Pfaff 463 Sewing Machine with Table Motor", 450.0, "Used"),
    _row("200000000001", "Pfaff 463 Sewing Machine Head Only", 380.0, "Used"),
    _row("200000000002", "Pfaff 463 Bobbin Case Sewing Machine Part", 25.0, None),
    _row("200000000003", "Pfaff 463 Sewing Machine Serviced", 500.0, "Used"),
    _row("200000000004", "Pfaff 463 Sewing Machine NEW in crate", 900.0, "New"),
    _row("200000000005", "Pfaff 463 Handwheel", 30.0, "Used"),
]

TOKENS = ["pfaff", "463", "sewing machine"]


def _merge(api_rows=API_ROWS, note=None):
    return cs.merge_with_chrome(CHROME_RUN, api_rows, unit="single",
                                require_tokens=TOKENS, condition="used",
                                compsapi_note=note)


def _by_id(rows):
    return {r["item_id"]: r for r in rows}


def test_merge_dedupes_compsapi_against_chrome_and_tags_source():
    p = _merge()
    kept = _by_id(p["comps"])
    assert p["sources"]["compsapi"]["n_duplicate"] == 1
    assert kept["100000000002"]["comp_source"] == "chrome"      # Chrome copy wins
    assert sum(r["item_id"] == "100000000002" for r in p["comps"]) == 1
    assert kept["200000000001"]["comp_source"] == "compsapi"
    assert {r["comp_source"] for r in p["comps"]} == {"chrome", "compsapi"}


def test_exact_match_drops_loose_and_unverifiable_rows():
    p = _merge()
    kept = set(_by_id(p["comps"]))
    assert kept == {"100000000001", "100000000002", "100000000004",
                    "200000000001", "200000000003"}
    why = {d["item_id"]: d["reason"] for d in p["dropped"]}
    assert "multi-item" in why["100000000003"]                   # lot of 2 (Chrome)
    assert why["200000000002"] == cs.NO_CONDITION_REASON         # the null-condition part
    assert "new" in why["200000000004"].lower()                  # wrong cohort
    assert "core token" in why["200000000005"]                   # handwheel, not a machine
    assert "1 CompsAPI row(s) dropped: no condition" in cs.merge_line(p)


def test_compsapi_row_without_condition_is_dropped_even_if_title_matches():
    rows = [_row("300000000001", "Pfaff 463 Sewing Machine", 410.0, None)]
    p = _merge(rows)
    assert p["sources"]["compsapi"]["n_kept"] == 0
    assert p["dropped"][-1]["reason"] == cs.NO_CONDITION_REASON


def test_skipped_compsapi_leaves_chrome_rows_and_says_so():
    p = _merge(None, note="CompsAPI quota used up for this month")
    assert p["sources"]["compsapi"]["status"] == "skipped"
    assert {r["comp_source"] for r in p["comps"]} == {"chrome"}
    assert p["sources"]["chrome"]["n_kept"] == 3
    assert "Chrome results stand" in cs.merge_line(p)


def test_merged_run_feeds_price_stats_without_losing_chrome_rows(tmp_path):
    # Three verified Used CompsAPI rows would make the strict Used cohort
    # "not thin"; the merged run's reduced cohort keeps the Chrome rows in.
    api = [_row(f"40000000000{i}", "Pfaff 463 Sewing Machine", 400.0 + i, "Used")
           for i in range(3)]
    path = cs.save_merged_run(_merge(api), tmp_path)
    r = price_stats.price_from_runs(best_match_json=path, unit_type="single",
                                    require_tokens=TOKENS, condition="used")
    srcs = [c["comp_source"] for c in r["kept_comps"]]
    assert srcs.count("chrome") == 3 and srcs.count("compsapi") == 3


# --- CLI: quota out / missing key never block PRICE -------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("READ_CACHE_DIR", str(tmp_path / ".cache"))
    monkeypatch.setenv("COMPS_RUNS_DIR", str(tmp_path / "runs"))
    chrome = tmp_path / "chrome.json"
    chrome.write_text(json.dumps(CHROME_RUN))

    def run(*extra):
        monkeypatch.setattr(sys, "argv", ["compsapi_sold.py", "--with-chrome", str(chrome),
                                          "--condition", "used", "--require-tokens",
                                          *TOKENS, "--save-dir", str(tmp_path), *extra])
        cs._cli()
    return run


def _api(usage, sold=(200, {}, b"{}")):
    calls = []

    def opener(req):
        path = urllib.parse.urlparse(req.full_url).path
        calls.append(path)
        return {"/v1/usage": (200, {}, (FIX / usage).read_bytes()), "/v1/sold": sold}[path]
    opener.calls = calls
    return opener


def _saved(out):
    path = out.split("Saved results: ")[1].splitlines()[0]
    return json.loads(Path(path).read_text())


def test_cli_quota_out_keeps_chrome_rows(cli, monkeypatch, capsys):
    api = _api("usage_capped.json")
    monkeypatch.setattr(cs, "_client_from_config", lambda: cs.CompsAPIClient("k", opener=api))
    cli()                                           # no SystemExit: PRICE goes on
    out = capsys.readouterr().out
    assert "CompsAPI skipped: CompsAPI cap reached" in out
    assert "Chrome results stand" in out
    assert api.calls == ["/v1/usage"]
    run = _saved(out)
    assert run["source"] == "merged:chrome+compsapi"
    assert {r["comp_source"] for r in run["comps"]} == {"chrome"}


def test_cli_missing_key_keeps_chrome_rows(cli, monkeypatch, capsys):
    import config
    monkeypatch.delenv("COMPSAPI_API_KEY", raising=False)
    monkeypatch.setattr(config, "load_config", lambda reload=False: {})
    cli()
    out = capsys.readouterr().out
    assert "COMPSAPI_API_KEY" in out and "Chrome results stand" in out
    assert len(_saved(out)["comps"]) == 3


def test_cli_merges_api_rows_and_reuses_todays_search(cli, monkeypatch, capsys):
    sold = {"ok": True, "items": [
        {"item_id": "200000000001", "title": "Pfaff 463 Sewing Machine Head Only",
         "price": 380.0, "shipping_cost": 0.0, "format": "FIXED_PRICE",
         "best_offer": False, "condition": "Used"},
        {"item_id": "200000000002", "title": "Pfaff 463 Sewing Machine Bobbin Case",
         "price": 25.0, "shipping_cost": 5.0, "format": "FIXED_PRICE",
         "best_offer": False, "condition": None},
    ]}
    api = _api("usage_monthly.json", (200, {}, json.dumps(sold).encode()))
    monkeypatch.setattr(cs, "_client_from_config", lambda: cs.CompsAPIClient("k", opener=api))
    cli()
    run = _saved(capsys.readouterr().out)
    assert _by_id(run["comps"])["200000000001"]["comp_source"] == "compsapi"
    assert "200000000002" not in _by_id(run["comps"])
    n = len(api.calls)
    cli()                                           # same day: cached, no request
    assert len(api.calls) == n
    assert "200000000001" in _by_id(_saved(capsys.readouterr().out)["comps"])


def test_cli_with_chrome_requires_condition(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["compsapi_sold.py", "--with-chrome", "x.json"])
    with pytest.raises(SystemExit):
        cs._cli()
