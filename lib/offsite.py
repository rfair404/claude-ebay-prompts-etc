"""Offsite copy of the listing data — ledgers, inventory/ shoots, reports.

The Windows box this pipeline ran on was wiped in 2026-10 and took every
ledger, photo and draft.md with it; there was no second copy. This module
keeps one in an S3-compatible bucket (Cloudflare R2 by default: 10 GB free,
then $0.015/GB-month, no egress fees — Backblaze B2 works the same way).

    python -m lib.cli offsite check            # config + bucket reachable?
    python -m lib.cli offsite status           # what push/pull would do
    python -m lib.cli offsite push             # dry run
    python -m lib.cli offsite push --apply     # upload new + changed files
    python -m lib.cli offsite pull --apply     # restore files missing locally
    python -m lib.cli offsite pull --apply --overwrite   # bucket wins on diffs

Safety rules, because the thing this guards against is a local deletion:

  * push NEVER deletes a remote object. A file gone locally is reported as
    "remote only" and left alone in the bucket.
  * push overwriting a changed remote object first server-side copies the
    old one to history/<UTC stamp>/<path>, so a corrupted or truncated
    ledger can't silently replace the good one.
  * pull never overwrites a differing local file unless --overwrite, and
    then moves the local copy to .offsite_conflicts/<stamp>/<path> first.
  * everything is a dry run without --apply.

The data always comes from the MAIN checkout (where inventory/ and the
ledgers live, CLAUDE.md), even when run from a worktree.

Bucket layout:  <prefix>data/<relpath>      the current copy
                <prefix>history/<stamp>/<relpath>   superseded versions

Config (config.yaml; env vars win for the two secrets):

    offsite:
      endpoint: "https://<ACCOUNT_ID>.r2.cloudflarestorage.com"
      bucket: "ebaybiz-data"
      region: "auto"                 # R2: auto.  B2: e.g. us-west-004
      access_key_id: ""              # or $EBAYBIZ_OFFSITE_ACCESS_KEY_ID
      secret_access_key: ""          # or $EBAYBIZ_OFFSITE_SECRET_ACCESS_KEY
      prefix: ""                     # optional, e.g. "ebaybiz/"
      include: []                    # extra globs, e.g. ["letters/**"]

Stdlib only (SigV4 over urllib), like the rest of lib/ outside the CLIP deps.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent

# What counts as listing data, relative to the main checkout. Directory
# entries ending "/**" take everything under them. Matches .gitignore's
# class-2 (business data) list; the regenerable class-3 caches are left out.
DEFAULT_INCLUDE = (
    "listings_ledger*.csv",           # incl. -<store> and .backup-* variants
    "sales_ledger*.csv",
    "listings_log.txt",
    "hand_listed_locations*.csv",
    ".pick_list_state*.json",
    "inventory/**",                   # photos, draft.md, phase output
    "reports/**",
    "share-inventory/**",
)
# Never uploaded, even when an include glob matches. config.yaml is not in
# the include list at all: it holds the eBay tokens AND this bucket's keys.
EXCLUDE_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini"}
EXCLUDE_SUFFIXES = (".pyc", ".part", ".tmp")

STATE_FILE = ".offsite_state.json"     # md5 cache; /.*.json is gitignored
CONFLICT_DIR = ".offsite_conflicts"
MAX_SINGLE_PUT = 5 * 1024**3 - 1       # S3 single-PUT ceiling
WORKERS = 8


class OffsiteError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OffsiteConfig:
    endpoint: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    prefix: str = ""
    include: tuple[str, ...] = DEFAULT_INCLUDE


def load_offsite_config(cfg: Optional[dict] = None) -> OffsiteConfig:
    if cfg is None:
        sys.path.insert(0, str(REPO / "lib"))
        import config as _config
        cfg = _config.load_config()
    o = cfg.get("offsite") or {}
    key_id = os.environ.get("EBAYBIZ_OFFSITE_ACCESS_KEY_ID") or o.get("access_key_id") or ""
    secret = (os.environ.get("EBAYBIZ_OFFSITE_SECRET_ACCESS_KEY")
              or o.get("secret_access_key") or "")
    missing = [k for k, v in (("endpoint", o.get("endpoint")), ("bucket", o.get("bucket")),
                              ("access_key_id", key_id), ("secret_access_key", secret)) if not v]
    if missing:
        raise OffsiteError(
            f"offsite storage not configured — missing {', '.join(missing)}.\n"
            f"Add an `offsite:` block to config.yaml (see lib/config.example.yaml\n"
            f"and docs/offsite-storage.md for the R2 setup steps).")
    prefix = str(o.get("prefix") or "")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return OffsiteConfig(
        endpoint=str(o["endpoint"]).rstrip("/"), bucket=str(o["bucket"]),
        region=str(o.get("region") or "auto"),
        access_key_id=str(key_id), secret_access_key=str(secret), prefix=prefix,
        include=DEFAULT_INCLUDE + tuple(o.get("include") or ()))


def data_root(start: Path = REPO) -> Path:
    """The main checkout — where the ledgers and inventory/ really live."""
    try:
        common = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).resolve().parent
    except (subprocess.CalledProcessError, FileNotFoundError):
        return start


# ---------------------------------------------------------------------------
# SigV4 S3 client
# ---------------------------------------------------------------------------

def _q(s: str, safe: str = "-_.~") -> str:
    return urllib.parse.quote(s, safe=safe)


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method: str, host: str, path: str, query: dict[str, str],
                  headers: dict[str, str], payload_sha256: str, *,
                  access_key_id: str, secret_access_key: str, region: str,
                  now: dt.datetime, service: str = "s3") -> dict[str, str]:
    """Return `headers` plus Host, x-amz-date, x-amz-content-sha256 and
    Authorization. `path` is already URI-encoded (S3 does not double-encode)."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    h = {k.lower(): str(v).strip() for k, v in headers.items()}
    h["host"] = host
    h["x-amz-date"] = amz_date
    h["x-amz-content-sha256"] = payload_sha256
    signed = ";".join(sorted(h))
    canonical = "\n".join([
        method, path,
        "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items())),
        "".join(f"{k}:{h[k]}\n" for k in sorted(h)),
        signed, payload_sha256])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret_access_key).encode(), day)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k_: v for k_, v in h.items()}
    out["authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, "
                            f"SignedHeaders={signed}, Signature={sig}")
    return out


