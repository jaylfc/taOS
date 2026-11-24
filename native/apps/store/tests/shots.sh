#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offscreen renders of the real taos-store executable against the fake controller, with real web app instances made
# by the real `taos-webapp` CLI (a fake browser, a temporary home: nothing of anyone's own data is touched).
#   shots.sh <apps build dir> <out dir> [theme ...]      (default themes: graphite)
# Each theme is rendered dark and light.
set -euo pipefail
B=$1; OUT=$2; shift 2
THEMES=${*:-graphite}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
H=$(mktemp -d); export HOME=$H XDG_DATA_HOME=$H/data XDG_CONFIG_HOME=$H/config XDG_RUNTIME_DIR=$H/rt
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"; chmod 700 "$XDG_RUNTIME_DIR"
# a person's own theme, greeter copy and themes are never read or written
export TAOS_APPEARANCE_MIRROR_DIR=$H/mirror TAOS_SYSTEM_APPEARANCE=$H/etc-appearance.json TAOS_SYSTEM_THEME_DIR=$H/system-themes
mkdir -p "$H/mirror"
unset TAOS_APPEARANCE TAOS_THEME_DIR
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software} TAOS_SCREENSHOT_DELAY_MS=${DELAY:-3500}
export TAOS_WEBAPP_NO_NETWORK=1 TAOS_WEBAPP_BROWSER="$HERE/../../../shell/webapp/tests/fakebrowser.sh" FAKE_BROWSER_STOP=$H/stop
CLI=$(find "$B" -type f -name taos-webapp -perm -u+x | head -n 1)
python3 "$HERE/fake_store_controller.py" > "$H/fake.out" &
FAKE=$!
trap 'kill $FAKE 2>/dev/null || true; rm -rf "$H"' EXIT
for _ in $(seq 50); do grep -q LISTENING "$H/fake.out" && break; sleep 0.1; done
PORT=$(awk '/LISTENING/{print $2}' "$H/fake.out")
export TAOS_URL="http://127.0.0.1:$PORT"
cfg() { curl -s -X POST "$TAOS_URL/__fake/config" -d "$1" >/dev/null; }
cfg '{"store":"seed","admin":true}'
"$B/tests/taos-apps-seed-session" "$TAOS_URL" alex hunter2-secret-pw
# the person's web apps, made the way an agent would
"$CLI" add --url https://www.youtube.com --name YouTube --from-store youtube >/dev/null
"$CLI" add --url https://www.youtube.com --name "YouTube (kids)" --from-store youtube >/dev/null
"$CLI" add --url https://github.com --name GitHub --from-store github >/dev/null
"$CLI" add --url https://wiki.example.org --name "Team wiki" >/dev/null
ID=$("$CLI" list --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
"$CLI" launch "$ID" >/dev/null; sleep 0.6; touch "$H/stop"; sleep 0.6; rm -f "$H/stop"
shot() { # name args...
  local name=$1; shift
  TAOS_SCREENSHOT="$OUT/$name.png" TAOS_SCREENSHOT_SIZE=${SIZE:-1120x740} "$B/store/taos-store" "$@" >/dev/null 2>"$OUT/$name.log" || echo "shot $name failed ($?)"
}
for theme in $THEMES; do for variant in dark light; do
  export TAOS_THEME=$theme TAOS_THEME_VARIANT=$variant
  p="$theme-$variant"
  shot "discover-$p"
  shot "webapps-$p" --section webapps
  shot "add-website-$p" --section webapps --add-website
  shot "detail-youtube-$p" --app youtube
  shot "detail-docker-$p" --app searxng
  shot "detail-model-$p" --app qwen3-8b
  shot "apps-$p" --section apps
  shot "models-$p" --section models
  shot "themes-$p" --section themes
  shot "installed-$p" --section installed
  shot "updates-$p" --section updates
  SIZE=420x740 shot "narrow-webapps-$p" --section webapps
done; done
ls -la "$OUT" | awk '{print $5, $9}'
