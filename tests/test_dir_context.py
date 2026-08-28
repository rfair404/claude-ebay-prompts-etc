"""GH #46 — lib/dir_context.py: the context.txt parser + cascade.

`inventory/` is gitignored (real content is operator-local, sometimes
naming a person), so these tests run against
tests/fixtures/dir_context/inventory/, a mini tree shaped like the real
one: inventory/ESTATES/FR/books/TEJ/, plus the empty/prose-only shapes
already on disk for real (JUDGE, KIM, THRIFT/goodwill). No pytest
fixtures -- runs under tests/run_all.py too.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))

from dir_context import (  # noqa: E402
    DirContext,
    forbidden_claims,
    forbidden_phrases,
    load_context,
    parse_context_file,
    parse_context_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dir_context" / "inventory"


# --------------------------------------------------------------------- #
# parse_context_text: the three shapes that already exist on disk
# --------------------------------------------------------------------- #

def test_empty_file_yields_no_keys_no_prose():
    keys, prose = parse_context_text("")
    assert keys == {}
    assert prose == ""


def test_whitespace_only_file_is_treated_as_empty():
    keys, prose = parse_context_text("\n\n   \n")
    assert keys == {}
    assert prose == ""


def test_prose_only_file():
    text = (
        "A retired judge's personal collection, downsized on relocation.\n"
        "Kept in a climate-controlled home office, not storage.\n"
    )
    keys, prose = parse_context_text(text)
    assert keys == {}
    assert prose == text.strip()


def test_keys_plus_prose_file_matches_the_real_fr_shape():
    text = (FIXTURES / "ESTATES" / "FR" / "context.txt").read_text(encoding="utf-8")
    keys, prose = parse_context_text(text)
    assert keys == {
        "source": "[private], estate, Greensboro NC house",
        "acquired": "2026-06",
        "environment": "smoker — indoors, occasional. no pets.",
        "storage": "attic and closets, unclimatized; decades in place.",
        "era": "household accumulated ~1955-2010",
        "cost": "FREE",
    }
    assert prose.startswith("Someone in the house smoked indoors")
    assert 'Buyer-facing copy says "an estate" — never a name.' in prose
    # cost stays a string, never coerced to a number.
    assert keys["cost"] == "FREE"


def test_unknown_key_line_is_not_an_error_and_falls_into_prose():
    text = "wibble: not a real field\nSome ordinary sentence follows."
    keys, prose = parse_context_text(text)  # must not raise
    assert keys == {}
    assert "wibble: not a real field" in prose


def test_cost_string_like_spent_dollars_is_kept_as_a_string():
    keys, _ = parse_context_text("cost: spent $650\n")
    assert keys["cost"] == "spent $650"
    assert isinstance(keys["cost"], str)


def test_missing_file_parses_as_empty():
    cf = parse_context_file(FIXTURES / "no-such-dir")
    assert cf.is_empty
    assert cf.keys == {} and cf.prose == ""


# --------------------------------------------------------------------- #
# load_context: walking up + nearest-wins merge
# --------------------------------------------------------------------- #

def test_load_context_merges_three_levels_nearest_wins():
    item_dir = FIXTURES / "ESTATES" / "FR" / "books" / "TEJ"
    ctx = load_context(item_dir)

    # From FR/context.txt, unrefined by books/ (books/ never mentions them).
    assert ctx.environment == "smoker — indoors, occasional. no pets."
    assert ctx.storage == "attic and closets, unclimatized; decades in place."
    assert ctx.cost == "FREE"
    assert ctx.source == "[private], estate, Greensboro NC house"

    # From books/context.txt (nearer than FR/), a field FR/ never set.
    assert ctx.kind == "books"

    # TEJ/ itself has no context.txt, so only FR/ and books/ are "read".
    assert len(ctx.files_read) == 2
    assert ctx.files_read[0].name == "FR"
    assert ctx.files_read[1].name == "books"

    # Prose from both, general (FR) before specific (books) refinement.
    assert "smoked indoors" in ctx.merged_prose
    assert "Refines the estate context above" in ctx.merged_prose
    assert ctx.merged_prose.index("smoked indoors") < ctx.merged_prose.index("Refines")


def test_load_context_empty_placeholder_still_counted_but_changes_nothing():
    # THRIFT/context.txt has real content; THRIFT/goodwill/context.txt is
    # one of the six empty placeholders that already exist for real.
    item_dir = FIXTURES / "THRIFT" / "goodwill" / "item1"
    ctx = load_context(item_dir)
    assert ctx.cost == "varies"  # inherited from THRIFT/, goodwill/ added nothing
    assert len(ctx.files_read) == 2  # THRIFT/ and THRIFT/goodwill/ both "read"
    assert ctx.files_read[-1].name == "goodwill"


def test_load_context_absent_chain_is_todays_behavior():
    with tempfile.TemporaryDirectory() as td:
        item_dir = Path(td) / "some" / "item"
        item_dir.mkdir(parents=True)
        ctx = load_context(item_dir)
        assert not ctx.has_context
        assert ctx.public_summary == {}
        assert ctx.merged_prose == ""


def test_load_context_sub_estate_refines_without_restating():
    # ESTATES/FR/books/ never sets `environment`; the parent's still applies.
    ctx = load_context(FIXTURES / "ESTATES" / "FR" / "books" / "TEJ")
    assert ctx.field_sources["environment"].name == "FR"
    assert ctx.field_sources["kind"].name == "books"


# --------------------------------------------------------------------- #
# forbidden_claims: the DRAFT-side block list
# --------------------------------------------------------------------- #

def test_smoker_environment_forbids_smoke_free():
    ctx = load_context(FIXTURES / "ESTATES" / "FR" / "books" / "TEJ")
    assert "smoke-free" in forbidden_phrases(ctx)


def test_no_pets_in_environment_does_not_forbid_pet_free():
    # The real FR/context.txt text is "no pets" -- a negative constraint
    # about absence, not presence. Must not block the true claim.
    ctx = load_context(FIXTURES / "ESTATES" / "FR" / "books" / "TEJ")
    assert "pet-free" not in forbidden_phrases(ctx)


def test_pets_present_forbids_pet_free():
    ctx = DirContext(environment="house has two resident cats")
    assert "pet-free" in forbidden_phrases(ctx)


def test_unclimatized_storage_forbids_climate_controlled():
    ctx = DirContext(storage="attic, unclimatized, decades in place")
    assert "climate-controlled" in forbidden_phrases(ctx)


def test_no_environment_or_storage_forbids_nothing():
    ctx = DirContext()
    assert forbidden_claims(ctx) == []


def test_forbidden_claim_carries_reason_and_source_key():
    ctx = DirContext(environment="known smoker in the household")
    claims = forbidden_claims(ctx)
    hit = next(c for c in claims if c.phrase == "smoke-free")
    assert hit.source_key == "environment"
    assert "smoker" in hit.reason


# --------------------------------------------------------------------- #
# PII guardrail: `source:` never leaks into a display/public view
# --------------------------------------------------------------------- #

PRIVATE_NAME = "Jane Q. Homeowner"


def test_source_absent_from_public_summary():
    ctx = DirContext(source=PRIVATE_NAME, environment="smoker")
    assert "source" not in ctx.public_summary
    assert PRIVATE_NAME not in ctx.public_summary.values()


def test_source_absent_from_str_and_repr():
    ctx = DirContext(source=PRIVATE_NAME, environment="smoker", cost="FREE")
    assert PRIVATE_NAME not in str(ctx)
    assert PRIVATE_NAME not in repr(ctx)
    # but the accessor itself still has it, for genuine local-only use.
    assert ctx.source == PRIVATE_NAME


def test_source_still_readable_directly_for_local_use():
    ctx = load_context(FIXTURES / "ESTATES" / "FR" / "books" / "TEJ")
    assert ctx.source == "[private], estate, Greensboro NC house"
    assert ctx.source not in str(ctx)
    assert ctx.source not in repr(ctx)
