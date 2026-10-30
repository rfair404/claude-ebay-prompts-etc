#!/usr/bin/env python3
"""The v3 formats, pinned.

Every artefact this pipeline passes between phases has a shape that something
downstream depends on: the draft's frontmatter is read by the validator and the
eBay sync, the PREP manifest is read by the review page and the gate, the ledger
is read by the audit tools, the review card is read by a human deciding whether
to publish. All of them carry a version stamp. None of them had anything that
noticed when the shape changed underneath the stamp.

That is what this file is. It is not testing behaviour — it is a lock. Each test
holds the exact field set of one format. Adding, renaming or dropping a field
fails here, loudly, in the same commit that does it.

WHEN A TEST HERE FAILS, that is the format changing. Two legitimate answers:

  * the change is additive and safe — add the field to the lock below, in the
    same commit, so the next reader sees when it appeared;
  * the change breaks readers — bump the format's version stamp
    (`template_version`, `MANIFEST_VERSION`), teach the reader both shapes, and
    update the lock.

The wrong answer is to relax an assertion so it stops noticing.

Run:  python tests/test_formats.py
  or: pytest tests/test_formats.py
"""
import base64
import csv
import hashlib
import io
import json
import shlex
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import pytest                                                # noqa: E402
import yaml                                                  # noqa: E402


# ---------------------------------------------------------------------------
# the draft — templates/listing-v1.md, stamped template_version: v1
# ---------------------------------------------------------------------------

TEMPLATE = ROOT / "templates" / "listing-v1.md"

DRAFT_TOP = {
    "_field_constraints", "best_offer", "category_id", "category_path",
    "condition", "condition_description", "cost_of_goods", "format",
    "item_specifics", "meta", "photos", "price", "promoted", "quantity",
    "returns_policy_id", "shipping", "store", "template_version", "title",
}
DRAFT_META = {
    "drafted_at", "ebay_inventory_sku", "ebay_offer_id", "item_id",
    "last_synced", "notes", "shoot_dir",
}
DRAFT_SPECIFICS = {
    "brand", "character_family", "collection", "color", "country_of_origin",
    "department", "extra", "finish", "material", "occasion", "pattern", "size",
    "style", "subject", "theme", "time_period_manufactured", "type", "upc",
}
DRAFT_SHIPPING = {
    "domestic_shipping_type", "free_shipping", "fulfillment_mode",
    "handling_time_days", "international", "item_location_zip", "local_pickup",
    "package_in", "primary_service", "weight",
    # Added 2026-08-27, additive and safe: absent means false, so drafts
    # written before it read identically. Routes US-export-restricted items
    # (firearm parts, body armor, night vision) to the US-only fulfillment
    # policy. Normally set by lib/us_only.py, not by hand.
    "us_only",
}
# The limits the eBay form actually enforces. A wrong number here is a publish
# rejection or a silently truncated listing, so they are pinned by value.
DRAFT_LIMITS = {
    "title": 80, "price": 13, "quantity": 5, "condition_description": 1000,
    "item_specifics.type": 65, "item_specifics.brand": 65, "item_specifics.upc": 20,
    "shipping.weight.major_lb": 3, "shipping.weight.minor_oz": 2,
    "shipping.package_in.length": 5,
}


def _template() -> dict:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    fm = tpl[tpl.index("---\n") + 4: tpl.index("---\n\n# Description")]
    return yaml.safe_load(fm)


def test_draft_template_is_v1_and_its_fields_are_fixed():
    d = _template()
    assert d["template_version"] == "v1", d["template_version"]
    assert set(d) == DRAFT_TOP, set(d) ^ DRAFT_TOP
    assert set(d["meta"]) == DRAFT_META, set(d["meta"]) ^ DRAFT_META
    assert set(d["item_specifics"]) == DRAFT_SPECIFICS, \
        set(d["item_specifics"]) ^ DRAFT_SPECIFICS
    assert set(d["shipping"]) == DRAFT_SHIPPING, set(d["shipping"]) ^ DRAFT_SHIPPING


