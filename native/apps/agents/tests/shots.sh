#!/usr/bin/env bash
# Offscreen renders of the real taos-agents executable against the fake controller.
#   shots.sh <build dir> <out dir> [theme ...]      (default themes: graphite)
# Each theme is rendered dark and light. Uses a private XDG_RUNTIME_DIR; the
# fake controller is started and stopped here.
set -euo pipefail
B=$1; OUT=$2; shift 2
THEMES=${*:-graphite}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
export XDG_RUNTIME_DIR="$(mktemp -d)"; chmod 700 "$XDG_RUNTIME_DIR"
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software} TAOS_SCREENSHOT_DELAY_MS=${DELAY:-3500}
python3 "$HERE/fake_agents_controller.py" > "$XDG_RUNTIME_DIR/fake.out" &
FAKE=$!
trap 'kill $FAKE 2>/dev/null || true' EXIT
for _ in $(seq 50); do grep -q LISTENING "$XDG_RUNTIME_DIR/fake.out" && break; sleep 0.1; done
PORT=$(awk '/LISTENING/{print $2}' "$XDG_RUNTIME_DIR/fake.out")
export TAOS_URL="http://127.0.0.1:$PORT"
cfg() { curl -s -X POST "$TAOS_URL/__fake/config" -d "$1" >/dev/null; }
"$B/tests/taos-apps-seed-session" "$TAOS_URL" alex hunter2-secret-pw
shot() { # name args...
  local name=$1; shift
  TAOS_SCREENSHOT="$OUT/$name.png" TAOS_SCREENSHOT_SIZE=${SIZE:-1040x700} "$B/agents/taos-agents" "$@" >/dev/null 2>"$OUT/$name.log" || echo "shot $name failed ($?)"
}
for theme in $THEMES; do for variant in dark light; do
  export TAOS_THEME=$theme TAOS_THEME_VARIANT=$variant
  p="$theme-$variant"
  cfg '{"agents":"seed","list_fail":0}'
  shot "list-$p"
  shot "detail-running-$p" --agent researcher
  shot "detail-remote-paused-$p" --agent scribe
  shot "detail-remote-offline-$p" --agent builder
  SIZE=420x700 shot "narrow-list-$p"
  cfg '{"agents":"empty"}'
  shot "empty-$p"
  cfg '{"agents":"seed","list_fail":403}'
  shot "forbidden-$p"
done; done
ls -la "$OUT" | awk '{print $5, $9}'
