#!/usr/bin/env bash
# The shipping station on green (RUN.md, "Green: the shipping station").
#
#   tools/ship_station.sh push     # the laptop's ship queue -> green (after each pick-list poll)
#   tools/ship_station.sh pull     # green's boxes.csv -> <main>/reports/shipping/boxes-green.csv
#   tools/ship_station.sh setup    # green's code, venv and `serve` user unit; re-run after a merge
#   tools/ship_station.sh status   # serve up? code current? queue fresh? QL-1100 ready?
#   tools/ship_station.sh --dry-run <command>   # say what it would do; change nothing
#
# Every connection opens from the laptop (`ssh green`, IPv4 only, as
# tools/green_setup.py's firewall expects). Green gets the merged code,
# origin/main as `git archive` (tracked files only: never config.yaml, the
# ledgers or a key, which are all untracked), and the ship queue. It sends back only
# boxes.csv. Green is optional: push and pull give up after 5 s with one
# line, and the laptop carries on.
#
# On green:  ~/pops-ship/            the code, replaced whole by `setup`
#            ~/pops-ship/reports  -> ~/pops-ship-data/reports  (survives redeploys)
#            ~/pops-ship-venv/       fastapi, uvicorn, pyyaml, pillow: what `serve` imports
#            ~/.config/systemd/user/pops-serve.service   `python -m lib.cli serve`, 127.0.0.1:8770,
#            started at boot (linger)
set -euo pipefail

HOST=${SHIP_STATION_HOST:-green}
MAIN=${SHIP_STATION_MAIN:-$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')}
APP=pops-ship
DATA=pops-ship-data
VENV=pops-ship-venv
UNIT=pops-serve.service
URL=http://127.0.0.1:8770/ship/box
QUEUE=reports/shipping/ship_queue.json
BOXES=reports/shipping/boxes.csv
REF=${SHIP_STATION_REF:-origin/main}   # what green runs: merged main, not the main checkout's HEAD
BACK=$MAIN/reports/shipping/boxes-green.csv   # never boxes.csv: green is its only writer
DEPS="'fastapi>=0.121.0' 'uvicorn>=0.38.0' pyyaml pillow"
SSH=(ssh -o AddressFamily=inet -o BatchMode=yes -o ConnectTimeout=5)

DRY=0
if [[ ${1:-} == --dry-run ]]; then DRY=1; shift; fi
say() { echo "ship station: $*" >&2; }
on_green() { "${SSH[@]}" "$HOST" "$@"; }

push() {
  local src=$MAIN/$QUEUE
  [[ -s $src ]] || { say "no $QUEUE in the main checkout yet; nothing to push"; return 0; }
  if ((DRY)); then echo "would copy $src -> $HOST:~/$DATA/$QUEUE (temp file, then rename)"; return 0; fi
  local rc=0
  on_green "[ -d ~/$DATA/reports ] || exit 3; mkdir -p ~/$DATA/reports/shipping &&
    cat > ~/$DATA/$QUEUE.tmp && mv ~/$DATA/$QUEUE.tmp ~/$DATA/$QUEUE" < "$src" || rc=$?
  case $rc in
    0) say "ship queue -> $HOST" ;;
    3) say "$HOST isn't set up yet: tools/ship_station.sh setup" ;;
    *) say "$HOST didn't take the ship queue (down?); the laptop carries on" ;;
  esac
  return $rc
}

pull() {
  if ((DRY)); then echo "would copy $HOST:~/$DATA/$BOXES -> $BACK (only if green has rows)"; return 0; fi
  mkdir -p "$(dirname "$BACK")"
  local tmp=$BACK.tmp
  if ! on_green "cat ~/$DATA/$BOXES 2>/dev/null || true" > "$tmp"; then
    rm -f "$tmp"; say "$HOST didn't answer; $(basename "$BACK") left as it was"; return 1
  fi
  if [[ ! -s $tmp ]]; then rm -f "$tmp"; say "no boxes recorded on $HOST yet"; return 0; fi
  mv "$tmp" "$BACK"   # byte for byte: green writes CRLF and it stays CRLF
  say "$(($(wc -l < "$BACK") - 1)) box rows <- $HOST, in reports/shipping/$(basename "$BACK")"
}

UNIT_FILE="[Unit]
Description=Pop's Games web app for the ship page, 127.0.0.1:8770 only
[Service]
WorkingDirectory=%h/$APP
ExecStart=%h/$VENV/bin/python -m lib.cli serve
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target"

DEPLOY="set -euo pipefail
if [ -e ~/$APP ] && [ ! -e ~/$APP/.deployed ]; then
  echo \"~/$APP exists and isn't ship_station.sh's; move it and run setup again\" >&2; exit 4; fi
