"""single-pass mode (V4_PLAN Phase 4, #30): the gate-check orchestrator.

Three things are load-bearing here, matching the deliverable in the issue:

1. An all-clean shoot produces exactly ONE card and zero questions.
2. A shoot where one stage flagged something stops the chain right there,
   names the exception, and never fabricates past it — later stages'
   files are never even read.
3. `check_*` are pure reads: running single-pass never edits a single
   byte of any stage's own output, so the same inputs still produce the
   same outputs whether the four (five) stages ran in one sitting or apart
   — the plan's non-negotiable ("a diet that changes behavior is a bug").

No network: the review-card render step takes injected fakes, exactly the
pattern `tests/test_review_card_html.py` already uses for its own card.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import single_pass as sp  # noqa: E402

DRAFT_MD = """---
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
meta:
  ebay_inventory_sku: "SKU-1"
---
# Description

Body paragraph.
"""


def _clean_prep_manifest(names=("a.jpg",)) -> dict:
    """A PREP manifest with every frame resolved, nothing guessed, no crop
    refused, and the run approved — the confidence-gate "normal case"
    (prep.md: "run --approve-auto yourself ... move straight to colour")."""
    photos = {}
    for n in names:
        photos[n] = {
            "orientation": {"applied": 0, "needs_ask": False, "guessed": False,
                             "subject_angle": 0, "osd_proposal": 0},
            "crop": {"applied": True, "box": [0, 0, 10, 10]},
        }
    return {"version": 1, "photos": photos, "approved": True,
            "auto": {"guessed": []}}


def _write_clean_shoot(shoot: Path) -> None:
    shoot.mkdir(parents=True, exist_ok=True)
    (shoot / "identify.txt").write_text("SHOOT SUMMARY\nDistinct items: 1\n",
                                         encoding="utf-8")
    prep_dir = shoot / ".prep"
    prep_dir.mkdir(parents=True, exist_ok=True)
    (prep_dir / "prep.json").write_text(json.dumps(_clean_prep_manifest()),
                                         encoding="utf-8")
    (shoot / "price.txt").write_text("Max supported price: $40\n", encoding="utf-8")
    (shoot / "investigate.txt").write_text("Item 1: condition fine.\n",
                                            encoding="utf-8")
    (shoot / "draft.md").write_text(DRAFT_MD, encoding="utf-8")


def _fake_review_builder(calls):
    def build(draft_path: Path):
        calls.append(("review", draft_path))
        (draft_path.parent / "review_card.md").write_text(
            "Preflight\n  • ok\nNeeds review / manual intervention\n  • none\n",
            encoding="utf-8")
        return "card text", str(draft_path.parent / "review_card.md")
    return build


def _fake_html_builder(calls):
    def build(shoot: Path):
        calls.append(("html", shoot))
        p = shoot / "review_card.html"
        p.write_text("<title>fake card</title>", encoding="utf-8")
        return p
    return build


# ---------------------------------------------------------------------------
# 1. all-clean run -> one card, zero questions
# ---------------------------------------------------------------------------

def test_all_clean_run_yields_one_card_and_zero_questions(tmp_path):
    shoot = tmp_path / "item-1"
    _write_clean_shoot(shoot)
    calls = []

    result = sp.run_single_pass(
        shoot,
        review_builder=_fake_review_builder(calls),
        html_builder=_fake_html_builder(calls),
    )

    assert result.clean is True
    assert result.asks == ()
    assert result.blocked_at is None
    assert result.stages_cleared == sp.STAGE_ORDER
    assert result.review_card_html == shoot / "review_card.html"
    assert (shoot / "review_card.html").exists()
    # exactly one card built, exactly one review pass, exactly one html render
    assert [c[0] for c in calls] == ["review", "html"]
    assert "OK 5/5" in result.summary()


def test_clean_run_reuses_an_existing_review_card_without_rebuilding_it(tmp_path):
    """If REVIEW already ran (review_card.md on disk from a prior --review),
    single-pass must not re-run it — same card, not a second decision."""
    shoot = tmp_path / "item-1"
    _write_clean_shoot(shoot)
    (shoot / "review_card.md").write_text("Preflight\n  • already built\n",
                                           encoding="utf-8")
    calls = []

    result = sp.run_single_pass(
        shoot,
        review_builder=_fake_review_builder(calls),
        html_builder=_fake_html_builder(calls),
    )

    assert result.clean is True
    assert [c[0] for c in calls] == ["html"]          # review builder NOT called
    assert "already built" in (shoot / "review_card.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. a flagged stage stops the chain, names it, never fabricates onward
# ---------------------------------------------------------------------------

def test_prep_guessed_frame_stops_the_chain_and_names_it(tmp_path):
    shoot = tmp_path / "item-2"
    _write_clean_shoot(shoot)
    m = _clean_prep_manifest()
    m["photos"]["a.jpg"]["orientation"]["guessed"] = True
    m["photos"]["a.jpg"]["orientation"]["subject_angle"] = 90
    m["photos"]["a.jpg"]["orientation"]["osd_proposal"] = 90
    m["approved"] = False
    (shoot / ".prep" / "prep.json").write_text(json.dumps(m), encoding="utf-8")
    calls = []

    result = sp.run_single_pass(
        shoot,
        review_builder=_fake_review_builder(calls),
        html_builder=_fake_html_builder(calls),
    )

    assert result.clean is False
    assert result.blocked_at == "prep"
    assert result.stages_cleared == ("identify",)      # identify passed, prep didn't
    assert len(result.asks) == 1
    assert result.asks[0].stage == "prep"
    assert "a.jpg" in result.asks[0].detail
    assert "guessed" in result.asks[0].detail
    # never reaches price/investigate/draft, never builds a card
    assert calls == []
    assert not (shoot / "review_card.html").exists()
    assert "blocked at prep" in result.summary()
    assert "ASK[prep] a.jpg" in result.summary()


def test_identify_maker_mark_gate_asks_by_name_and_does_not_fabricate(tmp_path):
    """The maker-mark stop-and-ask gate (identify.md), reported via the
    single-pass sentinel instead of pausing the chat."""
    shoot = tmp_path / "item-3"
    _write_clean_shoot(shoot)
    ask_dir = shoot / ".single_pass"
    ask_dir.mkdir()
    question = ("Can you read the mark on the base of the silver pot? Any "
                "lion/letters, 'STERLING'/'EPNS', or a number?")
    (ask_dir / "ask.json").write_text(
        json.dumps({"stage": "identify", "detail": question}), encoding="utf-8")
    calls = []

    result = sp.run_single_pass(
        shoot,
        review_builder=_fake_review_builder(calls),
        html_builder=_fake_html_builder(calls),
    )

    assert result.clean is False
    assert result.blocked_at == "identify"
    assert result.stages_cleared == ()
    assert [a.detail for a in result.asks] == [question]
    assert calls == []
    # PREP's manifest says everything is fine, but identify blocked first —
    # single-pass must not skip ahead and use PREP's clean state as a stand-in
    # answer for the identify question it can't resolve on its own.
    assert not (shoot / "review_card.html").exists()


def test_a_missing_stage_output_blocks_without_guessing(tmp_path):
    """DRAFT hard-requires investigate.txt; a shoot that skipped straight to
    DRAFT must be told that, not silently priced/drafted around it."""
    shoot = tmp_path / "item-4"
    shoot.mkdir()
    (shoot / "identify.txt").write_text("SHOOT SUMMARY\n", encoding="utf-8")
    prep_dir = shoot / ".prep"
    prep_dir.mkdir()
    (prep_dir / "prep.json").write_text(json.dumps(_clean_prep_manifest()),
                                         encoding="utf-8")
    (shoot / "price.txt").write_text("Max supported price: $10\n", encoding="utf-8")
    # no investigate.txt, no draft.md

    result = sp.run_single_pass(shoot)

    assert result.clean is False
    assert result.blocked_at == "investigate"
    assert "investigate.txt" in result.asks[0].detail


# ---------------------------------------------------------------------------
# 3. single-pass never edits a stage's own files (same inputs -> same outputs)
# ---------------------------------------------------------------------------

def _hashes(shoot: Path) -> dict:
    out = {}
    for rel in ("identify.txt", "price.txt", "investigate.txt", "draft.md",
                ".prep/prep.json"):
        p = shoot / rel
        if p.exists():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_single_pass_never_mutates_any_stage_output(tmp_path):
    shoot = tmp_path / "item-5"
    _write_clean_shoot(shoot)
    before = _hashes(shoot)
    calls = []

    sp.run_single_pass(shoot, review_builder=_fake_review_builder(calls),
                        html_builder=_fake_html_builder(calls))

    after = _hashes(shoot)
    assert before == after


def test_single_pass_never_mutates_stage_outputs_on_a_blocked_run_either(tmp_path):
    shoot = tmp_path / "item-6"
    _write_clean_shoot(shoot)
    m = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
    m["photos"]["a.jpg"]["orientation"]["needs_ask"] = True
    (shoot / ".prep" / "prep.json").write_text(json.dumps(m), encoding="utf-8")
    before = _hashes(shoot)

    result = sp.run_single_pass(shoot)

    assert result.clean is False
    assert _hashes(shoot) == before


# ---------------------------------------------------------------------------
# per-stage check functions, in isolation
# ---------------------------------------------------------------------------

def test_check_price_never_blocks_even_when_thin(tmp_path):
    """price.md: 'Autonomy: no step gates or stops.' Thin/absent comps are
    logged, never asked."""
    shoot = tmp_path / "p"
    shoot.mkdir()
    (shoot / "price.txt").write_text(
        "Max supported price: $8\nData quality: thin\n", encoding="utf-8")
    assert sp.check_price(shoot) == []


def test_check_prep_reports_unoverridden_crop_refusal(tmp_path):
    shoot = tmp_path / "c"
    (shoot / ".prep").mkdir(parents=True)
    m = _clean_prep_manifest()
    m["photos"]["a.jpg"]["crop"] = {"applied": False, "reason": "no studio backdrop"}
    (shoot / ".prep" / "prep.json").write_text(json.dumps(m), encoding="utf-8")
    asks = sp.check_prep(shoot)
    assert len(asks) == 1
    assert "no studio backdrop" in asks[0].detail


def test_check_prep_does_not_reask_an_operator_override(tmp_path):
    """A deliberate operator 'keep as shot' (crop.operator set) is a decision
    already made, not an open question."""
    shoot = tmp_path / "c2"
    (shoot / ".prep").mkdir(parents=True)
    m = _clean_prep_manifest()
    m["photos"]["a.jpg"]["crop"] = {"applied": False, "reason": "operator: keep as shot",
                                     "operator": True}
    (shoot / ".prep" / "prep.json").write_text(json.dumps(m), encoding="utf-8")
    assert sp.check_prep(shoot) == []


def test_check_prep_flags_clean_but_unapproved_manifest(tmp_path):
    """Auto pass resolved everything but --approve-auto hasn't stamped it —
    single-pass must not treat that as done."""
    shoot = tmp_path / "c3"
    (shoot / ".prep").mkdir(parents=True)
    m = _clean_prep_manifest()
    m["approved"] = False
    (shoot / ".prep" / "prep.json").write_text(json.dumps(m), encoding="utf-8")
    asks = sp.check_prep(shoot)
    assert len(asks) == 1
    assert "not yet approved" in asks[0].detail


def test_malformed_ask_file_is_treated_as_no_ask(tmp_path):
    """A gate check must never itself crash a routine run."""
    shoot = tmp_path / "m"
    ask_dir = shoot / ".single_pass"
    ask_dir.mkdir(parents=True)
    (ask_dir / "ask.json").write_text("{not json", encoding="utf-8")
    assert sp._pending_ask(shoot, "identify") == []


def test_stage_order_matches_run_md_dependency_chain():
    """DRAFT depends on INVESTIGATE, PREP is a gate IDENTIFY feeds — the
    order must be the real one, not the issue's shorthand."""
    assert sp.STAGE_ORDER == ("identify", "prep", "price", "investigate", "draft")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def test_cli_registers_single_pass():
    from cli import COMMANDS
    assert COMMANDS["single-pass"][0] == "lib.single_pass"


