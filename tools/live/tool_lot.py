#!/usr/bin/env python3
"""TOOL LOT — one command per lot for a tool sale: the D5's frame in, a numbered draft out.

Russell puts a lot in view of the D5100 and runs, from the main checkout:

    python tools/live/tool_lot.py

Each run:
  1. grab      copies the live-view feed's latest frame (tools/d5100_webcam.sh
               writes it every 2 s). Never touches gphoto2 or the feed. A frame
               older than MAX_AGE seconds means the feed is down: refused.
  2. identify  one headless `claude -p` call with the frame inline, no tools,
               returning JSON (what's in it, condition notes, size class).
  3. draft     inventory/tool-sale-<yyyy-mm>/tool-lot-<N>/draft.md, price.txt
               and the source frame D5_0001.jpg. N = highest existing + 1,
               claimed with an atomic mkdir, so two runs at once get two numbers.
  4. record    `list_edit.py --record`: SKU stamped, ledger row DRAFTED.
               Earlier lots in the sale that were never recorded (tool-lot-1,
               made by hand) are recorded here too.
  5. prep      `lib.photo_prep.prep`: upright, crisp, no crop, rendered to
               listing/D5_0001.jpg, the draft pointed at it. It approves
               under the art director's standing tool-sale rule when the
               identify says every titled item shows, the frame is sharp and
               lit and the rig is in frame; otherwise the lot is HELD for
               the art director (no approval, no sync).
  6. sync      `list_edit.py --validate`, then, once the photos are approved,
               `list_edit.py --sync`: an UNPUBLISHED offer (an eBay draft).
               Until then the line names what it waits on. Never publishes.
               --no-sync skips this step.

Every list_edit and prep call runs in the main checkout with its .venv python,
wherever this was started: config.yaml and the ledgers live only there.

Every stage prints its time. Every lot starts at the sale's start price
(Russell, 2026-10-25: tool-sale Live lots start at $1). The tool writes it as
`price:` and puts price.txt beside the draft with Russell's `OPERATOR PRICE:`
line, which is what lib/price_gate.py accepts for a price he set himself.

Per-sale settings live in <sale>/sale.json, written on the first run: store
(default "default"), rotate (clockwise degrees to upright, default 90: the
rig shoots sideways), the start price, the two shipping policy ids (small /
large lots) and the identify model. A flag (--store, --rotate, --model,
--start-price) changes it for this run and saves it for the rest of the sale.

    python tools/live/tool_lot.py                  # the normal run
    python tools/live/tool_lot.py --no-sync        # offline: frame, identify, draft, record
    python tools/live/tool_lot.py --store junk     # this sale lists to the junk store from now on
    python tools/live/tool_lot.py --frame X.jpg    # a saved frame instead of the feed's
    python tools/live/tool_lot.py --sale-dir DIR   # another sale folder (a test, a second sale)
    python tools/live/tool_lot.py --reidentify 2 --note "that's a pry bar"
                                                   # lot 2 again: copy rewritten, same offer updated
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no flock; the atomic mkdir still keeps numbers apart
    fcntl = None

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "lib"))
import show_flow  # noqa: E402  (the feed's frame paths)
from ebay_schema import CONDITION_ENUM  # noqa: E402

LOT_PREFIX = "tool-lot-"
LOT_RE = re.compile(rf"^{LOT_PREFIX}(\d+)$")
PHOTO = "D5_0001.jpg"  # the source frame; PREP renders listing/D5_0001.jpg from it
MAX_AGE = 5.0  # seconds; the feed writes every 2 s
TITLE_MAX = 80
IDENTIFY_TIMEOUT = 90
DECIDED = "2026-10-25"  # Russell's "$1 start" for this sale's Live lots

# Live shipping (buyer-paid calculated, +$1 handling per extra item) on the
# default store, created 2026-10-22.
LIVE_SMALL = "302129021014"
LIVE_LARGE = "302129111014"
DEFAULTS = {
    "store": "default",
    "rotate": 90,  # clockwise; the rig shoots sideways (art director, 2026-10-25)
    "policy_small": LIVE_SMALL,
    "policy_large": LIVE_LARGE,
    # Russell, 2026-10-26: Opus 5.5 identifies (about 6-10 s a lot). On the
    # live frame Haiku 4.5 named items that weren't there. --model overrides.
    "model": "claude-opus-5-5",
    "start_price": "1.00",
    "price_decided": DECIDED,
}
# Size class -> (weight lb, package L x W x D in, which policy). Guesses for
# calculated shipping; meta.notes says so.
SIZES = {
    "small": (1, (10, 7, 2), "policy_small"),
    "medium": (3, (14, 10, 4), "policy_small"),
    "large": (8, (18, 14, 8), "policy_large"),
    "heavy": (20, (20, 16, 10), "policy_large"),
}

PROMPT = """You catalog a lot for an eBay tool sale from one photo. The plywood
board, the easel, the cloth behind it and the small black scale ruler are the
set, never part of the lot. Things that look like workspace clutter or props
(sticky notes, loose paper, pens, rulers, tape) go in maybe_not_for_sale, never
in items or the title. Reply with JSON only, no code fence:
{"lot": "what's in the lot for a title, most valuable first, at most 62 chars, e.g. Spring Clamps, LED Flashlight & Aviation Snips",
 "items": ["one line per item or group, with count and color, e.g. 2 spring clamps, black with orange tips"],
 "maybe_not_for_sale": ["clutter or props you can see that are probably not for sale, e.g. yellow sticky-note pad"],
 "type": "eBay item type, e.g. Tool Lot",
 "brand": "a brand you can read on the tools, else empty",
 "color": "main color",
 "condition": "USED_GOOD unless clearly better or worse: USED_EXCELLENT, USED_GOOD, USED_ACCEPTABLE or FOR_PARTS_OR_NOT_WORKING",
 "condition_notes": "one sentence on visible wear, said positively, e.g. Used shop tools with normal wear.",
 "disclosures": ["required disclosures only, e.g. Flashlight not tested. for anything powered"],
 "size": "small (fits a bubble mailer), medium (a small box), large (a big box) or heavy (over 15 lb)",
 "all_visible": true if every item you named shows at least its identifying part (cut off at the edge is fine),
 "in_focus": true if the lot is in focus,
 "well_lit": true if the lot is bright enough to see,
 "rig_in_frame": true if the plywood board or the sawhorse/easel shows in the frame}
