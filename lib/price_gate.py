"""Sold-pull gate — a price set without sold comps does not go live.

The 2026-10-24 comp audit (reports/comp_audit/2026-10-24/SUMMARY.txt) found
about 30 of 62 prices were set while PRICE's Stage B sold pull was
UNAVAILABLE, so PRICE fell back to active asks or judgment. Asks run above
solds for common goods, so ~25 came out high; a one-off antique with no comps
(K01-C021 Hopkins band saw) had no ceiling at all and sold the same day at
full ask. price.txt already recorded `B · Browser : UNAVAILABLE`; nothing
read it.

This module reads it. The source of truth is the research log PRICE already
writes (prompts/price.md, "Research log"): the FIRST `B · Browser : <STATUS>`
line in `<shoot>/price.txt`, the one beside draft.md. A refresh block goes on
top of the prior record, so the first line is the latest run.

- `RAN` passes only with proof on disk: a Stage B save
  (`apify_run_*browse-*.json`) in the shoot dir, or comps.csv rows from stage
  B or C that carry a URL and are not active asks.
- `THIN`, or `RAN` with `n_kept=0`, is the one-off case: the pull ran and
  found no usable sold comps. It passes only with a
  `ONE-OFF: historian <maker, era> · judgment $X · push-high $Y` line, the
  draft priced at Push-high or above with Best Offer on, or an auction.
  `conf=thin` with a few comps kept is the ordinary era-peer path, not a
  one-off, and passes on its proof.
- `OPERATOR PRICE: <Russell's words> (<date>)` passes: prices are his call.
- Anything else (UNAVAILABLE, BLOCKED, SKIPPED, no B line, no price.txt)
  blocks.

Live listings (meta.ebay_listing_id set) are exempt, so revisions and
markdowns are untouched. Offline; no eBay calls.
"""

from __future__ import annotations

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from draft_io import Draft

_B_LINE_RE = re.compile(r"^\s*B\s*·\s*Browser\s*:\s*([A-Z]+)?(.*)$")
_C_LINE_RE = re.compile(r"^\s*C\s*·\s*Chrome\s*:")
_ZERO_KEPT_RE = re.compile(r"n_kept\s*=\s*0\b")
_OPERATOR_RE = re.compile(r"^\s*OPERATOR PRICE:\s*\S", re.MULTILINE)
_ONE_OFF_RE = re.compile(r"^\s*ONE-OFF:\s*(.*)$", re.MULTILINE)
_HISTORIAN_RE = re.compile(r"historian\s+([^·]*)", re.IGNORECASE)
_PUSH_HIGH_RE = re.compile(r"push-high\s*\$?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)

_REFRESH = ("re-run PRICE with a sold pull (prompts/price.md Stage B), or add "
            "an `OPERATOR PRICE:` line if Russell set this price himself")


def _b_block(text: str) -> tuple[str | None, str]:
    """(status word, the B line plus its continuation lines) for the first
    `B · Browser :` line, or (None, "") when there is none."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = _B_LINE_RE.match(ln)
        if not m:
            continue
        block = [ln]
        for nxt in lines[i + 1:]:
            if not nxt.strip() or _C_LINE_RE.match(nxt) or not nxt[:1].isspace():
                break
            block.append(nxt)
        return (m.group(1) or ""), "\n".join(block)
    return None, ""


def _has_sold_proof(shoot: Path) -> bool:
    if any(shoot.glob("apify_run_*browse-*.json")):
        return True
    cpath = shoot / "comps.csv"
    if not cpath.exists():
        return False
    with cpath.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("stage") or "").strip().upper() not in ("B", "C"):
                continue
            if not (r.get("url") or "").strip():
                continue
            kind = f"{r.get('listing_type') or ''} {r.get('note') or ''}".lower()
            if "active" in kind or "asking" in kind:
                continue
            return True
    return False


def _money(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _one_off_issues(text: str, draft: Draft) -> list[str]:
    m = _ONE_OFF_RE.search(text)
    if not m:
        return ["price: sold pull found no usable sold comps (THIN) and price.txt "
                "has no `ONE-OFF: historian <maker, era> · judgment $X · "
                "push-high $Y` line — one-off rule (prompts/price.md): historian "
                "ID first, then Push-high well above judgment with Best Offer, "
                "or an auction"]
    line = m.group(1)
    hm = _HISTORIAN_RE.search(line)
    if not hm or not hm.group(1).strip():
        return ["price: ONE-OFF line has no historian maker/era — get the "
                "historian's ID before pricing a one-off"]
    if str(draft.get("format") or "FIXED_PRICE").upper() == "AUCTION":
        return []
    issues = []
    if not draft.get("best_offer.enabled"):
        issues.append("price: one-off priced without Best Offer — set "
                      "best_offer.enabled: true, or list it as an auction")
    pm = _PUSH_HIGH_RE.search(line)
    push = _money(pm.group(1)) if pm else None
    price = _money(str(draft.get("price") or ""))
    if push is None:
        issues.append("price: ONE-OFF line has no `push-high $Y` figure")
    elif price is None or price < push:
        issues.append(f"price: ${draft.get('price')} is below the one-off "
                      f"Push-high ${push} on the ONE-OFF line — list at Push-high")
    return issues


def sold_pull_issues(draft: Draft) -> list[str]:
    """Blocking issues for a draft whose price lacks a sold pull. [] = passes."""
    if str(draft.get("meta.ebay_listing_id") or "").strip():
        return []
    shoot = Path(draft.path).parent
    pt = shoot / "price.txt"
    if not pt.exists():
        return [f"price: no price.txt beside the draft — PRICE never ran; {_REFRESH}"]
    text = pt.read_text(encoding="utf-8", errors="replace")
    if _OPERATOR_RE.search(text):
        return []
    status, block = _b_block(text)
    if status is None:
        return [f"price: price.txt has no `B · Browser :` research-log line; {_REFRESH}"]
    if status == "RAN":
        if not _has_sold_proof(shoot):
            return ["price: price.txt says `B · Browser : RAN` but the shoot dir "
                    "has no Stage B save (apify_run_*browse-*.json) and no "
                    f"stage B/C sold rows in comps.csv; {_REFRESH}"]
        if _ZERO_KEPT_RE.search(block):
            return _one_off_issues(text, draft)
        return []
    if status == "THIN":
        return _one_off_issues(text, draft)
    return [f"price: `B · Browser : {status or '?'}` — this price was set "
            f"without a sold pull (asks or judgment only); {_REFRESH}"]
