"""In-hand voice linter + draft state resolution — the GH #40 acceptance set.

Every block/exemption example here is a real phrase from the 2026-08 live
sweep (the five listings named in the issue). No pytest fixtures — runs
under tests/run_all.py too.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))

from voice_check import check_voice  # noqa: E402
from draft_io import parse_draft  # noqa: E402


def _draft(body: str, cond: str = "Clean copy.", title: str = "Vintage Book") -> object:
    tmpl = "\n".join([
        "---",
        "template_version: v1",
        "meta:",
        "  item_id: t",
        '  ebay_inventory_sku: "deadbeef"',
        "  ebay_offer_id: null",
        "  ebay_listing_id: null",
        '  notes: "UPC not shown in the photographed surfaces"',
        f'title: "{title}"',
        "condition: USED_EXCELLENT",
        f'condition_description: "{cond}"',
        "item_specifics:",
        "  type: Book",
        'price: "10.00"',
        "quantity: 1",
        "photos:",
        "  - a.jpg",
        "---",
        body,
    ])
    td = tempfile.mkdtemp()
    p = Path(td) / "draft.md"
    p.write_text(tmpl, encoding="utf-8")
    return parse_draft(p)


def _blocks(findings):
    return [f for f in findings if f.startswith("voice (block)")]


def test_shown_surfaces_blocks_and_noted_passes():
    # Acceptance #1, verbatim from a-brief-history-of-time.
    assert _blocks(check_voice(_draft("No tears noted on the shown surfaces.")))
    assert not check_voice(_draft("No tears noted."))


def test_the_sweeps_missed_phrases_all_block():
    for phrase in [
        "One was taken out only for these photographs.",     # car-set
        "Light wear in the frames shown.",                   # style-incentives
        "Condition not assessable from photos.",             # style-incentives
        "No vertical ruler frame was shot.",                 # bronze-dog
        "The surface was examined at full resolution.",      # bronze-dog
        "Stones intact on the surfaces shown.",              # broach
        "Backstamp not verified from photos.",               # broach
        "Ships as-shown.",
        "Knife handle seated tight; not shake-tested.",
        "Odor not verified.",
    ]:
        assert _blocks(check_voice(_draft(phrase))), phrase


def test_exemptions_do_not_flag():
    for phrase in [
        "Please see the photos and read the description for full details.",
        "The name and street on the mailing label are masked in the photos.",
        "The backstamp cannot be read through the sealed poly sleeve.",
        "Untested; sold as-is.",
        "Every player pictured and named on the back cover.",
        "Photography by Fabrizio Ferri.",
        "Staples visible in the gutter.",                    # in-hand use
        "Shows light wear at the corners.",
    ]:
        assert not _blocks(check_voice(_draft(phrase))), phrase


def test_no_x_visible_warns_but_does_not_block():
    fs = check_voice(_draft("No chips or cracks visible."))
    assert not _blocks(fs)
    assert any(f.startswith("voice (warn)") for f in fs)


def test_honest_odor_disclosure_is_not_blocked():
    # The honesty ground rule outranks the regex: a real defect must survive.
    fs = check_voice(_draft("Slight musty odor from storage."))
    assert not _blocks(fs)


def test_meta_notes_are_never_scanned():
    # The template's meta.notes carries banned copy on purpose.
    for f in check_voice(_draft("Clean copy throughout.")):
        assert "meta" not in f


def test_condition_description_is_scanned():
    fs = check_voice(_draft("Clean.", cond="Wear as shown in the photos."))
    assert any("condition_description" in f for f in _blocks(fs))


def test_validate_draft_for_sync_blocks_camera_copy():
    import list_edit

    td = Path(tempfile.mkdtemp())
    (td / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
    (td / "draft.md").write_text("\n".join([
        "---",
        "template_version: v1",
        "meta:",
        '  ebay_inventory_sku: "deadbeef"',
        'title: "Vintage Book"',
        "condition: USED_EXCELLENT",
        'condition_description: "No tears noted on the shown surfaces."',
        "item_specifics:",
        "  type: Book",
        'price: "10.00"',
        "quantity: 1",
        "photos:",
        "  - a.jpg",
        "---",
        "A clean vintage book from a local estate, described honestly.",
    ]), encoding="utf-8")
    issues = list_edit.validate_draft_for_sync(td / "draft.md")
    assert any(i.startswith("voice (block)") for i in issues), issues

    (td / "draft.md").write_text(
        (td / "draft.md").read_text(encoding="utf-8").replace(
            "No tears noted on the shown surfaces.", "No tears noted."),
        encoding="utf-8")
    issues = list_edit.validate_draft_for_sync(td / "draft.md")
    assert not any(i.startswith("voice") for i in issues), issues


def test_resolve_draft_state_flags_stale_meta():
    # christmas-elk's shape: meta says null, eBay says live. API mocked.
    import list_edit

    td = Path(tempfile.mkdtemp())
    (td / "draft.md").write_text("\n".join([
        "---",
        "meta:",
        '  ebay_inventory_sku: "deadbeef"',
        "  ebay_offer_id: null",
        "  ebay_listing_id: null",
        'title: "Elk"',
        "---",
        "body",
    ]), encoding="utf-8")

    real = list_edit.offer_sellable_state
    list_edit.offer_sellable_state = lambda sku, creds: dict(
        sellable=True, status="PUBLISHED", quantity=1,
        offer_id="9090", listing_id="206494264413", reason="")
    try:
        st = list_edit.resolve_draft_state(td / "draft.md", creds=object())
    finally:
        list_edit.offer_sellable_state = real
    assert st["stale"] and st["listing_id"] == "206494264413"
    assert st["sku"] == "deadbeef"

    # Correct meta -> not stale.
    (td / "draft.md").write_text(
        (td / "draft.md").read_text(encoding="utf-8")
        .replace("ebay_offer_id: null", 'ebay_offer_id: "9090"')
        .replace("ebay_listing_id: null", 'ebay_listing_id: "206494264413"'),
        encoding="utf-8")
    list_edit.offer_sellable_state = lambda sku, creds: dict(
        sellable=True, status="PUBLISHED", quantity=1,
        offer_id="9090", listing_id="206494264413", reason="")
    try:
        st = list_edit.resolve_draft_state(td / "draft.md", creds=object())
    finally:
        list_edit.offer_sellable_state = real
    assert not st["stale"]
