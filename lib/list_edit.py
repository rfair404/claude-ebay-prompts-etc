"""
Function 6 — LIST / EDIT (sync local DRAFT file → eBay DRAFT listing).

Reads a `draft.md`, uploads its photos to eBay Picture Services (EPS),
creates/updates an InventoryItem + an UNPUBLISHED Offer, and writes the
eBay IDs back into the draft. The listing ends in eBay's DRAFT state;
the user reviews and publishes manually in Seller Hub.

================================================================
PUBLISHING IS EXPLICIT, MANUAL, AND CONFIRMATION-GATED.
================================================================
--sync NEVER publishes. It creates an UNPUBLISHED offer (a draft) and
stops. Nothing in the build/sync path — not a config flag, env var, or
automated chain — can make a listing go live.

Publishing is a SEPARATE, deliberate command:

    python list_edit.py --publish <shoot-dir>            # DRY RUN (shows what would go live)
    python list_edit.py --publish <shoot-dir> --confirm  # actually publishes
    python list_edit.py --list    <shoot-dir> --confirm  # sync THEN publish (post review-gate)

Without --confirm it is a dry run. `publish_offer()` is the ONLY place
this module calls the publishOffer endpoint, it runs only on a draft you
already synced (has an ebay_offer_id), and it requires --confirm. It is
never invoked by --sync and never triggered automatically.

The firewall's intent — no ACCIDENTAL or AUTOMATIC publication — is
preserved: nothing in the IDENTIFY→DRAFT pipeline publishes, and every
publish path still requires the explicit --confirm guard. What changed
(v3 review-gate): publishing is no longer categorically forbidden to the
agent. After DRAFT, the REVIEW phase (prompts/review.md) presents a
review card and STOPS; only an explicit human approval at that gate lets
the agent run `--list <dir> --confirm` (one step: sync then publish).

----- Why the eBay Sell API (vs the Chrome stand-in) -----

The Chrome stand-in (prompts/list_edit_chrome.md) drives the seller UI
and hits two environment walls for photos: file_upload is sandboxed to
session-shared files, and browsers are granted read-only OS-automation
tier (can't type into the native file dialog). This API path bypasses the
UI entirely — photos POST to EPS server-side, the description is one HTTP
field (no rich-text editor), and everything is headless and repeatable in
any environment, including a scheduled run.

----- One-time setup (see lib/SETUP_EBAY_API.md) -----

Needs in config.yaml under `ebay:`  app_id, cert_id, dev_id, redirect_uri,
user_refresh_token, plus account-specific:  merchant_location_key,
fulfillment_policy_id, payment_policy_id, return_policy_id.
`python list_edit.py --setup-check` verifies them and lists your account's
policy IDs / locations to paste in.

----- CLI -----

    python list_edit.py --validate <shoot-dir|draft.md>   # no creds needed
    python list_edit.py --record <shoot-dir|draft.md>     # DRAFT-time: stamp SKU + ledger record (DRAFTED), no creds
    python list_edit.py --preflight <shoot-dir|draft.md>  # check/auto-fix condition+shipping vs category
    python list_edit.py --review <shoot-dir|draft.md>     # record + preflight + build the REVIEW card (stops; no publish)
    python list_edit.py --setup-check                      # verify creds + policies
    python list_edit.py --sync <shoot-dir|draft.md>        # create/update eBay DRAFT (runs preflight)
    python list_edit.py --list <shoot-dir|draft.md> --confirm  # sync + publish (post review-gate)
    python list_edit.py --offers                           # query ALL offers on the account
    python list_edit.py --withdraw-offer <id> --confirm    # end a live offer (keeps it, UNPUBLISHED)
    python list_edit.py --delete-offer <id> --confirm      # delete an offer (ends listing if live)
    python list_edit.py --delete-item <sku> --confirm      # delete inventory item + ALL its offers
    python list_edit.py --check                             # legacy stub-status report
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from config import ConfigError, config_path, load_config
from draft_io import (Draft, parse_draft, resolve_photo_paths, set_photo_order,
                       update_meta)
from voice_check import check_voice
from ebay_client import (
    DEFAULT_MARKETPLACE,
    EbayAPIError,
    EbayAuthError,
    EbayCredentials,
    api_send,
    create_local_pickup_policy,
    delete_inventory_item,
    delete_offer,
    get_allowed_condition_ids,
    get_condition_names,
    get_category_suggestions,
    get_fulfillment_policies,
    get_inventory_locations,
    get_offer,
    get_offers_for_sku,
    get_payment_policies,
    get_return_policies,
    get_user_access_token,
    iter_inventory_items,
    load_credentials,
    upload_site_hosted_picture,
    withdraw_offer,
)


# No AUTOMATIC/ACCIDENTAL publish: --sync never publishes, the pipeline
# never publishes, and every publish path requires the explicit --confirm
# guard. Publishing is reachable by the agent only after a human approves
# the REVIEW gate (prompts/review.md) — it is no longer categorically refused.
FIREWALL_NO_AUTO_PUBLISH = True
STUB_MESSAGE = "LIST/EDIT is stubbed — awaiting eBay developer key. See PLAN.md Function 6."

CURRENCY = "USD"
_VALID_CONDITIONS = {
    "NEW", "LIKE_NEW", "NEW_OTHER", "NEW_WITH_DEFECTS",
    "MANUFACTURER_REFURBISHED", "CERTIFIED_REFURBISHED", "EXCELLENT_REFURBISHED",
    "VERY_GOOD_REFURBISHED", "GOOD_REFURBISHED", "SELLER_REFURBISHED",
    "USED_EXCELLENT", "USED_VERY_GOOD", "USED_GOOD", "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".bmp"}

# draft item_specifics key -> eBay aspect display name
_ASPECT_NAMES = {
    "brand": "Brand", "type": "Type", "material": "Material", "color": "Color",
    "country_of_origin": "Country/Region of Manufacture", "pattern": "Pattern",
    "theme": "Theme", "style": "Style", "size": "Size", "subject": "Subject",
    "collection": "Collection", "character_family": "Character Family",
    "occasion": "Occasion", "time_period_manufactured": "Time Period Manufactured",
    "finish": "Finish", "department": "Department",
}


@dataclass
class SyncResult:
    offer_id: str
    inventory_sku: str
    operation: str            # "created" or "updated"
    photo_eps_urls: list[str]
    category_id: str
    eb_seller_hub_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_draft_path(target: str | Path) -> Path:
    """Accept a draft.md or a shoot directory; return the draft.md path."""
    p = Path(target)
    if p.is_dir():
        p = p / "draft.md"
    if not p.exists():
        raise FileNotFoundError(f"No draft.md found at {p}")
    return p


# The canonical SKU format: exactly 8 lowercase hex digits (see _canonical_sku).
# Anything else — most commonly a legacy "url-style" label like
# "ebaybiz-sand-dollars" stamped by an older version of the app — is NOT
# canonical. Those slugs predate the sku-keyed sync/ledger scheme and must be
# migrated before they reach eBay (see normalize_draft_identity).
_CANONICAL_SKU_RE = re.compile(r"^[0-9a-f]{8}$")


def _canonical_sku(draft: Draft) -> str:
    """The deterministic canonical SKU for a draft: an 8-hex-digit hash of the
    listing title + shoot folder name.

    - Unique per item: the folder name is unique per item, so two items that
      happen to share a title still get different SKUs.
    - Deterministic per draft: the same draft always hashes to the same SKU,
      so re-syncs stay idempotent.
    - 8 hex digits (32 bits, ~4.3B space) → negligible collision risk at the
      volumes this business lists.
    """
    title = re.sub(r"\s+", " ", str(draft.get("title") or "")).strip().lower()
    folder = draft.path.parent.name or str(draft.get("meta.item_id") or "item")
    basis = f"{title}\n{folder}".encode("utf-8")
    return hashlib.sha1(basis).hexdigest()[:8]


def _sku_for(draft: Draft) -> str:
    """Resolve a draft's SKU. Prefer an already-stamped CANONICAL sku (so a
    live listing keeps its label and re-syncs stay idempotent), but never trust
    a legacy url-style label — recompute the canonical one instead. Drafts from
    an older app version are migrated in place by normalize_draft_identity()
    before record/sync; this is the last-line guard so a stale slug can never
    reach eBay even if normalization was skipped."""
    existing = str(draft.get("meta.ebay_inventory_sku") or "").strip()
    if existing and _CANONICAL_SKU_RE.match(existing):
        return existing
    return _canonical_sku(draft)


def normalize_draft_identity(draft_path: Path) -> dict:
    """Self-heal a draft carried over from an older app version — NO eBay calls.

    Two fixes, both deterministic from the frontmatter alone:
      1. **Legacy url-style SKU** (e.g. "ebaybiz-sand-dollars") → rewritten to
         the canonical 8-hex label. Those slugs don't round-trip through the
         sku-keyed sync/ledger and must never reach eBay.
      2. **Orphaned listing/offer ids** — when the SKU is migrated, the
         ebay_offer_id / ebay_listing_id / published_at it carried belong to the
         OLD label's listing and are now orphaned, so they're cleared. The next
         sync then creates a fresh offer under the correct SKU, and publish
         stamps the real listing id back.

    A missing SKU is just left for record/sync to stamp; an already-canonical
    SKU is left untouched. Returns a report dict the caller can print/log:
    {changed, sku_before, sku_after, cleared:[...], note}."""
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    sku_before = str(draft.get("meta.ebay_inventory_sku") or "").strip()
    canonical = _canonical_sku(draft)
    report = {"changed": False, "sku_before": sku_before or None,
              "sku_after": sku_before or canonical, "cleared": [], "note": ""}

    # Only act on a legacy (present but non-canonical) SKU. Missing → nothing to
    # migrate; canonical → already good (don't churn a live listing's label).
    if not sku_before or _CANONICAL_SKU_RE.match(sku_before):
        return report

    updates: dict[str, str] = {"ebay_inventory_sku": canonical}
    cleared: list[str] = []
    for field in ("ebay_offer_id", "ebay_listing_id", "published_at"):
        if str(draft.get(f"meta.{field}") or "").strip():
            updates[field] = ""          # "" is the cleared convention (see end_listing)
            cleared.append(field)
    note = (f"IDENTITY NORMALIZED: legacy url-style SKU '{sku_before}' -> "
            f"canonical '{canonical}'"
            + (f"; cleared orphaned {', '.join(cleared)}" if cleared else ""))
    notes = str(draft.get("meta.notes") or "")
    updates["notes"] = (notes + " | " + note).strip(" |")
    update_meta(draft_path, updates)
    report.update(changed=True, sku_after=canonical, cleared=cleared, note=note)
    return report


def _to_decimal_str(val: object) -> Optional[str]:
    if val is None or val == "":
        return None
    try:
        d = Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None
    return f"{d:.2f}"


# --- Listings ledger: ONE row per item (keyed by SKU), updated through its
#     lifecycle DRAFTED -> SYNCED -> PUBLISHED -> ENDED/DELETED. The SKU is a
#     deterministic hash (see _sku_for), so the record can be created at DRAFT
#     time and updated in place as the item is published/withdrawn/deleted.

_LEDGER_FIELDS = ["sku", "status", "title", "price", "offer_id", "listing_id",
                  "url", "drafted_at", "synced_at", "published_at", "ended_at",
                  "updated_at"]
_LEDGER_TS_FOR = {"DRAFTED": "drafted_at", "SYNCED": "synced_at",
                  "PUBLISHED": "published_at", "ENDED": "ended_at"}


def _ledger_path() -> Path:
    """Ledger location: $EBAYBIZ_LISTINGS_LEDGER (or legacy $EBAYBIZ_LISTINGS_LOG),
    else <repo>/listings_ledger.csv."""
    env = os.environ.get("EBAYBIZ_LISTINGS_LEDGER") or os.environ.get("EBAYBIZ_LISTINGS_LOG")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "listings_ledger.csv"


def upsert_listing(sku: str, status: str, *, title: str = "", price: str = "",
                   offer_id: str = "", listing_id: str = "", url: str = "") -> Optional[str]:
    """Create or update this item's row in the listings ledger (keyed by SKU).

    Status only advances sensibly: a re-sync of an already-PUBLISHED item
    keeps PUBLISHED; DRAFTED never overwrites a later status; ENDED/DELETED
    are explicit and always apply. Only fields that are provided are written
    (so a later update never blanks an earlier value). Best-effort — never
    raises (ledger bookkeeping must not break a sync/publish)."""
    if not sku:
        return None
    import csv
    try:
        path = _ledger_path()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows: list[dict] = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        row = next((r for r in rows if r.get("sku") == sku), None)
        if row is None:
            row = {k: "" for k in _LEDGER_FIELDS}
            row["sku"] = sku
            rows.append(row)
        for k, v in (("title", title), ("price", price), ("offer_id", offer_id),
                     ("listing_id", listing_id), ("url", url)):
            if v:
                row[k] = str(v)
        cur = row.get("status") or ""
        if status == "DRAFTED":
            row["status"] = cur or "DRAFTED"
        elif status == "SYNCED":
            row["status"] = "PUBLISHED" if cur == "PUBLISHED" else "SYNCED"
        else:                       # PUBLISHED / ENDED / DELETED
            row["status"] = status
        tsfield = _LEDGER_TS_FOR.get(status)
        if tsfield and not row.get(tsfield):
            row[tsfield] = ts
        row["updated_at"] = ts
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_LEDGER_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in _LEDGER_FIELDS})
        return str(path)
    except (OSError, csv.Error):
        return None


def set_hero_photo(draft_path: Path, name: str) -> list[str]:
    """Move one photo to the front of the draft's `photos:` list.

    Entry one is eBay's gallery image — the frame a buyer judges the listing by
    before reading a word — so which photo leads is a decision worth making
    deliberately and cheaply, the same way PREP makes orientation and crop
    deliberate. Everything else keeps its relative order, so promoting a frame
    never silently reshuffles the rest.

    `name` matches on the full entry, the file name, or the stem, case-blind.
    Returns the new order.
    """
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    photos = [str(p) for p in (draft.frontmatter.get("photos") or [])]
    if not photos:
        raise SystemExit(f"{draft_path}: the draft has no photos to reorder.")

    want = name.strip().lower()
    hit = next((p for p in photos
                if want in (p.lower(), Path(p).name.lower(), Path(p).stem.lower())), None)
    if hit is None:
        raise SystemExit(f"no photo matching {name!r} in the draft. Have: "
                         + ", ".join(Path(p).name for p in photos))

    order = [hit] + [p for p in photos if p != hit]
    if order == photos:
        return photos
    set_photo_order(draft_path, order)
    return order


def record_draft(draft_path: Path) -> tuple[str, Optional[str]]:
    """DRAFT-time, no credentials: compute the SKU (deterministic hash of
    title+folder), stamp it into the draft's frontmatter, and create the
    item's ledger record (status DRAFTED). Lets the record exist from the
    moment a title is chosen; sync/publish later update the same row.
    Returns (sku, ledger_path)."""
    draft_path = _resolve_draft_path(draft_path)
    norm = normalize_draft_identity(draft_path)   # self-heal legacy url-style sku / orphaned ids
    if norm["changed"]:
        print(f"  [normalize] {norm['note']}")
    draft = parse_draft(draft_path)
    sku = _sku_for(draft)
    if str(draft.get("meta.ebay_inventory_sku") or "") != sku:
        update_meta(draft_path, {"ebay_inventory_sku": sku})
    ledger = upsert_listing(sku, "DRAFTED", title=str(draft.get("title") or ""),
                            price=_to_decimal_str(draft.get("price")) or "")
    return sku, ledger


def _ebay_extra(field: str) -> Optional[str]:
    """Read an account-specific eBay setting for the ACTIVE environment.

    Prefers ebay.<environment>.<field>; falls back to flat ebay.<field>.
    """
    section = (load_config().get("ebay") or {})
    env = section.get("environment") or "sandbox"
    env_section = section.get(env) or {}
    v = env_section.get(field)
    if v is None:
        v = section.get(field)
    return str(v) if v else None


# ---------------------------------------------------------------------------
# Pre-flight validation (NO credentials needed)
# ---------------------------------------------------------------------------

def validate_draft_for_sync(draft_path: Path) -> list[str]:
    """Return a list of issues that would block a sync. Empty list = ready.

    Runs entirely offline. Catches the problems the user can fix in the
    local file before any eBay call — including the V1 empty-description
    failure mode.
    """
    issues: list[str] = []
    try:
        draft = parse_draft(draft_path)
    except Exception as e:  # parse failure is itself the blocking issue
        return [f"draft parse error: {e}"]

    title = str(draft.get("title") or "")
    if not title:
        issues.append("title: required, empty")
    elif len(title) > 80:
        issues.append(f"title: {len(title)}/80 — exceeds 80 chars")

    price = _to_decimal_str(draft.get("price"))
    if price is None:
        issues.append("price: required, missing or non-numeric")
    elif Decimal(price) <= 0:
        issues.append(f"price: {price} — must be positive")

    qty = draft.get("quantity")
    if not isinstance(qty, int) or qty < 1:
        issues.append(f"quantity: {qty!r} — must be an integer >= 1")

    cond = str(draft.get("condition") or "")
    if not cond:
        issues.append("condition: required, empty")
    elif cond not in _VALID_CONDITIONS:
        issues.append(f"condition: {cond!r} — not a recognized eBay condition enum")

    if not str(draft.get("item_specifics.type") or "").strip():
        issues.append("item_specifics.type: required, empty")

    # Fulfillment mode: a typo must not silently ship a ship-risky item.
    mode = str(draft.get("shipping.fulfillment_mode") or "SHIP").strip().upper()
    if mode not in ("SHIP", "LOCAL_PICKUP"):
        issues.append(f"shipping.fulfillment_mode: {mode!r} — must be SHIP or LOCAL_PICKUP")

    # SKU format: a stamped SKU must be canonical (8 hex). A legacy url-style
    # slug (e.g. "ebaybiz-sand-dollars" from an older app version) is flagged
    # here — `--record`/`--sync` auto-heal it via normalize_draft_identity, but
    # a standalone `--validate` should surface that the draft needs migrating.
    sku = str(draft.get("meta.ebay_inventory_sku") or "").strip()
    if sku and not _CANONICAL_SKU_RE.match(sku):
        issues.append(f"meta.ebay_inventory_sku: {sku!r} — legacy/url-style SKU, "
                      f"not canonical 8-hex; run `--normalize` (or --record/--sync auto-heals)")

    if len(draft.body.strip()) < 20:
        issues.append("description body: empty/too short (V1 missing-description guard)")

    photos = resolve_photo_paths(draft)
    if not photos:
        issues.append("photos: none listed")
    for ph in photos:
        if not ph.exists():
            issues.append(f"photo missing on disk: {ph.name}")
        elif ph.suffix.lower() not in _IMAGE_EXTS:
            issues.append(f"photo not an image type: {ph.name}")

    # numeric structural caps
    for dotpath, cap in (("shipping.weight.major_lb", 3), ("shipping.weight.minor_oz", 2),
                         ("shipping.package_in.length", 5), ("shipping.package_in.width", 5),
                         ("shipping.package_in.depth", 5)):
        v = draft.get(dotpath)
        if v not in (None, "") and len(str(v)) > cap:
            issues.append(f"{dotpath}: {v!r} exceeds maxLen {cap}")

    # In-hand voice (prompts/draft.md, GH #40). Camera-frame language in
    # buyer-visible copy blocks the sync — it is cheap to fix in the file and
    # expensive to fix once live. Soft normalizations are warnings and do not
    # block; run `python lib/voice_check.py --audit inventory/ --warnings`.
    issues.extend(check_voice(draft))

    return issues


# ---------------------------------------------------------------------------
# Photo upload
# ---------------------------------------------------------------------------

def _assert_photos_cleared(photo_paths: list[Path]) -> None:
    """The PREP gate, enforced in code at the point of no return.

    Every path that puts a photo on eBay funnels through `upload_photos_to_eps`,
    so this is the one place worth guarding. A prompt instruction is not a
    control: the sideways-photo incident happened with the rules already written
    down. This refuses to upload photos that were not prepped AND explicitly
    approved — the same shape as the `--confirm` guard on publishing.

    Shoots with no PREP manifest at all are legacy (photos came from the old
    `no-exif/` chain). Those are refused too, with the command to fix them,
    because "every image goes through the filter" is the whole point.
    """
    if not photo_paths:
        return
    from photo_prep.prep import PrepGateError, assert_approved

    # `listing/` is PREP's output; the rest are the old chain's intermediates.
    # All of them sit one level under the shoot, so the shoot is their parent —
    # otherwise the error tells you to go and prep `no-exif/` itself.
    _SUBDIRS = {"listing", "no-exif", "evened", "trimmed", "cropped", ".orig"}
    shoots = {p.parent.parent if p.parent.name in _SUBDIRS else p.parent
              for p in photo_paths}
    for shoot in sorted(shoots):
        try:
            assert_approved(shoot)
        except PrepGateError as e:
            raise SystemExit(f"[PREP GATE] {e}") from None


def upload_photos_to_eps(photo_paths: list[Path],
                         creds: Optional[EbayCredentials] = None) -> list[str]:
    """Upload each photo to EPS (in order); return EPS URLs in the same order."""
    _assert_photos_cleared(photo_paths)
    creds = creds or load_credentials()
    urls: list[str] = []
    for ph in photo_paths:
        data = Path(ph).read_bytes()
        urls.append(upload_site_hosted_picture(data, picture_name=Path(ph).stem, creds=creds))
    return urls


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _build_aspects(draft: Draft) -> dict[str, list[str]]:
    aspects: dict[str, list[str]] = {}
    spec = draft.frontmatter.get("item_specifics") or {}
    for key, name in _ASPECT_NAMES.items():
        val = spec.get(key)
        if isinstance(val, str) and val.strip():
            aspects[name] = [val.strip()]
    for k, v in (spec.get("extra") or {}).items():
        if isinstance(v, str) and v.strip():
            aspects[str(k)] = [v.strip()]
    return aspects


def _build_weight_lb(draft: Draft) -> Optional[float]:
    lb = draft.get("shipping.weight.major_lb")
    oz = draft.get("shipping.weight.minor_oz") or 0
    try:
        total = float(lb) + float(oz) / 16.0
    except (TypeError, ValueError):
        return None
    return round(total, 2) if total > 0 else None


def _md_inline(text: str) -> str:
    """Escape HTML, then render the inline markdown the templates use."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # [label](http(s)://url) -> anchor. Runs after HTML-escape so the generated
    # tag survives; only http(s) targets allowed (eBay permits links to other
    # eBay listings/pages within a description).
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _body_to_html(md: str) -> str:
    """Convert the markdown subset used in draft bodies (ATX headings, `-`/`*`
    bullet lists, **bold**, blank-line paragraphs) into HTML.

    eBay renders `product.description` / `listingDescription` as HTML, so a raw
    markdown body collapses into one run-on paragraph (newlines and `#`/`-`
    markers are ignored). This restores the intended structure. Stdlib-only —
    no markdown dependency, matching the rest of lib/.

    A wrapped line CONTINUES what it is wrapping. Draft bodies are written at a
    sane column width, so a long bullet spills onto the next line — and that
    continuation line does not start with `-`. Treating it as a new block was
    the bug a buyer saw: the list closed after the first line, the remainder of
    the sentence became its own paragraph, and the next bullet opened a fresh
    list. On a live listing it read as line breaks landing mid-sentence.

    So a non-blank line that starts no new construct is appended to whatever is
    currently open — the bullet, or the paragraph — which is also what standard
    markdown does with lazy continuation.
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_ul = False
    para: list[str] = []
    li: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + " ".join(_md_inline(p) for p in para) + "</p>")
            para.clear()

    def flush_li() -> None:
        if li:
            out.append("<li>" + " ".join(_md_inline(x) for x in li) + "</li>")
            li.clear()

    def close_ul() -> None:
        nonlocal in_ul
        flush_li()
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            flush_para()
            close_ul()
            continue
        m_h = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        m_li = re.match(r"^[-*]\s+(.*)$", stripped)
        if m_h:
            flush_para()
            close_ul()
            level = min(len(m_h.group(1)) + 1, 4)   # "#" -> h2, "##" -> h3, ...
            out.append(f"<h{level}>{_md_inline(m_h.group(2))}</h{level}>")
        elif m_li:
            flush_para()
            flush_li()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            li.append(m_li.group(1))
        elif li:
            li.append(stripped)          # the bullet wraps — keep it in the bullet
        else:
            close_ul()
            para.append(stripped)
    flush_para()
    close_ul()
    return "\n".join(out)


def _build_inventory_item(draft: Draft, image_urls: list[str]) -> dict:
    item: dict = {
        "availability": {"shipToLocationAvailability": {"quantity": int(draft.get("quantity") or 1)}},
        "condition": str(draft.get("condition")),
        "product": {
            "title": str(draft.get("title")),
            "description": _body_to_html(draft.body),
            "imageUrls": image_urls,
            "aspects": _build_aspects(draft),
        },
    }
    cond_desc = str(draft.get("condition_description") or "").strip()
    if cond_desc and item["condition"] != "NEW":
        item["conditionDescription"] = cond_desc[:1000]
    upc = str(draft.get("item_specifics.upc") or "").strip()
    if upc:
        item["product"]["upc"] = [upc]

    weight = _build_weight_lb(draft)
    length = draft.get("shipping.package_in.length")
    width = draft.get("shipping.package_in.width")
    depth = draft.get("shipping.package_in.depth")
    pkg: dict = {}
    if weight:
        pkg["weight"] = {"value": weight, "unit": "POUND"}
    if all(x not in (None, "", 0) for x in (length, width, depth)):
        pkg["dimensions"] = {"length": float(length), "width": float(width),
                             "height": float(depth), "unit": "INCH"}
    if pkg:
        # packageType is REQUIRED for CALCULATED shipping (which includes every
        # international/eIS quote). Domestic free-flat-rate never needed it, so
        # it was omitted for months; moving a flat-rate media offer onto a
        # calculated policy surfaced it as error 25002 / err:216314
        # "Please provide a valid Shipping Package type".
        pkg["packageType"] = _package_type(draft)
        item["packageWeightAndSize"] = pkg
    return item


def _package_type(draft: Draft) -> str:
    """eBay packageType for this item, inferred from the packed dimensions.

    Only a rate-calculation hint — eBay uses weight+dims for the actual quote —
    but it must be present and VALID or calculated/international shipping is
    rejected.

    MAILING_BOX is deliberately NOT used. eBay rejected it on every attempt
    with `errorId 25101 — Invalid <ShippingPackage> (err:216305|MailingBoxes)`,
    including on an obvious box (a 10x8x2 hardcover). PACKAGE_THICK_ENVELOPE
    was accepted for the same item. Only these two values are known-good on
    this account, so those are the only two emitted.
    """
    try:
        dims = sorted(float(draft.get(f"shipping.package_in.{k}") or 0)
                      for k in ("length", "width", "depth"))
    except (TypeError, ValueError):
        return "PACKAGE_THICK_ENVELOPE"
    # flat AND large: catalogs, magazines, LPs — the one case for LARGE_ENVELOPE
    if dims and dims[-1] >= 12 and dims[0] <= 1:
        return "LARGE_ENVELOPE"
    return "PACKAGE_THICK_ENVELOPE"


def _build_offer(draft: Draft, sku: str, category_id: str,
                 location_key: str, policies: dict) -> dict:
    price = _to_decimal_str(draft.get("price"))
    fmt = str(draft.get("format") or "FIXED_PRICE").upper()
    offer: dict = {
        "sku": sku,
        "marketplaceId": DEFAULT_MARKETPLACE,
        "format": fmt,
        "availableQuantity": int(draft.get("quantity") or 1),
        "categoryId": str(category_id),
        "listingDescription": _body_to_html(draft.body),
        "merchantLocationKey": location_key,
        "listingPolicies": {
            "fulfillmentPolicyId": policies["fulfillment"],
            "paymentPolicyId": policies["payment"],
            "returnPolicyId": policies["return"],
        },
    }
    if fmt == "AUCTION":
        # Auctions: the start bid goes in auctionStartPrice (NOT price); a
        # listingDuration is REQUIRED (DAYS_1..DAYS_10); availableQuantity is
        # rejected (always 1 implicitly); Best Offer is not permitted. eBay also
        # forbids immediate-payment on auctions, so use the auction-specific
        # payment policy (no immediate pay) when configured.
        offer.pop("availableQuantity", None)
        offer["pricingSummary"] = {
            "auctionStartPrice": {"value": price, "currency": CURRENCY}
        }
        offer["listingDuration"] = str(draft.get("listing_duration") or "DAYS_7").upper()
        if policies.get("payment_auction"):
            offer["listingPolicies"]["paymentPolicyId"] = policies["payment_auction"]
    else:
        offer["pricingSummary"] = {"price": {"value": price, "currency": CURRENCY}}
        if draft.get("best_offer.enabled"):
            terms: dict = {"bestOfferEnabled": True}
            decline = _to_decimal_str(draft.get("best_offer.auto_decline_amount"))
            accept = _to_decimal_str(draft.get("best_offer.auto_accept_amount"))
            if decline:
                terms["autoDeclinePrice"] = {"value": decline, "currency": CURRENCY}
            if accept:
                terms["autoAcceptPrice"] = {"value": accept, "currency": CURRENCY}
            offer["listingPolicies"]["bestOfferTerms"] = terms
    return offer


# ---------------------------------------------------------------------------
# Account prerequisites
# ---------------------------------------------------------------------------

def _resolve_policies_and_location(creds: EbayCredentials) -> tuple[dict, str]:
    """Read the required policy IDs + merchant location from config.

    These are account-specific and captured once (see --setup-check).
    Raises EbayAuthError with guidance if any is missing.
    """
    missing = []
    policies = {
        "fulfillment": _ebay_extra("fulfillment_policy_id"),
        "payment": _ebay_extra("payment_policy_id"),
        "return": _ebay_extra("return_policy_id"),
    }
    # Optional: a Media Mail fulfillment policy used for true media items
    # (books, sheet music, recordings, computer media). NOT magazines /
    # catalogs / anything carrying advertising — periodicals are excluded from
    # Media Mail (DMM 173.4.2). Not required — falls back to default.
    policies["fulfillment_media"] = _ebay_extra("fulfillment_policy_id_media")
    # Optional: a Local-pickup-only policy used for ship-risky items (fragile /
    # oversized). Not required — only items the user marks LOCAL_PICKUP need it.
    policies["fulfillment_local_pickup"] = _ebay_extra("fulfillment_policy_id_local_pickup")
    # Optional: an international (eBay International Shipping) policy — Worldwide
    # shipToLocations, no region exclusions. Only items with
    # `shipping.international: true` use it, and only if they clear the
    # dangerous-goods gate in _international_blockers().
    policies["fulfillment_international"] = _ebay_extra("fulfillment_policy_id_international")
    # Optional: a payment policy WITHOUT immediate-payment, required for AUCTION
    # offers (eBay forbids immediate-pay on auctions — error 25003). Only auction
    # listings need it; falls back to the default payment policy otherwise.
    policies["payment_auction"] = _ebay_extra("payment_policy_id_auction")
    location = _ebay_extra("merchant_location_key")
    for k, v in policies.items():
        if k in ("fulfillment_media", "fulfillment_local_pickup", "payment_auction"):
            continue  # optional
        if not v:
            missing.append(f"ebay.{k}_policy_id")
    if not location:
        missing.append("ebay.merchant_location_key")
    if missing:
        raise EbayAuthError(
            "Missing account-specific settings in config.yaml: "
            + ", ".join(missing)
            + "\n  Run `python list_edit.py --setup-check` to list your account's "
              "policy IDs and locations, then paste them under `ebay:` in config."
        )
    return policies, location


def _find_offer_id_for_sku(sku: str, creds: EbayCredentials) -> Optional[str]:
    """Return the existing API offerId for this SKU, or None.

    Authoritative source for update-vs-create — do NOT trust
    meta.ebay_offer_id, which the Chrome stand-in may have set to a UI
    draftId (a different ID space than the Sell API offerId).
    """
    try:
        d = api_send("GET", f"/sell/inventory/v1/offer?sku={urllib.parse.quote(sku)}", creds=creds)
    except EbayAPIError as e:
        if e.status == 404:
            return None
        raise
    offers = d.get("offers") or []
    return str(offers[0]["offerId"]) if offers and offers[0].get("offerId") else None


def _resolve_category_id(draft: Draft, creds: EbayCredentials) -> str:
    explicit = str(draft.get("category_id") or "").strip()
    if explicit:
        return explicit
    title = str(draft.get("title") or "")
    data = get_category_suggestions(title, creds=creds)
    suggestions = data.get("categorySuggestions") or []
    if not suggestions:
        raise EbayAPIError(0, f"No category suggestion for title {title!r}; set category_id in draft.md", None)
    return str(suggestions[0]["category"]["categoryId"])


# ---------------------------------------------------------------------------
# Pre-publish preflight — validate condition + shipping against the category
# ---------------------------------------------------------------------------

# eBay condition enum <-> numeric conditionId (the metadata API speaks ids).
_COND_ENUM_TO_ID = {
    "NEW": 1000, "LIKE_NEW": 2750, "NEW_OTHER": 1500, "NEW_WITH_DEFECTS": 1750,
    "MANUFACTURER_REFURBISHED": 2000, "CERTIFIED_REFURBISHED": 2000,
    "EXCELLENT_REFURBISHED": 2010, "VERY_GOOD_REFURBISHED": 2020,
    "GOOD_REFURBISHED": 2030, "SELLER_REFURBISHED": 2500,
    "USED_EXCELLENT": 3000, "USED_VERY_GOOD": 4000, "USED_GOOD": 5000,
    "USED_ACCEPTABLE": 6000, "FOR_PARTS_OR_NOT_WORKING": 7000,
}
_COND_ID_TO_ENUM = {
    1000: "NEW", 1500: "NEW_OTHER", 1750: "NEW_WITH_DEFECTS", 2750: "LIKE_NEW",
    2000: "CERTIFIED_REFURBISHED", 2010: "EXCELLENT_REFURBISHED",
    2020: "VERY_GOOD_REFURBISHED", 2030: "GOOD_REFURBISHED", 2500: "SELLER_REFURBISHED",
    3000: "USED_EXCELLENT", 4000: "USED_VERY_GOOD", 5000: "USED_GOOD",
    6000: "USED_ACCEPTABLE", 7000: "FOR_PARTS_OR_NOT_WORKING",
}
# The new-ladder rungs resolve to a name only via the per-category lookup
# (ebay_client.get_condition_names); 2990/3010 are deliberately NOT added to the
# global table above, because their meaning is category-dependent.
# eBay's NEWER three-rung used ladder, used by jewelry and a growing set of
# categories. Our enum ids (3000/4000/5000/6000) predate it and do not line up:
# on this ladder id 3000 is "Pre-owned - Good", so an item we grade
# USED_EXCELLENT is ADVERTISED as "Pre-owned - Good" — a rung below what it is —
# and 2990 ("Pre-owned - Excellent") cannot be reached at all, because the Sell
# API takes an enum NAME (see the payload build) and we have no name for 2990.
# Measured live on categories 262008 and 262011.
#
# NOT fixed here on purpose. Reaching 2990 needs the Sell API's own enum for it
# (likely PRE_OWNED_EXCELLENT) verified against eBay's current enum list, not
# guessed — a wrong enum fails the publish outright, and these run against LIVE
# listings. Until then the reporting is at least honest: the preflight prints
# the label the BUYER sees, from the per-category lookup, so nobody reads
# "USED_EXCELLENT" and assumes that is what is on the page.
_NEW_USED_LADDER = {2990, 3000, 3010}

_COND_FAMILIES = (
    [1000, 1500, 1750, 2750],          # new-ish
    [2000, 2010, 2020, 2030, 2500],    # refurbished
    [3000, 4000, 5000, 6000],          # used grades
    [7000],                            # for parts
)


def _remap_condition_for_category(enum: str, allowed_ids: set[int]) -> tuple[str, Optional[str]]:
    """Return (condition_enum, change_reason). reason is None if unchanged.

    Picks the closest accepted condition in the SAME family (used->used,
    new->new). Raises if the category accepts nothing in that family (a real
    mismatch the user must resolve — e.g. listing a used item in a new-only
    category).
    """
    cid = _COND_ENUM_TO_ID.get(enum)
    if cid is None or not allowed_ids or cid in allowed_ids:
        return enum, None  # unknown enum, no metadata, or already valid
    family = next((f for f in _COND_FAMILIES if cid in f), [])
    candidates = [i for i in family if i in allowed_ids]
    if not candidates:
        allowed_names = ", ".join(_COND_ID_TO_ENUM.get(i, str(i)) for i in sorted(allowed_ids))
        raise EbayAPIError(
            0, f"condition {enum} is invalid for this category and no same-grade "
               f"alternative is accepted. Category accepts: {allowed_names}. "
               f"Fix the draft's condition or category_id.", None)
    # Prefer generic "Used" (3000) for used items; else the nearest accepted id.
    target = 3000 if (3000 in candidates and cid in _COND_FAMILIES[2]) else \
        min(candidates, key=lambda i: abs(i - cid))
    new_enum = _COND_ID_TO_ENUM[target]
    return new_enum, (f"condition {enum} not accepted by category "
                      f"(accepts {sorted(allowed_ids)}) -> remapped to {new_enum}")


def _set_draft_condition(draft_path: Path, new_enum: str) -> None:
    """Rewrite the top-level `condition:` line in the draft file."""
    text = draft_path.read_text(encoding="utf-8")
    new_text = re.sub(r'(?m)^(condition:\s*).*$', f'condition: "{new_enum}"', text, count=1)
    if new_text != text:
        draft_path.write_text(new_text, encoding="utf-8")


def _is_media_service(code: str) -> bool:
    """True if a shipping service code denotes USPS Media Mail."""
    c = (code or "").lower()
    return "media" in c  # USPSMedia / USPSMediaMail


# Items eBay International Shipping REFUSES at the US hub. Offering
# international on one of these yields a cancelled order plus a seller defect,
# so these hard-block.
#
# Matched against the TITLE and item_specifics (type/material) ONLY — never the
# body or condition text. That distinction is the whole design: condition prose
# is full of words that look like hazmat but describe appearance. Scanning the
# body flagged 46 of 209 drafts, essentially all false: "no PAINT loss" ->
# flammable liquid, "LIGHTER shade" -> butane, a ceramic hen's "FEATHER"
# detail -> CITES. Titles name what a thing IS; descriptions say what it looks
# like.
_DANGEROUS_GOODS_PATTERNS = (
    # "lighter" is an adjective at least as often as a noun in this inventory
    # ("Lighter Blue Glaze", "lighter weight wool"), and an optional qualifier
    # group made the bare word match — hard-blocking eIS on innocuous titles.
    # Require either a qualifier, or the word used as a noun (not immediately
    # followed by a colour/comparative).
    (r"\bbutane\b|\blighter fluid\b|\bzippo\b"
     r"|\b(cigarette|pocket|torch|butane|gas)\s+lighter\b"
     r"|\blighters?\b(?!\s+(blue|green|red|brown|gray|grey|tan|shade|color|colour|"
     r"weight|wood|finish|tone|version|than))",
     "lighter / butane (flammable — eIS prohibited)"),
    (r"\blithium\b|\bli-ion\b|\bpower ?bank\b", "lithium battery (UN3480)"),
    (r"\baerosol\b|\bspray paint\b|\bcompressed gas\b|\bpropane\b",
     "aerosol / compressed gas"),
    (r"\bperfume\b|\bcologne\b|\beau de (toilette|parfum)\b",
     "alcohol-based fragrance (flammable)"),
    (r"\bgunpowder\b|\bammunition\b|\bammo\b|\bprimers\b|\bblack powder\b|\bflare\b",
     "explosives / ammunition components"),
)

# Ambiguous in an antiques catalog: these words usually name a FORM, a COLOR or
# a period style rather than the actual regulated substance — an empty antique
# "whiskey jug" is stoneware, and "Ivory Cream" is a paint colour on faux grips.
# Blocking on them was wrong 3 times out of 4 against real inventory, so they
# WARN and ask for a human call instead.
_DG_REVIEW_PATTERNS = (
    (r"\bivory\b|\btortoise ?shell\b|\btaxidermy\b|\bwhalebone\b|\bscrimshaw\b|\bhorn\b",
     "possible CITES / wildlife material — confirm it's faux or synthetic"),
    (r"\bwine\b|\bwhiskey\b|\bwhisky\b|\bbourbon\b|\bliqueur\b|\bbeer\b",
     "alcohol wording — confirm the vessel is EMPTY (an empty antique jug is fine)"),
    (r"\bknife\b|\bknives\b|\bdagger\b|\bsword\b|\brazor\b",
     "bladed item — legal in most destinations but some refuse; check before shipping"),
)

# If any of these appear, the material words above are describing an imitation,
# so don't even raise the CITES review flag.
_FAUX_RE = re.compile(r"\bfaux\b|\bimitation\b|\bsimulated\b|\brepro(duction)?\b"
                      r"|\bresin\b|\bcelluloid\b|\bbakelite\b|\bplastic\b|\bstyle\b",
                      re.I)

_INTL_WEIGHT_WARN_OZ = 5 * 16   # ~5 lb


def _international_blockers(draft: Draft) -> list[str]:
    """Hard reasons this item cannot ship internationally. Empty = clear.

    Scans title + item_specifics only (see _DANGEROUS_GOODS_PATTERNS).
    """
    parts = [str(draft.get("title") or "")]
    for k in ("item_specifics.type", "item_specifics.material",
              "item_specifics.subject"):
        parts.append(str(draft.get(k) or ""))
    haystack = " ".join(parts)
    return [label for pattern, label in _DANGEROUS_GOODS_PATTERNS
            if re.search(pattern, haystack, re.I)]


def _international_warnings(draft: Draft) -> list[str]:
    """Soft flags — surfaced at REVIEW, do NOT block. Judgement calls where the
    right answer depends on the item, not a rule."""
    out = []
    parts = [str(draft.get("title") or "")]
    for k in ("item_specifics.type", "item_specifics.material",
              "item_specifics.subject"):
        parts.append(str(draft.get(k) or ""))
    haystack = " ".join(parts)
    faux = bool(_FAUX_RE.search(haystack))
    for pattern, label in _DG_REVIEW_PATTERNS:
        if re.search(pattern, haystack, re.I):
            if faux and "CITES" in label:
                continue          # "Faux Stag ... Ivory Cream" is not ivory
            out.append(label)
    try:
        lb = int(draft.get("shipping.weight.major_lb") or 0)
        oz = int(draft.get("shipping.weight.minor_oz") or 0)
        if lb * 16 + oz > _INTL_WEIGHT_WARN_OZ:
            out.append(f"heavy ({lb} lb {oz} oz) — international postage may exceed "
                       f"item value; worth checking before it sells")
    except (TypeError, ValueError):
        pass
    if str(draft.get("shipping.fulfillment_mode") or "").upper() == "LOCAL_PICKUP":
        out.append("item is flagged ship-risky (local pickup) — fragile/oversized "
                   "pieces travel badly through the eIS hub")
    return out


def _resolve_shipping_policy(draft: Draft, policies: dict,
                             creds: EbayCredentials) -> tuple[str, list[str]]:
    """Choose the fulfillment policy for THIS item and validate it.

    Routing, in order:
    1. If the draft is `fulfillment_mode: LOCAL_PICKUP` (DRAFT sets this for
       ship-risky items the user confirmed as local-pickup), use the
       local-pickup policy.
    2. Else if `shipping.international: true` AND the item clears the
       dangerous-goods / weight gate, use the international (eIS) policy.
    3. Else if `primary_service` is Media Mail (DRAFT sets this for books /
       sheet music / recordings / computer media — NOT magazines, catalogs, or
       anything carrying advertising, which are excluded from Media Mail by
       DMM 173.4.2) AND a media policy is configured, use it.
    4. Else use the default (USPS Ground) policy.
    Returns (chosen_fulfillment_id, messages).

    International is OPT-IN per item, never global. eBay International Shipping
    is an account-level enrollment, so the moment a listing uses a policy whose
    shipToLocations include Worldwide, eIS offers it abroad — including for
    items eIS will refuse at the hub. The gate below is what stops that.
    """
    default_fid = str(policies.get("fulfillment") or "")
    media_fid = str(policies.get("fulfillment_media") or "")
    pickup_fid = str(policies.get("fulfillment_local_pickup") or "")
    intl_fid = str(policies.get("fulfillment_international") or "")
    mode = str(draft.get("shipping.fulfillment_mode") or "SHIP").strip().upper()
    want = str(draft.get("shipping.primary_service") or "").strip()
    wants_intl = str(draft.get("shipping.international") or "").strip().lower() in (
        "true", "yes", "1", "on")
    msgs: list[str] = []

    if wants_intl and mode == "LOCAL_PICKUP":
        wants_intl = False
        msgs.append("shipping: international requested but item is LOCAL_PICKUP — "
                    "ignored (a pickup-only item has nothing to ship).")
    if wants_intl:
        blockers = _international_blockers(draft)
        if blockers:
            wants_intl = False
            msgs.append("shipping: INTERNATIONAL REFUSED — " + "; ".join(blockers) +
                        ". eBay International Shipping rejects these at the US hub, "
                        "which cancels the order and books a seller defect. Listing "
                        "domestic-only. Override by clearing the matched wording or "
                        "setting the policy id by hand if you know it's safe.")
        elif not intl_fid:
            wants_intl = False
            msgs.append("shipping: international requested but "
                        "ebay.fulfillment_policy_id_international is unset — "
                        "listing domestic-only.")

    if mode == "LOCAL_PICKUP":
        if pickup_fid:
            chosen, label = pickup_fid, "local pickup (pickup-only policy)"
        else:
            chosen, label = default_fid, "default ground (NO local-pickup policy configured)"
            msgs.append("shipping: item is LOCAL_PICKUP but ebay.fulfillment_policy_id_local_pickup "
                        "is unset — falling back to the default ground policy. Add a local-pickup "
                        "policy (see SETUP_EBAY_API.md) so ship-risky items list as pickup-only.")
    elif wants_intl:
        chosen, label = intl_fid, "international (eIS-enabled policy)"
        for w in _international_warnings(draft):
            msgs.append(f"shipping: INTERNATIONAL REVIEW — {w}")
        if _is_media_service(want):
            # USPS Media Mail is a domestic-only service, so an international
            # policy can't carry it. Going international costs the media rate.
            msgs.append("shipping: item wanted Media Mail but international was "
                        "requested — Media Mail is DOMESTIC-ONLY, so this uses "
                        "Ground Advantage domestically. If the media rate matters "
                        "more than international reach, set shipping.international "
                        "false, or create a media+worldwide policy.")
    elif _is_media_service(want):
        if media_fid:
            chosen, label = media_fid, "Media Mail (media policy)"
        else:
            chosen, label = default_fid, "default ground (NO media policy configured)"
            msgs.append("shipping: item wants Media Mail but ebay.fulfillment_policy_id_media "
                        "is unset — using default ground policy. Add a Media Mail policy to save on media.")
    else:
        chosen, label = default_fid, "default ground policy"

    # Validate the chosen policy actually exists and ships.
    try:
        pols = get_fulfillment_policies(creds=creds)
        pol = next((p for p in pols if str(p.get("fulfillmentPolicyId")) == chosen), None)
        if not pol:
            msgs.append(f"shipping: chosen fulfillment_policy_id {chosen} not found on this account")
        else:
            offered = [s.get("shippingServiceCode")
                       for opt in (pol.get("shippingOptions") or [])
                       for s in (opt.get("shippingServices") or []) if s.get("shippingServiceCode")]
            # A local-pickup-only / freight policy legitimately carries no parcel
            # shippingServices — pickup/freight are top-level flags, not services.
            pickup_or_freight = bool(pol.get("localPickup") or pol.get("freightShipping"))
            if not offered and not pickup_or_freight:
                msgs.append(f"shipping: policy '{pol.get('name')}' offers no services — publish may fail")
            else:
                desc = ", ".join(offered) if offered else (
                    "local pickup" if pol.get("localPickup") else "freight")
                msgs.insert(0, f"shipping: using {label} -> '{pol.get('name')}' ({desc})")
    except (EbayAPIError, EbayAuthError) as e:
        msgs.append(f"shipping: could not verify fulfillment policy ({e})")
    return chosen, msgs


def _insurance_notes(draft: Draft) -> list[str]:
    """Remind to insure high-value items — only $100 is auto-included, and the
    eBay API can't set insurance (it's bought at label time via ShipCover)."""
    price = _to_decimal_str(draft.get("price"))
    try:
        if price and Decimal(price) > Decimal("100"):
            return [f"insurance: price ${price} > $100 — only $100 ships included; "
                    f"add ShipCover/added coverage when buying the label (Seller Hub -> "
                    f"Get shipping label -> Additional liability coverage)."]
    except InvalidOperation:
        pass
    return []


def preflight_listing(draft_path: Path, creds: Optional[EbayCredentials] = None,
                      apply: bool = True) -> list[str]:
    """Check (and, if apply=True, auto-correct) a draft against its eBay
    category BEFORE sync/publish: condition validity + shipping. Returns a
    list of human-readable messages. Safe to run repeatedly (idempotent)."""
    creds = creds or load_credentials()
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    category_id = _resolve_category_id(draft, creds)
    policies, _loc = _resolve_policies_and_location(creds)
    msgs: list[str] = [f"category: {category_id}"]

    # Condition vs category
    allowed, required = get_allowed_condition_ids(category_id, creds=creds)
    cur = str(draft.get("condition") or "")
    if not allowed:
        msgs.append(f"condition: {cur} (category condition metadata unavailable — left as-is)")
    else:
        names = get_condition_names(category_id, creds=creds)
        new_enum, reason = _remap_condition_for_category(cur, allowed)
        shown = names.get(_COND_ENUM_TO_ID.get(new_enum, -1))
        if reason:
            # Say what the BUYER will see, not what our enum is called: the two
            # disagree on every category using eBay's three-tier used ladder.
            msgs.append(reason + (f' -> buyer sees "{shown}"' if shown else ""))
            if apply:
                _set_draft_condition(draft_path, new_enum)
                draft.frontmatter["condition"] = new_enum
                notes = str(draft.get("meta.notes") or "")
                update_meta(draft_path, {"notes": (notes + f" | PREFLIGHT: {reason}").strip(" |")})
        elif shown:
            msgs.append(f'condition: {cur} OK for category -> buyer sees "{shown}"')
        else:
            msgs.append(f"condition: {cur} OK for category")

    # Shipping policy selection (media vs ground) + validation
    _chosen, ship = _resolve_shipping_policy(draft, policies, creds)
    msgs.extend(ship)
    # Insurance reminder for high-value items
    msgs.extend(_insurance_notes(draft))
    return msgs


# ---------------------------------------------------------------------------
# REVIEW — one step: record + preflight + assemble the decision card
# ---------------------------------------------------------------------------

def build_review_card(draft_path: Path,
                      creds: Optional[EbayCredentials] = None) -> tuple[str, str]:
    """Prepare an item for the REVIEW gate in ONE step and return
    (card_text, card_path). Does NOT publish.

    It (1) ensures the item is recorded (SKU + DRAFTED ledger row),
    (2) runs preflight (condition remap + shipping policy + insurance flag),
    and (3) assembles the decision card deterministically from the draft,
    comps, ledger, and preflight — written to <shoot>/review_card.md.
    """
    import csv
    creds = creds or load_credentials()
    draft_path = _resolve_draft_path(draft_path)
    shoot = draft_path.parent

    sku, _ = record_draft(draft_path)                  # ensure SKU + DRAFTED
    pf = preflight_listing(draft_path, creds=creds)    # remap + shipping + insurance
    draft = parse_draft(draft_path)                    # re-read (condition may have changed)

    title = str(draft.get("title") or "")
    price = str(draft.get("price") or "")
    cond = str(draft.get("condition") or "")
    cond_desc = str(draft.get("condition_description") or "").strip() or "(none)"
    qty = draft.get("quantity") or 1
    photos = draft.frontmatter.get("photos") or []
    hero = photos[0] if photos else "—"
    if draft.get("best_offer.enabled"):
        _decl = draft.get("best_offer.auto_decline_amount")
        bo = f"on @ auto-decline ${_decl}" if _decl else "on"
    else:
        bo = "off"
    _mode = str(draft.get("shipping.fulfillment_mode") or "SHIP").strip().upper()
    if _mode == "LOCAL_PICKUP":
        _hint = str(draft.get("shipping.local_pickup.location_hint") or "").strip()
        fulfill = f"LOCAL PICKUP only{(' · ' + _hint) if _hint else ''} (no parcel shipping; freight by quote)"
    else:
        fulfill = "Ship" + (f" · {draft.get('shipping.primary_service')}" if draft.get("shipping.primary_service") else "")

    # Comps to verify: prefer structured comps.csv, else URL lines from price.txt.
    comps: list[str] = []
    cpath = shoot / "comps.csv"
    if cpath.exists():
        for r in list(csv.DictReader(cpath.open(encoding="utf-8")))[:8]:
            if r.get("url"):
                comps.append(f"  • ${r.get('price','')} — {(r.get('title') or '')[:55]} — {r['url']}")
    if not comps and (shoot / "price.txt").exists():
        for ln in (shoot / "price.txt").read_text(encoding="utf-8").splitlines():
            if "http" in ln:
                comps.append("  • " + ln.strip())
            if len(comps) >= 8:
                break
    if not comps:
        comps = ["  • (no comp URLs found — see price.txt)"]

    # International (eIS) eligibility — ALWAYS shown, whether or not the item
    # opted in. The point is that a reviewer sees "this can never go abroad"
    # BEFORE approving, so a gun-shaped butane lighter is caught at the gate
    # rather than after an international buyer's order is cancelled at the hub.
    intl_on = str(draft.get("shipping.international") or "").strip().lower() in (
        "true", "yes", "1", "on")
    intl_blockers = _international_blockers(draft)
    intl_warnings = _international_warnings(draft)
    if intl_blockers:
        intl_lines = [f"  ✖ CANNOT SHIP INTERNATIONALLY — {'; '.join(intl_blockers)}"]
        if intl_on:
            intl_lines.append("  ✖ draft asks for international but it will be "
                              "REFUSED and listed domestic-only — fix the draft.")
        else:
            intl_lines.append("  • correctly domestic-only. Do NOT set "
                              "shipping.international true on this item.")
    elif intl_warnings:
        intl_lines = [f"  ⚠ {'ENABLED' if intl_on else 'eligible'} — needs a human call:"]
        intl_lines += [f"      – {w}" for w in intl_warnings]
    elif intl_on:
        intl_lines = ["  ✓ ENABLED — ships worldwide via eBay International Shipping.",
                      "  • delivered-price basis no longer holds: an overseas buyer "
                      "pays eIS freight + duties on top of this price."]
    else:
        intl_lines = ["  • eligible, not enabled (shipping.international: false). "
                      "Set true to offer it worldwide."]

    # Flags worth a human eye.
    flags: list[str] = []
    nr = shoot / "NEEDS_REVIEW.md"
    if nr.exists():
        flags = ["  • " + ln.strip() for ln in nr.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not flags:
        flags = ["  • None"]

    # Ledger status for this SKU.
    status = "?"
    lp = _ledger_path()
    if lp.exists():
        for r in csv.DictReader(lp.open(encoding="utf-8")):
            if r.get("sku") == sku:
                status = r.get("status") or "?"
                break

    # THE FINAL PHOTOS, LISTED. Every one, in order, hero marked, gaps called out.
    #
    # The card used to print a count and a hero filename. A count cannot tell
    # you that a frame is still the un-prepped original, that a redacted frame
    # was replaced by its un-redacted twin, or that listing/ holds a look nobody
    # picked — all three of which happened. These are the exact files that go to
    # eBay on approval, so they belong on the surface where approval is given.
    photo_lines = []
    try:
        _m = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
        _by_out = {(r.get("output") or ""): r for r in (_m.get("photos") or {}).values()}
        prep_note = f"  look: {_m.get('chosen_preset') or 'none picked'}"
        if not _m.get("approved"):
            prep_note += "   [!] PREP NOT APPROVED"
    except (OSError, ValueError):
        _by_out, prep_note = {}, "  [!] no PREP manifest for these photos"

    for _i, _rel in enumerate(photos):
        _rel = str(_rel)
        _f = shoot / _rel
        _tag = "hero" if _i == 0 else f"{_i + 1:>4}"
        if not _f.exists():
            photo_lines.append(f"  {_tag}  {_rel}   [!] MISSING FROM DISK")
            continue
        _rec = _by_out.get(_rel)
        _bits = []
        if _rec:
            _o = _rec.get("orientation") or {}
            if _o.get("applied"):
                _bits.append(f"rot {_o['applied']}deg")
            if (_rec.get("unskew") or {}).get("applied"):
                _bits.append("squared (legacy)")
            if (_rec.get("crop") or {}).get("applied"):
                _bits.append("cropped")
            _want = _rec.get("out_sha256")
            if _want:
                # PREP records a 16-char prefix; match the same way it does.
                _got = hashlib.sha256(_f.read_bytes()).hexdigest()[:len(_want)]
                if _got != _want:
                    _bits.append("[!] CHANGED SINCE PREP APPROVED IT")
        else:
            _bits.append("[!] not in the PREP manifest")
        photo_lines.append(f"  {_tag}  {_rel}" + (f"   [{', '.join(_bits)}]" if _bits else ""))

    card = "\n".join([
        f"━━ REVIEW: {shoot.name}  (sku {sku} · ledger {status}) ━━",
        f'Title:     "{title}"  [{len(title)}/80]',
        f"Price:     ${price}  ·  Best Offer: {bo}",
        f"Condition: {cond}",
        f"Quantity:  {qty}   ·   Photos: {len(photos)} (hero: {hero})",
        f"Fulfillment: {fulfill}",
        "Preflight (condition / shipping / insurance):",
        *[f"  • {m}" for m in pf],
        "International (eBay International Shipping):",
        *intl_lines,
        "Comps (open to verify):",
        *comps,
        "Condition detail:",
        f"  {cond_desc}",
        f"Final photos - exactly what publishes ({len(photos)}):{prep_note}",
        *photo_lines,
        "⚠ Needs review / manual intervention:",
        *flags,
        "",
        f"→ Approve publishes this LIVE at ${price}. On approval, run:",
        f"    python lib/list_edit.py --list {shoot} --confirm",
    ])
    (shoot / "review_card.md").write_text(card + "\n", encoding="utf-8")
    return card, str(shoot / "review_card.md")


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def create_or_update_listing(draft_path: Path,
                             creds: Optional[EbayCredentials] = None) -> SyncResult:
    """Sync a draft.md into eBay as an UNPUBLISHED (draft) offer.

    CREATE flow when frontmatter has no ebay_offer_id; EDIT flow otherwise.
    The offer is never published — that is a manual user action in Seller Hub.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Sync needs user-context OAuth. Run `python list_edit.py --setup-check`.")

    draft_path = _resolve_draft_path(draft_path)
    norm = normalize_draft_identity(draft_path)   # self-heal legacy url-style sku / orphaned ids before anything hits eBay
    if norm["changed"]:
        print(f"  [normalize] {norm['note']}")
    issues = validate_draft_for_sync(draft_path)
    if issues:
        raise ValueError("draft is not sync-ready:\n  - " + "\n  - ".join(issues))

    draft = parse_draft(draft_path)
    sku = _sku_for(draft)
    policies, location_key = _resolve_policies_and_location(creds)
    category_id = _resolve_category_id(draft, creds)

    # 0.5) Preflight: make the condition valid for THIS category (auto-remap)
    #      and sanity-check shipping. Prevents the publish-time 25021
    #      "condition invalid for category" failure (and surfaces shipping
    #      mismatches) before any photo upload or offer write.
    allowed_conditions, _req = get_allowed_condition_ids(category_id, creds=creds)
    if allowed_conditions:
        new_enum, reason = _remap_condition_for_category(
            str(draft.get("condition") or ""), allowed_conditions)
        if reason:
            _set_draft_condition(draft_path, new_enum)
            draft.frontmatter["condition"] = new_enum
            notes = str(draft.get("meta.notes") or "")
            update_meta(draft_path, {"notes": (notes + f" | PREFLIGHT: {reason}").strip(" |")})
            print(f"  [preflight] {reason}")
    # Shipping: pick the right fulfillment policy (Media Mail for media items,
    # else default ground) and use it for this offer.
    chosen_fulfillment, ship_msgs = _resolve_shipping_policy(draft, policies, creds)
    policies = {**policies, "fulfillment": chosen_fulfillment}
    for _m in ship_msgs + _insurance_notes(draft):
        print(f"  [preflight] {_m}")

    # 1) photos -> EPS
    image_urls = upload_photos_to_eps(resolve_photo_paths(draft), creds=creds)

    # 2) InventoryItem (create-or-replace; PUT is idempotent on the SKU)
    item_body = _build_inventory_item(draft, image_urls)
    api_send("PUT", f"/sell/inventory/v1/inventory_item/{sku}", item_body, creds=creds)

    # 3) Offer — update if one already exists for this SKU, else create.
    #    Look it up by SKU (idempotent); do not trust meta.ebay_offer_id,
    #    which may be a Chrome UI draftId rather than an API offerId.
    offer_body = _build_offer(draft, sku, category_id, location_key, policies)
    offer_id = _find_offer_id_for_sku(sku, creds)
    if offer_id:
        api_send("PUT", f"/sell/inventory/v1/offer/{offer_id}", offer_body, creds=creds)
        operation = "updated"
    else:
        resp = api_send("POST", "/sell/inventory/v1/offer", offer_body, creds=creds)
        offer_id = str(resp.get("offerId") or "")
        if not offer_id:
            raise EbayAPIError(0, f"createOffer returned no offerId: {resp}", None)
        operation = "created"

    # 4) write the eBay IDs back into the draft frontmatter
    update_meta(draft_path, {
        "ebay_offer_id": offer_id,
        "ebay_inventory_sku": sku,
        "last_synced": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })

    # 5) ledger: upsert this item's lifecycle record (-> SYNCED; a re-sync of
    #    an already-published item keeps PUBLISHED).
    if upsert_listing(sku, "SYNCED", title=str(draft.get("title") or ""),
                      offer_id=offer_id, price=_to_decimal_str(draft.get("price")) or ""):
        print(f"  [ledger] {sku} -> SYNCED")

    hub = "https://www.ebay.com/sh/lst/drafts"
    return SyncResult(offer_id=offer_id, inventory_sku=sku, operation=operation,
                      photo_eps_urls=image_urls, category_id=category_id,
                      eb_seller_hub_url=hub)


