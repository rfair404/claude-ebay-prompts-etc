"""Today's pick sheets as R2 links (the /pick-list skill) or on paper (/pl).

Does the whole flow described in prompts/pick_list.md ("Run"): polls every
store from the main checkout, stages each box's sheet as
inventory/_pick_lists/pick_<id>[+<id2>].html, uploads them all with one
`lib.cli offsite share`, and prints one line per box for the reply:

    LINK <id>[+<id2>] <url> | <title> | ship by <YYYY-MM-DD>
    HELD <the poll's eBay Live hold line>
    FAILED <id>[+<id2>]|poll (<reason>)
    RETRY <the one offsite share command for the sheets that didn't upload>
    NONE
    DONE links=N failed=N held=<orders held>

With --print it sends each box's sheet to CannonBW instead (the /pl skill):
renders it to PDF with headless Chromium, because CUPS here has no HTML
filter, and hands the PDF to `lp -d CannonBW`. The printer is named so a
change of system default can't send sheets to the QL-1100 label printer.
One line per box:

    PRINTED <id>[+<id2>] | <title> | ship by <YYYY-MM-DD>
    HELD, FAILED and NONE as above
    DONE printed=N failed=N held=<orders held> printer=CannonBW

Never prints the poll's 127.0.0.1 links or the buyer's name and address.
Exit 1 if anything FAILED.

    python tools/pick_list_links.py [--print]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import offsite                                                      # noqa: E402
import pick_store                                                   # noqa: E402

STAGE = Path("inventory") / "_pick_lists"
PRINTER = "CannonBW"
# Seconds before a hung Chromium or lp counts as a failed box.
PRINT_TIMEOUT = 120
# pick_list.py --poll, one line per box: "  <id>[ + <id>]  http://.../pick/<token>"
BOX_RE = re.compile(r"^  (\S+(?: \+ \S+)*)  [a-z]+://\S+/pick/([A-Za-z0-9_-]+)$")
# publish_sheet()'s warning when a box got no link at all
NO_LINK_RE = re.compile(r"^  ! could not publish a link for (.+?) \((.*)\);")
ORDER_ID_RE = re.compile(r"\A[A-Za-z0-9-]+\Z")
# Per-store verdicts. NONE needs a "nothing" verdict and no "new pick list(s)"
# one, so a change to the poll's wording fails loudly instead of reading as
# "nothing to ship" while orders wait.
NOTHING_RE = re.compile(r"^\[OK\] (?:.+ )?nothing (?:else )?awaiting shipment$")
PENDING_RE = re.compile(r"^\[OK\] (?:.+ )?\d+ new pick list\(s\)")


def parse_poll(out: str) -> tuple[list[tuple[list[str], str]], list[str], int,
                                  list[tuple[str, str]]]:
    """(boxes, held, held_orders, no_link) from `pick_list.py --poll` stdout.
    boxes is [(order_ids, token)]. held keeps only the per-show summary lines;
    the per-order lines carry the buyer's username, so they are only counted."""
    boxes, held, no_link = [], [], []
    held_orders = 0
    for line in out.splitlines():
        if m := BOX_RE.match(line):
            boxes.append((m.group(1).split(" + "), m.group(2)))
        elif line.startswith("[..] ") and " held until " in line:
            held.append(line[5:])
        elif line.startswith("  held "):
            held_orders += 1
        elif m := NO_LINK_RE.match(line):
            no_link.append((m.group(1).replace(", ", "+"), m.group(2)))
    return boxes, held, held_orders, no_link


def sheet_summary(main: Path, order_ids: list[str]) -> tuple[str, str]:
    """(title, ship-by) for a box, from the seller's pick_lists/pick_<id>.txt.
    Only the item lines and SHIP BY are read; the address lines are not."""
    titles, dates = [], []
    for oid in order_ids:
        txt = main / "pick_lists" / f"pick_{oid}.txt"
        try:
            text = txt.read_text(encoding="utf-8")
        except OSError:
            continue
        titles += re.findall(r"^  \[\d+\] x\d+  (.+)$", text, re.M)
        dates += re.findall(r"SHIP BY (\d{4}-\d{2}-\d{2})", text)
    title = titles[0] if titles else "?"
    if len(titles) > 1:
        title += f" + {len(titles) - 1} more"
    return title, min(dates) if dates else "?"


def fetch_sheet(main: Path, order_ids: list[str], token: str) -> str:
    """One box's served sheet. A sheet the poll dropped in an item folder has
    no .html in pick_lists/.served/, only a meta file pointing at it, so it is
    read through pick_store rather than by path."""
    if not all(ORDER_ID_RE.match(o) for o in order_ids):
        raise ValueError(f"unexpected order id in {order_ids}")
    html, status = pick_store.fetch(token, store_dir=main / "pick_lists" / ".served")
    if html is None:
        raise FileNotFoundError(f"sheet {status}")
    return html


def stage(main: Path, order_ids: list[str], token: str) -> Path:
    """Copy one box's served sheet to inventory/_pick_lists/ and return the
    path relative to `main`."""
    html = fetch_sheet(main, order_ids, token)
    rel = STAGE / f"pick_{'+'.join(order_ids)}.html"
    (main / rel).parent.mkdir(parents=True, exist_ok=True)
    (main / rel).write_text(html, encoding="utf-8")
    return rel


