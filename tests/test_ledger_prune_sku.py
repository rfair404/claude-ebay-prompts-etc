"""`ledger_reconcile.py --prune-sku <sku> --store <s>`: remove one stale row.

An item moved to another store leaves a DRAFTED row behind in its first
store's ledger. `--prune-unknown` would remove it, and with it every draft that
was never synced and is still being worked on. `--prune-sku` removes exactly
the named row, and only once the SKU is shown to belong to another store and
to have no offer or sale under this one. Dry run unless --apply.

Drives the real CLI (main) against tmp ledgers and a fake eBay per account.

Run:  pytest tests/test_ledger_prune_sku.py
"""
import csv
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import config as config_mod                                   # noqa: E402
import ledger_reconcile as lr                                  # noqa: E402
import stores                                                  # noqa: E402

CFG = {"ebay": {"stores": {"junk": {}}}, "storefronts": {"junk": {}}}
FIELDS = lr.FIELDS

STALE = {"sku": "98d244c6", "status": "DRAFTED", "title": "March 1990 Playboy",
         "price": "83.42", "drafted_at": "2026-10-22T10:31:35Z"}
KEEP = {"sku": "ed901dbe", "status": "DRAFTED", "title": "Lot of 4 Wood Bowls",
        "price": "47.99", "drafted_at": "2026-10-23T12:40:39Z"}
LIVE = {"sku": "aaaa1111", "status": "PUBLISHED", "title": "Live thing",
        "price": "20.0", "offer_id": "O-1", "listing_id": "111"}
MOVED = {"sku": "98d244c6", "status": "PUBLISHED", "title": "March 1990 Playboy",
         "price": "35.0", "offer_id": "324892353011", "listing_id": "880046873074"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, "REPO", tmp_path)
    for var in ("EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG", stores.STORE_ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: CFG)
    monkeypatch.setattr(lr, "ledger_changed", lambda path: None)
    return tmp_path


@pytest.fixture
def ebay(monkeypatch):
    """offers[store][sku] -> list of offers; anything absent is a 404 (no item).
    `calls` records (store, sku) for every lookup."""
    state = SimpleNamespace(offers={"default": {}, "junk": {}}, calls=[], fail=None)

    def creds(store=None):
        return SimpleNamespace(store=store or "default")

    def offers(sku, c):
        state.calls.append((c.store, sku))
        if state.fail:
            raise lr.EbayAPIError(500, state.fail)
        if sku not in state.offers[c.store]:
            raise lr.EbayAPIError(404, "no inventory item")
        return state.offers[c.store][sku]

    monkeypatch.setattr(lr, "load_credentials", creds)
    monkeypatch.setattr(lr, "get_offers_for_sku", offers)
    return state


def _write(path, rows, eol="\r\n"):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator=eol)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _skus(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r["sku"] for r in csv.DictReader(f)]


def _run(*argv):
    return lr.main(list(argv))


