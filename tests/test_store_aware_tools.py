"""The account-facing tools under tools/ honour `--store` (GH #156 §2/§4).

Before #156 every one of these called `load_credentials()` with no arguments
and read/wrote the repo-root `listings_ledger.csv` / `sales_ledger.csv` /
`inventory_sheet.csv`, so a second store was only reachable through
$EBAYBIZ_STORE — and even then half of each tool (the files, the Browse
seller, the policy set) still pointed at the main store.

What these tests pin, for each tool:

  * the default store is unchanged — historic filenames, historic seller;
  * a named store uses ITS OWN files and ITS OWN credentials;
  * nothing assumes two stores — a three-store config works the same way.

No network, no real config: stores.REPO points at tmp_path, load_config is
patched, and every eBay call is replaced by a recorder.

Run:  pytest tests/test_store_aware_tools.py
"""
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import config as config_mod                                   # noqa: E402
import list_edit                                               # noqa: E402
import stores                                                  # noqa: E402

THREE = {
    "ebay": {
        "environment": "production",
        "production": {"fulfillment_policy_id": "MAIN-F",
                       "fulfillment_policy_id_us_only": "MAIN-US",
                       "return_policy_id": "MAIN-R"},
        "stores": {
            "junk": {"fulfillment_policy_id": "JUNK-F",
                     "fulfillment_policy_id_media": "JUNK-M"},
            "outlet": {"fulfillment_policy_id": "OUT-F"},
            "records": {},
        },
    },
    "store": {"display_name": "Main"},
    "storefronts": {
        "junk": {"seller_username": "junkhandle"},
        "outlet": {"seller_username": "outlethandle"},
        "records": {},
    },
}

