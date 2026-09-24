#!/usr/bin/env python3
"""lib/pick_store.py — the temp store behind a pick-sheet *link* (GH #151).

A pick sheet is something a person opens, looks at, and prints. Handing over
`pick_lists/pick_<id>.html` hands over a path on one machine; this module
parks the rendered page in a short-lived store and hands back a URL that
webapp/server.py's `/pick/{token}` route serves.

    sheet = publish(html, order_ids=["03-11111-22222"])
    print(sheet.url())      # http://127.0.0.1:8770/pick/<token>

Everything here is deliberately local: the store is a directory under
`pick_lists/` (already gitignored) and the server that reads it binds to
127.0.0.1 only. That is the point of starting with the local server — the
sheet becomes a link without becoming a thing on the internet. When #31
Phase 3 puts the app on a reachable interface, or a remote bucket replaces
this directory, only `url()` and the storage calls change; the guarantees
below are what has to survive that move.

THE GUARDRAILS, and why each one is here. These sheets carry the buyer's
full name and street address (see tools/pick_list.py). Turning them into a
URL is a change in that posture, not a refactor, so:

  * Unguessable. 32 random bytes (256 bits) of `secrets.token_urlsafe`. The
    order id never appears in the path, so a link can't be guessed from an
    order number and doesn't leak one if it's seen over a shoulder.
  * Expiring. Every sheet carries `expires_at` (default 48h — long enough to
    outlive a packing session, short enough that yesterday's buyer addresses
    aren't still being served next week). `fetch()` deletes an expired sheet
    the moment it is asked for, and `purge_expired()` sweeps the rest on
    every publish, so expiry doesn't depend on anyone remembering.
  * Revocable. `revoke()` by token or by order id deletes it now.
  * Not a record. The link is ephemeral output. It is never written into a
    committed file or the listings ledger; the one place it is remembered is
    the local, gitignored poll-state file, so a re-poll can hand back the
    same link instead of minting a second one.
  * Not listed, not indexed. There is no route that enumerates the store,
    and the serving route sends `X-Robots-Tag: noindex` + `no-store`.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Under pick_lists/ so it inherits that directory's gitignore rule — buyer
# PII must never be committable, whichever half of the tool wrote it.
STORE_DIR = ROOT / "pick_lists" / ".served"

TTL_HOURS_DEFAULT = 48
TOKEN_BYTES = 32            # 256 bits — see "Unguessable" above
DEFAULT_HOST = "127.0.0.1"  # webapp/server.py binds here and nowhere else
DEFAULT_PORT = 8770         # webapp.server imports this, so there is one value

_TOKEN_RE = re.compile(r"\A[A-Za-z0-9_-]{16,128}\Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Sheet:
    """One published pick sheet: the token that addresses it, what it is for,
    and when it stops being served."""
    token: str
    order_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""
    path: Path | None = None
    # Where the sheet actually lives, when that is not the store's own copy —
    # an item's folder under inventory/ (see publish(source=...)). Recorded
    # repo-relative in the meta file; absolute here.
    source: Path | None = None

    def url(self, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> str:
        return url_for(self.token, port=port, host=host)

    def is_expired(self, now: datetime | None = None) -> bool:
        deadline = _parse_iso(self.expires_at)
        if deadline is None:
            return False     # no/garbled expiry recorded -> serve it, don't guess
        return (now or _now()) >= deadline


def url_for(token: str, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> str:
    return f"http://{host}:{port}/pick/{token}"


def valid_token(token: str) -> bool:
    """Token charset check — also the path-traversal guard. The token is the
    filename, so anything outside [A-Za-z0-9_-] never reaches the store."""
    return bool(_TOKEN_RE.match(token or ""))


def _html_path(token: str, store_dir: Path) -> Path:
    return store_dir / f"{token}.html"


def _meta_path(token: str, store_dir: Path) -> Path:
    return store_dir / f"{token}.json"


def _resolve_source(raw: str, *, root: Path | None = None) -> Path | None:
    """A `source` path — as passed to publish(), or as read back out of a meta
    file — resolved absolute, or None if it lands outside the repo.

    This is the path-traversal guard for the one field in the store that is a
    path rather than a token. Nothing but this module writes these meta files
    today, but `source` is the first value in the store that turns into a
    filesystem read *outside* the store, so it is checked on the way out as
    well as on the way in.

    `root` defaults to ROOT read at CALL time, not as a default argument that
    captures it at import. Both matter: a default argument would freeze the
    value a test or a tool cannot then repoint, and _rel_to_root() below reads
    the global too — one of the pair binding early and the other late is the
    kind of split that only shows up as a sheet mysteriously served from the
    wrong place."""
    if not raw:
        return None
    root = root if root is not None else ROOT
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _rel_to_root(path: Path, *, root: Path | None = None) -> str:
    """A resolved source as the meta file records it: repo-relative, so the
    pointer survives the checkout moving. Only ever called with a path
    _resolve_source() already vouched for against the same root, which is what
    makes the relative_to() safe."""
    root = root if root is not None else ROOT
    return path.resolve().relative_to(root).as_posix()


def _read_meta(meta_path: Path) -> Sheet | None:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("token") or meta_path.stem
    return Sheet(token=token,
                 order_ids=[str(o) for o in (data.get("order_ids") or [])],
                 created_at=data.get("created_at", ""),
                 expires_at=data.get("expires_at", ""),
                 path=_html_path(token, meta_path.parent),
                 source=_resolve_source(data.get("source") or ""))


def _delete(token: str, store_dir: Path) -> None:
    """Stop serving this sheet. Deletes only what the store owns — its own
    .html copy and the meta file. A `source` sheet's real file belongs to the
    item's folder under inventory/, and revoking a link or letting it expire
    must not reach in and delete the seller's own file."""
    for p in (_html_path(token, store_dir), _meta_path(token, store_dir)):
        try:
            p.unlink()
        except OSError:
            pass