def poll(main: Path, runner) -> tuple[list[tuple[list[str], str]], list[str], int, int]:
    """Run the poll. Returns (boxes, lines, failed, held_orders), where lines
    are the HELD lines, a FAILED line per box that got no sheet at all, and
    NONE when nothing waits. A box with no sheet never matches BOX_RE, so it
    is counted here or it would go unprinted and unreported."""
    out = runner([sys.executable, "tools/pick_list.py", "--poll", "--all-stores"],
                 cwd=main, capture_output=True, text=True)
    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()[-1:] or ["no output"]
        return [], [f"FAILED poll (exit {out.returncode}: {tail[0]})"], 1, 0
    boxes, held, held_orders, no_link = parse_poll(out.stdout)
    lines = [f"HELD {h}" for h in held]
    lines += [f"FAILED {ids} (no sheet published: {why})" for ids, why in no_link]
    failed = len(no_link)
    if not boxes and not no_link:
        verdicts = out.stdout.splitlines()
        if (any(NOTHING_RE.match(v) for v in verdicts)
                and not any(PENDING_RE.match(v) for v in verdicts)):
            lines.append("NONE")
        else:
            lines.append("FAILED poll (output not recognised; run tools/pick_list.py"
                         " --poll --all-stores to read it)")
            failed += 1
    return boxes, lines, failed, held_orders


def run(main: Path, *, runner=subprocess.run) -> tuple[list[str], int]:
    """Do the whole flow. Returns (output lines, failed count)."""
    boxes, lines, failed, held_orders = poll(main, runner)

    staged: list[tuple[list[str], Path]] = []
    for ids, token in boxes:
        try:
            staged.append((ids, stage(main, ids, token)))
        except (OSError, ValueError) as e:
            lines.append(f"FAILED {'+'.join(ids)} (staging: {e})")
            failed += 1

    links = 0
    if staged:
        up = runner([sys.executable, "-m", "lib.cli", "offsite", "share",
                     *(p.as_posix() for _, p in staged)],
                    cwd=main, capture_output=True, text=True)
        # `offsite share` prints one URL per file, in argument order, and
        # stops at the first error.
        urls = [u for u in up.stdout.split() if u.startswith("https://")]
        err = (up.stderr or "").strip().splitlines()[-1:] or [f"exit {up.returncode}"]
        retry = []
        for i, (ids, rel) in enumerate(staged):
            if i < len(urls):
                title, ship_by = sheet_summary(main, ids)
                lines.append(f"LINK {'+'.join(ids)} {urls[i]} | {title} | ship by {ship_by}")
                links += 1
            else:
                lines.append(f"FAILED {'+'.join(ids)} (upload of {rel}: {err[0]})")
                retry.append(rel.as_posix())
                failed += 1
        if retry:
            lines.append(f"RETRY cd {main} && .venv/bin/python -m lib.cli offsite share "
                         + " ".join(retry))
    lines.append(f"DONE links={links} failed={failed} held={held_orders}")
    return lines, failed


def print_box(main: Path, ids: list[str], token: str, tmp: Path, runner) -> str | None:
    """Render one box's sheet to PDF and send it to PRINTER. Returns why it
    failed, or None once lp has taken it."""
    try:
        html = fetch_sheet(main, ids, token)
    except (OSError, ValueError) as e:
        return f"no sheet: {e}"
    # Named by token: fetch() has vouched for it, and it needs no URI escaping.
    src, pdf = tmp / f"{token}.html", tmp / f"{token}.pdf"
    src.write_text(html, encoding="utf-8")
    # A throwaway profile keeps headless Chromium off Russell's open browser.
    # Chromium can log errors and still write a good PDF, so the PDF is the
    # test of a render, not the exit code.
    try:
        r = runner(["chromium", "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={tmp / 'profile'}", f"--print-to-pdf={pdf}",
                    src.as_uri()],
                   cwd=main, capture_output=True, text=True, timeout=PRINT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"render: {e}"
    if not (pdf.exists() and pdf.stat().st_size):
        return f"render: no PDF ({_tail(r)})"
    try:
        r = runner(["lp", "-d", PRINTER, str(pdf)],
                   cwd=main, capture_output=True, text=True, timeout=PRINT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"lp: {e}"
    return None if r.returncode == 0 else f"lp: {_tail(r)}"


def _tail(done: subprocess.CompletedProcess) -> str:
    """A finished command's last stderr line, or its exit code."""
    return ((done.stderr or "").strip().splitlines()[-1:] or [f"exit {done.returncode}"])[0]


def run_print(main: Path, *, runner=subprocess.run) -> tuple[list[str], int]:
    """Poll, then print every box awaiting shipment on PRINTER. Every box
    prints, even ones polled earlier, so running it twice prints twice.
    Returns (output lines, failed count)."""
    boxes, lines, failed, held_orders = poll(main, runner)
    printed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for ids, token in boxes:
            box = "+".join(ids)
            if why := print_box(main, ids, token, Path(tmp), runner):
                lines.append(f"FAILED {box} ({why})")
                failed += 1
            else:
                title, ship_by = sheet_summary(main, ids)
                lines.append(f"PRINTED {box} | {title} | ship by {ship_by}")
                printed += 1
    lines.append(f"DONE printed={printed} failed={failed} held={held_orders} printer={PRINTER}")
    return lines, failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help=f"print each sheet on {PRINTER} instead of uploading it")
    args = ap.parse_args(argv)
    lines, failed = (run_print if args.do_print else run)(offsite.data_root(ROOT))
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
