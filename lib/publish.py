"""
`ebz publish` — LIST one reviewed draft to eBay, Whatnot, or both.

    python -m lib.cli publish inventory/<item> --to ebay              # == listing --list
    python -m lib.cli publish inventory/<item> --to whatnot
    python -m lib.cli publish inventory/<item> --to both --crosslist --confirm

Each channel does exactly what its own command does — this module only picks
the channel(s) and holds the rules that span both:

  * Same gate as `listing --list`: without --confirm every channel syncs its
    draft/unpublished copy and prints a DRY RUN; nothing goes live.
  * Cross-listing guard: a quantity-1 item may only be live in one place
    unless --crosslist is given. Nothing here ends the other channel's
    listing when one sells, so a double sale has to be cancelled by hand.
  * Channels run in order (eBay first) and independently: a Whatnot failure
    does not undo a successful eBay publish. The exit code is non-zero if
    any channel failed, and the summary says which.

Defaults: --to comes from `publish.default_channels` in config (a list), else
["ebay"], so `ebz publish <item>` with no --to behaves like `listing --list`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
for _p in (str(_LIB.parent), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import ConfigError, load_config                    # noqa: E402
from draft_io import parse_draft                                # noqa: E402

CHANNELS = ("ebay", "whatnot")


def _resolve_draft_path(target: str | Path) -> Path:
    p = Path(target)
    return p / "draft.md" if p.is_dir() else p


def default_channels() -> list[str]:
    sec = load_config().get("publish") or {}
    chans = sec.get("default_channels") if isinstance(sec, dict) else None
    if isinstance(chans, str):
        chans = [chans]
    chans = [str(c).strip().lower() for c in (chans or ["ebay"])]
    bad = [c for c in chans if c not in CHANNELS]
    if bad:
        raise ConfigError(f"publish.default_channels has unknown channel(s) {bad}; "
                          f"choose from {list(CHANNELS)}")
    return chans


def parse_channels(to: Optional[str]) -> list[str]:
    if not to:
        return default_channels()
    to = to.strip().lower()
    if to in ("both", "all"):
        return list(CHANNELS)
    chans = [c.strip() for c in to.split(",") if c.strip()]
    bad = [c for c in chans if c not in CHANNELS]
    if bad:
        raise ValueError(f"unknown channel(s) {bad} — use ebay, whatnot, or both")
    return [c for c in CHANNELS if c in chans]          # canonical order, de-duped


def crosslist_blockers(draft_path: Path, channels: list[str], crosslist: bool) -> list[str]:
    """Reasons this publish would leave a one-of-a-kind item live in two places."""
    if crosslist:
        return []
    draft = parse_draft(draft_path)
    try:
        qty = int(draft.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1
    if qty > 1:
        return []
    live_ebay = bool(str(draft.get("meta.ebay_listing_id") or "").strip())
    live_wn = bool(str(draft.get("meta.whatnot_published_at") or "").strip())
    out = []
    if set(channels) == set(CHANNELS):
        out.append("--to both on a quantity-1 item")
    if "ebay" in channels and live_wn and not live_ebay:
        out.append("it is already live on Whatnot")
    if "whatnot" in channels and live_ebay and not live_wn:
        out.append("it is already live on eBay")
    return out


def _publish_ebay(draft_path: Path, store: Optional[str], confirm: bool) -> str:
    from ebay_client import load_credentials
    from list_edit import _resolve_store, create_or_update_listing, publish_offer
    creds = load_credentials(store=_resolve_store(str(draft_path), store))
    s = create_or_update_listing(draft_path, creds=creds)
    print(f"  [ebay] {s.operation} draft offer {s.offer_id} ({len(s.photo_eps_urls)} photos)")
    r = publish_offer(draft_path, creds=creds, confirm=confirm)
    if r.status_before == "PUBLISHED":
        return f"already live — {r.listing_url or r.listing_id}"
    if r.dry_run:
        return f"DRY RUN — would publish offer {r.offer_id} at ${r.price}"
    promo = f" · promoted: {r.promotion}" if r.promotion else ""
    return f"LIVE — {r.listing_url}{promo}"


def _publish_whatnot(draft_path: Path, confirm: bool) -> str:
    from whatnot_list import publish_to_whatnot, sync_to_whatnot
    s = sync_to_whatnot(draft_path)
    print(f"  [whatnot] {s.operation} product {s.product_id} on {s.environment} "
          f"(listing {s.listing_id}, {s.media_count} photos sent)")
    # The cross-list rule was already applied across channels above.
    r = publish_to_whatnot(draft_path, confirm=confirm, crosslist=True)
    if r.status_before == "PUBLISHED" and not r.dry_run:
        return f"already live — {r.listing_url or r.listing_id}"
    if r.dry_run:
        return f"DRY RUN — would publish listing {r.listing_id} at ${r.price}"
    return f"LIVE — {r.listing_url or r.listing_id}"


def publish(target: str | Path, channels: list[str], *, confirm: bool = False,
            crosslist: bool = False, store: Optional[str] = None) -> dict[str, tuple[bool, str]]:
    """Run each channel; return {channel: (ok, message)}."""
    draft_path = _resolve_draft_path(target)
    if not draft_path.exists():
        raise FileNotFoundError(f"No draft.md found at {draft_path}")
    blockers = crosslist_blockers(draft_path, channels, crosslist)
    if blockers:
        raise ValueError(
            "refusing to cross-list a one-of-a-kind item: " + "; ".join(blockers) + ".\n"
            "  Nothing ends the other listing when one sells, so it could sell twice.\n"
            "  Re-run with --crosslist to accept that risk.")
    results: dict[str, tuple[bool, str]] = {}
    for ch in channels:
        try:
            msg = _publish_ebay(draft_path, store, confirm) if ch == "ebay" \
                else _publish_whatnot(draft_path, confirm)
            results[ch] = (True, msg)
        except SystemExit as e:              # PREP gate raises SystemExit
            results[ch] = (False, str(e.code))
        except Exception as e:               # noqa: BLE001 — one channel must not sink the other
            results[ch] = (False, f"{type(e).__name__}: {e}")
    return results


def _cli() -> None:
    ap = argparse.ArgumentParser(
        prog="ebz publish",
        description="LIST a reviewed draft to eBay, Whatnot, or both. DRY RUN unless --confirm.")
    ap.add_argument("target", help="shoot folder or draft.md")
    ap.add_argument("--to", help="ebay | whatnot | both | ebay,whatnot "
                                 "(default: publish.default_channels, else ebay)")
    ap.add_argument("--confirm", action="store_true", help="actually go live")
    ap.add_argument("--crosslist", action="store_true",
                    help="allow a quantity-1 item to be live on both channels")
    ap.add_argument("--store", help="eBay store (default: the draft's store:)")
    args = ap.parse_args()
    try:
        channels = parse_channels(args.to)
        results = publish(args.target, channels, confirm=args.confirm,
                          crosslist=args.crosslist, store=args.store)
    except (ValueError, ConfigError, FileNotFoundError) as e:
        print(f"[X] {e}")
        sys.exit(1)
    print()
    for ch, (ok, msg) in results.items():
        print(f"  {'[OK]' if ok else '[X] '} {ch:<8} {msg}")
    if not args.confirm and all(ok for ok, _ in results.values()):
        print("\n  Nothing went live. Re-run with --confirm to publish.")
    sys.exit(0 if all(ok for ok, _ in results.values()) else 1)


if __name__ == "__main__":
    _cli()
