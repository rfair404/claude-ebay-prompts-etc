"""ledger_store — the one write API for the ledgers (GH #380, Phase 1).

    ledger_store.update("listings", fn, store="junk")
    ledger_store.update("client_ledger", fn, intake="CONSIGN-K01")

`fn(rows) -> rows` is pure: it gets a copy of the ledger's rows (dicts) and
returns the rows to write. Readers are unchanged; they keep reading the CSV,
which this module keeps current as a cache.

Two modes, picked by config.yaml:

  local   No `ledger_store:` block. Read the CSV, apply fn, back it up as
          <stem>.backup-<stamp><suffix> (today's habit) and replace it
          atomically. Refused when .ledger_etags.json exists: this checkout
          has written to the cloud, and a local write would fork the record.
  cloud   `ledger_store: {url, token}`. GET the key from the allowlist Worker
          (cloud/ledger-worker/), apply fn, PUT with If-Match on the ETag it
          came with. A 412 means another writer got there first: re-GET and
          re-apply fn, up to MAX_ATTEMPTS times. Then the CSV is replaced
          atomically and gdrive_mirror.ledger_changed() nudges the Drive copy.
          The Worker keeps the previous object under history/ (90 days).

Offline (Worker unreachable or 5xx): the write lands in the CSV and the key is
marked pending in .ledger_etags.json with the ETag the CSV was based on.
`ebz ledger push` uploads it if the cloud has not moved since; otherwise it
stops and names the rebuild from eBay. A pending key is pushed first by the
next update(), so an offline write is never silently overwritten.

CLI (`python -m lib.cli ledger ...`): check, pull, push, seed, history,
restore. Every command that writes to the cloud is a dry run without --apply,
except push, whose only job is to upload writes already made.

LEDGERS is the allowlist and must equal ALLOWED in cloud/ledger-worker/
worker.js (tests/test_ledger_store.py checks). Adding a key means changing
both and Russell redeploying the Worker. inventory_sheet (rebuilt from eBay)
and clients.csv (client names) are deliberately not on it.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import stores  # noqa: E402  per-store file names (#156)

REPO = LIB.parent

# The allowlist. Keys are the local file names, so a key reads as the file it
# backs. Per store: default keeps the bare name, a named store gets -<store>.
# Consignment ledgers are keyed by client code only, never a client name.
LEDGERS = (
    "listings_ledger.csv",
    "sales_ledger.csv",
    "unwound_orders.csv",
    "listings_ledger-junk.csv",
    "sales_ledger-junk.csv",
    "unwound_orders-junk.csv",
    "client_ledger-K01.csv",
)

# update() name -> the per-store base file name
STORE_LEDGERS = {
    "listings": "listings_ledger.csv",
    "sales": "sales_ledger.csv",
    "unwound": "unwound_orders.csv",
}
CLIENT_LEDGER = "client_ledger"
CLIENT_LEDGER_FILE = "client_ledger.csv"   # tools/consign.py LEDGER_NAME
CLIENT_KEY_PREFIX = "client_ledger-"

# What to run when an offline write can't be pushed because the cloud moved.
REBUILD = {
    "listings_ledger": "python -m lib.cli --store {store} reconcile --apply",
    "sales_ledger": "python lib/sync_actuals.py --store {store} --apply",
    "unwound_orders": "python lib/sync_actuals.py --store {store} --apply",
    "client_ledger": "python -m lib.cli consign sync --apply",
}

STATE_FILE = ".ledger_etags.json"      # /.*.json is gitignored
TOKEN_ENV = "EBAYBIZ_LEDGER_TOKEN"
MAX_ATTEMPTS = 5
MAX_BYTES = 5 * 1024 * 1024            # the Worker's cap
PROBE_KEY = "inventory_sheet.csv"      # never allowlisted: `check` expects 403
TIMEOUT = 20


class LedgerStoreError(RuntimeError):
    pass


class NotAllowed(LedgerStoreError):
    """A key that is not on the allowlist."""


class Refused(LedgerStoreError):
    """Cloud state exists but config has no ledger_store: block."""


class Conflict(LedgerStoreError):
    """412 retries exhausted, or a pending offline write the cloud has moved past."""


class Offline(LedgerStoreError):
    """The Worker could not be reached (or answered 5xx)."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)   # lower-case names
    body: bytes = b""


