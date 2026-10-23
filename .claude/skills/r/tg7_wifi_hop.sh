#!/usr/bin/env bash
# Hop the laptop's one Wi-Fi adapter onto the TG-7's own network, copy new
# photos off the card, then put the laptop back on the network it was on.
#
#   tg7_wifi_hop.sh            hop, sync, reconnect (tg7_pull.sh starts it detached)
#   tg7_wifi_hop.sh --check    exit 0 and print the profile name if a saved
#                              TG-7 Wi-Fi profile exists and its SSID is in range
#
# Joining the camera drops internet, Claude included, so the hop needs nothing
# from Claude once started. It waits $TG7_HOP_DELAY s first so the calling
# Claude turn can finish its API calls. The home connection is brought back on
# every exit (done, failed, killed, or the $TG7_HOP_LIMIT s time limit), with
# retries. Steps are logged to $XDG_RUNTIME_DIR/tg7_hop.log; the status file
# $XDG_RUNTIME_DIR/tg7_hop.status is written last, after the reconnect:
#   ok | failed: <reason>
#   new: <n> photo(s) -> <dir>
#   network: <what happened to the home connection>
#   log: <log path>
#
# One connection at a time: it only brings saved profiles up on the Wi-Fi
# device, which replaces the active one. It never adds, edits or deletes a
# profile. It refuses on air (inventory/_live/ON_AIR): the stream owns the
# network.
set -uo pipefail

RUN=${XDG_RUNTIME_DIR:-/tmp}
LOG=$RUN/tg7_hop.log
STATUS=$RUN/tg7_hop.status
DELAY=${TG7_HOP_DELAY:-30}
LIMIT=${TG7_HOP_LIMIT:-600}
CAM=192.168.0.10
WIFI=802-11-wireless

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

# Saved Wi-Fi profiles whose name or SSID has TG-7 in it: "uuid<TAB>ssid<TAB>name".
tg7_profiles() {
  local uuid type name ssid
  nmcli -t -f UUID,TYPE,NAME con show 2>/dev/null | while IFS=: read -r uuid type name; do
    [ "$type" = "$WIFI" ] || continue
    ssid=$(nmcli -g 802-11-wireless.ssid con show uuid "$uuid" 2>/dev/null)
    name=${name//\\:/:}
    case "${name^^} ${ssid^^}" in *TG-7*) printf '%s\t%s\t%s\n' "$uuid" "$ssid" "$name" ;; esac
  done
}

# SSIDs in range, from a fresh scan (or the cached list if a scan is refused).
in_range() {
  { nmcli -t -f SSID dev wifi list --rescan yes 2>/dev/null || nmcli -t -f SSID dev wifi list 2>/dev/null; } |
    sed 's/\\:/:/g'
}

# Sets TG_UUID/TG_SSID/TG_NAME to the first TG-7 profile in range, or WHY.
find_tg7() {
  local profiles seen uuid ssid name
  profiles=$(tg7_profiles)
  [ -n "$profiles" ] || { WHY="no saved TG-7 Wi-Fi profile in NetworkManager"; return 1; }
  seen=$(in_range)
  while IFS=$'\t' read -r uuid ssid name; do
    if [ -n "$ssid" ] && grep -qxF -- "$ssid" <<<"$seen"; then
      TG_UUID=$uuid TG_SSID=$ssid TG_NAME=$name
      return 0
    fi
  done <<<"$profiles"
  WHY="the TG-7's Wi-Fi ($(cut -f2 <<<"$profiles" | paste -sd,)) is not in range; is Wi-Fi on at the camera?"
  return 1
}

# The active Wi-Fi connection other than the camera's: "uuid<TAB>device<TAB>name".
home_conn() {
  local uuid type dev name
  nmcli -t -f UUID,TYPE,DEVICE,NAME con show --active 2>/dev/null | while IFS=: read -r uuid type dev name; do
    if [ "$type" = "$WIFI" ] && [ "$uuid" != "$TG_UUID" ]; then
      printf '%s\t%s\t%s\n' "$uuid" "$dev" "${name//\\:/:}"
      break
    fi
  done
}

wifi_dev() { nmcli -t -f DEVICE,TYPE dev 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}'; }

cam_up() { curl -s -m 3 -o /dev/null -A "OI.Share v2" "http://$CAM/get_caminfo.cgi"; }