def publish(html: str, *, order_ids: list[str] | None = None,
            ttl_hours: float = TTL_HOURS_DEFAULT,
            store_dir: Path = STORE_DIR,
            source: Path | None = None) -> Sheet:
    """Park one rendered sheet in the store and return its Sheet (token, TTL,
    url()).

    With `source`, the sheet is served FROM that file instead of from a copy
    in the store: the store keeps only the meta file, and `fetch()` reads the
    source at request time. That is how a sold order's sheet lives in the
    item's own folder under inventory/ and is still reachable by link. The
    file must already exist and must sit inside the repo; anything else falls
    back to storing the passed `html`, because a token pointing at a file
    nothing can read is a 404 with extra steps.

    What `source` deliberately does NOT change: the link is still addressed by
    an unguessable token, still expires, and is still revocable. Expiry and
    revocation delete the store's meta file only — never the seller's file in
    inventory/. So the sheet stays on disk next to the item's photos for as
    long as the item's folder does, while the URL stops working on schedule.

    Two sweeps happen first, and both are about not leaving copies of a
    buyer's address lying around: anything already expired is deleted, and
    any live sheet covering one of these same orders is replaced. One
    shipment has one live link — a re-render supersedes the last one rather
    than leaving two working URLs for the same box, one of them stale."""
    store_dir.mkdir(parents=True, exist_ok=True)
    purge_expired(store_dir=store_dir)
    for oid in (order_ids or []):
        revoke(order_id=oid, store_dir=store_dir)

    # A source that doesn't exist or sits outside the repo is not served from;
    # the sheet falls back to a stored copy so the link still answers.
    src = None
    if source is not None:
        src = _resolve_source(str(source))
        if src is not None and not src.is_file():
            src = None

    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    sheet = Sheet(token=token,
                  order_ids=[str(o) for o in (order_ids or [])],
                  created_at=_iso(now),
                  expires_at=_iso(now + timedelta(hours=ttl_hours)),
                  path=None if src else _html_path(token, store_dir),
                  source=src)
    meta = {"token": token, "order_ids": sheet.order_ids,
            "created_at": sheet.created_at,
            "expires_at": sheet.expires_at}
    if src is None:
        _html_path(token, store_dir).write_text(html, encoding="utf-8")
    else:
        meta["source"] = _rel_to_root(src)
    _meta_path(token, store_dir).write_text(json.dumps(meta, indent=1),
                                            encoding="utf-8")
    return sheet


