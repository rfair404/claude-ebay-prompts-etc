"""/lower: the aging price-step rules (lib/lower.py).

Every test here fails when a house rule breaks: the cut math, the $36 rule,
the melt floor and its fail-closed spot, KBD and K01, the 15-day clock,
price.txt reuse, the backfill, and the apply path's guards.
"""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import lower as L  # noqa: E402

TODAY = date(2026, 11, 3)


# --- cut math ---------------------------------------------------------------

@pytest.mark.parametrize("current,rec,new,step", [
    # third of the gap, rounded down: 149 - 49/3 = 132.67 -> 132
    ("149", "100", "132", "17"),
    # 15% cap binds: 200 - 150/3 = 150, but 200*0.85 = 170
    ("200", "50", "170", "30"),
    # cap rounds UP so the step never passes 15%: 99*0.85 = 84.15 -> 85
    ("99", "40", "85", "14"),
    # margin binds: rec 100 + 5% = 105; third = 110 - 10/3 = 106.67 -> 106
    ("110", "100", "106", "4"),
    # 5% beats $2 above $40: rec 41 -> margin $2.05; third = 47
    ("50", "41", "47", "3"),
    # non-whole current price ends whole; 129.99*0.85 = 110.49 -> 111
    ("129.99", "60", "111", "18.99"),
])
def test_cut_math_table(current, rec, new, step):
    c = L.cut_math(D(current), D(rec))
    assert c.status == L.PROPOSED
    assert c.new == D(new) and c.step == D(step)
    assert c.new == c.new.to_integral_value()                 # whole dollars
    assert c.new >= D(current) * D("0.85")                    # cap
    assert c.new >= D(rec) + L.margin(D(rec))                 # margin


def test_margin_is_two_dollars_or_five_percent():
    assert L.margin(D("20")) == D("2")
    assert L.margin(D("100")) == D("5.00")


def test_at_target_inside_margin():
    assert L.cut_math(D("105"), D("100")).status == L.AT_TARGET
    assert L.cut_math(D("42"), D("40")).status == L.AT_TARGET     # 40 + $2
    assert L.cut_math(D("100"), D("100")).status == L.AT_TARGET


def test_step_under_two_dollars_is_skipped():
    # rec 100 -> target 105; 106 - 105 = $1
    c = L.cut_math(D("106"), D("100"))
    assert c.status == L.SKIPPED and c.new is None


def test_best_offer_floors_scale_and_round_down():
    c = L.cut_math(D("149"), D("100"), auto_decline=D("100"), auto_accept=D("140"))
    # 132/149 of 100 = 88.59 -> 88; of 140 = 124.03 -> 124
    assert c.new_decline == D("88") and c.new_accept == D("124")
    assert L.cut_math(D("149"), D("100")).new_decline is None


# --- the $36 rule -----------------------------------------------------------

@pytest.mark.parametrize("ask,held", [
    ("35.99", True), ("36.00", False), ("30.00", True), ("25", True),
    ("27.50", True), ("24.99", True), ("120", False)])
def test_36_dollar_rule(ask, held):
    assert (L.ask_rule(D(ask)) is not None) is held


def test_36_rule_reasons_name_the_band():
    assert "30-35.99" in L.ask_rule(D("35.99"))
    assert "25 or under" in L.ask_rule(D("25"))


# --- evaluate() fixtures ----------------------------------------------------

def _shoot(tmp_path, name="item", tiers=("40", "60", "90"), age_days=1):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    c, r, p = tiers
    lines = [f"  Conservative  ${c}   — floor"] if c else []
    lines += [f"  Recommended   ${r}   — median"] if r else []
    lines += [f"  Push-high     ${p}   — ceiling"]
    f = d / "price.txt"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    t = (TODAY - timedelta(days=age_days)).toordinal()
    ts = (date.fromordinal(t) - date(1970, 1, 1)).days * 86400 + 43200
    os.utime(f, (ts, ts))
    return d