LEDGER_FIELDS = ["sku", "status", "title", "price", "offer_id", "listing_id", "url",
                 "drafted_at", "synced_at", "published_at", "ended_at", "shipped_at",
                 "updated_at"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repo root with the three-store config loaded."""
    monkeypatch.setattr(stores, "REPO", tmp_path)
    for var in ("EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG", stores.STORE_ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: THREE)
    monkeypatch.setattr(list_edit, "load_config", lambda *a, **k: THREE)
    return tmp_path


def _write_ledger(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_FIELDS})


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------
# ledger_reconcile — per-store ledger, sales ledger, report, creds
# --------------------------------------------------------------------------
def _fake_ebay(monkeypatch, module, price="12.0"):
    """Every store's account holds one SKU 'aaaa1111', live at `price`."""
    seen = []

    def creds_for(store=None):
        seen.append(store)
        return SimpleNamespace(store=store or "default")

    monkeypatch.setattr(module, "load_credentials", creds_for)
    monkeypatch.setattr(module, "iter_inventory_items", lambda creds: [{"sku": "aaaa1111"}])
    monkeypatch.setattr(module, "get_offers_for_sku", lambda sku, creds: [{
        "offerId": f"O-{creds.store}", "status": "PUBLISHED",
        "pricingSummary": {"price": {"value": price}},
        "listing": {"listingId": "999", "listingStatus": "ACTIVE"}}])
    return seen


@pytest.mark.parametrize("store,suffix", [("default", ""), ("junk", "-junk"),
                                          ("outlet", "-outlet"), ("records", "-records")])
def test_reconcile_uses_the_named_stores_files_and_account(repo, monkeypatch, store, suffix):
    import ledger_reconcile as lr
    seen = _fake_ebay(monkeypatch, lr)
    mine = repo / f"listings_ledger{suffix}.csv"
    _write_ledger(mine, [{"sku": "aaaa1111", "status": "PUBLISHED", "price": "20.0"}])
    # A decoy ledger for some other store that must not be touched.
    other = repo / ("listings_ledger-junk.csv" if store != "junk" else "listings_ledger.csv")
    _write_ledger(other, [{"sku": "aaaa1111", "status": "PUBLISHED", "price": "20.0"}])
    before_other = other.read_bytes()

    assert lr.reconcile(store, apply=True) == 0

    assert seen == [store]                                       # its own creds
    assert _read(mine)[0]["price"] == "12.0"                     # its own ledger rewritten
    assert other.read_bytes() == before_other                    # nobody else's
    report = repo / f"ledger_reconcile_report{suffix}.json"
    assert json.loads(report.read_text(encoding="utf-8"))["drift"]
    backups = list(repo.glob(f"listings_ledger{suffix}.backup-*.csv"))
    assert len(backups) == 1


def test_reconcile_protects_sold_with_the_stores_own_sales_ledger(repo, monkeypatch):
    """A SOLD row survives because an order in ITS store's sales ledger backs it;
    the default store's sales ledger says nothing about junk's sales."""
    import ledger_reconcile as lr
    _fake_ebay(monkeypatch, lr)
    # eBay reads the junk SKU as a SYNCED (post-sale) offer
    monkeypatch.setattr(lr, "get_offers_for_sku", lambda sku, creds: [{
        "offerId": "O", "status": "UNPUBLISHED", "listing": {}}])
    _write_ledger(repo / "listings_ledger-junk.csv",
                  [{"sku": "aaaa1111", "status": "SOLD", "offer_id": "O"}])
    (repo / "sales_ledger-junk.csv").write_text("order_id,sku\n1-1,aaaa1111\n",
                                                 encoding="utf-8")
    lr.reconcile("junk", apply=False)
    doc = json.loads((repo / "ledger_reconcile_report-junk.json").read_text(encoding="utf-8"))
    assert doc["protected_sold"] == 1 and doc["unbacked_sold"] == []


def test_reconcile_apply_refuses_all_stores(repo, capsys):
    import ledger_reconcile as lr
    assert lr.main(["--apply", "--all-stores"]) == 2
    assert "--store" in capsys.readouterr().out


def test_reconcile_all_stores_reads_every_configured_store(repo, monkeypatch):
    import ledger_reconcile as lr
    ran = []
    monkeypatch.setattr(lr, "reconcile", lambda s, apply, prune: ran.append(s) or 0)
    assert lr.main(["--all-stores"]) == 0
    assert ran == ["default", "junk", "outlet", "records"]


# --------------------------------------------------------------------------
# live_audit — the Browse seller and the Sell account are the same store
# --------------------------------------------------------------------------
def test_live_audit_seller_comes_from_the_storefront(repo):
    import live_audit as la
    assert la.seller_for_store("junk") == "junkhandle"
    assert la.seller_for_store("outlet") == "outlethandle"


def test_live_audit_default_store_keeps_the_legacy_seller(repo):
    import live_audit as la
    assert la.seller_for_store("default") == "popsgames"


def test_live_audit_default_store_prefers_its_configured_username(repo, monkeypatch):
    import live_audit as la
    cfg = dict(THREE, store={"display_name": "Main", "seller_username": "mainhandle"})
    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: cfg)
    assert la.seller_for_store("default") == "mainhandle"


def test_live_audit_named_store_without_username_refuses(repo):
    """Falling back to popsgames here would diff junk's offers against the main
    store's Browse actives — the exact mismatch #156 §4 describes."""
    import live_audit as la
    with pytest.raises(SystemExit) as e:
        la.seller_for_store("records")
    assert "seller_username" in str(e.value)


def test_live_audit_passes_one_store_to_both_halves(repo, monkeypatch):
    import live_audit as la
    got = {}
    monkeypatch.setattr(la, "fetch_offers",
                        lambda store=None, verbose=True: got.setdefault("offers", store) and [])
    monkeypatch.setattr(la, "fetch_actives",
                        lambda seller, verbose=True: got.setdefault("seller", seller) and {})
    monkeypatch.setattr(la, "INVENTORY", repo / "inventory")
    (repo / "inventory").mkdir()
    assert la.main(["--store", "junk"]) == 0
    assert got == {"offers": "junk", "seller": "junkhandle"}


