"""lib/offsite.py — SigV4 signing and the push/pull safety rules.

The signer is checked against AWS's published S3 example (GET /test.txt with
a Range header, "Signature Calculations for the Authorization Header"). The
sync rules run against an in-memory bucket. No pytest fixtures — runs under
tests/run_all.py too.
"""
import datetime as dt
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import offsite  # noqa: E402
from offsite import RemoteObject  # noqa: E402

DP = "data/"


class FakeRemote:
    def __init__(self):
        self.objs: dict[str, bytes] = {}

    def list(self, prefix):
        return {k: RemoteObject(len(v), hashlib.md5(v).hexdigest())
                for k, v in self.objs.items() if k.startswith(prefix)}

    def put(self, key, data, md5_hex, content_type="application/octet-stream"):
        assert hashlib.md5(data).hexdigest() == md5_hex
        self.objs[key] = data
        self.types = {**getattr(self, "types", {}), key: content_type}

    def presign_get(self, key, expires, content_type=None):
        return f"https://bucket/{key}?expires={expires}&type={content_type}"

    def copy(self, src, dst):
        self.objs[dst] = self.objs[src]

    def get(self, key):
        return self.objs[key]

    def delete(self, key):
        del self.objs[key]


def _tree(files: dict[str, bytes]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="offsite_test_"))
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _plan(root, remote):
    cache = offsite.Md5Cache(root / offsite.STATE_FILE)
    return offsite.make_plan(root, offsite.DEFAULT_INCLUDE, remote.list(DP), DP, cache)