def test_the_field_limits_are_the_ones_ebay_enforces():
    """These are not style choices. Over the cap, eBay rejects or truncates."""
    fc = _template()["_field_constraints"]
    for path, cap in DRAFT_LIMITS.items():
        assert path in fc, f"{path} lost its constraint"
        assert fc[path].get("max_len") == cap, (path, fc[path].get("max_len"), cap)
    for req in ("title", "price", "quantity", "item_specifics.type"):
        assert fc[req].get("required") is True, f"{req} stopped being required"


# Drafts written before the constraints block existed stamp `template_version:
# v1` and carry none of it ("legacy"); some carry a PARTIAL block ("partial":
# rules dropped, none altered). Both are under-enforced rather than
# mis-enforced, which is a smaller problem than checking the wrong limit, so
# the audit counts them and fails only on a block that disagrees.


def _audit_drafts(drafts):
    """Drafts, checked against the template they claim.

    Two different assertions, because there are two different situations:

      * a draft that CARRIES a constraints block must match the template
        exactly. There is no tolerance here — a drifted block means the
        validator is enforcing limits that eBay does not, or missing ones it
        does.
      * a draft that carries NONE is old, from before the block existed. Those
        are counted (the caller decides what a count means), not fixed.
    """
    tpl_fc = _template()["_field_constraints"]
    matched = legacy = partial = 0
    for p in drafts:
        txt = p.read_text(encoding="utf-8", errors="replace")
        if "template_version: v1" not in txt:
            continue
        try:
            d = yaml.safe_load(txt.split("---\n", 2)[1])
        except (yaml.YAMLError, IndexError):
            raise AssertionError(f"{p}: frontmatter no longer parses")
        if not isinstance(d, dict):
            raise AssertionError(f"{p}: frontmatter is not a mapping")
        if "_field_constraints" not in d:
            legacy += 1
            continue
        fc = d["_field_constraints"]
        # A rule that DISAGREES with the template, or one the template has never
        # heard of, is the dangerous case: the validator would enforce a limit
        # eBay does not have, or wave through one it does. No tolerance for it.
        for rule, spec in fc.items():
            assert rule in tpl_fc, f"{p}: unknown constraint {rule!r}"
            assert spec == tpl_fc[rule], \
                f"{p}: constraint {rule!r} disagrees with the template: " \
                f"{spec} vs {tpl_fc[rule]}"
        if set(fc) != set(tpl_fc):
            partial += 1                      # under-enforced; counted below
        assert d.get("title") is not None, f"{p}: no title"
        matched += 1

    return matched, legacy, partial


def _fixture_draft(tmp_path, name, constraints=None, version="v1"):
    d = tmp_path / name
    d.mkdir()
    fm = {"template_version": version, "title": "Synthetic Widget"}
    if constraints is not None:
        fm["_field_constraints"] = constraints
    (d / "draft.md").write_text("---\n" + yaml.safe_dump(fm) + "---\nbody\n",
                                encoding="utf-8")
    return d / "draft.md"


def test_the_draft_audit_accepts_the_templates_own_constraints_and_counts_legacy(tmp_path):
    """The audit used to walk Russell's real inventory/, so its pass/fail
    depended on his data (50 legacy drafts there against a 'ceiling' of 31) and
    it did nothing in CI. It now runs on drafts built here.

    A draft carrying the template's block matches; one carrying none is counted
    as legacy; one carrying a subset is counted as partial."""
    tpl = _template()["_field_constraints"]
    some = dict(list(tpl.items())[:2])
    drafts = [_fixture_draft(tmp_path, "full", tpl),
              _fixture_draft(tmp_path, "partial", some),
              _fixture_draft(tmp_path, "legacy"),
              _fixture_draft(tmp_path, "other-version", version="v2")]
    assert _audit_drafts(drafts) == (2, 1, 1)


@pytest.mark.parametrize("change, message", [
    (lambda fc: {**fc, "title": {**fc["title"], "max_len": 200}}, "disagrees with the template"),
    (lambda fc: {**fc, "invented_rule": {"required": True}}, "unknown constraint"),
], ids=["a-limit-eBay-does-not-have", "a-rule-the-template-never-heard-of"])
def test_the_draft_audit_rejects_a_drifted_constraints_block(tmp_path, change, message):
    """A wrong limit is a rejected or truncated listing; there is no tolerance
    for it. Mutate one rule and the audit must name the problem."""
    bad = _fixture_draft(tmp_path, "drifted", change(_template()["_field_constraints"]))
    with pytest.raises(AssertionError, match=message):
        _audit_drafts([bad])


