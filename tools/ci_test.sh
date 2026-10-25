#!/usr/bin/env bash
# Run the full test suite in GitHub Actions on a pushed branch, and wait for it.
#
#   tools/ci_test.sh            # the current branch
#   tools/ci_test.sh <branch>   # any branch already on origin
#
# The full suite runs in CI, not on this machine or green (decision 2026-10-24):
# full local runs beside the desktop app, Firefox and OBS got the app
# OOM-killed. CI tests what is on origin, so this refuses uncommitted or
# unpushed work instead of reporting a green run for code it never saw.
#
# Dispatches .github/workflows/tests.yml (workflow_dispatch), finds that run
# without mistaking a parallel dispatch for it (a run that was not there
# before, on this exact commit, created after the dispatch started), then
# `gh run watch --exit-status`. On failure it prints the failed jobs' log tail
# and exits non-zero. It waits minutes, so start it with run_in_background
# (tools/bg_guard.py). It loads nothing locally, so it is allowed on air.
set -euo pipefail

WORKFLOW=tests.yml
POLL_S=${CI_TEST_POLL_S:-3}       # wait between looks for the new run
POLL_N=${CI_TEST_POLL_N:-40}      # looks before giving up (~2 min at 3 s)
TAIL_N=${CI_TEST_TAIL_N:-80}      # failed-log lines to print

die() { printf 'ci_test: %s\n' "$*" >&2; exit 2; }

current=$(git rev-parse --abbrev-ref HEAD)
branch=${1:-$current}
[[ $branch != HEAD ]] || die "detached HEAD: name the branch, e.g. tools/ci_test.sh my-branch"

if [[ $branch == "$current" ]]; then
  dirty=$(git status --porcelain --untracked-files=no)
  [[ -z $dirty ]] || die "uncommitted changes on $branch, and CI only tests what is pushed.
Commit and push first, then run this again:
$dirty"
  # New files are not uncommitted changes to git, but CI will not have them.
  new_py=$(git ls-files --others --exclude-standard -- '*.py')
  [[ -z $new_py ]] || printf 'ci_test: warning: untracked, not in CI:\n%s\n' "$new_py" >&2
fi

git fetch -q origin "refs/heads/$branch" 2>/dev/null ||
  die "$branch is not on origin. Push first:  git push -u origin $branch"
sha=$(git rev-parse FETCH_HEAD)
if local_sha=$(git rev-parse -q --verify "refs/heads/$branch"); then
  [[ $local_sha == "$sha" ]] || die "local $branch ($local_sha) is not what origin has ($sha).
Push first:  git push origin $branch"
fi

runs() {  # id, headSha, createdAt, url of recent dispatches of this workflow on the branch
  gh run list --workflow "$WORKFLOW" --branch "$branch" --event workflow_dispatch --limit 30 \
    --json databaseId,headSha,createdAt,url \
    --jq '.[] | [.databaseId, .headSha, .createdAt, .url] | @tsv'
}

before=$(runs | cut -f1 | tr '\n' ' ')
# A minute of slack for clock skew between here and GitHub. A run that was
# already listed above is never ours, so the slack cannot pick an old run.
since=$(date -u -d "@$(( $(date +%s) - 60 ))" +%Y-%m-%dT%H:%M:%SZ)
gh workflow run "$WORKFLOW" --ref "$branch" ||
  die "the dispatch was refused (gh's error above). An HTTP 403 means the gh token cannot
write Actions: a fine-grained token needs 'Actions: Read and write' on this repo.
Until then, open the branch's PR: CI runs the suite on every PR."
printf 'ci_test: dispatched %s on %s at %s\n' "$WORKFLOW" "$branch" "${sha:0:12}"

run_id='' url=''
for (( i = 0; i < POLL_N; i++ )); do
  # Oldest new match: a dispatch someone fires after ours, on the same commit,
  # tests the same code, and one on another commit fails the headSha test.
  read -r run_id url < <(runs | awk -F'\t' -v sha="$sha" -v since="$since" -v before="$before" '
      BEGIN { n = split(before, b, " "); for (k = 1; k <= n; k++) seen[b[k]] = 1 }
      $2 == sha && $3 >= since && !($1 in seen) { line = $1 " " $4 }
      END { if (line != "") print line }') || true
  [[ -n $run_id ]] && break
  sleep "$POLL_S"
done
[[ -n $run_id ]] || die "dispatched, but no new run for ${sha:0:12} showed up; look at: gh run list --workflow $WORKFLOW --branch $branch"

printf 'ci_test: watching run %s  %s\n' "$run_id" "$url"
if gh run watch "$run_id" --exit-status --interval 15; then
  printf 'ci_test: PASSED  %s\n' "$url"
  exit 0
fi
printf '\nci_test: FAILED  %s\n--- last %s lines of the failed jobs ---\n' "$url" "$TAIL_N" >&2
gh run view "$run_id" --log-failed | tail -n "$TAIL_N" >&2 || true
exit 1
