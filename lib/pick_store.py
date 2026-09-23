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
                 path=_html_path(token, meta_path.parent))


def _delete(token: str, store_dir: Path) -> None:
    for p in (_html_path(token, store_dir), _meta_path(token, store_dir)):
        try:
            p.unlink()
        except OSError:
            pass


def publish(html: str, *, order_ids: list[str] | None = None,
            ttl_hours: float = TTL_HOURS_DEFAULT,
            store_dir: Path = STORE_DIR) -> Sheet:
    """Park one rendered sheet in the store and return its Sheet (token, TTL,
    url()).

    Two sweeps happen first, and both are about not leaving copies of a
    buyer's address lying around: anything already expired is deleted, and
    any live sheet covering one of these same orders is replaced. One
    shipment has one live link — a re-render supersedes the last one rather
    than leaving two working URLs for the same box, one of them stale."""
    store_dir.mkdir(parents=True, exist_ok=True)
    purge_expired(store_dir=store_dir)
    for oid in (order_ids or []):
        revoke(order_id=oid, store_dir=store_dir)

    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    sheet = Sheet(token=token,
                  order_ids=[str(o) for o in (order_ids or [])],
                  created_at=_iso(now),
                  expires_at=_iso(now + timedelta(hours=ttl_hours)),
                  path=_html_path(token, store_dir))
    _html_path(token, store_dir).write_text(html, encoding="utf-8")
    _meta_path(token, store_dir).write_text(
        json.dumps({"token": token, "order_ids": sheet.order_ids,
                    "created_at": sheet.created_at,
                    "expires_at": sheet.expires_at}, indent=1),
        encoding="utf-8")
    return sheet


def fetch(token: str, *, store_dir: Path = STORE_DIR) -> tuple[str | None, str]:
    """Return (html, status) where status is "ok" | "expired" | "missing".

    An expired sheet is deleted here, on the way to answering 410 — expiry
    that only happens when a sweep runs is expiry a forgotten server doesn't
    have. A token that doesn't parse is "missing", never a filesystem read."""
    if not valid_token(token):
        return None, "missing"
    meta = _read_meta(_meta_path(token, store_dir))
    html_path = _html_path(token, store_dir)
    if meta is None or not html_path.exists():
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
