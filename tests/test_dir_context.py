#!/usr/bin/env python3
"""lib/dir_context.py — key/prose parsing, cascade merge, and derived blocks.

Written in response to review feedback (both the human scrutiny pass and
Copilot): the module shipped with zero automated tests despite forbidding
buyer-facing claims (smoke-free, pet-free, climate-controlled) on legal /
disclosure grounds, where a silent regression in the negation or cascade
logic is a real-world liability, not just a bug.

Covers: the key-block vs. prose parsing boundary, nearest-wins override
across directory depth, each of the three block triggers with its negation
counter-example (including the "smoke-free" / "pet-free" case Copilot
flagged — the trigger regex matches the bare root word, e.g. "smok", so a
hyphenated negative qualifier like "smoke-free" used to read as evidence of
smoking rather than a denial of it), MergedContext.blocks()/brief() output
shape, and sweep() over a synthetic multi-level tree.

Run:  python tests/test_dir_context.py
  or: pytest tests/test_dir_context.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import dir_context as DC  # noqa: E402


_cleanup: list[Path] = []


def _make(files: dict) -> Path:
    """Write {relpath: content} under a fresh temp dir INSIDE the repo root
    (ContextFile.rel and sweep() both compute paths relative to dir_context's
    hardcoded REPO, so a synthetic tree has to live under it to round-trip)."""
    root = Path(tempfile.mkdtemp(prefix="dctx_", dir=str(DC.REPO)))
    _cleanup.append(root)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def teardown_module(module):  # picked up by pytest; also called from __main__
    for root in _cleanup:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------- parse()
def test_key_block_parses_leading_keys_then_prose():
    root = _make({"context.txt":
                  "source: Frankie, estate, Greensboro NC\n"
                  "environment: smoker\n"
                  "\n"
                  "She smoked most of her life.\n"})
    cf = DC.parse(root / "context.txt")
    assert cf.keys == {"source": "Frankie, estate, Greensboro NC",
                       "environment": "smoker"}
    assert cf.prose == "She smoked most of her life."


def test_single_letter_key_is_recognized():
    """The docstring promises keys are 'a single lowercase word', with no
    stated minimum length — a one-letter key like 'x:' must parse like any
    other, not silently fall through to prose."""
    root = _make({"context.txt": "x: shorthand value\n"})
    cf = DC.parse(root / "context.txt")
    assert cf.keys == {"x": "shorthand value"}
    assert cf.prose == ""


def test_prose_line_ending_in_colon_is_not_swallowed_as_a_key():
    """The narrow key regex is the whole point: a prose sentence that
    happens to end in a colon must stay prose, not become a bogus key with
    an empty/missing value."""
    root = _make({"context.txt":
                  "Items that I've purchased from my neighbors:\n"
                  "assorted estate lot, mixed sources.\n"})
    cf = DC.parse(root / "context.txt")
    assert cf.keys == {}
    assert "Items that I've purchased from my neighbors:" in cf.prose


def test_file_with_no_key_block_is_all_prose():
    root = _make({"context.txt": "Just background notes, no keys here.\n"})
    cf = DC.parse(root / "context.txt")
    assert cf.keys == {}
    assert cf.prose == "Just background notes, no keys here."


def test_file_with_only_keys_has_empty_prose():
    root = _make({"context.txt": "source: FR\nenvironment: smoker\n"})
    cf = DC.parse(root / "context.txt")
    assert cf.keys == {"source": "FR", "environment": "smoker"}
    assert cf.prose == ""


# ------------------------------------------------------------ chain_for() / load()
def test_chain_for_walks_root_to_item_outermost_first():
    root = _make({
        "context.txt": "source: FR\n",
        "books/context.txt": "storage: attic\n",
        "books/TEJ/draft.md": "---\ntitle: x\n---\n",
    })
    chain = DC.chain_for(root / "books" / "TEJ", root=root)
    assert [str(c.path.relative_to(root)) for c in chain] == [
        "context.txt", "books/context.txt"]


def test_nearest_wins_child_overrides_parent_key():
    root = _make({
        "context.txt": "storage: attic, unclimatized\n",
        "sub/context.txt": "storage: moved to climate-controlled unit in 2020\n",
    })
    ctx = DC.load(root / "sub", root=root)
    assert ctx.keys["storage"] == "moved to climate-controlled unit in 2020"


def test_absent_context_file_is_todays_behavior():
    root = _make({"item/draft.md": "---\ntitle: x\n---\n"})
    ctx = DC.load(root / "item", root=root)
    assert not ctx
    assert ctx.keys == {} and ctx.blocked == [] and ctx.prose == ""


# ------------------------------------------------------- _derive_blocks() / blocks()
def test_smoker_triggers_smoke_free_block():
    root = _make({"context.txt": "environment: smoker — indoors, occasional.\n"})
    ctx = DC.load(root, root=root)
    whys = {b.why for b in ctx.blocked}
    assert "someone smoked in the source home" in whys


def test_smoke_free_assertion_does_not_trigger_the_block():
    """Regression: the trigger regex matches the bare root ('smok'), so a
    context asserting 'smoke-free' must NOT read as evidence of smoking —
    that would forbid the estate's own true claim."""
    root = _make({"context.txt": "environment: smoke-free household.\n"})
    ctx = DC.load(root, root=root)
    whys = {b.why for b in ctx.blocked}
    assert "someone smoked in the source home" not in whys