# --- lot attribution (lib/lot_check.py) ---------------------------------------
import lot_check  # noqa: E402


def _inv(tmp_path):
    inv = tmp_path / "inventory"
    (inv / "ES" / "K01" / "box1" / "item1").mkdir(parents=True)
    (inv / "ES" / "K01" / "context.txt").write_text("client: K01\n")
    (inv / "ES" / "M02" / "item9").mkdir(parents=True)
    (inv / "ES" / "M02" / "context.txt").write_text("kind: event\n")
    (inv / "other" / "x").mkdir(parents=True)
    (inv / "ES" / "M02" / "item9" / "context.txt").write_text("part_of: ES/K01\n")
    return inv


def test_lot_resolves_from_folder(tmp_path):
    inv = _inv(tmp_path)
    assert lot_check.resolve_lot(inv / "ES/K01/box1/item1", inv) == "ES/K01"
    assert lot_check.resolve_lot(inv / "ES/M02/item9", inv) == "ES/K01"
    assert lot_check.resolve_lot(inv / "other/x", inv) is None


def test_identify_blocked_without_or_with_wrong_lot(tmp_path):
    inv = _inv(tmp_path)
    item = inv / "ES/K01/box1/item1"
    (item / "identify.txt").write_text("SHOOT SUMMARY\nPhotos processed: 3\n")
    assert "no `Lot:` line" in lot_check.problems(item, inv)[0]
    (item / "identify.txt").write_text("SHOOT SUMMARY\nLot: ES/M02\n")
    assert "belongs to ES/K01" in lot_check.problems(item, inv)[0]
    lot_check.stamp(item, inv)
    assert lot_check.problems(item, inv) == []