class HttpTransport:
    """GET/PUT against the Worker. Raises Offline when it can't be reached."""

    def __init__(self, url: str, token: str, timeout: float = TIMEOUT):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def __call__(self, method: str, path: str, body: Optional[bytes] = None,
                 headers: Optional[dict] = None) -> Response:
        h = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return Response(r.status, {k.lower(): v for k, v in r.headers.items()},
                                r.read())
        except urllib.error.HTTPError as e:
            resp = Response(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read())
        except (urllib.error.URLError, OSError) as e:
            raise Offline(f"ledger Worker unreachable: {e}") from None
        if resp.status >= 500:
            raise Offline(f"ledger Worker answered {resp.status}")
        return resp


def key_path(key: str) -> str:
    return "/k/" + urllib.parse.quote(key, safe="")


# ---------------------------------------------------------------------------
# CSV + files
# ---------------------------------------------------------------------------

def parse_csv(data: bytes) -> tuple[list[str], list[dict]]:
    r = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    rows = [{k: (v if v is not None else "") for k, v in row.items() if k is not None}
            for row in r]
    return list(r.fieldnames or []), rows


def to_csv(fields: list[str], rows: list[dict]) -> bytes:
    """Existing columns keep their order; a column fn added goes on the end."""
    cols = list(fields)
    for row in rows:
        cols += [k for k in row if k not in cols]
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    """Write beside the target, fsync, then os.replace: a reader sees the old
    file or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read(path: Path) -> tuple[list[str], list[dict]]:
    return parse_csv(path.read_bytes()) if path.exists() else ([], [])


def _apply(fn: Callable[[list[dict]], list[dict]], rows: list[dict]) -> list[dict]:
    out = fn([dict(r) for r in rows])
    if out is None:
        raise LedgerStoreError("update fn returned None — it must return the rows to write")
    return list(out)


def _gdrive_ledger_changed(path: Path) -> None:
    import gdrive_mirror
    gdrive_mirror.ledger_changed(path)


def _consign():
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import consign
    return consign


def _client_code(folder: Path) -> str:
    return _consign().intake_client(folder)


def _client_dir(code: str) -> Optional[Path]:
    """The one intake folder holding client `code`'s ledger, or None."""
    dirs = [d for d in _consign().client_dirs(code) if (d / CLIENT_LEDGER_FILE).exists()]
    return dirs[0] if len(dirs) == 1 else None


