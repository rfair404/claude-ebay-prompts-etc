#!/usr/bin/env python3
"""A small 5-7-5 haiku for the bottom of a pick sheet (GH #161).

Personalizes each printed pick list without printing anything personal on
it: the buyer's name never appears (pick_list_html.py already reduces it to
first-name-plus-initial for the "BUYER" block above this), so the haiku
draws only on two *thematic* signals instead — what the item is (from the
listing title) and the region the order is shipping to (from the ship-to
state) — to picture the thing in use where the buyer is. Nothing in the output is ever the literal title, name, or
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
# Words the vowel-group rule miscounts, pinned to how they're said. Add one
# here rather than bending a good line to fit the heuristic.
_SYLLABLE_OVERRIDES = {
    "becomes": 2, "unwrapped": 2, "evenings": 2, "basement": 2,
    "nowhere": 2, "snowed": 1, "radiators": 4,
}


def _syllables(word: str) -> int:
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    if word in _SYLLABLE_OVERRIDES:
        return _SYLLABLE_OVERRIDES[word]
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
# The voice (locked in with the owner on #164): the poem is about the thing
# being USED, where the buyer is — not about the parcel travelling, and not
# scenery for its own sake. Plain words, concrete, a dry aside at the end.
#   line 1 (5)  the item in use           — keyed to the item's category
#   line 2 (7)  where they are            — a household scene, flavored by
#                                           the ship-to region
#   line 3 (5)  how it goes               — keyed to the category again, so
#                                           the poem reads as one scene
# e.g. checkers to CA:  Red jumps black. King me. / Out back where the
#                       evenings cool, / someone sulks. Rematch.
# New lines should pass the same test: could this only be about this kind
# of thing, in use? "Boxes wait for hands" could be anything — cut it.
# --------------------------------------------------------------------------- #

# Item category, from the listing title. First match wins, so the narrow
# categories (checkers, chess) come ahead of the broad "games". Keywords
# match whole words (plural "s" allowed) so "card" never hits "cardboard".
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "checkers": ("checkers", "checker", "draughts"),
    "chess": ("chess",),
    "cards": ("card", "tcg", "pokemon", "pokémon", "yugioh", "yu-gi-oh", "mtg", "deck"),
    "toys": ("toy", "figure", "figurine", "funko", "plush", "action figure", "doll",
             "model kit"),
    "electronics": ("console", "controller", "cable", "adapter", "charger", "headset",
                    "camera", "radio"),
    "books": ("book", "comic", "manga", "novel", "magazine"),
    "games": ("game", "board game", "puzzle", "jigsaw", "dice", "dominoes",
              "backgammon", "cribbage"),
}

LINE1_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "checkers": ("Red jumps black. King me.", "Double jump. King me."),
    "chess": ("Pawn up two. Your move.",),
    "games": ("Board out, pieces set", "Shuffle, roll, and deal"),
    "cards": ("Draw seven, shuffle",),
    "toys": ("Wind it up, let go", "Floor becomes a town"),
    "books": ("Chapter one, again", "Pages turn at night"),
    "electronics": ("Plug in. Hear it hum.", "Power light goes green"),
    "general": ("Unwrapped, put to use", "Finds its shelf, its job"),
}

LINE3_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "checkers": ("someone sulks. Rematch.",),
    "chess": ("mate in three. Reset.",),
    "games": ("loser sets it up.", "best two out of three."),
    "cards": ("nobody folds first.",),
    "toys": ("grown-ups play it too.",),
    "books": ("one more, then the light.",),
    "electronics": ("still works. Told you so.",),
    "general": ("right where it belongs.",),
}

# --------------------------------------------------------------------------- #
# ship-to state -> US Census region -> where the thing gets used (7 syllables).
# --------------------------------------------------------------------------- #
_NORTHEAST = {"CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"}
_MIDWEST = {"IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"}
_SOUTH = {"DE", "FL", "GA", "MD", "NC", "SC", "VA", "DC", "WV", "AL", "KY",
          "MS", "TN", "AR", "LA", "OK", "TX"}
_WEST = {"AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"}

LINE2_BY_REGION: dict[str, tuple[str, ...]] = {
    "west": ("on a porch in the late sun,", "out back where the evenings cool,"),
    "south": ("on the porch while the tea sweats,", "screen door slapping, fan on high,"),
    "midwest": ("kitchen table, snow outside,", "basement rec room, Friday night,"),
    "northeast": ("by the radiator's knock,", "snowed in, nowhere else to be,"),
    "other": ("at your kitchen table, now,",),
}


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
        if any(re.search(rf"\b{re.escape(kw)}s?\b", low) for kw in keywords):
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
    """A 3-line haiku about the item in use where the buyer is: category
    for lines 1 and 3, ship-to region for line 2 — never the buyer's name,
    which never enters this function at all. `order_id` seeds the
    (deterministic) line choices."""
    category = _category_for_title(item_title)
    region = _region_for_state(state)
    oid = order_id or "unknown-order"
    return [
        _pick(f"{oid}|1", LINE1_BY_CATEGORY[category]),
        _pick(f"{oid}|2", LINE2_BY_REGION[region]),
        _pick(f"{oid}|3", LINE3_BY_CATEGORY[category]),
    ]
