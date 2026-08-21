#!/usr/bin/env python3
"""Printed media: force the crop OFF on every frame, render `asshot`, stop.

Two measured failures, one cause. On a catalog photographed open on a light
sweep, the page IS the subject and the page is also a large light field with a
picture printed on it:

  * the colour stage hands the page to the backdrop pass, which neutralises it
    toward paper white (fall-and-winter-1980 lost 52-94% of its saturation on
    renders that were already live);
  * the crop stage locks onto whatever is highest-contrast ON the page — the
    photograph in the layout, a redaction rectangle — and crops the catalog
    away. j-crew/3 shipped cropped to a printed boot with the J.CREW masthead
    gone; mark-shale-business-casual to a printed chair; the fall-and-winter
    mailer to a bare black bar. Of 56 cropped media frames the worst kept 18.9%
    of the original.

There is no crop worth making on printed paper: the object of the listing is the
whole page, edges included. So this sets every frame to crop=off rather than
trying to teach the detector what a catalog is.

RENDERS ONLY. It never pushes — the operator asked to see the result first, and
a batch that both changes and publishes gives no chance to disagree.

    python tools/media_crop_fix.py --shoots .media_shoots.txt
    python tools/media_crop_fix.py --shoots .media_shoots.txt --limit 3
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES = ("orientation", "unskew", "crop", "color")
IMG = (".jpg", ".jpeg", ".png", ".heic", ".JPG", ".JPEG", ".PNG")


def run(args: list[str], timeout: int = 2400) -> tuple[int, str]:
    p = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def frames(shoot: Path) -> list[str]:
    """Frame names from the manifest if it exists, else from the directory."""
    pj = shoot / ".prep" / "prep.json"
    if pj.exists():
        try:
            return list(json.loads(pj.read_text(encoding="utf-8")).get("photos", {}))
        except ValueError:
            pass
    return sorted(p.name for p in shoot.iterdir()
                  if p.is_file() and p.suffix in IMG)


def process(shoot_s: str) -> tuple[str, dict]:
    shoot = REPO / shoot_s
    if not shoot.exists():
        return "failed", {"error": "shoot directory missing"}

    # Plan first if there is no manifest — --crop needs one to write into.
    if not (shoot / ".prep" / "prep.json").exists():
        rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s, "--check", "--quiet"])
        if rc != 0:
            return "failed", {"error": out.strip()[-300:], "stage": "check"}

    names = frames(shoot)
    if not names:
        return "failed", {"error": "no frames found"}

    # Crop off BEFORE rendering, not after: a crop change invalidates the
    # renders, so doing it second would mean rendering every frame twice.
    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s,
                   "--crop", *[f"{n}=off" for n in names]])
    if rc != 0:
        return "failed", {"error": out.strip()[-300:], "stage": "crop-off"}

    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s,
                   "--only", "asshot", "--apply", "--quiet"])
    if rc != 0:
        return "failed", {"error": out.strip()[-300:], "stage": "apply"}

    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s, "--pick", "asshot"])
    if rc != 0:
        return "failed", {"error": out.strip()[-300:], "stage": "pick"}

    for st in STAGES:
        rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s,
                       "--approve-stage", st, "--quiet"])
        if rc != 0:
            return "failed", {"error": out.strip()[-200:], "stage": f"approve:{st}"}
    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s, "--approve", "--quiet"])
    if rc != 0:
        return "failed", {"error": out.strip()[-200:], "stage": "approve"}

    return "rendered", {"frames": len(names), "preset": "asshot", "crop": "off (all)"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoots", default=".media_shoots.txt")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--log", default=".media_crop_fix.log")
    a = ap.parse_args()

    todo = [l.strip() for l in Path(a.shoots).read_text(encoding="utf-8").splitlines() if l.strip()][:a.limit]
    logf = open(a.log, "a", encoding="utf-8", buffering=1)

    def say(m):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
        print(line); logf.write(line + chr(10)); sys.stdout.flush()

    say("crop-off + asshot, RENDER ONLY — %d shoots" % len(todo))
    tally = {}
    for s in todo:
        t0 = time.time()
        try:
            state, info = process(s)
        except subprocess.TimeoutExpired as e:
            state, info = "failed", {"error": "TIMEOUT after %ss" % e.timeout}
        except Exception as e:
            state, info = "failed", {"error": ("%s: %s" % (type(e).__name__, e))[:300]}
        tally[state] = tally.get(state, 0) + 1
        say("[%-8s] %s  %.0fs  %s" % (state, s, time.time() - t0,
                                      info.get("error", "")[:120] if info.get("error") else
                                      "%d frames" % info.get("frames", 0)))
    say("--- done: %s ---" % tally)
    say("NOTHING PUSHED. review, then push with list_edit --update <shoot> --fields photos --confirm")


if __name__ == "__main__":
    main()
