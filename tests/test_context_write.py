#!/usr/bin/env python3
"""Regression tests for lib/context_write.py — the context.txt writer (#118).

Covers: correct key insertion/update, prose preservation, idempotency,
dry-run, and the hard PII rule (a bucket's free-text prose must never be
echoed back — to stdout, to a log, to anything — only the extracted keys).

Run:  pytest tests/test_context_write.py
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import context_write as cw  # noqa: E402


# --------------------------------------------------------------------------
# upsert_context_text — the pure text transform
# --------------------------------------------------------------------------

def test_new_keys_are_inserted_ahead_of_existing_prose():
    text = "An estate sale. Loaded the truck twice.\n"
    got = cw.upsert_context_text(text, {"kind": "event", "spend": "575"})
    assert got == ("kind: event\nspend: 575\n\n"
                   "An estate sale. Loaded the truck twice.\n")


def test_an_existing_key_line_is_rewritten_in_place_not_duplicated():
    text = "kind: event\nspend: 500\n\nSome prose.\n"
    got = cw.upsert_context_text(text, {"spend": "575"})
    assert got == "kind: event\nspend: 575\n\nSome prose.\n"
    assert got.count("spend:") == 1


def test_prose_is_never_altered_only_key_lines_change():
    text = "kind: channel\n\nA thrift habit, ongoing, no single receipt.\n"
    got = cw.upsert_context_text(text, {"acquired": "2026-01"})
    assert "A thrift habit, ongoing, no single receipt." in got
    assert "acquired: 2026-01" in got


def test_empty_file_gets_just_the_key_lines():
    got = cw.upsert_context_text("", {"kind": "event"})
    assert got == "kind: event\n"


def test_upsert_is_idempotent_second_call_is_a_no_op():
    text = "An estate sale.\n"
    once = cw.upsert_context_text(text, {"kind": "event", "spend": "575"})
    twice = cw.upsert_context_text(once, {"kind": "event", "spend": "575"})
    assert once == twice


def test_unrelated_existing_keys_pass_through_untouched():
    text = "kind: event\nacquired: 2026-01\n"
    got = cw.upsert_context_text(text, {"spend": "575"})
    assert "kind: event" in got
    assert "acquired: 2026-01" in got
    assert "spend: 575" in got


# --------------------------------------------------------------------------
# _fmt_num — canonical numeric text (no $, no commas, no trailing .0)
# --------------------------------------------------------------------------

def test_fmt_num_drops_trailing_zero_on_a_whole_number():
    assert cw._fmt_num(575.0) == "575"


def test_fmt_num_keeps_a_real_decimal():
    assert cw._fmt_num(12.5) == "12.5"


# --------------------------------------------------------------------------
# write_context — the file-writing entry point
# --------------------------------------------------------------------------

def test_write_context_creates_a_new_file_with_the_given_keys(tmp_path):
    f, changed, keys = cw.write_context(tmp_path, kind="event", spend=575.0,
                                        acquired="2026-07")
    assert changed is True
    assert f.is_file()
    text = f.read_text()
    assert "kind: event" in text
    assert "spend: 575" in text
    assert "acquired: 2026-07" in text
    assert keys == {"kind": "event", "spend": "575", "acquired": "2026-07"}


def test_write_context_preserves_prose_already_on_disk(tmp_path):
    (tmp_path / "context.txt").write_text(
        "An estate sale in Social Circle Georgia hosted by my cousin Jane.\n")
    cw.write_context(tmp_path, spend=575.0, kind="event")
    text = (tmp_path / "context.txt").read_text()
    assert "cousin Jane" in text                      # prose survives
    assert "spend: 575" in text
    assert "kind: event" in text


def test_write_context_is_idempotent_second_call_reports_no_change(tmp_path):
    cw.write_context(tmp_path, spend=575.0, kind="event", spend_unit="lot")
    f, changed, _ = cw.write_context(tmp_path, spend=575.0, kind="event",
                                     spend_unit="lot")
    assert changed is False


def test_write_context_dry_run_never_touches_the_file(tmp_path):
    (tmp_path / "context.txt").write_text("kind: event\nspend: 500\n")
    before = (tmp_path / "context.txt").read_text()
    f, changed, keys = cw.write_context(tmp_path, spend=600.0, dry_run=True)
    after = (tmp_path / "context.txt").read_text()
    assert changed is True                             # WOULD have changed
    assert after == before                              # but did not write
    assert keys == {"spend": "600"}


def test_write_context_only_touches_the_keys_it_is_given(tmp_path):
    (tmp_path / "context.txt").write_text("kind: event\nspend: 500\nacquired: 2026-01\n")
    cw.write_context(tmp_path, spend=575.0)
    text = (tmp_path / "context.txt").read_text()
    assert "kind: event" in text
    assert "acquired: 2026-01" in text
    assert "spend: 575" in text


def test_write_context_writes_spend_unit(tmp_path):
    cw.write_context(tmp_path, spend=8.0, spend_unit="item")
    text = (tmp_path / "context.txt").read_text()
    assert "spend_unit: item" in text


def test_write_context_raises_when_nothing_is_given(tmp_path):
    with pytest.raises(ValueError):
        cw.write_context(tmp_path)


# --------------------------------------------------------------------------
# PII: prose must never be echoed — only extracted keys, anywhere
# --------------------------------------------------------------------------

_PII_MARKER = "cousin Jane Smith of 4501 Maple"


def test_cli_main_never_prints_the_prose_it_read(tmp_path, capsys):
    (tmp_path / "context.txt").write_text(
        f"An estate sale hosted by my {_PII_MARKER} Street.\n")
    rc = cw.main([str(tmp_path), "--spend", "575", "--kind", "event"])
    assert rc == 0
    out = capsys.readouterr().out
    assert _PII_MARKER not in out                      # prose never echoed
    assert "kind: event" in out                         # only the keys
    assert "spend: 575" in out


def test_cli_dry_run_also_never_prints_the_prose(tmp_path, capsys):
    (tmp_path / "context.txt").write_text(f"Hosted by my {_PII_MARKER} Street.\n")
    cw.main([str(tmp_path), "--spend", "600", "--dry-run"])
    out = capsys.readouterr().out
    assert _PII_MARKER not in out


def test_write_context_keys_written_never_contains_prose_text(tmp_path):
    (tmp_path / "context.txt").write_text(f"Hosted by my {_PII_MARKER} Street.\n")
    _, _, keys = cw.write_context(tmp_path, spend=575.0)
    assert all(_PII_MARKER not in str(v) for v in keys.values())


# --------------------------------------------------------------------------
# CLI argument handling
# --------------------------------------------------------------------------

def test_cli_errors_on_a_nonexistent_directory(tmp_path, capsys):
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit):
        cw.main([str(missing), "--spend", "575"])


def test_cli_errors_when_nothing_is_given(tmp_path):
    with pytest.raises(SystemExit):
        cw.main([str(tmp_path)])


def test_cli_errors_on_an_unparseable_spend(tmp_path):
    with pytest.raises(SystemExit):
        cw.main([str(tmp_path), "--spend", "not-a-number"])


def test_cli_accepts_dollar_and_comma_formatted_spend(tmp_path, capsys):
    rc = cw.main([str(tmp_path), "--spend", "$1,250.50"])
    assert rc == 0
    text = (tmp_path / "context.txt").read_text()
    assert "spend: 1250.5" in text