# ---------------------------------------------------------------------------
# Field-scoped update — change ONLY the named fields, preserve everything else
# ---------------------------------------------------------------------------
#
# create_or_update_listing() rebuilds the WHOLE inventory item + offer from
# draft.md and PUTs them, which clobbers any change made on eBay's side that
# the draft doesn't know about (most painfully, a photo re-order done in the
# Seller Hub UI). update_listing_fields() instead GETs the current live
# objects, overlays ONLY the requested field groups from the draft, and PUTs
# them back — so unrequested fields keep their current eBay value. It never
# publishes, so it can only edit an existing listing, never push a new one live.

# friendly name -> canonical field group
_SYNC_FIELD_ALIASES = {
    "description": "description", "desc": "description", "body": "description",
    "title": "title",
    "price": "price",
    "condition": "condition", "cond": "condition",
    "aspects": "aspects", "specifics": "aspects", "itemspecifics": "aspects",
    "photos": "photos", "images": "photos", "pics": "photos",
    "shipping": "shipping", "weight": "shipping", "dims": "shipping",
    "quantity": "quantity", "qty": "quantity",
    "bestoffer": "bestoffer", "best_offer": "bestoffer", "offer": "bestoffer",
    # Re-resolve which fulfillment policy the offer points at. Needed on its own
    # because the `shipping` group only touches packageWeightAndSize on the
    # ITEM — the policy lives in listingPolicies on the OFFER, so flipping
    # shipping.international has no effect without this.
    "policies": "policies", "policy": "policies", "fulfillment": "policies",
    "international": "policies", "intl": "policies",
}
_ITEM_SIDE = {"title", "description", "condition", "aspects", "photos", "shipping", "quantity"}
_OFFER_SIDE = {"description", "price", "quantity", "bestoffer", "policies"}
# Keys accepted by PUT inventory_item / updateOffer — used to strip read-only
# echo fields (offerId, listing, status, …) out of the GET response before PUT.
_WRITABLE_ITEM_KEYS = ("availability", "condition", "conditionDescription",
                       "packageWeightAndSize", "product")