def _listing(price="90", start_days=40, title="Vintage brass widget", sku="abc123",
             item_id="111"):
    return L.Listing(store="default", item_id=item_id, sku=sku, title=title,
                     price=D(price), start=TODAY - timedelta(days=start_days))


def _ctx(shoot=None, **kw):
    kw.setdefault("shoot_dir", lambda l: shoot)
    return L.Ctx(today=TODAY, **kw)


def test_proposed_end_to_end(tmp_path):
    row = L.evaluate(_listing(), _ctx(_shoot(tmp_path)))
    assert row["status"] == L.PROPOSED
    assert row["new_price"] == "80.00" and row["recommended"] == "60.00"


# --- 15-day clock -----------------------------------------------------------

def test_fifteen_day_boundary(tmp_path):
    s = _shoot(tmp_path)
    assert L.evaluate(_listing(start_days=15), _ctx(s))["status"] == L.SKIPPED
    assert L.evaluate(_listing(start_days=16), _ctx(s))["status"] == L.PROPOSED


def test_log_entry_restarts_the_clock(tmp_path):
    log = [{"sku": "abc123", "item_id": "", "store": "", "date": "2026-10-25",
            "source": "backfill:last_cut"}]
    row = L.evaluate(_listing(start_days=90), _ctx(_shoot(tmp_path), log_rows=log))
    assert row["status"] == L.SKIPPED
    assert row["last_change"] == "2026-10-25" and row["last_change_source"] == "backfill:last_cut"


def test_relist_after_log_entry_uses_start_date():
    log = [{"sku": "abc123", "date": "2026-09-01", "source": "lower"}]
    d, src = L.last_change(log, sku="abc123", item_id="", store="default",
                           start=date(2026, 10, 1))
    assert (d, src) == (date(2026, 10, 1), "listed")


def test_log_from_other_store_does_not_count():
    log = [{"sku": "abc123", "store": "junk", "date": "2026-11-01", "source": "lower"}]
    d, _ = L.last_change(log, sku="abc123", item_id="", store="default",
                         start=date(2026, 9, 1))
    assert d == date(2026, 9, 1)


# --- price.txt reuse vs STALE_PRICE -----------------------------------------

def test_fresh_price_txt_is_reused(tmp_path):
    assert L.evaluate(_listing(), _ctx(_shoot(tmp_path, age_days=15)))["status"] == L.PROPOSED


def test_old_price_txt_is_stale(tmp_path):
    row = L.evaluate(_listing(), _ctx(_shoot(tmp_path, age_days=16)))
    assert row["status"] == L.STALE_PRICE and "16 days" in row["reason"]


def test_missing_price_txt_and_missing_recommended_are_stale(tmp_path):
    assert L.evaluate(_listing(), _ctx(None))["status"] == L.STALE_PRICE
    s = _shoot(tmp_path, tiers=("", "", "149"))
    assert L.evaluate(_listing(), _ctx(s))["reason"] == "price.txt has no Recommended tier"


def test_tiers_above_the_first_end_line_only():
    t = ("  Recommended   $60\n=== END PRICE (prior record below) ===\n"
         "  Conservative  $10\n")
    tiers = L.read_tiers(t)
    assert tiers["recommended"] == D("60") and tiers["conservative"] is None


def test_rerun_cap_queues_oldest_first():
    ctx = _ctx(None)
    ls = [_listing(sku=f"s{i}", item_id=str(i), start_days=20 + i) for i in range(5)]
    rows = L.scan_rows(ls, ctx, max_reprice=2)
    queued = [r["sku"] for r in rows if r["rerun"] == "yes"]
    assert sorted(queued) == ["s3", "s4"]
    assert sum(r["rerun"] == "no" for r in rows) == 3
    assert all("re-run cap" in r["reason"] for r in rows if r["rerun"] == "no")


# --- holds and consignment --------------------------------------------------

