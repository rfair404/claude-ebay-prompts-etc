"""lib/ledger_store.py (GH #380 P1.1) against an in-memory stand-in for the
Worker: ETag locking and 412 retry, the offline queue, local mode, the
refusal case, atomic writes, the Drive mirror hook — and the parity check
that the Worker's ALLOWED list is ledger_store.LEDGERS. Nothing here talks to
Cloudflare. No pytest fixtures — runs under tests/run_all.py too.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import ledger_store as ls  # noqa: E402

WORKER_JS = ROOT / "cloud" / "ledger-worker" / "worker.js"
HEADER = b"order_id,sku,price\r\n"


class FakeWorker:
    """Mimics worker.js: allowlist, If-Match / If-None-Match PUTs, history.

    `race=n` lets another writer land just before each of the next n PUTs,
    which is what makes the real Worker answer 412. `offline=True` makes every
    call raise Offline, as HttpTransport does when the Worker is unreachable.
    """

    def __init__(self, objects=None, *, allowed=ls.LEDGERS):
        self.allowed = set(allowed)
        self.objects, self.history, self.n = {}, {}, 0
        self.race, self.offline, self.puts = 0, False, 0
        for k, v in (objects or {}).items():
            self._store(k, v)

    def _store(self, key, data):
        if key in self.objects:
            self.history.setdefault(key, []).append((f"v{self.n}", self.objects[key][0]))
        self.n += 1
        self.objects[key] = (data, f'"e{self.n}"')

    def __call__(self, method, path, body=None, headers=None):
        if self.offline:
            raise ls.Offline("unreachable")
        headers = headers or {}
        parts = path.strip("/").split("/")
        route, key = parts[0], parts[1]
        if key not in self.allowed:
            return ls.Response(403, {}, b"key not allowed")
        if route == "h":
            if len(parts) == 3:
                hit = dict(self.history.get(key, [])).get(parts[2])
                return ls.Response(200, {}, hit) if hit else ls.Response(404)
            out = [{"stamp": s, "size": len(b)} for s, b in self.history.get(key, [])]
            return ls.Response(200, {}, json.dumps(out).encode())
        if method == "GET":
            if key not in self.objects:
                return ls.Response(404)
            data, etag = self.objects[key]
            return ls.Response(200, {"etag": etag}, data)
        self.puts += 1
        if self.race:
            self.race -= 1
            cur = self.objects.get(key, (HEADER, None))[0]
            self._store(key, cur + b"RACE,x,1\r\n")
        cur = self.objects.get(key)
        if headers.get("If-None-Match") == "*":
            if cur:
                return ls.Response(412)
        elif not cur or cur[1] != headers.get("If-Match"):
            return ls.Response(412)
        self._store(key, body)
        return ls.Response(200, {"etag": self.objects[key][1]}, b"ok")


def _store(transport=None, files=None):
    root = Path(tempfile.mkdtemp(prefix="ledger_store_test_"))
    for rel, data in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    mirrored = []
    st = ls.LedgerStore(root=root, transport=transport, mirror=mirrored.append,
                        client_dir=lambda code: root / "inventory" / f"CONSIGN-{code}",
                        out=open(os.devnull, "w"))
    return st, root, mirrored


def add(order_id):
    return lambda rows: rows + [{"order_id": order_id, "sku": "s", "price": "1.00"}]


def _ids(data: bytes):
    return [r["order_id"] for r in ls.parse_csv(data)[1]]


# --------------------------------------------------------------------------
# parity: the Worker's allowlist is ledger_store's
# --------------------------------------------------------------------------

def test_worker_allowlist_matches_ledgers():
    m = re.search(r"const ALLOWED = \[(.*?)\];", WORKER_JS.read_text(), re.S)
    assert m, "worker.js lost its `const ALLOWED = [...]`"
    allowed = re.findall(r'"([^"]+)"', m.group(1))
    assert allowed == list(ls.LEDGERS)


def test_allowlist_scope():
    assert len(ls.LEDGERS) == 7
    assert not any("inventory_sheet" in k or "clients" in k for k in ls.LEDGERS)
    assert ls.PROBE_KEY not in ls.LEDGERS
    # client ledgers are keyed by code only
    assert [k for k in ls.LEDGERS if k.startswith("client_ledger")] == ["client_ledger-K01.csv"]


# --------------------------------------------------------------------------
# 412 retry
# --------------------------------------------------------------------------

def test_412_retry_table():
    # (other writers landing before our PUT, expect success, PUTs made)
    cases = [(0, True, 1), (1, True, 2), (ls.MAX_ATTEMPTS - 1, True, ls.MAX_ATTEMPTS),
             (ls.MAX_ATTEMPTS, False, ls.MAX_ATTEMPTS)]
    for race, ok, puts in cases:
        w = FakeWorker({"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
        w.race = race
        st, root, mirrored = _store(w)
        try:
            st.update("sales", add("B"), store="default")
            assert ok, race
        except ls.Conflict:
            assert not ok, race
        assert w.puts == puts, (race, w.puts)
        cloud = _ids(w.objects["sales_ledger.csv"][0])
        cache = root / "sales_ledger.csv"
        if ok:
            # fn re-applied on top of every row the other writers added
            assert cloud == ["A"] + ["RACE"] * race + ["B"], (race, cloud)
            assert cache.read_bytes() == w.objects["sales_ledger.csv"][0]
            assert st.state()["etags"]["sales_ledger.csv"] == w.objects["sales_ledger.csv"][1]
            assert mirrored == [cache]
        else:
            assert "B" not in cloud and not cache.exists() and mirrored == []


def test_first_write_seeds_with_if_none_match():
    # no local file: a genuine first write creates the key
    w = FakeWorker()
    st, root, _ = _store(w)
    st.update("unwound", add("U1"), store="junk")
    assert _ids(w.objects["unwound_orders-junk.csv"][0]) == ["U1"]
    assert (root / "unwound_orders-junk.csv").exists()


def test_unseeded_key_with_local_rows_is_refused():
    w = FakeWorker()
    local = HEADER + b"A,s,1\r\n"
    st, root, mirrored = _store(w, {"sales_ledger.csv": local})
    try:
        st.update("sales", add("B"), store="default")
        raise AssertionError("expected Refused")
    except ls.Refused as e:
        assert "ledger seed --apply" in str(e)
    assert w.puts == 0 and "sales_ledger.csv" not in w.objects
    assert (root / "sales_ledger.csv").read_bytes() == local and mirrored == []
    # the seed path still works, and the write goes through afterwards
    st.seed(apply=True)
    st.update("sales", add("B"), store="default")
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["A", "B"]


# --------------------------------------------------------------------------
# offline queue
# --------------------------------------------------------------------------

def test_offline_queue_then_push():
    w = FakeWorker({"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st, root, mirrored = _store(w)
    st.update("sales", add("B"), store="default")          # online: cache + etag
    base = st.state()["etags"]["sales_ledger.csv"]
    w.offline = True
    st.update("sales", add("C"), store="default")
    st.update("sales", add("D"), store="default")
    assert _ids((root / "sales_ledger.csv").read_bytes()) == ["A", "B", "C", "D"]
    assert st.state()["pending"] == {"sales_ledger.csv": base}   # first base kept
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["A", "B"]
    w.offline = False
    assert st.push() == ["pushed sales_ledger.csv"]
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["A", "B", "C", "D"]
    assert st.state()["pending"] == {}
    assert len(mirrored) == 3


def test_pending_write_is_pushed_before_the_next_update():
    w = FakeWorker({"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st, root, _ = _store(w)
    st.update("sales", add("B"), store="default")
    w.offline = True
    st.update("sales", add("C"), store="default")
    w.offline = False
    st.update("sales", add("D"), store="default")
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["A", "B", "C", "D"]
    assert st.state()["pending"] == {}


def test_offline_write_refused_when_cloud_moved():
    w = FakeWorker({"listings_ledger-junk.csv": HEADER + b"A,s,1\r\n"})
    st, root, _ = _store(w)
    st.update("sales", add("x"), store="junk")             # unrelated key, fine
    st.pull()
    w.offline = True
    st.update("listings", add("C"), store="junk")
    w.offline = False
    w._store("listings_ledger-junk.csv", HEADER + b"OTHER,s,1\r\n")   # another machine
    for attempt in (st.push, lambda: st.update("listings", add("D"), store="junk")):
        try:
            attempt()
            raise AssertionError("expected Conflict")
        except ls.Conflict as e:
            assert "reconcile --apply" in str(e) and "--store junk" in str(e)
    assert _ids(w.objects["listings_ledger-junk.csv"][0]) == ["OTHER"]
    assert "listings_ledger-junk.csv" in st.state()["pending"]


# --------------------------------------------------------------------------
# local mode and the refusal case
# --------------------------------------------------------------------------

def test_local_mode_writes_csv_with_backup():
    st, root, mirrored = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    rows = st.update("sales", add("B"), store="default")
    assert [r["order_id"] for r in rows] == ["A", "B"]
    assert _ids((root / "sales_ledger.csv").read_bytes()) == ["A", "B"]
    backups = list(root.glob("sales_ledger.backup-*.csv"))
    assert len(backups) == 1 and _ids(backups[0].read_bytes()) == ["A"]
    assert mirrored == [root / "sales_ledger.csv"]
    assert not (root / ls.STATE_FILE).exists()


def test_local_backup_false_skips_backup_but_still_chains():
    st, root, mirrored = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st.update("sales", add("B"), store="default", backup=False)
    st.update("sales", add("C"), store="default", backup=False)
    assert _ids((root / "sales_ledger.csv").read_bytes()) == ["A", "B", "C"]
    assert list(root.glob("sales_ledger.backup-*.csv")) == []
    assert [e["kind"] for e in _events(st)] == ["genesis", "change", "change"]
    assert _rebuilt(st) == ["A", "B", "C"]
    ok, clean, _ = st.verify()
    assert ok and clean


def _set_ledger_override(value):
    """Set (or with None, clear) $EBAYBIZ_LISTINGS_LEDGER; returns the old value."""
    old = os.environ.pop("EBAYBIZ_LISTINGS_LEDGER", None)
    if value is not None:
        os.environ["EBAYBIZ_LISTINGS_LEDGER"] = value
    return old


def test_open_store_modes_table():
    cfg = {"ledger_store": {"url": "https://x.workers.dev", "token": "t"}}
    # (config, state file exists, expect)
    cases = [({}, False, "local"), ({}, True, "refused"),
             (cfg, False, "cloud"), (cfg, True, "cloud")]
    old = os.environ.pop(ls.TOKEN_ENV, None)
    old_ledger = _set_ledger_override(None)
    try:
        for conf, state, want in cases:
            root = Path(tempfile.mkdtemp())
            if state:
                (root / ls.STATE_FILE).write_text("{}")
            try:
                st = ls.open_store(conf, root=root)
                got = "cloud" if st.cloud else "local"
            except ls.Refused as e:
                got = "refused"
                assert ls.STATE_FILE in str(e)
            assert got == want, (conf, state, got)
    finally:
        _set_ledger_override(old_ledger)
        if old is not None:
            os.environ[ls.TOKEN_ENV] = old


def test_open_store_is_local_under_listings_ledger_override():
    cfg = {"ledger_store": {"url": "https://x.workers.dev", "token": "t"}}
    root = Path(tempfile.mkdtemp())
    (root / ls.STATE_FILE).write_text("{}")
    old = _set_ledger_override(str(root / "listings_ledger.csv"))
    try:
        for conf in (cfg, {}):
            assert not ls.open_store(conf, root=root).cloud, conf
    finally:
        _set_ledger_override(old)


def test_config_missing_token_is_an_error():
    old = os.environ.pop(ls.TOKEN_ENV, None)
    try:
        ls.load_ledger_config({"ledger_store": {"url": "https://x"}})
        raise AssertionError("expected LedgerStoreError")
    except ls.LedgerStoreError as e:
        assert "token" in str(e)
    finally:
        if old is not None:
            os.environ[ls.TOKEN_ENV] = old


def test_refused_store_writes_nothing():
    root = Path(tempfile.mkdtemp())
    (root / ls.STATE_FILE).write_text("{}")
    (root / "sales_ledger.csv").write_bytes(HEADER)
    old = _set_ledger_override(None)
    try:
        ls.open_store({}, root=root).update("sales", add("B"), store="default")
        raise AssertionError("expected Refused")
    except ls.Refused:
        pass
    finally:
        _set_ledger_override(old)
    assert (root / "sales_ledger.csv").read_bytes() == HEADER


# --------------------------------------------------------------------------
# atomic write
# --------------------------------------------------------------------------

def test_atomic_write_leaves_old_file_on_failure():
    root = Path(tempfile.mkdtemp())
    target = root / "sales_ledger.csv"
    target.write_bytes(b"old")
    real = os.replace

    def boom(*a, **k):
        raise OSError("disk full")
    os.replace = boom
    try:
        ls.atomic_write(target, b"new")
        raise AssertionError("expected OSError")
    except OSError:
        pass
    finally:
        os.replace = real
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in root.iterdir()) == ["sales_ledger.csv"]
    ls.atomic_write(target, b"new")
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in root.iterdir()) == ["sales_ledger.csv"]


def test_csv_keeps_column_order_and_appends_new():
    data = ls.to_csv(["b", "a"], [{"a": "1", "b": "2", "c": "3"}])
    assert data.splitlines()[0] == b"b,a,c"


# --------------------------------------------------------------------------
# mirror hook, names, allowlist
# --------------------------------------------------------------------------

def test_mirror_only_after_a_successful_write():
    # (worker state, expect mirror calls)
    cases = [("ok", 1), ("http_error", 0), ("conflict", 0)]
    for mode, calls in cases:
        w = FakeWorker({"sales_ledger.csv": HEADER})
        if mode == "conflict":
            w.race = ls.MAX_ATTEMPTS
        if mode == "http_error":
            w.allowed = set()                       # every key 403
        st, root, mirrored = _store(w)
        try:
            st.update("sales", add("B"), store="default")
        except ls.LedgerStoreError:
            pass
        assert len(mirrored) == calls, mode


def test_client_ledger_key_and_path():
    w = FakeWorker()
    st, root, _ = _store(w)
    (root / "inventory" / "CONSIGN-K01").mkdir(parents=True)
    st.update("client_ledger", add("K01-C001"), intake="CONSIGN-K01")
    assert "client_ledger-K01.csv" in w.objects
    assert (root / "inventory" / "CONSIGN-K01" / "client_ledger.csv").exists()


def test_bad_targets_table():
    st, root, _ = _store(FakeWorker())
    (root / "inventory" / "CONSIGN-K02").mkdir(parents=True)
    cases = [
        (("client_ledger",), {"intake": "CONSIGN-K02"}, ls.NotAllowed),   # unlisted client
        (("listings",), {"store": "other"}, ls.NotAllowed),                # unlisted store
        (("inventory_sheet",), {"store": "default"}, ls.LedgerStoreError),
        (("client_ledger",), {}, ls.LedgerStoreError),                     # no intake
    ]
    for args, kw, exc in cases:
        try:
            st.update(*args, add("x"), **kw)
            raise AssertionError(f"expected {exc.__name__} for {args} {kw}")
        except exc:
            pass


def test_local_mode_accepts_unlisted_keys():
    st, root, _ = _store(None)
    folder = root / "inventory" / "CONSIGN-K02"
    folder.mkdir(parents=True)
    assert "client_ledger-K02.csv" not in ls.LEDGERS
    key, path = st.resolve("client_ledger", intake="CONSIGN-K02")
    assert key == "client_ledger-K02.csv" and path == folder / "client_ledger.csv"
    assert st.rows("client_ledger", intake="CONSIGN-K02") == []
    st.update("client_ledger", add("K02-C001"), intake="CONSIGN-K02")
    assert [r["order_id"] for r in st.rows("client_ledger", intake="CONSIGN-K02")] == ["K02-C001"]
    assert [e["kind"] for e in _events(st, "client_ledger-K02.csv")] == ["genesis", "change"]


def test_cloud_mode_rows_refuses_unlisted_keys():
    st, root, _ = _store(FakeWorker())
    (root / "inventory" / "CONSIGN-K02").mkdir(parents=True)
    try:
        st.rows("client_ledger", intake="CONSIGN-K02")
        raise AssertionError("expected NotAllowed")
    except ls.NotAllowed:
        pass


# --------------------------------------------------------------------------
# check, seed, history/restore
# --------------------------------------------------------------------------

def test_check_needs_the_403_probe():
    st, _, _ = _store(FakeWorker())
    ok, lines = st.check()
    assert ok and any("refused (403)" in l for l in lines)
    leaky = FakeWorker(allowed=list(ls.LEDGERS) + [ls.PROBE_KEY])
    leaky.objects[ls.PROBE_KEY] = (b"x", '"e"')
    ok, lines = _store(leaky)[0].check()
    assert not ok and any("expected 403" in l for l in lines)
    w = FakeWorker()
    w.offline = True
    assert _store(w)[0].check()[0] is False
    assert _store(None)[0].check()[0] is True               # local mode


def test_seed_dry_run_then_apply_never_overwrites():
    w = FakeWorker({"sales_ledger.csv": HEADER + b"CLOUD,s,1\r\n"})
    st, root, _ = _store(w, {"sales_ledger.csv": HEADER + b"LOCAL,s,1\r\n",
                             "listings_ledger.csv": HEADER + b"L,s,1\r\n"})
    lines = st.seed()
    assert "would seed listings_ledger.csv" in "\n".join(lines)
    assert "listings_ledger.csv" not in w.objects
    st.seed(apply=True)
    assert _ids(w.objects["listings_ledger.csv"][0]) == ["L"]
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["CLOUD"]


def test_history_and_restore():
    w = FakeWorker({"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st, root, _ = _store(w)
    st.update("sales", add("B"), store="default")
    versions = st.history("sales_ledger.csv")
    assert len(versions) == 1
    stamp = versions[0]["stamp"]
    assert "would restore" in st.restore("sales_ledger.csv", stamp)
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["A", "B"]
    st.restore("sales_ledger.csv", stamp, apply=True)
    assert _ids(w.objects["sales_ledger.csv"][0]) == ["A"]
    assert _ids((root / "sales_ledger.csv").read_bytes()) == ["A"]
    assert len(st.history("sales_ledger.csv")) == 2             # undoable


# --------------------------------------------------------------------------
# rows(): the read API (#380 P3.1)
# --------------------------------------------------------------------------

def test_rows_reads_and_filters():
    st, root, _ = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\nB,t,2\r\nC,s,3\r\n"})
    assert [r["order_id"] for r in st.rows("sales")] == ["A", "B", "C"]
    assert [r["order_id"] for r in st.rows("sales", sku="s")] == ["A", "C"]
    assert [r["order_id"] for r in st.rows("sales", sku="s", price=3)] == ["C"]
    assert st.rows("sales", sku="nope") == []
    assert st.rows("sales", store="junk") == []                 # no file yet
    for bad, kw in (("sales", {"skuu": "s"}), ("inventory_sheet", {})):
        try:
            st.rows(bad, **kw)
            raise AssertionError(f"expected LedgerStoreError for {bad} {kw}")
        except ls.LedgerStoreError:
            pass


def test_rows_under_listings_ledger_override():
    root = Path(tempfile.mkdtemp())
    path = root / "elsewhere.csv"
    path.write_bytes(b"sku,status\r\nX1,LIVE\r\nX2,SOLD\r\n")
    old = _set_ledger_override(str(path))
    try:
        assert [r["sku"] for r in ls.rows("listings", status="LIVE")] == ["X1"]
    finally:
        _set_ledger_override(old)


# --------------------------------------------------------------------------
# the event log (#394)
# --------------------------------------------------------------------------

FIXTURE = ROOT / "tests" / "fixtures" / "ledger_chain"


def _events(st, key="sales_ledger.csv"):
    return [json.loads(line) for line in st.chain_path(key).read_text().splitlines()]


def _rebuilt(st, key="sales_ledger.csv"):
    rp = ls.replay(st.chain_path(key), key)
    assert rp.broken is None, rp.broken
    return sorted(json.loads(k)["order_id"] for k in rp.keys.elements())


def test_update_writes_genesis_then_changes():
    st, root, _ = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st.update("sales", add("B"))
    st.update("sales", lambda rows: [dict(r, price="9") if r["order_id"] == "A" else r
                                     for r in rows])
    evs = _events(st)
    assert [e["kind"] for e in evs] == ["genesis", "change", "change"]
    assert [r["order_id"] for r in evs[0]["add"]] == ["A"]         # rows as found
    assert evs[2]["remove"] == [{"order_id": "A", "sku": "s", "price": "1"}]
    assert evs[2]["add"] == [{"order_id": "A", "sku": "s", "price": "9"}]
    assert [e["prev"] for e in evs[1:]] == [e["hash"] for e in evs[:-1]]
    assert _rebuilt(st) == ["A", "B"]
    ok, clean, _ = st.verify()
    assert ok and clean


def test_unchanged_write_adds_no_event():
    st, root, _ = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st.update("sales", add("B"))
    st.update("sales", lambda rows: rows)
    assert len(_events(st)) == 2


def test_csv_format_is_unchanged_by_the_log():
    st, root, _ = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st.update("sales", add("B"))
    assert (root / "sales_ledger.csv").read_bytes() == HEADER + b"A,s,1\r\nB,s,1.00\r\n"
    assert st.chain_path("sales_ledger.csv").parent == root / "reports" / "ledger_chain"


def test_write_outside_update_is_drift_then_recorded():
    st, root, _ = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st.update("sales", add("B"))
    (root / "sales_ledger.csv").write_bytes(HEADER + b"A,s,1\r\nB,s,1.00\r\nHAND,x,5\r\n")
    ok, clean, lines = st.verify()
    assert ok and not clean and "DRIFT" in "\n".join(lines)
    st.update("sales", add("C"))
    assert [e["kind"] for e in _events(st)] == ["genesis", "change", "drift", "change"]
    assert _rebuilt(st) == ["A", "B", "C", "HAND"]
    ok, clean, _ = st.verify()
    assert ok and clean


def test_cloud_and_offline_writes_are_logged():
    w = FakeWorker({"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    w.race = 1                                   # the retry must log once, not twice
    st, root, _ = _store(w)
    st.update("sales", add("B"))
    assert [e["kind"] for e in _events(st)] == ["genesis", "change"]
    w.offline = True
    st.update("sales", add("C"))
    assert _rebuilt(st) == ["A", "B", "C", "RACE"]
    assert st.verify()[0]


def test_broken_log_is_not_extended_and_write_still_lands():
    st, root, _ = _store(None, {"sales_ledger.csv": HEADER + b"A,s,1\r\n"})
    st.update("sales", add("B"))
    p = st.chain_path("sales_ledger.csv")
    p.write_text(p.read_text().replace('"price":"1.00"', '"price":"0.01"'))
    before = p.read_bytes()
    st.update("sales", add("C"))
    assert _ids((root / "sales_ledger.csv").read_bytes()) == ["A", "B", "C"]
    assert p.read_bytes() == before


def test_listings_override_keeps_the_log_beside_it():
    root = Path(tempfile.mkdtemp())
    path = root / "ledger.csv"
    path.write_bytes(b"sku,status\r\nX1,LIVE\r\n")
    old = _set_ledger_override(str(path))
    try:
        st = ls.open_store({})
        assert st.chain_dir == root / "ledger_chain"           # never the repo's reports/
        st.mirror, st.out = (lambda p: None), open(os.devnull, "w")
        st.update("listings", lambda rows: rows + [{"sku": "X2", "status": "DRAFT"}])
        assert (root / "ledger_chain" / "listings_ledger.csv.jsonl").exists()
    finally:
        _set_ledger_override(old)


def _verify_cli(chain_dir, root=FIXTURE, *extra):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = ls.main(["verify", "--chain-dir", str(chain_dir), "--root", str(root), *extra])
    return rc, buf.getvalue()


def test_verify_cli_on_the_fixture_is_clean():
    rc, out = _verify_cli(FIXTURE / "chain", FIXTURE, "--strict")
    assert rc == 0, out
    assert "ok     sales_ledger.csv  4 events  2 rows" in out


def _tampered(edit):
    d = Path(tempfile.mkdtemp())
    lines = (FIXTURE / "chain" / "sales_ledger.csv.jsonl").read_text().splitlines()
    (d / "sales_ledger.csv.jsonl").write_text("\n".join(edit(lines)) + "\n")
    return d


def _rehash_edit(lines):
    """Edit a past event AND recompute its own hashes: the next link breaks."""
    ev = json.loads(lines[1])
    ev["add"][0]["sold_price"] = "3.00"
    genesis = json.loads(lines[0])["add"]
    ev["state"] = ls.state_hash(ev["fields"], ls._row_keys(ev["fields"], genesis + ev["add"]))
    ev["hash"] = ls.event_hash(ev)
    return [lines[0], json.dumps(ev), *lines[2:]]


def test_verify_cli_names_the_first_broken_link_table():
    cases = [
        ("edited row", lambda l: [l[0], l[1].replace('"30.00"', '"3.00"'), *l[2:]],
         "line 2 (seq 1): hash does not match"),
        ("deleted event", lambda l: [l[0], *l[2:]], "line 2 (seq 2): prev does not link"),
        ("swapped events", lambda l: [l[0], l[2], l[1], l[3]], "line 2 (seq 2): prev"),
        ("rehashed edit", _rehash_edit, "line 3 (seq 2): prev does not link"),
        ("garbage line", lambda l: [l[0], "{not json", *l[2:]], "line 2: not a JSON event"),
        ("genesis removed", lambda l: l[1:], "line 1 (seq 1): prev"),
    ]
    for name, edit, want in cases:
        rc, out = _verify_cli(_tampered(edit))
        assert rc == 1, (name, out)
        assert "BROKEN sales_ledger.csv" in out and want in out, (name, out)


def test_verify_cli_drift_fails_only_when_strict():
    root = Path(tempfile.mkdtemp())
    data = (FIXTURE / "sales_ledger.csv").read_bytes() + b"00-00000-00009,HAND,1.00\r\n"
    (root / "sales_ledger.csv").write_bytes(data)
    rc, out = _verify_cli(FIXTURE / "chain", root)
    assert rc == 0 and "DRIFT" in out, out
    rc, out = _verify_cli(FIXTURE / "chain", root, "--strict")
    assert rc == 1, out


def test_verify_with_no_logs_is_clean():
    rc, out = _verify_cli(Path(tempfile.mkdtemp()))
    assert rc == 0 and "no event logs" in out
