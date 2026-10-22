"""lib/gdrive_mirror.py — the rclone command, the ledger-write trigger guards,
and the debounce worker. rclone itself is never run. No pytest fixtures —
runs under tests/run_all.py too.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import gdrive_mirror as gm  # noqa: E402

CFG = gm.GdriveConfig(remote="gdrive", folder="EBAYBIZ-backup")


def _tree(files):
    root = Path(tempfile.mkdtemp(prefix="gdrive_test_"))
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return root


class _Patch:
    """Swap module attributes for one block, the run_all-safe monkeypatch."""

    def __init__(self, obj, **attrs):
        self.obj, self.attrs, self.old = obj, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.old[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)

    def __exit__(self, *_):
        for k, v in self.old.items():
            setattr(self.obj, k, v)


def test_copy_args_never_deletes_and_keeps_history():
    args = gm.copy_args(CFG, Path("/data"), Path("/tmp/list.txt"), "20261018T000000Z",
                        apply=True, few=False)
    assert args[:4] == ["rclone", "copy", str(Path("/data")), "gdrive:EBAYBIZ-backup/data"]
    assert "sync" not in args
    i = args.index("--backup-dir")
    assert args[i + 1] == "gdrive:EBAYBIZ-backup/history/20261018T000000Z"
    assert "--checksum" in args and "--dry-run" not in args and "--no-traverse" not in args


def test_copy_args_dry_run_and_few():
    args = gm.copy_args(CFG, Path("/d"), Path("/l"), "S", apply=False, few=True)
    assert "--dry-run" in args and "--no-traverse" in args


def test_selection_ledgers_only_vs_full():
    root = _tree(["listings_ledger.csv", "sales_ledger-junk.csv", "config.yaml",
                  "inventory/a/draft.md", "inventory/a/IMG_1.JPG", "reports/r.html"])
    assert gm.selection(CFG, root, True) == ["listings_ledger.csv", "sales_ledger-junk.csv"]
    full = gm.selection(CFG, root, False)
    assert "inventory/a/draft.md" in full and "reports/r.html" in full
    assert "config.yaml" not in full and "inventory/a/IMG_1.JPG" not in full


def test_push_writes_files_from_list():
    root = _tree(["listings_ledger.csv", "inventory/a/draft.md"])
    seen = {}

    def run(args):
        seen["args"] = args
        seen["list"] = Path(args[args.index("--files-from-raw") + 1]).read_text()
        return type("R", (), {"returncode": 0})()

    with _Patch(gm.shutil, which=lambda _: "/usr/bin/rclone"):
        assert gm.push(CFG, root, apply=True, ledgers_only=True, run=run) == 0
    assert seen["list"] == "listings_ledger.csv\n"
    assert not Path(seen["args"][seen["args"].index("--files-from-raw") + 1]).exists()


def test_load_config():
    c = gm.load_gdrive_config({"gdrive": {"remote": "gd:", "folder": "/B/"},
                               "offsite": {"include": ["letters/**"]}})
    assert (c.remote, c.folder, c.auto) == ("gd", "B", True)
    assert c.include[-1] == "letters/**"
    try:
        gm.load_gdrive_config({})
    except gm.GdriveError:
        pass
    else:
        raise AssertionError("unconfigured must raise")


def _trigger(root, ledger, **env):
    calls = []
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with _Patch(gm, load_gdrive_config=lambda: CFG), \
             _Patch(gm.shutil, which=lambda _: "/usr/bin/rclone"):
            started = gm.ledger_changed(ledger, root=root,
                                        spawn=lambda *a, **k: calls.append(a))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return started, calls


def test_trigger_spawns_for_main_ledger():
    root = _tree(["listings_ledger.csv"])
    started, calls = _trigger(root, root / "listings_ledger.csv")
    assert started and len(calls) == 1
    assert calls[0][0][-3:] == ["_worker", "--root", str(root.resolve())]
    assert (root / gm.PENDING_FILE).exists()


def test_trigger_ignores_ledger_outside_main_checkout():
    root = _tree([])
    other = _tree(["listings_ledger.csv"])
    started, calls = _trigger(root, other / "listings_ledger.csv")
    assert not started and not calls and not (root / gm.PENDING_FILE).exists()


def test_trigger_off_switch():
    root = _tree(["listings_ledger.csv"])
    started, calls = _trigger(root, root / "listings_ledger.csv", EBAYBIZ_GDRIVE_AUTO="0")
    assert not started and not calls


def test_trigger_only_nudges_a_running_worker():
    root = _tree(["listings_ledger.csv"])
    (root / gm.LOCK_FILE).write_text(str(os.getpid()))       # a live pid
    started, calls = _trigger(root, root / "listings_ledger.csv")
    assert not started and not calls
    assert (root / gm.PENDING_FILE).exists()                 # the nudge


def test_stale_lock_is_replaced():
    root = _tree([])
    lock = root / gm.LOCK_FILE
    lock.write_text("999999999")                             # no such pid
    assert not gm._lock_alive(lock)
    assert gm._take_lock(lock)
    assert lock.read_text() == str(os.getpid())


def test_trigger_never_raises():
    with _Patch(gm, load_gdrive_config=lambda: (_ for _ in ()).throw(RuntimeError("x"))):
        root = _tree(["listings_ledger.csv"])
        assert gm.ledger_changed(root / "listings_ledger.csv", root=root) is False


def test_worker_batches_writes_and_cleans_up():
    root = _tree(["listings_ledger.csv"])
    (root / gm.PENDING_FILE).write_text("1")
    pushes = []

    def fake_push(cfg, r, **kw):
        pushes.append(kw)
        if len(pushes) == 1:                                 # a write lands mid-push
            (root / gm.PENDING_FILE).write_text("2")
        return 0

    with _Patch(gm, load_gdrive_config=lambda: CFG, push=fake_push):
        assert gm._worker(root, debounce=0) == 0
    assert len(pushes) == 2 and pushes[0] == {"apply": True, "ledgers_only": True}
    assert not (root / gm.PENDING_FILE).exists()
    assert not (root / gm.LOCK_FILE).exists()


def test_upsert_listing_nudges_drive():
    import list_edit
    root = _tree([])
    ledger = root / "listings_ledger.csv"
    seen = []
    old_env = os.environ.get("EBAYBIZ_LISTINGS_LEDGER")
    os.environ["EBAYBIZ_LISTINGS_LEDGER"] = str(ledger)
    try:
        with _Patch(list_edit, ledger_changed=seen.append):
            list_edit.upsert_listing("SKU1", "PUBLISHED", price="10.00")
    finally:
        if old_env is None:
            os.environ.pop("EBAYBIZ_LISTINGS_LEDGER", None)
        else:
            os.environ["EBAYBIZ_LISTINGS_LEDGER"] = old_env
    assert seen == [ledger]
    assert "PUBLISHED" in ledger.read_text()


def test_unit_files():
    u = gm.unit_files("/py", Path("/repo"))
    svc, timer = u["ebaybiz-gdrive.service"], u["ebaybiz-gdrive.timer"]
    assert "ExecStart=/py -m lib.cli gdrive push --apply" in svc
    assert f"WorkingDirectory={Path('/repo')}" in svc
    assert "Persistent=true" in timer and "OnCalendar=" in timer
