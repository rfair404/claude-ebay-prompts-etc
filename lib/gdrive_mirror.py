"""Second offsite copy of the listing data — Google Drive, via rclone.

R2 (lib/offsite.py) is copy one. This is copy two, on a different provider
and a different account, so one billing lapse, revoked key or bad push can't
take out both. It copies the same files offsite.py selects (ledgers, drafts,
identify.txt, reports — never photos, never config.yaml).

    python -m lib.cli gdrive check              # rclone + remote reachable?
    python -m lib.cli gdrive push               # dry run: what would upload
    python -m lib.cli gdrive push --apply       # everything offsite.py selects
    python -m lib.cli gdrive push --apply --ledgers   # just the ledger files
    python -m lib.cli gdrive install-timer      # daily full push (systemd --user)

It runs on its own two ways:

  * After every ledger write — a draft, sync, publish, end, delete, sale or
    shipment (list_edit.upsert_listing), a sales-ledger merge
    (sync_actuals) and `reconcile --apply` — ledger_changed() starts a
    detached background push of the ledger files. It waits DEBOUNCE seconds
    first, so a pick-list run that marks 30 items SHIPPED uploads once. It
    never blocks or fails the command that wrote the ledger.
  * Once a day, the systemd user timer pushes the full set. Persistent=true:
    a day the machine was off runs at the next boot.

Safety rules (the same as R2's):

  * never deletes on Drive: `rclone copy`, never `sync`. A file gone locally
    stays on Drive.
  * a Drive file that push replaces is first moved to
    <folder>/history/<UTC stamp>/<path> (rclone --backup-dir), so a truncated
    ledger can't silently replace the good one.
  * a dry run unless --apply (the automatic runs pass it).

Drive layout:   <folder>/data/<relpath>              the current copy
                <folder>/history/<stamp>/<relpath>   superseded versions

Config (config.yaml):

    gdrive:
      remote: "gdrive"            # the rclone remote name (`rclone config`)
      folder: "EBAYBIZ-backup"    # top-level folder in My Drive
      auto: true                  # push the ledgers after each ledger write

rclone holds the Google OAuth token (in ~/.config/rclone/rclone.conf), so
nothing secret is in config.yaml. Setup: docs/offsite-storage.md.

The data always comes from the MAIN checkout, even when run from a worktree.
Background runs log to <main>/.gdrive.log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from offsite import DEFAULT_INCLUDE, LEDGER_INCLUDE, data_root, local_files  # noqa: E402

REPO = LIB.parent

LOG_FILE = ".gdrive.log"             # /.*.log is gitignored
PENDING_FILE = ".gdrive_pending.txt"  # /.*.txt is gitignored
LOCK_FILE = ".gdrive_lock.txt"
DEBOUNCE = 20                        # seconds a background push waits for more writes
LOCK_STALE = 30 * 60                 # a lock older than this is a crashed worker

UNIT_NAME = "ebaybiz-gdrive"


class GdriveError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GdriveConfig:
    remote: str
    folder: str
    auto: bool = True
    include: tuple[str, ...] = DEFAULT_INCLUDE
    rclone: str = "rclone"

    @property
    def data_dest(self) -> str:
        return f"{self.remote}:{self.folder}/data"

    def history_dest(self, stamp: str) -> str:
        return f"{self.remote}:{self.folder}/history/{stamp}"


def load_gdrive_config(cfg: Optional[dict] = None) -> GdriveConfig:
    if cfg is None:
        import config as _config
        cfg = _config.load_config()
    g = cfg.get("gdrive") or {}
    if not g.get("remote"):
        raise GdriveError(
            "Google Drive copy not configured — add a `gdrive:` block to config.yaml\n"
            "(see lib/config.example.yaml and docs/offsite-storage.md).")
    folder = str(g.get("folder") or "EBAYBIZ-backup").strip("/")
    extra = tuple((cfg.get("offsite") or {}).get("include") or ())
    return GdriveConfig(remote=str(g["remote"]).rstrip(":"), folder=folder,
                        auto=bool(g.get("auto", True)),
                        include=DEFAULT_INCLUDE + extra,
                        rclone=str(g.get("rclone") or "rclone"))


# ---------------------------------------------------------------------------
# rclone
# ---------------------------------------------------------------------------

def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def copy_args(cfg: GdriveConfig, root: Path, files_from: Path, stamp: str, *,
              apply: bool, few: bool) -> list[str]:
    """The `rclone copy` command line. `few`: a short file list, where
    --no-traverse (look up each file) beats listing the whole Drive folder."""
    args = [cfg.rclone, "copy", str(root), cfg.data_dest,
            "--files-from-raw", str(files_from),
            "--backup-dir", cfg.history_dest(stamp),
            "--checksum",               # Drive stores md5; mtime isn't trusted
            "-v"]
    if few:
        args.append("--no-traverse")
    if not apply:
        args.append("--dry-run")
    return args


def selection(cfg: GdriveConfig, root: Path, ledgers_only: bool) -> list[str]:
    return local_files(root, LEDGER_INCLUDE if ledgers_only else cfg.include)


def push(cfg: GdriveConfig, root: Path, *, apply: bool, ledgers_only: bool = False,
         run=subprocess.run) -> int:
    """Copy the selected files to Drive. Returns rclone's exit code."""
    if not shutil.which(cfg.rclone):
        raise GdriveError(f"{cfg.rclone!r} not found — install rclone (docs/offsite-storage.md)")
    rels = selection(cfg, root, ledgers_only)
    if not rels:
        print("gdrive: nothing selected to copy")
        return 0
    fd, tmp = tempfile.mkstemp(prefix="gdrive_files_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(rels) + "\n")
        args = copy_args(cfg, root, Path(tmp), _stamp(), apply=apply, few=ledgers_only)
        print(f"gdrive: {len(rels)} file(s) {root} -> {cfg.data_dest}"
              f"{'' if apply else '  (dry run)'}", flush=True)
        return run(args).returncode
    finally:
        os.unlink(tmp)


def check(cfg: GdriveConfig, run=subprocess.run) -> None:
    """Round-trip a probe file through the Drive folder."""
    if not shutil.which(cfg.rclone):
        raise GdriveError(f"{cfg.rclone!r} not found — install rclone (docs/offsite-storage.md)")
    probe = f"{cfg.remote}:{cfg.folder}/.gdrive_probe"
    data = dt.datetime.now(dt.timezone.utc).isoformat()
    r = run([cfg.rclone, "rcat", probe], input=data, text=True, capture_output=True)
    if r.returncode:
        raise GdriveError(f"write to {probe} failed: {r.stderr.strip()}")
    r = run([cfg.rclone, "cat", probe], text=True, capture_output=True)
    if r.returncode or r.stdout != data:
        raise GdriveError(f"read back from {probe} failed: {r.stderr.strip() or 'content differs'}")


# ---------------------------------------------------------------------------
# Automatic push after a ledger write
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True
    # Windows: os.kill(pid, 0) would terminate the process, so ask for a handle.
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return k32.GetLastError() == 5         # access denied: exists, not ours
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259               # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _lock_alive(lock: Path) -> bool:
    """A crashed worker's lock must not stop the next push: the lock is live
    only while its pid runs, and never past LOCK_STALE."""
    try:
        pid = int(lock.read_text().strip() or 0)
        age = time.time() - lock.stat().st_mtime
    except (OSError, ValueError):
        return False
    if age > LOCK_STALE:
        return False
    return _pid_alive(pid) if pid else True


def _take_lock(lock: Path) -> bool:
    if lock.exists() and not _lock_alive(lock):
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def ledger_changed(path, *, root: Optional[Path] = None, spawn=subprocess.Popen) -> bool:
    """Called right after a ledger file is written. Starts (or nudges) the
    background Drive push. Best-effort: never raises, never waits. Returns
    True when it started a worker.

    Only the main checkout's ledgers count: a test's temporary ledger, or a
    worktree's own copy (GH #103), is not the business record."""
    try:
        if os.environ.get("EBAYBIZ_GDRIVE_AUTO", "1") == "0":
            return False
        root = (root or data_root()).resolve()
        if Path(path).resolve().parent != root:
            return False
        cfg = load_gdrive_config()
        if not cfg.auto or not shutil.which(cfg.rclone):
            return False
        (root / PENDING_FILE).write_text(str(time.time_ns()))
        if _lock_alive(root / LOCK_FILE):
            return False                 # the running worker will see the nudge
        log = open(root / LOG_FILE, "ab")
        kw = ({"start_new_session": True} if os.name == "posix" else
              {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)
                                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)})
        spawn([sys.executable, str(Path(__file__).resolve()), "_worker", "--root", str(root)],
              cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
              close_fds=True, **kw)
        log.close()
        return True
    except Exception:                    # bookkeeping must not break a publish
        return False


