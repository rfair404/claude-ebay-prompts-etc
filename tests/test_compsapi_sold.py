"""Tests for lib/compsapi_sold.py — the CompsAPI Stage B fallback.

No network: every HTTP call goes through a fake opener that serves the
doc-shaped fixtures in tests/fixtures/compsapi/ (field names from
https://compsapi.com/docs, 2026-11-12). Run files go to tmp_path and the
same-day cache to READ_CACHE_DIR under tmp_path.

Run:  pytest tests/test_compsapi_sold.py
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


def _fix(name):
    return (FIX / name).read_bytes()


class FakeAPI:
    """Serves fixtures by path; records every request."""

    def __init__(self, routes):
        self.routes = routes          # path -> (status, headers, body bytes)
        self.calls = []

    def __call__(self, req):
        u = urllib.parse.urlparse(req.full_url)
        self.calls.append({"path": u.path, "params": dict(urllib.parse.parse_qsl(u.query)),
                           "headers": {k.lower(): v for k, v in req.header_items()}})
        return self.routes[u.path]

    def paths(self):
        return [c["path"] for c in self.calls]


def _ok_api(usage="usage_ok.json"):
    return FakeAPI({"/v1/usage": (200, {}, _fix(usage)),
                    "/v1/sold": (200, {}, _fix("sold.json"))})


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("READ_CACHE_DIR", str(tmp_path / ".cache"))
    monkeypatch.setenv("COMPS_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path


# --- request shape ---------------------------------------------------------

def test_search_sends_bearer_user_agent_and_best_offer_flag():
    api = _ok_api()
    cs.fetch_comps(cs.CompsAPIClient("k-123", opener=api), "stanley framing square")
    assert api.paths() == ["/v1/usage", "/v1/sold"]
    sold = api.calls[1]
    assert sold["headers"]["authorization"] == "Bearer k-123"
    assert sold["headers"]["user-agent"]           # missing UA -> 403 at the vendor
    assert sold["params"] == {"q": "stanley framing square", "days": "90",
                              "size": "240", "best_offer": "1"}


def test_days_outside_vendor_range_is_refused_before_any_request():
    api = _ok_api()
    with pytest.raises(cs.CompsAPIError):
        cs.CompsAPIClient("k", opener=api).sold("x", days=2000)
    assert api.calls == []


# --- mapping into the shared CompRecord shape -------------------------------

def test_items_map_to_comprecords_with_delivered_basis():
    items = json.loads(_fix("sold.json"))["items"]
    comps = cs.items_to_comps(items, "q")
    assert len(comps) == 4                         # blank-title row dropped
    first = comps[0]
    assert (first.item_id, first.sold_price, first.shipping_cost, first.total_price,
            first.sold_date, first.listing_type) == (
        "226000000001", 24.99, 9.5, 34.49, "2026-11-02", "Buy It Now")
    assert comps[1].listing_type == "Auction" and comps[1].shipping_type == "free"
    assert comps[3].total_price is None            # unknown shipping stays unknown


def test_best_offer_rows_are_flagged_and_null_is_not():
    comps = cs.items_to_comps(json.loads(_fix("sold.json"))["items"])
    by_id = {c.item_id: c for c in comps}
    assert by_id["226000000003"].bo_accepted is True
    assert by_id["226000000003"].listing_type == "Best Offer"
    assert by_id["226000000004"].bo_accepted is False    # null = vendor couldn't check


def test_saved_run_feeds_price_stats_and_drops_best_offer_rows(tmp_path):
    comps, data, _ = cs.fetch_comps(cs.CompsAPIClient("k", opener=_ok_api()), "q")
    path = cs.save_compsapi_run(comps, "q", data["items"], tmp_path / "runs")
    run = json.loads(Path(path).read_text())
    assert run["source"] == "compsapi:sold" and run["n_comps"] == 4
    r = price_stats.price_from_runs(best_match_json=path, price_high_json=None,
                                    unit_type="single", condition=None,
                                    price_field="total")
    assert r["n_raw"] == 4
    assert "https://www.ebay.com/itm/226000000003" not in {
        c["url"] for c in r["kept_comps"]}
    removed = [x for lg in r["filter_log"] for x in lg["removed"]]
    assert [x for x in removed if x["reason"] == price_stats.BO_ACCEPTED_REASON]


# --- quota and errors: fail closed ------------------------------------------

def test_capped_usage_stops_before_the_search():
    api = _ok_api("usage_capped.json")
    with pytest.raises(cs.CompsAPIQuotaError):
        cs.fetch_comps(cs.CompsAPIClient("k", opener=api), "q")
    assert api.paths() == ["/v1/usage"]            # no search request spent


def test_monthly_plan_usage_without_cap_lets_the_search_run():
    api = _ok_api("usage_monthly.json")
    comps, _, u = cs.fetch_comps(cs.CompsAPIClient("k", opener=api), "q")
    assert comps and "console" in cs.usage_line(u)


def test_429_quota_reached_is_a_quota_error():
    api = FakeAPI({"/v1/usage": (200, {}, _fix("usage_monthly.json")),
                   "/v1/sold": (429, {}, _fix("error_quota.json"))})
    with pytest.raises(cs.CompsAPIQuotaError, match="month"):
        cs.fetch_comps(cs.CompsAPIClient("k", opener=api), "q")


def test_429_rate_limit_reports_retry_after():
    api = FakeAPI({"/v1/usage": (200, {}, _fix("usage_monthly.json")),
                   "/v1/sold": (429, {"Retry-After": "17"}, _fix("error_rate.json"))})
    with pytest.raises(cs.CompsAPIError, match="17s") as e:
        cs.fetch_comps(cs.CompsAPIClient("k", opener=api), "q")
    assert not isinstance(e.value, cs.CompsAPIQuotaError)


def test_bad_key_fails_at_the_usage_check():
    api = FakeAPI({"/v1/usage": (401, {}, _fix("error_auth.json"))})
    with pytest.raises(cs.CompsAPIError, match="401"):
        cs.fetch_comps(cs.CompsAPIClient("bad", opener=api), "q")


def test_missing_key_is_a_clear_cli_error(monkeypatch, capsys):
    import config
    monkeypatch.delenv("COMPSAPI_API_KEY", raising=False)
    monkeypatch.setattr(config, "load_config", lambda reload=False: {})
    monkeypatch.setattr(sys, "argv", ["compsapi_sold.py", "q"])
    with pytest.raises(SystemExit) as e:
        cs._cli()
    assert e.value.code == 2
    assert "COMPSAPI_API_KEY" in capsys.readouterr().err


def test_key_comes_from_env_before_config(monkeypatch):
    import config
    monkeypatch.setenv("COMPSAPI_API_KEY", "env-key")
    monkeypatch.setattr(config, "load_config",
                        lambda reload=False: {"compsapi": {"api_key": "cfg-key"}})
    assert config.get_compsapi_key() == "env-key"
    monkeypatch.delenv("COMPSAPI_API_KEY")
    assert config.get_compsapi_key() == "cfg-key"


# --- CLI: same-day cache saves quota ---------------------------------------

def test_cli_second_same_day_run_spends_no_request(monkeypatch, capsys):
    api = _ok_api()
    monkeypatch.setattr(cs, "_client_from_config", lambda: cs.CompsAPIClient("k", opener=api))
    monkeypatch.setattr(sys, "argv", ["compsapi_sold.py", "stanley", "square"])
    cs._cli()
    assert "Saved results:" in capsys.readouterr().out
    n = len(api.calls)
    cs._cli()
    assert "cache hit" in capsys.readouterr().out
    assert len(api.calls) == n
    monkeypatch.setattr(sys, "argv", ["compsapi_sold.py", "stanley", "square", "--fresh"])
    cs._cli()
    assert len(api.calls) == n + 2


# --- compare against a browser run ------------------------------------------

def test_compare_reports_overlap_price_and_best_offer():
    comps = cs.items_to_comps(json.loads(_fix("sold.json"))["items"])
    browser = {"queries": ["stanley framing square"], "comps": [
        {"item_id": "226000000001", "sold_price": 24.99},
        {"item_id": "226000000002", "sold_price": 35.0},
        {"item_id": "226000000003", "sold_price": 30.0, "bo_accepted": True},
        {"item_id": "999999999999", "sold_price": 10.0},
    ]}
    r = cs.compare_run(browser, comps)
    assert (r["matched"], r["same_price"]) == (3, 1)
    assert r["price_diff"] == [("226000000002", 35.0, 31.0)]
    assert r["best_offer"] == [("226000000003", 30.0, 22.0, True)]


def test_recent_browser_runs_dedupes_queries(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    for i, q in enumerate(["a", "A", "b"]):
        (d / f"apify_run_2026111{i}T000000Z_browse-best_match-p1-{i}.json").write_text(
            json.dumps({"queries": [q], "comps": []}))
    (d / "apify_run_x_compsapi-sold-1.json").write_text(json.dumps({"queries": ["c"]}))
    got = cs.recent_browser_runs(5, d)
    assert len(got) == 2 and all("browse-" in p.name for p in got)
