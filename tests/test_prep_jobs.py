#!/usr/bin/env python3
"""`--apply --jobs N` has to survive the way PREP is actually launched.

On Windows a `ProcessPoolExecutor` pickles its task BY QUALIFIED NAME and the
worker re-imports that module to get it back. The task was defined in
`lib/photo_prep/prep.py`, which is importable — except on the one path the
operator uses:

    python -m lib.cli prep <shoot> --apply --jobs 3

`lib/cli.py` dispatches with `runpy.run_module(mod, run_name="__main__")`, and
`run_module` defaults to `alter_sys=False`: the module's code runs in a bare
globals dict named `"__main__"` that is never installed in `sys.modules`, while
`sys.modules["__main__"]` is still `lib/cli.py`. Every function defined by that
run claims `__module__ == "__main__"` and none of them can be looked up there,
so the submit died in the parent's queue feeder thread before one frame was
dispatched:

    _pickle.PicklingError: Can't pickle <function _apply_worker at 0x...>:
    attribute lookup _apply_worker on __main__ failed

Reproduced 2026-09-20 on Windows 11 / Python 3.12 against
inventory/THRIFT/home-casino (12 frames). `--jobs 1` pickles nothing and never
noticed.

WHY ONE OF THESE TESTS SHELLS OUT. An in-process `run_apply(..., jobs=2)`
passes with the bug still in place — imported normally, prep's `__module__` is
`lib.photo_prep.prep` and the pickle resolves fine. Only a real
`python -m lib.cli` run reproduces the condition, so that is what
`test_jobs_2_works_through_the_real_cli_entry_point` does. The rest stay
in-process and cheap.

The second half is the exit code: the run printed its traceback and still
exited 0, so a backgrounded `--apply --jobs N` looked like a success while
having rendered nothing, and the next stage built a review card on whatever
the previous run left behind.

Run:  python tests/test_prep_jobs.py
  or: pytest tests/test_prep_jobs.py
"""
import importlib
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.photo_prep import prep as P                          # noqa: E402

# Small enough that a cold CLI run (cv2 + the orientation model dominate) stays
# under about twenty seconds, big enough for the colour pass to read a sweep.
SIZE = (900, 1200)