def test_kbd_gold_is_held_but_k01_same_number_is_not(tmp_path):
    s = _shoot(tmp_path)
    kbd = {"item_id": "KBD-C020", "client_code": "KBD"}
    assert L.evaluate(_listing(), _ctx(s, consign_row=lambda l: kbd))["status"] == L.HELD
    k01 = {"item_id": "K01-C020", "client_code": "K01"}
    row = L.evaluate(_listing(), _ctx(s, consign_row=lambda l: k01))
    assert row["status"] == L.PROPOSED and "K01 consignment (no ads)" in row["note"]


def test_consignment_floor_is_never_crossed(tmp_path):
    crow = {"item_id": "K01-C001", "client_code": "K01", "floor": "85"}
    row = L.evaluate(_listing(), _ctx(_shoot(tmp_path), consign_row=lambda l: crow))
    assert row["status"] == L.HELD and "consignment floor" in row["reason"]


def test_hold_file_holds_until_its_date(tmp_path):
    s = _shoot(tmp_path)
    holds = [{"sku": "abc123", "until": "2026-11-29", "reason": "Thanksgiving hold"}]
    assert L.evaluate(_listing(), _ctx(s, holds=holds))["status"] == L.HELD
    holds[0]["until"] = "2026-11-01"
    assert L.evaluate(_listing(), _ctx(s, holds=holds))["status"] == L.PROPOSED


def test_conservative_and_cost_floors(tmp_path):
    s = _shoot(tmp_path, tiers=("85", "60", "90"))      # Conservative above the new ask
    assert "Conservative" in L.evaluate(_listing(), _ctx(s))["reason"]
    s2 = _shoot(tmp_path, name="b")
    row = L.evaluate(_listing(), _ctx(s2, cost=lambda l: D("82")))
    assert row["status"] == L.HELD and "cost" in row["reason"]


def test_under_36_held_before_price_research(tmp_path):
    row = L.evaluate(_listing(price="35.99"), _ctx(None))
    assert row["status"] == L.HELD                 # not STALE_PRICE: no re-run wasted


# --- gold / silver melt floor -----------------------------------------------

GOLD = "Estate 14K Yellow Gold Rope Chain Necklace 18 in 10.0g"


def test_metal_detection():
    assert L.metal_of(GOLD)[0] == "gold"
    assert L.metal_of("Vintage 1/20 12K Gold Filled Bracelet") is None
    assert L.metal_of("14K Gold Plated Hoop Earrings") is None
    assert L.metal_of("Sterling Silver Ring Size 7")[0] == "silver"
    assert L.metal_of("Wallace Silverplate Footed Bowl") is None
    assert L.weight_of(GOLD) == D("10.0")


def test_melt_floor_holds_a_cut_below_melt(tmp_path):
    s = _shoot(tmp_path, tiers=("200", "250", "450"))
    # 10 g * .585 * $4000 / 31.1035 = $752 melt; cut to 383 is under it
    row = L.evaluate(_listing(price="450", title=GOLD),
                     _ctx(s, spot=lambda m: D("4000")))
    assert row["status"] == L.HELD and "melt" in row["reason"]


def test_melt_floor_allows_a_cut_above_melt(tmp_path):
    s = _shoot(tmp_path, tiers=("200", "250", "450"))
    row = L.evaluate(_listing(price="450", title=GOLD), _ctx(s, spot=lambda m: D("1000")))
    assert row["status"] == L.PROPOSED and row["melt"]


def test_best_offer_floor_below_melt_is_held(tmp_path):
    s = _shoot(tmp_path, tiers=("200", "250", "450"))
    # melt at $1000 spot = $188; floor 200 * 383/450 = 170 < 188
    row = L.evaluate(_listing(price="450", title=GOLD),
                     _ctx(s, spot=lambda m: D("1000"), floors=lambda l: (D("200"), None)))
    assert row["status"] == L.HELD and "Best Offer floor" in row["reason"]


def test_spot_unavailable_fails_closed(tmp_path):
    s = _shoot(tmp_path, tiers=("200", "250", "450"))
    row = L.evaluate(_listing(price="450", title=GOLD), _ctx(s, spot=lambda m: None))
    assert row["status"] == L.HELD and row["reason"] == "spot unavailable"


