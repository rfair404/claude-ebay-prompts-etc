"""US-only routing — items eBay requires be sold domestically.

Some items are legal to sell on eBay.com but ONLY to US buyers, by US export
law rather than by carrier rule. Firearm magazines and parts are the case that
forced this module: eBay refuses the publish outright with errorId 25019 and

    "The item that you tried to list can only be sold on the U.S. eBay website
     (eBay.com) by sellers located in the United States. Please edit your
     listing to offer shipping to the United States only."
     (PI_USFAW_CBT_v2rep10006258 — firearms/weapons policy + ITAR Part 121)

**The trap this module exists to close.** That refusal is NOT prevented by
`shipping.international: false`, and it is NOT prevented by a policy with
`globalShipping: false` and zero international shipping options. Measured
2026-08-27 on a Ruger Mini-14 magazine: the draft had international false, the
default policy had globalShipping false and no international options, and the
publish still failed. The actual trigger is
`shipToLocations.regionIncluded = "Worldwide"` on the fulfillment policy, which
is what makes a listing eBay-International-Shipping eligible. eIS is an
ACCOUNT-level enrollment, so it cannot be switched off per listing from the
draft — the only lever is a policy whose shipToLocations are US-only.

Hence `ebay.fulfillment_policy_id_us_only`. Routing lives in
`list_edit.py:_resolve_shipping_policy`; this module only decides WHETHER an
item needs it.

**Detection follows the house rule from `_DANGEROUS_GOODS_PATTERNS`:** scan the
TITLE and item specifics, never the body or condition text. Titles name what a
thing IS; descriptions say what it looks like. That distinction matters more
here than anywhere else in the codebase, because this inventory genuinely sells
MAGAZINES — Esquire, Britches catalogs, periodical lots. A bare `\\bmagazine\\b`
would route half the paper inventory to a firearms policy. Every pattern below
therefore requires firearm CONTEXT alongside the part word.
"""

from __future__ import annotations

import re
from typing import Any

# --- the aspect signal (strongest, zero false positives) -------------------
# eBay's own firearm-accessory categories REQUIRE these aspects; if a draft
# carries one, eBay has already classified the item as gun-related and the
# ITAR gate is certain to fire. Matched case-insensitively on the key.
_GUN_ASPECT_KEYS = (
    "for gun type",
    "number of rounds",
    "caliber/gauge",
    "caliber",
    "gauge",
)

# --- part words: meaningless alone, decisive with firearm context ----------
_PART_WORDS = (
    r"magazine|mag\b|clip|barrel|receiver|bolt carrier|bolt|trigger|"
    r"handguard|hand guard|buttstock|butt stock|stock|upper|lower|"
    r"charging handle|firing pin|extractor|muzzle|choke|forend|fore-end"
)

# --- firearm context: any ONE of these next to a part word is enough -------
_GUN_CONTEXT = (
    r"rifle|pistol|handgun|shotgun|firearm|carbine|revolver|gun\b|"
    r"ruger|glock|colt|remington|winchester|mossberg|marlin|savage|"
    r"beretta|sig sauer|sig\b|springfield|kel-?tec|henry|browning|"
    r"smith\s*&\s*wesson|s&w\b|ar-?15|ak-?47|m1a|mini-?14|1911|10/22"
)

# --- calibers: a caliber beside a part word is unambiguous ----------------
_CALIBER = (
    r"\.?223\b|5\.56|7\.62|\.308\b|\.30-06|9\s?mm|\.45\s?acp|\.40\s?s&w|"
    r"\.380\b|\.22\s?lr|\.22\b|12\s?ga(uge)?|20\s?ga(uge)?|\.410\b|"
    r"\.243\b|\.270\b|\.357\b|\.38\s?spl|\.44\s?mag"
)

US_ONLY_PATTERNS: tuple[tuple[str, str], ...] = (
    # An explicit export marker needs no corroboration.
    (r"\bitar\b|\bexport[- ]restricted\b",
     "explicitly marked export-restricted (ITAR)"),
    # A named firearm platform plus a part word.
    (rf"(?:{_GUN_CONTEXT})[\w\s\-/.,']{{0,40}}?(?:{_PART_WORDS})",
     "firearm part or magazine (US export-restricted)"),
    # A part word plus a named firearm platform, the other way round.
    (rf"(?:{_PART_WORDS})[\w\s\-/.,']{{0,40}}?(?:{_GUN_CONTEXT})",
     "firearm part or magazine (US export-restricted)"),
    # A caliber beside a part word — covers unbranded parts.
    (rf"(?:{_CALIBER})[\w\s\-/.,']{{0,40}}?(?:{_PART_WORDS})",
     "firearm part in a named caliber (US export-restricted)"),
    (rf"(?:{_PART_WORDS})[\w\s\-/.,']{{0,40}}?(?:{_CALIBER})",
     "firearm part in a named caliber (US export-restricted)"),
    # Whole categories that are US-only regardless of wording.
    (r"\bgun\s+parts?\b|\bfirearm\s+parts?\b|\brifle\s+parts?\b",
     "firearm parts (US export-restricted)"),
    (r"\bbody\s?armou?r\b|\bballistic\s+(vest|plate|panel)\b|\bplate\s+carrier\b",
     "body armor (US export-restricted)"),
    (r"\bsuppressor\b|\bsilencer\b", "suppressor (US-only, heavily regulated)"),
    (r"\bnight\s?vision\b|\bthermal\s+(scope|sight|imager)\b",
     "night vision / thermal optic (ITAR Cat. XII)"),
)

_TRUE = ("true", "yes", "1", "on")


def _haystack(draft: Any) -> str:
    """Title + item specifics only — never the body. See module docstring."""
    parts = [str(draft.get("title") or "")]
    for key in ("item_specifics.type", "item_specifics.material",
                "item_specifics.subject", "item_specifics.brand"):
        parts.append(str(draft.get(key) or ""))
    extra = draft.get("item_specifics.extra")
    if isinstance(extra, dict):
        for k, v in extra.items():
            parts.append(f"{k} {v}")
    return " ".join(parts)


def _gun_aspect_hits(draft: Any) -> list[str]:
    extra = draft.get("item_specifics.extra")
    if not isinstance(extra, dict):
        return []
    hits = []
    for key in extra:
        if str(key).strip().lower() in _GUN_ASPECT_KEYS:
            hits.append(f'carries the "{key}" item specific — eBay classes this '
                        f"as a firearm accessory")
    return hits


def us_only_reasons(draft: Any) -> list[str]:
    """Why this item must ship US-only. Empty list = no restriction found.

    Returns human-readable reasons, not booleans, so REVIEW can show the
    operator WHICH signal fired — a routing decision nobody can audit is a
    routing decision nobody will trust.
    """
    if str(draft.get("shipping.us_only") or "").strip().lower() in _TRUE:
        return ["shipping.us_only is set in the draft"]

    reasons = _gun_aspect_hits(draft)
    hay = _haystack(draft)
    for pattern, label in US_ONLY_PATTERNS:
        if re.search(pattern, hay, re.I):
            if label not in reasons:
                reasons.append(label)
    return reasons


def is_us_only(draft: Any) -> bool:
    return bool(us_only_reasons(draft))
