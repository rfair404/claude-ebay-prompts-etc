"""
comps_csv — append PRICE comps to a reviewable CSV (all stages).

Stage B (Apify) persists raw results to JSON via apify_ebay.py. Stages A
(WebSearch) and C (Chrome) are agent-driven and have no code path, so PRICE
logs their comps here instead: one `<shoot-dir>/comps.csv` with a `stage`
column, so the user can open a single spreadsheet and review every comp the
hunt looked at — across all three sources.

Standard library only (csv) — runs in minimal/sandboxed environments.

Columns:
    captured_at, item, stage, query, price, title, sold_date, condition,
    listing_type, url, note

CLI usage:
    # append one comp (Stage A or C)
    python comps_csv.py --shoot-dir <dir> --item 1 --stage A \
        --query "M Civilized Man 1983" --price 99.99 \
        --title "M 1983 Reagan/Prince Philip" \
        --url "https://www.ebay.com/itm/267669342148" \
        --sold-date 2026-05-14 --condition "Pre-Owned" --note "near-exact"

    # start a fresh file for a new run (writes header only)
    python comps_csv.py --shoot-dir <dir> --reset

    # fold a saved Apify run JSON into the SAME csv as stage B rows
    python comps_csv.py --shoot-dir <dir> --from-apify-json <path-to-run.json>

Programmatic:
    from comps_csv import append_comp, from_apify_json
    append_comp("inventory/my-shoot", stage="A", query="...", price=99.99,
                title="...", url="...", item="1", note="near-exact")
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Union

CSV_NAME = "comps.csv"
HEADER = [
    "captured_at", "item", "stage", "query", "price", "title",
    "sold_date", "condition", "listing_type", "url", "note",
]
VALID_STAGES = {"A", "B", "C"}


def _csv_path(shoot_dir: Union[str, Path]) -> Path:
    return Path(shoot_dir) / CSV_NAME


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reset(shoot_dir: Union[str, Path]) -> str:
    """Truncate (or create) the CSV with just the header row. Returns path."""
    path = _csv_path(shoot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=HEADER).writeheader()
    return str(path)


def append_rows(shoot_dir: Union[str, Path], rows: Iterable[dict]) -> str:
    """Append rows (dicts keyed by HEADER fields) to the CSV; create if new."""
    path = _csv_path(shoot_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for row in rows:
            r = {k: "" for k in HEADER}
            r.update(row)
            r.setdefault("captured_at", _now())
            if not r["captured_at"]:
                r["captured_at"] = _now()
            w.writerow(r)
    return str(path)


def append_comp(shoot_dir: Union[str, Path], *, stage: str, query: str,
                price: Union[str, float, None], title: str, url: str,
                item: str = "", sold_date: str = "", condition: str = "",
                listing_type: str = "", note: str = "") -> str:
    """Append one comp row. `stage` is A / B / C."""
    stage = str(stage).upper()
    if stage not in VALID_STAGES:
        raise ValueError(f"stage must be one of {sorted(VALID_STAGES)}; got {stage!r}")
    return append_rows(shoot_dir, [{
        "captured_at": _now(), "item": str(item), "stage": stage,
        "query": query, "price": "" if price is None else price,
        "title": title, "sold_date": sold_date, "condition": condition,
        "listing_type": listing_type, "url": url, "note": note,
    }])


def from_apify_json(shoot_dir: Union[str, Path], json_path: Union[str, Path],
                    item: str = "") -> str:
    """Fold a saved Apify run JSON (apify_ebay.save_run_json) into the CSV as
    stage B rows — so one comps.csv holds all three stages."""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    query = "; ".join(data.get("queries") or [])
    rows = []
    for c in data.get("comps", []):
        rows.append({
            "captured_at": _now(), "item": str(item), "stage": "B",
            "query": query, "price": c.get("sold_price", ""),
            "title": c.get("title", ""), "sold_date": c.get("sold_date", "") or "",
            "condition": c.get("condition", "") or "",
            "listing_type": c.get("listing_type", "") or "",
            "url": c.get("url", ""),
            "note": f"apify run {data.get('run_id','')}",
        })
    return append_rows(shoot_dir, rows)


def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(description="Append PRICE comps to a reviewable comps.csv")
    ap.add_argument("--shoot-dir", required=True, help="Shoot directory (CSV is <dir>/comps.csv)")
    ap.add_argument("--reset", action="store_true", help="Truncate the CSV to just the header")
    ap.add_argument("--from-apify-json", help="Fold a saved Apify run JSON in as stage B rows")
    ap.add_argument("--item", default="", help="Item id/number (multi-item shoots)")
    ap.add_argument("--stage", choices=sorted(VALID_STAGES), help="A / B / C")
    ap.add_argument("--query", default="")
    ap.add_argument("--price")
    ap.add_argument("--title", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--sold-date", default="", dest="sold_date")
    ap.add_argument("--condition", default="")
    ap.add_argument("--listing-type", default="", dest="listing_type")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    if args.reset:
        print(f"[OK] reset {reset(args.shoot_dir)}")
        return
    if args.from_apify_json:
        path = from_apify_json(args.shoot_dir, args.from_apify_json, item=args.item)
        print(f"[OK] folded Apify JSON into {path}")
        return
    if not args.stage:
        ap.error("provide --stage (with --price/--title/--url), or --reset, or --from-apify-json")
    path = append_comp(
        args.shoot_dir, stage=args.stage, query=args.query, price=args.price,
        title=args.title, url=args.url, item=args.item, sold_date=args.sold_date,
        condition=args.condition, listing_type=args.listing_type, note=args.note,
    )
    print(f"[OK] appended {args.stage} comp to {path}")


if __name__ == "__main__":
    _cli()