_WRITABLE_OFFER_KEYS = ("availableQuantity", "categoryId", "listingDescription",
                        "listingPolicies", "pricingSummary", "merchantLocationKey",
                        "marketplaceId", "format", "sku", "tax", "listingDuration",
                        "quantityLimitPerBuyer", "storeCategoryNames", "listingStartDate",
                        "secondaryCategoryId", "hideBuyerDetails",
                        "includeCatalogProductDetails", "lotSize")


def _canonical_sync_fields(fields) -> set[str]:
    """Map user-supplied field names to canonical groups; raise on unknown."""
    out: set[str] = set()
    unknown: list[str] = []
    for raw in fields:
        key = re.sub(r"[\s_-]+", "", str(raw).strip().lower())
        canon = _SYNC_FIELD_ALIASES.get(key) or _SYNC_FIELD_ALIASES.get(str(raw).strip().lower())
        if canon:
            out.add(canon)
        elif raw:
            unknown.append(str(raw))
    if unknown:
        valid = sorted(set(_SYNC_FIELD_ALIASES.values()))
        raise ValueError(f"unknown --fields value(s): {', '.join(unknown)}. "
                         f"Valid groups: {', '.join(valid)}")
    if not out:
        raise ValueError("no fields given — pass --fields description[,price,…]")
    return out


