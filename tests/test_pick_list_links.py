"""tools/pick_list_links.py — poll output -> staged sheets -> one LINK per box.

The poll and the upload are subprocesses, so both are faked with a runner
that returns canned stdout. Sheets are published into a scratch main
checkout through pick_store itself. No network, no real pick_lists/.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import pick_list_links                                      # noqa: E402
import pick_store                                           # noqa: E402

SOLO, PAIR_A, PAIR_B = "01-11111-11111", "02-22222-22222", "02-22222-33333"


def _txt(main, oid, title, ship_by):
    (main / "pick_lists" / f"pick_{oid}.txt").write_text(
        f"PICK  order {oid}\n      status NOT_STARTED / PAID   SHIP BY {ship_by}\n"
        f"  [1] x1  {title}\n  SHIP TO   Jane Buyer\n            1 Main St\n",
        encoding="utf-8")


def _main(tmp_path):
    main = tmp_path / "main"
    (main / "pick_lists").mkdir(parents=True)
    served = main / "pick_lists" / ".served"
    solo = pick_store.publish("<p>solo sheet</p>", order_ids=[SOLO], store_dir=served)
    pair = pick_store.publish("<p>pair sheet</p>", order_ids=[PAIR_A, PAIR_B],
                              store_dir=served)
    _txt(main, SOLO, "Gold cross pendant", "2026-10-25")
    _txt(main, PAIR_A, "Cable knit sweater", "2026-10-24")
    _txt(main, PAIR_B, "Wool scarf", "2026-10-23")
    poll = ("[OK] 1 new pick list(s)  (x); 2 already printed\n"
            f"  {SOLO}  http://127.0.0.1:8770/pick/{solo.token}\n"
            f"  {PAIR_A} + {PAIR_B}  http://127.0.0.1:8770/pick/{pair.token}\n"
            "  ! the local app isn't running\n")
    return main, poll


class Runner:
    def __init__(self, poll_out, share_out="", share_err="", poll_rc=0, share_rc=0):
        self.poll = (poll_rc, poll_out)
        self.share = (share_rc, share_out, share_err)
        self.calls = []

    def __call__(self, argv, cwd, capture_output, text):
        self.calls.append(argv)
        if "tools/pick_list.py" in argv:
            rc, out = self.poll
            return subprocess.CompletedProcess(argv, rc, out, "boom" if rc else "")
        rc, out, err = self.share
        return subprocess.CompletedProcess(argv, rc, out, err)


def test_one_link_per_box_with_title_and_earliest_ship_by(tmp_path):
    main, poll = _main(tmp_path)
    run = Runner(poll, share_out="https://r2.example/a?sig=1\nhttps://r2.example/b?sig=2\n")
    lines, failed = pick_list_links.run(main, runner=run)

    assert failed == 0
    assert lines == [
        f"LINK {SOLO} https://r2.example/a?sig=1 | Gold cross pendant | ship by 2026-10-25",
        f"LINK {PAIR_A}+{PAIR_B} https://r2.example/b?sig=2 | Cable knit sweater + 1 more"
        " | ship by 2026-10-23",
        "DONE links=2 failed=0 held=0"]
    assert "--print" not in run.calls[0]
    share = run.calls[1]
    assert share[-2:] == [f"inventory/_pick_lists/pick_{SOLO}.html",
                          f"inventory/_pick_lists/pick_{PAIR_A}+{PAIR_B}.html"]
    staged = main / "inventory" / "_pick_lists" / f"pick_{PAIR_A}+{PAIR_B}.html"
    assert staged.read_text(encoding="utf-8") == "<p>pair sheet</p>"
    assert not any("127.0.0.1" in l or "Jane Buyer" in l for l in lines)


def test_upload_stopping_partway_fails_the_rest_with_a_retry_command(tmp_path):
    main, poll = _main(tmp_path)
    run = Runner(poll, share_out="https://r2.example/a?sig=1\n",
                 share_err="offsite: HTTP 403 Forbidden", share_rc=2)
    lines, failed = pick_list_links.run(main, runner=run)

    assert failed == 1
    assert lines[0].startswith(f"LINK {SOLO} https://r2.example/a?sig=1")
    assert lines[1].startswith(f"FAILED {PAIR_A}+{PAIR_B} (upload of")
    assert "HTTP 403 Forbidden" in lines[1]
    assert lines[2] == (f"RETRY cd {main} && .venv/bin/python -m lib.cli offsite share "
                        f"inventory/_pick_lists/pick_{PAIR_A}+{PAIR_B}.html")
    assert lines[-1] == "DONE links=1 failed=1 held=0"


def test_missing_sheet_is_failed_and_not_uploaded(tmp_path):
    main, _ = _main(tmp_path)
    poll = f"  {SOLO}  http://127.0.0.1:8770/pick/{'x' * 43}\n"
    run = Runner(poll)
    lines, failed = pick_list_links.run(main, runner=run)

    assert failed == 1
    assert lines == [f"FAILED {SOLO} (staging: sheet missing)",
                     "DONE links=0 failed=1 held=0"]
    assert len(run.calls) == 1          # nothing to upload, so no share call


def test_nothing_to_ship_with_held_live_orders_keeps_buyer_out(tmp_path):
    main, _ = _main(tmp_path)
    poll = ("[..] 2 order(s) from eBay Live show 'Fri' held until Fri 10:00 PM ET "
            "(8 h combined-shipping window)\n"
            f"  held {SOLO}  buyer_handle  until Fri 10:00 PM ET\n"
            f"  held {PAIR_A}  other_handle  until Fri 10:00 PM ET\n"
            "[OK] nothing else awaiting shipment\n")
    lines, failed = pick_list_links.run(main, runner=Runner(poll))

    assert failed == 0
    assert lines == ["HELD 2 order(s) from eBay Live show 'Fri' held until Fri 10:00 PM ET "
                     "(8 h combined-shipping window)",
                     "NONE", "DONE links=0 failed=0 held=2"]
    assert not any("_handle" in l for l in lines)


def test_poll_error_is_one_failed_line(tmp_path):
    main, _ = _main(tmp_path)
    lines, failed = pick_list_links.run(main, runner=Runner("", poll_rc=1))
    assert failed == 1
    assert lines == ["FAILED poll (exit 1: boom)", "DONE links=0 failed=1 held=0"]


def test_unrecognised_poll_output_is_failed_not_none(tmp_path):
    """Orders are waiting but the box lines changed shape: never say NONE."""
    main, _ = _main(tmp_path)
    reworded = ("[OK] junk: nothing awaiting shipment\n"
                "[OK] 1 new pick list(s)  (01-11111-11111)\n"
                f"  box {SOLO} -> http://127.0.0.1:8770/sheet/abc\n")
    for out in (reworded, "Polled 2 stores.\n", ""):
        lines, failed = pick_list_links.run(main, runner=Runner(out))
        assert failed == 1, out
        assert "NONE" not in lines
        assert lines[0].startswith("FAILED poll (output not recognised")
        assert lines[-1] == "DONE links=0 failed=1 held=0"


def test_box_whose_sheet_never_published_is_failed(tmp_path):
    main, _ = _main(tmp_path)
    poll = (f"  ! could not publish a link for {PAIR_A}, {PAIR_B} (disk full); "
            "the local sheet in pick_lists/ is the fallback\n")
    lines, failed = pick_list_links.run(main, runner=Runner(poll))
    assert failed == 1
    assert lines[0] == f"FAILED {PAIR_A}+{PAIR_B} (no sheet published: disk full)"


def test_item_folder_sheet_is_staged_from_its_source(tmp_path, monkeypatch):
    """A sold item's sheet lives in its folder under inventory/, and
    pick_lists/.served/ holds only a meta file pointing at it."""
    main, _ = _main(tmp_path)
    monkeypatch.setattr(pick_store, "ROOT", main)
    folder = main / "inventory" / "shoot" / "item-1"
    folder.mkdir(parents=True)
    (folder / f"pick_{SOLO}.html").write_text("<p>item folder sheet</p>", encoding="utf-8")
    served = main / "pick_lists" / ".served"
    sheet = pick_store.publish("<p>not this</p>", order_ids=[SOLO], store_dir=served,
                               source=folder / f"pick_{SOLO}.html")
    assert not (served / f"{sheet.token}.html").exists()
    poll = f"  {SOLO}  http://127.0.0.1:8770/pick/{sheet.token}\n"
    lines, failed = pick_list_links.run(
        main, runner=Runner(poll, share_out="https://r2.example/a?sig=1\n"))

    assert failed == 0
    assert lines[0].startswith(f"LINK {SOLO} https://r2.example/a?sig=1")
    staged = main / "inventory" / "_pick_lists" / f"pick_{SOLO}.html"
    assert staged.read_text(encoding="utf-8") == "<p>item folder sheet</p>"