# ---------------------------------------------------------------------------
# the PREP manifest — .prep/prep.json, stamped version: 1
# ---------------------------------------------------------------------------

MANIFEST_TOP = {"version", "shoot", "created", "updated", "approved",
                "approved_at", "settings", "photos"}
PHOTO_CORE = {"src_sha256", "src_size", "orientation", "status"}
ORIENTATION_KEYS = {
    "applied", "exif_angle", "exif_tag", "needs_ask", "notes", "osd_angle",
    "osd_conf", "osd_note", "source", "subject_angle", "vision_angle",
}
STAGE_ORDER = ("orientation", "crop", "color")


def test_manifest_version_and_stage_order_are_fixed():
    from lib.photo_prep import prep as P
    from lib.photo_prep import stages as S

    assert P.MANIFEST_VERSION == 1, P.MANIFEST_VERSION
    # Order is the contract, not just membership: the gate walks it, and every
    # stage's meaning depends on what ran before it.
    assert S.STAGES == STAGE_ORDER, S.STAGES


def test_a_real_manifest_carries_the_fields_readers_depend_on():
    found = sorted(ROOT.glob("inventory/**/.prep/prep.json"))
    if not found:
        return
    checked = 0
    for p in found[:25]:
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("version") != 1:
            continue
        assert MANIFEST_TOP <= set(m), f"{p}: missing {MANIFEST_TOP - set(m)}"
        for name, rec in list((m.get("photos") or {}).items())[:5]:
            assert PHOTO_CORE <= set(rec), \
                f"{p}::{name}: missing {PHOTO_CORE - set(rec)}"
            assert ORIENTATION_KEYS <= set(rec["orientation"]), \
                f"{p}::{name}: orientation missing " \
                f"{ORIENTATION_KEYS - set(rec['orientation'])}"
        checked += 1
    assert checked, "no version-1 manifests found to check"


# ---------------------------------------------------------------------------
# the ledger — listings_ledger.csv
# ---------------------------------------------------------------------------

LEDGER_COLUMNS = ["sku", "status", "title", "price", "offer_id", "listing_id",
                  "url", "drafted_at", "synced_at", "published_at", "ended_at",
                  "shipped_at", "updated_at"]


def test_ledger_columns_and_their_order():
    p = ROOT / "listings_ledger.csv"
    if not p.exists():
        return
    with p.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert header == LEDGER_COLUMNS, header


LEDGER_STATUSES = {"DRAFTED", "SYNCED", "PUBLISHED", "SOLD", "SHIPPED", "ENDED",
                   "DELETED", "OUT_OF_STOCK", "VOIDED", ""}


def _unknown_ledger_statuses(p):
    seen = set()
    with p.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            seen.add((row.get("status") or "").strip())
    return sorted(seen - LEDGER_STATUSES)


def test_ledger_statuses_stay_in_the_known_set():
    """A status nobody recognises silently drops rows out of every report."""
    p = ROOT / "listings_ledger.csv"
    if not p.exists():
        return
    unknown = _unknown_ledger_statuses(p)
    assert not unknown, f"unknown ledger statuses: {unknown}"