class ListingNotSellable(RuntimeError):
    """The SKU's offer is not live — updating it could resurrect a sold item."""


def offer_sellable_state(sku: str, creds: EbayCredentials) -> dict:
    """Ask eBay — not the local ledger — whether this SKU is still on sale.

    The ledger cannot answer this. An accepted Best Offer never writes back to
    it, and `sync_actuals` exists precisely because orders are the only record
    that sees every sale. So a sold item can sit in the ledger as PUBLISHED for
    hours, and a batch update will happily write to it.

    Returns {sellable, status, quantity, offer_id, listing_id, reason}.
    """
    try:
        d = api_send("GET", f"/sell/inventory/v1/offer?sku={urllib.parse.quote(sku)}",
                     creds=creds)
    except EbayAPIError as e:
        if e.status == 404:
            return dict(sellable=False, status="NO_OFFER", quantity=0, offer_id=None,
                        listing_id=None, reason="no offer exists for this SKU")
        raise
    offers = d.get("offers") or []
    if not offers:
        return dict(sellable=False, status="NO_OFFER", quantity=0, offer_id=None,
                    listing_id=None, reason="no offer exists for this SKU")
    o = offers[0]
    status = str(o.get("status") or "").upper()
    qty = o.get("availableQuantity")
    qty = int(qty) if qty is not None else None
    listing = (o.get("listing") or {})
    lstatus = str(listing.get("listingStatus") or "").upper()

    reason = ""
    if status != "PUBLISHED":
        reason = f"offer status is {status or 'unknown'}, not PUBLISHED"
    elif lstatus and lstatus not in ("ACTIVE",):
        reason = f"listing status is {lstatus}, not ACTIVE"
    elif qty == 0:
        reason = "availableQuantity is 0 — the item has sold out"
    return dict(sellable=not reason, status=status or lstatus or "unknown",
                quantity=qty, offer_id=o.get("offerId"),
                listing_id=listing.get("listingId"), reason=reason)