@dataclass
class RemoteObject:
    size: int
    etag: str          # lowercase hex md5 for single-part uploads


class S3Remote:
    """The five calls this module needs, path-style, against any S3 API."""

    def __init__(self, cfg: OffsiteConfig):
        self.cfg = cfg
        u = urllib.parse.urlsplit(cfg.endpoint)
        self.scheme, self.host = u.scheme or "https", u.netloc
        self.base = u.path.rstrip("/")

    def _request(self, method: str, key: str = "", query: Optional[dict] = None,
                 body: bytes = b"", headers: Optional[dict] = None):
        query = query or {}
        path = f"{self.base}/{_q(self.cfg.bucket)}"
        if key:
            path += "/" + _q(key, safe="/-_.~")
        h = sigv4_headers(method, self.host, path, query, headers or {},
                          hashlib.sha256(body).hexdigest(),
                          access_key_id=self.cfg.access_key_id,
                          secret_access_key=self.cfg.secret_access_key,
                          region=self.cfg.region,
                          now=dt.datetime.now(dt.timezone.utc))
        h.pop("host")
        url = f"{self.scheme}://{self.host}{path}"
        if query:
            url += "?" + "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
        req = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None,
                                     method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            detail = e.read()[:500].decode("utf-8", "replace")
            raise OffsiteError(f"{method} {key or self.cfg.bucket}: HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise OffsiteError(f"{method} {self.cfg.endpoint}: {e.reason}") from e

    def list(self, prefix: str) -> dict[str, RemoteObject]:
        out, token = {}, None
        while True:
            q = {"list-type": "2", "prefix": prefix}
            if token:
                q["continuation-token"] = token
            with self._request("GET", query=q) as r:
                root = ET.fromstring(r.read())
            ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
            for c in root.iter(f"{ns}Contents"):
                out[c.findtext(f"{ns}Key")] = RemoteObject(
                    int(c.findtext(f"{ns}Size") or 0),
                    (c.findtext(f"{ns}ETag") or "").strip('"').lower())
            if (root.findtext(f"{ns}IsTruncated") or "").lower() != "true":
                return out
            token = root.findtext(f"{ns}NextContinuationToken")

    def put(self, key: str, data: bytes, md5_hex: str) -> None:
        md5_b64 = base64.b64encode(bytes.fromhex(md5_hex)).decode()
        self._request("PUT", key, body=data, headers={
            "content-md5": md5_b64, "content-type": "application/octet-stream"}).close()

    def copy(self, src_key: str, dst_key: str) -> None:
        src = "/" + _q(self.cfg.bucket) + "/" + _q(src_key, safe="/-_.~")
        self._request("PUT", dst_key, headers={"x-amz-copy-source": src}).close()

    def get(self, key: str) -> bytes:
        with self._request("GET", key) as r:
            return r.read()

    def check(self) -> int:
        """Round-trip a probe object; returns the object count under prefix."""
        probe = self.cfg.prefix + ".offsite_probe"
        data = dt.datetime.now(dt.timezone.utc).isoformat().encode()
        self.put(probe, data, hashlib.md5(data).hexdigest())
        if self.get(probe) != data:
            raise OffsiteError("probe object read back differently")
        return len(self.list(self.cfg.prefix + "data/"))


# ---------------------------------------------------------------------------
# Local side
# ---------------------------------------------------------------------------

def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    return (any(p in EXCLUDE_NAMES or p == CONFLICT_DIR for p in parts)
            or rel.endswith(EXCLUDE_SUFFIXES))


def local_files(root: Path, include: Iterable[str]) -> list[str]:
    """Relative POSIX paths of every file the include globs select."""
    found: set[str] = set()
    for pat in include:
        if pat.endswith("/**"):
            base = root / pat[:-3]
            if base.is_dir():
                for p in base.rglob("*"):
                    if p.is_file():
                        found.add(p.relative_to(root).as_posix())
        else:
            for p in root.glob(pat):
                if p.is_file():
                    found.add(p.relative_to(root).as_posix())
    return sorted(r for r in found if not _excluded(r))


class Md5Cache:
    """md5 per (size, mtime_ns), so a re-run doesn't re-hash every photo."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def md5(self, root: Path, rel: str) -> str:
        st = (root / rel).stat()
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
        hit = self.data.get(rel)
        if hit and hit[0] == stamp:
            return hit[1]
        h = hashlib.md5()
        with open(root / rel, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        self.data[rel] = [stamp, h.hexdigest()]
        return self.data[rel][1]

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    local_only: list[str] = field(default_factory=list)    # push uploads
    changed: list[str] = field(default_factory=list)       # differ both sides
    remote_only: list[str] = field(default_factory=list)   # pull restores
    same: int = 0
    too_big: list[str] = field(default_factory=list)


def make_plan(root: Path, include: Iterable[str], remote: dict[str, RemoteObject],
              data_prefix: str, cache: Md5Cache) -> Plan:
    plan = Plan()
    rel_remote = {k[len(data_prefix):]: v for k, v in remote.items()
                  if k.startswith(data_prefix)}
    for rel in local_files(root, include):
        size = (root / rel).stat().st_size
        if size > MAX_SINGLE_PUT:
            plan.too_big.append(rel)
            continue
        r = rel_remote.pop(rel, None)
        if r is None:
            plan.local_only.append(rel)
        elif r.size == size and (("-" in r.etag) or r.etag == cache.md5(root, rel)):
            # A multipart ETag ("-N") isn't an md5; same size is the best we
            # get. This module never writes multipart, so it only arises from
            # uploads made some other way.
            plan.same += 1
        else:
            plan.changed.append(rel)
    plan.remote_only = sorted(rel_remote)
    return plan


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parallel(fn: Callable[[str], None], rels: list[str], verb: str) -> list[str]:
    failed = []

    def one(rel):
        try:
            fn(rel)
            print(f"  {verb} {rel}")
        except (OffsiteError, OSError) as e:
            failed.append(rel)
            print(f"  FAILED {rel}: {e}", file=sys.stderr)

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(one, rels))
    return failed


def push(remote, root: Path, plan: Plan, data_prefix: str,
         history_prefix: str) -> list[str]:
    def up(rel):
        data = (root / rel).read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        if rel in changed:
            remote.copy(data_prefix + rel, history_prefix + rel)
        remote.put(data_prefix + rel, data, md5)

    changed = set(plan.changed)
    return _parallel(up, plan.local_only + plan.changed, "pushed")


def pull(remote, root: Path, plan: Plan, data_prefix: str, overwrite: bool) -> list[str]:
    conflicts = root / CONFLICT_DIR / _stamp()

    def down(rel):
        data = remote.get(data_prefix + rel)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            keep = conflicts / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), keep)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)

    return _parallel(down, plan.remote_only + (plan.changed if overwrite else []), "pulled")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _show(plan: Plan, limit: int = 20) -> None:
    def block(title, rels):
        if not rels:
            return
        print(f"{title}: {len(rels)}")
        for r in rels[:limit]:
            print(f"    {r}")
        if len(rels) > limit:
            print(f"    ... {len(rels) - limit} more")

    print(f"in sync: {plan.same}")
    block("local only (push uploads)", plan.local_only)
    block("changed (push overwrites, old copy kept in history/; "
          "pull --overwrite takes the bucket's)", plan.changed)
    block("remote only (pull restores; push never deletes)", plan.remote_only)
    block("too big for a single PUT (skipped)", plan.too_big)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ebz offsite", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("check", "status", "push", "pull"))
    ap.add_argument("--apply", action="store_true", help="actually transfer (default: dry run)")
    ap.add_argument("--overwrite", action="store_true",
                    help="pull: replace differing local files (local copy kept in "
                         f"{CONFLICT_DIR}/)")
    ap.add_argument("--root", type=Path, help="data root (default: the main checkout)")
    a = ap.parse_args(argv)

    try:
        cfg = load_offsite_config()
        remote = S3Remote(cfg)
        root = (a.root or data_root()).resolve()
        if a.action == "check":
            n = remote.check()
            print(f"ok — {cfg.endpoint}/{cfg.bucket}: write/read round-trip passed, "
                  f"{n} data object(s) stored")
            return 0

        data_prefix = cfg.prefix + "data/"
        cache = Md5Cache(root / STATE_FILE)
        plan = make_plan(root, cfg.include, remote.list(data_prefix), data_prefix, cache)
        cache.save()
        print(f"root: {root}\nbucket: {cfg.bucket} ({cfg.endpoint})")
        _show(plan)
        if a.action == "status":
            return 0
        if not a.apply:
            print(f"\ndry run — re-run with --apply to {a.action}")
            return 0
        if a.action == "push":
            failed = push(remote, root, plan, data_prefix,
                          f"{cfg.prefix}history/{_stamp()}/")
        else:
            failed = pull(remote, root, plan, data_prefix, a.overwrite)
        if failed:
            print(f"\n{len(failed)} file(s) failed — re-run to retry", file=sys.stderr)
            return 1
        return 0
    except OffsiteError as e:
        print(f"offsite: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
