# Repo-wide instructions

## Shared working directory — use a worktree for git state changes

This directory is opened by many concurrent Claude Code sessions at once
(interactive terminals, Remote Control, scheduled tasks). They all share one
git working tree. A `git checkout`/`switch`/`stash` in one session changes
the files every other session sees, mid-task, with no warning to them.

**Any session about to run `git checkout <branch>`, `git switch`, `git
stash` (push or pop), or `git checkout -b <new-branch>` must call
`EnterWorktree` first**, unless it is only reading git state (`status`,
`log`, `diff`, `show`) — reads are safe and don't need a worktree.

`EnterWorktree` creates an isolated checkout under `.claude/worktrees/` and
moves *this session's* working directory there; it does not disturb any
other session. Use `ExitWorktree` with `action: "keep"` when handing the
branch off for review, or `action: "remove"` once its PR has merged and
there's nothing left to keep.

This rule exists because a session once ran `git checkout main` →
`git checkout -b <feature>` directly in the shared directory, silently
switching the working tree under every other concurrent session.

## Concurrent sessions duplicate work — check before you start

The same fact that makes the worktree rule necessary — many sessions open on
this repo at once — makes them solve the same problem twice without knowing
it. That is more expensive than a merge conflict, because most of it merges
*cleanly* and the duplication only surfaces later as two APIs for one job, or
two conventions for one filename.

Measured on 2026-09-23/24: thirteen PRs in one day, and four files absorbed
nearly all of them — `lib/ebay_client.py` and `lib/list_edit.py` (five PRs
each), `lib/config.py` and `tools/pick_list_html.py` (four each).

**Before filing an issue, search for it.**

    gh issue list --search "<keywords>" --state open

Say in the issue what you searched and what you found. #158 was filed as a
new per-store-ledger issue when #156 §3 already covered it in more depth —
and because the two issues were implemented separately, one PR ignored
`listings_ledger.*.csv` while the other's code wrote `listings_ledger-*.csv`.
The ignore rule missed the real files. One duplicate issue produced a gap
that would have let account data be committed.

**Before editing a hot file, look for an open PR that already touches it.**

    gh pr list --state open --json number,title,files \
      -q '.[] | select(.files[].path == "lib/list_edit.py") | .number'

If one exists: branch from *that* PR's head rather than `main`, or say so and
pick different work. Do not start a parallel implementation because the other
PR "isn't merged yet" — that is precisely how the duplicate arises.

**Don't leave a PR in draft once it is reviewable.** A draft is a dependency
nobody can see. #159's own description says it invented a second
`_resolve_store()` rather than block on #150, which was sitting in draft; the
two definitions then merged into one module, where Python keeps the last and
the other's calls fail at runtime.

**Dependent work branches from the dependency's branch, not `main`.** If your
change only makes sense once another PR lands, base it there and say so.

**One issue, one PR, when the files overlap.** A multi-section audit issue
(#156) fanned into PRs that each touched `.gitignore` and each invented their
own convention. Split by *file ownership*, not by section number.

**A clean merge is not a coherent result.** When two branches that touched the
same area are combined, re-read the merged region rather than trusting git.
Merging #147/#156/#158 produced, all without a single conflict marker: two
functions with one name, two APIs for creating a no-returns policy, two
`Store:` lines on one card, and the ignore-pattern gap above. Only one of the
five collisions that day was a conflict git would show you.
