"""The freight shipments themselves — one SQLite file, reports/freight.db
in the MAIN checkout (service.db_path() resolves it from a worktree too).

reports/ is gitignored (it holds buyer addresses once a shipment is booked)
and is already in `ebz offsite`'s backup set.

A shipment walks:  draft -> quoted -> booking -> booked -> picked_up
                   -> in_transit -> delivered
                   (cancelled from anywhere before delivered)

`booking` is a lock, taken atomically before a provider's book endpoint is
called (claim_booking). It stops two processes — the CLI and the web app, or
two tabs — from booking one shipment twice. If the provider call ends in a
definite error the lock is released; if the outcome is unknown (the request
may have reached the provider and the answer was lost) the shipment STAYS in
`booking` until a person checks the provider and resolves it.

Quotes live on the shipment as a JSON list. A re-quote replaces the API
quotes and keeps the manual ones (a phone quote doesn't go stale because
Warp's did).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from freight.models import FreightQuote, FreightRequest, quote_ref

STATUSES = ("draft", "quoted", "booking", "booked", "picked_up", "in_transit",
            "delivered", "cancelled")
BOOKABLE = ("draft", "quoted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    store        TEXT NOT NULL DEFAULT '',
    order_id     TEXT NOT NULL DEFAULT '',
    sku          TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    request      TEXT NOT NULL,
    quotes       TEXT NOT NULL DEFAULT '[]',
    quote_errors TEXT NOT NULL DEFAULT '{}',
    quoted_at    TEXT,
    booking      TEXT,
    buyer_paid   REAL,
    notes        TEXT NOT NULL DEFAULT '',
    events       TEXT NOT NULL DEFAULT '[]'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Shipment:
    id: int
    created_at: str
    updated_at: str
    store: str
    order_id: str
    sku: str
    title: str
    status: str
    request: FreightRequest
    quotes: list[FreightQuote] = field(default_factory=list)
    quote_errors: dict = field(default_factory=dict)
    quoted_at: Optional[str] = None
    booking: Optional[dict] = None
    buyer_paid: Optional[float] = None
    notes: str = ""
    events: list[dict] = field(default_factory=list)

    @property
    def cheapest(self) -> Optional[FreightQuote]:
        return self.quotes[0] if self.quotes else None

    @property
    def cost(self) -> Optional[float]:
        return (self.booking or {}).get("price")

    @property
    def margin(self) -> Optional[float]:
        """What the buyer paid for freight minus what it cost — the number
        that says whether a freight-by-quote offer is pricing right."""
        if self.buyer_paid is None or self.cost is None:
            return None
        return round(self.buyer_paid - self.cost, 2)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "created_at": self.created_at, "updated_at": self.updated_at,
            "store": self.store, "order_id": self.order_id, "sku": self.sku,
            "title": self.title, "status": self.status,
            "request": self.request.to_dict(),
            "quotes": [q.to_dict() for q in self.quotes],
            "quote_errors": self.quote_errors, "quoted_at": self.quoted_at,
            "booking": self.booking, "buyer_paid": self.buyer_paid,
            "notes": self.notes, "events": self.events,
            "cost": self.cost, "margin": self.margin,
        }


def _quote_basis(req: FreightRequest) -> tuple:
    """What a price depends on. Contact details and street lines don't."""
    return (req.origin.zip[:5], req.destination.zip[:5], req.pickup_date,
            json.dumps([u.__dict__ for u in req.units], sort_keys=True),
            tuple(sorted(req.accessorials)), req.declared_value)


def _sort(quotes: list[FreightQuote]) -> list[FreightQuote]:
    # Cheapest first; among equal prices, bookable before indicative.
    for q in quotes:
        q.ref = q.ref or quote_ref(q)
    return sorted(quotes, key=lambda q: (q.price, not q.bookable, q.provider, q.carrier))


class FreightStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # Commit and CLOSE every time: a connection left open holds a file
        # lock on Windows, and the main checkout's copy is opened by both
        # the CLI and the web app.
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def _row(self, r: sqlite3.Row) -> Shipment:
        return Shipment(
            id=r["id"], created_at=r["created_at"], updated_at=r["updated_at"],
            store=r["store"], order_id=r["order_id"], sku=r["sku"], title=r["title"],
            status=r["status"], request=FreightRequest.from_dict(json.loads(r["request"])),
            quotes=[FreightQuote.from_dict(q) for q in json.loads(r["quotes"])],
            quote_errors=json.loads(r["quote_errors"]), quoted_at=r["quoted_at"],
            booking=json.loads(r["booking"]) if r["booking"] else None,
            buyer_paid=r["buyer_paid"], notes=r["notes"], events=json.loads(r["events"]))

    def create(self, req: FreightRequest, *, store: str = "", order_id: str = "",
               sku: str = "", title: str = "", notes: str = "",
               buyer_paid: Optional[float] = None) -> int:
        req.validate()
        now = _now()
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO shipments (created_at, updated_at, store, order_id, sku, "
                "title, request, notes, buyer_paid) VALUES (?,?,?,?,?,?,?,?,?)",
                (now, now, store, order_id, sku, title, json.dumps(req.to_dict()),
                 notes, buyer_paid))
            return int(cur.lastrowid)

    def get(self, sid: int) -> Optional[Shipment]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        return self._row(r) if r else None

    def require(self, sid: int) -> Shipment:
        s = self.get(sid)
        if s is None:
            raise KeyError(f"no freight shipment #{sid}")
        return s

    def list(self, status: Optional[str] = None, open_only: bool = False) -> list[Shipment]:
        q, args = "SELECT * FROM shipments", []
        if status:
            q, args = q + " WHERE status=?", [status]
        elif open_only:
            q += " WHERE status NOT IN ('delivered','cancelled')"
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY id DESC", args).fetchall()
        return [self._row(r) for r in rows]

    def _update(self, sid: int, **cols) -> None:
        cols["updated_at"] = _now()
        sets = ", ".join(f"{k}=?" for k in cols)
        with self._lock, self._conn() as c:
            n = c.execute(f"UPDATE shipments SET {sets} WHERE id=?",
                          (*cols.values(), sid)).rowcount
        if not n:
            raise KeyError(f"no freight shipment #{sid}")

    def update_request(self, sid: int, req: FreightRequest) -> None:
        s = self.require(sid)
        if s.status not in ("draft", "quoted"):
            raise ValueError(f"#{sid} is {s.status}; its request can't change after booking")
        req.validate()
        if _quote_basis(req) == _quote_basis(s.request):
            # Only names/streets/phones changed — what was priced is the same.
            self._update(sid, request=json.dumps(req.to_dict()))
            return
        # A changed load, lane, date or accessorial invalidates every quote,
        # manual ones included.
        self._update(sid, request=json.dumps(req.to_dict()), quotes="[]",
                     quote_errors="{}", quoted_at=None, status="draft")

    def set_quotes(self, sid: int, quotes: list[FreightQuote], errors: dict) -> Shipment:
        s = self.require(sid)
        if s.status not in ("draft", "quoted"):
            raise ValueError(f"#{sid} is {s.status}; re-quoting a booked shipment is a new shipment")
        keep = [q for q in s.quotes if q.tier == "manual"]
        merged = _sort(keep + list(quotes))
        self._update(sid, quotes=json.dumps([q.to_dict() for q in merged]),
                     quote_errors=json.dumps(errors), quoted_at=_now(),
                     status="quoted" if merged else "draft")
        return self.require(sid)

    def add_manual_quote(self, sid: int, quote: FreightQuote) -> Shipment:
        s = self.require(sid)
        if s.status not in ("draft", "quoted"):
            raise ValueError(f"#{sid} is {s.status}; it already has a booked carrier")
        quote.tier = "manual"
        quote.bookable = False
        merged = _sort(s.quotes + [quote])
        self._update(sid, quotes=json.dumps([q.to_dict() for q in merged]), status="quoted")
        return self.require(sid)

    def claim_booking(self, sid: int) -> str:
        """Atomically move draft/quoted -> booking. Returns the status it held,
        for release_booking(). Raises ValueError if someone else holds it —
        the conditional UPDATE is what makes two concurrent bookers safe,
        across processes, not just threads."""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT status FROM shipments WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise KeyError(f"no freight shipment #{sid}")
            prev = row["status"]
            if prev == "booking":
                raise ValueError(f"#{sid} already has a booking attempt in progress or of "
                                 "unknown outcome — check the provider before trying again")
            if prev not in BOOKABLE:
                raise ValueError(f"#{sid} is {prev}; nothing to book")
            n = c.execute("UPDATE shipments SET status='booking', updated_at=? "
                          "WHERE id=? AND status=?", (_now(), sid, prev)).rowcount
        if not n:
            raise ValueError(f"#{sid} changed while booking started — look at it again")
        return prev

    def release_booking(self, sid: int, prev: str, note: str) -> None:
        """A definite failure: nothing was booked. Back to `prev`."""
        s = self.require(sid)
        if s.status == "booking":
            events = s.events + [{"when": _now(), "status": "book_failed", "note": note}]
            self._update(sid, status=prev, events=json.dumps(events))

    def mark_book_unknown(self, sid: int, note: str) -> None:
        """The request may have reached the provider. Keep the lock; say why."""
        s = self.require(sid)
        events = s.events + [{"when": _now(), "status": "book_unknown", "note": note}]
        self._update(sid, events=json.dumps(events))

    def update_booking(self, sid: int, booking: dict) -> None:
        self._update(sid, booking=json.dumps(booking))

    def record_booking(self, sid: int, booking: dict) -> Shipment:
        s = self.require(sid)
        if s.status not in BOOKABLE + ("booking",):
            raise ValueError(f"#{sid} is already {s.status}")
        events = s.events + [{"when": _now(), "status": "booked",
                              "note": f"{booking.get('provider')} / {booking.get('carrier')}"}]
        self._update(sid, booking=json.dumps(booking), status="booked",
                     events=json.dumps(events))
        return self.require(sid)

    def set_status(self, sid: int, status: str, note: str = "") -> Shipment:
        if status not in STATUSES:
            raise ValueError(f"status {status!r} not one of {STATUSES}")
        s = self.require(sid)
        if s.status in ("delivered", "cancelled") and status != s.status:
            raise ValueError(f"#{sid} is {s.status}; that's final")
        if status == "booking":
            raise ValueError("`booking` is set only by a booking in progress")
        if status == "booked" and not s.booking:
            raise ValueError(f"#{sid} has no booking — use `mark-booked` to record one")
        if s.status == "booking" and status not in ("quoted", "cancelled"):
            raise ValueError(f"#{sid} has a booking attempt of unknown outcome — check the "
                             "provider, then `mark-booked` it or set it back to `quoted`")
        if status in ("picked_up", "in_transit", "delivered") and not s.booking:
            raise ValueError(f"#{sid} has no booking — book it or `mark-booked` first")
        events = s.events + [{"when": _now(), "status": status, "note": note}]
        self._update(sid, status=status, events=json.dumps(events))
        return self.require(sid)

    def set_fields(self, sid: int, **fields) -> Shipment:
        allowed = {"order_id", "sku", "title", "notes", "buyer_paid", "store"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"can't set {sorted(bad)}")
        if fields:
            self._update(sid, **fields)
        return self.require(sid)