def _rebuild_hint(key: str) -> str:
    stem = Path(key).stem
    for base, cmd in REBUILD.items():
        if stem == base or stem.startswith(base + "-"):
            store = stem[len(base) + 1:] if base != CLIENT_LEDGER else ""
            return cmd.format(store=store or stores.DEFAULT_STORE)
    return "rebuild it from eBay"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_ledger_config(cfg: Optional[dict] = None) -> Optional[dict]:
    """{url, token} from config.yaml's `ledger_store:`, or None for local mode."""
    if cfg is None:
        import config as _config
        cfg = _config.load_config()
    block = (cfg or {}).get("ledger_store")
    if not block:
        return None
    url = str(block.get("url") or "").strip()
    token = os.environ.get(TOKEN_ENV) or str(block.get("token") or "").strip()
    missing = [k for k, v in (("url", url), ("token", token)) if not v]
    if missing:
        raise LedgerStoreError(
            f"ledger_store: in config.yaml is missing {', '.join(missing)} — "
            f"re-run cloud/ledger-worker/setup.sh --phase 1")
    return {"url": url, "token": token}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class LedgerStore:
    """`transport=None` is local mode. `root` holds the store ledgers and the
    state file; `mirror(path)` runs after each successful write."""

    def __init__(self, *, root: Path, transport=None,
                 mirror: Callable[[Path], None] = _gdrive_ledger_changed,
                 client_dir: Callable[[str], Optional[Path]] = _client_dir,
                 out=sys.stderr):
        self.root = Path(root)
        self.transport = transport
        self.mirror = mirror
        self.client_dir = client_dir
        self.out = out

    @property
    def cloud(self) -> bool:
        return self.transport is not None

    # -- state ------------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    def state(self) -> dict:
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            s = {}
        s.setdefault("etags", {})
        s.setdefault("pending", {})
        return s

    def _save(self, s: dict) -> None:
        atomic_write(self.state_path, json.dumps(s, indent=2, sort_keys=True).encode())

    def _set_etag(self, key: str, etag: Optional[str], *, clear_pending: bool) -> None:
        s = self.state()
        if etag:
            s["etags"][key] = etag
        if clear_pending:
            s["pending"].pop(key, None)
        self._save(s)

    # -- names ------------------------------------------------------------
    def resolve(self, name: str, store: Optional[str] = None,
                intake=None) -> tuple[str, Path]:
        """(allowlisted key, local CSV path) for an update() target."""
        if name == CLIENT_LEDGER:
            if not intake:
                raise LedgerStoreError("client_ledger needs intake= (the CONSIGN-* folder)")
            folder = Path(intake)
            if not folder.is_absolute():
                folder = self.root / "inventory" / folder
            code = _client_code(folder)
            if not code:
                raise LedgerStoreError(f"{folder.name} names no client; can't key its ledger")
            key, path = f"{CLIENT_KEY_PREFIX}{code}.csv", folder / CLIENT_LEDGER_FILE
        elif name in STORE_LEDGERS:
            if intake:
                raise LedgerStoreError(f"{name} takes store=, not intake=")
            key = stores.store_file(STORE_LEDGERS[name], store).name
            env = os.environ.get("EBAYBIZ_LISTINGS_LEDGER")
            path = Path(env) if (name == "listings" and env) else self.root / key
        else:
            raise LedgerStoreError(
                f"unknown ledger {name!r} — one of {', '.join([*STORE_LEDGERS, CLIENT_LEDGER])}")
        if key not in LEDGERS:
            raise NotAllowed(f"{key} is not on the ledger allowlist (lib/ledger_store.LEDGERS)")
        return key, path

    def path_for_key(self, key: str) -> Optional[Path]:
        """Local cache path for a key, or None when it can't be placed."""
        if key.startswith(CLIENT_KEY_PREFIX):
            d = self.client_dir(Path(key).stem[len(CLIENT_KEY_PREFIX):])
            return d / CLIENT_LEDGER_FILE if d else None
        return self.root / key

    # -- the write API ----------------------------------------------------
    def update(self, name: str, fn, *, store: Optional[str] = None, intake=None) -> list[dict]:
        key, path = self.resolve(name, store, intake)
        if not self.cloud:
            return self._local(path, fn)
        if key in self.state()["pending"]:
            try:
                self._push_key(key, path)
            except Offline:
                return self._offline(key, path, fn)
        for _ in range(MAX_ATTEMPTS):
            try:
                r = self.transport("GET", key_path(key))
            except Offline:
                return self._offline(key, path, fn)
            if r.status == 404:
                if _read(path)[1]:
                    raise Refused(
                        f"{key} is not seeded but {path} has rows; nothing written. "
                        f"Run `python -m lib.cli ledger seed --apply` first.")
                fields, rows, etag = [], [], None
            elif r.status == 200:
                (fields, rows), etag = parse_csv(r.body), r.headers.get("etag")
            else:
                raise LedgerStoreError(f"GET {key}: HTTP {r.status} {r.body[:200]!r}")
            new = _apply(fn, rows)
            data = to_csv(fields, new)
            if len(data) > MAX_BYTES:
                raise LedgerStoreError(f"{key} is {len(data)} bytes, over the 5 MB cap")
            cond = {"If-Match": etag} if etag else {"If-None-Match": "*"}
            try:
                p = self.transport("PUT", key_path(key), data,
                                   {"Content-Type": "text/csv", **cond})
            except Offline:
                return self._offline(key, path, fn)
            if p.status == 412:
                continue                      # someone else wrote; re-read, re-apply
            if p.status not in (200, 201):
                raise LedgerStoreError(f"PUT {key}: HTTP {p.status} {p.body[:200]!r}")
            atomic_write(path, data)
            self._set_etag(key, p.headers.get("etag"), clear_pending=True)
            self.mirror(path)
            return new
        raise Conflict(f"{key}: still conflicting after {MAX_ATTEMPTS} attempts; "
                       f"nothing written — run it again")

    def _local(self, path: Path, fn) -> list[dict]:
        fields, rows = _read(path)
        new = _apply(fn, rows)
        if path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}").write_bytes(
                path.read_bytes())
        atomic_write(path, to_csv(fields, new))
        self.mirror(path)
        return new

    def _offline(self, key: str, path: Path, fn) -> list[dict]:
        fields, rows = _read(path)
        new = _apply(fn, rows)
        atomic_write(path, to_csv(fields, new))
        s = self.state()
        # Keep the ETag of the FIRST offline write: that is what the cache
        # was based on, and what the cloud must still be at for push.
        s["pending"].setdefault(key, s["etags"].get(key))
        self._save(s)
        self.mirror(path)
        print(f"[ledger] offline: {key} saved locally, pending — "
              f"run `python -m lib.cli ledger push` once back online", file=self.out)
        return new

    # -- push / pull / seed -----------------------------------------------
    def _push_key(self, key: str, path: Path) -> str:
        base = self.state()["pending"].get(key)
        r = self.transport("GET", key_path(key))
        current = r.headers.get("etag") if r.status == 200 else None
        if r.status not in (200, 404):
            raise LedgerStoreError(f"GET {key}: HTTP {r.status}")
        moved = Conflict(
            f"{key}: the cloud copy changed after this machine's offline write, "
            f"so the offline write was not uploaded. Pull the cloud copy and "
            f"rebuild from eBay:\n  python -m lib.cli ledger pull --discard-pending\n"
            f"  {_rebuild_hint(key)}")
        if current != base:
            raise moved
        cond = {"If-Match": base} if base else {"If-None-Match": "*"}
        p = self.transport("PUT", key_path(key), path.read_bytes(),
                           {"Content-Type": "text/csv", **cond})
        if p.status == 412:
            raise moved
        if p.status not in (200, 201):
            raise LedgerStoreError(f"PUT {key}: HTTP {p.status}")
        etag = p.headers.get("etag")
        self._set_etag(key, etag, clear_pending=True)
        return etag

    def push(self) -> list[str]:
        """Upload every pending offline write. Raises on the first conflict."""
        out = []
        for key in sorted(self.state()["pending"]):
            path = self.path_for_key(key)
            if not path or not path.exists():
                raise LedgerStoreError(f"{key}: pending, but its local file is gone")
            self._push_key(key, path)
            out.append(f"pushed {key}")
        return out or ["nothing pending"]

    def pull(self, *, discard_pending: bool = False) -> list[str]:
        out, pending = [], self.state()["pending"]
        for key in LEDGERS:
            if key in pending and not discard_pending:
                out.append(f"skip {key}: offline write pending (push, or --discard-pending)")
                continue
            path = self.path_for_key(key)
            if path is None:
                out.append(f"skip {key}: no single local folder for it")
                continue
            r = self.transport("GET", key_path(key))
            if r.status == 404:
                out.append(f"skip {key}: not seeded")
                continue
            if r.status != 200:
                raise LedgerStoreError(f"GET {key}: HTTP {r.status}")
            atomic_write(path, r.body)
            self._set_etag(key, r.headers.get("etag"), clear_pending=True)
            self.mirror(path)
            out.append(f"pulled {key} ({len(r.body)} bytes)")
        return out

    def seed(self, *, apply: bool = False) -> list[str]:
        """First upload of each local ledger. Never overwrites a seeded key."""
        out = []
        for key in LEDGERS:
            path = self.path_for_key(key)
            if path is None or not path.exists():
                out.append(f"skip {key}: no local file")
                continue
            r = self.transport("GET", key_path(key))
            if r.status == 200:
                self._set_etag(key, r.headers.get("etag"), clear_pending=False)
                out.append(f"skip {key}: already seeded")
                continue
            if r.status != 404:
                raise LedgerStoreError(f"GET {key}: HTTP {r.status}")
            data = path.read_bytes()
            if not apply:
                out.append(f"would seed {key} ({len(data)} bytes)")
                continue
            p = self.transport("PUT", key_path(key), data,
                               {"Content-Type": "text/csv", "If-None-Match": "*"})
            if p.status not in (200, 201):
                raise LedgerStoreError(f"PUT {key}: HTTP {p.status}")
            self._set_etag(key, p.headers.get("etag"), clear_pending=False)
            out.append(f"seeded {key} ({len(data)} bytes)")
        return out

    # -- history ----------------------------------------------------------
    def history(self, key: str) -> list[dict]:
        _check_key(key)
        r = self.transport("GET", "/h/" + urllib.parse.quote(key, safe=""))
        if r.status != 200:
            raise LedgerStoreError(f"history {key}: HTTP {r.status}")
        return json.loads(r.body.decode("utf-8"))

    def restore(self, key: str, stamp: str, *, apply: bool = False) -> str:
        """Put an old version back as the current one. The version it replaces
        goes to history/ like any other PUT, so a restore can be undone."""
        _check_key(key)
        if key in self.state()["pending"]:
            raise LedgerStoreError(f"{key}: offline write pending — push or pull first")
        old = self.transport("GET", "/h/" + urllib.parse.quote(key, safe="") + "/"
                             + urllib.parse.quote(stamp, safe=""))
        if old.status != 200:
            raise LedgerStoreError(f"history {key} {stamp}: HTTP {old.status}")
        if not apply:
            return f"would restore {key} to {stamp} ({len(old.body)} bytes)"
        cur = self.transport("GET", key_path(key))
        cond = ({"If-Match": cur.headers.get("etag")} if cur.status == 200
                else {"If-None-Match": "*"})
        p = self.transport("PUT", key_path(key), old.body, {"Content-Type": "text/csv", **cond})
        if p.status not in (200, 201):
            raise LedgerStoreError(f"PUT {key}: HTTP {p.status} — nothing restored")
        path = self.path_for_key(key)
        if path is not None:
            atomic_write(path, old.body)
            self.mirror(path)
        self._set_etag(key, p.headers.get("etag"), clear_pending=True)
        return f"restored {key} to {stamp}"

    # -- check ------------------------------------------------------------
    def check(self) -> tuple[bool, list[str]]:
        if not self.cloud:
            return True, ["local mode: no ledger_store: in config.yaml"]
        ok, out = True, []
        try:
            for key in LEDGERS:
                r = self.transport("GET", key_path(key))
                if r.status == 200:
                    out.append(f"ok    {key}  {len(r.body)} bytes  etag {r.headers.get('etag')}")
                elif r.status == 404:
                    out.append(f"--    {key}  not seeded (ledger seed --apply)")
                else:
                    ok = False
                    out.append(f"FAIL  {key}  HTTP {r.status}")
            r = self.transport("GET", key_path(PROBE_KEY))
            if r.status == 403:
                out.append(f"ok    allowlist: {PROBE_KEY} refused (403)")
            else:
                ok = False
                out.append(f"FAIL  allowlist: {PROBE_KEY} answered {r.status}, expected 403")
        except Offline as e:
            return False, out + [f"FAIL  {e}"]
        pending = sorted(self.state()["pending"])
        if pending:
            out.append(f"pending offline writes: {', '.join(pending)} (ledger push)")
        return ok, out


