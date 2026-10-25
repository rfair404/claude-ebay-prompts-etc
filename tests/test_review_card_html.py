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


def test_render_returns_exactly_what_build_writes(tmp_path):
    # webapp/server.py's /review/{shoot} route (#31 Phase 2) calls render()
    # directly instead of build() + read — the two must never drift apart.
    s = _shoot(tmp_path)
    from_build = R.build(s).read_text(encoding="utf-8")
    from_render = R.render(s)
    assert from_render == from_build


def test_page_declares_utf8_first(tmp_path):
    # Served as bare text/html (the offsite link) or opened from disk, a page
    # without this renders "—" as "â€”". Browsers only honour it near the top.
    page = R.render(_shoot(tmp_path))
    assert page.startswith('<meta charset="utf-8">')


def test_render_does_not_write_a_file(tmp_path):
    s = _shoot(tmp_path)
    assert not (s / "review_card.html").exists()
    R.render(s)
    assert not (s / "review_card.html").exists()


def test_render_survives_an_empty_meta_block(tmp_path):
    # `meta:` present with nothing under it parses as `meta: None` in YAML,
    # not `{}` — fm.get("meta", {}) then returns None, and .get() on it used
    # to raise AttributeError instead of falling through to "not recorded".
    from PIL import Image
    s = tmp_path / "1"
    (s / "listing").mkdir(parents=True)
    Image.new("RGB", (40, 30), (200, 180, 160)).save(s / "listing" / "a.jpg")
    (s / "draft.md").write_text(
        '---\ntitle: "No Meta Yet"\nprice: "9.00"\nmeta:\nphotos:\n'
        '  - listing/a.jpg\n---\n# Description\nbody.\n', encoding="utf-8")
    html = R.render(s)
    assert "No Meta Yet" in html
    assert "sku not recorded" in html


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


def test_the_cover_picker_is_per_frame_and_carries_no_command(tmp_path):
    """The page offers a cover choice, not a command to paste.

    It used to print `--set-hero <shoot> <frame>` per frame, and this test used
    to assert that. The page stopped doing that on purpose: the review is a
    conversation now, the picker reports its choice in the chat, and the page
    says so in as many words — "for looking, not for doing".

    What survives from the old test is the part that mattered: the choice is
    made per frame and is unambiguous. One radio per frame, in one group, so
    exactly one cover can be chosen and the first is pre-selected.
    """
    html, _shoot = _page(tmp_path)
    assert html.count('type="radio"') == 2                 # one per frame
    assert html.count('name="hero"') == 2                  # one group
    assert 'id="h0" checked' in html                       # frame 1 leads
    assert "--set-hero" not in html                        # no command to paste


def test_the_decision_material_survives(tmp_path):
    """Price, condition disclosure, preflight and every flagged line."""
    html, _ = _page(tmp_path)
    for must in ("$42.00", "Slight discoloration due to age.",
                 "condition remapped to Used", "the spine is cracked",
                 "Test Item Title", "SKU-1", "Does Not Apply"):
        assert must in html, must


def test_nothing_publishes_from_the_page(tmp_path):
    """The page cannot publish, and no longer even hands over the command.

    The guarantee got STRONGER, not weaker. It used to show
    `--list <shoot> --confirm` for the operator to paste, and this test held
    that the --confirm guard survived. There is now no publish command on the
    page at all: it states its own role — "for looking, not for doing" — and the
    decision is taken in the chat.

    So the assertion is the absence of every affordance, runnable or copyable.
    """
    html, _shoot = _page(tmp_path)
    low = html.lower()
    assert "<form" not in low and "<button" not in low     # nothing runnable
    assert "--list" not in html and "--confirm" not in html  # nothing to paste
    assert "not for doing" in html                         # and it says so


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


# #156 §5 — the store a draft publishes to is part of what is being approved.
def test_the_page_names_the_drafts_store(tmp_path):
    s = _shoot(tmp_path)
    (s / "draft.md").write_text(DRAFT.replace('quantity: 1\n', 'quantity: 1\nstore: junk\n'),
                                encoding="utf-8")
    page = R.render(s)
    assert "store: junk" in page


def test_no_store_field_reads_as_the_default_store(tmp_path):
    page = R.render(_shoot(tmp_path))
    assert "store: default" in page


