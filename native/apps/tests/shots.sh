#!/usr/bin/env bash
# Offscreen screenshots of the real app executables (effects such as blur do not
# render offscreen; the rest does). Usage:
#   shots.sh <build dir> <controller url> <user> <password> <out dir> [fake]
# With a user that exists on the controller. Uses a private XDG_RUNTIME_DIR so no
# other app's desktop session file is touched.
set -euo pipefail
B=$1; URL=$2; USER_=$3; PASS=$4; OUT=$5
mkdir -p "$OUT"
export XDG_RUNTIME_DIR="$(mktemp -d)"; chmod 700 "$XDG_RUNTIME_DIR"
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software} TAOS_URL=$URL TAOS_SCREENSHOT_DELAY_MS=${DELAY:-5000}
"$B/tests/taos-apps-seed-session" "$URL" "$USER_" "$PASS"
shot() { # name exe args...
  local name=$1; shift
  TAOS_SCREENSHOT="$OUT/$name.png" "$@" >/dev/null 2>"$OUT/$name.log" || echo "shot $name failed ($?)"
  ls -la "$OUT/$name.png" | awk '{print $5, $9}'
}
shot notifications "$B/notifications/taos-notifications"
shot decisions-pending "$B/decisions/taos-decisions"
shot decisions-archive "$B/decisions/taos-decisions" --tab=archive
shot decisions-withdrawn "$B/decisions/taos-decisions" --tab=withdrawn
if [ "${6:-}" = "fake" ]; then   # the extended fake controller: also the questions on their own, and the notifications empty state
  curl -s -X POST "$URL/__fake/config" -d '{"seed":true,"requests":false}' >/dev/null
  shot decisions-questions "$B/decisions/taos-decisions"
  curl -s -X POST "$URL/__fake/config" -d '{"seed":true}' >/dev/null
fi
