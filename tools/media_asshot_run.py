#!/usr/bin/env python3
"""Re-render printed media (books, magazines, catalogs, mailers) as-shot and push.

The rule this implements is in prompts/prep.md: on printed paper the colour
correction cannot be trusted, because the open page IS a large light field and
the segmenter hands it to the backdrop pass. Measured on fall-and-winter-1980,
live renders had lost 52-94% of their saturation. So this class renders `asshot`
(k=0) rather than `studio`, every time.

Two things it does headless that the interactive path refuses to do, both by
explicit operator instruction:

  * ORIENTATION IS GUESSED, NOT ASKED. A frame the pipeline flags ASK is
    recorded at the pipeline's own computed subject angle and shipped. This is
    a real loosening of the rule that a human answers orientation, and it can
    be wrong in a way nothing here detects: on fall-and-winter-1980 two frames
    shipped rotated 90 degrees while flagged as resolved, not ASK. The guess is
    the best signal available, not a correct answer.
  * DESKEW IS ACCEPTED. Whatever the unskew stage decides stands.

The one thing it will NOT do is write to a sold listing. It never passes
--allow-not-sellable, so list_edit's guard — which asks eBay, not the ledger,
because an accepted Best Offer never writes back to the ledger — refuses any
offer that is not live. A shoot whose offer has sold is recorded `skipped`.

Scope is every pending shoot, printed media first, because the operator's
instruction when the audit resumed was "attempt each item" and "when in doubt,
take the photos as is". `asshot` is the safe universal answer to that: it is a
passthrough, so it cannot drain a colour or crush a backdrop the way the
correction did. It is not the BEST look for every shoot — a dark-cloth item
genuinely gains from `punch` — but it is the one that cannot make a listing
worse than the camera saw it.

Renders `--only asshot`: one preset instead of six, ~11s a frame instead of
~64s, which is what makes the whole queue fit in a working session.

  python tools/media_asshot_run.py --apply              # the whole queue
  python tools/media_asshot_run.py --apply --limit 3
  python tools/media_asshot_run.py --apply --deadline 9.5
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
import retouch_tracker as T

REPO = Path(__file__).resolve().parents[1]

# Printed-paper shoots. Matched on the shoot path, which is how this inventory
# is actually organised (f-books/, more-mags-444/, decatur-pubs/, ...).
MEDIA = re.compile(
    r"book|mag|catalog|pubs|mailer|britches|j-crew|ranger|nautica"
    r"|paul-fredrick|folio|mark-shale|fall-and-winter|zachry|decatur",
    re.I)

STAGES = ("orientation", "unskew", "crop", "color")


def run(args: list[str], timeout: int = 1800) -> tuple[int, str]:
    p = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def guess_orientation(shoot_s: str) -> list[str]:
    """Record the pipeline's own estimate for every unresolved frame.

    --set-rotate, not --rotate: the absolute form is idempotent. The relative
    form applied twice moves the frame twice, which is how a catalog spread
    once went from applied 0 to 270 on a second run of the same pasted line.
    """
    pj = REPO / shoot_s / ".prep" / "prep.json"
    if not pj.exists():
        return []
    d = json.loads(pj.read_text(encoding="utf-8"))
    pairs = []
    for name, rec in d.get("photos", {}).items():
        o = rec.get("orientation") or {}
        if o.get("needs_ask"):
            pairs.append(f"{name}={int(o.get('subject_angle') or 0) % 360}")
    return pairs


def process(shoot_s: str, apply: bool) -> tuple[str, dict]:
    shoot = REPO / shoot_s
    if not shoot.exists():
        return "failed", {"error": "shoot directory missing"}

    rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s,
                   "--only", "asshot", "--apply", "--quiet"])
    if rc != 0:
        return "failed", {"error": out.strip()[-300:], "stage": "apply"}

    guessed = guess_orientation(shoot_s)
    if guessed:
        rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s,
                       "--set-rotate", *guessed])
        if rc != 0:
            return "failed", {"error": out.strip()[-300:], "stage": "rotate"}
        # Recording a rotation invalidates the renders and the approval.
        rc, out = run([sys.executable, "-m", "lib.photo_prep.prep", shoot_s,
                       "--only", "asshot", "--apply", "--quiet"])
        if rc != 0:
            return "failed", {"error": out.strip()[-300:], "stage": "re-apply"}

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

    info = {"preset": "asshot", "guessed_orientation": guessed}
    if not apply:
        info["dry_run"] = True
        return "pending", info

    # NO --allow-not-sellable, ever. The guard is the sold-listing protection.
    rc, out = run([sys.executable, "lib/list_edit.py", "--update", shoot_s,
                   "--fields", "photos", "--confirm"])
    if rc != 0:
        low = out.lower()
        if "not on sale" in low or "notsellable" in low or "sold" in low:
            return "skipped", {**info, "reason": "offer not live (sold/ended) — not touched",
                               "detail": out.strip()[-200:]}
        return "failed", {**info, "error": out.strip()[-300:], "stage": "push"}
    return "done", {**info, "push": out.strip()[-120:]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--deadline", type=float, default=9.5,
                    help="hours; stop starting new shoots past this")
    ap.add_argument("--log", default=".audit_headless.log")
    a = ap.parse_args()

    started = time.time()
    logf = open(a.log, "a", encoding="utf-8", buffering=1)

    def say(msg: str) -> None:
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line)
        logf.write(line + chr(10))
        sys.stdout.flush()

    d = T.load()
    pend = [k for k, v in sorted(d["shoots"].items()) if v.get("state") == "pending"]
    # Printed media first: that class has a measured failure and a written rule.
    batch = ([k for k in pend if MEDIA.search(k)] +
             [k for k in pend if not MEDIA.search(k)])[:a.limit]
    if not batch:
        say("nothing pending")
        T.summary()
        return

    say("batch of %d (%s), deadline %.1fh"
        % (len(batch), "APPLY" if a.apply else "DRY RUN", a.deadline))
    for s in batch:
        if (time.time() - started) / 3600.0 > a.deadline:
            say("DEADLINE reached — %s and the rest left pending" % s)
            break
        T.mark(s, "in_progress")
        t0 = time.time()
        try:
            state, info = process(s, a.apply)
        except subprocess.TimeoutExpired as e:
            state, info = "failed", {"error": "TIMEOUT after %ss" % e.timeout}
        except Exception as e:                      # never let one shoot end the run
            state, info = "failed", {"error": ("%s: %s" % (type(e).__name__, e))[:300]}
        T.mark(s, "pending" if state == "pending" else state, **info)
        say("[%-9s] %s  %.0fs%s" % (state, s, time.time() - t0,
            ("  rot-guessed %d" % len(info["guessed_orientation"]))
            if info.get("guessed_orientation") else ""))
        if info.get("error"):
            say("       ERROR %s" % info["error"][:200])
        if info.get("reason"):
            say("       %s" % info["reason"])
    say("--- batch end ---")
    T.summary()



if __name__ == "__main__":
    main()
