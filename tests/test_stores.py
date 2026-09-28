#!/usr/bin/env python3
"""lib/stores.py — the one store model (GH #156).

Pins the three things every multi-store caller leans on:

  * one precedence rule for "which store" (explicit > env > config > default),
    shared by credentials, storefront terms and ledger paths;
  * one file-naming convention (default = historic name, named = -<store>),
    which .gitignore must cover — a per-store file that git would stage is
    the one failure here that can't be undone;
  * nothing assumes exactly two stores.

No config file is read: load_config is patched per test.

Run:  python tests/test_stores.py
  or: pytest tests/test_stores.py
"""
import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import config as config_mod  # noqa: E402
import stores  # noqa: E402

THREE = {
    "ebay": {"stores": {"junk": {}, "outlet": {}}},
    "store": {"display_name": "Main"},
    # "records" is a business with no credentials yet — still a store.
    "storefronts": {"junk": {"display_name": None}, "records": {}},
}


@contextlib.contextmanager
def _cfg(cfg, env=None):
    real_load, real_env = config_mod.load_config, dict(os.environ)
    config_mod.load_config = lambda *a, **k: cfg
    os.environ.pop(stores.STORE_ENV_VAR, None)
    os.environ.pop("EBAYBIZ_LISTINGS_LEDGER", None)
    os.environ.pop("EBAYBIZ_LISTINGS_LOG", None)
    if env:
        os.environ.update(env)
    try:
        yield
    finally:
        config_mod.load_config = real_load
        os.environ.clear()
        os.environ.update(real_env)


def test_precedence_explicit_env_config_default():
    with _cfg({}):
        assert stores.resolve_store_name() == "default"
    with _cfg({"ebay": {"active_store": "junk"}}):
        assert stores.resolve_store_name() == "junk"
    with _cfg({"ebay": {"active_store": "junk"}}, env={"EBAYBIZ_STORE": "outlet"}):
        assert stores.resolve_store_name() == "outlet"
        assert stores.resolve_store_name("records") == "records"


def test_bad_names_never_reach_the_filesystem():
    with _cfg({}):
        for bad in ("../x", "a/b", "a.b", "two words", ""):
            try:
                stores.store_file("listings_ledger.csv", bad or "x y")
            except ValueError:
                continue
            raise AssertionError(f"accepted {bad!r}")


def test_configured_stores_is_default_first_and_not_capped_at_two():
    with _cfg(THREE):
        assert stores.configured_stores() == ["default", "junk", "outlet", "records"]
    with _cfg({}):
        assert stores.configured_stores() == ["default"]


def test_default_store_keeps_every_historic_filename():
    with _cfg({}):
        p = stores.paths("default")
        assert p.listings_ledger == stores.REPO / "listings_ledger.csv"
        assert p.sales_ledger == stores.REPO / "sales_ledger.csv"
        assert p.inventory_sheet_csv == stores.REPO / "inventory_sheet.csv"
        assert p.pick_list_state == stores.REPO / ".pick_list_state.json"
        assert p.finances_sync_status == stores.REPO / "reports" / "finances_sync_status.json"


def test_named_store_gets_its_own_file_for_every_path():
    with _cfg(THREE):
        d, j, o = stores.paths("default"), stores.paths("junk"), stores.paths("outlet")
        for field in d.__dataclass_fields__:
            if field == "store":
                continue
            a, b, c = getattr(d, field), getattr(j, field), getattr(o, field)
            assert len({a, b, c}) == 3, f"{field} collides across stores"
            assert b.parent == a.parent, f"{field} moved directory"
        assert j.sales_ledger.name == "sales_ledger-junk.csv"


def test_every_named_store_path_is_gitignored():
    """A per-store ledger that git would stage leaks buyer PII and revenue into
    history — the #156/#158 gap where the ignore rule used '.' and the code
    wrote '-'. Checked with git itself, not by re-reading the patterns."""
    with _cfg(THREE):
        rels = []
        for store in ("default", "junk", "outlet"):
            p = stores.paths(store)
            for field in p.__dataclass_fields__:
                if field != "store":
                    rels.append(str(getattr(p, field).relative_to(stores.REPO)))
    r = subprocess.run(["git", "check-ignore", "--no-index", "-v", "-n", *rels],
                       cwd=ROOT, capture_output=True, text=True)
    unignored = [ln.split("\t")[-1] for ln in r.stdout.splitlines() if ln.startswith("::")]
    assert not unignored, f"not gitignored: {unignored}"


