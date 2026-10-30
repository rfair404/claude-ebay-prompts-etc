"""Listings-ledger writes go through lib/ledger_store (GH #380 P1.2).

list_edit.upsert_listing() and tools/ledger_reconcile.py --apply are the two
listings writers. These pin both modes:

  * local (no `ledger_store:` in config): the CSV is rewritten as before.
    upsert_listing makes no .backup-* copy (it runs on every sync, publish,
    sale and shipment); reconcile keeps its one backup per --apply.
  * cloud: the write merges into the Worker's copy under If-Match, so a row
    another machine wrote is never dropped, and the CSV becomes the cache.

No network: the Worker is a fake transport; stores.REPO is tmp_path.

Run:  pytest tests/test_listings_ledger_store.py
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import ledger_store as LS                                      # noqa: E402
import list_edit as L                                          # noqa: E402
import ledger_reconcile as LR                                  # noqa: E402
import stores                                                  # noqa: E402

CLOUD_CFG = {"ledger_store": {"url": "https://w.example", "token": "t"}}
FIELDS = L._LEDGER_FIELDS


class _Cloud:
    """The Worker's GET and conditional PUT, nothing else."""

    def __init__(self):
        self.objects, self.n, self.puts = {}, 0, []

    def seed(self, key, rows):
        self.n += 1
        self.objects[key] = (LS.to_csv(FIELDS, rows), f'"e{self.n}"')

    def rows(self, key):
        return LS.parse_csv(self.objects[key][0])[1]

    def __call__(self, method, path, body=None, headers=None):
        key = path.strip("/").split("/")[1]
        if method == "GET":
            if key not in self.objects:
                return LS.Response(404)
            data, etag = self.objects[key]
            return LS.Response(200, {"etag": etag}, data)
        headers = headers or {}
        etag = self.objects.get(key, (None, None))[1]
        if headers.get("If-Match") != etag and not (
                etag is None and headers.get("If-None-Match") == "*"):
            return LS.Response(412)
        self.n += 1
        self.objects[key] = (body, f'"e{self.n}"')
        self.puts.append(key)
        return LS.Response(200, {"etag": f'"e{self.n}"'}, b"ok")


def _isolate(monkeypatch, root, cfg=None):
    monkeypatch.setattr(stores, "REPO", root)
    monkeypatch.setattr(stores, "load_config", lambda: cfg or {})
    for var in ("EBAYBIZ_STORE", "EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EBAYBIZ_GDRIVE_AUTO", "0")
    monkeypatch.setattr(L, "ledger_changed", lambda path: None)
    monkeypatch.setattr(LR, "ledger_changed", lambda path: None)


