#!/usr/bin/env python3
"""Unit tests for lib/marble_index.py — the forum text-search / verify layer.

Covers the pure helpers (_text_matcher, _row_blob, FIELD_SETS) and drives the
real command paths `cmd_search` (pure text over meta.jsonl — no model) and
`cmd_verify` (visual corroboration, with embed_images MOCKED so no CLIP/network
is loaded). Everything runs against a temp index redirected off kb/index/.

Run:  python tests/test_marble_index.py
  or: pytest tests/test_marble_index.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import numpy as np                     # noqa: E402
import vindex                          # noqa: E402
import marble_index as MI             # noqa: E402


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------
def test_text_matcher_and_is_default():
    hit = MI._text_matcher(["vitro", "conqueror"])
    assert hit("blue vitro conqueror marble")
    assert not hit("vitro agate")            # missing 'conqueror' -> AND fails


def test_text_matcher_any_is_or():
    hit = MI._text_matcher(["vitro", "akro"], any_=True)
    assert hit("akro agate corkscrew")
    assert hit("vitro all-red")
    assert not hit("marble king rainbow")


def test_text_matcher_case_insensitive_by_default():
    hit = MI._text_matcher(["Vitro"])
    assert hit("a VITRO conqueror")
    hit_cs = MI._text_matcher(["Vitro"], case=True)
    assert not hit_cs("a vitro conqueror")   # case-sensitive miss
    assert hit_cs("a Vitro conqueror")


def test_text_matcher_regex():
    hit = MI._text_matcher([r"conqueror|tornado"], regex=True)
    assert hit("this is a tornado")
    assert hit("vitro conqueror")
    assert not hit("plain aggie")


def test_row_blob_joins_fields_and_tolerates_none():
    m = {"title": "what is this", "op_question": None, "answer": "Vitro"}
    blob = MI._row_blob(m, ("title", "op_question", "answer", "missing"))
    assert "what is this" in blob and "Vitro" in blob
    assert "None" not in blob, "None fields must render as empty, not the string 'None'"


def test_field_sets_shape():
    assert MI.FIELD_SETS["answer"] == ("answer",)
    assert "title" in MI.FIELD_SETS["all"] and "answer" in MI.FIELD_SETS["all"]
    assert "answer" not in MI.FIELD_SETS["question"]


# --------------------------------------------------------------------------
# fixtures: a temp index + fake meta rows
# --------------------------------------------------------------------------
def _tmp_index_with(meta, emb=None):
    """Redirect MI.IDX to a temp index dir holding `meta` (and optional emb)."""
    d = Path(tempfile.mkdtemp(prefix="mi_test_"))
    ix = vindex.VIndex("unit-test")
    ix.dir, ix.emb_p = d, d / "emb.npy"
    ix.meta_p, ix.state_p = d / "meta.jsonl", d / "state.json"
    ix.dir.mkdir(parents=True, exist_ok=True)
    with ix.meta_p.open("w", encoding="utf-8") as f:
        for r in meta:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if emb is not None:
        np.save(ix.emb_p, np.asarray(emb, dtype="float32"))
    MI.IDX = ix                       # cmd_* read the module global
    return ix


def _row(tid, *, title="", q="", ans=None, by=None, grp=None, rep=0.0, turl=None, nrep=0):
    return {"tid": tid, "title": title, "op_question": q, "answer": ans,
            "answer_by": by, "answer_group": grp, "answer_rep": rep,
            "turl": turl or f"https://f/{tid}", "img": f"https://img/{tid}.jpg",
            "n_replies": nrep}


def _args(**kw):
    base = dict(field="all", query=[], any=False, regex=False, case=False,
               answered_only=False, min_rep=0.0, top=10, json=None,
               images=["x"], maker=[], close=0.04)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _search_json(meta, **argkw):
    jf = Path(tempfile.mkdtemp(prefix="mi_out_")) / "out.json"
    _tmp_index_with(meta)
    MI.cmd_search(_args(json=str(jf), **argkw))
    return json.loads(jf.read_text())


# --------------------------------------------------------------------------
# cmd_search
# --------------------------------------------------------------------------
def test_search_finds_and_ranks_by_answer_rep():
    meta = [
        _row(1, q="is this vitro?", ans="Yes, Vitro Conqueror", by="Steph", rep=29.6),
        _row(2, q="vitro?", ans="Vitro all-red", by="Chad", rep=12.0),
        _row(3, q="akro?", ans="Akro corkscrew", by="x", rep=50.0),
    ]
    out = _search_json(meta, query=["vitro"])
    tids = [r["tid"] for r in out]
    assert tids == [1, 2], "only vitro threads, ranked by answer_rep desc"


def test_search_dedups_to_highest_rep_row_per_thread():
    meta = [
        _row(1, q="vitro?", ans="maybe", by="lo", rep=2.0),
        _row(1, q="vitro?", ans="Vitro Conqueror confirmed", by="hi", rep=30.0),
    ]
    out = _search_json(meta, query=["vitro"])
    assert len(out) == 1 and out[0]["answer_rep"] == 30.0, "one row/thread, highest rep kept"


def test_search_answered_only_and_min_rep_filters():
    meta = [
        _row(1, q="vitro?", ans=None, rep=0.0),               # unresolved
        _row(2, q="vitro?", ans="Vitro", by="lo", rep=1.0),   # low rep
        _row(3, q="vitro?", ans="Vitro", by="hi", rep=20.0),  # keeper
    ]
    out = _search_json(meta, query=["vitro"], answered_only=True, min_rep=5.0)
    assert [r["tid"] for r in out] == [3]


def test_search_field_answer_ignores_title_guess():
    # OP title/question says 'vitro' but the EXPERT answer says Akro.
    meta = [_row(1, q="is this vitro?", ans="No — Akro Agate", by="Steph", rep=29.0)]
    assert _search_json(meta, query=["vitro"], field="answer") == []
    assert len(_search_json(meta, query=["akro"], field="answer")) == 1


def test_search_top_limit():
    meta = [_row(i, q="vitro", ans="Vitro", rep=float(i)) for i in range(1, 8)]
    out = _search_json(meta, query=["vitro"], top=3)
    assert len(out) == 3
    assert [r["tid"] for r in out] == [7, 6, 5], "top-3 by rep desc"


def test_search_any_flag():
    meta = [_row(1, q="marble king", ans="Marble King rainbow", rep=5.0)]
    assert _search_json(meta, query=["vitro", "king"], any=True), "OR should match on 'king'"
    assert _search_json(meta, query=["vitro", "king"], any=False) == [], "AND should fail"


# --------------------------------------------------------------------------
# cmd_verify  (embed_images mocked -> no CLIP)
# --------------------------------------------------------------------------
def _verify_json(meta, emb, q_vec, **argkw):
    jf = Path(tempfile.mkdtemp(prefix="mi_vout_")) / "out.json"
    _tmp_index_with(meta, emb=emb)
    orig_embed, orig_load, orig_check = MI.embed_images, MI._load_refs, MI.IDX.check_model
    MI.embed_images = lambda pil: np.asarray([q_vec], dtype="float32")
    MI._load_refs = lambda refs: ["dummy"]
    MI.IDX.check_model = lambda state: None            # skip model guard on temp index
    try:
        MI.cmd_verify(_args(json=str(jf), **argkw))
    finally:
        MI.embed_images, MI._load_refs = orig_embed, orig_load
    return json.loads(jf.read_text())


def test_verify_corroborated_when_keyword_is_global_best():
    # 3 rows; row0 is the keyword maker AND the closest look-alike to q.
    emb = np.array([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]], dtype="float32")
    meta = [_row(1, ans="Vitro Conqueror", rep=29.0),
            _row(2, ans="Akro", rep=5.0),
            _row(3, ans="Marble King", rep=5.0)]
    out = _verify_json(meta, emb, q_vec=[1.0, 0.0], maker=["vitro"], field="answer")
    assert out["verdict"].startswith("CORROBORATED"), out["verdict"]
    assert out["kw_top"] >= out["global_top"] - 1e-6


def test_verify_weak_when_keyword_far_from_best():
    # keyword maker (row2) is visually far from q; a non-keyword row is the best.
    emb = np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
    meta = [_row(1, ans="Akro Agate", rep=5.0),        # closest to q=[1,0]
            _row(2, ans="Vitro Conqueror", rep=5.0)]   # orthogonal -> sim 0
    out = _verify_json(meta, emb, q_vec=[1.0, 0.0], maker=["vitro"], field="answer")
    assert out["verdict"].startswith("WEAK"), out["verdict"]
    assert out["gap"] > 2 * 0.04


def test_verify_no_keyword_match_returns_early_without_json():
    emb = np.array([[1.0, 0.0]], dtype="float32")
    meta = [_row(1, ans="Akro Agate", rep=5.0)]
    jf = Path(tempfile.mkdtemp(prefix="mi_v0_")) / "out.json"
    _tmp_index_with(meta, emb=emb)
    # Restore every patched attribute: leaving _load_refs stubbed leaks into the
    # rest of the session, and the next test to exercise the real _load_refs
    # silently tests the stub instead (it returns ["dummy"] and never raises).
    orig_embed, orig_load, orig_check = MI.embed_images, MI._load_refs, MI.IDX.check_model
    MI.embed_images = lambda pil: np.asarray([[1.0, 0.0]], dtype="float32")
    MI._load_refs = lambda refs: ["dummy"]
    MI.IDX.check_model = lambda state: None
    try:
        MI.cmd_verify(_args(json=str(jf), maker=["vitro"], field="answer"))
    finally:
        MI.embed_images, MI._load_refs = orig_embed, orig_load
        MI.IDX.check_model = orig_check
    assert not jf.exists(), "no-match path returns before writing JSON"


# --------------------------------------------------------------------------
# load_refs_aligned  (the alignment fix behind verify_batch)
# --------------------------------------------------------------------------
def test_load_refs_aligned_keeps_ref_identity_and_skips_failures():
    from PIL import Image
    d = Path(tempfile.mkdtemp(prefix="refs_"))
    p1, p2 = d / "a.jpg", d / "b.jpg"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(p1)
    Image.new("RGB", (4, 4), (4, 5, 6)).save(p2)
    bad = str(d / "missing.jpg")                  # .exists() False -> tries download_pil
    orig = MI.download_pil
    MI.download_pil = lambda url: (_ for _ in ()).throw(RuntimeError("no net"))
    try:
        pairs = MI.load_refs_aligned([str(p1), bad, str(p2)])
    finally:
        MI.download_pil = orig
    assert [ref for ref, _ in pairs] == [str(p1), str(p2)], \
        "failed ref must be dropped WITHOUT shifting the survivors' identity"
    assert len(pairs) == 2 and all(hasattr(im, "size") for _, im in pairs)


def test_load_refs_raises_when_all_fail():
    orig = MI.download_pil
    MI.download_pil = lambda url: (_ for _ in ()).throw(RuntimeError("no net"))
    try:
        MI._load_refs(["http://nope/x.jpg"])
    except SystemExit:
        pass
    else:
        raise AssertionError("_load_refs must raise when nothing loads")
    finally:
        MI.download_pil = orig


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