def resolve_draft_state(draft_path: Path,
                        creds: Optional[EbayCredentials] = None) -> dict:
    """What state is this draft ACTUALLY in? Ask eBay, keyed on SKU.

    Read-only. Makes no writes and cannot publish.

    A draft's own `meta.ebay_*` fields are a log of what happened at sync time,
    not a record of what is true now — and they go stale silently. During the
    2026-08 voice sweep, `inventory/FR/christmas-elk/draft.md` carried
    `ebay_offer_id: null` and no listing id while being LIVE on eBay as
    206494264413. It was classified as an unpublished draft and skipped.

    `offer_sellable_state()` already asks eBay the right question, but it was
    only reachable from inside `update_listing_fields()` — so the only way to
    learn a draft's real state was to attempt a write. This exposes it for
    triage, which is what any audit or bulk sweep actually needs. Same doctrine
    as tools/ledger_reconcile.py, one level down: the API is truth, the local
    copy is assumed stale.

    Returns the offer_sellable_state dict plus `sku`, `draft_offer_id`,
    `draft_listing_id` and `meta_stale` (True when the draft disagrees).
    """
    creds = creds or load_credentials()
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    sku = str(draft.get("meta.ebay_inventory_sku") or "").strip()
    if not sku:
        return dict(sellable=False, status="NO_SKU", quantity=0, offer_id=None,
                    listing_id=None, reason="draft has no meta.ebay_inventory_sku",
                    sku=None, draft_offer_id=None, draft_listing_id=None,
                    meta_stale=False, path=draft_path)

    state = offer_sellable_state(sku, creds)
    d_offer = str(draft.get("meta.ebay_offer_id") or "").strip() or None
    d_listing = str(draft.get("meta.ebay_listing_id") or "").strip() or None
    state.update(sku=sku, draft_offer_id=d_offer, draft_listing_id=d_listing,
                 path=draft_path)
    state["meta_stale"] = (
        (state.get("offer_id") and d_offer != str(state["offer_id"])) or
        (state.get("listing_id") and d_listing != str(state["listing_id"]))
    )
    return state


