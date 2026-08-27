"""The terse-output convention: format stability for lib/verdict.py.

Other tooling (and the operator's eye) greps these lines; the shape is a
contract. No pytest fixtures — runs under tests/run_all.py too.
"""
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.verdict import emit  # noqa: E402

VERDICT_RE = re.compile(
    r"^[\w ./-]+: OK \d+/\d+(, \d+ flagged)?( -> \S.*)?$")


def _run(*args, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        line = emit(*args, **kw)
    return line, buf.getvalue().splitlines()


def test_clean_run_is_one_line():
    line, out = _run("check", 14)
    assert line == "check: OK 14/14"
    assert out == [line]
    assert VERDICT_RE.match(line)


def test_flagged_rows_print_before_the_verdict():
    line, out = _run("check", 14,
                     [("DSC_0101.jpg", "orientation ASK"),
                      ("DSC_0104.jpg", "no crop: detectors disagree")],
                     detail=".prep/prep.json")
    assert out[0] == "  FLAG DSC_0101.jpg  orientation ASK"
    assert out[1] == "  FLAG DSC_0104.jpg  no crop: detectors disagree"
    assert out[2] == line == "check: OK 12/14, 2 flagged -> .prep/prep.json"
    assert VERDICT_RE.match(line)


def test_next_hint_prints_after_the_verdict():
    _, out = _run("check", 3, [("a.jpg", "ASK")], next_hint="--stage orientation")
    assert out[-1] == "  next: --stage orientation"


def test_detail_accepts_a_path():
    line, _ = _run("geometry", 5, detail=Path(".prep") / "prep.json")
    assert line.endswith("prep.json")
    assert VERDICT_RE.match(line)