def test_ledger_status_lock_runs_without_a_real_ledger():
    """CI has no listings_ledger.csv, so the test above skips there. This one
    pins the set on a fixture row, so a new status (VOIDED, from --void) can't
    pass CI and then fail on Russell's machine."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "listings_ledger.csv"
        with p.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(LEDGER_COLUMNS)
            w.writerow(["abc12345", "VOIDED"] + [""] * (len(LEDGER_COLUMNS) - 2))
        unknown = _unknown_ledger_statuses(p)
    assert not unknown, f"unknown ledger statuses: {unknown}"


# ---------------------------------------------------------------------------
# the REVIEW card — the surface a human approves a publish on
# ---------------------------------------------------------------------------

CARD_SECTIONS = [
    "━━ REVIEW:",
    "Title:",
    "Price:",
    "Condition:",
    "Quantity:",
    "Fulfillment:",
    "Preflight",
    "Comps (open to verify):",
    "Condition detail:",
    "Context (estate background):",
    "Final photos",
    "⚠ Needs review / manual intervention:",
    "→ Approve publishes this LIVE",
]


# ---- a fake eBay at urlopen -------------------------------------------------
# The tests below run the real list_edit code, the real ebay_client request
# building and the real CLI. Only the wire is faked.

class _Wire:
    """A response object urlopen's `with` block can use."""

    def __init__(self, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self._buf = io.BytesIO(raw)
        self.headers = {}

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeEbay:
    """Records every request and answers just enough of the Sell API for a
    sync-then-publish to complete. Unknown requests answer {}.
    `requests` holds (method, path-with-query, json-body-or-None, headers)."""

    def __init__(self):
        self.requests = []
        self.offer_price = None
        self.sku = None

    def urlopen(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        method, path = req.get_method(), url.path
        raw = req.data or b""
        try:
            body = json.loads(raw) if raw[:1] in (b"{", b"[") else None
        except ValueError:
            body = None
        self.requests.append((method, path + (("?" + url.query) if url.query else ""),
                              body, {k.lower(): v for k, v in req.header_items()}))
        if path.endswith("/oauth2/token"):
            return _Wire({"access_token": "fake-token", "expires_in": 7200})
        if path.endswith("/ws/api.dll"):                         # Trading GetItem
            return _Wire(b"<GetItemResponse><Ack>Success</Ack></GetItemResponse>")
        if "create_image_from_file" in path:
            return _Wire({"imageUrl": "https://i.ebayimg.example/fake.jpg"})
        if path.endswith("/fulfillment_policy"):
            return _Wire({"fulfillmentPolicies": [
                {"fulfillmentPolicyId": "F1", "name": "Ground",
                 "shippingOptions": [{"optionType": "DOMESTIC", "costType": "FLAT_RATE",
                                      "shippingServices": [
                                          {"shippingServiceCode": "USPSGroundAdvantage"}]}]}]})
        if method == "PUT" and "/inventory_item/" in path:
            self.sku = path.rsplit("/", 1)[1]
            return _Wire(b"")
        if method == "POST" and path.endswith("/sell/inventory/v1/offer"):
            self.offer_price = body["pricingSummary"]["price"]["value"]
            return _Wire({"offerId": "OFFER-1"})
        if method == "GET" and path.endswith("/offer/OFFER-1"):
            return _Wire({"status": "UNPUBLISHED", "sku": self.sku,
                          "pricingSummary": {"price": {"value": self.offer_price}}})
        if method == "POST" and path.endswith("/offer/OFFER-1/publish"):
            return _Wire({"listingId": "110011"})
        return _Wire({})

    def writes(self):
        """Every request that changes something on eBay (not the token grant)."""
        return [(m, p, b) for m, p, b, _ in self.requests
                if m in ("POST", "PUT", "DELETE") and "/oauth2/token" not in p]

    def publishes(self):
        return [r for r in self.writes() if r[1].endswith("/publish")]


# Synthetic account data. Nothing here is real.
_FAKE_CONFIG = """\
ebay:
  environment: sandbox
  sandbox:
    app_id: DEFAULT-APP
    cert_id: default-cert
    dev_id: default-dev
    user_refresh_token: default-refresh
  fulfillment_policy_id: F1
  payment_policy_id: P1
  return_policy_id: R1
  merchant_location_key: LOC1
  stores:
    junk:
      environment: sandbox
      app_id: JUNK-APP
      cert_id: junk-cert
      dev_id: junk-dev
      user_refresh_token: junk-refresh
      fulfillment_policy_id: F1
      payment_policy_id: P1
      return_policy_id: R1
      merchant_location_key: LOC2
"""

_DRAFT = """\
---
title: "Vintage Widget MPN-100"
price: 24.99
quantity: 1
condition: "USED_GOOD"
condition_description: "Light shelf wear."
category_id: "12345"
item_specifics:
  type: "Widget"
photos:
  - "listing/a.jpg"
shipping:
  fulfillment_mode: SHIP
meta:
  notes: ""
---
A well-loved vintage widget from our collection, kept in a dry room and
gently cared for over the years.
"""

_PRICE_TXT = ('  B · Browser   : RAN — query "widget" — best_match n=9 + price_high n=9'
              ' — saved 2 JSONs — price_stats n_kept=8, conf=good\n')


@pytest.fixture
def ebay(tmp_path, monkeypatch):
    """A synthetic shoot, a throwaway config and ledger, and a fake eBay."""
    import config
    import ebay_client
    import list_edit

    cfg = tmp_path / "config.yaml"
    cfg.write_text(_FAKE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("EBAYBIZ_CONFIG", str(cfg))
    monkeypatch.delenv("EBAYBIZ_STORE", raising=False)
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(tmp_path / "ledger.csv"))
    monkeypatch.setenv("EBAYBIZ_GDRIVE_AUTO", "0")
    monkeypatch.setattr(config, "_cached_config", None)       # re-read; restored after
    # token caches are module state: start empty and put the old ones back
    for name in ("_app_caches", "_user_caches", "_user_scopes_by_store"):
        monkeypatch.setattr(ebay_client, name, {})
    fake = FakeEbay()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)

    shoot = tmp_path / "shoot"
    (shoot / "listing").mkdir(parents=True)
    (shoot / "listing" / "a.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")
    (shoot / "draft.md").write_text(_DRAFT, encoding="utf-8")
    # a sold-price pull on record, so the price gate lets the draft through
    (shoot / "price.txt").write_text(_PRICE_TXT, encoding="utf-8")
    (shoot / "apify_run_20261024T000000Z_browse-best_match-p1-20261023T200000.json"
     ).write_text("{}", encoding="utf-8")
    fake.shoot = shoot
    fake.list_edit = list_edit
    return fake


def _approve_prep(shoot):
    """A PREP manifest, signed off, for one source frame rendered to
    listing/a.jpg, with both hashes matching what is on disk."""
    src = shoot / "IMG_0001.jpg"
    src.write_bytes(b"\xff\xd8\xff fake original")
    out = shoot / "listing" / "a.jpg"
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()[:16]   # PREP keeps 16
    (shoot / ".prep").mkdir(exist_ok=True)
    (shoot / ".prep" / "prep.json").write_text(json.dumps({
        "version": 1, "approved": True, "chosen_preset": "crisp",
        "photos": {"IMG_0001.jpg": {"src_sha256": sha(src), "output": "listing/a.jpg",
                                    "out_sha256": sha(out)}},
    }), encoding="utf-8")


@pytest.mark.parametrize("label", CARD_SECTIONS)
def test_the_review_card_still_says_all_of_it(ebay, label):
    """Anything dropped from this list is something a human stops being shown
    at the moment they authorise a live listing. `Final photos` is here because
    a count and a hero filename were not enough — they could not reveal a frame
    that never went through PREP.

    Built from a real draft through build_review_card, so a label that only
    survives in a comment or a dead branch no longer counts."""
    card, path = ebay.list_edit.build_review_card(ebay.shoot / "draft.md")
    assert label in card, f"the review card no longer shows: {label!r}\n{card}"
    assert Path(path).read_text(encoding="utf-8").strip() == card.strip(), \
        "the card on disk must be the card that was shown"


def test_the_review_card_shows_this_listings_own_values(ebay):
    """The labels above could all be present and empty. These are the values a
    human actually approves: what is on the card must be what is in the draft."""
    card, _ = ebay.list_edit.build_review_card(ebay.shoot / "draft.md")
    assert 'Title:     "Vintage Widget MPN-100"  [22/80]' in card
    assert "Price:     $24.99  ·  Best Offer: off" in card
    assert "Approve publishes this LIVE at $24.99" in card
    assert "Condition: USED_GOOD" in card
    assert "Light shelf wear." in card
    assert "hero  listing/a.jpg" in card


def test_a_clean_card_says_so_and_a_photo_gap_withdraws_it(ebay):
    """ALL CLEAR is an AND of every section. A frame edited after PREP approved
    it must take the banner away and say why."""
    _approve_prep(ebay.shoot)
    card, _ = ebay.list_edit.build_review_card(ebay.shoot / "draft.md")
    assert "ALL CLEAR" in card.splitlines()[1], card

    (ebay.shoot / "listing" / "a.jpg").write_bytes(b"\xff\xd8\xff edited later")
    card, _ = ebay.list_edit.build_review_card(ebay.shoot / "draft.md")
    assert "ALL CLEAR" not in card
    assert "CHANGED SINCE PREP APPROVED IT" in card


def _published_command(card: str) -> list[str]:
    """The argv the card tells the operator to run to publish, as typed."""
    line = next(ln for ln in card.splitlines() if "list_edit.py --list" in ln)
    # posix=False: POSIX mode eats the backslashes in a Windows path. It keeps
    # the surrounding quotes, so strip them.
    argv = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in shlex.split(line.strip(), posix=False)]
    assert argv[:2] == ["python", "lib/list_edit.py"], argv
    return argv[2:]


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["list_edit.py", *argv])
    import list_edit
    try:
        list_edit._cli()
    except SystemExit as e:                               # a refusal exits non-zero
        return e.code
    return 0