count() { find "$DEST" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.orf' \) 2>/dev/null | wc -l; }

# Put the laptop back on the connection it was on before the hop.
restore() {
  local i
  [ "$SWITCHED" = 1 ] || return 0
  SWITCHED=0
  if [ -z "$HOME_UUID" ]; then
    log "no Wi-Fi connection was up before the hop; dropping the TG-7's"
    nmcli con down uuid "$TG_UUID" >>"$LOG" 2>&1
    NET="TG-7 Wi-Fi dropped (no Wi-Fi connection was up before the hop)"
    return 0
  fi
  for i in 1 2 3 4; do
    log "reconnect $HOME_NAME (try $i of 4)"
    if nmcli --wait 20 con up uuid "$HOME_UUID" ifname "$DEV" >>"$LOG" 2>&1; then
      log "back on $HOME_NAME"
      NET="back on $HOME_NAME"
      return 0
    fi
    [ "$i" = 4 ] || sleep 3
  done
  log "could not bring $HOME_NAME back; dropping the TG-7's so NetworkManager can autoconnect"
  nmcli con down uuid "$TG_UUID" >>"$LOG" 2>&1
  NET="NOT back on $HOME_NAME after 4 tries; TG-7 Wi-Fi dropped so NetworkManager can autoconnect"
}

# EXIT trap: reconnect first, then write the status file in one move.
finish() {
  local new
  trap '' TERM INT HUP
  restore
  new=$(( $(count) - BEFORE ))
  [ "$new" -ge 0 ] || new=0
  RESULT=${RESULT:-"failed: stopped unexpectedly (see log)"}
  {
    echo "$RESULT"
    echo "new: $new photo(s) -> $DEST"
    echo "network: $NET"
    echo "log: $LOG"
  } >"$STATUS.tmp" && mv -f "$STATUS.tmp" "$STATUS"
  log "status: $RESULT; $new new; $NET"
}

main() {
  if [ "${1:-}" = --check ]; then
    find_tg7 || { echo "$WHY" >&2; exit 1; }
    echo "$TG_NAME"
    exit 0
  fi

  # The overall time limit: timeout sends TERM to the hop's process group,
  # the trap below turns that into a failed status, and finish reconnects.
  if [ -z "${TG7_HOP_TIMED:-}" ]; then
    TG7_HOP_TIMED=1 exec timeout -k 150 "$LIMIT" bash "$0" "$@"
  fi

  MAIN=${TG7_MAIN:-$(git -C "$(dirname "$0")" worktree list --porcelain | awk 'NR==1{print $2}')}
  DEST=${TG7_PHOTOS:-$HOME/Pictures/TG-7}
  mkdir -p "$RUN" "$DEST"

  # One hop at a time; a second one leaves the first to write the status.
  exec 9>"$RUN/tg7_hop.lock"
  flock -n 9 || exit 0

  : >"$LOG"
  RESULT= SWITCHED=0 NET="unchanged (never left it)" BEFORE=$(count)
  TG_UUID= TG_SSID= TG_NAME= HOME_UUID= HOME_NAME= DEV= WHY=
  trap finish EXIT
  trap 'RESULT=${RESULT:-"failed: stopped by a signal (the ${LIMIT}s time limit, or killed)"}; exit 143' TERM INT HUP

  log "hop started: photos -> $DEST; waiting ${DELAY}s so Claude can finish its turn"
  sleep "$DELAY"

  if [ -e "$MAIN/inventory/_live/ON_AIR" ]; then
    RESULT="failed: on air (inventory/_live/ON_AIR is set), the stream keeps the network"
    exit 1
  fi
  find_tg7 || { RESULT="failed: $WHY"; exit 1; }
  IFS=$'\t' read -r HOME_UUID DEV HOME_NAME < <(home_conn)
  DEV=${DEV:-$(wifi_dev)}
  [ -n "$DEV" ] || { RESULT="failed: no Wi-Fi device found"; exit 1; }
  log "home connection: ${HOME_NAME:-none}; camera profile: $TG_NAME (SSID $TG_SSID) on $DEV"

  SWITCHED=1
  log "join $TG_NAME"
  if ! nmcli --wait 45 con up uuid "$TG_UUID" ifname "$DEV" >>"$LOG" 2>&1; then
    RESULT="failed: could not join the TG-7's Wi-Fi ($TG_SSID)"
    exit 1
  fi

  log "wait for the camera at $CAM"
  local i
  for i in $(seq 20); do cam_up && break; sleep 2; done
  if ! cam_up; then
    RESULT="failed: joined $TG_SSID but the camera at $CAM did not answer"
    exit 1
  fi

  log "sync -> $DEST"
  python3 "$MAIN/tools/live/tg7cam" sync --to "$DEST" >>"$LOG" 2>&1
  local rc=$?
  if [ "$rc" != 0 ]; then
    RESULT="failed: tg7cam sync exited $rc (see log)"
    exit 1
  fi
  RESULT=ok
  exit 0
}

# Parsed whole before it runs, so an edit to this file mid-hop can't change it.
main "$@"
