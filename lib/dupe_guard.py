"""dupe_guard — one physical object, one record.

The same object photographed twice (a bin walk-through frame, then its own
shoot; a second TG-7 import; an intake re-entry) used to come back as a new
catalog or client-ledger row. Every path that creates a row now scores the
candidate against the rows already on record first:

    score = description similarity            (0..1, shared words / sqrt(|a|*|b|))
          + W_FRAME  when a frame id is shared (the same TG-7 frame)
          + W_BIN    when the bin is the same
          + W_HINT   when triage's "possible duplicate of <row>" note names it
                     (a hint lifts a match into review, never on to attach)
    (capped at 1.0; the same item folder is 1.0 outright)

- one row at ATTACH or above, and no second one: the candidate is that row.
  Its frames (and folder, if the row had none) go onto the row, and the row is
  marked for an IDENTIFY re-run.
- two rows at ATTACH or above, or a best score between REVIEW and ATTACH: the
  candidate goes to the review queue (dupe_queue.csv) for the inventory
  manager. No row is created.
- below REVIEW: a new row, as before.

`--new` (force_new) makes the row anyway, and says so in its `why`.
Plain csv and re only; nothing here calls eBay or deletes anything.
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

ATTACH = 0.6
REVIEW = 0.35
W_FRAME = 0.15
W_BIN = 0.1
W_HINT = 0.25

QUEUE = "dupe_queue.csv"
QUEUE_FIELDS = ("date", "action", "target", "candidate", "bin", "photos", "folder",
                "matches", "note")
CATALOG = "catalog.csv"
LEDGER = "client_ledger.csv"
CATALOG_FIELDS = ("no", "bin", "description", "qty", "marks", "condition", "photos",
                  "inventory_folder", "why")

# Words that say nothing about which object it is.
STOP = frozenset("""
    the and with for from that this one two three four five six seven eight nine ten
    are has have its into lot set pair each per some more most rest also plus not
    any all other small large big long short about approx inch inches
    item items piece pieces shot shots frame frames photo photos bin mixed
    new old used vintage misc assorted various see still only stay stays