def test_unknown_weight_fails_closed(tmp_path):
    s = _shoot(tmp_path, tiers=("200", "250", "450"))
    row = L.evaluate(_listing(price="450", title="14K Yellow Gold Ring Size 6"),
                     _ctx(s, spot=lambda m: D("1000")))
    assert row["status"] == L.HELD and "weight" in row["reason"]


def test_live_spot_refuses_old_quote(monkeypatch):
    import io
    import json

    def fake(url, timeout=0):
        body = json.dumps({"price": 4000, "updatedAt": "2026-01-01T00:00:00Z"})
        return io.BytesIO(body.encode())
    monkeypatch.setattr(L.urllib.request, "urlopen", fake)
    assert L.live_spot("gold") is None


def test_live_spot_network_failure_is_none(monkeypatch):
    def down(url, timeout=0):
        raise L.urllib.error.URLError("offline")
    monkeypatch.setattr(L.urllib.request, "urlopen", down)
    assert L.live_spot("gold") is None


# --- backfill ---------------------------------------------------------------

def test_parse_last_cut_cells():
    y = 2026
    assert L.parse_last_cut("2026-10-11 -5% (30+d rule) 39.99->38.00", y) == (
        date(2026, 10, 11), "39.99", "38.00")
    assert L.parse_last_cut("cut10-22", y)[0] == date(2026, 10, 22)
    assert L.parse_last_cut("reverted10-22", y)[0] == date(2026, 10, 22)
    assert L.parse_last_cut("never (Halloween hold to 2026-11-01)", y) is None
    assert L.parse_last_cut("in10-22:", y) is None
    assert L.parse_last_cut("new since 10-22", y) is None


def _reports(tmp_path):
    r = tmp_path / "reports"
    (r / "markdowns").mkdir(parents=True)
    (r / "aging_price_cut_2026-10-24b.csv").write_text(
        "store,sku,listing_id,title,current_price,auto_decline,last_cut,in_proposal,"
        "proposed_price\n"
        "default,acda0eb2,206511905657,Cat & Dog,73.00,52.00,2026-10-22 -10%,yes,70.00\n"
        "default,zzz,206000000001,Unapplied,50.00,,,yes,47.00\n", encoding="utf-8")
    (r / "aging_price_cut_2026-10-24b_apply.log").write_text(
        "[OK] updated price\n"
        "== 206511905657 acda0eb2 $73.00 -> $70.00  floor -> $50.00  Vintage Cat\n"
        "APPLIED: ok 1, skipped 0, failed 0 (of 1)\n", encoding="utf-8")
    (r / "aging_price_cut_2026-10-29_apply.log").write_text(
        "== 206000000001 zzz $50.00 -> $47.00  x\nDRY RUN: ok 1\n", encoding="utf-8")
    (r / "k01_cut_2026-10-27_apply.log").write_text(
        "== K01-C077 0a238c24 junk $11.99 -> $11.39 Ridgid rule\n"
        "APPLIED: ok 1, problems 0 of 1\n", encoding="utf-8")
    (r / "markdowns" / "2026-11-02-aging.csv").write_text(
        "store,sku,listing_id,title,last_cut,hold_until,excluded_reason\n"
        "default,p1,880009332259,Pfaff,,,\"Russell's price: Pfaff CPC test\"\n"
        "default,t1,2061,Turkey,never,2026-11-29,Fall/Thanksgiving hold\n"
        "default,u1,2062,Mag,,,under $36 (Russell's rule)\n", encoding="utf-8")
    return r