def test_live_audit_apply_writes_only_the_stores_ledger_and_drafts(repo, monkeypatch, tmp_path):
    import live_audit as la
    inv = repo / "inventory"
    for name, store_line in (("j", 'store: "junk"\n'), ("m", "")):
        (inv / name).mkdir(parents=True)
        (inv / name / "draft.md").write_text(
            f'---\ntitle: "{name}"\nprice: "10"\n{store_line}meta:\n'
            f'  ebay_inventory_sku: "{name}{name}{name}11111"\n---\n', encoding="utf-8")
    monkeypatch.setattr(la, "INVENTORY", inv)
    monkeypatch.setattr(la, "REPO", repo)

    junk_led = repo / "listings_ledger-junk.csv"
    main_led = repo / "listings_ledger.csv"
    _write_ledger(junk_led, [{"sku": "gone0001", "status": "PUBLISHED"}])
    _write_ledger(main_led, [{"sku": "gone0001", "status": "PUBLISHED"}])
    main_before = main_led.read_bytes()

    drafts = la.scan_drafts("junk", la.load_ledger("junk"))
    assert [d["dir"] for d in drafts] == ["inventory/j"]
    assert [d["dir"] for d in la.scan_drafts("default", la.load_ledger("default"))] \
        == ["inventory/m"]

    offers, actives = tmp_path / "o.json", tmp_path / "a.json"
    offers.write_text("[]", encoding="utf-8")
    actives.write_text("[]", encoding="utf-8")
    assert la.main(["--store", "junk", "--offers", str(offers),
                    "--actives", str(actives), "--apply"]) == 0
    assert _read(junk_led)[0]["status"] == "NEEDS_CHECK"
    assert main_led.read_bytes() == main_before


# --------------------------------------------------------------------------
# live_shipping_survey — "live policy" is the store's own configured set
# --------------------------------------------------------------------------
def test_live_policies_are_per_store(repo):
    import live_shipping_survey as lss
    assert lss.live_policies("default") == {"MAIN-F", "MAIN-US"}
    assert lss.live_policies("junk") == {"JUNK-F", "JUNK-M"}
    assert lss.live_policies("outlet") == {"OUT-F"}
    assert lss.live_policies("records") == set()


def test_dead_only_refuses_a_store_with_no_policies(repo, monkeypatch, capsys):
    import live_shipping_survey as lss
    monkeypatch.setattr(lss.ec, "load_credentials", lambda store=None: SimpleNamespace())
    (repo / ".offer_policy_survey-records.json").write_text("[]", encoding="utf-8")
    assert lss.main(["--store", "records", "--dead-only"]) == 2
    assert "ebay.stores.records.fulfillment_policy_id" in capsys.readouterr().err


def test_survey_file_defaults_per_store(repo):
    import live_shipping_survey as lss
    assert lss.survey_path("junk") == repo / ".offer_policy_survey-junk.json"
    assert lss.survey_path("default") == repo / ".offer_policy_survey.json"
    (repo / ".offer_policy_survey2.json").write_text("[]", encoding="utf-8")
    assert lss.survey_path("default") == repo / ".offer_policy_survey2.json"   # legacy
    assert lss.survey_path("junk") == repo / ".offer_policy_survey-junk.json"


# --------------------------------------------------------------------------
# ebay_sheet / inventory_sync — per-store default outputs
# --------------------------------------------------------------------------
@pytest.mark.parametrize("store,suffix", [(None, ""), ("junk", "-junk"), ("outlet", "-outlet")])
def test_ebay_sheet_writes_the_stores_own_sheet(repo, monkeypatch, store, suffix):
    import ebay_sheet
    built = []
    monkeypatch.setattr(ebay_sheet, "build", lambda s=None: built.append(s) or [])
    ebay_sheet.main(["--store", store] if store else [])
    assert built == [store or "default"]
    assert (repo / f"inventory_sheet{suffix}.csv").exists()
    assert (repo / f"inventory_sheet{suffix}.json").exists()


def test_inventory_sync_indexes_only_the_stores_drafts(repo, monkeypatch):
    import inventory_sync
    monkeypatch.setattr(inventory_sync, "REPO", repo)
    inv = repo / "inventory"
    for name, sku, store_line in (("j", "abcdef01", 'store: "junk"\n'),
                                  ("m", "abcdef02", "")):
        (inv / name).mkdir(parents=True)
        (inv / name / "draft.md").write_text(
            f'---\ntitle: "x"\n{store_line}meta:\n  ebay_inventory_sku: "{sku}"\n---\n',
            encoding="utf-8")
    assert set(inventory_sync.local_index("junk")) == {"abcdef01"}
    assert set(inventory_sync.local_index("default")) == {"abcdef02"}
    assert set(inventory_sync.local_index()) == {"abcdef01", "abcdef02"}