def test_the_publish_command_on_the_card_publishes_and_only_with_confirm(ebay, monkeypatch, capsys):
    """The card tells the operator exactly what publishes. Run that command, as
    printed, against a fake eBay: it must publish once, at the price the card
    showed. Then run it with --confirm taken off: it may sync, but nothing may
    be published.

    GH #147: the command may also carry `--store <name>` before --confirm, so
    the check is on behaviour, not on where the flags sit."""
    _approve_prep(ebay.shoot)
    card, _ = ebay.list_edit.build_review_card(ebay.shoot / "draft.md")
    argv = _published_command(card)
    assert "--confirm" in argv, f"the card's publish command lost --confirm: {argv}"

    # without --confirm: a dry run, nothing goes live
    assert _run_cli(monkeypatch, [a for a in argv if a != "--confirm"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert ebay.publishes() == [], "a publish reached eBay without --confirm"
    assert ebay.writes(), "the dry run should still have synced the offer"

    # as printed: publishes exactly once, at the price the card showed
    assert _run_cli(monkeypatch, argv) == 0
    assert "[LIVE] Published offer OFFER-1 -> listing 110011" in capsys.readouterr().out
    assert len(ebay.publishes()) == 1
    assert "$24.99" in card and ebay.offer_price == "24.99", \
        "the price on the card is not the price sent to eBay"


def test_the_card_command_refuses_photos_that_never_went_through_prep(ebay, monkeypatch):
    """The card flags an un-prepped frame, and the command it prints must back
    that up: with --confirm and no approved PREP, nothing reaches eBay at all."""
    card, _ = ebay.list_edit.build_review_card(ebay.shoot / "draft.md")
    assert "[!] no PREP manifest" in card
    code = _run_cli(monkeypatch, _published_command(card))
    assert isinstance(code, str) and "PREP GATE" in code, code
    assert ebay.writes() == [] and ebay.publishes() == []


def test_the_card_for_another_store_publishes_to_that_store_only(ebay, monkeypatch):
    """A card made for the junk store carries `--store junk`. Following it must
    authenticate as junk, never as the default account."""
    import ebay_client
    _approve_prep(ebay.shoot)
    creds = ebay_client.load_credentials(store="junk")
    card, _ = ebay.list_edit.build_review_card(ebay.shoot / "draft.md", creds=creds)
    argv = _published_command(card)
    assert argv[argv.index("--store") + 1] == "junk", argv
    assert argv[-1] == "--confirm"

    assert _run_cli(monkeypatch, argv) == 0
    assert len(ebay.publishes()) == 1
    grants = [h["authorization"] for m, p, b, h in ebay.requests if p.endswith("/oauth2/token")]
    junk = "Basic " + base64.b64encode(b"JUNK-APP:junk-cert").decode()
    assert grants and set(grants) == {junk}, \
        "publishing from a junk-store card used another account's credentials"


if __name__ == "__main__":
    import inspect
    # tests that take fixtures need pytest; this runner can't supply them
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)
           and not inspect.signature(v).parameters]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                               # noqa: BLE001
            bad += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
