#!/usr/bin/env python3
"""dashboard — one page: backlog by stage, drafts awaiting review, live/ledger drift.

Phase 1 of #31 (see `docs/webapp-architecture.md`): extends the existing
static-HTML-generator pattern this repo already has —
`tools/sales_report.py`'s `gather()` -> `reports/sales_dashboard.html` — to the
other read-only views the issue names, beyond sales. Still just

    python -m lib.cli dashboard                # -> reports/dashboard.html
    python -m lib.cli dashboard --out FILE

writing one HTML file to open locally. No server, no job queue, no background
process, no new dependency, no network call of any kind — every gather
function below reads only what is already on disk:

  * **Backlog by stage** — one row per `inventory/<shoot>/`, entirely via
    `lib.status.gather()` (already a pure, read-only, per-shoot state read —
    reused as-is, not reimplemented; see the module-mapping table in the
    architecture doc).
  * **Drafts awaiting review** — `lib.report.collect()`, the same disk
    draft.md/draft_group.md + `listings_ledger.csv` merge the activity report
    already builds, filtered to rows with no `published_at` (the same split
    `lib.report.report_pipeline()` prints as text, kept structured here for
    HTML instead of re-parsed). Each row's local, offline-only
    `lib.list_edit.validate_draft_for_sync()` issues are attached where a
    disk draft exists, so "awaiting review" also says what would block it.
  * **Live-listing vs. ledger drift** — a new, local-only comparison between
    `inventory_sheet.csv` (the last `tools/ebay_sheet.py` sync — a snapshot
    already on disk, not re-fetched here) and `listings_ledger.csv`, using
    `tools/sales_report.py`'s own CSV-reading helper (`_rows`) and its
    CHOICE-variation dedup rule (group by `listing_id`) so the two dashboards
    can't drift apart on what counts as "one live listing". `tools/
    ledger_reconcile.py`'s `compute_drift()` is the closest existing pattern
    for this shape of diff, but it calls the live Sell API for eBay's truth —
    out of bounds for a read-only local dashboard, so this instead diffs two
    already-synced local CSVs against each other, at the cost of being only
    as fresh as the last sync of each.

Read-only, always: nothing here writes to `sales_ledger.csv`,
`listings_ledger.csv`, or any file under `inventory/`.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling tools

import list_edit as _list_edit                              # noqa: E402
import report as _report                                    # noqa: E402
import status as _status                                    # noqa: E402
from single_pass import STAGE_ORDER, STAGE_OUTPUT            # noqa: E402
from source_report import _is_backup_path                    # noqa: E402
from sales_report import STYLE, _e, _f, _itm, _money, _rows, _stat  # noqa: E402

REPORTS = REPO / "reports"
INVENTORY = REPO / "inventory"
LEDGER = REPO / "listings_ledger.csv"
LIVE_SHEET = REPO / "inventory_sheet.csv"
OUT_HTML = REPORTS / "dashboard.html"


# ---------------------------------------------------------------------------
# 1 · backlog by stage — lib.status.gather() over every shoot dir
# ---------------------------------------------------------------------------

def _looks_like_shoot(d: Path) -> bool:
    """A shoot dir is one with either a frame in it (the very first thing any
    shoot has) or one of the five stage outputs — the same two facts
    `lib.status.gather()` itself reads, not a new definition of "shoot"."""
    # Cheap, fixed-count existence checks first — a shoot past IDENTIFY
    # already has one of these and never needs the iterdir() scan below.
    if any((d / STAGE_OUTPUT[stage]).exists() for stage in STAGE_ORDER):
        return True
    try:
        return any(p.is_file() and p.suffix.lower() in _status._FRAME_EXT
                   for p in d.iterdir())
    except OSError:
        return False


def iter_shoot_dirs():
    """Every `inventory/<shoot>/` dir, backup/scratch dirs excluded (the same
    rule `lib.source_report` already uses for the same reason: a `_prepped`
    or dot-prefixed copy is not a distinct shoot).

    Walks directories only (`os.walk`, pruning excluded subtrees in place)
    rather than `rglob("*")`, which would otherwise stat every photo file
    under `inventory/` just to find directory names — and would still
    descend into a `_prepped`/dot-prefixed subtree before the exclusion
    filter got a chance to skip it."""
    root = INVENTORY
    if not root.is_dir():
        return
    found = []
    for dirpath, dirnames, _filenames in os.walk(root):
        keep = []
        for name in dirnames:
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if _is_backup_path(rel):
                continue
            keep.append(name)
        dirnames[:] = keep
        found.extend(Path(dirpath) / name for name in keep)
    for d in sorted(found):
        if _looks_like_shoot(d):
            yield d


# identify/investigate/draft's pending detail can come straight from a
# `.single_pass/ask.json` a stage wrote — free text (e.g. a maker-mark
# reading, a grouping question), not a structured field. prep's and price's
# own pending messages are always code-generated (filenames, crop reasons,
# canned "not written yet" text), never file-sourced free text — safe as-is.
_ASK_SOURCED_STAGES = frozenset({"identify", "investigate", "draft"})


def gather_backlog() -> dict:
    """One row per shoot, via `lib.status.gather()` — untouched per-shoot
    state logic, just assembled across the whole tree and bucketed by which
    stage is currently blocking each one."""
    rows = []
    for shoot in iter_shoot_dirs():
        state = _status.gather(shoot)
        try:
            state["dir"] = shoot.relative_to(REPO).as_posix()
        except ValueError:
            state["dir"] = shoot.as_posix()
        # Read the structured `stages` map directly rather than parsing
        # `next_action`'s human-readable "stage: reason" text — the same
        # first-pending-stage rule `lib.status.gather()` itself uses to
        # build that string, just not tied to its exact display phrasing.
        state["blocked_stage"] = next(
            (stage for stage in STAGE_ORDER if state["stages"][stage]["pending"]), None)
        if state["blocked_stage"] in _ASK_SOURCED_STAGES:
            state["next_action"] = (
                f"{state['blocked_stage']}: pending — run `python -m lib.cli "
                f"status {state['dir']}` for the question")
        rows.append(state)

    def _rank(r: dict) -> int:
        return STAGE_ORDER.index(r["blocked_stage"]) if r["blocked_stage"] in STAGE_ORDER \
            else len(STAGE_ORDER)

    rows.sort(key=lambda r: (_rank(r), r["dir"]))
    stage_counts = Counter(r["blocked_stage"] or "ready for review" for r in rows)
    return {"rows": rows, "stage_counts": stage_counts, "count": len(rows)}


# ---------------------------------------------------------------------------
# 2 · drafts awaiting review — lib.report.collect(), filtered to unpublished
# ---------------------------------------------------------------------------

_VOICE_PREFIX = "voice (block): "
_PARSE_ERROR_PREFIX = "draft parse error: "


def _redact_issue(issue: str, path) -> str:
    """`validate_draft_for_sync()` can return two kinds of issue string that
    carry free text from the draft itself, not just a structured field name:

    - `check_voice()`'s voice-block lines quote up to 90 chars of the
      flagged BUYER-FACING body/field text verbatim (lib/voice_check.py).
    - A `draft parse error: {e}` line embeds the underlying exception's
      message directly — for a YAML error, that's often a quoted excerpt of
      the draft's own front matter or body.

    Neither is PII, but this dashboard's own policy (matching
    lib/source_report.py / tools/sales_report.py) is structured/aggregated
    fields only, so both get redacted to "what kind of problem" plus a
    pointer to the real command for the actual text."""
    if issue.startswith(_VOICE_PREFIX):
        field = issue[len(_VOICE_PREFIX):].split(":", 1)[0].strip() or "body"
        return f"{_VOICE_PREFIX}{field} — in-hand voice phrase flagged (run `ebz voice` for the text)"
    if issue.startswith(_PARSE_ERROR_PREFIX):
        return (f"draft parse error — run "
                f"`python -m lib.cli listing --validate {path}` for details")
    return issue


def _blocking_issues(row: dict) -> list[str]:
    """Offline `validate_draft_for_sync()` issues for a disk draft — nothing
    here calls eBay. Skipped for a CHOICE group (`draft_group.md` doesn't
    have the single-item shape that function validates) or a ledger-only row
    with no draft file to read."""
    if row.get("group") or not row.get("path"):
        return []
    p = REPO / row["path"]
    if not p.exists():
        return []
    try:
        issues = _list_edit.validate_draft_for_sync(p)
    except Exception as e:                                   # noqa: BLE001
        # A parse/validation exception (e.g. a YAML error) can carry a quoted
        # excerpt of the draft's own front matter in its message. Keep this
        # page to structured/aggregated fields only — name the failure type,
        # not its text — and point at the real command for the details.
        return [f"validation error ({type(e).__name__}) — run "
                f"`python -m lib.cli listing --validate {row.get('path')}` for details"]
    return [_redact_issue(i, row.get("path")) for i in issues]


def gather_drafts() -> dict:
    """Drafted-only and synced-not-published rows — the same split
    `lib.report.report_pipeline()` already prints as text, kept structured
    here so it can render as an HTML section instead."""
    rows = [r for r in _report.collect() if not r.get("published_at")]
    for r in rows:
        r["blocking_issues"] = _blocking_issues(r)

    synced, drafted = [], []
    for r in rows:
        (synced if r.get("offer_id") or r.get("synced_at") else drafted).append(r)
    synced.sort(key=lambda r: -_f(r.get("price"), 0.0))
    drafted.sort(key=lambda r: -_f(r.get("price"), 0.0))
    return {"synced": synced, "drafted": drafted, "count": len(rows)}


# ---------------------------------------------------------------------------
# 3 · live-listing vs. ledger drift — two local CSVs, no eBay call
# ---------------------------------------------------------------------------

def _price_drift(live_price, ledger_price) -> bool:
    lp, dp = _f(live_price, None), _f(ledger_price, None)
    return lp is not None and dp is not None and abs(lp - dp) >= 0.005


def gather_drift() -> dict:
    """Compare the last-synced `inventory_sheet.csv` against
    `listings_ledger.csv`. Both are local snapshots an earlier sync already
    wrote to disk (`tools/ebay_sheet.py`, `lib.list_edit`) — this makes no
    eBay call of its own, so it is only as fresh as those snapshots are."""
    have_live_snapshot, have_ledger = LIVE_SHEET.exists(), LEDGER.exists()
    if not (have_live_snapshot and have_ledger):
        # _rows() returns [] for a missing file the same as for an empty
        # one, so comparing against a genuinely missing side would flag
        # every row on the other side as "missing" from a file that was
        # simply never synced — not a real drift finding. The "not found"
        # note the page renders from have_* already says why there's
        # nothing to compare.
        return {"rows": [], "have_live_snapshot": have_live_snapshot,
                "have_ledger": have_ledger, "count": 0}

    live = _rows(LIVE_SHEET)
    ledger = _rows(LEDGER)
    ledger_by_sku = {r["sku"]: r for r in ledger if r.get("sku")}

    seen_lids: set = set()
    rows = []
    for lv in live:
        lid = lv.get("listing_id") or ""
        if (lv.get("live") or "").lower() != "yes" or not lid or lid in seen_lids:
            continue                                          # CHOICE variation, already counted
        seen_lids.add(lid)
        sku = lv.get("sku") or ""
        led = ledger_by_sku.get(sku)
        issues = []
        if led is None:
            issues.append("no listings_ledger.csv row for this live sku")
        else:
            if _price_drift(lv.get("price"), led.get("price")):
                issues.append(f"price: ledger {_money(_f(led.get('price')))} "
                              f"vs live {_money(_f(lv.get('price')))}")
            live_title = (lv.get("title") or "").strip()
            ledger_title = (led.get("title") or "").strip()
            if live_title and ledger_title and live_title != ledger_title:
                issues.append("title differs from ledger")
            if (led.get("status") or "") != "PUBLISHED":
                issues.append(f"ledger status is {led.get('status') or '(blank)'}, not PUBLISHED")
        if issues:
            rows.append({
                "sku": sku, "listing_id": lid, "title": lv.get("title", ""),
                "live_price": lv.get("price", ""),
                "ledger_price": (led or {}).get("price", ""),
                "issues": issues,
            })

    live_skus = {lv.get("sku") for lv in live if (lv.get("live") or "").lower() == "yes"}
    for r in ledger:
        if r.get("status") == "PUBLISHED" and r.get("sku") and r["sku"] not in live_skus:
            rows.append({
                "sku": r["sku"], "listing_id": r.get("listing_id", ""),
                "title": r.get("title", ""), "live_price": "",
                "ledger_price": r.get("price", ""),
                "issues": ["ledger says PUBLISHED but this sku is not in the "
                           "current live sheet (inventory_sheet.csv)"],
            })

    return {
        "rows": rows,
        "have_live_snapshot": have_live_snapshot,
        "have_ledger": have_ledger,
        "count": len(rows),
    }


# ---------------------------------------------------------------------------
# combined gather
# ---------------------------------------------------------------------------

def gather() -> dict:
    return {
        "backlog": gather_backlog(),
        "drafts": gather_drafts(),
        "drift": gather_drift(),
    }


# ---------------------------------------------------------------------------
# draw
# ---------------------------------------------------------------------------

def _backlog_section(d: dict) -> str:
    counts = " · ".join(f"{k} {v}" for k, v in
                        sorted(d["stage_counts"].items(),
                               key=lambda kv: (kv[0] != "ready for review", kv[0])))
    rows = "".join(
        f'<tr><td>{_e(r["dir"])}</td>'
        f'<td class="num">{r["frames"]}</td>'
        f'<td>{_e(r["blocked_stage"] or "ready for review")}</td>'
        f'<td class="dim">{_e(r["next_action"])}</td>'
        f'<td>{_e(r["sku"] or "—")}</td>'
        f'<td>{_e(r["ledger_status"] or "—")}</td></tr>'
        for r in d["rows"][:200])
    return (
        '<div class="card"><div class="pad"><h2>Backlog by stage</h2>'
        f'<p class="note" style="margin:0 0 14px;padding:0;border:0">{d["count"]} shoot(s) '
        f'in <code>inventory/</code>. By blocking stage: {_e(counts) or "—"}</p>'
        '<div class="scroll"><table><tr><th>Shoot</th><th class="num">Frames</th>'
        '<th>Blocked at</th><th>Next action</th><th>SKU</th><th>Ledger</th></tr>'
        f'{rows}</table></div>'
        + ('' if d["rows"] else '<p class="note">No shoot directories found under '
                                'inventory/.</p>')
        + '</div></div>')


def _draft_rows(rows: list[dict]) -> str:
    return "".join(
        f'<tr><td>{_e(r.get("title") or "(untitled)")}'
        + ('<span class="pill on" style="margin-left:8px">GROUP</span>'
           if r.get("group") else "")
        + f'<div class="dim" style="font-size:11.5px">{_e(r.get("path") or r.get("sku") or "")}'
          f'</div></td>'
        f'<td class="num">{_money(_f(r.get("price"), 0.0))}</td>'
        f'<td>{_e(r.get("sku") or "—")}</td>'
        f'<td class="warn">{_e("; ".join(r.get("blocking_issues") or []) or "—")}</td></tr>'
        for r in rows[:100])


def _drafts_section(d: dict) -> str:
    return (
        '<div class="card"><div class="pad"><h2>Drafts awaiting review</h2>'
        '<div class="stats" style="margin:0 -24px 18px;border-top:1px solid var(--rule);'
        'border-bottom:1px solid var(--rule)">'
        + _stat(str(len(d["synced"])), "synced, not published",
                "an eBay draft exists — one step from live")
        + _stat(str(len(d["drafted"])), "drafted, not synced", "local only")
        + '</div>'
        + (f'<h3 style="font:600 11px/1 &quot;IBM Plex Sans&quot;,sans-serif;'
           f'letter-spacing:.1em;text-transform:uppercase;color:var(--muted);'
           f'margin:0 0 10px">Synced, awaiting publish</h3>'
           f'<div class="scroll"><table><tr><th>Item</th><th class="num">Price</th>'
           f'<th>SKU</th><th>Blocking</th></tr>{_draft_rows(d["synced"])}</table></div>'
           if d["synced"] else "")
        + (f'<h3 style="font:600 11px/1 &quot;IBM Plex Sans&quot;,sans-serif;'
           f'letter-spacing:.1em;text-transform:uppercase;color:var(--muted);'
           f'margin:22px 0 10px">Drafted, not yet synced</h3>'
           f'<div class="scroll"><table><tr><th>Item</th><th class="num">Price</th>'
           f'<th>SKU</th><th>Blocking</th></tr>{_draft_rows(d["drafted"])}</table></div>'
           if d["drafted"] else "")
        + ('' if d["count"] else '<p class="note">Nothing drafted or synced-but-unpublished '
                                  'right now.</p>')
        + '</div></div>')


def _drift_section(d: dict) -> str:
    rows = "".join(
        f'<tr><td>{_itm(r["listing_id"], r["title"][:64] or r["sku"])}'
        f'<div class="dim" style="font-size:11.5px">{_e(r["sku"])}</div></td>'
        f'<td class="num">{_e(r["live_price"] or "—")}</td>'
        f'<td class="num">{_e(r["ledger_price"] or "—")}</td>'
        f'<td class="warn">{_e("; ".join(r["issues"]))}</td></tr>'
        for r in d["rows"][:100])
    if not d["have_live_snapshot"] or not d["have_ledger"]:
        missing = " and ".join(
            n for n, have in (("inventory_sheet.csv", d["have_live_snapshot"]),
                              ("listings_ledger.csv", d["have_ledger"])) if not have)
        note = (f'<p class="note">{_e(missing)} not found — run the usual sync '
                f'(<code>tools/ebay_sheet.py</code> / a listing sync) at least once, '
                f'then re-run this dashboard.</p>')
    else:
        note = ('<p class="note">Compares two local snapshots as of their own last sync — '
                'this makes no eBay call, so drift here can lag the truth by however old '
                'the last sync is.</p>')
    return (
        '<div class="card"><div class="pad"><h2>Live listing vs. ledger drift</h2>'
        f'<p class="note" style="margin:0 0 14px;padding:0;border:0">{d["count"]} live '
        f'listing(s) disagree with the local ledger.</p>'
        '<div class="scroll"><table><tr><th>Item</th><th class="num">Live price</th>'
        f'<th class="num">Ledger price</th><th>Issue</th></tr>{rows}</table></div>'
        + ('' if d["rows"] else '<p class="note">No drift found.</p>')
        + note + '</div></div>')


def draw(d: dict) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        '<div class="card"><div class="hdr">'
        '<p class="eyebrow">ebaybiz · dashboard</p>'
        '<h1>Backlog, review queue &amp; drift</h1>'
        f'<div class="ct">built {_e(now)} local · read-only, local files only — '
        'no eBay/Apify call</div></div>'
        '<div class="stats">'
        + _stat(str(d["backlog"]["count"]), "shoots in the pipeline")
        + _stat(str(d["drafts"]["count"]), "drafts awaiting review")
        + _stat(str(d["drift"]["count"]), "live/ledger disagreements")
        + '</div></div>')
    body = "\n".join([
        header,
        _backlog_section(d["backlog"]),
        _drafts_section(d["drafts"]),
        _drift_section(d["drift"]),
    ])
    return ('<meta charset="utf-8">\n<title>Dashboard</title>\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<style>{STYLE}</style>\n<div class="wrap">\n{body}\n</div>\n')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(OUT_HTML))
    a = ap.parse_args()

    d = gather()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)  # still creates reports/ for the default
    out.write_text(draw(d), encoding="utf-8")

    print(f"{d['backlog']['count']} shoot(s) · {d['drafts']['count']} draft(s) awaiting "
          f"review · {d['drift']['count']} live/ledger disagreement(s)")
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
