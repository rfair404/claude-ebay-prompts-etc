#!/usr/bin/env python3
"""The in-hand voice linter (GH #40).

Written after the 2026-08 sweep, where the rule was enforced by whatever regex
somebody typed that day. That regex matched `shown in` and `as shown` but not
bare `shown`, so it silently missed 12 LIVE listings. Every BLOCK case below is
a real phrase that shipped to a real buyer, and every EXEMPT case is real copy
that a naive checker would wrongly flag — the exemptions matter as much as the
catches, because a linter that cries wolf gets muted.

Run:  python tests/test_voice_check.py
  or: pytest tests/test_voice_check.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import voice_check as V                                      # noqa: E402
from draft_io import Draft                                   # noqa: E402


def _draft(cond: str = "", body: str = "", title: str = "A vintage thing",
           specifics: dict | None = None) -> Draft:
    fm = {"title": title, "condition_description": cond,
          "meta": {"notes": "internal: odor not verified; undersides not photographed"}}
    if specifics:
        fm["item_specifics"] = specifics
    return Draft(path=Path("draft.md"), frontmatter=fm, body=body)


def _blocks(draft) -> list[str]:
    return V.check_voice(draft)


# --- the phrases that shipped, and must now be caught --------------------

def test_blocks_real_phrases_that_reached_live_listings():
    cases = [
        "Knife handle seated tight in photos; not shake-tested.",
        "No wear-through or brassing noted on the surfaces shown.",
        "Boards square; no tears noted on the shown surfaces.",
        "Bindings intact in frames shown; order form present.",
        "Binding tight; no detached or missing pages in the frames shown.",
        "Not assessed: backs of interior panels; any soiling/foxing not shown.",
        "Exact page count and spine staples not assessable from photos.",
        "Cannot assess from photos: stone material beyond rhinestone.",
        "with no chips, cracks, flaking, or hairlines visible in any photo.",
        "Interior odor and lid seal untested.",
        "Photographed through the packaging.",
        "Sold uncleaned, as photographed.",
        "Original box worn. Sold as pictured.",
        "Undersides not photographed on every piece.",
        "height approximately 3 in, estimated - no vertical ruler frame was shot.",
        "the underside was examined at full resolution.",
        "Country of origin and UPC are not shown in the photographed surfaces.",
        "Surface not raking-light inspected - tiny fleabites cannot be ruled out.",
        "Measured off the photos rather than with a caliper.",
    ]
    for phrase in cases:
        assert _blocks(_draft(cond=phrase)), f"MISSED: {phrase!r}"


def test_bare_shown_is_caught():
    """The exact gap that let 12 live listings through."""
    assert _blocks(_draft(cond="no tears noted on the shown surfaces"))
    assert _blocks(_draft(cond="pages shown are clean and bright"))
    assert _blocks(_draft(body="- Only the top face is photographed."))


# --- the exemptions: correct copy that must NOT be flagged --------------

def test_standing_close_line_is_exempt():
    assert not _blocks(_draft(
        body="Please see the photos and read the description for full details."))
    assert not _blocks(_draft(body="Please review all photos."))


def test_pii_redaction_disclosure_is_exempt():
    """Required by the PII house rule — live on gilhes, lot2, the-eagle."""
    for phrase in [
        "the recipient name and address are redacted in the photos.",
        "addressee blocked out for privacy in the photos.",
        "subscriber name and street masked in the photos for privacy.",
        "the return address and store names are left visible.",
    ]:
        assert not _blocks(_draft(cond=phrase)), f"WRONGLY FLAGGED: {phrase!r}"


def test_sealed_item_limits_are_exempt():
    """A physical limit is not a camera limit."""
    for phrase in [
        "Odour cannot be assessed while the bag is sealed.",
        "Backs of pieces not assessable while sealed; pattern read from the fronts.",
        "Toning judged through the plastic; the piece was not unwrapped.",
    ]:
        assert not _blocks(_draft(cond=phrase)), f"WRONGLY FLAGGED: {phrase!r}"


def test_item_own_content_is_exempt():
    """'pictured' about the item's own pages/artwork is a description, not a hedge."""
    for phrase in [
        "a full player photo section in which every athlete is pictured and named",
        "Named styles pictured include the clayton wing-tip and the cooper slip-on.",
        "Catalog only - no merchandise pictured inside is included.",
        "Full-bleed editorial fashion photography by Fabrizio Ferri.",
    ]:
        assert not _blocks(_draft(body=phrase)), f"WRONGLY FLAGGED: {phrase!r}"


def test_in_hand_uses_of_the_same_words_are_exempt():
    for phrase in [
        "the gold bands show wear and rubbing from age and handling",
        "Full mirror polish on the show face, no scratch field.",
        "Saddle-stitched (staples visible in gutter); binding sound.",
        "light haze in the foil backing, visible under magnification only",
        "plating haze on the BACK of the ID plate only (not visible when worn)",
    ]:
        assert not _blocks(_draft(cond=phrase)), f"WRONGLY FLAGGED: {phrase!r}"


def test_grade_setting_untested_is_exempt():
    assert not _blocks(_draft(cond="UNTESTED; sold as-is as a vintage component."))


def test_clean_rewrites_pass():
    """The AFTER side of every fix the sweep applied must lint clean."""
    for phrase in [
        "Knife handle sits tight.",
        "No chips, cracks, flaking, or hairlines noted.",
        "The foot rim sits beneath the original label.",
        "Not collated page by page; completeness not individually verified.",
        "Spreader: 6 7/8 inches",
        "Piece count not individually verified against the standard set.",
        "Sold sealed in the original packaging; assessed through the bag.",
    ]:
        assert not _blocks(_draft(cond=phrase)), f"FALSE POSITIVE: {phrase!r}"


# --- scoping ------------------------------------------------------------

def test_internal_meta_is_never_scanned():
    """meta.notes MUST keep recording can't-assess observations."""
    d = _draft(cond="No chips or cracks noted.")
    d.frontmatter["meta"]["notes"] = ("odor not verified; undersides not photographed; "
                                      "not assessable from photos; sold as pictured")
    assert not _blocks(d)


def test_body_after_end_marker_is_internal():
    body = ("A clean vintage piece.\n\n"
            "<!-- END BUYER DESCRIPTION -->\n"
            "internal: undersides not photographed, odor not verified")
    assert not _blocks(_draft(body=body))


def test_item_specifics_are_scanned():
    d = _draft(specifics={"pattern": "as shown", "brand": "Rogers"})
    assert _blocks(d)


def test_title_is_scanned():
    assert _blocks(_draft(title="Vintage Brooch Sold As Pictured Estate Find"))


def test_warnings_do_not_block():
    """'no monogram visible' is true but off-idiom: warn, never block."""
    d = _draft(cond="No use-wear, no plate wear-through, and no monogram visible.")
    assert not _blocks(d)
    detailed = V.check_voice_detailed(d)
    assert any(f.severity == V.WARN for f in detailed)


def test_findings_carry_a_fix_hint():
    f = V.check_voice_detailed(_draft(cond="no chips visible in the photos"))[0]
    assert f.severity == V.BLOCK and f.fix and f.field == "condition_description"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{'FAILED' if fails else 'OK'} — {fails} failure(s)")
    raise SystemExit(1 if fails else 0)
