"""The Frame Check page: the picture a card shows must follow the radio.

The bug this suite exists for: the CSS that decides which picture a card shows
listed the option VALUES by hand — "0","90","180","270","on","off","__none__",
"studio","punch". The colour stage later grew `half`, `tenth` and `crisp`.
Picking any of those three matched no rule, so the card went blank and the
full-size preview opened empty — a page that looked finished and did nothing
for half the options on its most-used stage.

The fix matches on the option's INDEX, generated up to MAX_OPTS, so a stage can
add an option without anyone remembering to touch the stylesheet. These tests
hold that property: they fail if the rules go back to being hand-listed, if a
stage grows past the generated range, or if the markup stops carrying `data-i`.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.prep_sheet_html as P  # noqa: E402


def _card(n_options, stage="color"):
    """A card with n options, the second one proposed."""
    opts = [{"value": f"v{i}", "label": f"L{i}", "img": "data:image/jpeg;base64,AA",
             "spin": 0} for i in range(n_options)]
    return {"name": "ZZ1.JPG", "options": opts, "chosen": "v1",
            "status": "held", "note": "n", "why": ""}


def test_every_option_index_gets_a_rule():
    """Up to MAX_OPTS, each index selects its own picture — in the card and in
    the full-size preview alike, since both `.v` copies live inside `.card`."""
    css = P._pick_rules()
    for j in range(P.MAX_OPTS):
        assert f'.card:has(.pick.i{j}:checked) .v[data-i="{j}"]' in css, j
    assert css.rstrip().endswith("{display:block}")


def test_the_rules_are_generated_not_hand_listed():
    """No option VALUE may appear in the switching CSS. The moment one does,
    the stylesheet has to be edited every time a stage grows an option — which
    is exactly how `half`, `tenth` and `crisp` shipped broken."""
    css = P._pick_rules()
    for dead in ('[value="studio"]', '[value="punch"]', '[value="0"]',
                 '[value="on"]', '[value="off"]', 'data-v='):
        assert dead not in css, f"{dead} is hand-listed in the picture rules"


def test_the_template_asks_for_the_generated_rules():
    """A generator that never gets substituted in is the same bug again."""
    assert "__PICKRULES__" in P.TEMPLATE
    src = (ROOT / "tools" / "prep_sheet_html.py").read_text(encoding="utf-8")
    assert '.replace("__PICKRULES__", _pick_rules())' in src


def test_card_markup_carries_the_index_on_both_the_input_and_the_picture():
    html = P._card_html("color", _card(6), 0)
    for j in range(6):
        assert f'class="pick i{j}"' in html or f'class="pick proposed i{j}"' in html, j
        assert f'data-i="{j}"' in html, j
    # the chosen option is the one marked checked, exactly once
    assert html.count(" checked") == 1


def test_the_five_colour_looks_all_switch():
    """The regression itself, at the size the colour stage actually ships:
    as-shot plus studio / punch / half / tenth / crisp."""
    looks = ["__none__", "studio", "punch", "half", "tenth", "crisp"]
    opts = [{"value": v, "label": v, "img": "data:image/jpeg;base64,AA", "spin": 0}
            for v in looks]
    card = {"name": "ZZ1.JPG", "options": opts, "chosen": "crisp",
            "status": "held", "note": "", "why": ""}
    html = P._card_html("color", card, 0)
    css = P._pick_rules()
    for j, v in enumerate(looks):
        assert f'data-i="{j}" data-v="{v}"' in html, v
        assert f'.card:has(.pick.i{j}:checked) .v[data-i="{j}"]' in css, v


def test_too_many_options_raises_instead_of_rendering_a_dead_card():
    """Silently rendering a card whose choice cannot be shown is the failure
    mode this whole suite is about. Going past the generated range must stop."""
    try:
        P._card_html("color", _card(P.MAX_OPTS + 1), 0)
    except RuntimeError as e:
        assert "MAX_OPTS" in str(e)
    else:
        raise AssertionError("a card with more options than rules must raise")


def test_the_page_still_works_without_javascript():
    """Selection is native radios, the shown picture is a `:has()` rule, the
    tabs are a radio group and the previews are `:target`. Script assembles the
    command and nothing else, so no click may route through a handler."""
    src = (ROOT / "tools" / "prep_sheet_html.py").read_text(encoding="utf-8")
    body = src.split("TEMPLATE = r", 1)[1]
    assert "onclick" not in body, "inline onclick puts a click behind script"
    assert "createElement" not in body and "innerHTML" not in body, \
        "the DOM must be rendered from Python, not built in the browser"
    assert 'type="radio"' in P._card_html("color", _card(3), 0)
    assert ".big:target{display:grid}" in P.TEMPLATE