mkdir -p ~/$DATA/reports/shipping
rm -rf ~/$APP.new && mkdir ~/$APP.new && tar -x -C ~/$APP.new
rm -rf ~/$APP.new/reports && ln -s ~/$DATA/reports ~/$APP.new/reports
echo \"\$REV\" > ~/$APP.new/.deployed
rm -rf ~/$APP.old; if [ -e ~/$APP ]; then mv ~/$APP ~/$APP.old; fi
mv ~/$APP.new ~/$APP && rm -rf ~/$APP.old"

VENV_STEP="set -euo pipefail
[ -x ~/$VENV/bin/python ] || python3 -m venv ~/$VENV || {
  echo 'python3 -m venv failed: green needs python3-venv (tools/green_setup.py installs it)' >&2; exit 5; }
~/$VENV/bin/pip install -q $DEPS"

SERVICE="set -euo pipefail
mkdir -p ~/.config/systemd/user
printf '%s\n' \"\$UNIT_FILE\" > ~/.config/systemd/user/$UNIT
# Linger: the user manager runs from boot and outlives this ssh session, so
# pops-serve starts at boot without a desktop login.
[ \"\$(loginctl show-user \"\$USER\" -p Linger --value 2>/dev/null)\" = yes ] || loginctl enable-linger \"\$USER\" || {
  echo 'loginctl enable-linger was refused; tools/green_setup.py turns it on as root' >&2; exit 7; }
systemctl --user daemon-reload
systemctl --user enable -q $UNIT
systemctl --user restart $UNIT
for i in \$(seq 20); do
  ~/$VENV/bin/python -c \"import urllib.request as u; u.urlopen('$URL', timeout=2)\" 2>/dev/null && { echo 'serve answers on $URL'; exit 0; }
  sleep 0.5
done
echo 'serve did not answer; journalctl --user -u $UNIT -n 30' >&2; exit 6"

rev() {  # REF after a fetch; a failed fetch falls back to the last one seen
  [[ $REF == origin/* ]] && { git -C "$MAIN" fetch -q origin "${REF#origin/}" 2>/dev/null || say "fetch failed; using the last $REF"; }
  git -C "$MAIN" rev-parse --short "$REF"
}

setup() {
  local rev; rev=$(rev)
  if ((DRY)); then
    printf 'would copy %s at %s (tracked files only) to %s:~/%s\n' "$REF" "$rev" "$HOST" "$APP"
    printf '\n--- on %s: code\n%s\n\n--- on %s: venv\n%s\n\n--- on %s: user unit\n%s\n%s\n' \
      "$HOST" "$DEPLOY" "$HOST" "$VENV_STEP" "$HOST" "$UNIT_FILE" "$SERVICE"
    return 0
  fi
  if [[ -e $MAIN/inventory/_live/ON_AIR ]]; then say "a show is on air; setup waits (push and pull still work)"; return 1; fi
  git -C "$MAIN" archive "$rev" | on_green "REV=$rev bash -c $(printf %q "$DEPLOY")"
  say "code at $rev -> $HOST:~/$APP"
  on_green "bash -c $(printf %q "$VENV_STEP")"
  on_green "UNIT_FILE=$(printf %q "$UNIT_FILE") bash -c $(printf %q "$SERVICE")"
  push || true
}

status() {
  local rev; rev=$(rev)
  if ((DRY)); then echo "would read $HOST: $UNIT, $URL, ~/$APP/.deployed, the queue's age, lpstat -p QL-1100"; return 0; fi
  on_green "echo \"serve:   \$(systemctl --user is-active $UNIT 2>&1)\"
    echo \"linger:  \$(loginctl show-user \"\$USER\" -p Linger --value 2>&1)\"
    ~/$VENV/bin/python -c \"import urllib.request as u; u.urlopen('$URL', timeout=3)\" 2>/dev/null \
      && echo 'page:    $URL answers' || echo 'page:    $URL does not answer'
    d=\$(cat ~/$APP/.deployed 2>/dev/null || echo none)
    [ \"\$d\" = $rev ] && echo \"code:    $rev, current $REF\" || echo \"code:    \$d, $REF is $rev: tools/ship_station.sh setup\"
    q=~/$DATA/$QUEUE; [ -f \$q ] && echo \"queue:   pushed \$(( (\$(date +%s) - \$(stat -c %Y \$q)) / 60 )) min ago\" || echo 'queue:   none yet'
    echo \"printer: \$(lpstat -p QL-1100 2>&1 | head -1)\"" || { say "$HOST didn't answer"; return 1; }
}

case ${1:-} in
  push|pull|setup|status) "$1" ;;
  *) sed -n '2,9p' "$0" >&2; exit 2 ;;
esac
