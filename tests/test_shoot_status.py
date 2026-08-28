"""tools/shoot_status.py — the one-call read a conductor uses instead of the
ls/cat/grep chain (RUN.md, "Concurrency and delegation" / #62).

Covers the progression through phase files + the PREP gate, the sku/ledger
lookup, and that a malformed draft.md degrades to "no sku" rather than raising.
"""
import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import tools.shoot_status as S  # noqa: E402

DRAFT = """---
title: "Test Widget"
meta:
  ebay_inventory_sku: deadbeef
---
Body.
"""


def _shoot(tmp: Path, **files) -> Path:
    shoot = tmp / "widget"
    shoot.mkdir()
    (shoot / "one.jpg").write_bytes(b"\xff\xd8\xff")  # not real jpeg bytes; find_images only checks suffix
    for name, content in files.items():
        (shoot / name).write_text(content, encoding="utf-8")
    return shoot


def test_no_files_yet_points_at_identify():
    with tempfile.TemporaryDirectory() as d:
        shoot = _shoot(Path(d))
        s = S.status(shoot)
        assert s["next_action"] == "run IDENTIFY"
        assert not any(s["files"].values())
        assert s["sku"] is None
        assert s["ledger"] is None


def test_identify_done_points_at_prep():
    with tempfile.TemporaryDirectory() as d:
        shoot = _shoot(Path(d), **{"identify.txt": "call: widget"})
        s = S.status(shoot)
        assert s["files"]["identify"] is True
        assert s["next_action"] == "open PREP stage orientation"


def test_full_chain_reads_sku_and_ledger_row(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        shoot = _shoot(tmp, **{
            "identify.txt": "call: widget",
            "price.txt": "working price: $19.99",
            "investigate.txt": "confident assessment",
            "draft.md": DRAFT,
        })
        ledger = tmp / "ledger.csv"
        with ledger.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["sku", "status", "title"])
            w.writeheader()
            w.writerow({"sku": "deadbeef", "status": "DRAFTED", "title": "Test Widget"})
        monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))

        s = S.status(shoot)
        assert s["sku"] == "deadbeef"
        assert s["ledger"]["status"] == "DRAFTED"
        # PREP was never approved, so DRAFT existing doesn't advance next_action
        # past the still-open gate.
        assert s["next_action"] == "open PREP stage orientation"


def test_malformed_draft_degrades_to_no_sku():
    with tempfile.TemporaryDirectory() as d:
        shoot = _shoot(Path(d), **{"draft.md": "not frontmatter at all"})
        s = S.status(shoot)
        assert s["sku"] is None
        assert s["ledger"] is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
