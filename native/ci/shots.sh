#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offscreen screenshots of every native app's main window against the fake
# controllers (no real controller, private XDG_RUNTIME_DIR). Uses the apps' own
# TAOS_SCREENSHOT grab and their shots scripts.
#   native/ci/shots.sh <apps build> <activity build> <settings build> <out dir>
# Writes <out>/<app>/*.png and <out>/SUMMARY.txt. Exit 1 if an app produced no image.
set -uo pipefail
APPS=$(cd "${1:?apps build dir}" && pwd); ACT=$(cd "${2:?activity build dir}" && pwd)
SET=$(cd "${3:?settings build dir}" && pwd); OUT=$4
N=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software
: >"$OUT/SUMMARY.txt"
fail=0
report() { # app dir
  local n; n=$(find "$2" -name '*.png' 2>/dev/null | wc -l)
  echo "$1: $n image(s)" | tee -a "$OUT/SUMMARY.txt"
  [ "$n" -gt 0 ] || fail=1
}
run() { # app dir cmd...
  local app=$1 dir=$2; shift 2; mkdir -p "$dir"
  "$@" >"$dir/run.log" 2>&1 || echo "$app: script exited $? (see run.log)" | tee -a "$OUT/SUMMARY.txt"
  report "$app" "$dir"
}
# notifications + decisions: the apps fake controller
R=$(mktemp -d); chmod 700 "$R"
python3 "$N/apps/tests/fake_apps_controller.py" >"$R/out" & FP=$!
for _ in $(seq 50); do grep -q LISTENING "$R/out" 2>/dev/null && break; sleep 0.1; done
URL="http://127.0.0.1:$(awk '/LISTENING/{print $2}' "$R/out")"
run notifications+decisions "$OUT/notifications-decisions" bash "$N/apps/tests/shots.sh" "$APPS" "$URL" alex hunter2-secret-pw "$OUT/notifications-decisions" fake
kill $FP 2>/dev/null
run agents "$OUT/agents" bash "$N/apps/agents/tests/shots.sh" "$APPS" "$OUT/agents"
run messages "$OUT/messages" bash "$N/apps/messages/tests/shots.sh" "$APPS" "$OUT/messages"
run store "$OUT/store" bash "$N/apps/store/tests/shots.sh" "$APPS" "$OUT/store"
# settings: renders every page against fake engines itself
mkdir -p "$OUT/settings"
export XDG_RUNTIME_DIR="$R/rt"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
SB=$(find "$SET" -type f -name settings-shots -o -type f -name settings_shots | head -n 1)
if [ -n "$SB" ]; then run settings "$OUT/settings" "$SB" "$OUT/settings"; else echo "settings: no shots binary found, skipped" | tee -a "$OUT/SUMMARY.txt"; fail=1; fi
# activity: scene runner against its own fake
python3 "$N/apps/activity/tests/fake_activity.py" "$N/testing/fake_controller.py" >"$R/act" 2>&1 & AP=$!
for _ in $(seq 50); do grep -q LISTENING "$R/act" 2>/dev/null && break; sleep 0.1; done
AURL="http://127.0.0.1:$(awk '/LISTENING/{print $2}' "$R/act")"
AS=$(find "$ACT" -type f -name activity-shots | head -n 1)
if [ -n "$AS" ]; then run activity "$OUT/activity" "$AS" "$AURL" alex hunter2-secret-pw "$N/apps/activity/qml" "$OUT/activity" desktop light; else echo "activity: no shots binary found, skipped" | tee -a "$OUT/SUMMARY.txt"; fail=1; fi
kill $AP 2>/dev/null
rm -rf "$R"
exit $fail