def test_backfill_reads_applied_logs_and_last_cut_only(tmp_path):
    r = _reports(tmp_path)
    log, holds = tmp_path / "pc.csv", tmp_path / "holds.csv"
    added, h, _ = L.backfill(r, log, holds)
    rows = L.read_csv(log)
    keys = {(x["sku"], x["date"]) for x in rows}
    assert ("acda0eb2", "2026-10-24") in keys            # applied log
    assert ("acda0eb2", "2026-10-22") in keys            # last_cut column
    assert ("0a238c24", "2026-10-27") in keys            # K01 log format
    assert not any(x["sku"] == "zzz" for x in rows)      # proposal / dry run only
    applied = next(x for x in rows if x["date"] == "2026-10-24")
    assert (applied["old_ask"], applied["new_ask"], applied["new_floors"]) == (
        "73.00", "70.00", "50.00")
    assert applied["store"] == "default"
    held = {x["sku"] for x in L.read_csv(holds)}
    assert held == {"p1", "t1"}                          # not "Russell's rule"
    assert added == 3 and h == 2


def test_backfill_is_idempotent(tmp_path):
    r = _reports(tmp_path)
    log, holds = tmp_path / "pc.csv", tmp_path / "holds.csv"
    L.backfill(r, log, holds)
    before = (log.read_text(), holds.read_text())
    added, h, _ = L.backfill(r, log, holds)
    assert (added, h) == (0, 0)
    assert (log.read_text(), holds.read_text()) == before


# --- apply ------------------------------------------------------------------

def _queue_row(**kw):
    r = {f: "" for f in L.QUEUE_FIELDS}
    r.update(store="default", item_id="111", sku="abc123", title="Widget",
             status=L.PROPOSED, current_price="90.00", new_price="80.00", step="10.00",
             auto_decline="70.00", new_auto_decline="62.00")
    r.update(kw)
    return r


def _draft(tmp_path):
    d = tmp_path / "shoot"
    d.mkdir()
    (d / "draft.md").write_text(
        '---\ntitle: "Widget"\nprice: "90.00"\nbest_offer:\n  enabled: true\n'
        '  auto_decline_amount: "70.00"\n  auto_accept_amount: null\n---\nbody\n',
        encoding="utf-8")
    return d


def _apply(tmp_path, rows, *, confirm, live="90.00", rc=0, yes=("abc123",)):
    d = _draft(tmp_path)
    calls, seen = [], {}

    def run(r, path, fields, conf):
        calls.append((fields, conf))
        seen["text"] = (path / "draft.md").read_text()
        return rc
    log = tmp_path / "pc.csv"
    res = L.apply_rows(rows, set(yes), confirm=confirm, live_price=lambda r: D(live),
                       draft_for=lambda r: d, run_update=run, log_path=log, today=TODAY)
    return res, calls, log, d, seen


def test_apply_dry_run_never_writes_the_log_and_restores_the_draft(tmp_path):
    res, calls, log, d, seen = _apply(tmp_path, [_queue_row()], confirm=False)
    assert [x.outcome for x in res] == ["dry-run ok"]
    assert calls == [("price,bestoffer", False)]
    assert 'price: "80.00"' in seen["text"] and '"62.00"' in seen["text"]
    assert not log.exists()
    assert 'price: "90.00"' in (d / "draft.md").read_text()


def test_apply_refuses_when_live_price_moved(tmp_path):
    res, calls, log, _, _ = _apply(tmp_path, [_queue_row()], confirm=True, live="85.00")
    assert [x.outcome for x in res] == ["refused"]
    assert calls == [] and not log.exists()


def test_apply_confirm_logs_source_lower(tmp_path):
    res, calls, log, d, _ = _apply(tmp_path, [_queue_row()], confirm=True)
    assert [x.outcome for x in res] == ["applied"] and calls == [("price,bestoffer", True)]
    row = L.read_csv(log)[0]
    assert (row["old_ask"], row["new_ask"], row["source"], row["date"]) == (
        "90.00", "80.00", "lower", "2026-11-03")
    assert '"Widget"' in (d / "draft.md").read_text()          # title untouched


def test_apply_failed_update_is_not_logged(tmp_path):
    res, _, log, d, _ = _apply(tmp_path, [_queue_row()], confirm=True, rc=1)
    assert [x.outcome for x in res] == ["failed"] and not log.exists()
    assert 'price: "90.00"' in (d / "draft.md").read_text()


