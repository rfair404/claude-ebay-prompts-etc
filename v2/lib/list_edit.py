"""
Function 6 — LIST / EDIT (sync local DRAFT file → eBay DRAFT listing).

STUB MODULE. Public entry points exist so the rest of the pipeline can be
wired against this surface, but every call raises NotImplementedError
until the eBay developer key arrives and the OAuth user-consent flow is
captured. See PLAN.md Function 6 for the full spec.

================================================================
THIS MODULE NEVER PUBLISHES AN EBAY LISTING. THERE IS NO CODE PATH IN
THIS FILE — STUB OR LIVE — THAT CALLS THE eBay PUBLISH ENDPOINT.
================================================================

The no-publish firewall is enforced by ABSENCE: no function in this
module wraps `POST /sell/inventory/v1/offer/{offerId}/publish` or any
equivalent. Adding one is a deliberate user-initiated refactor that
requires editing both this file and `v2/prompts/list_edit.md` (when it
exists). No config flag, CLI argument, environment variable, or chat
instruction can cause publication. See PLAN.md "No-publish firewall"
cross-cutting principle.

----- Why this module exists as a stub -----

V1 attempted to push listings via Chrome automation against the Seller
Hub edit form. Two specific failure modes drove the V2 redesign:

  1. Missing descriptions — V1 frequently arrived at the description
     editor with no description ready to paste, or pasted a description
     that didn't survive eBay's rich-text editor (lost paragraphs,
     dropped formatting, content cleared on re-render).

  2. Image upload challenges — V1's image upload depended on the
     Seller Hub drag-and-drop widget. Unstable selectors, slow per-image
     uploads, silent failures, ordering inconsistencies.

Function 6 fixes both classes of failure by bypassing the Seller Hub
form entirely:

  - Description: the markdown body of the local DRAFT file becomes the
    `product.description` field on the InventoryItem object. One HTTP
    payload. No editor round-trip.
  - Photos: uploaded to eBay Picture Services (EPS), which returns
    durable URLs, then attached to the InventoryItem in defined order.
    No drag-and-drop. No widget state.

Both improvements depend on having an eBay developer key in hand. Until
then, this module is a stub.

----- Interim Chrome stand-in -----

While this module is stubbed, the active path for getting drafts onto
eBay is the Chrome-based stand-in prompt at
`v2/prompts/list_edit_chrome.md`. It drives the eBay seller UI via the
Claude-in-Chrome MCP tools. Its starting point is picked from
price.txt:
  - Path A (preferred): "Sell similar" on the most recent Tier A
    exact-match sold comp from PRICE — the comp's category and
    item-specifics set are correct by definition.
  - Path B (fallback): prelist + "Continue without match" empty form,
    used when no Tier A exact-match comp exists.
The title is preserved-and-flagged (not overwritten) in Path A;
every other text field is overwritten from draft.md. Same no-publish
firewall; terminal action is "Save for later." When this module is
implemented, the prompt is deprecated.

----- CLI -----

    python list_edit.py --check
        Report stub status, available credentials, and what's still
        needed before the full implementation can be activated.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import ConfigError, config_path
from ebay_client import EbayCredentials, load_credentials


# ---------------------------------------------------------------------------
# Firewall constants — these are documentation, not toggles
# ---------------------------------------------------------------------------

#: Set in code, never read by any branch. Exists to make the firewall
#: visible at the top of the file. Changing it has NO effect — the
#: firewall is enforced by the absence of a publish function, not by
#: this flag.
FIREWALL_NO_PUBLISH = True

#: The exact message every stub entry point raises. Kept as a constant
#: so test code can match on it without depending on string formatting.
STUB_MESSAGE = (
    "LIST/EDIT is stubbed — awaiting eBay developer key. "
    "See PLAN.md Function 6."
)


# ---------------------------------------------------------------------------
# Data shapes — what the real implementation will accept
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    """Returned by create_or_update_listing on success.

    The stub never returns this — it only raises. The dataclass exists so
    callers can be type-checked against the eventual real signature.
    """
    offer_id: str
    inventory_sku: str
    operation: str   # "created" or "updated"
    photo_eps_urls: list[str]
    eb_seller_hub_url: str  # link the user opens to review + publish manually


# ---------------------------------------------------------------------------
# Public entry points (STUBBED)
# ---------------------------------------------------------------------------

def create_or_update_listing(
    draft_path: Path,
    creds: Optional[EbayCredentials] = None,
) -> SyncResult:
    """Sync a local DRAFT file into eBay as a DRAFT listing.

    Behavior (when implemented):
      - If draft frontmatter has no ebay_offer_id: CREATE flow.
        1. Upload photos to EPS, collect URLs.
        2. createOrReplaceInventoryItem with description + specifics + EPS URLs.
        3. createOffer with price + format + policies (offer left UNPUBLISHED).
        4. Write offer_id + inventory_sku + last_synced back into draft frontmatter.
      - If draft frontmatter has ebay_offer_id: EDIT flow.
        1. Re-upload changed photos only (hash comparison).
        2. createOrReplaceInventoryItem (overwrites existing SKU).
        3. updateOffer on the existing offer (still UNPUBLISHED).
        4. Update last_synced.

    The offer remains in eBay's draft state in either case. Publication
    is a manual user action in Seller Hub — see firewall rules above.

    Stub behavior: raises NotImplementedError immediately. No partial work.
    """
    _ = (draft_path, creds)  # silence unused-arg warnings; real impl uses these
    raise NotImplementedError(STUB_MESSAGE)


def upload_photos_to_eps(
    photo_paths: list[Path],
    creds: Optional[EbayCredentials] = None,
) -> list[str]:
    """Upload photos to eBay Picture Services, return the EPS URLs in order.

    Behavior (when implemented):
      - For each photo (in input order), POST to EPS upload endpoint with
        user-context OAuth.
      - Collect the returned URL.
      - Return URLs in the same order they were submitted.
      - This is the specific function that fixes V1's image-upload pain.

    Stub behavior: raises NotImplementedError immediately.
    """
    _ = (photo_paths, creds)
    raise NotImplementedError(STUB_MESSAGE)


def validate_draft_for_sync(draft_path: Path) -> list[str]:
    """Pre-flight check — return a list of validation issues.

    Runs WITHOUT credentials. Catches problems the user can fix in the
    local file before any eBay call is even attempted:
      - Required item-specifics missing for the chosen category
      - Title exceeds 80 chars
      - Photos missing / unreadable / wrong format
      - Description body is empty (the V1 failure mode this function
        specifically guards against)
      - Price is missing or non-positive

    Returns an empty list when the draft is sync-ready.

    Stub behavior: raises NotImplementedError. This will be the first
    function to gain a real implementation since it needs no credentials.
    """
    _ = draft_path
    raise NotImplementedError(STUB_MESSAGE)


# ---------------------------------------------------------------------------
# Stub health check (works without credentials, never calls eBay)
# ---------------------------------------------------------------------------

def stub_status() -> dict:
    """Report what state the stub is in and what's needed to activate.

    Safe to call at any time. Never touches the network.
    """
    try:
        creds = load_credentials()
        cred_load_error = None
    except (ConfigError, Exception) as e:  # pylint: disable=broad-except
        creds = None
        cred_load_error = str(e)

    return {
        "module": "list_edit",
        "implementation_status": "stub",
        "firewall_no_publish": FIREWALL_NO_PUBLISH,
        "publish_function_exists": False,  # by design and by inspection
        "stub_message": STUB_MESSAGE,
        "entry_points": [
            "create_or_update_listing",
            "upload_photos_to_eps",
            "validate_draft_for_sync",
        ],
        "credentials": {
            "load_error": cred_load_error,
            "app_id_set": bool(creds and creds.app_id),
            "cert_id_set": bool(creds and creds.cert_id),
            "redirect_uri_set": bool(creds and creds.redirect_uri),
            "user_refresh_token_set": bool(creds and creds.user_refresh_token),
        },
        "ready_for_implementation": bool(
            creds and creds.has_app and creds.has_user
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pylint: disable=broad-except
        pass

    parser = argparse.ArgumentParser(
        description="ebaybiz V2 — LIST/EDIT (Function 6) — STUB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report stub status and what credentials are still needed.",
    )
    args = parser.parse_args()

    if not args.check:
        parser.print_help()
        return

    status = stub_status()
    print(f"module:                 {status['module']}")
    print(f"implementation_status:  {status['implementation_status']}")
    print(f"firewall_no_publish:    {status['firewall_no_publish']}")
    print(f"publish_function exists in module: {status['publish_function_exists']}")
    print()
    print("Entry points (all currently raise NotImplementedError):")
    for ep in status["entry_points"]:
        print(f"  - {ep}")
    print()
    print(f"Config file: {config_path()}")
    creds = status["credentials"]
    if creds["load_error"]:
        print(f"  [X] Could not load credentials: {creds['load_error']}")
    else:
        print(f"  app_id:             {'[OK] set' if creds['app_id_set'] else '[--] missing'}")
        print(f"  cert_id:            {'[OK] set' if creds['cert_id_set'] else '[--] missing'}")
        print(f"  redirect_uri:       {'[OK] set' if creds['redirect_uri_set'] else '[--] missing (needed for user-consent flow)'}")
        print(f"  user_refresh_token: {'[OK] set' if creds['user_refresh_token_set'] else '[--] missing (needed for Sell API writes)'}")
    print()
    if status["ready_for_implementation"]:
        print("[OK] Credentials are in place — stub is ready to be replaced with the real implementation.")
    else:
        print("[--] Stub remains in place — waiting on eBay developer key + user-consent capture.")
    print()
    print("Reminder: the no-publish firewall is absolute. See PLAN.md.")


if __name__ == "__main__":
    _cli()