def _worker(root: Path, debounce: float = DEBOUNCE) -> int:
    """Push the ledgers until no new write arrived during the last push."""
    lock, pending = root / LOCK_FILE, root / PENDING_FILE
    cfg = load_gdrive_config()
    rc = 0
    while _take_lock(lock):
        try:
            while True:
                time.sleep(debounce)
                try:
                    seen = pending.read_text()
                except FileNotFoundError:
                    break
                print(f"[{_stamp()}] ledger changed — pushing", flush=True)
                try:
                    rc = push(cfg, root, apply=True, ledgers_only=True)
                except GdriveError as e:
                    print(f"gdrive: {e}", flush=True)
                    rc = 2
                print(f"[{_stamp()}] rclone exit {rc}", flush=True)
                try:
                    if pending.read_text() == seen:
                        pending.unlink()
                except FileNotFoundError:
                    pass
        finally:
            lock.unlink(missing_ok=True)
        if not pending.exists():         # nothing arrived between break and unlock
            break
    return rc


# ---------------------------------------------------------------------------
# Daily timer (systemd --user)
# ---------------------------------------------------------------------------

def unit_files(python: str, repo: Path) -> dict[str, str]:
    return {
        f"{UNIT_NAME}.service": (
            "[Unit]\n"
            "Description=EBAYBIZ: copy the listing data to Google Drive (lib/gdrive_mirror.py)\n\n"
            "[Service]\nType=oneshot\n"
            f"WorkingDirectory={repo}\n"
            f"ExecStart={python} -m lib.cli gdrive push --apply\n"),
        f"{UNIT_NAME}.timer": (
            "[Unit]\nDescription=EBAYBIZ: daily Google Drive copy\n\n"
            "[Timer]\nOnCalendar=*-*-* 03:15:00\nRandomizedDelaySec=15m\n"
            "Persistent=true\n\n[Install]\nWantedBy=timers.target\n"),
    }


