#!/usr/bin/env python3
"""Push a batch of PREP'd shoots to their LIVE eBay listings.

Runs, per shoot: back up draft.md -> repoint `photos:` at listing/ -> approve
-> update ONLY the photos field group on the live listing.

Deliberately serial and fail-soft. A batch that stops on the first error leaves
half the run in an unknown state; this records the outcome of every shoot and
carries on, so the report at the end is the truth about all of them.

The PREP gate still applies — `upload_photos_to_eps` refuses photos that were
not prepped and approved, so a shoot that somehow reaches here unprepared is
rejected by the code, not by this script's good intentions.

    python tools/prep_push.py --queue .prep_queue.txt --dry
    python tools/prep_push.py --queue .prep_queue.txt --go
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def ledger() -> dict:
    out = {}
    with open(ROOT / "listings_ledger.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["sku"]] = r
    return out


def sku_of(shoot: Path) -> str | None:
    t = (shoot / "draft.md").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'ebay_inventory_sku:\s*"?([0-9a-f]{8})"?', t)
    return m.group(1) if m else None


def already_pushed(shoot: Path) -> bool:
    """Has this shoot's photo set actually been uploaded to eBay?

    It must be a record of the UPLOAD, not of local state. The first version
    inferred it from "draft points at listing/ AND manifest approved" — both of
    which the --dry pass sets by design, so the subsequent --go skipped all 32
    shoots and reported success having pushed nothing. Failing safe, but silent.

    `pushed_at` is written only after the eBay call returns, so it cannot be
    true unless photos really went up.
    """
    mf = shoot / ".prep" / "prep.json"
    if not mf.exists():
        return False
    return bool(json.loads(mf.read_text(encoding="utf-8")).get("pushed_at"))


def mark_pushed(shoot: Path, listing_id: str) -> None:
    """Stamp the manifest AFTER a successful upload."""
    mf = shoot / ".prep" / "prep.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["pushed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    m["pushed_listing_id"] = listing_id
    mf.write_text(json.dumps(m, indent=2), encoding="utf-8")


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return p.returncode, (p.stdout + p.stderr).strip()


def push_one(shoot: Path, go: bool, listing_id: str = "") -> dict:
    res = {"shoot": shoot.as_posix(), "steps": [], "ok": False, "error": None}
    py = sys.executable

    if already_pushed(shoot):
        res.update(ok=True, skipped="already pushed")
        return res

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = shoot / f"draft.md.bak-{stamp}"
    shutil.copyfile(shoot / "draft.md", bak)
    res["backup"] = bak.name

    for label, cmd in [
        ("repoint", [py, "-m", "lib.photo_prep.prep", str(shoot),
                     "--repoint-draft", "--apply-repoint"]),
        ("approve", [py, "-m", "lib.photo_prep.prep", str(shoot), "--approve"]),
        ("update",  [py, "lib/list_edit.py", "--update", str(shoot),
                     "--fields", "photos"]),
    ]:
        if label == "update" and not go:
            res["steps"].append(f"{label}: SKIPPED (--dry)")
            continue
        rc, out = run(cmd)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        res["steps"].append(f"{label}: rc={rc} {tail[:120]}")
        if rc != 0:
            res["error"] = f"{label} failed: {tail[:200]}"
            # Put the draft back so a failed shoot is not left half-repointed.
            shutil.copyfile(bak, shoot / "draft.md")
            res["steps"].append("draft.md restored from backup")
            return res
        if label == "update" and go:
            if "[OK] updated" not in out:
                res["error"] = f"update did not confirm an upload: {tail[:200]}"
                return res
            mark_pushed(shoot, listing_id)
    res["ok"] = True
    return res


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", default=".prep_queue.txt")
    ap.add_argument("--go", action="store_true", help="actually update the live listings")
    ap.add_argument("--dry", action="store_true", help="everything except the eBay call")
    ap.add_argument("--out", default="docs/prep_pushed.md")
    args = ap.parse_args()
    if not (args.go or args.dry):
        ap.error("pass --dry or --go")

    led = ledger()
    shoots = [Path(l.strip()) for l in open(ROOT / args.queue, encoding="utf-8") if l.strip()]
    results = []
    for i, s in enumerate(shoots, 1):
        t0 = time.monotonic()
        sku = sku_of(s)
        row = led.get(sku or "", {})
        r = push_one(s, go=args.go, listing_id=row.get("listing_id", ""))
        r.update(sku=sku, title=row.get("title", ""), url=row.get("url", ""),
                 secs=round(time.monotonic() - t0, 1))
        results.append(r)
        flag = "ok " if r["ok"] else "FAIL"
        note = r.get("skipped") or r.get("error") or ""
        print(f"[{i:2}/{len(shoots)}] {flag} {s.as_posix():48} {r['secs']:6.1f}s {note[:70]}")
        sys.stdout.flush()

    ok = [r for r in results if r["ok"] and not r.get("skipped")]
    skip = [r for r in results if r.get("skipped")]
    bad = [r for r in results if not r["ok"]]
    print(f"\nupdated {len(ok)} · already done {len(skip)} · failed {len(bad)}")

    lines = ["# Listings updated with the PREP filter", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M}. "
             f"{len(ok) + len(skip)} listings now show punch-processed photos.", "",
             "| listing | shoot | frames |", "|---|---|---|"]
    for r in ok + skip:
        mf = Path(r["shoot"]) / ".prep" / "prep.json"
        n = len(json.loads(mf.read_text(encoding="utf-8")).get("photos", {})) if mf.exists() else "?"
        lines.append(f"| [{r['title'][:60]}]({r['url']}) | `{r['shoot']}` | {n} |")
    if bad:
        lines += ["", "## Not updated", ""]
        lines += [f"- `{r['shoot']}` — {r['error']}" for r in bad]
    Path(ROOT / args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    json.dump(results, open(ROOT / ".prep_push.json", "w"), indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
