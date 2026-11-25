#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offscreen pictures of the radio screens (radios, switch network, settings, people and messages with two networks) of the real
# taos-survival executable, against the stand-in engines (fake-survival-engines): no radio, no real firmware manager, no display.
# Everything is private: HOME, XDG_*, TMPDIR and runtime dir are fresh directories under <work dir>, and it refuses to run with a
# live runtime dir. It cannot transmit and cannot flash.
#   shots_radio.sh <build dir> <out dir> [theme variant ...]      (default: graphite dark; add pairs like "graphite light")
# The build dir needs: survival/taos-survival, survival/tests/fake-survival-engines, survival/tests/make-survival-fixture-packs.
# Set LD_LIBRARY_PATH yourself when libzim is not in a system path.  JOBS=3 renders three pictures at a time.
set -euo pipefail
B=$(readlink -f "$1"); OUT=$2; shift 2
mkdir -p "$OUT"; OUT=$(readlink -f "$OUT")
THEMES=("$@"); [ ${#THEMES[@]} -gt 0 ] || THEMES=(graphite dark)
APP=$B/survival/taos-survival; ENG=$B/survival/tests/fake-survival-engines; PACKS=$B/survival/tests/make-survival-fixture-packs
for f in "$APP" "$ENG" "$PACKS"; do [ -x "$f" ] || { echo "missing $f" >&2; exit 1; }; done
WORK=$(mktemp -d "$B/shots-radio.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

one() { # scenario flash-delay-ms screenshot-delay-ms out.png size theme variant app-args...
  local sc=$1 fd=$2 sd=$3 png=$4 size=$5 theme=$6 var=$7; shift 7
  local h; h=$(mktemp -d "$WORK/run.XXXXXX")
  ( export HOME=$h XDG_DATA_HOME=$h/data XDG_CONFIG_HOME=$h/config XDG_CACHE_HOME=$h/cache XDG_RUNTIME_DIR=$h/rt TMPDIR=$h/tmp
    mkdir -p "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$TMPDIR" "$h/fw" "$h/mirror"; chmod 700 "$XDG_RUNTIME_DIR"
    unset WAYLAND_DISPLAY DISPLAY TAOS_APPEARANCE TAOS_THEME_DIR
    case "$XDG_RUNTIME_DIR" in /run/user/*) echo "refusing: live runtime dir" >&2; exit 9;; esac
    export TAOS_FIRMWARE_DIR=$h/fw TAOS_FIRMWARE_PORT=$((20000 + RANDOM % 20000)) TAOS_SURVIVAL_NO_SYSTEMCTL=1
    "$ENG" "$sc" "$fd" > "$h/engines.log" 2>&1 & ep=$!
    trap 'kill $ep 2>/dev/null; wait $ep 2>/dev/null || true' EXIT
    for _ in $(seq 1 50); do grep -q ready "$h/engines.log" 2>/dev/null && break; sleep 0.1; done
    export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software TAOS_SCREENSHOT_DELAY_MS=$sd TAOS_SURVIVAL_DEMO=1 TAOS_REDUCE_MOTION=1
    export TAOS_APPEARANCE_MIRROR_DIR=$h/mirror TAOS_SYSTEM_APPEARANCE=$h/etc-appearance.json TAOS_SYSTEM_THEME_DIR=$h/system-themes
    export TAOS_URL=http://127.0.0.1:9 TAOS_SURVIVAL_DIR=$h/survival TAOS_THEME=$theme TAOS_THEME_VARIANT=$var
    mkdir -p "$TAOS_SURVIVAL_DIR/packs"; "$PACKS" "$TAOS_SURVIVAL_DIR/packs" >/dev/null 2>&1
    TAOS_SCREENSHOT=$png TAOS_SCREENSHOT_SIZE=$size "$APP" "$@" > "$h/app.log" 2>&1 || { tail -5 "$h/app.log" >&2; exit 1; }
  )
}
jobs_list=()
add() { local l="" x; for x in "$@"; do l+=$(printf '%q ' "$x"); done; jobs_list+=("$l"); }
i=0
while [ $i -lt ${#THEMES[@]} ]; do
  T=${THEMES[$i]}; V=${THEMES[$((i+1))]}; i=$((i+2))
  for size in 1600x1000 412x915; do
    k=$([ "${size%%x*}" -lt 760 ] && echo phone || echo desktop); p="$T-$V-$k"
    add one 0 4500 "$OUT/radios-meshtastic-$p.png" $size $T $V --section taostak --tab radios
    add bare 0 4500 "$OUT/radios-board-not-connected-$p.png" $size $T $V --section taostak --tab radios
    add two 0 4500 "$OUT/radios-two-networks-$p.png" $size $T $V --section taostak --tab radios
    add two 0 4500 "$OUT/people-two-networks-$p.png" $size $T $V --section taostak --tab people
    add two 0 4500 "$OUT/messages-two-networks-$p.png" $size $T $V --section taostak --tab messages
    add one 0 5000 "$OUT/settings-meshtastic-$p.png" $size $T $V --section taostak --tak-view 'settings:!a1b2c3d4'
    add two 0 5000 "$OUT/settings-meshcore-$p.png" $size $T $V --section taostak --tak-view settings:a1b2c3d4e5f6
    add one 0 5500 "$OUT/switch-choose-$p.png" $size $T $V --section taostak --tak-view switch:/dev/ttyUSB0 --demo swstep=choose
    add one 0 7500 "$OUT/switch-review-$p.png" $size $T $V --section taostak --tak-view switch:/dev/ttyUSB0 --demo 'swstep=review;typed=Heltec V3;ack=1'
    add one 90000 9000 "$OUT/switch-running-$p.png" $size $T $V --section taostak --tak-view switch:/dev/ttyUSB0 --demo 'swstep=review;swgo=run'
    add one 0 16000 "$OUT/switch-done-$p.png" $size $T $V --section taostak --tak-view switch:/dev/ttyUSB0 --demo 'swstep=review;swgo=run'
    add one 0 9000 "$OUT/switch-failed-$p.png" $size $T $V --section taostak --tak-view switch:/dev/ttyUSB0 --demo 'swstep=review;swgo=wrong'
  done
done
for line in "${jobs_list[@]}"; do
  while [ "$(jobs -rp | wc -l)" -ge "${JOBS:-2}" ]; do sleep 1; done
  eval "one $line" &
done
wait
ls "$OUT" | grep -c png