def _check_key(key: str) -> None:
    if key not in LEDGERS:
        raise NotAllowed(f"{key} is not on the ledger allowlist")


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------

def open_store(cfg: Optional[dict] = None, *, root: Optional[Path] = None,
               transport=None) -> LedgerStore:
    """The store config.yaml asks for. Raises Refused when this checkout has
    cloud state (.ledger_etags.json) but the config lost its ledger_store:.
    Always local mode while $EBAYBIZ_LISTINGS_LEDGER redirects the ledger."""
    root = Path(root) if root else REPO
    if os.environ.get("EBAYBIZ_LISTINGS_LEDGER"):
        return LedgerStore(root=root)      # test redirection never touches the cloud
    conf = load_ledger_config(cfg)
    if conf is None:
        if (root / STATE_FILE).exists():
            raise Refused(
                f"{root / STATE_FILE} exists, so the ledgers live in the cloud, but "
                f"config.yaml has no ledger_store: block. Nothing written. Restore "
                f"the block (cloud/ledger-worker/setup.sh --phase 1 writes it).")
        return LedgerStore(root=root)
    return LedgerStore(root=root, transport=transport or HttpTransport(conf["url"], conf["token"]))


def update(name: str, fn: Callable[[list[dict]], list[dict]], *,
           store: Optional[str] = None, intake=None) -> list[dict]:
    """Apply `fn(rows) -> rows` to one ledger and return the rows written."""
    return open_store().update(name, fn, store=store, intake=intake)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ebz ledger", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="every key's state + the 403 allowlist probe")
    p = sub.add_parser("pull", help="cloud -> local CSVs")
    p.add_argument("--discard-pending", action="store_true",
                   help="overwrite keys with an unpushed offline write")
    sub.add_parser("push", help="upload pending offline writes")
    p = sub.add_parser("seed", help="first upload of the local ledgers (dry run)")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("history", help="saved versions of one key (90 days)")
    p.add_argument("key", choices=LEDGERS)
    p = sub.add_parser("restore", help="put a saved version back (dry run)")
    p.add_argument("key", choices=LEDGERS)
    p.add_argument("stamp")
    p.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    try:
        st = open_store()
        if args.cmd == "check":
            ok, lines = st.check()
            print("\n".join(lines))
            return 0 if ok else 1
        if not st.cloud:
            print("ledger: local mode (no ledger_store: in config.yaml) — nothing to do")
            return 1
        if args.cmd == "pull":
            lines = st.pull(discard_pending=args.discard_pending)
        elif args.cmd == "push":
            lines = st.push()
        elif args.cmd == "seed":
            lines = st.seed(apply=args.apply)
            if not args.apply:
                lines.append("dry run — add --apply to upload")
        elif args.cmd == "history":
            lines = [f"{v.get('stamp')}  {v.get('size')} bytes" for v in st.history(args.key)]
            lines = lines or [f"no saved versions of {args.key}"]
        else:
            lines = [st.restore(args.key, args.stamp, apply=args.apply)]
        print("\n".join(lines))
        return 0
    except LedgerStoreError as e:          # Refused, Conflict, Offline, ...
        print(f"[X] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