Name only what you can see. Write about what is there, never about what is absent."""


class LotError(RuntimeError):
    pass


def _log(stage: str, secs: float, what: str) -> None:
    print(f"  {stage:<9}{secs:5.1f}s  {what}", flush=True)


# --------------------------------------------------------------- settings

def operator_price(s: dict) -> str:
    """price.txt's line for the sale's start price, in the shape
    lib/price_gate.py accepts for a price Russell set himself."""
    p = Decimal(s["start_price"])
    shown = f"${p:.0f}" if p == p.to_integral_value() else f"${p:.2f}"
    return (f'OPERATOR PRICE: Russell: "{shown} start" for tool-sale Live lots '
            f'({s["price_decided"]})')


def sale_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    return main_dir() / "inventory" / f"tool-sale-{datetime.now():%Y-%m}"


def settings(sale: Path, overrides: dict) -> dict:
    """The sale's settings: sale.json over DEFAULTS, then this run's flags,
    which are saved for the rest of the sale."""
    path = sale / "sale.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    s = {**DEFAULTS, **saved, **{k: v for k, v in overrides.items() if v is not None}}
    if overrides.get("start_price") is not None:
        s["start_price"] = f"{Decimal(str(overrides['start_price'])):.2f}"
        if s["start_price"] != {**DEFAULTS, **saved}["start_price"]:
            s["price_decided"] = f"{datetime.now():%Y-%m-%d}"  # Russell typed it today
    if s != {**DEFAULTS, **saved} or not path.exists():
        sale.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    return s


# --------------------------------------------------------------- 1. grab

def feed_frames() -> list[Path]:
    """Where the running feed writes its frame (show_flow.feed_snaps)."""
    return show_flow.feed_snaps(show_flow.feed_pids())


def upright(im: Image.Image, rotate: float) -> Image.Image:
    """The frame turned `rotate` degrees clockwise (PREP's --set-rotate angle)."""
    return im.rotate(-rotate, expand=True) if rotate % 360 else im


def grab(sources: list[Path], max_age: float | None = MAX_AGE,
         now: float | None = None) -> Image.Image:
    """The newest frame among `sources`, as shot. Refuses a frame older than
    `max_age` seconds (None: any age, for --frame)."""
    found = []
    for p in sources:
        try:
            found.append((p.stat().st_mtime, p))
        except OSError:
            pass
    if not found:
        raise LotError("no D5 frame: the live-view feed isn't running "
                       f"(looked for {', '.join(str(p) for p in sources)}). "
                       "Start it: python tools/live/show_flow.py feed start")
    mtime, path = max(found)
    age = (time.time() if now is None else now) - mtime
    if max_age is not None and age > max_age:
        raise LotError(f"D5 feed is down: its newest frame ({path}) is {age:.0f}s old. "
                       "Check the camera is on, then: python tools/live/show_flow.py feed start")
    for attempt in range(3):  # the feed rewrites the file every 2 s; a read can catch it mid-write
        try:
            im = Image.open(io.BytesIO(path.read_bytes()))
            im.load()
            break
        except (OSError, SyntaxError):
            if attempt == 2:
                raise LotError(f"can't read the D5 frame {path}")
            time.sleep(0.3)
    return im.convert("RGB")


# --------------------------------------------------------------- 2. identify

def claude_bin() -> str | None:
    """The claude CLI, found the way tools/tg7_import.sh finds it."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if found:
        return found

    def ver(p: Path):
        return [int(x) if x.isdigit() else 0 for x in p.parent.name.split(".")]
    cands = sorted(Path.home().glob(".config/Claude/claude-code/*/claude"), key=ver)
    return str(cands[-1]) if cands else None


def parse_identify(text: str) -> dict:
    """The JSON object in the model's reply (a stray code fence is fine)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise LotError(f"identify gave no JSON: {text[:200]!r}")
    try:
        data = json.loads(m.group(0))
    except ValueError as e:
        raise LotError(f"identify gave bad JSON ({e}): {text[:200]!r}") from None
    if not isinstance(data, dict) or not str(data.get("lot") or "").strip():
        raise LotError(f"identify named nothing: {text[:200]!r}")
    return data


def identify(im: Image.Image, model: str, run=None, note: str | None = None) -> dict:
    """One headless claude call with the frame inline: no tools, no settings,
    no session saved, no thinking, so it is a single fast model turn. `note`
    is Russell's correction ("that's a pry bar, not a caulk gun"), passed as
    context."""
    run = run or subprocess.run
    exe = claude_bin()
    if not exe:
        raise LotError("no claude CLI found (set CLAUDE_BIN)")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    msg = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": "Catalog this lot." + (
            f"\nRussell, who has the lot in hand, says: {note}\nTrust what he says about the items."
            if note else "")}]}}
    cmd = [exe, "-p", "--model", model, "--effort", "low", "--tools", "",
           "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
           "--disable-slash-commands", "--system-prompt", PROMPT,
           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    try:
        r = run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True,
                timeout=IDENTIFY_TIMEOUT, cwd=tempfile.gettempdir(),
                env={**os.environ, "MAX_THINKING_TOKENS": "0"})
    except subprocess.TimeoutExpired:
        raise LotError(f"identify took over {IDENTIFY_TIMEOUT}s") from None
    result = None
    for line in (r.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            result = ev
    if r.returncode or not result or result.get("is_error"):
        why = (result or {}).get("result") or (r.stderr or "").strip()[-200:] or f"exit {r.returncode}"
        raise LotError(f"identify failed: {why}")
    return parse_identify(str(result.get("result") or ""))


# --------------------------------------------------------------- 3. draft

def lot_numbers(sale: Path) -> list[int]:
    out = []
    if sale.is_dir():
        for d in sale.iterdir():
            m = LOT_RE.match(d.name)
            if m and d.is_dir():
                out.append(int(m.group(1)))
    return sorted(out)


def claim_lot(sale: Path) -> tuple[int, Path]:
    """The next lot folder, made with an atomic mkdir: a run that loses the
    race to another one takes the next number instead."""
    sale.mkdir(parents=True, exist_ok=True)
    n = (lot_numbers(sale) or [0])[-1] + 1
    while True:
        d = sale / f"{LOT_PREFIX}{n}"
        try:
            d.mkdir()
            return n, d
        except FileExistsError:
            n += 1


def title_for(n: int, lot: str) -> str:
    """TOOL LOT #<N> <what's in it>, cut at a word to 80 chars."""
    head = f"TOOL LOT #{n}"
    lot = re.sub(r"\s+", " ", lot or "").strip()
    room = TITLE_MAX - len(head) - 1
    if len(lot) > room:
        lot = lot[:room + 1].rsplit(" ", 1)[0].rstrip(" ,&-;")
    return f"{head} {lot}".strip()


def _q(v) -> str:
    """A YAML double-quoted scalar (JSON's escapes are YAML's)."""
    return json.dumps("" if v is None else str(v), ensure_ascii=False)


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(main_dir()).as_posix()
    except ValueError:
        return p.as_posix()


def build_draft(n: int, lot_dir: Path, ident: dict | None, s: dict, *,
                error: str | None = None, held: list[str] | None = None,
                now: datetime | None = None) -> str:
    """draft.md text, in the layout of the hand-made tool-lot-1."""
    now = now or datetime.now(timezone.utc)
    ident = ident or {}
    size = str(ident.get("size") or "medium").strip().lower()
    size = size if size in SIZES else "medium"
    lb, (ln, wd, dp), pol = SIZES[size]
    cond = str(ident.get("condition") or "").strip().upper()
    # The identify's grade as is. At sync, list_edit's preflight maps it to a
    # condition the category accepts and says so (82251 takes only id 3000
    # for used goods, eBay's plain "Used").
    cond = cond if cond in CONDITION_ENUM else "USED_GOOD"
    items = [str(i).strip() for i in (ident.get("items") or []) if str(i).strip()]
    disclosures = " ".join(str(d).strip() for d in (ident.get("disclosures") or []) if str(d).strip())
    wear = str(ident.get("condition_notes") or "Used, with normal wear.").strip()
    cond_desc = " ".join(x for x in (wear, disclosures) if x)

    notes = [f"TOOL LOT #{n} from one D5 live-view frame, {now.astimezone():%Y-%m-%d} "
             f"(tools/live/tool_lot.py, identify: {s['model']}).",
             f"price: ${s['start_price']} start, the sale's start price ({operator_price(s)}).",
             f"weight/dims: guesses from size class '{size}', not measured; "
             "calculated shipping charges by them."]
    if error:
        notes.append(f"IDENTIFY FAILED: {error} -- write the title and items by hand.")
    if held:
        notes.append(f"PREP HOLD for the art director: {'; '.join(held)}.")
    if not_for_sale(ident):
        notes.append(f"left out as probably not for sale: {'; '.join(not_for_sale(ident))}.")
    notes_yaml = "\n".join(f"    {line}" for line in notes)

    body = ["A lot of tools, sold together.", ""]
    if items:
        body += ["## What's Included", ""] + [f"- {i}" for i in items] + [""]
    body += ["## Condition", "", f"{wear} {disclosures} Please see the photos.".replace("  ", " "),
             "", "Thank you for looking."]

    return f"""---
template_version: v1

store: {_q(s['store'])}

meta:
  item_id:              {_q(lot_dir.name)}
  shoot_dir:            {_q(_rel(lot_dir))}
  drafted_at:           {_q(now.strftime('%Y-%m-%dT%H:%M:%SZ'))}
  notes: |-
{notes_yaml}
title: {_q(title_for(n, str(ident.get('lot') or '')))}

category_id:            ""
category_path:          "Home & Garden > Tools & Workshop Equipment > Hand Tools > Other Hand Tools"

condition: {_q(cond)}

condition_description: {_q(cond_desc)}

item_specifics:
  type:                       {_q(ident.get('type') or 'Tool Lot')}
  brand:                      {_q(ident.get('brand') or 'Unbranded')}
  upc:                        ""
  collection:                 ""
  subject:                    ""
  material:                   ""
  character_family:           ""
  occasion:                   ""
  color:                      {_q(ident.get('color') or '')}
  country_of_origin:          ""
  pattern:                    ""
  theme:                      ""
  style:                      ""
  size:                       ""
  department:                 ""
  time_period_manufactured:   ""
  finish:                     ""
  extra: {{}}

format: "FIXED_PRICE"
price: {_q(s['start_price'])}
cost_of_goods: null
quantity: 1
best_offer:
  enabled: false
  auto_decline_amount: null
  auto_accept_amount: null

shipping:
  weight:
    major_lb: {lb}
    minor_oz: 0
  package_in:
    length: {ln}
    width:  {wd}
    depth:  {dp}
  international: false
  us_only: false
  free_shipping: false
  fulfillment_policy_id: {_q(s[pol])}
  domestic_shipping_type: ""
  primary_service: ""
  handling_time_days: 1
  item_location_zip: ""

photos:
  - {PHOTO}

returns_policy_id: ""

promoted:
  general:  false
  priority: false
---

""" + "\n".join(body) + "\n"


# --------------------------------------------------------------- 4. record

class _SaleLock:
    """Serialises ledger writes between two runs on the same sale."""

    def __init__(self, sale: Path):
        self.path = sale / ".lock"

    def __enter__(self):
        self.fh = open(self.path, "a+")
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def main_dir(start: Path = REPO) -> Path:
    """The main checkout, where config.yaml and the ledgers live (GH #103).
    From a linked worktree that is the parent of git's common dir."""
    from stores import main_checkout
    return main_checkout(start)


def main_python(main: Path) -> str:
    """The main checkout's .venv python (list_edit and prep need numpy and
    friends the system python lacks), else this one."""
    venv = main / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def list_edit(args: list[str], run=None):
    """`python lib/list_edit.py <args>` run in the main checkout, whatever
    directory this was started from: a worktree's own copy would write a
    stray ledger and read no config."""
    main = main_dir()
    return (run or subprocess.run)([main_python(main), str(main / "lib" / "list_edit.py"), *args],
                                   capture_output=True, text=True, cwd=main)


def photo_prep(args: list[str], run=None):
    """`python -m lib.photo_prep.prep <args>` in the main checkout."""
    main = main_dir()
    return (run or subprocess.run)([main_python(main), "-m", "lib.photo_prep.prep", *args],
                                   capture_output=True, text=True, cwd=main)


def _lines(r) -> list[str]:
    return [ln.strip() for ln in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines()
            if ln.strip()]


def _fail_line(r) -> str:
    lines = _lines(r)
    return next((ln for ln in reversed(lines)
                 if ln.startswith(("[X]", "[FAIL]", "[ERR", "Error", "error"))),
                lines[-1] if lines else f"exit {r.returncode}")[:200]


def _record_one(d: Path, run=None) -> str:
    r = list_edit(["--record", str(d)], run=run)
    if r.returncode:
        raise LotError(f"--record {d.name}: {_fail_line(r)}")
    m = re.search(r"sku ([0-9a-f]{8})", r.stdout or "")
    return m.group(1) if m else "?"


def record(sale: Path, lot_dir: Path, run=None) -> tuple[str, list[str]]:
    """`list_edit.py --record` this lot (SKU + DRAFTED row, in the store its
    draft names) and any earlier lot in the sale that has a draft but was
    never recorded. Returns (sku, adopted names)."""
    from draft_io import parse_draft
    adopted = []
    with _SaleLock(sale):
        for n in lot_numbers(sale):
            d = sale / f"{LOT_PREFIX}{n}"
            if d == lot_dir or not (d / "draft.md").exists():
                continue
            try:
                stamped = parse_draft(d / "draft.md").get("meta.ebay_inventory_sku")
            except Exception as e:  # noqa: BLE001 — a broken draft is left for a person
                print(f"  record   skipped {d.name}: its draft.md doesn't parse ({e})")
                continue
            if not stamped:
                _record_one(d, run=run)
                adopted.append(d.name)
        sku = _record_one(lot_dir, run=run)
    return sku, adopted



# --------------------------------------------------------------- 5. prep

# The art director's standing rule for tool-sale lots (2026-10-25): turn the
# frame upright, crisp, full frame as shot (no crop; the "reads as a detail"
# flag is expected), and approve when every titled item shows at least its
# identifying part. Hold for the art director when one doesn't, the frame is
# soft or dark, or the rig isn't in frame. The identify call answers those.
CHECKS = {"all_visible": "an item named in the title isn't visible",
          "in_focus": "the frame is out of focus",
          "well_lit": "the frame is too dark",
          "rig_in_frame": "the board or sawhorse isn't in frame (rig moved?)"}


def hold_reasons(ident: dict | None) -> list[str]:
    """Why the photos can't be approved under the art director's rule; [] = approve."""
    if not ident:
        return ["identify failed, so nothing checked the frame"]
    return [why for key, why in CHECKS.items() if ident.get(key) is not True]


def prep(lot_dir: Path, rotate: float, approve: bool, run=None) -> tuple[bool, str]:
    """PREP the lot's one frame and point the draft at listing/. With
    `approve` (the art director's rule passed), sign every stage off and stamp
    the approval, re-rendering listing/ after the stages as PREP requires.
    Returns (approved, one line)."""
    f = PHOTO
    steps = [["--auto"], ["--set-rotate", f"{f}={rotate:g}"], ["--crop", f"{f}=off"], ["--apply"],
             ["--pick", "crisp"]]
    if approve:
        steps += [["--approve-stage", st] for st in ("orientation", "crop", "color")]
        steps += [["--apply"]]
    steps += [["--repoint-draft", "--apply-repoint"]]
    if approve:
        steps += [["--approve"]]
    slowest = (0.0, "")
    for args in steps:
        t = time.monotonic()
        r = photo_prep([str(lot_dir), *args], run=run)
        slowest = max(slowest, (time.monotonic() - t, " ".join(args)))
        if r.returncode:
            return False, f"prep {' '.join(args)} failed: {_fail_line(r)}"
    slow = f" (slowest step: {slowest[1]} {slowest[0]:.1f}s)"
    if approve:
        return True, "listing/D5_0001.jpg rendered, approved (art director's tool-sale rule)" + slow
    return False, "listing/D5_0001.jpg rendered, not approved" + slow


# --------------------------------------------------------------- 6. sync

def sync(lot_dir: Path, approved: bool, run=None) -> tuple[bool, str]:
    """`list_edit.py --validate`; then, with the photos approved,
    `list_edit.py --sync` (an UNPUBLISHED offer). Returns (synced, one line)."""
    r = list_edit(["--validate", str(lot_dir)], run=run)
    if r.returncode:
        issues = [ln[2:] for ln in _lines(r) if ln.startswith("- ")]
        first = issues[0] if issues else _fail_line(r)
        more = f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""
        return False, f"eBay draft not created, blocked: {first[:160]}{more}"
    if not approved:
        return False, "eBay draft not created: the photos are held for the art director"
    r = list_edit(["--sync", str(lot_dir)], run=run)
    if r.returncode == 0:
        return True, next((ln for ln in _lines(r) if ln.startswith("[OK]")), "synced")
    return False, f"eBay sync failed: {_fail_line(r)}"


# --------------------------------------------------------------- re-identify

def not_for_sale(ident: dict | None) -> list[str]:
    """What the identify set aside as workspace clutter or props."""
    return [str(x).strip() for x in ((ident or {}).get("maybe_not_for_sale") or []) if str(x).strip()]


def _split(text: str) -> tuple[list[str], str]:
    """(frontmatter lines, body) of a draft.md."""
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n(.*)$", text, re.DOTALL)
    if not m:
        raise LotError("draft.md has no frontmatter")
    return m.group(1).split("\n"), m.group(2)


def _block(lines: list[str], key: str) -> tuple[int, int] | None:
    """[start, end) of a top-level key's lines: the key line and the indented
    lines under it."""
    for i, ln in enumerate(lines):
        if ln.startswith(f"{key}:"):
            j = i + 1
            while j < len(lines) and lines[j].startswith((" ", "\t")):
                j += 1
            return i, j
    return None


def rewrite_copy(old: str, new: str, note_line: str) -> str:
    """`old` draft.md with its buyer copy (title, condition, condition
    description, item specifics, body) taken from `new`, and `note_line`
    added to meta.notes. Everything else stays: SKU, offer id, photos,
    price, shipping."""
    old_fm, _ = _split(old)
    new_fm, new_body = _split(new)
    for key in ("title", "condition", "condition_description", "item_specifics"):
        o, n = _block(old_fm, key), _block(new_fm, key)
        if n is None:
            continue
        repl = new_fm[n[0]:n[1]]
        if o is None:
            old_fm += repl
        else:
            old_fm[o[0]:o[1]] = repl
    meta = _block(old_fm, "meta")
    if meta:
        notes = next((i for i in range(*meta) if old_fm[i].strip().startswith("notes: |")), None)
        if notes is not None:
            j = notes + 1
            while j < meta[1] and old_fm[j].startswith("    "):
                j += 1
            old_fm.insert(j, f"    {note_line}")
    return "---\n" + "\n".join(old_fm) + "\n---\n" + new_body


def _items(body: str) -> list[str]:
    return [ln[2:].strip() for ln in body.splitlines() if ln.startswith("- ")]


def _photos_approved(lot_dir: Path, run=None) -> bool:
    """PREP's own gate (photo_prep.prep.assert_approved, staleness included),
    asked in the main checkout's venv: the system python has no numpy."""
    main = main_dir()
    code = ("import sys; from pathlib import Path; sys.path.insert(0, 'lib'); "
            "from photo_prep.prep import assert_approved; assert_approved(Path(sys.argv[1]))")
    r = (run or subprocess.run)([main_python(main), "-c", code, str(lot_dir)],
                                capture_output=True, text=True, cwd=main)
    return r.returncode == 0


def ebay_status(lot_dir: Path, run=None) -> str | None:
    """The lot's offer status as eBay reports it (`list_edit.py --status`,
    resolved by SKU through the Inventory API, GH #40): UNPUBLISHED,
    PUBLISHED, NO_OFFER, ... None when it can't be read. Never draft meta."""
    r = list_edit(["--status", str(lot_dir)], run=run)
    if r.returncode:
        return None
    m = re.search(r"^\s*[0-9a-f]{8}\s+(\S+)\s+offer\b", r.stdout or "", re.MULTILINE)
    return m.group(1).upper() if m else None


def reidentify(sale: Path, n: int, s: dict, note: str | None = None, no_sync: bool = False,
               run=None) -> int:
    """Identify lot N again from its own frame (Russell asked for Opus, or
    disputes what's in it), rewrite its buyer copy in place, re-record, and
    update its unpublished offer if it has one. Never a new lot, never a new
    offer, never a publish."""
    from draft_io import parse_draft
    t0 = time.monotonic()
    lot_dir = sale / f"{LOT_PREFIX}{n}"
    draft, frame = lot_dir / "draft.md", lot_dir / PHOTO
    if not (draft.exists() and frame.exists()):
        print(f"[FAIL] {_rel(lot_dir)}: no draft.md and {PHOTO} to re-identify", file=sys.stderr)
        return 2
    before = parse_draft(draft)

    t = time.monotonic()
    try:
        ident = identify(upright(Image.open(frame).convert("RGB"), s["rotate"]), s["model"],
                         run=run, note=note)
    except LotError as e:
        _log("identify", time.monotonic() - t, f"FAILED: {e}")
        print(f"[FAIL] TOOL LOT #{n}: identify failed; draft.md left as it was.")
        return 1
    _log("identify", time.monotonic() - t, f"{ident.get('lot')} ({s['model']})")
    held = hold_reasons(ident)

    line = f"re-identified {datetime.now():%Y-%m-%d} with {s['model']}"
    if note:
        line += f" (Russell: {note})"
    if held:
        line += f"; PREP HOLD for the art director: {'; '.join(held)}"
    if not_for_sale(ident):
        line += f"; left out as probably not for sale: {'; '.join(not_for_sale(ident))}"
    new = build_draft(n, lot_dir, ident, s, held=held)
    draft.write_text(rewrite_copy(draft.read_text(encoding="utf-8"), new, line + "."),
                     encoding="utf-8")
    after = parse_draft(draft)

    print(f"  title  old: {before.get('title')}")
    print(f"         new: {after.get('title')}")
    print(f"  items  old: {'; '.join(_items(before.body)) or '(none)'}")
    print(f"         new: {'; '.join(_items(after.body)) or '(none)'}")
    if not_for_sale(ident):
        print(f"  maybe not for sale, left out: {'; '.join(not_for_sale(ident))}")

    t = time.monotonic()
    try:
        sku = _record_one(lot_dir, run=run)
    except LotError as e:
        _log("record", time.monotonic() - t, f"FAILED: {e}")
        return 1
    _log("record", time.monotonic() - t, f"sku {sku} (same lot, ledger title updated)")

    if held:
        state, ok = "copy rewritten, HELD for the art director", False
    elif no_sync:
        state, ok = "copy rewritten; eBay not checked or updated (--no-sync)", True
    else:
        # Only an offer eBay itself calls UNPUBLISHED is updated. A lot
        # published from Seller Hub or the Live event can still look like a
        # draft in draft.md; syncing it would edit a live listing mid-bidding.
        t = time.monotonic()
        status = ebay_status(lot_dir, run=run)
        _log("status", time.monotonic() - t, f"eBay says {status or 'nothing (status read failed)'}")
        if status == "NO_OFFER":
            state, ok = "copy rewritten (no eBay offer yet)", True
        elif status != "UNPUBLISHED":
            state = (f"copy rewritten; eBay says {status or 'nothing readable'}, so updating "
                     "the offer is the listing manager's step")
            ok = status is not None
        else:
            t = time.monotonic()
            synced, msg = sync(lot_dir, _photos_approved(lot_dir, run=run), run=run)
            _log("sync", time.monotonic() - t, msg)
            state = "copy rewritten, offer updated" if synced else "copy rewritten, offer update failed"
            ok = synced
    print(f"  total    {time.monotonic() - t0:5.1f}s")
    tag = "[OK]" if ok else "[HOLD]" if held else "[FAIL]"
    print(f"{tag} TOOL LOT #{n}: {state}. Not published.")
    return 0 if ok else 1


# --------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-sync", action="store_true", help="skip the eBay step (offline)")
    ap.add_argument("--store", help="eBay store for this sale (saved in sale.json)")
    ap.add_argument("--rotate", type=float,
                    help="clockwise degrees that make the frame upright (saved; default 90)")
    ap.add_argument("--model", help="identify model (saved)")
    ap.add_argument("--start-price", help="every lot's start price (saved; default 1.00)")
    ap.add_argument("--frame", type=Path, help="use this image instead of the feed's frame")
    ap.add_argument("--sale-dir", help="sale folder (default inventory/tool-sale-<yyyy-mm>)")
    ap.add_argument("--reidentify", type=int, metavar="N",
                    help="re-run identify on lot N's own frame and rewrite its copy")
    ap.add_argument("--note", help="with --reidentify: Russell's correction, given to identify")
    args = ap.parse_args(argv)
    if args.note and not args.reidentify:
        ap.error("--note goes with --reidentify N")

    t0 = time.monotonic()
    sale = sale_dir(args.sale_dir)
    try:
        s = settings(sale, {"store": args.store, "rotate": args.rotate, "model": args.model,
                            "start_price": args.start_price})
    except InvalidOperation:
        print(f"[FAIL] --start-price {args.start_price!r} is not a number", file=sys.stderr)
        return 2
    if args.reidentify:
        return reidentify(sale, args.reidentify, s, note=args.note, no_sync=args.no_sync)

    t = time.monotonic()
    try:
        if args.frame:
            im = grab([args.frame], max_age=None)
        else:
            im = grab(feed_frames())
    except LotError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2
    n, lot_dir = claim_lot(sale)
    im.save(lot_dir / PHOTO, "JPEG", quality=92)  # as shot; PREP turns it upright
    _log("grab", time.monotonic() - t, f"{_rel(lot_dir / PHOTO)} {im.width}x{im.height}")

    t = time.monotonic()
    ident, err = None, None
    try:
        ident = identify(upright(im, s["rotate"]), s["model"])
        _log("identify", time.monotonic() - t, f"{ident.get('lot')} ({s['model']})")
        if not_for_sale(ident):
            print(f"  maybe not for sale, left out: {'; '.join(not_for_sale(ident))}")
    except LotError as e:
        err = str(e)
        _log("identify", time.monotonic() - t, f"FAILED: {err}")
    held = hold_reasons(ident)

    t = time.monotonic()
    draft = lot_dir / "draft.md"
    draft.write_text(build_draft(n, lot_dir, ident, s, error=err, held=held), encoding="utf-8")
    (lot_dir / "price.txt").write_text(operator_price(s) + "\n", encoding="utf-8")
    from draft_io import parse_draft
    title = parse_draft(draft).get("title")
    _log("draft", time.monotonic() - t, f"{title} (${s['start_price']})")

    t = time.monotonic()
    try:
        sku, adopted = record(sale, lot_dir)
    except Exception as e:  # noqa: BLE001 — the draft is on disk; say what didn't happen
        _log("record", time.monotonic() - t, f"FAILED: {e}")
        print(f"[FAIL] TOOL LOT #{n} drafted at {_rel(lot_dir)} but not recorded: "
              f"python lib/list_edit.py --record {_rel(lot_dir)}", file=sys.stderr)
        return 1
    extra = f" (also recorded {', '.join(adopted)})" if adopted else ""
    _log("record", time.monotonic() - t, f"sku {sku}, ledger DRAFTED, store {s['store']}{extra}")

    t = time.monotonic()
    approved, line = prep(lot_dir, s["rotate"], approve=not held)
    if held:
        line += f"; HELD for the art director: {'; '.join(held)}"
    _log("prep", time.monotonic() - t, line)

    synced = False
    if args.no_sync:
        _log("sync", 0.0, "skipped (--no-sync)")
    else:
        t = time.monotonic()
        synced, line = sync(lot_dir, approved)
        _log("sync", time.monotonic() - t, line)

    print(f"  total    {time.monotonic() - t0:5.1f}s")
    ok = approved and not err and (synced or args.no_sync)
    if synced:
        state = "eBay draft (unpublished offer)"
    elif held:
        state = "HELD for the art director, recorded"
    elif not approved:
        state = "PREP failed, recorded"
    elif args.no_sync:
        state = "local draft, recorded"
    else:
        state = "sync failed, recorded"
    tag = "[OK]" if ok else "[HOLD]" if held else "[FAIL]"
    print(f"{tag} TOOL LOT #{n}: {state} at ${s['start_price']}. Not published.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
