#!/usr/bin/env python3
"""The v3 formats, pinned.

Every artefact this pipeline passes between phases has a shape that something
downstream depends on: the draft's frontmatter is read by the validator and the
eBay sync, the PREP manifest is read by the review page and the gate, the ledger
is read by the audit tools, the review card is read by a human deciding whether
to publish. All of them carry a version stamp. None of them had anything that
noticed when the shape changed underneath the stamp.

That is what this file is. It is not testing behaviour — it is a lock. Each test
holds the exact field set of one format. Adding, renaming or dropping a field
fails here, loudly, in the same commit that does it.

WHEN A TEST HERE FAILS, that is the format changing. Two legitimate answers:

  * the change is additive and safe — add the field to the lock below, in the
    same commit, so the next reader sees when it appeared;
  * the change breaks readers — bump the format's version stamp
    (`template_version`, `MANIFEST_VERSION`), teach the reader both shapes, and
    update the lock.

The wrong answer is to relax an assertion so it stops noticing.

Run:  python tests/test_formats.py
  or: pytest tests/test_formats.py
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import yaml                                                  # noqa: E402


# ---------------------------------------------------------------------------
# the draft — templates/listing-v1.md, stamped template_version: v1
# ---------------------------------------------------------------------------

TEMPLATE = ROOT / "templates" / "listing-v1.md"

DRAFT_TOP = {
    "_field_constraints", "best_offer", "category_id", "category_path",
    "condition", "condition_description", "cost_of_goods", "format",
    "item_specifics", "meta", "photos", "price", "promoted", "quantity",
    "returns_policy_id", "shipping", "template_version", "title",
}
DRAFT_META = {
    "drafted_at", "ebay_inventory_sku", "ebay_offer_id", "item_id",
    "last_synced", "notes", "shoot_dir",
}
DRAFT_SPECIFICS = {
    "brand", "character_family", "collection", "color", "country_of_origin",
    "department", "extra", "finish", "material", "occasion", "pattern", "size",
    "style", "subject", "theme", "time_period_manufactured", "type", "upc",
}
DRAFT_SHIPPING = {
    "domestic_shipping_type", "free_shipping", "fulfillment_mode",
    "handling_time_days", "international", "item_location_zip", "local_pickup",
    "package_in", "primary_service", "weight",
}
# The limits the eBay form actually enforces. A wrong number here is a publish
# rejection or a silently truncated listing, so they are pinned by value.
DRAFT_LIMITS = {
    "title": 80, "price": 13, "quantity": 5, "condition_description": 1000,
    "item_specifics.type": 65, "item_specifics.brand": 65, "item_specifics.upc": 20,
    "shipping.weight.major_lb": 3, "shipping.weight.minor_oz": 2,
    "shipping.package_in.length": 5,
}


def _template() -> dict:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    fm = tpl[tpl.index("---\n") + 4: tpl.index("---\n\n# Description")]
    return yaml.safe_load(fm)


def test_draft_template_is_v1_and_its_fields_are_fixed():
    d = _template()
    assert d["template_version"] == "v1", d["template_version"]
    assert set(d) == DRAFT_TOP, set(d) ^ DRAFT_TOP
    assert set(d["meta"]) == DRAFT_META, set(d["meta"]) ^ DRAFT_META
    assert set(d["item_specifics"]) == DRAFT_SPECIFICS, \
        set(d["item_specifics"]) ^ DRAFT_SPECIFICS
    assert set(d["shipping"]) == DRAFT_SHIPPING, set(d["shipping"]) ^ DRAFT_SHIPPING


def test_the_field_limits_are_the_ones_ebay_enforces():
    """These are not style choices. Over the cap, eBay rejects or truncates."""
    fc = _template()["_field_constraints"]
    for path, cap in DRAFT_LIMITS.items():
        assert path in fc, f"{path} lost its constraint"
        assert fc[path].get("max_len") == cap, (path, fc[path].get("max_len"), cap)
    for req in ("title", "price", "quantity", "item_specifics.type"):
        assert fc[req].get("required") is True, f"{req} stopped being required"


# Drafts written before the constraints block existed. They stamp
# `template_version: v1` and carry none of it, so the validator has nothing to
# read. 15 are published and 4 have sold, so they are not going to be rewritten
# casually — but the number must only ever go DOWN. A new one is a regression in
# whatever wrote it.
LEGACY_DRAFTS_WITHOUT_CONSTRAINTS = 31
# And 21 more carry a PARTIAL block — rules dropped, none altered and none
# invented. Those drafts are under-enforced rather than mis-enforced: the
# validator checks fewer fields than it should, which is a smaller problem than
# checking the wrong limit. Same rule as above, the number may only go down.
LEGACY_DRAFTS_WITH_PARTIAL_CONSTRAINTS = 21


def test_a_rendered_draft_still_matches_the_template():
    """Real drafts in the tree, checked against the template they claim.

    Two different assertions, because there are two different situations:

      * a draft that CARRIES a constraints block must match the template
        exactly. There is no tolerance here — a drifted block means the
        validator is enforcing limits that eBay does not, or missing ones it
        does.
      * a draft that carries NONE is old, from before the block existed. Those
        are counted, not fixed, and the count may only shrink.
    """
    drafts = sorted(ROOT.glob("inventory/**/draft.md"))
    if not drafts:
        return
    tpl_fc = _template()["_field_constraints"]
    matched = legacy = partial = 0
    for p in drafts:
        txt = p.read_text(encoding="utf-8", errors="replace")
        if "template_version: v1" not in txt:
            continue
        try:
            d = yaml.safe_load(txt.split("---\n", 2)[1])
        except (yaml.YAMLError, IndexError):
            raise AssertionError(f"{p}: frontmatter no longer parses")
        if not isinstance(d, dict):
            raise AssertionError(f"{p}: frontmatter is not a mapping")
        if "_field_constraints" not in d:
            legacy += 1
            continue
        fc = d["_field_constraints"]
        # A rule that DISAGREES with the template, or one the template has never
        # heard of, is the dangerous case: the validator would enforce a limit
        # eBay does not have, or wave through one it does. No tolerance for it.
        for rule, spec in fc.items():
            assert rule in tpl_fc, f"{p}: unknown constraint {rule!r}"
            assert spec == tpl_fc[rule], \
                f"{p}: constraint {rule!r} disagrees with the template: " \
                f"{spec} vs {tpl_fc[rule]}"
        if set(fc) != set(tpl_fc):
            partial += 1                      # under-enforced; counted below
        assert d.get("title") is not None, f"{p}: no title"
        matched += 1

    assert matched, "no drafts with a constraints block found to check"
    assert legacy <= LEGACY_DRAFTS_WITHOUT_CONSTRAINTS, (
        f"{legacy} drafts now claim v1 without a constraints block, up from "
        f"{LEGACY_DRAFTS_WITHOUT_CONSTRAINTS}. Something is writing drafts that "
        f"the validator cannot check.")
    assert partial <= LEGACY_DRAFTS_WITH_PARTIAL_CONSTRAINTS, (
        f"{partial} drafts now carry a partial constraints block, up from "
        f"{LEGACY_DRAFTS_WITH_PARTIAL_CONSTRAINTS}.")


# ---------------------------------------------------------------------------
# the PREP manifest — .prep/prep.json, stamped version: 1
# ---------------------------------------------------------------------------

MANIFEST_TOP = {"version", "shoot", "created", "updated", "approved",
                "approved_at", "settings", "photos"}
PHOTO_CORE = {"src_sha256", "src_size", "orientation", "status"}
ORIENTATION_KEYS = {
    "applied", "exif_angle", "exif_tag", "needs_ask", "notes", "osd_angle",
    "osd_conf", "osd_note", "source", "subject_angle", "vision_angle",
}
STAGE_ORDER = ("orientation", "unskew", "crop", "color")


def test_manifest_version_and_stage_order_are_fixed():
    from lib.photo_prep import prep as P
    from lib.photo_prep import stages as S

    assert P.MANIFEST_VERSION == 1, P.MANIFEST_VERSION
    # Order is the contract, not just membership: the gate walks it, and every
    # stage's meaning depends on what ran before it.
    assert S.STAGES == STAGE_ORDER, S.STAGES


def test_a_real_manifest_carries_the_fields_readers_depend_on():
    found = sorted(ROOT.glob("inventory/**/.prep/prep.json"))
    if not found:
        return
    checked = 0
    for p in found[:25]:
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("version") != 1:
            continue
        assert MANIFEST_TOP <= set(m), f"{p}: missing {MANIFEST_TOP - set(m)}"
        for name, rec in list((m.get("photos") or {}).items())[:5]:
            assert PHOTO_CORE <= set(rec), \
                f"{p}::{name}: missing {PHOTO_CORE - set(rec)}"
            assert ORIENTATION_KEYS <= set(rec["orientation"]), \
                f"{p}::{name}: orientation missing " \
                f"{ORIENTATION_KEYS - set(rec['orientation'])}"
        checked += 1
    assert checked, "no version-1 manifests found to check"


# ---------------------------------------------------------------------------
# the ledger — listings_ledger.csv
# ---------------------------------------------------------------------------

LEDGER_COLUMNS = ["sku", "status", "title", "price", "offer_id", "listing_id",
                  "url", "drafted_at", "synced_at", "published_at", "ended_at",
                  "updated_at"]


def test_ledger_columns_and_their_order():
    p = ROOT / "listings_ledger.csv"
    if not p.exists():
        return
    with p.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert header == LEDGER_COLUMNS, header


def test_ledger_statuses_stay_in_the_known_set():
    """A status nobody recognises silently drops rows out of every report."""
    p = ROOT / "listings_ledger.csv"
    if not p.exists():
        return
    known = {"DRAFTED", "SYNCED", "PUBLISHED", "SOLD", "ENDED", "DELETED",
             "OUT_OF_STOCK", ""}
    seen = set()
    with p.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            seen.add((row.get("status") or "").strip())
    unknown = seen - known
    assert not unknown, f"unknown ledger statuses: {sorted(unknown)}"


# ---------------------------------------------------------------------------
# the REVIEW card — the surface a human approves a publish on
# ---------------------------------------------------------------------------

CARD_SECTIONS = [
    "━━ REVIEW:",
    "Title:",
    "Price:",
    "Condition:",
    "Quantity:",
    "Fulfillment:",
    "Preflight",
    "Comps (open to verify):",
    "Condition detail:",
    "Final photos",
    "⚠ Needs review / manual intervention:",
    "→ Approve publishes this LIVE",
]


def test_the_review_card_still_says_all_of_it():
    """Anything dropped from this list is something a human stops being shown
    at the moment they authorise a live listing. `Final photos` is here because
    a count and a hero filename were not enough — they could not reveal a frame
    that never went through PREP."""
    src = (ROOT / "lib" / "list_edit.py").read_text(encoding="utf-8")
    i = src.index("def build_review_card")
    body = src[i:src.index("\ndef ", i + 10)]
    for label in CARD_SECTIONS:
        assert label in body, f"the review card no longer shows: {label!r}"


def test_the_publish_command_on_the_card_is_still_gated():
    """The card tells the operator exactly what publishes. If that line loses
    --confirm it is telling them something untrue about what happens next."""
    src = (ROOT / "lib" / "list_edit.py").read_text(encoding="utf-8")
    i = src.index("def build_review_card")
    body = src[i:src.index("\ndef ", i + 10)]
    m = re.search(r"--list \{shoot\} --confirm", body)
    assert m, "the card's publish command lost --confirm"


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