def install_timer(apply: bool) -> int:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    repo = data_root()
    python = repo / ".venv" / "bin" / "python"
    files = unit_files(str(python if python.exists() else sys.executable), repo)
    for name, text in files.items():
        print(f"--- {unit_dir / name}\n{text}")
    if not apply:
        print("dry run — re-run with --apply to write these and enable the timer")
        return 0
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (unit_dir / name).write_text(text)
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", f"{UNIT_NAME}.timer"]):
        if subprocess.run(cmd).returncode:
            return 1
    subprocess.run(["systemctl", "--user", "list-timers", f"{UNIT_NAME}.timer"])
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ebz gdrive", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("check", "push", "install-timer", "_worker"))
    ap.add_argument("--apply", action="store_true", help="actually copy (default: dry run)")
    ap.add_argument("--ledgers", action="store_true", help="push: the ledger files only")
    ap.add_argument("--root", type=Path, help="data root (default: the main checkout)")
    a = ap.parse_args(argv)
    try:
        if a.action == "install-timer":
            return install_timer(a.apply)
        root = (a.root or data_root()).resolve()
        if a.action == "_worker":
            return _worker(root)
        cfg = load_gdrive_config()
        if a.action == "check":
            check(cfg)
            print(f"ok — {cfg.remote}:{cfg.folder}: write/read round-trip passed")
            return 0
        return push(cfg, root, apply=a.apply, ledgers_only=a.ledgers)
    except GdriveError as e:
        print(f"gdrive: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