def test_lot_read_and_stamped_only_in_summary_header(tmp_path):
    inv = _inv(tmp_path)
    item = inv / "ES/K01/box1/item1"
    body = "--- Item 1 ---\nLot: 7 of a 12-piece estate set\n"
    (item / "identify.txt").write_text("SHOOT SUMMARY\nPhotos processed: 3\n" + body)
    assert "no `Lot:` line" in lot_check.problems(item, inv)[0]
    lot_check.stamp(item, inv)
    text = (item / "identify.txt").read_text()
    assert text.endswith(body)
    assert text.startswith("SHOOT SUMMARY\nLot:              ES/K01\n")
    assert lot_check.problems(item, inv) == []


def test_unclaimed_item_blocked_and_scratch_passes(tmp_path):
    inv = _inv(tmp_path)
    assert "no context.txt" in lot_check.problems(inv / "other/x", inv)[0]
    assert lot_check.problems(tmp_path, inv) == []


# --- jewelry measurements (lib/measure_check.py) ------------------------------
import measure_check  # noqa: E402

_J = "--- Item 1 ---\n- Category: necklace\n- Measured size: {s}\n- Measured weight: {w}\n"


def _shoot(tmp_path, s, w):
    (tmp_path / "identify.txt").write_text(_J.format(s=s, w=w))
    return tmp_path


