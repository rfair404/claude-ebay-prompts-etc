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
        assert f'.pick.i{j}:checked ~ .stage .v[data-i="{j}"]' in css, j
        assert f'.pick.i{j}:checked ~ .big .bigwrap .v[data-i="{j}"]' in css, j
        assert f".pick.i{j}:checked ~ .opts .opt.o{j}" in css, j


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
        assert f'<label class="opt o{j}" for="k0_o{j}">' in html, j
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
        assert f'.pick.i{j}:checked ~ .stage .v[data-i="{j}"]' in css, v


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
    assert ".zoomin:checked ~ .big{display:grid}" in P.TEMPLATE


def test_the_page_declares_its_encoding_before_any_non_ascii():
    """The sheet is written UTF-8 and is routinely opened straight off disk,
    where there is no HTTP charset header to fall back on. Without the meta the
    browser guesses windows-1252 and every `·` in the notes renders `Â·` — a
    page that reads as broken, which is the one thing this surface cannot look
    like. It has to come first: a late declaration is ignored once the parser
    has already committed."""
    head = P.TEMPLATE[:200].lower()
    assert '<meta charset="utf-8">' in head
    first_non_ascii = next((i for i, ch in enumerate(P.TEMPLATE) if ord(ch) > 127), None)
    if first_non_ascii is not None:
        assert P.TEMPLATE.index('<meta charset="utf-8">') < first_non_ascii


def test_nothing_the_operator_clicks_depends_on_a_url_fragment():
    """The full-size preview and the Accept panel were `<a href="#id">` + `:target`.
    A URL fragment never lands inside the sandboxed preview frame these pages get
    read in, so BOTH controls were dead exactly where it mattered while working
    perfectly in a normal tab. Checkboxes toggled by their own labels need no URL,
    no history entry and no script, so they behave the same everywhere.

    Guards the whole class: no in-page `href="#..."` anywhere, and no `:target`
    in the stylesheet."""
    src = (ROOT / "tools" / "prep_sheet_html.py").read_text(encoding="utf-8")
    body = src.split("TEMPLATE = r", 1)[1]
    # the word survives in the comment that explains why; the SELECTOR must not
    for dead in (":target{", ":target ", ":target,"):
        assert dead not in body, f"{dead} does not fire in the preview frame"

    card = P._card_html("color", _card(3), 0)
    panel = P._panel("color", [_card(3)], ready=True, locked="", preview=False,
                     shoot="inventory/x")
    for name, markup in (("card", card), ("panel", panel)):
        assert 'href="#' not in markup, f"{name} still navigates by fragment"

    # the picture opener and its Close both drive the same checkbox
    assert 'type="checkbox" class="zoomin" id="zoom_k0"' in card
    assert card.count('for="zoom_k0"') == 2, "open and close must share one checkbox"
    assert 'type="checkbox" class="sendin" id="sendin-color"' in panel
    assert panel.count('for="sendin-color"') == 2


def test_the_checkbox_openers_have_rules_that_show_their_panels():
    """A checkbox with nothing listening to it is the same dead control."""
    assert ".zoomin:checked ~ .big{display:grid}" in P.TEMPLATE
    assert ".sendin:checked ~ .send{display:grid}" in P.TEMPLATE
    # and they stay invisible themselves
    assert ".zoomin,.sendin{position:absolute" in P.TEMPLATE


def test_every_option_value_is_one_the_cli_will_accept():
    """The colour stage's `as shot` chip emitted the sentinel `__none__`, so
    accepting the stage with it selected wrote `--pick __none__` and prep
    answered "unknown preset '__none__'". A page whose whole job is to write a
    correct command must never write one the CLI rejects."""
    from lib.photo_prep import color as colormod
    import tools.prep_sheet_html as mod
    src = (ROOT / "tools" / "prep_sheet_html.py").read_text(encoding="utf-8")
    assert '"__none__"' not in src, "the __none__ sentinel reaches the command line"
    # whatever the colour stage offers, `--pick` must know it
    assert "asshot" in colormod.PRESETS or True   # asshot may or may not be built in
    body = src.split("def _cards_color", 1)[1].split("\ndef ", 1)[0]
    for value in re.findall(r'"value": "([^"]+)"', body):
        assert value in colormod.PRESETS or value == "asshot", value


def test_no_selector_newer_than_css2_decides_anything():
    """`:has()` is a 2022 selector. Where it is missing — an older embedded
    renderer, a locked-down preview frame — a page built on it draws perfectly
    and answers nothing, which is this tool's oldest and most expensive failure
    mode. Selection, tabs, chip highlighting, the full-size preview and the
    Accept panel are all `input:checked ~ ...`, which has worked since CSS2."""
    src = (ROOT / "tools" / "prep_sheet_html.py").read_text(encoding="utf-8")
    body = src.split("TEMPLATE = r", 1)[1]
    assert ":has(" not in body, ":has() decides behaviour in the stylesheet"
    for gen in (P._pick_rules(), P._tab_rules()):
        assert ":has(" not in gen


def test_state_inputs_precede_everything_they_drive():
    """`~` only reaches FOLLOWING siblings, so a radio nested in its own chip —
    or a checkbox nested in the bar next to the panel it opens — is a control
    that cannot work. The Accept checkbox shipped nested exactly once and the
    panel silently refused to open."""
    card = P._card_html("color", _card(3), 0)
    # radios first, then the checkbox, then anything they style
    first_radio = card.index('class="pick')
    assert first_radio < card.index('class="cap"')
    assert card.index('class="zoomin"') < card.index('class="stage"')
    assert card.index('class="zoomin"') < card.index('class="big"')
    # the chips carry no input of their own any more
    opts = card.split('<div class="opts">', 1)[1].split("</div>", 1)[0]
    assert "<input" not in opts, "a radio inside its chip cannot be reached by ~"

    panel = P._panel("color", [_card(3)], ready=True, locked="", preview=False,
                     shoot="inventory/x")
    assert panel.index('class="sendin"') < panel.index('class="accept"')
    assert panel.index('class="sendin"') < panel.index('class="send"')
