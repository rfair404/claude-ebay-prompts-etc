#!/usr/bin/env bash
# Run the full test suite in GitHub Actions on a branch's commit, and wait for it.
#
#   tools/ci_test.sh            # the current branch's HEAD
#   tools/ci_test.sh <branch>   # a local branch's tip
#
# The full suite runs in CI, not on this machine or green (decision 2026-10-24):
# full local runs beside the desktop app, Firefox and OBS got the app
# OOM-killed.
#
# It pushes the commit to ci/<branch> on origin, and .github/workflows/tests.yml
# runs on every push to ci/**. That needs only push access, which every session
# has; `gh workflow run` needs a token that can write Actions, and the laptop's
# cannot. ci/<branch> is a throwaway ref this script owns: it is force-pushed (a
# rebased branch must not be refused) and deleted again on exit, so every push
# starts a fresh run. ci/ is reserved for this script, so it refuses when an
# open PR's branch already has that name. Then it finds the new run of that
# exact commit (headSha, event push, branch ci/<branch>) and
# `gh run watch --exit-status`. On failure it prints the failed jobs' log tail
# and exits non-zero. The run and its logs outlive the ref.
#
# Uncommitted changes are refused, because CI would test the commit without
# them. It waits minutes, so start it with run_in_background
# (tools/bg_guard.py). It loads nothing locally, so it is allowed on air.
set -euo pipefail

WORKFLOW=tests.yml
POLL_S=${CI_TEST_POLL_S:-3}       # wait between looks for the run
POLL_N=${CI_TEST_POLL_N:-40}      # looks before giving up (~2 min at 3 s)
TAIL_N=${CI_TEST_TAIL_N:-80}      # failed-log lines to print

die() { printf 'ci_test: %s\n' "$*" >&2; exit 2; }

current=$(git rev-parse --abbrev-ref HEAD)
branch=${1:-$current}
[[ $branch != HEAD ]] || die "detached HEAD: name the branch, e.g. tools/ci_test.sh my-branch"
# main is tested on every push to it, and ci/main would be a second copy of it.
[[ $branch != main ]] || die "main is tested by CI on every push; run this from a feature branch"

if [[ $branch == "$current" ]]; then
  src=HEAD
  dirty=$(git status --porcelain --untracked-files=no)
  [[ -z $dirty ]] || die "uncommitted changes on $branch, and CI only tests a commit.
Commit first, then run this again:
$dirty"
  # New files are not uncommitted changes to git, but CI will not have them.
  new_py=$(git ls-files --others --exclude-standard -- '*.py')
  [[ -z $new_py ]] || printf 'ci_test: warning: untracked, not in CI:\n%s\n' "$new_py" >&2
else
  src="refs/heads/$branch"
fi
sha=$(git rev-parse -q --verify "$src^{commit}") || die "no local branch $branch"
ci_ref="ci/$branch"

# The force-push below would overwrite a working branch literally named ci/<x>.
open_pr=$(gh pr list --head "$ci_ref" --state open --json number -q '.[].number') ||
  die "could not check for an open PR on $ci_ref (gh's error above)"
[[ -z $open_pr ]] || die "$ci_ref is an open PR's branch (#$open_pr), and ci/ is reserved for this script.
Rename that branch; this would force-push over it."

runs() {  # id, headSha, url of recent push runs of this workflow on the ci ref
  gh run list --workflow "$WORKFLOW" --branch "$ci_ref" --event push --limit 30 \
    --json databaseId,headSha,url --jq '.[] | [.databaseId, .headSha, .url] | @tsv'
}
# Runs from earlier invocations stay listed after their ref is gone; ours is new.
before=$(runs | cut -f1 | tr '\n' ' ')

git push -q -f origin "$src:refs/heads/$ci_ref" ||
  die "could not push $branch to $ci_ref on origin (git's error above)"
trap 'git push -q origin --delete "$ci_ref" 2>/dev/null || true' EXIT
printf 'ci_test: pushed %s to %s\n' "${sha:0:12}" "$ci_ref"

run_id='' url=''
for (( i = 0; i < POLL_N; i++ )); do
  read -r run_id url < <(runs | awk -F'\t' -v sha="$sha" -v before="$before" '
      BEGIN { n = split(before, b, " "); for (k = 1; k <= n; k++) seen[b[k]] = 1 }
      $2 == sha && !($1 in seen) { print $1 " " $3; exit }') || true
  [[ -n $run_id ]] && break
  sleep "$POLL_S"
done
[[ -n $run_id ]] || die "pushed, but no new run for ${sha:0:12} showed up; look at: gh run list --workflow $WORKFLOW --branch $ci_ref"

printf 'ci_test: watching run %s  %s\n' "$run_id" "$url"
if gh run watch "$run_id" --exit-status --interval 15; then
  printf 'ci_test: PASSED  %s\n' "$url"
  exit 0
fi
printf '\nci_test: FAILED  %s\n--- last %s lines of the failed jobs ---\n' "$url" "$TAIL_N" >&2
gh run view "$run_id" --log-failed | tail -n "$TAIL_N" >&2 || true
exit 1
