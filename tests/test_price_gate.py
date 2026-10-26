#!/usr/bin/env python3
"""lib/price_gate.py — a price set without a sold pull does not go live.

One table over what PRICE can leave in price.txt (the `B · Browser :` research
log line, its proof on disk, the ONE-OFF and OPERATOR PRICE lines) against
what the draft says. Each row states whether the gate passes and, when it
blocks, a phrase the refusal must contain so Russell's card names the fix.
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from draft_io import Draft                                     # noqa: E402
from price_gate import sold_pull_issues                        # noqa: E402

RAN = "  B · Browser   : RAN — query \"x\" — best_match n=9 + price_high n=9 — saved 2 JSONs — price_stats n_kept=8, conf=good\n"
RAN_THIN = "  B · Browser   : RAN — query \"x\" — best_match n=0 + price_high n=0 — saved 2 JSONs — price_stats n_kept=0, conf=thin\n"
RAN_FEW = "  B · Browser   : RAN — saved best_match n=3 + price_high n=3 — price_stats n_kept=3, conf=thin\n"
THIN = "  B · Browser   : THIN — dual query ran, page says \"0 results\" (ladder descended to L3)\n"
UNAVAIL = "  B · Browser   : UNAVAILABLE — no claude-in-chrome (logged-in browser) in this session\n"
BLOCKED = "  B · Browser   : BLOCKED — extractor n:0 on a page with visible results\n"
C_LINE = "  C · Chrome    : NOT TRIGGERED — confidence OK\n"
ONE_OFF = "ONE-OFF: historian Hopkins, 1930s US bench tool · judgment $120 · push-high $249.99\n"
ONE_OFF_NO_HIST = "ONE-OFF: historian  · judgment $120 · push-high $249.99\n"
OPERATOR = "OPERATOR PRICE: Russell: keep it at $90 (2026-10-24)\n"

JSON_PROOF = {"apify_run_20261024T010101Z_browse-best_match-p1-20261023T210101.json": "{}"}
CSV_HEAD = "captured_at,item,stage,query,price,title,sold_date,condition,listing_type,url,note\n"
CSV_SOLD_C = {"comps.csv": CSV_HEAD + "t,1,C,q,10,T,2026-10-01,Used,sold,https://www.ebay.com/itm/1,\n"}
CSV_ASK_C = {"comps.csv": CSV_HEAD + "t,1,C,q,10,T,,Used,active,https://www.ebay.com/itm/1,[active — ASKING PRICE]\n"}
CSV_A_ONLY = {"comps.csv": CSV_HEAD + "t,1,A,q,10,T,2026-10-01,Used,sold,https://www.ebay.com/itm/1,\n"}

FIXED = {"price": "24.99"}
BO_AT_PUSH = {"price": "249.99", "best_offer": {"enabled": True}}
BO_BELOW_PUSH = {"price": "119.99", "best_offer": {"enabled": True}}
NO_BO_AT_PUSH = {"price": "249.99"}
AUCTION = {"price": "99.00", "format": "AUCTION"}
LIVE = {"price": "24.99", "meta": {"ebay_listing_id": "880000000001"}}

CASES = [
    # id, price.txt (None = absent), extra files, frontmatter, passes, refusal must contain
    ("ran-with-json", RAN + C_LINE, JSON_PROOF, FIXED, True, None),
    ("ran-with-sold-csv", RAN + C_LINE, CSV_SOLD_C, FIXED, True, None),
    ("ran-no-proof", RAN + C_LINE, {}, FIXED, False, "no Stage B save"),
    ("ran-asks-only-csv", RAN + C_LINE, CSV_ASK_C, FIXED, False, "no Stage B save"),
    ("ran-websearch-only-csv", RAN + C_LINE, CSV_A_ONLY, FIXED, False, "no Stage B save"),
    ("unavailable", UNAVAIL + C_LINE, CSV_SOLD_C, FIXED, False, "without a sold pull"),
    ("blocked", BLOCKED, JSON_PROOF, FIXED, False, "BLOCKED"),
    ("no-price-txt", None, {}, FIXED, False, "PRICE never ran"),
    ("no-b-line", "Max supported price: $24.99\n", {}, FIXED, False, "no `B · Browser :`"),
    ("refresh-on-top-wins", RAN + C_LINE + "\n## PRIOR RECORD\n" + UNAVAIL, JSON_PROOF, FIXED, True, None),
    ("stale-ran-under-unavailable-refresh", UNAVAIL + "\n## PRIOR RECORD\n" + RAN, JSON_PROOF, FIXED, False, "without a sold pull"),
    ("operator-price", UNAVAIL + OPERATOR, {}, FIXED, True, None),
    ("live-listing-exempt", UNAVAIL, {}, LIVE, True, None),
    ("thin-no-one-off-line", THIN, {}, BO_AT_PUSH, False, "ONE-OFF"),
    ("thin-one-off-bo-at-push", THIN + ONE_OFF, {}, BO_AT_PUSH, True, None),
    ("thin-one-off-below-push", THIN + ONE_OFF, {}, BO_BELOW_PUSH, False, "below the one-off Push-high"),
    ("thin-one-off-no-bo", THIN + ONE_OFF, {}, NO_BO_AT_PUSH, False, "without Best Offer"),
    ("thin-one-off-auction", THIN + ONE_OFF, {}, AUCTION, True, None),
    ("thin-one-off-no-historian", THIN + ONE_OFF_NO_HIST, {}, BO_AT_PUSH, False, "historian"),
    ("ran-but-zero-kept-is-one-off", RAN_THIN + ONE_OFF, JSON_PROOF, BO_BELOW_PUSH, False, "below the one-off Push-high"),
    ("ran-thin-cohort-is-era-peer-not-one-off", RAN_FEW + C_LINE, JSON_PROOF, FIXED, True, None),
    ("ran-but-zero-kept-compliant", RAN_THIN + ONE_OFF, JSON_PROOF, BO_AT_PUSH, True, None),
]


@pytest.mark.parametrize("price_txt,files,fm,passes,needle",
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_sold_pull_gate(price_txt, files, fm, passes, needle):
    with tempfile.TemporaryDirectory() as td:
        shoot = Path(td)
        if price_txt is not None:
            (shoot / "price.txt").write_text(price_txt, encoding="utf-8")
        for name, body in files.items():
            (shoot / name).write_text(body, encoding="utf-8")
        issues = sold_pull_issues(Draft(path=shoot / "draft.md", frontmatter=fm, body=""))
    if passes:
        assert issues == []
    else:
        assert issues and all(i.startswith("price: ") for i in issues)
        assert any(needle in i for i in issues), issues
