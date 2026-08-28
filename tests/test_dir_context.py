"""dir_context — bucket resolution and context.txt parsing (#46, #56).

Fixtures are built per-test under tmp_path rather than checked-in sample
directories: context.txt carries real acquisition cost and provenance, so no
sample of it belongs in a public repo (see .gitignore's business-data rule).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import dir_context as dc                                          # noqa: E402


def _tree(root: Path, layout: dict[str, str]) -> None:
    """layout: {"ESTATES/FR/context.txt": "kind: event\\nspend: 575\\n"}."""
    for rel, text in layout.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------- parse

def test_parse_empty_file(tmp_path):
    f = tmp_path / "context.txt"
    f.write_text("", encoding="utf-8")
    c = dc.parse_context_file(f)
    assert c["keys"] == {} and c["prose"] == ""


def test_parse_missing_file(tmp_path):
    c = dc.parse_context_file(tmp_path / "context.txt")
    assert c["keys"] == {} and c["prose"] == ""


def test_parse_prose_only(tmp_path):
    f = tmp_path / "context.txt"
    f.write_text("Picked up at a garage sale, no idea whose.", encoding="utf-8")
    c = dc.parse_context_file(f)
    assert c["keys"] == {}
    assert "garage sale" in c["prose"]


def test_parse_keys_then_prose(tmp_path):
    f = tmp_path / "context.txt"
    f.write_text(
        "kind: event\nspend: 575\nacquired: 2026-07\n\n"
        "An estate sale in Social Circle Georgia.\n",
        encoding="utf-8")
    c = dc.parse_context_file(f)
    assert c["keys"] == {"kind": "event", "spend": "575", "acquired": "2026-07"}
    assert c["prose"] == "An estate sale in Social Circle Georgia."


def test_key_line_inside_prose_is_not_a_key(tmp_path):
    """Once prose starts, "word: word" in a sentence stays prose."""
    f = tmp_path / "context.txt"
    f.write_text("kind: event\n\nCost basis: unclear, ask later.\n", encoding="utf-8")
    c = dc.parse_context_file(f)
    assert c["keys"] == {"kind": "event"}
    assert "Cost basis: unclear" in c["prose"]


# -------------------------------------------------------------- spend_amount

def test_spend_amount_none_and_blank_are_unrecorded():
    assert dc.spend_amount(None) is None
    assert dc.spend_amount("") is None
    assert dc.spend_amount("   ") is None


def test_spend_amount_free_is_zero_not_unrecorded():
    assert dc.spend_amount("FREE") == 0.0
    assert dc.spend_amount("free") == 0.0


def test_spend_amount_parses_dollar_and_plain():
    assert dc.spend_amount("$575") == 575.0
    assert dc.spend_amount("575") == 575.0
    assert dc.spend_amount("spent $650") == 650.0
    assert dc.spend_amount("1,169.50") == 1169.50


def test_spend_amount_unparseable_is_none():
    assert dc.spend_amount("a house") is None


# ----------------------------------------------------------------- bucket_for

def test_bucket_for_finds_owning_ancestor(tmp_path):
    root = tmp_path / "inventory"
    _tree(root, {"ESTATES/SCJ/context.txt": "kind: event\nspend: 575\n"})
    item = root / "ESTATES/SCJ/silver/tray-1"
    item.mkdir(parents=True)
    assert dc.bucket_for(item, root) == root / "ESTATES/SCJ"


def test_bucket_for_sub_lot_resolves_to_parent_bucket(tmp_path):
    """FREE/more-mags-444 is a sub-lot inside FREE, not its own bucket."""
    root = tmp_path / "inventory"
    _tree(root, {"FREE/context.txt": "kind: channel\n"})
    item = root / "FREE/more-mags-444/lot-1"
    item.mkdir(parents=True)
    assert dc.bucket_for(item, root) == root / "FREE"


def test_bucket_for_nested_bucket_wins_over_ancestor(tmp_path):
    root = tmp_path / "inventory"
    _tree(root, {
        "ESTATES/FR/context.txt": "kind: event\nspend: 400\n",
        "ESTATES/FR/books/context.txt": "these were in the damp basement\n",
    })
    item = root / "ESTATES/FR/books/TEJ"
    item.mkdir(parents=True)
    assert dc.bucket_for(item, root) == root / "ESTATES/FR/books"


def test_bucket_for_no_context_anywhere_is_none(tmp_path):
    root = tmp_path / "inventory"
    item = root / "MINE/whatever"
    item.mkdir(parents=True)
    assert dc.bucket_for(item, root) is None


def test_bucket_for_outside_root_is_none(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert dc.bucket_for(outside, tmp_path / "inventory") is None


# ------------------------------------------------------------- chain/context

def test_context_for_merges_nearest_wins(tmp_path):
    root = tmp_path / "inventory"
    _tree(root, {
        "ESTATES/FR/context.txt": "kind: event\nspend: 400\n\nSmoker in the house.\n",
        "ESTATES/FR/books/context.txt": "spend: 40\n\nDamp basement books.\n",
    })
    item = root / "ESTATES/FR/books/TEJ"
    item.mkdir(parents=True)
    ctx = dc.context_for(item, root)
    # spend refined by the sub-bucket; kind inherited unchanged from the parent
    assert ctx["keys"]["spend"] == "40"
    assert ctx["keys"]["kind"] == "event"
    assert "Smoker in the house." in ctx["prose"]
    assert "Damp basement books." in ctx["prose"]
    assert len(ctx["chain"]) == 2


def test_context_for_no_chain_is_empty(tmp_path):
    root = tmp_path / "inventory"
    item = root / "MINE/whatever"
    item.mkdir(parents=True)
    ctx = dc.context_for(item, root)
    assert ctx["keys"] == {} and ctx["prose"] == "" and ctx["chain"] == []


# ------------------------------------------------------------- is_backup_dir

def test_is_backup_dir():
    assert dc.is_backup_dir(Path("inventory/ESTATES/FR/_prepped/item"))
    assert dc.is_backup_dir(Path("inventory/ESTATES/FR/2026-08-01.prior-run-bak/item"))
    assert not dc.is_backup_dir(Path("inventory/ESTATES/FR/books/TEJ"))