def test_dry_run_changes_nothing_then_apply_removes_only_that_row(repo, ebay, capsys):
    main = repo / "listings_ledger.csv"
    _write(main, [LIVE, STALE, KEEP])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    before = main.read_bytes()

    assert _run("--prune-sku", "98d244c6", "--store", "default") == 0
    out = capsys.readouterr().out
    assert "[DRY RUN]" in out and "880046873074" in out
    assert main.read_bytes() == before
    assert not list(repo.glob("listings_ledger.backup-*.csv"))

    assert _run("--prune-sku", "98d244c6", "--store", "default", "--apply") == 0
    assert "[OK] pruned 98d244c6" in capsys.readouterr().out
    assert _skus(main) == ["aaaa1111", "ed901dbe"]
    # every other line is byte-for-byte what it was
    assert main.read_bytes().splitlines() == \
        [ln for ln in before.splitlines() if not ln.startswith(b"98d244c6,")]
    backups = list(repo.glob("listings_ledger.backup-*.csv"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    # the other store's ledger is untouched
    assert _skus(repo / "listings_ledger-junk.csv") == ["98d244c6"]


def test_lf_ledger_stays_lf(repo, ebay, capsys):
    main = repo / "listings_ledger.csv"
    _write(main, [STALE, KEEP], eol="\n")
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    assert _run("--prune-sku", "98d244c6", "--store", "default", "--apply") == 0
    assert b"\r" not in main.read_bytes()
    assert _skus(main) == ["ed901dbe"]


def test_other_stores_ebay_account_is_enough_evidence(repo, ebay, capsys):
    main = repo / "listings_ledger.csv"
    _write(main, [STALE, KEEP])
    ebay.offers["junk"]["98d244c6"] = [{"offerId": "324892353011",
                                        "listing": {"listingId": "880046873074"}}]
    assert _run("--prune-sku", "98d244c6", "--store", "default", "--apply") == 0
    assert "junk account" in capsys.readouterr().out
    assert _skus(main) == ["ed901dbe"]


def _refused(repo, capsys, *argv, ledger="listings_ledger.csv"):
    path = repo / ledger
    before = path.read_bytes()
    code = _run(*argv, "--apply")
    out = capsys.readouterr().out
    assert code != 0
    assert "[OK]" not in out
    assert path.read_bytes() == before
    assert not list(repo.glob("listings_ledger*.backup-*.csv"))
    return out


def test_refuses_a_draft_no_other_store_has(repo, ebay, capsys):
    # ed901dbe: DRAFTED, never synced, still being worked on. --prune-unknown
    # would drop it; --prune-sku must not.
    _write(repo / "listings_ledger.csv", [STALE, KEEP])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    out = _refused(repo, capsys, "--prune-sku", "ed901dbe", "--store", "default")
    assert "no other store" in out


def test_a_voided_row_elsewhere_is_not_evidence(repo, ebay, capsys):
    _write(repo / "listings_ledger.csv", [STALE])
    _write(repo / "listings_ledger-junk.csv", [dict(STALE, status="VOIDED")])
    _refused(repo, capsys, "--prune-sku", "98d244c6", "--store", "default")


def test_refuses_when_this_stores_ebay_account_has_an_offer(repo, ebay, capsys):
    _write(repo / "listings_ledger.csv", [STALE])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    ebay.offers["default"]["98d244c6"] = [{"offerId": "O-HERE"}]
    out = _refused(repo, capsys, "--prune-sku", "98d244c6", "--store", "default")
    assert "O-HERE" in out


@pytest.mark.parametrize("row", [
    dict(STALE, offer_id="O-9"),
    dict(STALE, listing_id="123456789012"),
    dict(STALE, status="SOLD"),
    dict(STALE, status="SHIPPED"),
])
def test_refuses_a_row_that_is_a_real_ebay_record_or_a_sale(repo, ebay, capsys, row):
    _write(repo / "listings_ledger.csv", [row])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    _refused(repo, capsys, "--prune-sku", "98d244c6", "--store", "default")
    assert ebay.calls == []          # refused on the row alone


def test_refuses_when_this_stores_sales_ledger_has_an_order(repo, ebay, capsys):
    _write(repo / "listings_ledger.csv", [STALE])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    (repo / "sales_ledger.csv").write_text("order_id,sku\n1-1,98d244c6\n", encoding="utf-8")
    _refused(repo, capsys, "--prune-sku", "98d244c6", "--store", "default")


@pytest.mark.parametrize("rows", [[KEEP], [STALE, STALE]])
def test_refuses_unless_exactly_one_row(repo, ebay, capsys, rows):
    _write(repo / "listings_ledger.csv", rows)
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    _refused(repo, capsys, "--prune-sku", "98d244c6", "--store", "default")


def test_ebay_error_fails_closed(repo, ebay, capsys):
    _write(repo / "listings_ledger.csv", [STALE])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    ebay.fail = "server error"
    out = _refused(repo, capsys, "--prune-sku", "98d244c6", "--store", "default")
    assert "lookup failed" in out


def test_needs_an_explicit_store(repo, ebay, monkeypatch):
    _write(repo / "listings_ledger.csv", [STALE])
    _write(repo / "listings_ledger-junk.csv", [MOVED])
    monkeypatch.setenv(stores.STORE_ENV_VAR, "default")
    with pytest.raises(SystemExit, match="explicit --store"):
        _run("--prune-sku", "98d244c6", "--apply")
    assert _skus(repo / "listings_ledger.csv") == ["98d244c6"]


@pytest.mark.parametrize("extra", [["--all-stores"], ["--store", "default", "--prune-unknown"]])
def test_refuses_bulk_flags(repo, ebay, extra):
    _write(repo / "listings_ledger.csv", [STALE])
    assert _run("--prune-sku", "98d244c6", *extra, "--apply") == 2
    assert _skus(repo / "listings_ledger.csv") == ["98d244c6"]


def test_prunes_from_a_named_store_too(repo, ebay, capsys):
    # the reverse move: stale row on junk, item now on the main store
    _write(repo / "listings_ledger.csv", [MOVED])
    _write(repo / "listings_ledger-junk.csv", [STALE, KEEP])
    assert _run("--prune-sku", "98d244c6", "--store", "junk", "--apply") == 0
    assert _skus(repo / "listings_ledger-junk.csv") == ["ed901dbe"]
    assert _skus(repo / "listings_ledger.csv") == ["98d244c6"]