def test_listings_ledger_env_override_wins_for_every_store():
    with _cfg(THREE, env={"EBAYBIZ_LISTINGS_LEDGER": "/tmp/one.csv"}):
        assert stores.paths("junk").listings_ledger == Path("/tmp/one.csv")
        assert stores.paths("default").listings_ledger == Path("/tmp/one.csv")


def test_draft_store_is_the_drafts_own_field_not_the_ambient_store():
    with _cfg({}, env={"EBAYBIZ_STORE": "junk"}):
        assert stores.draft_store({}) == "default"
        assert stores.draft_store(None) == "default"
        assert stores.draft_store({"store": "outlet"}) == "outlet"


def test_cli_contract_store_and_all_stores():
    ap = argparse.ArgumentParser()
    stores.add_store_args(ap, all_stores=True)
    with _cfg(THREE):
        assert stores.stores_from_args(ap.parse_args([])) == ["default"]
        assert stores.stores_from_args(ap.parse_args(["--store", "junk"])) == ["junk"]
        assert stores.stores_from_args(ap.parse_args(["--all-stores"])) == \
            ["default", "junk", "outlet", "records"]
        try:
            stores.stores_from_args(ap.parse_args(["--all-stores", "--store", "junk"]))
        except SystemExit:
            pass
        else:
            raise AssertionError("--store with --all-stores must refuse")


def test_writes_refuse_an_ambient_store():
    ap = argparse.ArgumentParser()
    stores.add_store_args(ap)
    with _cfg(THREE, env={"EBAYBIZ_STORE": "junk"}):
        try:
            stores.require_explicit_store(ap.parse_args([]), "bulk rewrite")
        except SystemExit:
            pass
        else:
            raise AssertionError("an env-var store must not authorise a write")
        assert stores.require_explicit_store(ap.parse_args(["--store", "junk"]), "x") == "junk"


def test_credentials_storefront_and_ledger_agree_on_the_store():
    """The whole point: one --store (or one env var) moves all three together."""
    import ebay_client
    cfg = dict(THREE, ebay={"active_store": "junk", "stores": {"junk": {"environment": "production"}}})
    real = ebay_client.load_config
    ebay_client.load_config = lambda *a, **k: cfg
    try:
        with _cfg(cfg):
            assert ebay_client.load_credentials().store == "junk"
            assert config_mod.get_storefront()["display_name"] is None
            assert stores.paths().listings_ledger.name == "listings_ledger-junk.csv"
    finally:
        ebay_client.load_config = real


def test_named_store_never_inherits_customer_facing_identity():
    cfg = {"store": {"display_name": "Main", "tagline": "BUY", "storefront_url": "ebay.com/usr/main",
                     "seller_username": "main", "returns": "free_30_day"},
           "storefronts": {"junk": {"returns": "none_as_is"}}}
    with _cfg(cfg):
        g = config_mod.get_store("junk")
        assert g == {"display_name": "", "tagline": "", "storefront_url": "", "closing_block": ""}
        assert config_mod.get_store("default")["storefront_url"] == "ebay.com/usr/main"
        assert config_mod.get_storefront("junk")["seller_username"] is None


def test_store_profile_selects_curate_math():
    cfg = {"active_profile": "default",
           "profiles": {"junk-lots": {"profit_floor": 10}},
           "storefronts": {"junk": {"profile": "junk-lots"}}}
    with _cfg(cfg):
        assert config_mod.get_profile(store="junk")["profit_floor"] == 10
        assert config_mod.get_profile(store="default")["profit_floor"] == 100
        # an unknown ambient store never breaks buy math
        os.environ["EBAYBIZ_STORE"] = "nope"
        assert config_mod.get_profile()["profit_floor"] == 100


def test_cli_shim_forwards_store_and_refuses_neutral_commands():
    def run(*a):
        return subprocess.run([sys.executable, "-m", "lib.cli", *a], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    r = run("--store", "junk", "voice")
    assert r.returncode == 2 and "store-neutral" in r.stdout
    r = run("--all-stores", "policy-sweep")
    assert r.returncode == 2 and "--all-stores" in r.stdout
    r = run("--store", "junk", "status", "--store", "junk")
    assert r.returncode == 2 and "once" in r.stdout


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
