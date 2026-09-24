"""
Config loader for ebaybiz V2.

Loads YAML config and provides typed accessors for API keys, Apify
Actor selection, eBay credentials, and CURATE strategy profiles.

Config file location (precedence, first found wins):
    1. EBAYBIZ_CONFIG env var (explicit override — must point at a file)
    2. <project-root>/config.yaml (preferred — editable from this project)
    3. %APPDATA%\\ebaybiz\\config.yaml (Windows fallback for legacy setups)
       or ~/.ebaybiz/config.yaml (macOS / Linux fallback)

The project root is computed from this file's location:
    <project-root>/lib/config.py  →  <project-root>

Per-setting precedence (highest wins):
    1. Explicit function argument
    2. Environment variable (where applicable)
    3. Config file value
    4. Built-in default

Usage:
    from config import get_apify_token, get_lens_actor, get_profile

    token = get_apify_token()              # raises ConfigError if missing
    actor = get_lens_actor()               # default if not configured
    profile = get_profile("estate-sale")   # full profile dict

CLI usage (manual testing / debugging):
    python config.py --where   # print resolved config file path
    python config.py --show    # print resolved config (secrets redacted)
    python config.py --check   # verify required secrets load
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError as e:
    raise ImportError(
        "PyYAML is required. Install with: pip install pyyaml"
    ) from e


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ConfigError(RuntimeError):
    """Raised when a required config value is missing or malformed."""


# ---------------------------------------------------------------------------
# Built-in defaults (last-resort fallbacks)
# ---------------------------------------------------------------------------

# Google Lens reverse-image actor (IDENTIFY's visual second opinion). Chosen for
# reliability (high success rate) + AI-mode + visual/exact match buckets.
DEFAULT_LENS_ACTOR = "borderline/google-lens"

# lib/fine_inspect.py — pluggable high-resolution image inspector (fine
# print, raised/embossed marks, signatures). "gemini" is the only backend
# shipped today; registering a new one in fine_inspect.BACKENDS makes its
# name valid here too.
DEFAULT_VISION_BACKEND = "gemini"
# Chosen for accuracy on fine detail over cost/latency — fine_inspect is
# called sparingly, on crops other reads already failed on.
DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"

DEFAULT_PROFILE = {
    "margin_target": 0.50,
    "buy_point_multiplier": 0.5,
    "fee_pct": 0.13,
    "profit_floor": 100,
    "drive_cost": 0,
}


# ---------------------------------------------------------------------------
# Config file location (cross-platform, project-root-preferred)
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """Compute the ebaybiz project root from this file's location.

    Layout: <project-root>/lib/config.py
    """
    return Path(__file__).resolve().parent.parent


def _project_root_config_path() -> Path:
    """The preferred config location — visible inside the project workspace."""
    return _project_root() / "config.yaml"


def _user_config_dir() -> Path:
    """Cross-platform location for the user-global ebaybiz config directory.

    - Windows: %APPDATA%\\ebaybiz\\
    - macOS / Linux: ~/.ebaybiz/
    """
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "ebaybiz"
    return Path.home() / ".ebaybiz"


def _user_config_path() -> Path:
    """Legacy user-global config path (kept as fallback)."""
    return _user_config_dir() / "config.yaml"


def config_path() -> Path:
    """Return the resolved config.yaml path (the one we'd actually read).

    Resolution order:
      1. EBAYBIZ_CONFIG env var if set
      2. <project-root>/config.yaml if it exists
      3. <user-config>/config.yaml otherwise (returned even if it doesn't
         exist — caller can check `.exists()` and create as needed)
    """
    override = os.environ.get("EBAYBIZ_CONFIG")
    if override:
        return Path(override).expanduser().resolve()

    project = _project_root_config_path()
    if project.exists():
        return project

    return _user_config_path()


# ---------------------------------------------------------------------------
# Loading (cached on first read)
# ---------------------------------------------------------------------------

_cached_config: Optional[dict] = None


def load_config(reload: bool = False) -> dict:
    """Load the config file. Caches the result.

    Returns an empty dict if the file doesn't exist — lets purely-env-var
    setups still work without a config file.

    Args:
        reload: if True, re-read from disk even if cached.

    Raises:
        ConfigError if the file exists but is not a YAML mapping.
    """
    global _cached_config
    if _cached_config is not None and not reload:
        return _cached_config

    path = config_path()
    if not path.exists():
        _cached_config = {}
        return _cached_config

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Config file at {path} is not valid YAML: {e}") from e

    if loaded is None:
        _cached_config = {}
    elif isinstance(loaded, dict):
        _cached_config = loaded
    else:
        raise ConfigError(
            f"Config file at {path} is not a YAML mapping "
            f"(got {type(loaded).__name__})"
        )

    return _cached_config


def _nested(config: dict, *keys: str) -> Any:
    """Get a value from a nested dict path, returning None on any missing key."""
    cur: Any = config
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


# ---------------------------------------------------------------------------
# Typed accessors
# ---------------------------------------------------------------------------

def get_apify_token() -> str:
    """Apify API token. Precedence: env var > config file > error.

    Raises:
        ConfigError if no token is available anywhere.
    """
    env = os.environ.get("APIFY_API_TOKEN")
    if env:
        return env

    config = load_config()
    token = _nested(config, "apify", "api_token")
    if token:
        return str(token)

    raise ConfigError(
        f"Apify API token not found.\n"
        f"  Set the APIFY_API_TOKEN environment variable, OR\n"
        f"  add this to {config_path()}:\n"
        f"      apify:\n"
        f"        api_token: \"<your-token>\"\n"
        f"  (Get a token at https://console.apify.com/account/integrations)"
    )


def get_lens_actor() -> str:
    """Google Lens reverse-image Actor ID. Precedence: env > config > default."""
    env = os.environ.get("APIFY_LENS_ACTOR")
    if env:
        return env

    config = load_config()
    actor = _nested(config, "apify", "lens_actor")
    if actor:
        return str(actor)

    return DEFAULT_LENS_ACTOR


def get_gemini_api_key() -> str:
    """Gemini API key for lib/fine_inspect.py. Precedence: env var > config
    file > error. Same precedence contract as get_apify_token().

    Raises:
        ConfigError if no key is available anywhere.
    """
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env

    config = load_config()
    key = _nested(config, "vision", "gemini", "api_key")
    if key:
        return str(key)

    raise ConfigError(
        f"Gemini API key not found.\n"
        f"  Set the GEMINI_API_KEY environment variable, OR\n"
        f"  add this to {config_path()}:\n"
        f"      vision:\n"
        f"        gemini:\n"
        f"          api_key: \"<your-key>\"\n"
        f"  (Get a key at https://aistudio.google.com/apikey)"
    )


def get_gemini_model() -> str:
    """Gemini model id for fine_inspect's Gemini backend. Precedence:
    env > config > DEFAULT_GEMINI_MODEL."""
    env = os.environ.get("GEMINI_VISION_MODEL")
    if env:
        return env

    config = load_config()
    model = _nested(config, "vision", "gemini", "model")
    if model:
        return str(model)

    return DEFAULT_GEMINI_MODEL


def get_vision_backend() -> str:
    """Which lib/fine_inspect.py backend to use. Precedence:
    env > config `vision.backend` > DEFAULT_VISION_BACKEND ("gemini")."""
    env = os.environ.get("VISION_BACKEND")
    if env:
        return env

    config = load_config()
    backend = _nested(config, "vision", "backend")
    if backend:
        return str(backend)

    return DEFAULT_VISION_BACKEND


def get_easypost_key() -> str:
    """EasyPost API key. Precedence: env var > config file > error.

    Same precedence contract as get_apify_token().
    EasyPost issues test and production keys from the same dashboard; which
    one is configured decides whether a confirmed buy_label() call hits real
    carriers or EasyPost's no-charge test mode — that is an account-setup
    choice made by a human, not something this loader infers (GH #80).

    Raises:
        ConfigError if no key is available anywhere.
    """
    env = os.environ.get("EASYPOST_API_KEY")
    if env:
        return env

    config = load_config()
    key = _nested(config, "easypost", "api_key")
    if key:
        return str(key)

    raise ConfigError(
        f"EasyPost API key not found.\n"
        f"  Set the EASYPOST_API_KEY environment variable, OR\n"
        f"  add this to {config_path()}:\n"
        f"      easypost:\n"
        f"        api_key: \"<your-key>\"\n"
        f"  Get a key (test or production) at https://www.easypost.com/account/api-keys\n"
        f"  A human still has to create the account and fund its balance before\n"
        f"  any real purchase can succeed — that step is deliberately not automated."
    )


def get_ebay_credentials() -> dict:
    """Return the raw eBay credentials section from config.

    For typed access + OAuth flow, use `ebay_client.load_credentials()`
    instead. This accessor exists for shell/CLI introspection.

    Returns:
        Dict (possibly empty) with keys: environment, app_id, cert_id,
        dev_id, redirect_uri, user_refresh_token.
    """
    config = load_config()
    section = config.get("ebay") or {}
    return {
        "environment":        section.get("environment") or "sandbox",
        "app_id":             section.get("app_id"),
        "cert_id":            section.get("cert_id"),
        "dev_id":             section.get("dev_id"),
        "redirect_uri":       section.get("redirect_uri"),
        "user_refresh_token": section.get("user_refresh_token"),
    }


def get_store() -> dict:
    """Return the `store:` branding section from config.

    Every key is a string, defaulting to "" when unset or when there is no
    config file at all — callers decide what an empty value falls back to
    (see tools/pick_list_html.py). This does not read `ebay.stores.<name>`
    (per-store OAuth credentials, see ebay_client.load_credentials()) — the
    two are unrelated config trees that happen to share the word "store".

    Returns:
        Dict with keys: display_name, tagline, storefront_url, closing_block.
    """
    config = load_config()
    section = config.get("store") or {}
    return {
        "display_name":   section.get("display_name") or "",
        "tagline":        section.get("tagline") or "",
        "storefront_url": section.get("storefront_url") or "",
        "closing_block":  section.get("closing_block") or "",
    }


def get_profile(name: Optional[str] = None) -> dict:
    """Return a CURATE strategy profile by name.

    If name is None, uses the `active_profile` value from the config
    (or "default" if not set). Returns built-in DEFAULT_PROFILE if the
    config file doesn't exist or the requested profile isn't found in
    a config file but the name is "default".

    Args:
        name: profile name, or None to use the active profile.

    Returns:
        Merged dict — built-in defaults overlaid with profile-specific
        overrides from the config file.

    Raises:
        ConfigError if a non-default profile name is requested but no
        profile by that name exists in the config file.
    """
    config = load_config()

    if name is None:
        name = _nested(config, "active_profile") or "default"

    profile = _nested(config, "profiles", name)

    if profile is None:
        if name == "default":
            # No config or no profiles section → built-in defaults
            return dict(DEFAULT_PROFILE)
        raise ConfigError(
            f"Profile '{name}' not found in {config_path()}.\n"
            f"  Add it under 'profiles:' in the config file, or use a "
            f"different --profile."
        )

    if not isinstance(profile, dict):
        raise ConfigError(
            f"Profile '{name}' in {config_path()} is not a YAML mapping "
            f"(got {type(profile).__name__})"
        )

    # Merge with built-in defaults so partial profiles still work
    merged = dict(DEFAULT_PROFILE)
    merged.update(profile)
    return merged


# ---------------------------------------------------------------------------
# Storefronts (GH #147) — the BUSINESS behind a seller account
# ---------------------------------------------------------------------------
#
# `ebay.stores.<name>` (ebay_client.load_credentials) answers "which account
# does this API call go to". That is not the same question as "what kind of
# shop is this". A secondary junk store is a different business: it sells
# as-is goods, refuses returns, makes the buyer pay postage, and signs off
# under its own name — none of which is a credential.
#
# The default storefront is the existing top-level `store:` block, read in
# place. It is deliberately NOT copied into `storefronts.default`: one fact,
# one home, and today's single-store configs keep working untouched.

# Keys a named storefront must state for itself. Inheriting the default
# store's IDENTITY is the bug this whole mechanism exists to fix — a junk
# listing that omits display_name must ship the unnamed thank-you, never
# sign off as the main storefront. Policy keys inherit; these do not.
STOREFRONT_IDENTITY_KEYS = ("display_name", "closing_block")


def list_storefronts() -> list[str]:
    """Names of the additional storefront profiles under `storefronts:`.

    The default storefront (top-level `store:`) is always available and is
    not included here — same convention as ebay_client.list_stores().
    """
    section = load_config().get("storefronts") or {}
    return sorted(section.keys())


def get_storefront(name: Optional[str] = None) -> dict:
    """Return the storefront profile for a store, defaults merged in.

    Args:
        name: storefront to load. Precedence matches
            ebay_client.load_credentials() so one `--store junk` selects the
            account AND the business behind it: explicit arg > EBAYBIZ_STORE
            env var > ebay.active_store in config > "default".

    Returns:
        Merged dict. Policy keys fall through to the default storefront when
        the named one omits them, so a sparse override only has to state what
        actually differs. Identity keys (STOREFRONT_IDENTITY_KEYS) never fall
        through — an unset display_name is None, not the default store's name.

    Raises:
        ConfigError if a non-default storefront name has no `storefronts:`
        entry — silently falling back to the main storefront's terms is how a
        junk listing would go out under the good store's identity.
    """
    config = load_config()

    if name is None:
        name = (
            os.environ.get("EBAYBIZ_STORE")
            or _nested(config, "ebay", "active_store")
            or "default"
        )

    base = dict(_nested(config, "store") or {})

    if name == "default":
        return base

    override = _nested(config, "storefronts", name)
    if override is None:
        known = ", ".join(list_storefronts()) or "(none configured)"
        raise ConfigError(
            f"Storefront '{name}' not found in {config_path()}.\n"
            f"  Add it under 'storefronts:' — a named store needs its own "
            f"identity and terms, and inheriting the default store's would "
            f"ship its name on the wrong listings.\n"
            f"  Configured storefronts: {known}"
        )
    if not isinstance(override, dict):
        raise ConfigError(
            f"Storefront '{name}' in {config_path()} is not a YAML mapping "
            f"(got {type(override).__name__})"
        )

    merged = {k: v for k, v in base.items() if k not in STOREFRONT_IDENTITY_KEYS}
    for k in STOREFRONT_IDENTITY_KEYS:
        merged[k] = None
    merged.update(override)
    return merged


# ---------------------------------------------------------------------------
# CLI entry point (for debugging / verification)
# ---------------------------------------------------------------------------

def _redact(value: Optional[str], keep: int = 6) -> str:
    """Show only the first N characters of a secret."""
    if not value:
        return "(not set)"
    if len(value) <= keep:
        return value[:1] + "***"
    return value[:keep] + "***"


def _cli() -> None:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="ebaybiz config utility (show / check / where)"
    )
    parser.add_argument(
        "--where",
        action="store_true",
        help="Print the config file path and exit",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print the resolved config (secrets redacted)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify required secrets load",
    )
    args = parser.parse_args()

    if not (args.where or args.show or args.check):
        parser.print_help()
        sys.exit(0)

    if args.where:
        path = config_path()
        print(path)
        print(f"  exists: {path.exists()}")

    if args.show:
        path = config_path()
        print(f"Config file: {path}")
        print(f"  exists: {path.exists()}")
        print()

        try:
            apify_token = get_apify_token()
            print(f"apify.api_token: {_redact(apify_token)}")
        except ConfigError:
            print("apify.api_token: (not set)")

        print(f"apify.lens_actor: {get_lens_actor()}")

        try:
            gemini_key = get_gemini_api_key()
            print(f"vision.gemini.api_key: {_redact(gemini_key)}")
        except ConfigError:
            print("vision.gemini.api_key: (not set)")

        print(f"vision.backend: {get_vision_backend()}")
        print(f"vision.gemini.model: {get_gemini_model()}")

        try:
            ep_key = get_easypost_key()
            print(f"easypost.api_key: {_redact(ep_key)}")
        except ConfigError:
            print("easypost.api_key: (not set)")

        ebay_creds = get_ebay_credentials()
        print()
        print(f"ebay.environment:        {ebay_creds['environment']}")
        print(f"ebay.app_id:             {_redact(ebay_creds['app_id'])}")
        print(f"ebay.cert_id:            {_redact(ebay_creds['cert_id'])}")
        print(f"ebay.dev_id:             {_redact(ebay_creds['dev_id'])}")
        print(f"ebay.redirect_uri:       {ebay_creds['redirect_uri'] or '(not set)'}")
        print(f"ebay.user_refresh_token: {_redact(ebay_creds['user_refresh_token'])}")

        print()
        print("active profile:")
        try:
            profile = get_profile()
            print(json.dumps(profile, indent=2))
        except ConfigError as e:
            print(f"  ERROR: {e}")

    if args.check:
        # Required: the eBay Sell API credentials. Every phase that touches the
        # account needs them. Apify is optional and is reported, not enforced --
        # it serves lens_id.py alone (Stage B runs through ebay_sold_browse.py).
        # A missing optional secret must not fail the check.
        errors = []
        try:
            get_ebay_credentials()
            print("[OK] ebay credentials: ok")
        except ConfigError as e:
            errors.append(("ebay", str(e)))
            print("[X] ebay credentials: MISSING")

        for name, getter, note in (
            ("APIFY_API_TOKEN", get_apify_token, "lens_id.py only"),
            ("GEMINI_API_KEY", get_gemini_api_key, "fine_inspect.py only"),
        ):
            try:
                getter()
                print(f"[OK] {name}: ok ({note})")
            except ConfigError:
                print(f"[--] {name}: not set (optional -- {note})")

        if errors:
            print()
            print("Required credentials are missing:")
            for name, msg in errors:
                print(f"  [{name}]")
                for line in msg.splitlines():
                    print(f"    {line}")
            sys.exit(1)


if __name__ == "__main__":
    _cli()
