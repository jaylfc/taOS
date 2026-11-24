#!/usr/bin/env bash
# Offscreen renders of the real taos-messages executable against the fake chat
# controller (no real controller, no real session file: a private XDG_RUNTIME_DIR).
#   shots.sh <build dir> <out dir>
# Writes <out>/<scenario>-<theme>-<variant>.png
set -euo pipefail
B=$1; OUT=$2
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
export XDG_RUNTIME_DIR="$(mktemp -d)"; chmod 700 "$XDG_RUNTIME_DIR"
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software} TAOS_SCREENSHOT_DELAY_MS=${DELAY:-4000}
python3 "$HERE/fake_chat_controller.py" >"$XDG_RUNTIME_DIR/port" &
FAKE=$!
trap 'kill $FAKE 2>/dev/null; rm -rf "$XDG_RUNTIME_DIR"' EXIT
for _ in $(seq 1 50); do grep -q LISTENING "$XDG_RUNTIME_DIR/port" 2>/dev/null && break; sleep 0.1; done
PORT=$(sed -n 's/LISTENING //p' "$XDG_RUNTIME_DIR/port")
URL=http://127.0.0.1:$PORT
export TAOS_URL=$URL
"$B/tests/taos-apps-seed-session" "$URL" alex hunter2-secret-pw
ctl() { curl -s -X POST "$URL/__fake/chat" -d "$1" >/dev/null; }
cfg() { curl -s -X POST "$URL/__fake/config" -d "$1" >/dev/null; }
shot() { # name args...
  local name=$1; shift
  for tv in graphite:dark graphite:light graphite-ice:dark graphite-warm:dark; do
    t=${tv%%:*}; v=${tv##*:}
    case "$name" in *) [ "$t" != graphite ] && [ "$name" != conversation ] && continue ;; esac
    TAOS_THEME=$t TAOS_THEME_VARIANT=$v TAOS_SCREENSHOT="$OUT/$name-$t-$v.png" TAOS_SCREENSHOT_SIZE=${SIZE:-1000x680} \
      "$B/messages/taos-messages" "$@" >/dev/null 2>"$OUT/$name-$t-$v.log" || echo "shot $name $t $v failed ($?)"
    ls -la "$OUT/$name-$t-$v.png" | awk '{print $5, $9}'
  done
}
ctl '{"seed":true}'
shot conversation --channel ch-general
shot agent-blocks --agent alpha
ctl '{"seed":true}'
SIZE=1000x680 shot list-only
ctl '{"empty":true}'
shot empty
ctl '{"seed":true}'
cfg '{"fail":{"path":"/api/chat/channels","status":500,"times":1000,"message":"database is locked"}}'
shot error
cfg '{"fail":null}'
ctl '{"seed":true}'
SIZE=520x680 shot narrow --channel ch-general
