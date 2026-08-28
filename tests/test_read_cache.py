"""Tests for lib/read_cache.py — the generic on-disk cache primitive
(V4_PLAN Phase 4, #30).

No network, no Chrome. Each test writes/reads under a temp directory passed
explicitly as `cache_dir`, so nothing here ever touches a real `.cache/`.

Run:  pytest tests/test_read_cache.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import read_cache  # noqa: E402


# ---------------------------------------------------------------------------
# get / put — basic hit/miss
# ---------------------------------------------------------------------------

def test_miss_when_nothing_written(tmp_path):
    result = read_cache.get("ns", "q1", cache_dir=tmp_path)
    assert result.hit is False
    assert result.value is None


def test_put_then_get_is_a_hit(tmp_path):
    read_cache.put("ns", "q1", value={"n": 3}, cache_dir=tmp_path)
    result = read_cache.get("ns", "q1", cache_dir=tmp_path)
    assert result.hit is True
    assert result.value == {"n": 3}


def test_value_survives_any_json_serializable_shape(tmp_path):
    value = {"sorts": {"best_match": {"n": 5, "path": "x.json"}}, "flags": [1, 2]}
    read_cache.put("ns", "q1", value=value, cache_dir=tmp_path)
    assert read_cache.get("ns", "q1", cache_dir=tmp_path).value == value


# ---------------------------------------------------------------------------
# Keying — query, namespace, and date all partition the cache
# ---------------------------------------------------------------------------

def test_different_query_is_a_different_entry(tmp_path):
    read_cache.put("ns", "gorham bowl", value="A", cache_dir=tmp_path)
    assert read_cache.get("ns", "gorham bowl", cache_dir=tmp_path).hit is True
    assert read_cache.get("ns", "reed barton bowl", cache_dir=tmp_path).hit is False


def test_different_namespace_is_a_different_entry(tmp_path):
    read_cache.put("comp_run", "q", value="A", cache_dir=tmp_path)
    assert read_cache.get("policy_read", "q", cache_dir=tmp_path).hit is False


def test_different_day_is_a_different_entry(tmp_path):
    read_cache.put("ns", "q1", value="yesterday", cache_dir=tmp_path,
                   date="2026-08-27")
    today = read_cache.get("ns", "q1", cache_dir=tmp_path, date="2026-08-28")
    assert today.hit is False
    # yesterday's entry is untouched, just not what a today-lookup sees
    stale = read_cache.get("ns", "q1", cache_dir=tmp_path, date="2026-08-27")
    assert stale.hit is True and stale.value == "yesterday"


def test_cache_key_is_order_sensitive_but_deterministic(tmp_path):
    k1 = read_cache.cache_key("ns", "a", "b", date="2026-08-28")
    k2 = read_cache.cache_key("ns", "a", "b", date="2026-08-28")
    k3 = read_cache.cache_key("ns", "b", "a", date="2026-08-28")
    assert k1 == k2
    assert k1 != k3


# ---------------------------------------------------------------------------
# Malformed / stale-on-disk edge cases — always degrade to a miss
# ---------------------------------------------------------------------------

def test_corrupt_json_file_is_a_miss_not_a_crash(tmp_path):
    key = read_cache.cache_key("ns", "q1")
    path = tmp_path / "ns" / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")
    result = read_cache.get("ns", "q1", cache_dir=tmp_path)
    assert result.hit is False


def test_wrong_shaped_json_is_a_miss(tmp_path):
    key = read_cache.cache_key("ns", "q1")
    path = tmp_path / "ns" / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text('["not", "a", "cache", "envelope"]', encoding="utf-8")
    assert read_cache.get("ns", "q1", cache_dir=tmp_path).hit is False


def test_missing_value_key_is_a_miss(tmp_path):
    key = read_cache.cache_key("ns", "q1")
    path = tmp_path / "ns" / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"namespace": "ns"}', encoding="utf-8")
    assert read_cache.get("ns", "q1", cache_dir=tmp_path).hit is False


# ---------------------------------------------------------------------------
# cached_call — the miss/hit/fresh contract callers actually use
# ---------------------------------------------------------------------------

def test_cached_call_miss_invokes_fetch_and_populates(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return {"n": 7}

    value, hit = read_cache.cached_call("ns", "q1", fetch=fetch, cache_dir=tmp_path)
    assert value == {"n": 7} and hit is False and len(calls) == 1
    assert read_cache.get("ns", "q1", cache_dir=tmp_path).value == {"n": 7}


def test_cached_call_hit_skips_fetch(tmp_path):
    read_cache.put("ns", "q1", value={"n": 1}, cache_dir=tmp_path)

    def fetch():
        raise AssertionError("fetch must not run on a cache hit")

    value, hit = read_cache.cached_call("ns", "q1", fetch=fetch, cache_dir=tmp_path)
    assert value == {"n": 1} and hit is True


def test_cached_call_fresh_bypasses_and_repopulates(tmp_path):
    read_cache.put("ns", "q1", value={"n": 1}, cache_dir=tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return {"n": 2}

    value, hit = read_cache.cached_call("ns", "q1", fetch=fetch, fresh=True,
                                        cache_dir=tmp_path)
    assert value == {"n": 2} and hit is False and len(calls) == 1
    # repopulated: a later non-fresh lookup now sees the fresh value
    assert read_cache.get("ns", "q1", cache_dir=tmp_path).value == {"n": 2}


def test_default_cache_dir_honors_read_cache_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("READ_CACHE_DIR", str(tmp_path / "custom"))
    assert read_cache.default_cache_dir() == tmp_path / "custom"


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-q"]))
