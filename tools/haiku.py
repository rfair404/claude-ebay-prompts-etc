#!/usr/bin/env python3
"""A small 5-7-5 haiku for the bottom of a pick sheet (GH #161).

Personalizes each printed pick list without printing anything personal on
it: the buyer's name never appears (pick_list_html.py already reduces it to
first-name-plus-initial for the "BUYER" block above this), so the haiku
draws only on two *thematic* signals instead — a word bank keyed to what
the item is (from the listing title) and one keyed to the region the order
is shipping to (from the ship-to state) — plus a closing line about the
parcel itself. Nothing in the output is ever the literal title, name, or
address text; every line comes from a small curated pool below, so there is
nothing to leak.

The selection is deterministic per order (seeded off the order id) so
re-rendering the same pick sheet — which happens whenever the tool is
re-run before a shipment goes out — always reproduces the same haiku rather
than handing the picker a different poem each time.

Line lengths are hand-picked against `_syllables()`, a standard
vowel-group heuristic (not true phonetic analysis — good enough for the
short, plain words used here). `tests/test_haiku.py` asserts every pool
line actually hits its target count under that same heuristic, so the
5-7-5 shape can't silently drift as lines are added or edited.
"""
from __future__ import annotations

import hashlib
import re

# --------------------------------------------------------------------------- #
# syllable heuristic — vowel-group count with a silent-e / -le adjustment.
# Only used by the test suite to keep the pools honest; the generator itself
# never counts syllables at runtime, it only picks whole pre-counted lines.
# --------------------------------------------------------------------------- #
def _syllables(word: str) -> int:
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    vowels = "aeiouy"
    count = 0
    prev_is_vowel = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_is_vowel:
            count += 1
        prev_is_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    if word.endswith("le") and len(word) > 2 and word[-3] not in vowels:
        count += 1
    return max(count, 1)


def line_syllables(line: str) -> int:
    """Total syllable count of a line, by the same heuristic as `_syllables`."""
    return sum(_syllables(w) for w in line.split())


# --------------------------------------------------------------------------- #
# item category, from a listing title -> a themed 5-syllable opening line.
# First matching category wins, so more specific keywords (e.g. "card")
# are listed ahead of ones that could also match a generic sense of "game".
# --------------------------------------------------------------------------- #
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cards": ("card", "tcg", "pokemon", "pokémon", "yugioh", "yu-gi-oh", "mtg"),
    "toys": ("figure", "figurine", "funko", "plush", "action figure", "doll", "model kit"),
    "electronics": ("console", "controller", "cable", "adapter", "charger", "headset", "camera"),
    "books": ("book", "comic", "manga", "novel", "magazine"),
    "games": ("game", "puzzle", "jigsaw", "dice"),
}

LINE1_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "games": ("Dice tumble and land", "Cards shuffle and wait",
              "Pawns line up to start", "The board opens wide"),
    "cards": ("Cards shuffle and wait", "A deck rests, unseen",
              "Bright pieces await", "Old wonders wait here"),
    "toys": ("Tiny hands reach out", "Bright pieces await",
              "Old wonders wait here", "Boxes wait for hands"),
    "electronics": ("Wires coil and rest", "Pixels wait to glow",
                     "Circuits sit and hum", "Screens wait, dark and still"),
    "books": ("Pages hold old ink", "Pages hold soft dust",
              "Old ink waits, unread", "Old wonders wait here"),
    "general": ("Boxes wait for hands", "Treasure found again",
                "Old wonders wait here", "Bright pieces await"),
}

# --------------------------------------------------------------------------- #
# ship-to state -> US Census region -> a themed 7-syllable middle line.
# --------------------------------------------------------------------------- #
_NORTHEAST = {"CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"}
_MIDWEST = {"IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"}
_SOUTH = {"DE", "FL", "GA", "MD", "NC", "SC", "VA", "DC", "WV", "AL", "KY",
          "MS", "TN", "AR", "LA", "OK", "TX"}
_WEST = {"AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"}

LINE2_BY_REGION: dict[str, tuple[str, ...]] = {
    "northeast": ("Rivers wind toward its home", "Northern winds carry it on",
                  "Miles fall behind it now"),
    "midwest": ("Across the fields it travels", "Through quiet prairie towns it rides",
                "Miles fall behind it now"),
    "south": ("Southern sun warms the long road", "Coastal fog wraps the journey",
              "Desert winds carry it home"),
    "west": ("Over mountains, far away", "Mountain peaks watch it travel",
             "Desert winds carry it home"),
    "other": ("Miles fall behind it now", "Beyond the city lights it goes",
              "Across the fields it travels"),
}

LINE3_CLOSING: tuple[str, ...] = (
    "Sent out with the sun", "Off it goes today", "Safe travels, dear box",
    "May it arrive well", "Handled with kind care", "The journey begins",
    "Thank you, safe travels", "Go well, small parcel",
)


def _region_for_state(state: str) -> str:
    code = (state or "").strip().upper()
    if code in _NORTHEAST:
        return "northeast"
    if code in _MIDWEST:
        return "midwest"
    if code in _SOUTH:
        return "south"
    if code in _WEST:
        return "west"
    return "other"


def _category_for_title(title: str) -> str:
    low = (title or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return category
    return "general"


def _pick(seed: str, pool: tuple[str, ...]) -> str:
    """Deterministically choose one line from `pool`, keyed by `seed` — same
    seed always returns the same line, different seeds spread across the
    pool. sha256 (not Python's salted `hash()`) so the choice is stable
    across runs and processes, which matters for re-rendering the same
    pick sheet before a shipment ships."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return pool[int(digest, 16) % len(pool)]


def generate_haiku(order_id: str, item_title: str, state: str) -> list[str]:
    """A 3-line haiku personalized to the order's item category and
    ship-to region — never to the buyer's name, which never enters this
    function at all. `order_id` seeds the (deterministic) line choices."""
    category = _category_for_title(item_title)
    region = _region_for_state(state)
    oid = order_id or "unknown-order"
    return [
        _pick(f"{oid}|1", LINE1_BY_CATEGORY[category]),
        _pick(f"{oid}|2", LINE2_BY_REGION[region]),
        _pick(f"{oid}|3", LINE3_CLOSING),
    ]