def test_jewelry_needs_real_measurements(tmp_path):
    assert measure_check.problems(_shoot(tmp_path, "18 in", "12.4 g")) == []
    for bad_s in ("ASK", "~18 in", "16-18 in", "unknown", ""):
        assert measure_check.problems(_shoot(tmp_path, bad_s, "12 g"))
    assert measure_check.problems(_shoot(tmp_path, "18 in", "estimated 12 g"))


def test_ring_size_counts_as_measured_size(tmp_path):
    for ok in ("size 7", "US size 7 1/2", "Size 6.5"):
        assert measure_check.problems(_shoot(tmp_path, ok, "3.1 g")) == [], ok
    # A bare number has no unit or "size" word, so it still needs asking.
    for bad in ("7", "size ~7", "size 6-7"):
        assert measure_check.problems(_shoot(tmp_path, bad, "3.1 g")), bad


def test_non_jewelry_not_gated_and_wired_into_identify(tmp_path):
    (tmp_path / "identify.txt").write_text("--- Item 1 ---\n- Category: chess set\n")
    assert measure_check.problems(tmp_path) == []
    _shoot(tmp_path, "ASK", "ASK")
    assert any("jewelry" in a.detail for a in sp.check_identify(tmp_path))


def test_jewelry_detector_ignores_household_rings_and_chains(tmp_path):
    for cat in ("pliers", "chain-nose pliers", "coffee mug", "glass", "ring dish glass",
                "napkin ring", "napkin rings (set of 6)", "curtain rings", "key ring",
                "rolling pin", "chain saw", "pendant light", "Tools > Pliers > ring pliers"):
        assert not measure_check._is_jewelry(cat), cat
    for cat in ("ring", "sterling ring", "Jewelry & Watches > Rings", "chain", "brooch pin",
                "glass bead necklace", "charm bracelet", "earrings"):
        assert measure_check._is_jewelry(cat), cat


def test_jewelry_word_outside_category_not_gated(tmp_path):
    (tmp_path / "identify.txt").write_text(
        "--- Item 1 ---\n- Category: drinking glass\n- Type: ring-pattern tumbler\n"
        "- Notes: gold ring around the rim\n")
    assert measure_check.problems(tmp_path) == []
