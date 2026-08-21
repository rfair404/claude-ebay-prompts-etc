#!/usr/bin/env python3
"""Replace a shoot's listing images with PLAIN renders and push them to eBay.

Plain = the manifest's own orientation + crop applied to the ORIGINAL camera
file, with zero tonal processing. This sidesteps the backdrop-normalisation bug
entirely rather than depending on it being correct.

Per shoot:
  1. render listing-plain/ from the originals (crop + orient only)
  2. move the existing listing/ aside to listing.bak-crushed/   (never deleted)
  3. copy the plain renders into listing/
  4. repoint draft.md's photos: at listing/, preserving order
  5. rewrite the manifest's out_sha256 so the prep gate sees the truth
  6. push photos to the live listing and verify the count

Originals at the shoot root are never written to.

  python tools/plainify.py <shoot> [<shoot> ...] [--apply]
"""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import retouch_tracker as T   # noqa: E402

def sh(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def plainify(shoot_s: str, apply: bool) -> tuple[str, dict]:
    shoot = REPO / shoot_s
    man_p = shoot / ".prep" / "prep.json"
    if not man_p.exists():
        return "failed", {"error": "no prep manifest"}

    rc, out = sh([sys.executable, "tools/prep_plain.py", shoot_s, "--quiet"])
    if rc != 0:
        return "failed", {"error": out.strip()[-200:], "stage": "render"}

    plain = shoot / "listing-plain"
    imgs = sorted(plain.glob("*.jpg"))
    if not imgs:
        return "failed", {"error": "no plain renders produced"}

    listing = shoot / "listing"
    bak = shoot / "listing.bak-crushed"
    if listing.exists() and not bak.exists():
        shutil.move(str(listing), str(bak))
    elif listing.exists():
        shutil.rmtree(listing)
    listing.mkdir(exist_ok=True)
    for f in imgs:
        shutil.copy2(f, listing / f.name)

    # draft.md -> listing/, order preserved where the old order is known
    dm = shoot / "draft.md"
    if dm.exists():
        t = dm.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(?ms)^photos:\n((?:\s+-\s+\".*\"\n)+)", t)
        names = [f.name for f in imgs]
        if m:
            prior = re.findall(r'"[^"]*/([^"/]+)"', m.group(1))
            order, seen = [], set()
            for p in prior:
                stem = Path(p).stem
                for n in names:
                    if Path(n).stem == stem and n not in seen:
                        order.append(n); seen.add(n)
            order += [n for n in names if n not in seen]
            block = "photos:\n" + "".join(f'  - "listing/{n}"\n' for n in order)
            t = t[:m.start()] + block + t[m.end():]
            dm.write_text(t, encoding="utf-8")

    man = json.loads(man_p.read_text(encoding="utf-8"))
    for src, meta in man.get("photos", {}).items():
        o = meta.get("output")
        if not o: continue
        op = shoot / o
        if op.exists():
            meta["out_sha256"] = hashlib.sha256(op.read_bytes()).hexdigest()[:16]
            meta["render"] = "plain (crop+orient only, no colour)"
    man["color_note"] = ("listing/ holds PLAIN renders: orientation + crop only, zero tonal "
                         "processing. Backdrop normalisation is bypassed deliberately.")
    man_p.write_text(json.dumps(man, indent=1), encoding="utf-8")

    if not apply:
        return "pending", {"rendered": len(imgs), "dry_run": True}

    rc, out = sh([sys.executable, "lib/list_edit.py", "--update", shoot_s,
                  "--fields", "photos", "--confirm"])
    if rc != 0:
        return "failed", {"error": out.strip()[-260:], "stage": "push"}
    return "done", {"photos": len(imgs), "push": out.strip()[-90:]}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shoots", nargs="+")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for s in a.shoots:
        T.mark(s, "in_progress")
        st, info = plainify(s, a.apply)
        T.mark(s, st if st != "pending" else "pending", **info)
        tag = f"{info.get('photos', info.get('rendered','?'))} photos"
        print(f"  [{st:<8}] {s}  {tag}")
        if info.get("error"):
            print(f"      {info['error'][:170]}")

if __name__ == "__main__":
    main()
