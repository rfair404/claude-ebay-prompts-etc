"""The `--apply --jobs N` pool task, in a module that is never `__main__`.

This is one function, and it lives in its own file for one reason: on
Windows a `ProcessPoolExecutor` pickles the callable BY QUALIFIED NAME
(`__module__` + `__qualname__`) and the child re-imports that module to get
it back. So the module a pool task is defined in has to be importable under
the name it advertises — and `lib/photo_prep/prep.py` is not, whenever PREP
is reached the way the operator actually reaches it:

    python -m lib.cli prep <shoot> --apply --jobs 3

`lib/cli.py` dispatches with `runpy.run_module(mod, run_name="__main__")`,
and `run_module` defaults to `alter_sys=False` — the module's code is
executed in a bare globals dict whose `__name__` is `"__main__"` and which
is NEVER installed in `sys.modules`. Everything defined by that run
therefore claims `__module__ == "__main__"`, while `sys.modules["__main__"]`
is still `lib/cli.py`. Pickling the task then fails in the parent's queue
feeder thread, before a single frame is dispatched:

    _pickle.PicklingError: Can't pickle <function _apply_worker at 0x...>:
    attribute lookup _apply_worker on __main__ failed

`--jobs 1` never noticed, because nothing is pickled on that path.
Reproduced 2026-09-20 on Windows 11 / Python 3.12.

Defining the task here instead makes the name it pickles under
(`lib.photo_prep.apply_worker.apply_worker`) one a child can import no
matter how the parent was launched — `python -m lib.cli prep`,
`python -m lib.photo_prep.prep`, `import lib.photo_prep.prep` from a test,
or a future dispatcher that does something else again. It is a property of
this module, not of the caller, which is the point: the next entry point
cannot silently take it away.

`prep` is imported INSIDE the function, not at module scope, because `prep`
imports this module — and because the parent never calls this function at
all, only pickles a reference to it. The import happens once per worker
process, on its first task.
"""
from __future__ import annotations

from pathlib import Path


def apply_worker(shoot: Path, name: str, rec: dict, aspect, pad: float,
                 smode: str, only: tuple) -> tuple:
    """`--jobs N` task body: render one frame inside a pool worker process.

    Takes and returns only picklable data — no manifest, no shared state,
    nothing captured by closure — because `ProcessPoolExecutor` pickles the
    call. `before`/`variants` are deliberately NOT returned: shipping
    full-resolution images back over the pool's pipe would be the expensive
    part all over again, so the parent instead reloads a rendered frame's
    pixels from disk afterwards for the sheet, the same way `--resume`
    already does for a frame it skips (docs/prep-resume-plan.md, the
    `--jobs N` section).

    Exceptions are caught and returned rather than raised: one bad source
    file must not sink every other frame's future already queued in the pool
    ("per-frame exception isolation" in the same design-doc section). The
    parent turns a returned error into `status: ERROR` + a flag, and — since
    #138 — into a non-zero exit, so a batch that rendered nothing cannot
    still look like a success.
    """
    from . import prep as prepmod

    try:
        new_rec, _before, _variants = prepmod._render_frame(
            shoot, name, rec, aspect, pad, smode, only)
        return name, new_rec, None
    except Exception as exc:                  # noqa: BLE001 -- see docstring
        return name, None, f"{type(exc).__name__}: {exc}"