def _scene(seed: int) -> np.ndarray:
    h, w = SIZE
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 225, np.uint8)
    img = np.clip(img.astype(np.int16)
                  + rng.normal(0, 3, img.shape).astype(np.int16),
                  0, 255).astype(np.uint8)
    img[h // 3:2 * h // 3, w // 3:2 * w // 3] = (70, 110, 190)
    return img


def _shoot(tmp: Path, n: int = 2) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cv2.imencode(".jpg", _scene(i))[1].tofile(str(tmp / f"IMG_{i}.jpg"))
    return tmp


def _prepped(td: Path, n: int = 2) -> Path:
    shoot = _shoot(td / "s", n=n)
    P.run_auto(shoot, "1:1", P.DEFAULT_PAD, "gentle", quiet=True)
    P.run_approve_auto(shoot)
    return shoot


def _preset_shas(shoot: Path, preset: str = "crisp") -> dict:
    return {n: r["presets"][preset]["sha256"]
            for n, r in P.load_manifest(shoot)["photos"].items()}


# ---------------------------------------------------------------------------
# the pool task's name, which is the whole bug
# ---------------------------------------------------------------------------

def test_the_pool_task_is_defined_somewhere_a_worker_can_import():
    """The cheap, instant statement of the rule: whatever `run_apply` submits
    must live in a module that is importable UNDER THE NAME IT CLAIMS, and that
    NOTHING EVER RUNS AS A SCRIPT.

    The second half is the one that bites, and it is why this test looks at the
    source file rather than only at `__module__`. Imported normally — which is
    how this test imports it, and how every other prep test does — a task
    defined in `prep.py` reports `lib.photo_prep.prep` and resolves fine. It
    only claims `"__main__"` when prep is the module being *executed*, which is
    exactly what `python -m lib.cli prep` and `python -m lib.photo_prep.prep`
    both do. So the checkable invariant is: the pool task does not live in a
    module with a `__main__` guard. Move it back into `prep.py` and this fails
    without needing to spawn anything."""
    fn = P._apply_worker
    assert fn.__module__ != "__main__", (
        "the pool task claims __module__ == '__main__' — it will not unpickle "
        "in a spawned worker; define it in a real module")
    mod = importlib.import_module(fn.__module__)
    assert getattr(mod, fn.__qualname__, None) is fn, (
        f"{fn.__module__}.{fn.__qualname__} does not resolve back to the "
        f"submitted function — pickle looks it up exactly this way")
    src = Path(mod.__file__).read_text("utf-8")
    assert '__name__ == "__main__"' not in src, (
        f"{fn.__module__} is runnable as a script, so anything that runs it "
        f"(python -m, or lib/cli.py's runpy dispatch) makes the pool task's "
        f"pickled name '__main__.{fn.__qualname__}' — which no worker can "
        f"look up. Keep the task in a module nothing executes.")


def test_the_pool_task_pickles():
    """What `pool.submit` actually does to it, stated directly. This is the
    call that raised PicklingError, minus the pool."""
    assert pickle.loads(pickle.dumps(P._apply_worker)) is P._apply_worker


def test_the_worker_module_does_not_import_prep_at_module_scope():
    """`prep` imports the worker module, so the worker must reach back into
    `prep` lazily. A module-scope import the other way is circular, and it
    breaks only in the child — i.e. only under `--jobs N`, only on spawn."""
    src = (ROOT / "lib" / "photo_prep" / "apply_worker.py").read_text("utf-8")
    bad = [ln for ln in src.splitlines()
           if ln.startswith(("import ", "from ")) and "prep" in ln]
    assert not bad, f"module-scope import of prep would be circular: {bad}"


# ---------------------------------------------------------------------------
# the real entry point — the only place the bug reproduces
# ---------------------------------------------------------------------------

def test_jobs_2_works_through_the_real_cli_entry_point():
    """`python -m lib.cli prep ... --jobs 2`, end to end, because the runpy
    dispatch IS the condition. Asserts all three things the bug broke: it
    exits 0, it does not print a PicklingError, and listing/ actually holds one
    rendered frame per source."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _shoot(Path(td) / "s", n=2)
        r = subprocess.run(
            [sys.executable, "-m", "lib.cli", "prep", str(shoot),
             "--apply", "--only", "crisp", "--jobs", "2"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        out = r.stdout + r.stderr
        assert "PicklingError" not in out, (
            "the pool task did not survive being pickled for a spawned "
            f"worker:\n{out[-2000:]}")
        assert r.returncode == 0, f"exit {r.returncode}\n{out[-2000:]}"
        rendered = sorted(p.name for p in (shoot / "listing").glob("*.jpg"))
        assert rendered == ["IMG_0.jpg", "IMG_1.jpg"], (
            f"--jobs 2 rendered {rendered}\n{out[-2000:]}")


# ---------------------------------------------------------------------------
# the pool path and the serial path must not drift
# ---------------------------------------------------------------------------

def test_the_pool_path_renders_the_same_pixels_as_the_serial_one():
    """Both branches call `_render_frame`, which is the point of it existing.
    Hashing the presets is what makes that a promise rather than a comment."""
    with tempfile.TemporaryDirectory() as td:
        serial = _prepped(Path(td) / "a")
        pooled = _prepped(Path(td) / "b")
        P.run_apply(serial, quiet=True, only=("crisp",), jobs=1)
        P.run_apply(pooled, quiet=True, only=("crisp",), jobs=2)
        assert _preset_shas(serial) == _preset_shas(pooled), (
            "--jobs N produced different pixels from --jobs 1")


def test_the_pool_path_keeps_manifest_order():
    """A pool completes out of order; the contact sheet is read top to bottom
    against the shoot. `rows` is reassembled from the manifest for exactly this
    reason, and the manifest's own order has to survive the fold-in."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td), n=4)
        m = P.run_apply(shoot, quiet=True, only=("crisp",), jobs=3)
        assert list(m["photos"]) == [f"IMG_{i}.jpg" for i in range(4)]


# ---------------------------------------------------------------------------
# a run that rendered nothing must not look like a success
# ---------------------------------------------------------------------------

def test_a_frame_that_did_not_render_fails_the_run():
    """The silent half of the bug. An apply that could not render a frame used
    to print a FLAG line, auto-pick a look and exit 0 — so a backgrounded
    `--jobs N` that rendered NOTHING still read as done."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td), n=2)
        (shoot / "IMG_1.jpg").unlink()

        rc = P.main([str(shoot), "--apply", "--only", "crisp", "--quiet"])

        assert rc == 1, "an apply that left a frame unrendered must exit non-zero"
        m = P.load_manifest(shoot)
        assert m["apply_run"]["failed"] == ["IMG_1.jpg"], m["apply_run"]
        assert not list((shoot / "listing").glob("*.jpg")), (
            "listing/ must not be repopulated by a failed apply — a stale "
            "render copied in over a frame that did not render this run is "
            "worse than none, because the next stage cannot tell")


def test_a_clean_run_still_exits_zero_and_records_no_failures():
    """The other half: the gate must not turn ordinary runs red."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td), n=2)
        rc = P.main([str(shoot), "--apply", "--only", "crisp", "--quiet"])
        assert rc == 0
        m = P.load_manifest(shoot)
        assert m["apply_run"]["failed"] == []
        assert m["chosen_preset"] == "crisp"
        assert len(list((shoot / "listing").glob("*.jpg"))) == 2


def test_a_worker_exception_is_isolated_reported_and_fails_the_run():
    """Per-frame exception isolation (docs/prep-resume-plan.md) and the new
    exit code, together: one bad frame must not sink the others, and must not
    let the run report success either."""
    with tempfile.TemporaryDirectory() as td:
        shoot = _prepped(Path(td), n=2)
        (shoot / "IMG_1.jpg").write_bytes(b"not a jpeg")

        m = P.run_apply(shoot, quiet=True, only=("crisp",), jobs=2)

        assert m["photos"]["IMG_1.jpg"]["status"] == "ERROR"
        assert m["photos"]["IMG_0.jpg"]["status"] in ("PICK", "ASK"), (
            "the good frame must still have rendered")
        assert m["apply_run"]["failed"] == ["IMG_1.jpg"], m["apply_run"]


if __name__ == "__main__":
    fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS  {_name}")
            except Exception as _e:                           # noqa: BLE001
                fails += 1
                print(f"FAIL  {_name}: {_e}")
    sys.exit(1 if fails else 0)
