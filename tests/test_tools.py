#!/usr/bin/env python3
"""Unit tests for the PURE helpers in the new tools/ CLIs.

Only the model-free / network-free logic is exercised (dedupe, reading-order,
record formatting, thread grouping, filename sanitising). The heavy paths
(CLIP embed, index build, cv2 crop, montage download) are covered by the
end-to-end tools when run against a real index, not here.

Run:  python tests/test_tools.py
  or: pytest tests/test_tools.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                     # noqa: E402
import verify_batch                    # noqa: E402
import marble_triage                   # noqa: E402
import marble_typechart                # noqa: E402
import marble_decide                   # noqa: E402
import reindex_forum                   # noqa: E402


# --------------------------------------------------------------------------
# verify_batch._marble_name
# --------------------------------------------------------------------------
def test_marble_name_strips_dsc_and_hdr():
    assert verify_batch._marble_name("/a/b/DSC_0042.jpg") == "0042"
    assert verify_batch._marble_name("shoot3_m01_HDR.jpg") == "shoot3_m01"
    assert verify_batch._marble_name("plain.png") == "plain"


# --------------------------------------------------------------------------
# marble_triage._best_per / reading_order / fmt
# --------------------------------------------------------------------------
def test_best_per_dedups_by_key_keeping_top_score():
    meta = [{"tid": 1}, {"tid": 1}, {"tid": 2}]
    sims = [0.5, 0.9, 0.7]
    out = marble_triage._best_per(meta, sims, "tid")
    assert [round(s, 3) for s, _ in out] == [0.9, 0.7], "best score per tid, ranked desc"
    assert out[0][1]["tid"] == 1 and out[1][1]["tid"] == 2


def test_reading_order_rows_then_columns():
    # 2x2 grid: circles (x, y, r)
    items = [((50, 10, 5), "TR"), ((10, 10, 5), "TL"),
             ((50, 50, 5), "BR"), ((10, 50, 5), "BL")]
    out = [tag for _, tag in marble_triage.reading_order(items)]
    assert out == ["TL", "TR", "BL", "BR"], f"top row L→R then bottom row, got {out}"


def test_reading_order_single_item_passthrough():
    items = [((1, 2, 3), "only")]
    assert marble_triage.reading_order(items) == items


def _full_rec():
    return {
        "family": [{"f": "machine", "p": 0.6}, {"f": "handmade", "p": 0.3}],
        "family_clip": [{"f": "machine", "p": 0.5}],
        "family_soft": True,
        "makers": [{"label": "Vitro", "p": 0.4, "tier": "$"}],
        "makers_hidden": 2,
        "forum_on": True,
        "forum_top": 0.812,
        "forum_corrob": 3,
        "forum": [{"sim": 0.812, "answer": "Vitro Conqueror", "by": "Steph",
                   "rep": 29.6, "title": "?", "url": "u"}],
        "forum_makers": {"vitro": 9},
        "maker_slam_dunk": True,
        "maker_verdict": "vitro",
        "comps_median": 12.5,
        "comps_range": [10.0, 15.0],
        "comps": [{"sim": 0.7, "price": 10.0, "title": "a marble", "url": "u"}],
    }


def test_fmt_renders_key_fields_including_median():
    txt = marble_triage.fmt(_full_rec(), "shoot1-m1")
    assert "[shoot1-m1]" in txt
    assert "⚠SOFT" in txt, "family_soft must surface"
    assert "SLAM DUNK" in txt and "vitro" in txt
    assert "median $12.50" in txt, "median formatted with 2 decimals"


def test_fmt_split_when_no_slam_dunk():
    rec = _full_rec()
    rec.update(maker_slam_dunk=False, maker_verdict=None, forum_makers={"vitro": 3, "akro": 2})
    txt = marble_triage.fmt(rec, "x")
    assert "split" in txt.lower() and "SHOW USER" in txt


def test_comps_uses_true_median_and_excludes_bool_price():
    # 5 distinct itemIds; item5's price is a bool and must be excluded.
    ee = np.eye(5, dtype="float32")
    q = np.array([5, 4, 3, 2, 1], dtype="float32")
    em = [{"itemId": 1, "price": 10.0}, {"itemId": 2, "price": 20.0},
          {"itemId": 3, "price": 30.0}, {"itemId": 4, "price": 40.0},
          {"itemId": 5, "price": True}]
    rec = {}
    marble_triage._comps(rec, q, (ee, em), top=10)
    # true median of [10,20,30,40] is 25.0 (NOT the old upper-middle 30.0)
    assert rec["comps_median"] == 25.0, rec["comps_median"]
    assert rec["comps_range"] == [10.0, 40.0]


def test_comps_none_when_no_priced_comps():
    ee = np.eye(2, dtype="float32")
    q = np.array([1, 1], dtype="float32")
    em = [{"itemId": 1}, {"itemId": 2, "price": None}]
    rec = {}
    marble_triage._comps(rec, q, (ee, em), top=10)
    assert rec["comps_median"] is None and rec["comps_range"] is None


# --------------------------------------------------------------------------
# reindex_forum._recent_threads / _is_thumb
# --------------------------------------------------------------------------
def test_recent_threads_groups_and_takes_newest():
    meta = [{"tid": 5, "title": "e"}, {"tid": 5, "title": "e"},
            {"tid": 9, "title": "i"}, {"tid": 3, "title": "c"},
            {"tid": None, "title": "skip"}]
    out = reindex_forum._recent_threads(meta, 2)
    assert [t for t, _, _ in out] == [9, 5], "newest (highest-tid) first, None dropped"
    assert out[0][1] == "i" and len(out[1][2]) == 2, "carries title + all rows"


def test_is_thumb():
    assert reindex_forum._is_thumb("https://x/img.thumb.jpg")
    assert reindex_forum._is_thumb("HTTPS://X/IMG.THUMB.JPG")     # case-insensitive
    assert not reindex_forum._is_thumb("https://x/img.jpg")


# --------------------------------------------------------------------------
# _safe (marble_typechart + marble_decide) — filename sanitiser
# --------------------------------------------------------------------------
def test_safe_sanitises_non_alnum():
    for mod in (marble_typechart, marble_decide):
        assert mod._safe("Marble King / Rainbo") == "Marble_King___Rainbo"
        assert mod._safe("Vitro") == "Vitro"


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