def test_no_smoking_assertion_does_not_trigger_the_block():
    root = _make({"context.txt": "environment: non-smoking household.\n"})
    ctx = DC.load(root, root=root)
    assert not any(b.why == "someone smoked in the source home" for b in ctx.blocked)


def test_pets_trigger_pet_free_block():
    root = _make({"context.txt": "environment: two cats lived in the house.\n"})
    ctx = DC.load(root, root=root)
    assert any(b.why == "pets in the source home" for b in ctx.blocked)


def test_no_pets_assertion_does_not_block_pet_free():
    """'No pets in the house' must NOT block *pet-free* — it IS pet-free."""
    root = _make({"context.txt": "environment: no pets in the house.\n"})
    ctx = DC.load(root, root=root)
    assert not any(b.why == "pets in the source home" for b in ctx.blocked)


def test_unclimatized_storage_triggers_climate_controlled_block():
    root = _make({"context.txt": "storage: attic, decades in place.\n"})
    ctx = DC.load(root, root=root)
    assert any(b.why == "stored unclimatized" for b in ctx.blocked)


def test_climate_controlled_assertion_does_not_trigger_the_block():
    root = _make({"context.txt": "storage: climate-controlled unit throughout.\n"})
    ctx = DC.load(root, root=root)
    assert not any(b.why == "stored unclimatized" for b in ctx.blocked)


def test_merged_context_blocks_flags_text_making_a_forbidden_claim():
    root = _make({"context.txt": "environment: smoker.\n"})
    ctx = DC.load(root, root=root)
    hit = ctx.blocks("Comes from a smoke-free home, no odors.")
    assert len(hit) == 1 and hit[0].why == "someone smoked in the source home"
    assert ctx.blocks("Great condition, ships fast.") == []


def test_brief_lists_must_not_claim_and_prose():
    root = _make({"context.txt": "environment: smoker.\n\nSome background prose.\n"})
    text = DC.brief(root, root=root)
    assert "MUST NOT CLAIM" in text
    assert "Some background prose." in text


def test_brief_is_empty_string_with_no_context_file():
    root = _make({"item/draft.md": "---\ntitle: x\n---\n"})
    assert DC.brief(root / "item", root=root) == ""


# --------------------------------------------------------------------- sweep()
def test_sweep_finds_a_contradicted_claim_in_a_multilevel_tree():
    root = _make({
        "context.txt": "source: FR estate\n",
        "books/context.txt": "environment: smoker.\n",
        "books/TEJ/draft.md": "---\ntitle: A Book\n---\nComes from a smoke-free home.\n",
        "furniture/context.txt": "storage: attic, unclimatized.\n",
        "furniture/chair/draft.md":
            "---\ntitle: A Chair\n---\nClimate-controlled storage always.\n",
        "clean/draft.md": "---\ntitle: Clean Item\n---\nNo estate concerns here.\n",
    })
    hits = DC.sweep(root=root)
    dirs = {d for d, _line, _b in hits}
    assert any(d.endswith("books/TEJ") for d in dirs)
    assert any(d.endswith("furniture/chair") for d in dirs)
    assert not any(d.endswith("clean") for d in dirs)
    assert len(hits) == 2


def test_sweep_is_empty_when_nothing_contradicts():
    root = _make({
        "context.txt": "source: FR estate\n",
        "item/draft.md": "---\ntitle: x\n---\nAn ordinary description.\n",
    })
    assert DC.sweep(root=root) == []


def test_sweep_does_not_raise_when_root_is_outside_repo():
    """--root can point anywhere; sweep() must not assume REPO is an ancestor."""
    outside_root = Path(tempfile.mkdtemp(prefix="dctx_outside_"))
    _cleanup.append(outside_root)
    (outside_root / "context.txt").write_text("environment: smoker.\n", encoding="utf-8")
    item = outside_root / "item"
    item.mkdir()
    (item / "draft.md").write_text(
        "---\ntitle: x\n---\nComes from a smoke-free home.\n", encoding="utf-8")

    hits = DC.sweep(root=outside_root)
    assert len(hits) == 1
    d, _line, _b = hits[0]
    assert d.endswith("item")


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception:
            bad += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    teardown_module(None)
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