def test_apply_only_yes_and_proposed(tmp_path):
    rows = [_queue_row(), _queue_row(sku="other", item_id="222"),
            _queue_row(sku="held", item_id="333", status=L.HELD)]
    res, calls, *_ = _apply(tmp_path, rows, confirm=False, yes=("abc123", "held"))
    assert [x.sku for x in res] == ["abc123"] and len(calls) == 1


def test_apply_accepts_item_number(tmp_path):
    res, *_ = _apply(tmp_path, [_queue_row()], confirm=False, yes=("111",))
    assert [x.outcome for x in res] == ["dry-run ok"]


def test_set_draft_prices_keeps_title():
    t = '---\ntitle: "price: 5 Widget"\nprice: "90.00"\n---\n'
    new = L.set_draft_prices(t, D("80"), None, None)
    assert L._title(new) == L._title(t) and 'price: "80.00"' in new


# --- scan never writes ------------------------------------------------------

def test_trading_read_refuses_write_calls():
    for call in ("ReviseFixedPriceItem", "ReviseItem", "EndItem", "AddItem"):
        with pytest.raises(PermissionError):
            L.trading_read(call, "", creds=None)


ACTIVE_XML = """<GetMyeBaySellingResponse><Ack>Success</Ack><ActiveList><ItemArray>
<Item><ItemID>206511905657</ItemID><SKU>acda0eb2</SKU><Title>Cat &amp; Dog</Title>
<ListingType>FixedPriceItem</ListingType>
<ListingDetails><StartTime>2026-09-01T10:00:00.000Z</StartTime></ListingDetails>
<SellingStatus><CurrentPrice currencyID="USD">73.0</CurrentPrice></SellingStatus>
<PictureDetails><GalleryURL>https://i.ebayimg.com/x.jpg</GalleryURL></PictureDetails>
<WatchCount>5</WatchCount></Item>
</ItemArray><PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages></PaginationResult>
</ActiveList></GetMyeBaySellingResponse>"""

ITEM_XML = """<GetItemResponse><Ack>Success</Ack><Item><Title>Cat &amp; Dog</Title>
<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled></BestOfferDetails>
<ListingDetails><MinimumBestOfferPrice currencyID="USD">52.0</MinimumBestOfferPrice>
</ListingDetails><SellingStatus><CurrentPrice currencyID="USD">73.0</CurrentPrice>
</SellingStatus></Item></GetItemResponse>"""


def test_scan_uses_read_calls_only(tmp_path, monkeypatch):
    calls = []

    def fake(call, inner, creds):
        calls.append(call)
        assert call in L.READ_CALLS
        return {"GetMyeBaySelling": ACTIVE_XML, "GetItem": ITEM_XML}[call]

    def no_subprocess(*a, **k):
        raise AssertionError("scan must not run a subprocess")
    monkeypatch.setattr(L.subprocess, "run", no_subprocess)
    ls = L.fetch_active("default", None, call=fake)
    assert ls[0].start == date(2026, 9, 1) and ls[0].price == D("73.0")
    assert ls[0].title == "Cat & Dog"
    s = _shoot(tmp_path, tiers=("40", "60", "90"))
    ctx = _ctx(s, floors=lambda l: (lambda it: (it["auto_decline"], it["auto_accept"]))(
        L.fetch_item(l.item_id, None, call=fake)))
    rows = L.scan_rows(ls, ctx)
    assert rows[0]["status"] == L.PROPOSED and rows[0]["new_auto_decline"]
    assert set(calls) == {"GetMyeBaySelling", "GetItem"}
    L.write_queue(rows, tmp_path / "out" / "queue.csv")
    html = L.review_html(rows, TODAY)
    assert "Cat &amp; Dog" in html and "yes / no" in html
    md = L.summary_md(rows, TODAY, stores_scanned=["default"], spot_seen={}, max_reprice=10)
    assert "PROPOSED 1" in md