def test_sigv4_matches_aws_published_example():
    h = offsite.sigv4_headers(
        "GET", "examplebucket.s3.amazonaws.com", "/test.txt", {},
        {"Range": "bytes=0-9"},
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1", now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
    assert h["authorization"].endswith(
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in h["authorization"]


def test_presign_matches_aws_published_example():
    # "Authenticating Requests: Using Query Parameters" — GET /test.txt, 24h.
    url = offsite.presign_url(
        "GET", "https", "examplebucket.s3.amazonaws.com", "/test.txt",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1", now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc),
        expires=86400)
    assert url.startswith("https://examplebucket.s3.amazonaws.com/test.txt?"
                          "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential="
                          "AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request")
    assert url.endswith(
        "X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


def test_presign_refuses_expiry_past_seven_days():
    try:
        offsite.presign_url("GET", "https", "h", "/k", access_key_id="a",
                            secret_access_key="s", region="auto",
                            now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                            expires=offsite.MAX_PRESIGN_SECONDS + 1)
    except offsite.OffsiteError:
        return
    raise AssertionError("an 8-day presign was accepted")


def test_share_uploads_as_html_keeps_history_and_links():
    root = _tree({"inventory/s/review_card.html": b"<p>v1</p>", "config.yaml": b"SECRET"})
    remote = FakeRemote()
    page = root / "inventory/s/review_card.html"
    url = offsite.share(remote, root, page, offsite.DEFAULT_INCLUDE, DP, "history/T/", 3600)
    assert remote.objs[DP + "inventory/s/review_card.html"] == b"<p>v1</p>"
    assert remote.types[DP + "inventory/s/review_card.html"] == "text/html; charset=utf-8"
    assert url == (f"https://bucket/{DP}inventory/s/review_card.html"
                   "?expires=3600&type=text/html")
    page.write_bytes(b"<p>v2</p>")
    offsite.share(remote, root, page, offsite.DEFAULT_INCLUDE, DP, "history/T2/", 3600)
    assert remote.objs["history/T2/inventory/s/review_card.html"] == b"<p>v1</p>"
    assert remote.objs[DP + "inventory/s/review_card.html"] == b"<p>v2</p>"


def test_share_refuses_files_outside_listing_data():
    root = _tree({"config.yaml": b"SECRET", "inventory/s/a.html": b"x"})
    for bad in (root / "config.yaml", Path(tempfile.mkdtemp()) / "elsewhere.html"):
        try:
            offsite.share(FakeRemote(), root, bad, offsite.DEFAULT_INCLUDE, DP, "h/", 60)
        except offsite.OffsiteError:
            continue
        raise AssertionError(f"shared {bad}")


def test_selects_listing_data_only():
    root = _tree({
        "listings_ledger.csv": b"a", "listings_ledger-junk.csv": b"b",
        "listings_ledger.csv.backup-20261008-232617": b"b2",
        "sales_ledger.csv": b"c", "inventory/shoot1/IMG_1.jpg": b"d",
        "inventory/shoot1/IMG_2.JPG": b"d", "inventory/shoot1/clip.mov": b"v",
        "inventory/_ebay_photos/206566144144.jpg": b"p",
        "inventory/shoot1/draft.md": b"e", "inventory/shoot1/review_card.html": b"r",
        "inventory/shoot1/.prep/notes.json": b"n", "inventory/shoot1/.picasa.ini": b"i",
        "reports/x.json": b"f", "reports/chart.png": b"img",
        "pick_lists/pick_06-1.html": b"pk", "pick_lists/picks_2026-10-05.pdf": b"pdf",
        "inventory_sheet-junk.csv": b"s", ".inventory_live.json": b"l",
        "ledger_reconcile_report-junk.json": b"rr",
        "config.yaml": b"SECRET", "lib/offsite.py": b"code",
        "inventory/shoot1/__pycache__/x.pyc": b"g", ".offsite_conflicts/old.csv": b"h",
    })
    got = offsite.local_files(root, offsite.DEFAULT_INCLUDE)
    assert got == [".inventory_live.json",
                   "inventory/shoot1/.prep/notes.json", "inventory/shoot1/draft.md",
                   "inventory/shoot1/review_card.html", "inventory_sheet-junk.csv",
                   "ledger_reconcile_report-junk.json",
                   "listings_ledger-junk.csv", "listings_ledger.csv",
                   "listings_ledger.csv.backup-20261008-232617",
                   "pick_lists/pick_06-1.html", "pick_lists/picks_2026-10-05.pdf",
                   "reports/x.json", "sales_ledger.csv"]


def test_selected_is_per_level_and_skips_media():
    inc = offsite.DEFAULT_INCLUDE
    assert offsite.selected("inventory/a/b/draft.md", inc)
    assert not offsite.selected("inventory/a/IMG.HEIC", inc)
    assert not offsite.selected("old/listings_ledger.csv", inc)   # root-level glob only
    assert not offsite.selected("config.yaml", inc)
    assert offsite.selected("letters/a.txt", inc + ("letters/**",))


def test_push_uploads_then_is_in_sync():
    root = _tree({"listings_ledger.csv": b"v1", "inventory/s/draft.md": b"d"})
    r = FakeRemote()
    plan = _plan(root, r)
    assert sorted(plan.local_only) == ["inventory/s/draft.md", "listings_ledger.csv"]
    assert offsite.push(r, root, plan, DP, "history/T/") == []
    again = _plan(root, r)
    assert again.same == 2 and not again.local_only and not again.changed


def test_push_keeps_old_version_in_history():
    root = _tree({"listings_ledger.csv": b"good ledger"})
    r = FakeRemote()
    offsite.push(r, root, _plan(root, r), DP, "history/T1/")
    (root / "listings_ledger.csv").write_bytes(b"truncated")
    plan = _plan(root, r)
    assert plan.changed == ["listings_ledger.csv"]
    offsite.push(r, root, plan, DP, "history/T2/")
    assert r.objs["data/listings_ledger.csv"] == b"truncated"
    assert r.objs["history/T2/listings_ledger.csv"] == b"good ledger"


def test_local_deletion_never_deletes_remote_and_pull_restores():
    root = _tree({"sales_ledger.csv": b"revenue", "inventory/s/draft.md": b"dd"})
    r = FakeRemote()
    offsite.push(r, root, _plan(root, r), DP, "history/T/")
    (root / "inventory/s/draft.md").unlink()
    plan = _plan(root, r)
    assert plan.remote_only == ["inventory/s/draft.md"]
    offsite.push(r, root, plan, DP, "history/T2/")
    assert "data/inventory/s/draft.md" in r.objs
    assert offsite.prunable(r, "", offsite.DEFAULT_INCLUDE) == []
    offsite.pull(r, root, plan, DP, overwrite=False)
    assert (root / "inventory/s/draft.md").read_bytes() == b"dd"


def test_photos_already_in_bucket_are_not_restored_and_prune_removes_them():
    root = _tree({"inventory/s/draft.md": b"d", "inventory/s/IMG.jpg": b"px"})
    r = FakeRemote()
    r.objs.update({"data/inventory/s/IMG.jpg": b"px",
                   "history/T0/inventory/s/IMG.jpg": b"old px",
                   "history/T0/listings_ledger.csv": b"old ledger"})
    plan = _plan(root, r)
    assert plan.local_only == ["inventory/s/draft.md"]
    assert plan.unselected == ["inventory/s/IMG.jpg"] and plan.remote_only == []
    offsite.push(r, root, plan, DP, "history/T/")
    keys = offsite.prunable(r, "", offsite.DEFAULT_INCLUDE)
    assert keys == ["data/inventory/s/IMG.jpg", "history/T0/inventory/s/IMG.jpg"]
    assert offsite.prune(r, keys) == []
    assert sorted(r.objs) == ["data/inventory/s/draft.md", "history/T0/listings_ledger.csv"]
    assert (root / "inventory/s/IMG.jpg").exists()                 # local untouched


def test_share_refuses_photos():
    root = _tree({"inventory/s/IMG.jpg": b"px"})
    try:
        offsite.share(FakeRemote(), root, root / "inventory/s/IMG.jpg",
                      offsite.DEFAULT_INCLUDE, DP, "h/", 60)
    except offsite.OffsiteError:
        return
    raise AssertionError("shared a photo")


def test_pull_leaves_differing_local_file_unless_overwrite():
    root = _tree({"listings_ledger.csv": b"bucket copy"})
    r = FakeRemote()
    offsite.push(r, root, _plan(root, r), DP, "history/T/")
    (root / "listings_ledger.csv").write_bytes(b"local edit")
    plan = _plan(root, r)
    offsite.pull(r, root, plan, DP, overwrite=False)
    assert (root / "listings_ledger.csv").read_bytes() == b"local edit"
    offsite.pull(r, root, plan, DP, overwrite=True)
    assert (root / "listings_ledger.csv").read_bytes() == b"bucket copy"
    kept = list((root / offsite.CONFLICT_DIR).rglob("listings_ledger.csv"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"local edit"


def test_missing_config_names_the_fields():
    try:
        offsite.load_offsite_config({"offsite": {"bucket": "b"}})
    except offsite.OffsiteError as e:
        assert "endpoint" in str(e) and "access_key_id" in str(e)
    else:
        raise AssertionError("expected OffsiteError")


def test_connect_tries_ipv4_before_ipv6():
    import socket
    v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1", 443, 0, 0))
    v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.0.1", 443))
    assert offsite._ipv4_first([v6, v4]) == [v4, v6]


def test_s3remote_against_local_stub_server():
    """The real HTTP client: path-style keys with spaces, Content-MD5,
    copy-source, and a paginated ListObjectsV2 response."""
    import threading
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    store: dict[str, bytes] = {}
    seen_auth = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _key(self):
            path = urllib.parse.urlsplit(self.path).path
            return urllib.parse.unquote(path.split("/", 2)[2]) if path.count("/") > 1 else ""

        def do_PUT(self):
            seen_auth.append(self.headers["Authorization"])
            src = self.headers.get("x-amz-copy-source")
            if src:
                store[self._key()] = store[urllib.parse.unquote(src.split("/", 2)[2])]
            else:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                import base64
                assert base64.b64decode(self.headers["Content-MD5"]) == hashlib.md5(body).digest()
                store[self._key()] = body
            self.send_response(200)
            self.end_headers()

        def do_DELETE(self):
            store.pop(self._key(), None)
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if "list-type" in q:
                keys = sorted(k for k in store if k.startswith(q["prefix"][0]))
                start = int(q.get("continuation-token", ["0"])[0])
                page, more = keys[start:start + 2], start + 2 < len(keys)
                ns = "http://s3.amazonaws.com/doc/2006-03-01/"
                body = f'<ListBucketResult xmlns="{ns}"><IsTruncated>{str(more).lower()}</IsTruncated>'
                if more:
                    body += f"<NextContinuationToken>{start + 2}</NextContinuationToken>"
                for k in page:
                    body += (f"<Contents><Key>{k}</Key><Size>{len(store[k])}</Size>"
                             f"<ETag>&quot;{hashlib.md5(store[k]).hexdigest()}&quot;</ETag></Contents>")
                data = (body + "</ListBucketResult>").encode()
            else:
                data = store[self._key()]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = offsite.load_offsite_config({"offsite": {
            "endpoint": f"http://127.0.0.1:{srv.server_port}", "bucket": "bkt",
            "access_key_id": "k", "secret_access_key": "s"}})
        remote = offsite.S3Remote(cfg)
        root = _tree({"inventory/Estate Lot 3/draft 1.md": b"a", "listings_ledger.csv": b"bb",
                      "sales_ledger.csv": b"ccc", "inventory/Estate Lot 3/IMG 1.jpg": b"px"})
        plan = offsite.make_plan(root, cfg.include, remote.list(DP), DP,
                                 offsite.Md5Cache(root / offsite.STATE_FILE))
        assert offsite.push(remote, root, plan, DP, "history/T/") == []
        assert "data/inventory/Estate Lot 3/draft 1.md" in store
        assert "data/inventory/Estate Lot 3/IMG 1.jpg" not in store
        listed = remote.list(DP)                     # 3 keys over 2 pages
        assert len(listed) == 3
        assert listed["data/sales_ledger.csv"].etag == hashlib.md5(b"ccc").hexdigest()
        assert remote.check() == 3
        assert all(a.startswith("AWS4-HMAC-SHA256 Credential=k/") for a in seen_auth)
        (root / "listings_ledger.csv").write_bytes(b"changed")
        plan = offsite.make_plan(root, cfg.include, remote.list(DP), DP,
                                 offsite.Md5Cache(root / offsite.STATE_FILE))
        offsite.push(remote, root, plan, DP, "history/T2/")
        assert store["history/T2/listings_ledger.csv"] == b"bb"
        store["data/inventory/Estate Lot 3/IMG 1.jpg"] = b"px"      # an old push
        keys = offsite.prunable(remote, "", cfg.include)
        assert keys == ["data/inventory/Estate Lot 3/IMG 1.jpg"]
        assert offsite.prune(remote, keys) == []
        assert "data/inventory/Estate Lot 3/IMG 1.jpg" not in store
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# Connecting: bounded connect per address, IPv6 -> IPv4 fallback. No network:
# getaddrinfo and the sockets are faked.
# ---------------------------------------------------------------------------

V6 = (offsite.socket.AF_INET6, offsite.socket.SOCK_STREAM, 6, "",
      ("2001:db8::1", 443, 0, 0))
V4 = (offsite.socket.AF_INET, offsite.socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))


class FakeSock:
    """Records settimeout/connect; connects to a family in DEAD time out."""
    made: list = []
    DEAD: set = {offsite.socket.AF_INET6}

    def __init__(self, af, *_):
        self.af, self.timeouts, self.closed = af, [], False
        FakeSock.made.append(self)

    def settimeout(self, t):
        self.timeouts.append(t)

    def bind(self, addr):
        pass

    def connect(self, sa):
        self.sa = sa
        if self.af in FakeSock.DEAD:
            raise TimeoutError("timed out")

    def close(self):
        self.closed = True


def _fake_net(infos, dead=(offsite.socket.AF_INET6,)):
    from unittest import mock
    FakeSock.made, FakeSock.DEAD = [], set(dead)
    calls = []

    def gai(host, port, family=0, type=0, *a):
        calls.append(family)
        return [i for i in infos if family in (0, i[0])]

    return calls, mock.patch.multiple(offsite.socket, getaddrinfo=gai, socket=FakeSock)


def test_connect_never_waits_on_ipv6_when_ipv4_works():
    calls, patch = _fake_net([V6, V4])               # resolver lists IPv6 first
    with patch:
        s = offsite.connect(("r2.test", 443), 30, connect_timeout=5)
    assert [m.af for m in FakeSock.made] == [offsite.socket.AF_INET]
    assert s.sa == V4[4]
    assert s.timeouts == [5, 30]                      # bounded connect, then I/O


def test_connect_falls_back_to_ipv6_when_ipv4_is_dead():
    calls, patch = _fake_net([V6, V4], dead=(offsite.socket.AF_INET,))
    with patch:
        s = offsite.connect(("r2.test", 443), 30, connect_timeout=5)
    v4 = FakeSock.made[0]
    assert v4.af == offsite.socket.AF_INET and v4.closed and v4.timeouts == [5]
    assert s.af == offsite.socket.AF_INET6 and s.sa == V6[4]


def test_connect_ipv4_only_never_resolves_ipv6():
    calls, patch = _fake_net([V6, V4])
    with patch:
        s = offsite.connect(("r2.test", 443), 30, ipv4_only=True)
    assert calls == [offsite.socket.AF_INET]
    assert [m.af for m in FakeSock.made] == [offsite.socket.AF_INET] and s.sa == V4[4]


def test_connect_failure_names_every_address():
    calls, patch = _fake_net([V6, V4], dead=(offsite.socket.AF_INET, offsite.socket.AF_INET6))
    with patch:
        try:
            offsite.connect(("r2.test", 443), 30)
        except OSError as e:
            assert "192.0.2.1" in str(e) and "2001:db8::1" in str(e)
            assert "timed out" in str(e)
        else:
            raise AssertionError("expected OSError")
    assert all(m.closed for m in FakeSock.made)


def test_ipv4_only_config_option():
    base = {"endpoint": "https://x", "bucket": "b",
            "access_key_id": "k", "secret_access_key": "s"}
    assert not offsite.load_offsite_config({"offsite": base}).ipv4_only
    assert offsite.load_offsite_config({"offsite": {**base, "ipv4_only": True}}).ipv4_only


def test_s3remote_requests_survive_a_dead_ipv6_route():
    """Through urllib: the endpoint 'resolves' to a dead IPv6 address first,
    then to a local stub server. The GET must go over IPv4, promptly."""
    import socket
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest import mock

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real_socket = socket.socket
    dead_v6 = []

    def gai(host, p, family=0, type=0, *a):
        assert host == "r2.test"
        infos = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", p, 0, 0)),
                 (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p))]
        return [i for i in infos if family in (0, i[0])]

    def make_socket(af=socket.AF_INET, *a, **kw):
        if af == socket.AF_INET6:
            dead_v6.append(af)
            return FakeSock(af)
        return real_socket(af, *a, **kw)

    try:
        cfg = offsite.load_offsite_config({"offsite": {
            "endpoint": f"http://r2.test:{port}", "bucket": "b",
            "access_key_id": "k", "secret_access_key": "s"}})
        with mock.patch.object(socket, "getaddrinfo", gai), \
                mock.patch.object(socket, "socket", make_socket), \
                mock.patch.dict("os.environ", {"no_proxy": "*", "NO_PROXY": "*"}):
            t0 = time.monotonic()
            assert offsite.S3Remote(cfg).get("x") == b"ok"
            assert time.monotonic() - t0 < 5
        assert not dead_v6                       # IPv4 first: never dialled
    finally:
        srv.shutdown()