@pytest.fixture
def local(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    return tmp_path


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    fake = _Cloud()
    _isolate(monkeypatch, tmp_path, CLOUD_CFG)
    monkeypatch.setattr(LS, "HttpTransport", lambda url, token: fake)
    return fake


def _row(sku, **kw):
    return {**{k: "" for k in FIELDS}, "sku": sku, **kw}


def _read(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# -- upsert_listing ---------------------------------------------------------

def test_local_upsert_writes_the_csv_and_makes_no_backup(local):
    assert L.upsert_listing("sku-a", "DRAFTED", title="A", price="10.00") \
        == str(local / "listings_ledger.csv")
    L.upsert_listing("sku-a", "SYNCED", offer_id="O1")
    (row,) = _read(local / "listings_ledger.csv")
    assert (row["status"], row["offer_id"], row["price"]) == ("SYNCED", "O1", "10.00")
    header = (local / "listings_ledger.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(FIELDS)
    assert not list(local.glob("*.backup-*"))
    assert not (local / LS.STATE_FILE).exists()


def test_local_field_edit_without_a_row_writes_nothing(local):
    assert L.upsert_listing("sku-x", "", price="5.00") is None
    assert not (local / "listings_ledger.csv").exists()


def test_cloud_upsert_keeps_a_row_another_machine_wrote(tmp_path, cloud):
    cloud.seed("listings_ledger.csv", [_row("elsewhere", status="PUBLISHED")])
    L.upsert_listing("here", "DRAFTED", title="Here")

    assert [r["sku"] for r in cloud.rows("listings_ledger.csv")] == ["elsewhere", "here"]
    assert [r["sku"] for r in _read(tmp_path / "listings_ledger.csv")] == ["elsewhere", "here"]
    assert not list(tmp_path.glob("*.backup-*"))


def test_cloud_upsert_writes_the_named_stores_key(cloud):
    L.upsert_listing("j1", "PUBLISHED", listing_id="111", store="junk")
    assert set(cloud.objects) == {"listings_ledger-junk.csv"}
    assert cloud.rows("listings_ledger-junk.csv")[0]["listing_id"] == "111"


def test_cloud_field_edit_without_a_row_puts_nothing(cloud):
    cloud.seed("listings_ledger.csv", [_row("other")])
    assert L.upsert_listing("missing", "", price="5.00") is None
    assert cloud.puts == []


def test_upsert_refuses_when_cloud_state_lost_its_config(local):
    (local / LS.STATE_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(L.LedgerWriteError):
        L.upsert_listing("sku-a", "DRAFTED", strict=True)
    assert L.upsert_listing("sku-a", "DRAFTED") is None       # best-effort: warns
    assert not (local / "listings_ledger.csv").exists()


# -- ledger_reconcile --apply ----------------------------------------------

def _fake_ebay(monkeypatch, price="12.0"):
    monkeypatch.setattr(LR, "ebay_truth", lambda store=None: {"live1": {
        "sku": "live1", "status": "PUBLISHED", "price": price, "offer_id": "O",
        "listing_id": "999", "url": "https://www.ebay.com/itm/999",
        "listing_status": "ACTIVE"}})


def _write(path, rows):
    path.write_bytes(LS.to_csv(FIELDS, rows))


def test_local_reconcile_backs_up_once_and_rewrites(local, monkeypatch, capsys):
    _fake_ebay(monkeypatch)
    _write(local / "listings_ledger.csv", [_row("live1", status="PUBLISHED", price="20.0")])

    assert LR.reconcile("default", apply=True) == 0

    assert _read(local / "listings_ledger.csv")[0]["price"] == "12.0"
    (backup,) = local.glob("listings_ledger.backup-*.csv")
    assert f"backed up -> {backup.name}" in capsys.readouterr().out


def test_cloud_reconcile_merges_into_the_cloud_copy(tmp_path, cloud, monkeypatch):
    _fake_ebay(monkeypatch)
    _write(tmp_path / "listings_ledger.csv", [_row("live1", status="PUBLISHED", price="20.0")])
    # The cloud has the same row plus one this checkout's cache never saw.
    cloud.seed("listings_ledger.csv", [_row("live1", status="PUBLISHED", price="20.0"),
                                       _row("draft9", status="DRAFTED")])

    assert LR.reconcile("default", apply=True) == 0

    got = {r["sku"]: r for r in cloud.rows("listings_ledger.csv")}
    assert set(got) == {"live1", "draft9"} and got["live1"]["price"] == "12.0"
    assert _read(tmp_path / "listings_ledger.csv") == cloud.rows("listings_ledger.csv")
    assert not list(tmp_path.glob("*.backup-*"))                # the Worker keeps history


def test_cloud_reconcile_prunes_orphans_from_the_cloud_copy(tmp_path, cloud, monkeypatch):
    _fake_ebay(monkeypatch)
    rows = [_row("live1", status="PUBLISHED", price="12.0", offer_id="O",
                 listing_id="999", url="https://www.ebay.com/itm/999"),
            _row("gone1", status="SYNCED"), _row("void1", status="VOIDED")]
    _write(tmp_path / "listings_ledger.csv", rows)
    cloud.seed("listings_ledger.csv", rows)

    assert LR.reconcile("default", apply=True, prune=True) == 0

    assert [r["sku"] for r in cloud.rows("listings_ledger.csv")] == ["live1", "void1"]


def test_reconcile_store_off_the_allowlist_still_writes_locally(local, monkeypatch):
    _fake_ebay(monkeypatch)
    monkeypatch.setattr(stores, "load_config", lambda: {"ebay": {"stores": {"outlet": {}}}})
    _write(local / "listings_ledger-outlet.csv", [_row("live1", status="PUBLISHED", price="20.0")])

    assert LR.reconcile("outlet", apply=True) == 0

    assert _read(local / "listings_ledger-outlet.csv")[0]["price"] == "12.0"
    assert len(list(local.glob("listings_ledger-outlet.backup-*.csv"))) == 1
