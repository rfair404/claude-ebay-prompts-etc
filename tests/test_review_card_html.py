"""The REVIEW page: the surface the publish decision is made on.

The text card is what the ledger records; it is not what anyone can decide
from. This page is the official review surface, and these tests hold the parts
of it that were paid for in rounds of rework:

- it works with JavaScript OFF. Two JS-driven versions of the Frame Check page
  rendered perfectly and responded to nothing in the viewer the operator
  actually uses. Selection is native radios, the shown picture is `:has()`, the
  full-size view is `:target`.
- the picture is IN it. A gate that shows a title and a price is asking for
  approval from memory.
- the hero picker rewrites a command instead of pretending it can reach the
  CLI, and the command it writes is idempotent.
- everything flagged for a human survives to the page. A card you cannot argue
  with is not a review.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import tools.review_card_html as R  # noqa: E402

DRAFT = """---
title: "Test Item Title"
price: "42.00"
condition: "Used"
quantity: 1
best_offer:
  enabled: true
  auto_decline_amount: 30
condition_description: "Slight discoloration due to age."
photos:
  - listing/a.jpg
  - listing/b.jpg
shipping:
  primary_service: "USPSGroundAdvantage"
  weight: {major_lb: 1, minor_oz: 4}
  package_in: {length: 12, width: 9, depth: 2}
item_specifics:
  Brand: "J.Crew"
  extra: {Model: "Does Not Apply"}
meta:
  ebay_inventory_sku: "SKU-1"
---
# Description

Body paragraph.

- a bullet
  wrapped onto the next line
"""

CARD = """Preflight
  • condition remapped to Used
Needs review / manual intervention
  • the spine is cracked
"""


def _shoot(tmp_path):
    from PIL import Image
    s = tmp_path / "1"
    (s / "listing").mkdir(parents=True)
    for n in ("a.jpg", "b.jpg"):
        Image.new("RGB", (40, 30), (200, 180, 160)).save(s / "listing" / n)
    (s / "draft.md").write_text(DRAFT, encoding="utf-8")
    (s / "review_card.md").write_text(CARD, encoding="utf-8")
    return s


def _page(tmp_path):
    s = _shoot(tmp_path)
    return R.build(s).read_text(encoding="utf-8"), s


def test_the_page_runs_without_javascript(tmp_path):
    """No script tag, no inline handler, no href="javascript:". If the page
    ever needs one, it has stopped being usable where it is used."""
    html, _ = _page(tmp_path)
    assert "<script" not in html.lower()
    assert not re.search(r"\son(click|change|load|input)\s*=", html, re.I)
    assert "javascript:" not in html.lower()


def test_selection_is_native_radios_and_css(tmp_path):
    """The three mechanisms the page is allowed to use."""
    html, _ = _page(tmp_path)
    assert html.count('type="radio"') == 2          # one per frame
    assert 'name="hero"' in html
    assert "body:has(#h1:checked) .shot[data-i=\"1\"]" in html
    assert ".big:target{display:block}" in html


def test_the_photos_are_in_the_page(tmp_path):
    """Embedded, not linked — the page travels as one file."""
    html, _ = _page(tmp_path)
    assert html.count("data:image/jpeg;base64,") >= 2
    assert "--p0:url(data:image/jpeg;base64," in html
    assert 'href="#big0"' in html and 'id="big0"' in html   # click to enlarge


def test_the_hero_command_is_idempotent_and_per_frame(tmp_path):
    """`--set-hero <frame>` names the absolute choice. A relative form would
    move the frame again on every paste — the bug that made the Frame Check
    page use `--set-rotate` rather than `--rotate`."""
    html, shoot = _page(tmp_path)
    assert f"--set-hero {shoot.as_posix()} b.jpg" in html
    assert "already leads — nothing to run." in html       # frame 1 is a no-op
    # one command block per frame, each tied to its radio by index
    assert html.count('<pre class="cmd" data-i=') == 2


def test_the_decision_material_survives(tmp_path):
    """Price, condition disclosure, preflight and every flagged line."""
    html, _ = _page(tmp_path)
    for must in ("$42.00", "Slight discoloration due to age.",
                 "condition remapped to Used", "the spine is cracked",
                 "Test Item Title", "SKU-1", "Does Not Apply"):
        assert must in html, must


def test_nothing_publishes_from_the_page(tmp_path):
    """It shows the publish command; it cannot run one. The command keeps its
    --confirm guard so a paste is still a deliberate act."""
    html, shoot = _page(tmp_path)
    assert f"--list {shoot.as_posix()} --confirm" in html
    assert "<form" not in html.lower() and "<button" not in html.lower()


def test_a_wrapped_bullet_stays_with_its_bullet(tmp_path):
    """A continuation line that floats free reads as if a defect were an
    aside."""
    html, _ = _page(tmp_path)
    assert "<li>a bullet wrapped onto the next line</li>" in html


def test_it_is_theme_aware_in_both_directions(tmp_path):
    """Light palette on bare :root, dark under the media query AND under an
    explicit [data-theme] so the viewer's toggle wins either way."""
    html, _ = _page(tmp_path)
    assert "@media (prefers-color-scheme: dark)" in html
    assert ':root[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html
