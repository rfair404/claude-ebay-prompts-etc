#!/usr/bin/env python3
"""Regression corpus for the in-hand voice linter.

tests/test_voice_check.py covers the linter's mechanics. This covers its
MEMORY: every phrase in tests/fixtures/voice_corpus.yaml is real copy from the
2026-08 sweep — either something that reached a live buyer, or correct copy a
naive checker wrongly flags.

Patterns drift, and each hand-rolled regex in that sweep had a different hole.
Three live listings were found only on the third pass because the wording
("some pieces shown stacked", "shown exactly as it is", "and photographed")
fell outside whatever pattern was current. This file makes those concrete, so
the same wording cannot come back a third time.

When a new voice miss turns up in the wild, add the phrase to the corpus with
its listing id — that is the whole maintenance protocol.

Run:  python tests/test_voice_corpus.py
  or: pytest tests/test_voice_corpus.py
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import voice_check as V                                      # noqa: E402
from draft_io import Draft                                   # noqa: E402

CORPUS = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "voice_corpus.yaml").read_text(encoding="utf-8"))


def _findings(phrase: str, field: str = "condition_description") -> list[str]:
    """Run the linter over one phrase placed in a buyer-visible field.
    Every finding — block AND warn — so a WARN regression on a phrase that
    must "stay silent" is not invisible to a check that only looks at blocks.
    """
    fm = {"title": "A vintage thing", "condition_description": "",
          "meta": {"notes": "internal: odor not verified; undersides not photographed"}}
    body = ""
    if field == "condition_description":
        fm["condition_description"] = phrase
    else:
        body = phrase
    d = Draft(path=Path("draft.md"), frontmatter=fm, body=body)
    return V.check_voice(d)


def _blocks(phrase: str, field: str = "condition_description") -> list[str]:
    return [f for f in _findings(phrase, field) if f.startswith("voice (block)")]


def test_every_shipped_phrase_is_blocked():
    """Each of these reached a real listing and had to be corrected."""
    missed = []
    for entry in CORPUS["block"]:
        if not _blocks(entry["phrase"]):
            missed.append(f"{entry['phrase']!r}  [{entry['src']}]")
    assert not missed, "corpus phrases NOT caught:\n  " + "\n  ".join(missed)


def test_shipped_phrases_are_caught_in_the_body_too():
    """The body is the biggest buyer-visible surface and the easiest to skip.

    A line-scanning audit script silently skipped the body in 277 of 287
    drafts — the flag it used to skip `meta:` never reset at the frontmatter
    boundary. Field-scoped checking is what prevents that, so assert it.
    """
    missed = [e["phrase"] for e in CORPUS["block"]
              if not _blocks(e["phrase"], field="body")]
    assert not missed, f"not caught in body: {missed}"


def test_correct_copy_is_never_flagged():
    """A linter that cries wolf gets muted. These must stay silent — not just
    unblocked; a WARN is a finding too, and 'must stay silent' means neither."""
    wrong = []
    for entry in CORPUS["clean"]:
        hits = _findings(entry["phrase"])
        if hits:
            wrong.append(f"{entry['phrase']!r} ({entry['why']}) -> {hits}")
    assert not wrong, "correct copy wrongly flagged:\n  " + "\n  ".join(wrong)


def test_known_gaps_are_still_gaps():
    """Characterization test, not an endorsement.

    These are real misses left open on purpose, each with a reason in the
    corpus. If one starts producing ANY finding — block or warn — this fails
    LOUDLY so the change is a decision rather than a side effect — move the
    entry up to `block:`.
    """
    for entry in CORPUS.get("known_gaps") or []:
        assert not _findings(entry["phrase"]), (
            f"known gap is now CAUGHT: {entry['phrase']!r}\n"
            f"  If that was intended, move it from `known_gaps:` to `block:` "
            f"in tests/fixtures/voice_corpus.yaml.")


def test_corpus_entries_cite_a_source():
    """An uncited phrase is folklore; the listing id is what makes it evidence."""
    for section in ("block", "clean"):
        for entry in CORPUS[section]:
            assert entry.get("src"), f"{section} entry missing src: {entry['phrase']!r}"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{'FAILED' if fails else 'OK'} — {fails} failure(s)")
    raise SystemExit(1 if fails else 0)
