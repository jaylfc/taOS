#!/usr/bin/env bash
# Offscreen renders of Settings > Apps with the real taos-settings executable: web apps made by the real
# `taos-webapp` CLI (a fake browser, a temporary home), container apps from the fake controller, and taOS's own apps
# from the repository's launcher entries. Nothing of anyone's own data is touched.
#   settings-apps-shots.sh <apps build dir> <settings build dir> <out dir> [theme ...]
set -euo pipefail
B=$1; SB=$2; OUT=$3; shift 3
THEMES=${*:-graphite}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
H=$(mktemp -d); export HOME=$H XDG_DATA_HOME=$H/data XDG_CONFIG_HOME=$H/config XDG_RUNTIME_DIR=$H/rt
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME/applications" "$XDG_CONFIG_HOME"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_DATA_DIRS=$H/none
cp "$HERE"/../../desktop/my.taos.*.desktop "$XDG_DATA_HOME/applications/"
# a person's own theme, greeter copy and themes are never read or written
export TAOS_APPEARANCE_MIRROR_DIR=$H/mirror TAOS_SYSTEM_APPEARANCE=$H/etc-appearance.json TAOS_SYSTEM_THEME_DIR=$H/system-themes
mkdir -p "$H/mirror"
unset TAOS_APPEARANCE TAOS_THEME_DIR
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software} TAOS_SCREENSHOT_DELAY_MS=${DELAY:-4000}
export TAOS_WEBAPP_NO_NETWORK=1 TAOS_WEBAPP_BROWSER="$HERE/../../../shell/webapp/tests/fakebrowser.sh" FAKE_BROWSER_STOP=$H/stop
CLI=$(find "$B" -type f -name taos-webapp -perm -u+x | head -n 1)
python3 "$HERE/fake_store_controller.py" > "$H/fake.out" &
FAKE=$!
trap 'kill $FAKE 2>/dev/null || true; rm -rf "$H"' EXIT
for _ in $(seq 50); do grep -q LISTENING "$H/fake.out" && break; sleep 0.1; done
PORT=$(awk '/LISTENING/{print $2}' "$H/fake.out")
export TAOS_URL="http://127.0.0.1:$PORT"
curl -s -X POST "$TAOS_URL/__fake/config" -d '{"store":"seed","admin":true}' >/dev/null
"$B/tests/taos-apps-seed-session" "$TAOS_URL" alex hunter2-secret-pw
"$CLI" add --url https://www.youtube.com --name YouTube --from-store youtube >/dev/null
"$CLI" add --url https://www.youtube.com --name "YouTube (kids)" --from-store youtube >/dev/null
"$CLI" add --url https://github.com --name GitHub --from-store github >/dev/null
ID=$("$CLI" list --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
"$CLI" launch "$ID" >/dev/null; sleep 0.6; touch "$H/stop"; sleep 0.6; rm -f "$H/stop"
for theme in $THEMES; do for variant in dark light; do
  export TAOS_THEME=$theme TAOS_THEME_VARIANT=$variant
  TAOS_SCREENSHOT="$OUT/settings-apps-$theme-$variant.png" TAOS_SCREENSHOT_SIZE=900x760 \
    "$SB/taos-settings" --section apps >/dev/null 2>"$OUT/settings-apps-$theme-$variant.log" || echo "shot failed ($?)"
done; done
ls -la "$OUT" | awk '/settings-apps/{print $5, $9}'
