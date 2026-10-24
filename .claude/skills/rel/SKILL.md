---
name: rel
description: Run the daily release. The technical-director reviews every open PR, posts its verdicts and returns the merge order and the next version tag; Russell gets one card with a single command block that merges, pulls the main checkout and tags the release. Use when Russell types /rel or asks to review and merge the open PRs, "what can merge today", or to cut a release.
---

# /rel — review and merge the open PRs

Typing `/rel` is the ask to run the whole release. The technical director
makes every call (verdicts, merge order, what to close, the version). Russell
only clicks merge, so the end of this skill is one card with one command block
he runs as a whole.

## Hand it off

Dispatch the `technical-director` agent (Agent tool, `run_in_background:
false`, since the card waits on it) with this brief:

- Review every open PR the way
  [`.claude/agents/technical-director.md`](../../agents/technical-director.md)
  says: read the diffs, check CI on both OSes, look for collisions between
  open PRs, post one `gh pr review <n> --comment` per PR starting with its
  verdict. Review branches in a worktree, never in the main checkout.
- Mark reviewable drafts ready (`gh pr ready <n>`) and decide stale and
  overlapping PRs (merge, fold or close) yourself.
- Work out the next version from the last tag (`git describe --tags
  --abbrev=0 origin/main`) and the biggest change among the PRs that will
  merge.
- Return the merge card and nothing else: **Merge now, in this order** (PR
  link and one line each), **Needs a fix first** (PR, must-fix, owner),
  **Close** (PR, reason), and **Release** (version and the release note,
  one line per PR with its link, plus anything Russell must do after
  pulling).

Don't hand Russell the TD's questions about code, order or process. The TD
decides them.

## After

Hand each **Needs a fix first** item to its owner (usually the `developer`)
with the TD's must-fix word for word, in the background. Don't wait on them;
they land in the next `/rel`.

## Reply

One operator card ([`.claude/agents/operator.md`](../../agents/operator.md)):
the merge list, then one `bash` block that does the whole release in order.
Fill in every value. The tag goes on the merge commit of the last PR, so read
it from `origin/main` after the merges rather than guessing a SHA:

```bash
gh pr merge 301 --merge && \
gh pr merge 305 --merge && \
git -C /home/russ/EBAYBIZ fetch origin && \
git -C /home/russ/EBAYBIZ pull --ff-only && \
git -C /home/russ/EBAYBIZ tag -a v5.5.0 origin/main -m "Release 5.5.0

- #301 feat: ...
- #305 fix: ..." && \
git -C /home/russ/EBAYBIZ push origin v5.5.0
```

Then one line each for what's waiting on a fix (and who has it) and what the
TD closed. If any merging PR is marked breaking (`!`), put its **Breaking**
step on the card as a numbered hands-on step after the block.

- **Nothing ready to merge:** say so in one line, with what's waiting on whom.
  No command block.
- **Nothing merged since the last tag and nothing to merge:** "Queue's clear,
  no release today." One line.
