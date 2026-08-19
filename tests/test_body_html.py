#!/usr/bin/env python3
"""The markdown-to-HTML pass that builds eBay's description.

Written after a live listing showed line breaks landing mid-sentence. Draft
bodies are wrapped at a sane column width, so a long bullet spills onto a second
line — and that continuation line does not begin with `-`. The converter treated
it as a new block: the list closed after the first line, the rest of the
sentence became its own paragraph, and the next bullet opened a fresh list.

Run:  python tests/test_body_html.py
  or: pytest tests/test_body_html.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import list_edit as L                                        # noqa: E402


def test_a_wrapped_bullet_stays_one_bullet():
    """The exact shape that shipped broken on listing 206502904506."""
    md = ("## What's Included\n\n"
          "- Locomotive with green bands, blue cab and a painted Christmas\n"
          "  tree panel, green dome, orange stack.\n"
          "- Flatcar with a striped ball.\n")
    html = L._body_to_html(md)

    assert html.count("<ul>") == 1, f"the list was split apart:\n{html}"
    assert html.count("<li>") == 2, f"expected 2 bullets, got:\n{html}"
    assert "<p>" not in html, f"a wrapped bullet became a paragraph:\n{html}"
    assert "Christmas tree panel" in html, "the wrap point lost its joining space"


def test_a_wrapped_paragraph_stays_one_paragraph():
    md = "Five wooden ornaments that are also\none train, about 15 inches long.\n"
    html = L._body_to_html(md)
    assert html.count("<p>") == 1
    assert "also one train" in html


def test_a_blank_line_still_ends_the_list():
    """Lazy continuation must not swallow whatever follows the list."""
    md = ("- first\n- second\n\n"
          "A paragraph after the list.\n")
    html = L._body_to_html(md)
    assert html.count("<li>") == 2
    assert "<p>A paragraph after the list.</p>" in html
    assert html.index("</ul>") < html.index("<p>"), "the list must close first"


def test_a_heading_ends_the_list_without_a_blank_line():
    md = "- first\n- second\n## Condition\n- a defect\n"
    html = L._body_to_html(md)
    assert html.count("<ul>") == 2, f"heading did not break the list:\n{html}"
    assert "<h3>Condition</h3>" in html


def test_headings_and_bullets_survive_a_real_body():
    md = ("# Description\n\n"
          "Opening line.\n\n"
          "## What's Included\n\n"
          "- one\n"
          "- two that wraps onto\n"
          "  a second line\n\n"
          "## Condition\n\n"
          "- a defect described plainly\n")
    html = L._body_to_html(md)
    assert html.count("<ul>") == 2
    assert html.count("<li>") == 3
    assert "two that wraps onto a second line" in html


def test_the_defect_text_is_never_dropped():
    """Whatever the structure does, disclosure has to survive it."""
    md = ("## Condition\n\n"
          "- Locomotive cab, right side: a patch of paint about half an inch\n"
          "  across pulled away to bare wood, with adhesive residue in it.\n")
    html = L._body_to_html(md)
    assert "adhesive residue in it" in html
    assert "half an inch across pulled away" in html


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                               # noqa: BLE001
            bad += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
