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
                   "?expires=3600&type=text/html; charset=utf-8")
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
        "sales_ledger.csv": b"c", "inventory/shoot1/IMG_1.jpg": b"d",
        "inventory/shoot1/draft.md": b"e", "reports/x.json": b"f",
        "config.yaml": b"SECRET", "lib/offsite.py": b"code",
        "inventory/shoot1/__pycache__/x.pyc": b"g", ".offsite_conflicts/old.csv": b"h",
    })
    got = offsite.local_files(root, offsite.DEFAULT_INCLUDE)
    assert got == ["inventory/shoot1/IMG_1.jpg", "inventory/shoot1/draft.md",
                   "listings_ledger-junk.csv", "listings_ledger.csv",
                   "reports/x.json", "sales_ledger.csv"]


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
    root = _tree({"sales_ledger.csv": b"revenue", "inventory/s/IMG.jpg": b"px"})
    r = FakeRemote()
    offsite.push(r, root, _plan(root, r), DP, "history/T/")
    (root / "inventory/s/IMG.jpg").unlink()
    plan = _plan(root, r)
    assert plan.remote_only == ["inventory/s/IMG.jpg"]
    offsite.push(r, root, plan, DP, "history/T2/")
    assert "data/inventory/s/IMG.jpg" in r.objs
    offsite.pull(r, root, plan, DP, overwrite=False)
    assert (root / "inventory/s/IMG.jpg").read_bytes() == b"px"


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
        root = _tree({"inventory/Estate Lot 3/IMG 1.jpg": b"a", "listings_ledger.csv": b"bb",
                      "sales_ledger.csv": b"ccc"})
        plan = offsite.make_plan(root, cfg.include, remote.list(DP), DP,
                                 offsite.Md5Cache(root / offsite.STATE_FILE))
        assert offsite.push(remote, root, plan, DP, "history/T/") == []
        assert "data/inventory/Estate Lot 3/IMG 1.jpg" in store
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
    finally:
        srv.shutdown()
