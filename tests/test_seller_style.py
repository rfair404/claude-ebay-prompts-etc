#!/usr/bin/env python3
"""The seller style study — measurement, not copying.

`lib/seller_style.py` turns a sample of another seller's ACTIVE listings into
measured technique. Two things have to hold for that to be safe and useful:

  1. The measurements must actually measure — a body skeleton is only worth
     carrying into a guide if the detector finds the same section order a human
     reading the bodies would.
  2. The rendered guide must carry statistics and rules ONLY. If a studied
     seller's title strings or sentences can leak into the guide, the whole
     "study, not copy" line is decoration. That leak check is the test that
     matters most here.

Run:  python tests/test_seller_style.py
  or: pytest tests/test_seller_style.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import seller_style as ss  # noqa: E402

# A fabricated sample in the shape `sample_seller` produces. The distinctive
# strings ("ZZQX", "PLUMWEASEL") exist so the leak test can look for them.
SAMPLE = {
    "seller": "teststore",
    "listings": [
        {
            "itemId": "v1|1|0",
            "title": 'Vtg 1940s ZZQX Brass Candlestick Holder 7 1/2" Pair Estate Mint Condition Lot',
            "askingPrice": 40.0,
            "brand": "ZZQX",
            "probe_category": "Antiques",
            "image_count": 12,
            "description": (
                "<div>UP FOR CONSIDERATION IS THIS PLUMWEASEL BRASS CANDLESTICK."
                "</div><div>SIZE: 7 1/2\" TALL</div><div>CONDITION: MINT WITH NO "
                "WEAR.</div><div>MAKER'S MARK: SIGNED, SHOWN IN PHOTO</div>"
            ),
        },
        {
            "itemId": "v1|2|0",
            "title": "Antique Sterling Silver Spoon Set Signed Ornate Floral Estate Find Rare Nice",
            "askingPrice": 90.0,
            "brand": "Unbranded",
            "probe_category": "Antiques",
            "image_count": 8,
            "description": (
                "<div>UP FOR CONSIDERATION IS THIS PLUMWEASEL SPOON SET.</div>"
                "<div>SIZE: 6\" LONG</div><div>CONDITION: LIGHT WEAR.</div>"
                "<div>MAKER'S MARK: STERLING</div>"
            ),
        },
        {
            "itemId": "v1|3|0",
            "title": "Mid-Century Glass Vase Green Swirl 9in Tall",
            "askingPrice": 20.0,
            "probe_category": "Pottery & Glass",
            "image_count": 5,
            "description": "<p>a short plain body with no labelled sections at all</p>",
        },
    ],
}


def test_strip_html_renders_paragraphs():
    txt = ss.strip_html("<div>one</div><div>two</div><br>three")
    assert "one" in txt and "three" in txt
    assert "<" not in txt
    # paragraph breaks survive, so paragraph counts mean something
    assert txt.count("\n\n") >= 1


def test_title_stats_measure_slots_and_budget():
    t = ss.title_stats(SAMPLE["listings"])
    assert t["n"] == 3
    # two of three lead with an era word, so the mean era slot sits at the front
    assert t["mean_slot"]["era"] is not None and t["mean_slot"]["era"] < 2
    assert t["slot_coverage_pct"]["era"] > 60
    # material shows up, but later in the title than the era word
    assert t["mean_slot"]["material"] > t["mean_slot"]["era"]
    assert t["pct_with_measurement"] > 0
    assert t["pct_with_year_or_decade"] > 0
    # the brand aspect counts only when it is actually in the title, and
    # "Unbranded" is never a brand
    assert t["pct_with_brand_in_title"] == round(100 / 3, 1)
    assert t["descriptor_budget"]["max"] >= 2
    assert t["leading_tokens"][0][0] in {"vtg", "antique", "mid-century"}


def test_body_skeleton_finds_the_section_order():
    b = ss.body_stats(SAMPLE["listings"])
    labels = [r["label"] for r in b["skeleton"]["sections"]]
    assert labels[:3] == ["SIZE", "CONDITION", "MAKER'S MARK"]
    # two of three bodies carry the skeleton
    assert b["skeleton"]["sections"][0]["pct"] == round(200 / 3, 1)
    assert b["pct_all_caps_body"] > 0


def test_photo_stats():
    p = ss.photo_stats(SAMPLE["listings"])
    assert p["n"] == 3
    assert p["photos"]["median"] == 8
    assert p["pct_12_or_more"] == round(100 / 3, 1)


def test_analyze_without_images_does_no_network():
    stats = ss.analyze(SAMPLE)          # images=0 → no fetching
    assert stats["look"] == {"n": 0}
    assert stats["sampled"] == 3
    assert dict(stats["categories"])["Antiques"] == 2


def test_guide_and_study_carry_no_verbatim_listing_text():
    """The bright line: technique out, the seller's own text never."""
    stats = ss.analyze(SAMPLE)
    guide = ss.render_guide(stats, "teststore")
    study = ss.render_study(stats)
    for rec in SAMPLE["listings"]:
        title = rec["title"]
        body = ss.strip_html(rec["description"])
        for doc in (guide, study):
            assert title not in doc
            # no sentence-length run of their copy either
            for sentence in [s for s in body.split(".") if len(s.split()) >= 5]:
                assert sentence.strip() not in doc
        # the fabricated marker words appear nowhere in the guide
        assert "PLUMWEASEL" not in guide.upper()


def test_guide_states_the_defaults_that_keep_it_safe():
    guide = ss.render_guide(ss.analyze(SAMPLE), "teststore")
    assert "default: off" in guide
    assert "status: draft" in guide
    assert "House rules win" in guide
    # and it must point back at the evidence
    assert "_studies/teststore.md" in guide


def test_slugify():
    assert ss.slugify("Patina Elements LLC!") == "patina-elements-llc"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                fails += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if fails else 0)
