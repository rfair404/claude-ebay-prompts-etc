"""The store model — one place that answers "which store, and where is its stuff" (GH #156).

A *store* is one eBay seller account plus the business behind it. Two config
trees describe it, and this module is the only thing that has to know both:

    ebay.stores.<name>     credentials, policy IDs, merchant location  (#150)
    storefronts.<name>     identity, terms, pricing posture            (#159)

The store with no name — `ebay.environment/sandbox/production` + top-level
`store:` — is the implicit store "default", so a single-store config keeps
working untouched. Nothing here assumes there are exactly two stores: every
helper takes a store name, and `configured_stores()` is the list to loop over.

What lives here, and why each was previously copy-pasted:

  resolve_store_name()   --store > EBAYBIZ_STORE > ebay.active_store > default.
                         Was duplicated in ebay_client.load_credentials(),
                         config.get_storefront() and list_edit's ledger
                         fallback; three copies of a precedence rule drift.
  store_file()           the per-store file-naming convention. The default
                         store keeps the historic bare name (no migration);
                         a named store gets `<stem>-<store><suffix>`, the
                         shape #158 already wrote for listings_ledger and the
                         one .gitignore covers.
  StorePaths / paths()   every per-store data file in one table, so adding a
                         store never means hunting for a hard-coded filename.
  add_store_args() /     the CLI contract: `--store NAME` on every tool that
  stores_from_args()     touches an account or its data, `--all-stores` on
                         the ones where a loop over stores is meaningful.
  draft_store()          which store a draft belongs to (its `store:` field).

Credentials themselves stay in ebay_client.load_credentials(); storefront
terms stay in config.get_storefront(). Both now resolve the name here.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config as _config
from config import ConfigError

DEFAULT_STORE = "default"
STORE_ENV_VAR = "EBAYBIZ_STORE"

# A store name ends up in filenames, URLs and CLI output. Keep it boring.
STORE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

REPO = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    """config.load_config(), looked up at call time so a test that patches
    either module sees its patch honoured here."""
    return _config.load_config()


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def validate_store_name(name: str) -> str:
    """Return `name` unchanged, or raise ValueError if it can't be a store name."""
    if not name or not STORE_NAME_RE.match(name):
        raise ValueError(
            f"invalid store name {name!r} — use only letters, digits, '-' and '_'")
    return name


def resolve_store_name(explicit: Optional[str] = None) -> str:
    """The one precedence rule: explicit > $EBAYBIZ_STORE > ebay.active_store > "default".

    Does not check that the store is configured — load_credentials() and
    get_storefront() each raise their own, more specific error for that.
    """
    name = (explicit
            or os.environ.get(STORE_ENV_VAR)
            or (load_config().get("ebay") or {}).get("active_store")
            or DEFAULT_STORE)
    return validate_store_name(str(name))


def is_default(store: Optional[str]) -> bool:
    return (store or DEFAULT_STORE) == DEFAULT_STORE


def configured_stores() -> list[str]:
    """Every store this config knows about, "default" first.

    A name counts if it has credentials (`ebay.stores`) OR a business profile
    (`storefronts`) — a store half-onboarded is still a store, and hiding it
    from `--all-stores` would hide the half that's missing.
    """
    cfg = load_config()
    named = set(((cfg.get("ebay") or {}).get("stores") or {}).keys())
    named |= set((cfg.get("storefronts") or {}).keys())
    named.discard(DEFAULT_STORE)
    return [DEFAULT_STORE] + sorted(named)


def store_label(store: Optional[str]) -> str:
    """Short tag for human output: "" for the default store, "[junk]" otherwise.

    The default store stays unlabelled so single-store output is unchanged.
    """
    return "" if is_default(store) else f"[{store}]"


# ---------------------------------------------------------------------------
# Per-store files
# ---------------------------------------------------------------------------

def store_file(path: Path | str, store: Optional[str] = None) -> Path:
    """The per-store variant of a data file.

    default: `path` unchanged       listings_ledger.csv
    named:   <stem>-<store><suffix> listings_ledger-junk.csv

    A relative `path` is taken from the repo root. `store=None` resolves the
    active store — pass a store explicitly wherever one is in hand.
    """
    p = Path(path)
    if not p.is_absolute():
        p = REPO / p
    name = resolve_store_name(store)
    if name == DEFAULT_STORE:
        return p
    return p.with_name(f"{p.stem}-{name}{p.suffix}")