# --------------------------------------------------------------------------
# policy_sweep — per-store cache, and the republish carries the store's creds
# --------------------------------------------------------------------------
def test_policy_sweep_republish_uses_the_named_stores_creds(monkeypatch):
    import policy_sweep
    calls = []

    def fake(method, path, body=None, marketplace=None, creds=None):
        calls.append((method, path, creds))
        return {"listingPolicies": {"returnPolicyId": "OLD"}} if method == "GET" else {}

    monkeypatch.setattr(policy_sweep, "api_send", fake)
    junk = SimpleNamespace(store="junk")
    policy_sweep.repair({"offerId": "1", "status": "PUBLISHED"}, "NEW", dry=False, creds=junk)
    assert [c[2] for c in calls] == [junk, junk, junk]
    assert calls[-1][1].endswith("/publish")


# --------------------------------------------------------------------------
# promote — every Marketing API call is bound to the one store
# --------------------------------------------------------------------------
def test_promote_threads_creds_into_every_call(monkeypatch):
    import ebay_client
    import promote
    seen = []
    monkeypatch.setattr(ebay_client, "api_send",
                        lambda *a, creds=None, **k: seen.append(creds) or {})
    junk = SimpleNamespace(store="junk")
    promote.add_ads("C1", ["1"], True, "G", creds=junk)
    promote.set_bidding("C1", "DYNAMIC", True, creds=junk)
    promote.campaigns_and_ads(junk)
    assert seen and all(c is junk for c in seen)


def test_promote_live_listings_reads_the_stores_sheet(repo):
    import promote
    (repo / "inventory_sheet-junk.csv").write_text(
        "sku,listing_id,live,title,price,category_top\nx,55,yes,Lot,9.99,Toys\n",
        encoding="utf-8")
    assert list(promote.live_listings("junk")) == ["55"]
    with pytest.raises(SystemExit):
        promote.live_listings("outlet")


# --------------------------------------------------------------------------
# price_audit — listing age comes from the store's own ledger
# --------------------------------------------------------------------------
def test_price_audit_dates_from_the_stores_ledger(repo, monkeypatch):
    import price_audit
    monkeypatch.setattr(price_audit, "REPO", repo)
    (repo / "inventory" / "j").mkdir(parents=True)
    (repo / "inventory" / "j" / "draft.md").write_text(
        "notes: Recommended $10\n", encoding="utf-8")
    _write_ledger(repo / "listings_ledger-junk.csv",
                  [{"sku": "s1", "published_at": "2020-01-01T00:00:00Z"}])
    audit = [{"state": "LIVE", "dir": "inventory/j", "sku": "s1", "live_price": "30"}]
    assert price_audit.scan(audit, 30, "junk")[0]["flag"] == "above-recommended"
    # The default store has no ledger row for s1 -> no age -> not flagged.
    assert price_audit.scan(audit, 30, "default") == []


# --------------------------------------------------------------------------
# session_observer — store-neutral: the default store's ledger, no config read
# --------------------------------------------------------------------------
def test_session_observer_ledger_path_is_the_default_stores(repo, monkeypatch):
    from tools import session_observer as so

    def boom(*a, **k):
        raise AssertionError("observe must not need config.yaml")

    monkeypatch.setattr(config_mod, "load_config", boom)
    assert so._ledger_path() == repo / "listings_ledger.csv"
    assert so._ledger_path("junk") == repo / "listings_ledger-junk.csv"


# --------------------------------------------------------------------------
# list_edit_group — a group draft's own `store:` picks the account
# --------------------------------------------------------------------------
def test_group_draft_store_field_picks_the_account(tmp_path, monkeypatch):
    import list_edit_group as g
    monkeypatch.setenv(stores.STORE_ENV_VAR, "outlet")      # ambient: ignored
    junk = tmp_path / "junk_group.md"
    junk.write_text('---\ntitle: "x"\nstore: junk\n---\nbody\n', encoding="utf-8")
    plain = tmp_path / "plain_group.md"
    plain.write_text('---\ntitle: "x"\n---\nbody\n', encoding="utf-8")
    assert g._group_store(junk, None) == "junk"
    assert g._group_store(plain, None) == "default"
    assert g._group_store(junk, "records") == "records"      # --store wins