def fetch(token: str, *, store_dir: Path = STORE_DIR) -> tuple[str | None, str]:
    """Return (html, status) where status is "ok" | "expired" | "missing".

    An expired sheet is deleted here, on the way to answering 410 — expiry
    that only happens when a sweep runs is expiry a forgotten server doesn't
    have. A token that doesn't parse is "missing", never a filesystem read.

    A sheet published with `source` is read from that file at request time, so
    the served page is whatever the item's folder holds now — re-render the
    sheet in place and the link shows the new one without republishing. If the
    source file has since been moved or deleted the sheet is "missing": the
    link outliving the file it points at is the one case where expiry isn't
    what stops it."""
    if not valid_token(token):
        return None, "missing"
    meta = _read_meta(_meta_path(token, store_dir))
    if meta is None:
        return None, "missing"
    html_path = meta.source or _html_path(token, store_dir)
    if not html_path.exists():
        return None, "missing"
    if meta.is_expired():
        _delete(token, store_dir)
        return None, "expired"
    try:
        return html_path.read_text(encoding="utf-8"), "ok"
    except OSError:
        return None, "missing"


def list_sheets(*, store_dir: Path = STORE_DIR,
                include_expired: bool = False) -> list[Sheet]:
    """Every sheet currently in the store. Local-only introspection (the
    tool's --list); there is deliberately no HTTP route that does this."""
    if not store_dir.exists():
        return []
    out = []
    for meta_path in sorted(store_dir.glob("*.json")):
        sheet = _read_meta(meta_path)
        if sheet is None:
            continue
        if not include_expired and sheet.is_expired():
            continue
        out.append(sheet)
    return out


def live_sheet_for(order_ids: list[str], *,
                   store_dir: Path = STORE_DIR) -> Sheet | None:
    """The unexpired sheet already published for exactly this shipment, if
    there is one. This is what makes a second poll hand back the same link
    instead of minting a new one for a sheet nobody has changed."""
    want = sorted(str(o) for o in order_ids)
    if not want:
        return None
    for sheet in list_sheets(store_dir=store_dir):
        if sorted(sheet.order_ids) == want:
            return sheet
    return None


def revoke(*, token: str | None = None, order_id: str | None = None,
           store_dir: Path = STORE_DIR) -> list[str]:
    """Delete now, before expiry. Returns the tokens that were removed."""
    killed: list[str] = []
    if token:
        if valid_token(token) and _meta_path(token, store_dir).exists():
            _delete(token, store_dir)
            killed.append(token)
        return killed
    if order_id:
        for sheet in list_sheets(store_dir=store_dir, include_expired=True):
            if str(order_id) in sheet.order_ids:
                _delete(sheet.token, store_dir)
                killed.append(sheet.token)
    return killed


def purge_expired(*, store_dir: Path = STORE_DIR) -> int:
    """Delete every expired sheet. Also sweeps an orphaned .html whose meta is
    gone — without a meta file nothing can say when it should stop being
    served, and a sheet nothing can expire is exactly what must not sit in a
    store holding buyer addresses."""
    if not store_dir.exists():
        return 0
    gone = 0
    for sheet in list_sheets(store_dir=store_dir, include_expired=True):
        if sheet.is_expired():
            _delete(sheet.token, store_dir)
            gone += 1
    # Orphaned .html files only — a `source` sheet has no .html here at all,
    # so it is never seen by this sweep, and its file in inventory/ is never
    # a candidate for deletion.
    for html_path in store_dir.glob("*.html"):
        if not _meta_path(html_path.stem, store_dir).exists():
            try:
                html_path.unlink()
                gone += 1
            except OSError:
                pass
    return gone


def clear(*, store_dir: Path = STORE_DIR) -> None:
    """Empty the store — every sheet, expired or not."""
    shutil.rmtree(store_dir, ignore_errors=True)


def server_is_up(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST,
                 timeout: float = 1.5) -> bool:
    """Is the local app actually running? A published sheet is a live link
    only while something is serving it, and a tool that prints a dead URL
    without saying so is worse than one that prints a path."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz",
                                    timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


SERVE_HINT = ("the local app isn't running, so this link won't open yet — "
              "start it with `python -m lib.cli serve` (127.0.0.1 only) and "
              "the same link works")