@dataclass(frozen=True)
class StorePaths:
    """Every per-store data file. Add a row here, not a literal in a tool."""
    store: str
    listings_ledger: Path
    sales_ledger: Path
    inventory_sheet_csv: Path
    inventory_sheet_json: Path
    hand_listed_locations: Path
    pick_list_state: Path
    offer_policy_survey: Path
    finances_sync_status: Path
    ebay_ads_json: Path
    sales_dashboard_html: Path
    price_vs_actual_csv: Path
    reconcile_report_json: Path


def paths(store: Optional[str] = None) -> StorePaths:
    """Resolved per-store file paths for `store` (default: the active store).

    $EBAYBIZ_LISTINGS_LEDGER (or legacy $EBAYBIZ_LISTINGS_LOG) still wins for
    the listings ledger — an explicit single-file override, used by tests.
    """
    name = resolve_store_name(store)
    f = lambda rel: store_file(rel, name)  # noqa: E731
    ledger_env = (os.environ.get("EBAYBIZ_LISTINGS_LEDGER")
                  or os.environ.get("EBAYBIZ_LISTINGS_LOG"))
    return StorePaths(
        store=name,
        listings_ledger=Path(ledger_env) if ledger_env else f("listings_ledger.csv"),
        sales_ledger=f("sales_ledger.csv"),
        inventory_sheet_csv=f("inventory_sheet.csv"),
        inventory_sheet_json=f("inventory_sheet.json"),
        hand_listed_locations=f("hand_listed_locations.csv"),
        pick_list_state=f(".pick_list_state.json"),
        offer_policy_survey=f(".offer_policy_survey.json"),
        finances_sync_status=f("reports/finances_sync_status.json"),
        ebay_ads_json=f("reports/ebay_ads.json"),
        sales_dashboard_html=f("reports/sales_dashboard.html"),
        price_vs_actual_csv=f("reports/price_vs_actual.csv"),
        reconcile_report_json=f("ledger_reconcile_report.json"),
    )


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------

def draft_store(draft: Optional[dict]) -> str:
    """Which store a parsed draft belongs to: its `store:` field, else "default".

    Deliberately NOT the active store: a draft with no `store:` was written
    for the main store, and attributing it to whatever $EBAYBIZ_STORE happens
    to say would move items between stores by environment variable.
    """
    v = (draft or {}).get("store")
    return validate_store_name(str(v)) if v else DEFAULT_STORE


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

def add_store_args(parser, *, all_stores: bool = False, help_extra: str = "") -> None:
    """Add `--store NAME` (and optionally `--all-stores`) to an argparse parser.

    Every tool that touches an eBay account or a per-store file takes the same
    flag with the same meaning — "store" means the seller account + business
    (sense 1 of #156 §6), never a storefront URL or a blob store.
    """
    parser.add_argument(
        "--store", metavar="NAME", default=None,
        help=("Which store (eBay seller account + its business) to use. "
              "Default: $EBAYBIZ_STORE, then ebay.active_store in config, then "
              "'default'. " + help_extra).strip())
    if all_stores:
        parser.add_argument(
            "--all-stores", action="store_true",
            help="Run once for every configured store (see lib/stores.py).")


def stores_from_args(args) -> list[str]:
    """The store(s) a parsed-args namespace asks for, resolved and validated."""
    if getattr(args, "all_stores", False):
        if getattr(args, "store", None):
            raise SystemExit("--store and --all-stores are mutually exclusive")
        return configured_stores()
    return [resolve_store_name(getattr(args, "store", None))]


def require_explicit_store(args, action: str) -> str:
    """For bulk writes: refuse to let ambient state pick the account.

    `--store` must be on the command line. The env var and config default are
    fine for reads; for a write they are the invisible lever #156 §4 warns
    about — easy to leave set, easy to forget.
    """
    if not getattr(args, "store", None):
        raise SystemExit(
            f"[X] {action} needs an explicit --store (one of: "
            f"{', '.join(configured_stores())}). $EBAYBIZ_STORE / "
            f"ebay.active_store are not enough for a write.")
    return resolve_store_name(args.store)


__all__ = [
    "DEFAULT_STORE", "STORE_ENV_VAR", "STORE_NAME_RE", "ConfigError",
    "validate_store_name", "resolve_store_name", "is_default",
    "configured_stores", "store_label", "store_file", "StorePaths", "paths",
    "draft_store", "add_store_args", "stores_from_args",
    "require_explicit_store",
]