""".split())
_WORD = re.compile(r"[a-z]+")
_DUPE_NOTE = re.compile(r"possible duplicate", re.I)


def tokens(text: str) -> set[str]:
    """The words of a description that can tell objects apart, singular."""
    out = set()
    for w in _WORD.findall((text or "").lower()):
        if len(w) < 3 or w in STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def frames(text: str) -> set[str]:
    """Frame ids from a `photos` field: `ZZ240649.JPG ZZ240650` -> {ZZ240649, ZZ240650}."""
    return {Path(p).stem.upper() for p in re.split(r"[\s,;]+", text or "") if p.strip()}


def norm_folder(folder: str) -> str:
    f = (folder or "").strip().replace("\\", "/").strip("/")
    if f and not f.startswith("inventory/"):
        f = "inventory/" + f
    return f


def norm_bin(b: str) -> str:
    b = (b or "").strip().lower()
    b = re.sub(r"^bin[-_ ]*", "", b)
    return b.lstrip("0") or ("0" if b else "")


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


@dataclass
class Row:
    """One existing record: a catalog.csv or client_ledger.csv row."""
    source: Path
    data: dict
    fields: list = field(default_factory=list)

    @property
    def is_ledger(self) -> bool:
        return self.source.name == LEDGER

    @property
    def key(self) -> str:
        if self.is_ledger:
            return self.data.get("item_id", "")
        return f"{rel(self.source)}#{self.data.get('no', '')}"

    def aliases(self) -> list[str]:
        out = [self.key]
        if not self.is_ledger:
            out.append(f"{self.source.parent.name}/{CATALOG}#{self.data.get('no', '')}")
        f = norm_folder(self.data.get("inventory_folder", ""))
        if f:
            out.append(f)
        return [a for a in out if a and not a.endswith("#")]


@dataclass
class Match:
    score: float
    row: Row
    reasons: list

    def label(self) -> str:
        return f"{self.row.key} {self.score:.2f} ({', '.join(self.reasons)})"


@dataclass
class Decision:
    action: str            # "attach" | "review" | "new"
    matches: list          # Match, best first, every one at REVIEW or above

    @property
    def best(self) -> Optional[Match]:
        return self.matches[0] if self.matches else None


def rel(p: Path) -> str:
    """`inventory/...` path of a file, whatever the checkout it sits in."""
    parts = p.resolve().parts
    if "inventory" in parts:
        i = len(parts) - 1 - parts[::-1].index("inventory")
        return "/".join(parts[i:])
    return p.as_posix()


def _hinted(hint: str, row: Row) -> bool:
    if not hint or not _DUPE_NOTE.search(hint):
        return False
    return any(re.search(rf"(?<![\w-]){re.escape(a)}(?!\w)", hint) for a in row.aliases())


def score(cand: dict, row: Row) -> Match:
    """How sure we are that `cand` (description, bin, photos, inventory_folder,
    hint) is the object `row` already records."""
    d = row.data
    cf = norm_folder(cand.get("inventory_folder", ""))
    if cf and cf == norm_folder(d.get("inventory_folder", "")):
        return Match(1.0, row, ["same folder"])
    s = similarity(cand.get("description", ""), d.get("description", ""))
    reasons = [f"words {s:.2f}"]
    if frames(cand.get("photos", "")) & frames(d.get("photos", "")):
        s += W_FRAME
        reasons.append("shared frame")
    cb = norm_bin(cand.get("bin", ""))
    if cb and cb == norm_bin(d.get("bin", "")):
        s += W_BIN
        reasons.append("same bin")
    if _hinted(cand.get("hint", ""), row):
        # a hint is a guess: it lifts a weak match into review, never to attach
        s = max(s, min(s + W_HINT, ATTACH - 0.01))
        reasons.append("triage hint")
    return Match(round(min(s, 1.0), 2), row, reasons)


def decide(cand: dict, rows: list[Row]) -> Decision:
    ms = sorted((m for m in (score(cand, r) for r in rows) if m.score >= REVIEW),
                key=lambda m: -m.score)
    if not ms:
        return Decision("new", [])
    if ms[0].score >= ATTACH and (len(ms) == 1 or ms[1].score < ATTACH):
        return Decision("attach", ms)
    return Decision("review", ms)


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def read_rows(path: Path) -> list[Row]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh)
        names = list(r.fieldnames or [])
        return [Row(path, {k: (v or "").strip() for k, v in row.items() if k}, names)
                for row in r]


def write_rows(path: Path, fields: list, rows: list[dict], backup: bool = False) -> None:
    if backup and path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}").write_bytes(
            path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def scope_files(inventory: Path, target: Path) -> list[Path]:
    """Default rows to check a new catalog row against: the target catalog,
    every other catalog under its top folder, every camera lot's catalog and
    every consignment ledger."""
    top = target.resolve().relative_to(inventory.resolve()).parts[0]
    found = {target.resolve()}
    found.update(p.resolve() for p in (inventory / top).rglob(CATALOG))
    found.update(p.resolve() for p in inventory.glob(f"tg7-*/{CATALOG}"))
    found.update(p.resolve() for p in inventory.glob(f"CONSIGN-*/**/{LEDGER}"))
    return sorted(found)


def expand(paths: list[Path]) -> list[Path]:
    """Files as given; a folder means every catalog and ledger under it."""
    out = []
    for p in paths:
        if p.is_dir():
            out += sorted(p.rglob(CATALOG)) + sorted(p.rglob(LEDGER))
        elif p.is_file():
            out.append(p)
    return out


def attach(row: Row, cand: dict, today: str) -> bool:
    """Put the candidate's frames (and folder, if the row has none) on `row`
    and mark it for IDENTIFY. False when the row already had all of it."""
    d = row.data
    have = frames(d.get("photos", ""))
    new = [f for f in sorted(frames(cand.get("photos", ""))) if f not in have]
    cf = norm_folder(cand.get("inventory_folder", ""))
    rf = norm_folder(d.get("inventory_folder", ""))
    set_folder = bool(cf and not rf)
    other_folder = cf if cf and rf and cf != rf else ""
    if other_folder and other_folder in d.get("why", ""):
        other_folder = ""
    if not (new or set_folder or other_folder):
        return False
    if new:
        d["photos"] = " ".join(p for p in [d.get("photos", "").strip(), *new] if p)
    if set_folder:
        d["inventory_folder"] = cf
    bits = [f"re-sighted {today}"]
    if new:
        bits.append(f"frames {' '.join(new)}")
    if other_folder:
        bits.append(f"second shoot folder {other_folder}")
    bits.append("re-run IDENTIFY")
    d["why"] = "; ".join(p for p in [d.get("why", "").strip(), ", ".join(bits)] if p)
    return True


def queue(folder: Path, action: str, target: str, cand: dict, matches: list,
          note: str = "", today: Optional[str] = None) -> Path:
    """Append one line to <folder>/dupe_queue.csv (the same line twice is
    written once)."""
    path = folder / QUEUE
    line = {"date": today or date.today().isoformat(), "action": action,
            "target": target, "candidate": cand.get("description", ""),
            "bin": cand.get("bin", ""), "photos": cand.get("photos", ""),
            "folder": cand.get("inventory_folder", ""),
            "matches": "; ".join(m.label() for m in matches[:3]), "note": note}
    old = [r.data for r in read_rows(path)]
    same = ("action", "target", "candidate", "photos", "matches")
    if any(all(o.get(k, "") == line[k] for k in same) for o in old):
        return path
    write_rows(path, QUEUE_FIELDS, old + [line])
    return path


def slug(text: str, words: int = 5) -> str:
    w = [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP]
    return "-".join(w[:words]) or "item"


@dataclass
class Outcome:
    action: str            # "attached" | "queued" | "added" | "unchanged"
    key: str
    decision: Decision
    path: Optional[Path] = None   # the folder made for a new row


def add_catalog_row(catalog: Path, cand: dict, scope: list[Path], *,
                    force_new: str = "", attach_to: str = "", make_folder: bool = True,
                    today: Optional[str] = None) -> Outcome:
    """The one way a catalog.csv row gets made: score `cand` against every
    row in `scope` (plus the catalog itself), then attach, queue or add."""
    today = today or date.today().isoformat()
    catalog = catalog.resolve()
    files = list(dict.fromkeys([catalog, *(p.resolve() for p in scope)]))
    loaded = {p: read_rows(p) for p in files}
    rows = [r for p in files for r in loaded[p]]
    decision = decide(cand, rows)
    target = rel(catalog)

    if attach_to:
        pick = [r for r in rows if attach_to in r.aliases()]
        if len(pick) != 1:
            raise SystemExit(f"--attach {attach_to}: no single row with that key")
        decision = Decision("attach", [Match(1.0, pick[0], ["--attach"])])
    elif force_new:
        decision = Decision("new", decision.matches)

    if decision.action == "attach":
        row = decision.best.row
        changed = attach(row, cand, today)
        if changed:
            src = loaded[row.source]
            write_rows(row.source, row.fields, [r.data for r in src],
                       backup=row.is_ledger)
            queue(catalog.parent, "ATTACHED", row.key, cand, decision.matches,
                  "re-run IDENTIFY on this row", today)
        return Outcome("attached" if changed else "unchanged", row.key, decision)

    if decision.action == "review":
        queue(catalog.parent, "REVIEW", target, cand, decision.matches,
              "inventory manager: --attach <key> or --new", today)
        return Outcome("queued", "", decision)

    own = loaded[catalog]
    fields = own[0].fields if own else list(CATALOG_FIELDS)
    for f in CATALOG_FIELDS:
        if f not in fields:
            fields.append(f)
    no = max((int(r.data["no"]) for r in own if r.data.get("no", "").isdigit()),
             default=0) + 1
    rec = {f: "" for f in fields}
    rec.update({k: v for k, v in cand.items() if k in fields})
    rec["no"] = f"{no:02d}"
    rec["bin"] = rec.get("bin") or catalog.parent.name
    folder = None
    if make_folder and not rec.get("inventory_folder"):
        folder = catalog.parent / f"{rec['no']}-{slug(rec['description'])}"
        rec["inventory_folder"] = rel(folder)
    if force_new:
        note = f"new row forced (--new {today}): {force_new}"
        rec["why"] = "; ".join(p for p in [rec.get("why", ""), note] if p)
        if decision.matches:
            queue(catalog.parent, "FORCED_NEW", f"{target}#{rec['no']}", cand,
                  decision.matches, force_new, today)
    write_rows(catalog, fields, [r.data for r in own] + [rec])
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        intake = folder / "intake.txt"
        if not intake.exists():
            intake.write_text("".join(f"{f}: {rec.get(f, '')}\n" for f in fields),
                              encoding="utf-8")
    return Outcome("added", f"{target}#{rec['no']}", decision, folder)
