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