def test_a_malformed_store_name_is_flagged_not_rendered_raw(tmp_path):
    s = _shoot(tmp_path)
    (s / "draft.md").write_text(DRAFT.replace('quantity: 1\n', 'quantity: 1\nstore: "<b>x</b>"\n'),
                                encoding="utf-8")
    page = R.render(s)
    assert "not a valid store name" in page
    assert "<b>x</b>" not in page


# ---------------------------------------------------------------------------
# The link: the local app's /review/<shoot>, never an offsite upload
# ---------------------------------------------------------------------------

def test_review_url_is_the_local_app_path_under_inventory(tmp_path):
    inv = tmp_path / "inventory"
    (inv / "fr" / "arquilt").mkdir(parents=True)
    (inv / "Estate Lot 3").mkdir()
    assert R.review_url(inv / "fr" / "arquilt", inv) == \
        "http://127.0.0.1:8770/review/fr/arquilt"
    assert R.review_url(inv / "Estate Lot 3", inv) == \
        "http://127.0.0.1:8770/review/Estate%20Lot%203"
    assert R.review_url(tmp_path / "elsewhere", inv) is None
    assert R.review_url(inv, inv) is None


def test_main_prints_the_local_link_and_uploads_nothing(tmp_path, monkeypatch, capsys):
    s = _shoot(tmp_path)
    monkeypatch.setattr(R, "review_url", lambda shoot: "http://127.0.0.1:8770/review/1")
    monkeypatch.setattr(R.pick_store, "server_is_up", lambda *a, **k: True)
    monkeypatch.setitem(sys.modules, "offsite", None)    # any import of it fails
    assert R.main([str(s)]) == 0
    out = capsys.readouterr()
    assert "review: http://127.0.0.1:8770/review/1" in out.out
    assert "share" not in out.out + out.err
    assert (s / "review_card.html").exists()


def test_main_says_so_when_the_local_app_is_down(tmp_path, monkeypatch, capsys):
    s = _shoot(tmp_path)
    monkeypatch.setattr(R, "review_url", lambda shoot: "http://127.0.0.1:8770/review/1")
    monkeypatch.setattr(R.pick_store, "server_is_up", lambda *a, **k: False)
    assert R.main([str(s)]) == 0
    assert "lib.cli serve" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The money line: what the buyer pays, from the draft and the resolved policy
# ---------------------------------------------------------------------------

def _price_fact(page):
    m = re.search(r"<dt>Price</dt><dd>(.*?)</dd>", page)
    return re.sub(r"<[^>]+>", "", m.group(1))


def _with(tmp_path, *, fm_extra="", ship_extra="", card=CARD):
    s = _shoot(tmp_path)
    d = DRAFT.replace('quantity: 1\n', 'quantity: 1\n' + fm_extra)
    d = d.replace('  primary_service: "USPSGroundAdvantage"\n',
                  '  primary_service: "USPSGroundAdvantage"\n' + ship_extra)
    (s / "draft.md").write_text(d, encoding="utf-8")
    (s / "review_card.md").write_text(card, encoding="utf-8")
    return R.render(s)


FREE_CARD = ("Preflight\n  • shipping: using default ground policy -> "
             "'Free USPS Ground' (USPSParcel) · seller pays (free shipping)\n")
CALC_CARD = ("Preflight\n  • shipping: using default ground policy -> "
             "'Buyer pays calculated - USPS Ground (1 day)' (USPSParcel) · buyer pays calculated\n")


def test_fixed_price_with_free_shipping_reads_as_delivered(tmp_path):
    page = _with(tmp_path, ship_extra="  free_shipping: true\n", card=FREE_CARD)
    assert _price_fact(page) == "$42.00 delivered — free shipping"


def test_fixed_price_with_calculated_shipping_never_says_free(tmp_path):
    page = _with(tmp_path, ship_extra='  free_shipping: false\n  domestic_shipping_type: "CALCULATED"\n',
                 card=CALC_CARD)
    assert _price_fact(page) == "$42.00 + buyer-paid calculated shipping"
    assert "free shipping" not in page and "delivered" not in _price_fact(page)


def test_calculated_shipping_from_the_draft_when_the_card_predates_the_policy_word(tmp_path):
    # Cards written before preflight said who pays: the draft decides.
    page = _with(tmp_path, ship_extra='  free_shipping: false\n  domestic_shipping_type: "CALCULATED"\n')
    assert _price_fact(page) == "$42.00 + buyer-paid calculated shipping"


