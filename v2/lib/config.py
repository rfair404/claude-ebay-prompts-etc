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
    <project-root>/v2/lib/config.py  →  <project-root>

Per-setting precedence (highest wins):
    1. Explicit function argument
    2. Environment variable (where applicable)
    3. Config file value
    4. Built-in default

Usage:
    from config import get_apify_token, get_apify_actor, get_profile

    token = get_apify_token()              # raises ConfigError if missing
    actor = get_apify_actor()              # default if not configured
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

DEFAULT_APIFY_ACTOR = "epctex/ebay-scraper"

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

    Layout: <project-root>/v2/lib/config.py
    """
    return Path(__file__).resolve().parent.parent.parent


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


def get_apify_actor() -> str:
    """Apify Actor ID. Precedence: env var > config file > built-in default."""
    env = os.environ.get("APIFY_EBAY_ACTOR")
    if env:
        return env

    config = load_config()
    actor = _nested(config, "apify", "ebay_actor")
    if actor:
        return str(actor)

    return DEFAULT_APIFY_ACTOR


def get_anthropic_key() -> str:
    """Anthropic API key. Precedence: env var > config file > error.

    Raises:
        ConfigError if no key is available anywhere.
    """
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env

    config = load_config()
    key = _nested(config, "anthropic", "api_key")
    if key:
        return str(key)

    raise ConfigError(
        f"Anthropic API key not found.\n"
        f"  Set the ANTHROPIC_API_KEY environment variable, OR\n"
        f"  add this to {config_path()}:\n"
        f"      anthropic:\n"
        f"        api_key: \"<your-key>\""
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
        help="Verify required secrets (Apify token, Anthropic key) load",
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

        print(f"apify.ebay_actor: {get_apify_actor()}")

        try:
            anth_key = get_anthropic_key()
            print(f"anthropic.api_key: {_redact(anth_key)}")
        except ConfigError:
            print("anthropic.api_key: (not set)")

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
        errors = []
        try:
            get_apify_token()
            print("[OK] APIFY_API_TOKEN: ok")
        except ConfigError as e:
            errors.append(("APIFY_API_TOKEN", str(e)))
            print("[X] APIFY_API_TOKEN: MISSING")

        try:
            get_anthropic_key()
            print("[OK] ANTHROPIC_API_KEY: ok")
        except ConfigError as e:
            errors.append(("ANTHROPIC_API_KEY", str(e)))
            print("[X] ANTHROPIC_API_KEY: MISSING")

        if errors:
            print()
            print("Some required secrets are missing:")
            for name, msg in errors:
                print(f"  [{name}]")
                for line in msg.splitlines():
                    print(f"    {line}")
            sys.exit(1)


if __name__ == "__main__":
    _cli()
