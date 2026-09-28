#!/usr/bin/env python3
"""lib/config.py — storefront resolution (GH #147).

`ebay.stores.<name>` says which SELLER ACCOUNT an API call goes to.
`storefronts.<name>` says what kind of SHOP it is: its name, its close, its
terms. The two are different questions and this suite exists to keep them
from collapsing into one.

The rule with teeth is identity non-inheritance. Policy keys a named
storefront omits fall through to the default store, because an override
should only state what differs. Identity keys must NOT — a junk listing
that omits display_name has to ship the unnamed thank-you, never sign off
as the main storefront. Inheriting identity is precisely the bug the
mechanism was built to fix, so it is pinned here rather than left to
reviewer memory.

No config file is read: load_config is patched per test.

Run:  python tests/test_storefront.py
  or: pytest tests/test_storefront.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import config as config_mod  # noqa: E402
from config import (  # noqa: E402
    ConfigError,
    STOREFRONT_IDENTITY_KEYS,
    get_storefront,
    list_storefronts,
)

MAIN_CLOSE = "Thank you for looking — Pop's Games.\n"
JUNK_CLOSE = "Sold as-is. Thank you for looking.\n"

CONFIG = {
    "ebay": {},
    "store": {
        "display_name": "Pop's Games",
        "closing_block": MAIN_CLOSE,
        "shipping": "free_ground",
        "returns": "free_30_day",
    },
    "storefronts": {
        "junk": {
            "display_name": None,
            "closing_block": JUNK_CLOSE,
            "returns": "none_as_is",
            "shipping": "buyer_pays_calculated",
            "routing": ["as_is", "untested", "damaged"],
        },
        # Deliberately sparse: states one term and nothing else.
        "outlet": {"returns": "none_as_is"},
    },
}


def _patched(cfg, fn, env=None):
    real_load = config_mod.load_config
    real_env = dict(os.environ)
    config_mod.load_config = lambda *a, **k: cfg
    if env:
        os.environ.update(env)
    try:
        return fn()
    finally:
        config_mod.load_config = real_load
        os.environ.clear()
        os.environ.update(real_env)


# ---------------------------------------------------------------------------
# The default storefront is the top-level `store:` block, read in place.
# ---------------------------------------------------------------------------

def test_default_storefront_is_the_top_level_store_block():
    def go():
        s = get_storefront("default")
        assert s["display_name"] == "Pop's Games"
        assert s["closing_block"] == MAIN_CLOSE
    _patched(CONFIG, go)


def test_no_argument_resolves_to_default():
    def go():
        assert get_storefront() == get_storefront("default")
    _patched(CONFIG, go)


def test_a_config_with_no_storefronts_section_still_has_a_default():
    """Single-store configs predate this feature and must not need migrating."""
    def go():
        assert get_storefront()["display_name"] == "Pop's Games"
        assert list_storefronts() == []
    _patched({"store": {"display_name": "Pop's Games"}}, go)


# ---------------------------------------------------------------------------
# Identity never inherits. This is the whole point.
# ---------------------------------------------------------------------------

def test_junk_does_not_inherit_the_default_store_name():
    def go():
        assert get_storefront("junk")["display_name"] is None
    _patched(CONFIG, go)


def test_junk_close_does_not_mention_the_default_storefront():
    def go():
        assert "Pop's Games" not in get_storefront("junk")["closing_block"]
    _patched(CONFIG, go)


def test_a_sparse_storefront_gets_null_identity_not_the_defaults():
    """`outlet` sets only `returns`. It must still not borrow a name."""
    def go():
        s = get_storefront("outlet")
        for key in STOREFRONT_IDENTITY_KEYS:
            assert s[key] is None, f"{key} leaked from the default storefront"
    _patched(CONFIG, go)


# ---------------------------------------------------------------------------
# Policy DOES inherit, so an override only states what differs.
# ---------------------------------------------------------------------------

def test_sparse_storefront_inherits_unstated_policy():
    def go():
        assert get_storefront("outlet")["shipping"] == "free_ground"
    _patched(CONFIG, go)


def test_stated_policy_overrides_the_default():
    def go():
        s = get_storefront("junk")
        assert s["returns"] == "none_as_is"
        assert s["shipping"] == "buyer_pays_calculated"
    _patched(CONFIG, go)


def test_unset_key_is_absent_rather_than_guessed():
    """price_posture is deliberately unset for junk: it inherits the house
    rule. An invented value here would be a pricing policy nobody approved."""
    def go():
        assert get_storefront("junk").get("price_posture") is None
    _patched(CONFIG, go)


# ---------------------------------------------------------------------------
# Selection precedence — matches ebay_client.load_credentials so that one
# `--store junk` picks the account AND the business behind it.
# ---------------------------------------------------------------------------

def test_env_var_selects_the_storefront():
    def go():
        assert get_storefront()["returns"] == "none_as_is"
    _patched(CONFIG, go, env={"EBAYBIZ_STORE": "junk"})


def test_active_store_in_config_selects_the_storefront():
    cfg = dict(CONFIG, ebay={"active_store": "junk"})

    def go():
        assert get_storefront()["returns"] == "none_as_is"
    _patched(cfg, go)


def test_explicit_argument_beats_the_env_var():
    def go():
        assert get_storefront("default")["display_name"] == "Pop's Games"
    _patched(CONFIG, go, env={"EBAYBIZ_STORE": "junk"})


# ---------------------------------------------------------------------------
# An unknown name is an error, never a silent fallback.
# ---------------------------------------------------------------------------

def test_unknown_storefront_raises_rather_than_falling_back():
    """Falling back would ship the main store's identity and terms on a
    listing meant for somewhere else — the expensive failure, silently."""
    def go():
        try:
            get_storefront("nope")
        except ConfigError as e:
            assert "nope" in str(e)
            assert "junk" in str(e), "the error should list what IS configured"
        else:
            raise AssertionError("expected ConfigError")
    _patched(CONFIG, go)


def test_a_non_mapping_storefront_is_an_error():
    cfg = dict(CONFIG, storefronts={"junk": "not-a-mapping"})

    def go():
        try:
            get_storefront("junk")
        except ConfigError as e:
            assert "mapping" in str(e)
        else:
            raise AssertionError("expected ConfigError")
    _patched(cfg, go)


def test_list_storefronts_excludes_the_implicit_default():
    def go():
        assert list_storefronts() == ["junk", "outlet"]
    _patched(CONFIG, go)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