def repair_draft_meta(draft_path: Path, creds: Optional[EbayCredentials] = None,
                      apply: bool = False) -> dict:
    """Write eBay's true offer/listing ids back into a draft whose meta is stale.

    No-op on a draft that already agrees. Touches only `meta.*` — never copy,
    price, photos, or anything eBay serves.
    """
    state = resolve_draft_state(draft_path, creds)
    if not state.get("meta_stale"):
        return state
    updates: dict[str, str] = {}
    if state.get("offer_id"):
        updates["ebay_offer_id"] = str(state["offer_id"])
    if state.get("listing_id"):
        updates["ebay_listing_id"] = str(state["listing_id"])
    if updates and apply:
        update_meta(state["path"], updates)
    state["repairs"] = updates
    return state


def update_listing_fields(draft_path: Path, fields,
                          creds: Optional[EbayCredentials] = None,
                          allow_not_sellable: bool = False) -> list[str]:
    """Update ONLY the named field groups on an existing eBay item/offer.

    GET-merge-PUT: unrequested fields (incl. photos + their order) keep their
    current eBay value. Never publishes. Returns the list of fields changed.

    REFUSES a SKU whose offer is not currently live, unless explicitly
    overridden. Writing to a sold-out inventory item is not harmless: this
    function used to round-trip `availability` on every update, so a PUT could
    hand eBay a positive quantity for an item that had already sold and get the
    listing resurrected as a new one. That happened on a real SKU. Checking the
    ledger is not sufficient — an accepted Best Offer never writes back to it.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Update needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    sku = _sku_for(draft)
    canon = _canonical_sync_fields(fields)
    item_fields = canon & _ITEM_SIDE
    offer_fields = canon & _OFFER_SIDE
    changed: list[str] = []

    state = offer_sellable_state(sku, creds)
    if not state["sellable"] and not allow_not_sellable:
        raise ListingNotSellable(
            f"SKU {sku} is not on sale — {state['reason']}. Refusing to update it; "
            f"writing to a sold item can relist it as a new listing. "
            f"(offer {state['offer_id']}, listing {state['listing_id']}) "
            f"Pass --allow-not-sellable only if you know the offer is live.")

    if item_fields:
        try:
            cur = api_send("GET", f"/sell/inventory/v1/inventory_item/{sku}", creds=creds)
        except EbayAPIError as e:
            raise ValueError(f"no inventory item for SKU {sku} — run a full `--sync` first ({e})")
        item = {k: cur[k] for k in _WRITABLE_ITEM_KEYS if k in cur}
        # `availability` MUST stay in the payload. This PUT is a full replace of
        # the inventory item, not a merge: omitting the key does not "leave
        # eBay's value alone", it sets the quantity to zero and drops the
        # listing to OUT_OF_STOCK. Removing it took five live listings out of
        # search before that was understood.
        #
        # The sold-item relist is prevented by the guard above instead, which is
        # the right place for it: refuse to write to an offer that is not live,
        # rather than write a deliberately incomplete item.
        if "availability" not in item and "quantity" not in item_fields:
            raise ValueError(
                f"SKU {sku}: eBay returned no availability block; refusing to "
                f"PUT an inventory item that would zero the quantity")
        product = item.setdefault("product", {})
        if "title" in item_fields:
            product["title"] = str(draft.get("title")); changed.append("title")
        if "description" in item_fields:
            product["description"] = _body_to_html(draft.body); changed.append("description")
        if "aspects" in item_fields:
            product["aspects"] = _build_aspects(draft); changed.append("aspects")
        if "condition" in item_fields:
            cond = str(draft.get("condition") or "")
            allowed, _r = get_allowed_condition_ids(_resolve_category_id(draft, creds), creds=creds)
            if allowed:
                new_enum, reason = _remap_condition_for_category(cond, allowed)
                if reason:
                    cond = new_enum
                    print(f"  [preflight] {reason}")
            item["condition"] = cond
            cd = str(draft.get("condition_description") or "").strip()
            if cd and cond != "NEW":
                item["conditionDescription"] = cd[:1000]
            changed.append("condition")
        if "shipping" in item_fields:
            full = _build_inventory_item(draft, product.get("imageUrls", []))
            if "packageWeightAndSize" in full:
                item["packageWeightAndSize"] = full["packageWeightAndSize"]
            changed.append("shipping")
        if "quantity" in item_fields:
            item.setdefault("availability", {}).setdefault(
                "shipToLocationAvailability", {})["quantity"] = int(draft.get("quantity") or 1)
            changed.append("quantity")
        if "photos" in item_fields:
            urls = upload_photos_to_eps(resolve_photo_paths(draft), creds=creds)
            product["imageUrls"] = urls
            changed.append(f"photos({len(urls)})")
        api_send("PUT", f"/sell/inventory/v1/inventory_item/{sku}", item, creds=creds)

    if offer_fields:
        offer_id = _find_offer_id_for_sku(sku, creds)
        if not offer_id:
            raise ValueError(f"no offer for SKU {sku} — run a full `--sync` first")
        cur = api_send("GET", f"/sell/inventory/v1/offer/{offer_id}", creds=creds)
        offer = {k: cur[k] for k in _WRITABLE_OFFER_KEYS if k in cur}
        if "description" in offer_fields and "description" not in changed:
            changed.append("description")
        if "description" in offer_fields:
            offer["listingDescription"] = _body_to_html(draft.body)
        if "price" in offer_fields:
            offer["pricingSummary"] = {"price": {"value": _to_decimal_str(draft.get("price")),
                                                 "currency": CURRENCY}}
            changed.append("price")
        if "quantity" in offer_fields and "quantity" not in changed:
            offer["availableQuantity"] = int(draft.get("quantity") or 1); changed.append("quantity")
        elif "quantity" in offer_fields:
            offer["availableQuantity"] = int(draft.get("quantity") or 1)
        if "bestoffer" in offer_fields:
            lp = offer.setdefault("listingPolicies", {})
            if draft.get("best_offer.enabled"):
                terms: dict = {"bestOfferEnabled": True}
                d = _to_decimal_str(draft.get("best_offer.auto_decline_amount"))
                a = _to_decimal_str(draft.get("best_offer.auto_accept_amount"))
                if d:
                    terms["autoDeclinePrice"] = {"value": d, "currency": CURRENCY}
                if a:
                    terms["autoAcceptPrice"] = {"value": a, "currency": CURRENCY}
                lp["bestOfferTerms"] = terms
            else:
                lp.pop("bestOfferTerms", None)
            changed.append("bestoffer")
        if "policies" in offer_fields:
            # Re-runs the same routing --sync uses (local pickup / international
            # / media / default) and applies any change to the LIVE offer. The
            # dangerous-goods gate runs here too, so an item that must not go
            # abroad silently stays on the domestic policy.
            pol_ids, _loc = _resolve_policies_and_location(creds)
            fid, pol_msgs = _resolve_shipping_policy(draft, pol_ids, creds)
            lp = offer.setdefault("listingPolicies", {})
            prev = lp.get("fulfillmentPolicyId")
            lp["fulfillmentPolicyId"] = fid
            changed.append(f"policies(fulfillment {prev}->{fid})"
                           if prev != fid else "policies(unchanged)")
            for m in pol_msgs:
                print(f"  {m}")
        api_send("PUT", f"/sell/inventory/v1/offer/{offer_id}", offer, creds=creds)

    update_meta(draft_path, {"last_synced": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return changed


# ---------------------------------------------------------------------------
# PUBLISH — explicit, manual, confirmation-gated (the one publish path)
# ---------------------------------------------------------------------------

@dataclass
class PublishResult:
    dry_run: bool
    offer_id: str
    title: str
    price: str
    status_before: str
    listing_id: Optional[str] = None   # set only when actually published
    listing_url: Optional[str] = None


def publish_offer(draft_path: Path, creds: Optional[EbayCredentials] = None,
                  confirm: bool = False) -> PublishResult:
    """Publish a previously-synced offer to a LIVE eBay listing.

    Guarded: requires an ebay_offer_id (from --sync) AND confirm=True. With
    confirm=False it is a DRY RUN — it fetches the offer and reports what
    WOULD go live without calling publish. This is the only function that
    calls publishOffer; --sync never does.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Publish needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    offer_id = str(draft.get("meta.ebay_offer_id") or "").strip()
    if not offer_id:
        raise ValueError("draft has no meta.ebay_offer_id — run `--sync` first to create the offer.")

    off = api_send("GET", f"/sell/inventory/v1/offer/{offer_id}", creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    _ps = off.get("pricingSummary") or {}
    price = str((_ps.get("price") or _ps.get("auctionStartPrice") or {}).get("value") or "?")
    sku = str(off.get("sku") or draft.get("meta.ebay_inventory_sku") or "")
    title = str(draft.get("title") or "")
    if not title and sku:
        try:
            title = str((api_send("GET", f"/sell/inventory/v1/inventory_item/{sku}", creds=creds)
                         .get("product") or {}).get("title") or "")
        except EbayAPIError:
            pass

    if status == "PUBLISHED":
        lid = str((off.get("listing") or {}).get("listingId") or "")
        return PublishResult(dry_run=False, offer_id=offer_id, title=title, price=price,
                             status_before=status, listing_id=lid or None,
                             listing_url=(f"https://www.ebay.com/itm/{lid}" if lid else None))

    if not confirm:
        return PublishResult(dry_run=True, offer_id=offer_id, title=title,
                             price=price, status_before=status)

    # --- the single publish call (with transient-error retry) ---
    # Right after an offer is created, eBay sometimes 5xx's or rejects the
    # offer's condition (25021) while category validation propagates, then
    # accepts the identical request seconds later. Retry those transients.
    resp = None
    for attempt in range(4):
        try:
            resp = api_send("POST", f"/sell/inventory/v1/offer/{offer_id}/publish", {}, creds=creds)
            break
        except EbayAPIError as e:
            transient = e.status >= 500 or (e.status == 400 and "25021" in (e.body or ""))
            if transient and attempt < 3:
                time.sleep(2.0 * (attempt + 1)); continue
            raise
    listing_id = str(resp.get("listingId") or "")
    if listing_id:
        update_meta(draft_path, {
            "ebay_listing_id": listing_id,
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        upsert_listing(sku, "PUBLISHED", title=title, offer_id=offer_id,
                       listing_id=listing_id, price=price,
                       url=f"https://www.ebay.com/itm/{listing_id}")
    return PublishResult(dry_run=False, offer_id=offer_id, title=title, price=price,
                         status_before=status, listing_id=listing_id or None,
                         listing_url=(f"https://www.ebay.com/itm/{listing_id}" if listing_id else None))


# ---------------------------------------------------------------------------
# END — take a live listing down (withdraw), also confirmation-gated
# ---------------------------------------------------------------------------

@dataclass
class EndResult:
    dry_run: bool
    offer_id: str
    title: str
    status_before: str
    ended: bool = False
    listing_id: Optional[str] = None


def end_listing(draft_path: Path, creds: Optional[EbayCredentials] = None,
                confirm: bool = False) -> EndResult:
    """End (withdraw) a live listing. Dry run unless confirm=True.

    Withdraws the offer, which ends the public listing; the offer returns
    to UNPUBLISHED so it can be re-synced/re-published later. The inverse
    of publish — same guard (does nothing without --confirm).
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("End needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    offer_id = str(draft.get("meta.ebay_offer_id") or "").strip()
    if not offer_id:
        raise ValueError("draft has no meta.ebay_offer_id — nothing to end.")

    off = api_send("GET", f"/sell/inventory/v1/offer/{offer_id}", creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    listing_id = str((off.get("listing") or {}).get("listingId") or "")
    title = str(draft.get("title") or "")

    if status != "PUBLISHED":
        return EndResult(dry_run=(not confirm), offer_id=offer_id, title=title,
                         status_before=status, ended=False, listing_id=listing_id or None)
    if not confirm:
        return EndResult(dry_run=True, offer_id=offer_id, title=title,
                         status_before=status, listing_id=listing_id or None)

    api_send("POST", f"/sell/inventory/v1/offer/{offer_id}/withdraw", {}, creds=creds)
    update_meta(draft_path, {
        "ebay_listing_id": "",
        "ended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    upsert_listing(str(draft.get("meta.ebay_inventory_sku") or ""), "ENDED",
                   title=title, offer_id=offer_id)
    return EndResult(dry_run=False, offer_id=offer_id, title=title,
                     status_before=status, ended=True, listing_id=listing_id or None)


# ---------------------------------------------------------------------------
# Account-level listing management — query / withdraw / delete by ID
# (works on ANY offer/SKU on the account, not just ones with a local draft)
# ---------------------------------------------------------------------------

def list_account_offers(creds: Optional[EbayCredentials] = None) -> list[dict]:
    """Enumerate every offer on the account (inventory items -> offers).

    Returns rows: sku, title, offer_id, status, listing_id, price, marketplace.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Query needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    rows: list[dict] = []
    for it in iter_inventory_items(creds=creds):
        sku = it.get("sku")
        title = str((it.get("product") or {}).get("title") or "")
        try:
            offers = get_offers_for_sku(sku, creds=creds)
        except EbayAPIError:
            offers = []
        if not offers:
            rows.append({"sku": sku, "title": title, "offer_id": None,
                         "status": "NO_OFFER", "listing_id": None,
                         "price": None, "marketplace": None})
        for off in offers:
            rows.append({
                "sku": sku, "title": title,
                "offer_id": off.get("offerId"),
                "status": off.get("status"),
                "listing_id": (off.get("listing") or {}).get("listingId"),
                "price": ((off.get("pricingSummary") or {}).get("price") or {}).get("value"),
                "marketplace": off.get("marketplaceId"),
            })
    return rows


@dataclass
class OfferActionResult:
    action: str               # "withdraw" | "delete-offer" | "delete-item"
    dry_run: bool
    done: bool
    target: str               # offer_id or sku
    status_before: Optional[str] = None
    title: Optional[str] = None
    listing_id: Optional[str] = None
    detail: str = ""


def withdraw_offer_by_id(offer_id: str, creds: Optional[EbayCredentials] = None,
                         confirm: bool = False) -> OfferActionResult:
    """Withdraw (end) a live offer by ID — keeps the offer (UNPUBLISHED)."""
    creds = creds or load_credentials()
    off = get_offer(offer_id, creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    listing_id = str((off.get("listing") or {}).get("listingId") or "") or None
    if status != "PUBLISHED":
        return OfferActionResult("withdraw", not confirm, False, offer_id, status,
                                 detail="not live (nothing to withdraw)", listing_id=listing_id)
    if not confirm:
        return OfferActionResult("withdraw", True, False, offer_id, status, listing_id=listing_id)
    withdraw_offer(offer_id, creds=creds)
    upsert_listing(str(off.get("sku") or ""), "ENDED", offer_id=offer_id)
    return OfferActionResult("withdraw", False, True, offer_id, status,
                             detail="ended; offer is now UNPUBLISHED", listing_id=listing_id)


def delete_offer_by_id(offer_id: str, creds: Optional[EbayCredentials] = None,
                       confirm: bool = False) -> OfferActionResult:
    """Delete an offer by ID (permanent). If live, this also ends the listing."""
    creds = creds or load_credentials()
    off = get_offer(offer_id, creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    listing_id = str((off.get("listing") or {}).get("listingId") or "") or None
    price = ((off.get("pricingSummary") or {}).get("price") or {}).get("value")
    if not confirm:
        return OfferActionResult("delete-offer", True, False, offer_id, status,
                                 detail=f"sku={off.get('sku')} price={price}", listing_id=listing_id)
    delete_offer(offer_id, creds=creds)
    upsert_listing(str(off.get("sku") or ""), "DELETED", offer_id=offer_id)
    return OfferActionResult("delete-offer", False, True, offer_id, status,
                             detail="offer deleted (inventory item/SKU kept)", listing_id=listing_id)


def delete_item_by_sku(sku: str, creds: Optional[EbayCredentials] = None,
                       confirm: bool = False) -> OfferActionResult:
    """Delete an inventory item (SKU) AND all its offers (permanent)."""
    creds = creds or load_credentials()
    try:
        offers = get_offers_for_sku(sku, creds=creds)
    except EbayAPIError:
        offers = []
    n_live = sum(1 for o in offers if str(o.get("status")) == "PUBLISHED")
    detail = f"{len(offers)} offer(s), {n_live} live — all will be removed"
    if not confirm:
        return OfferActionResult("delete-item", True, False, sku, detail=detail)
    delete_inventory_item(sku, creds=creds)
    upsert_listing(str(sku), "DELETED")
    return OfferActionResult("delete-item", False, True, sku,
                             detail=f"inventory item + {len(offers)} offer(s) deleted")


# ---------------------------------------------------------------------------
# Status / setup-check
# ---------------------------------------------------------------------------

def stub_status() -> dict:
    try:
        creds = load_credentials()
        err = None
    except Exception as e:  # pylint: disable=broad-except
        creds, err = None, str(e)
    return {
        "module": "list_edit",
        "implementation_status": "implemented (needs credentials + sandbox test)",
        "firewall_no_auto_publish": FIREWALL_NO_AUTO_PUBLISH,
        "publish_requires_confirm": True,
        "publish_requires_review_gate": True,
        "credentials": {
            "load_error": err,
            "app_id_set": bool(creds and creds.app_id),
            "cert_id_set": bool(creds and creds.cert_id),
            "dev_id_set": bool(creds and creds.dev_id),
            "user_refresh_token_set": bool(creds and creds.user_refresh_token),
        },
        "ready": bool(creds and creds.has_user and creds.dev_id),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_setup_check() -> None:
    creds = load_credentials()
    print(f"environment: {creds.environment}   config: {config_path()}")
    print(f"  app_id:             {'set' if creds.app_id else '(missing)'}")
    print(f"  cert_id:            {'set' if creds.cert_id else '(missing)'}")
    print(f"  dev_id:             {'set' if creds.dev_id else '(missing — REQUIRED for EPS photo upload)'}")
    print(f"  user_refresh_token: {'set' if creds.user_refresh_token else '(missing — REQUIRED for writes)'}")
    for f in ("merchant_location_key", "fulfillment_policy_id", "payment_policy_id", "return_policy_id"):
        print(f"  {f}: {_ebay_extra(f) or '(missing)'}")
    if not creds.has_user:
        print("\n[--] Cannot list account policies until app + user credentials are set.")
        return
    print("\nYour account's business policies + locations (paste IDs into config.yaml `ebay:`):")
    try:
        for label, items, idkey, namekey in (
            ("fulfillment_policy_id", get_fulfillment_policies(creds=creds), "fulfillmentPolicyId", "name"),
            ("payment_policy_id", get_payment_policies(creds=creds), "paymentPolicyId", "name"),
            ("return_policy_id", get_return_policies(creds=creds), "returnPolicyId", "name"),
        ):
            print(f"  {label}:")
            for it in items:
                print(f"    - {it.get(idkey)}   ({it.get(namekey)})")
        print("  merchant_location_key:")
        for loc in get_inventory_locations(creds=creds):
            print(f"    - {loc.get('merchantLocationKey')}   ({loc.get('name')})")
    except (EbayAuthError, EbayAPIError) as e:
        print(f"  [X] {e}")


def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(
        description="ebaybiz — LIST/EDIT (Function 6): sync draft.md -> eBay DRAFT; publish only via --publish/--list --confirm (post review-gate).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--validate", metavar="TARGET", help="Validate a draft.md or shoot dir (no creds).")
    ap.add_argument("--record", metavar="TARGET", help="DRAFT-time: stamp the SKU into the draft + create its ledger record (DRAFTED). No creds.")
    ap.add_argument("--normalize", metavar="TARGET", help="Migrate a legacy url-style SKU to canonical 8-hex + clear orphaned offer/listing ids in a draft.md or shoot dir. No creds.")
    ap.add_argument("--preflight", metavar="TARGET", help="Check (and auto-correct) condition + shipping against the eBay category (needs creds).")
    ap.add_argument("--set-hero", nargs=2, metavar=("TARGET", "PHOTO"),
                    help="move PHOTO to the front of the draft's photos list (eBay gallery image)")
    ap.add_argument("--review", metavar="TARGET", help="One step: record + preflight + build the REVIEW decision card. Stops for approval; does NOT publish.")
    ap.add_argument("--sync", metavar="TARGET", help="Create/update the eBay DRAFT (unpublished offer) from a draft.md or shoot dir.")
    ap.add_argument("--publish", metavar="TARGET", help="Publish a synced offer to a LIVE listing. DRY RUN unless --confirm is also given.")
    ap.add_argument("--list", metavar="TARGET", dest="list_target", help="Sync THEN publish in one step (post review-gate). DRY RUN unless --confirm is also given.")
    ap.add_argument("--update", metavar="TARGET", help="Update ONLY the --fields groups on an existing item/offer (GET-merge-PUT; never publishes; preserves photos + everything else).")
    ap.add_argument("--allow-not-sellable", action="store_true",
                    help="update even if the SKU's offer is not live (DANGEROUS: writing to a sold item can relist it)")
    ap.add_argument("--fields", metavar="LIST", help="Comma-separated field groups for --update (e.g. description,price). Groups: description,title,price,condition,aspects,photos,shipping,quantity,bestoffer,policies. Use 'policies' after changing shipping.international — it re-points the live offer at the right fulfillment policy.")
    ap.add_argument("--status", metavar="TARGET", help="Read-only: ask eBay (by SKU) what state a draft is really in. Accepts a draft.md, a shoot dir, or a tree to walk. Never writes.")
    ap.add_argument("--repair-meta", metavar="TARGET", help="Backfill eBay's true offer/listing ids into drafts whose meta.* is stale. DRY RUN unless --confirm.")
    ap.add_argument("--end", metavar="TARGET", help="End (withdraw) a live listing from a draft. DRY RUN unless --confirm is also given.")
    ap.add_argument("--offers", action="store_true", help="Query ALL offers on the account (sku, offerId, status, listingId, price).")
    ap.add_argument("--withdraw-offer", metavar="OFFER_ID", help="Withdraw (end) a live offer by ID — keeps the offer. DRY RUN unless --confirm.")
    ap.add_argument("--delete-offer", metavar="OFFER_ID", help="Delete an offer by ID (permanent; ends listing if live; keeps SKU). DRY RUN unless --confirm.")
    ap.add_argument("--delete-item", metavar="SKU", help="Delete an inventory item (SKU) AND all its offers (permanent). DRY RUN unless --confirm.")
    ap.add_argument("--confirm", action="store_true", help="Required with --publish/--list/--end/--withdraw-offer/--delete-offer/--delete-item to actually act (otherwise dry runs).")
    ap.add_argument("--setup-check", action="store_true", help="Verify creds and list account policy IDs.")
    ap.add_argument("--create-pickup-policy", action="store_true", help="Create (idempotent) a LOCAL-PICKUP-ONLY fulfillment policy and print its ID to paste into config (ebay.fulfillment_policy_id_local_pickup).")
    ap.add_argument("--check", action="store_true", help="Print module/credential status.")
    args = ap.parse_args()

    try:
        if args.validate:
            path = _resolve_draft_path(args.validate)
            issues = validate_draft_for_sync(path)
            if not issues:
                print(f"[OK] {path} is sync-ready.")
            else:
                print(f"[X] {path} has {len(issues)} issue(s):")
                for i in issues:
                    print(f"  - {i}")
                sys.exit(1)
            return
        if args.status:
            root = Path(args.status)
            targets = sorted(root.rglob("draft.md")) if root.is_dir() and not (root / "draft.md").exists() else [root]
            creds = load_credentials()
            rows = []
            for t in targets:
                if ".prior-run-bak" in str(t):
                    continue
                try:
                    st = resolve_draft_state(t, creds)
                except Exception as e:
                    print(f"  ERR  {t}: {e}")
                    continue
                flag = " STALE-META" if st.get("meta_stale") else ""
                name = t.parent.name if t.name == "draft.md" else t.name
                print(f"  {st['status']:<12} {name:<42} sku={st.get('sku')} "
                      f"listing={st.get('listing_id')}{flag}")
                rows.append(st)
            stale = sum(1 for r in rows if r.get("meta_stale"))
            live = sum(1 for r in rows if r.get("status") == "PUBLISHED")
            print(f"\n{len(rows)} draft(s): {live} live, {stale} with stale meta.")
            if stale:
                print("  fix with: python lib/list_edit.py --repair-meta <dir> --confirm")
            return
        if args.repair_meta:
            root = Path(args.repair_meta)
            targets = sorted(root.rglob("draft.md")) if root.is_dir() and not (root / "draft.md").exists() else [root]
            creds = load_credentials()
            fixed = 0
            for t in targets:
                if ".prior-run-bak" in str(t):
                    continue
                try:
                    st = repair_draft_meta(t, creds, apply=args.confirm)
                except Exception as e:
                    print(f"  ERR  {t}: {e}")
                    continue
                if st.get("repairs"):
                    fixed += 1
                    verb = "repaired" if args.confirm else "WOULD repair"
                    print(f"  {verb} {t.parent.name}: {st['repairs']}")
            if not fixed:
                print("[OK] no stale draft metadata found.")
            elif not args.confirm:
                print(f"\nDRY RUN — {fixed} draft(s) would change. Re-run with --confirm.")
            else:
                print(f"\n[OK] repaired {fixed} draft(s).")
            return
        if args.record:
            sku, ledger = record_draft(Path(args.record))
            print(f"[OK] recorded DRAFTED — sku {sku}")
            if ledger:
                print(f"  ledger: {ledger}")
            return
        if args.normalize:
            rep = normalize_draft_identity(Path(args.normalize))
            if rep["changed"]:
                print(f"[OK] normalized — sku {rep['sku_before']} -> {rep['sku_after']}")
                if rep["cleared"]:
                    print(f"  cleared orphaned: {', '.join(rep['cleared'])}")
            else:
                print(f"[OK] no change — sku {rep['sku_after']!r} already canonical (or none stamped)")
            return
        if args.setup_check:
            _print_setup_check()
            return
        if args.create_pickup_policy:
            pol = create_local_pickup_policy()
            pid = pol.get("fulfillmentPolicyId")
            print(f"[OK] local-pickup policy: {pid}  ({pol.get('name')!r}, localPickup={pol.get('localPickup')})")
            print(f"  Paste into config.yaml under the active ebay.<env> block:")
            print(f"    fulfillment_policy_id_local_pickup: \"{pid}\"")
            return
        if args.preflight:
            print(f"Preflight {args.preflight}:")
            for m in preflight_listing(Path(args.preflight)):
                print(f"  {m}")
            return
        if args.set_hero:
            target, photo = args.set_hero
            order = set_hero_photo(Path(target), photo)
            print(f"[OK] hero -> {order[0]}")
            for i, p in enumerate(order[1:], 2):
                print(f"  {i}. {p}")
            return
        if args.review:
            card, path = build_review_card(Path(args.review))
            print(card)
            print(f"\n[review_card] {path}")
            return
        if args.sync:
            res = create_or_update_listing(Path(args.sync))
            print(f"[OK] {res.operation} eBay DRAFT (not published).")
            print(f"  offer_id:  {res.offer_id}")
            print(f"  sku:       {res.inventory_sku}")
            print(f"  category:  {res.category_id}")
            print(f"  photos:    {len(res.photo_eps_urls)} uploaded to EPS")
            print(f"  status:    UNPUBLISHED (a draft; API offers don't appear in Seller Hub Drafts)")
            print(f"  to go live: python list_edit.py --publish {args.sync} --confirm")
            return
        if args.publish:
            res = publish_offer(Path(args.publish), confirm=args.confirm)
            if res.status_before == "PUBLISHED":
                print(f"[i] Already LIVE. listing {res.listing_id}")
                if res.listing_url: print(f"    {res.listing_url}")
            elif res.dry_run:
                print("[DRY RUN] Nothing published. This WOULD go live:")
                print(f"  offer:  {res.offer_id}")
                print(f"  title:  {res.title}")
                print(f"  price:  ${res.price}")
                print(f"  status: {res.status_before} -> would become PUBLISHED (a real, live listing)")
                print(f"\n  To actually publish: re-run with --confirm")
            else:
                print(f"[LIVE] Published offer {res.offer_id} -> listing {res.listing_id}")
                if res.listing_url: print(f"  {res.listing_url}")
                print("  This listing is now public and accepting buyers.")
            return
        if args.list_target:
            # One-step LIST = sync (create/update the offer) then publish it.
            # Same --confirm guard as --publish: without it, this syncs and then
            # shows a DRY RUN of what would go live. This is the path the agent
            # runs ONLY after a human approves the REVIEW card.
            sres = create_or_update_listing(Path(args.list_target))
            print(f"[OK] {sres.operation} eBay DRAFT (offer {sres.offer_id}, {len(sres.photo_eps_urls)} photos).")
            res = publish_offer(Path(args.list_target), confirm=args.confirm)
            if res.status_before == "PUBLISHED":
                print(f"[i] Already LIVE. listing {res.listing_id}")
                if res.listing_url: print(f"    {res.listing_url}")
            elif res.dry_run:
                print("[DRY RUN] Synced but NOT published. This WOULD go live:")
                print(f"  title:  {res.title}")
                print(f"  price:  ${res.price}")
                print(f"\n  To actually publish: re-run --list with --confirm")
            else:
                print(f"[LIVE] Published offer {res.offer_id} -> listing {res.listing_id}")
                if res.listing_url: print(f"  {res.listing_url}")
                print("  This listing is now public and accepting buyers.")
            return
        if args.update:
            if not args.fields:
                print("[X] --update requires --fields (e.g. --fields description). "
                      "No implicit 'all' — that's what --sync/--list are for.")
                sys.exit(1)
            field_list = [f for f in args.fields.split(",") if f.strip()]
            try:
                changed = update_listing_fields(Path(args.update), field_list,
                                                allow_not_sellable=args.allow_not_sellable)
            except ListingNotSellable as e:
                print(f"[SKIP] {e}")
                sys.exit(2)
            if changed:
                print(f"[OK] updated {', '.join(changed)} on {args.update} (other fields preserved).")
            else:
                print(f"[i] nothing changed on {args.update}.")
            return
        if args.end:
            res = end_listing(Path(args.end), confirm=args.confirm)
            if res.status_before != "PUBLISHED":
                print(f"[i] Nothing live to end (offer status: {res.status_before}).")
            elif res.dry_run:
                print("[DRY RUN] Nothing ended. This WOULD end a LIVE listing:")
                print(f"  offer:   {res.offer_id}")
                print(f"  listing: {res.listing_id}")
                print(f"  title:   {res.title}")
                print("\n  To actually end it: re-run with --confirm")
            else:
                print(f"[ENDED] Withdrew offer {res.offer_id} (listing {res.listing_id}) — no longer live.")
            return
        if args.offers:
            rows = list_account_offers()
            print(f"{len(rows)} offer(s) on the account:\n")
            print(f"  {'STATUS':12} {'OFFER_ID':16} {'LISTING_ID':14} {'PRICE':>8}  SKU / TITLE")
            for r in rows:
                price = f"${r['price']}" if r.get("price") else "-"
                print(f"  {str(r['status'] or '-'):12} {str(r['offer_id'] or '-'):16} "
                      f"{str(r['listing_id'] or '-'):14} {price:>8}  {r['sku']}  |  {r['title'][:48]}")
            return
        if args.withdraw_offer:
            res = withdraw_offer_by_id(args.withdraw_offer, confirm=args.confirm)
            if not res.done and res.status_before != "PUBLISHED":
                print(f"[i] Offer {res.target} is {res.status_before} — {res.detail}")
            elif res.dry_run:
                print("[DRY RUN] WOULD withdraw (end) this LIVE offer:")
                print(f"  offer:   {res.target}\n  listing: {res.listing_id}\n  status:  {res.status_before}")
                print("\n  To actually withdraw: re-run with --confirm")
            else:
                print(f"[ENDED] Withdrew offer {res.target} — {res.detail}")
            return
        if args.delete_offer:
            res = delete_offer_by_id(args.delete_offer, confirm=args.confirm)
            if res.dry_run:
                print("[DRY RUN] WOULD DELETE this offer (permanent):")
                print(f"  offer:   {res.target}\n  status:  {res.status_before}"
                      + (f" (LIVE — deleting ends the listing)" if res.status_before == "PUBLISHED" else ""))
                print(f"  {res.detail}")
                print("\n  To actually delete: re-run with --confirm")
            else:
                print(f"[DELETED] Offer {res.target} — {res.detail}")
            return
        if args.delete_item:
            res = delete_item_by_sku(args.delete_item, confirm=args.confirm)
            if res.dry_run:
                print("[DRY RUN] WOULD DELETE this inventory item AND its offers (permanent):")
                print(f"  sku: {res.target}\n  {res.detail}")
                print("\n  To actually delete: re-run with --confirm")
            else:
                print(f"[DELETED] Inventory item {res.target} — {res.detail}")
            return
        if args.check:
            s = stub_status()
            for k, v in s.items():
                print(f"{k}: {v}")
            return
        ap.print_help()
    except (EbayAuthError, EbayAPIError, ConfigError, ValueError, FileNotFoundError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        body = getattr(e, "body", None)
        if body:
            print(f"    eBay said: {body}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
