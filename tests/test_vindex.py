#!/usr/bin/env python3
"""Unit tests for lib/vindex.py — the shared CLIP visual-index STORE.

Covers the on-disk store (VIndex): state round-trip, model stamping/guard,
append + load row-alignment, and the new save_meta() in-place rewrite. None of
these touch CLIP or the network — they exercise the file/format layer only, so
the suite is fast and deterministic.

Run:  python tests/test_vindex.py     (standalone, prints a table)
  or: pytest tests/test_vindex.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import numpy as np                     # noqa: E402
import vindex                          # noqa: E402
from vindex import VIndex             # noqa: E402


def _tmp_index():
    """A VIndex redirected to a fresh temp dir (never touches kb/index/)."""
    d = Path(tempfile.mkdtemp(prefix="vindex_test_"))
    ix = VIndex("unit-test")
    ix.dir, ix.emb_p = d, d / "emb.npy"
    ix.meta_p, ix.state_p = d / "meta.jsonl", d / "state.json"
    return ix


def test_load_state_returns_default_copy_when_missing():
    ix = _tmp_index()
    default = {"count": 0, "seen": [1, 2]}
    st = ix.load_state(default)
    assert st == default
    st["count"] = 99                    # mutating the result must NOT mutate the default
    assert default["count"] == 0, "load_state must return a copy, not the default dict"


def test_save_then_load_state_roundtrips():
    ix = _tmp_index()
    st = {"count": 3, "indexed_topic_ids": [10, 20], "cursor": 5}
    ix.save_state(st)
    assert ix.state_p.exists()
    assert ix.load_state({}) == st


def test_stamp_records_backend_model_dim():
    ix = _tmp_index()
    st = {}
    ix.stamp(st, dim=512)
    assert st["backend"] == "clip"
    assert st["model"] == vindex.MODEL_NAME
    assert st["dim"] == 512 and isinstance(st["dim"], int)
    assert "last_sync_utc" in st


def test_check_model_passes_on_match_raises_on_mismatch():
    ix = _tmp_index()
    ix.check_model({"model": vindex.MODEL_NAME})       # must not raise
    for bad in ({"model": "clip-ViT-L-14"}, {"backend": "phash"}, {}):
        try:
            ix.check_model(bad)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"check_model should reject {bad}")


def test_append_and_load_roundtrip_row_aligned():
    ix = _tmp_index()
    rows = [{"i": 0, "tid": 1}, {"i": 1, "tid": 2}]
    emb = np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
    ix.append(rows, emb)
    e2, m2 = ix.load()
    assert e2.shape == (2, 2) and e2.dtype == np.float32
    assert m2 == rows
    assert len(m2) == e2.shape[0], "meta rows must equal embedding rows"


def test_append_twice_vstacks_and_stays_aligned():
    ix = _tmp_index()
    ix.append([{"i": 0}], np.array([[1.0, 0.0]], dtype="float32"))
    ix.append([{"i": 1}, {"i": 2}], np.array([[0.0, 1.0], [1.0, 1.0]], dtype="float32"))
    e, m = ix.load()
    assert e.shape == (3, 2)
    assert [r["i"] for r in m] == [0, 1, 2], "append must preserve order"
    assert len(m) == e.shape[0]


def test_load_raises_when_empty():
    ix = _tmp_index()
    try:
        ix.load()
    except SystemExit:
        pass
    else:
        raise AssertionError("load() must raise on an empty index")


def test_save_meta_rewrites_in_place_same_count_and_order():
    ix = _tmp_index()
    rows = [{"tid": 1, "answer": None}, {"tid": 2, "answer": None}, {"tid": 3, "answer": None}]
    ix.append(rows, np.eye(3, dtype="float32"))
    emb_before = np.load(ix.emb_p)
    # mutate a field in place, keep rows/order identical
    _, meta = ix.load()
    meta[1]["answer"] = "Vitro Conqueror"
    ix.save_meta(meta)
    e, m = ix.load()
    assert m[1]["answer"] == "Vitro Conqueror"
    assert [r["tid"] for r in m] == [1, 2, 3], "save_meta must preserve row order"
    assert len(m) == e.shape[0], "row count must still equal embeddings"
    assert np.array_equal(np.load(ix.emb_p), emb_before), "save_meta must not touch emb.npy"


def test_save_meta_leaves_no_tmp_file():
    ix = _tmp_index()
    rows = [{"tid": 1}]
    ix.append(rows, np.array([[1.0, 0.0]], dtype="float32"))
    ix.save_meta(rows)
    leftovers = list(ix.dir.glob("*.tmp"))
    assert not leftovers, f"atomic rewrite left temp files: {leftovers}"


def test_save_meta_unicode_roundtrip():
    ix = _tmp_index()
    rows = [{"tid": 1, "answer": "Christensen “slag” — José"}]
    ix.append(rows, np.array([[1.0, 0.0]], dtype="float32"))
    ix.save_meta(rows)
    _, m = ix.load()
    assert m[0]["answer"] == "Christensen “slag” — José"


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