def test_an_auction_shows_its_start_bid_and_length(tmp_path):
    page = _with(tmp_path, fm_extra='format: "AUCTION"\nlisting_duration: "DAYS_10"\n',
                 ship_extra='  free_shipping: false\n  domestic_shipping_type: "CALCULATED"\n',
                 card=CALC_CARD)
    assert _price_fact(page) == "start bid $42.00 · 10-day auction · buyer pays calculated shipping"
    assert "delivered" not in page
    assert "start bid · 10-day auction" in page              # header, under the amount


def test_the_policy_wins_and_a_disagreeing_draft_is_flagged(tmp_path):
    # The draft's free_shipping is read by nothing at publish; the policy is.
    page = _with(tmp_path, ship_extra="  free_shipping: true\n", card=CALC_CARD)
    fact = _price_fact(page)
    assert fact.startswith("$42.00 + buyer-paid calculated shipping")
    assert "the draft says seller pays (free shipping)" in fact


def test_unknown_payer_is_said_not_guessed(tmp_path):
    page = R.render(_shoot(tmp_path))
    assert "who pays shipping is not stated" in _price_fact(page)
    assert "free shipping" not in _price_fact(page)


# ---------------------------------------------------------------------------
# Same page, smaller facts
# ---------------------------------------------------------------------------

def test_condition_shows_what_the_buyer_sees_not_the_enum(tmp_path):
    card = 'Preflight\n  • condition: USED_EXCELLENT OK for category -> buyer sees "Pre-owned"\n'
    page = _with(tmp_path, card=card)
    m = re.search(r"<dt>Condition</dt><dd>(.*?)</dd>", page)
    assert re.sub(r"<[^>]+>", "", m.group(1)) == "Pre-owned"


def test_the_page_scales_on_a_phone(tmp_path):
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in R.render(_shoot(tmp_path))


def test_the_cover_hint_names_no_command_the_page_lacks(tmp_path):
    page = R.render(_shoot(tmp_path))
    assert "command" not in re.search(r'<p class="hint">(.*?)</p>', page, re.S).group(1)


def test_a_comps_board_in_the_folder_is_embedded_without_scripts(tmp_path):
    s = _shoot(tmp_path)
    assert "Comps board" not in R.render(s)
    (s / "comps_board.html").write_text(
        '<!doctype html><h3>EXACT COPY $19.84</h3><script>alert(1)</script>', encoding="utf-8")
    page = R.render(s)
    assert "Comps board" in page and "EXACT COPY $19.84" in page
    assert "<script" not in page.lower()                   # escaped into srcdoc
    frame = re.search(r"<iframe[^>]*>", page).group(0)
    assert 'sandbox="allow-popups allow-popups-to-escape-sandbox"' in frame


def test_a_supersedes_line_hides_the_flag_it_quotes(tmp_path):
    card = ("Needs review / manual intervention\n"
            "  • - [PRICE] tiers are active asks, run Stage B\n"
            "  • - [DRAFT] photos point at ORIGINALS; PREP not run\n"
            "  • - [PREP] AD re-signed. Supersedes the [DRAFT] \"photos point at ORIGINALS\" line.\n")
    page = _with(tmp_path, card=card)
    flagged = re.search(r"<h2>Flagged for a human</h2>\s*<ul class=\"flags\">(.*?)</ul>", page, re.S).group(1)
    assert "photos point at ORIGINALS; PREP not run" not in flagged
    assert "run Stage B" in flagged                         # unrelated flag stays
    settled = re.search(r'<details class="settled">(.*?)</details>', page, re.S).group(1)
    assert "1 earlier line settled" in settled and "PREP not run" in settled


def test_a_resolved_line_never_hides_a_flag_that_is_still_open(tmp_path):
    # TD repro on #349: "RESOLVED: price" used to fold the open label-weight flag.
    card = ("Needs review / manual intervention\n"
            "  • - [DRAFT] weight is a guess; price the label\n"
            "  • - 2026-10-24 RESOLVED: price — Push-high confirmed\n")
    page = _with(tmp_path, card=card)
    flagged = re.search(r"<h2>Flagged for a human</h2>\s*<ul class=\"flags\">(.*?)</ul>", page, re.S).group(1)
    assert "weight is a guess; price the label" in flagged
    assert '<details class="settled">' not in page
