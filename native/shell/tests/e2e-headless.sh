#!/bin/sh
# End to end check of taos-bar under a HEADLESS sway (never the physical seat).
#
# Needs: sway, swaymsg, grim, python3, and the Qt runtime for taos-bar in the
# environment (LD_LIBRARY_PATH, QT_PLUGIN_PATH ... see dev-env.sh.example).
#
#   TAOS_BAR_BIN=/path/to/taos-bar OUT_DIR=/path/for/shots tests/e2e-headless.sh
#
# Optional: E2E_RENDER_NODE=/dev/dri/renderD128 (render through that node),
#           E2E_IDLE_SECONDS=30 (measure RSS, CPU and wakeups of the idle bar).
# Exits non-zero when any assertion fails.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
BAR=${TAOS_BAR_BIN:?set TAOS_BAR_BIN to the taos-bar binary}
OUT=${OUT_DIR:-$PWD/e2e-out}
IDLE=${E2E_IDLE_SECONDS:-0}
mkdir -p "$OUT"
helper() { python3 "$HERE/e2e_helper.py" "$@"; }

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
failm() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }
assert_eq() { # description, expected, actual
    if [ "$2" = "$3" ]; then pass "$1 ($3)"; else failm "$1: expected '$2', got '$3'"; fi
}

RT=$(mktemp -d "${TMPDIR:-/tmp}/taos-e2e.XXXXXX") || exit 1
chmod 700 "$RT"
SWAYPID=""
BARPID=""
cleanup() {
    [ -n "$BARPID" ] && kill "$BARPID" 2>/dev/null
    [ -n "$SWAYPID" ] && kill "$SWAYPID" 2>/dev/null
    sleep 0.3
    rm -rf "$RT"
}
trap cleanup EXIT INT TERM

wait_until() { # seconds, command...
    n=$(( $1 * 10 )); shift
    while [ "$n" -gt 0 ]; do
        "$@" && return 0
        sleep 0.1
        n=$((n - 1))
    done
    return 1
}
logged() { grep -q -- "$1" "$LOG" 2>/dev/null; }

start_sway() { # scale
    cat >"$RT/sway.conf" <<EOC
output HEADLESS-1 resolution 1920x1080 position 0 0 scale $1
default_border none
EOC
    export XDG_RUNTIME_DIR="$RT"
    unset WAYLAND_DISPLAY SWAYSOCK
    if [ -n "${E2E_RENDER_NODE:-}" ]; then
        export WLR_RENDERER=gles2 WLR_RENDER_DRM_DEVICE="$E2E_RENDER_NODE"
    fi
    WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 sway -c "$RT/sway.conf" >"$RT/sway.log" 2>&1 &
    SWAYPID=$!
    wait_until 15 sh -c 'ls "$0"/sway-ipc.*.sock >/dev/null 2>&1 && ls "$0"/wayland-[0-9]* >/dev/null 2>&1' "$RT" || { failm "sway did not start"; cat "$RT/sway.log"; return 1; }
    SWAYSOCK=$(ls "$RT"/sway-ipc.*.sock | head -n 1); export SWAYSOCK
    WAYLAND_DISPLAY=$(basename "$(ls "$RT"/wayland-[0-9]* | grep -v '\.lock$' | head -n 1)"); export WAYLAND_DISPLAY
    return 0
}
stop_sway() {
    [ -n "$SWAYPID" ] && kill "$SWAYPID" 2>/dev/null
    [ -n "$SWAYPID" ] && wait "$SWAYPID" 2>/dev/null
    SWAYPID=""
    rm -f "$RT"/sway-ipc.*.sock "$RT"/wayland-[0-9]*
}
ws_rect() { swaymsg -t get_tree | helper ws-rect "$1"; }

scenario() { # name variant scale [extra bar args]
    name=$1; variant=$2; scale=$3; shift 3
    echo "== scenario $name (variant $variant, scale $scale)"
    LOG="$RT/bar-$name.log"
    start_sway "$scale" || return
    "$BAR" --variant "$variant" "$@" 2>"$LOG" &
    BARPID=$!
    wait_until 15 logged "bar added on output HEADLESS-1" || { failm "$name: bar did not appear"; cat "$LOG"; stop_sway; return; }
    sleep 1

    lh=$(awk "BEGIN{printf \"%d\", 1080/$scale}")
    want_h=$((lh - 40))
    wait_until 10 sh -c 'swaymsg -t get_tree | python3 "$0" ws-rect HEADLESS-1 | grep -q " 40 "' "$HERE/e2e_helper.py"
    assert_eq "$name: workspace rect y h" "40 $want_h" "$(ws_rect HEADLESS-1 | awk '{print $2, $4}')"
    assert_eq "$name: output mode and scale" "1920 1080 $scale" "$(swaymsg -t get_outputs | helper output HEADLESS-1 | sed 's/\.0$//')"
    if grep -qiE "unable to assign|qml:|ReferenceError|TypeError|warning" "$LOG"; then failm "$name: QML or Qt warnings in the bar log"; sed 's/^/    /' "$LOG"; else pass "$name: no QML warnings in the log"; fi
    kill -0 "$BARPID" 2>/dev/null && pass "$name: bar process alive" || failm "$name: bar exited early"
    logged "taos-bar 0\." && pass "$name: start line logged" || failm "$name: no start line"
    logged "theme=graphite/$variant" && pass "$name: theme/variant in start line" || failm "$name: wrong theme in start line"

    shot="$OUT/bar-$name.png"
    grim -o HEADLESS-1 "$shot" && pass "$name: grim screenshot" || failm "$name: grim failed"
    assert_eq "$name: screenshot size" "1920 1080" "$(helper png-size "$shot")"
    grim -o HEADLESS-1 -t ppm "$RT/shot.ppm"
    py=$(awk "BEGIN{printf \"%d\", 6*$scale}")
    set -- $(helper pixel "$RT/shot.ppm" 960 "$py")
    if [ "$variant" = dark ]; then
        [ "$1" -lt 40 ] && pass "$name: bar background is dark ($1 $2 $3)" || failm "$name: bar background not dark ($1 $2 $3)"
    else
        [ "$1" -gt 40 ] && pass "$name: bar background is light ($1 $2 $3)" || failm "$name: bar background not light ($1 $2 $3)"
    fi
    set -- $(helper pixel "$RT/shot.ppm" 960 600)
    [ "$1$2$3" = 000 ] && pass "$name: area below the bar untouched" || failm "$name: below-bar pixel is $1 $2 $3"
    CURSCENARIO_PID=$BARPID
}

finish_bar() { # name: SIGTERM must give a clean exit 0
    kill -TERM "$BARPID" 2>/dev/null
    wait "$BARPID"; rc=$?
    BARPID=""
    assert_eq "$1: SIGTERM exits cleanly" 0 "$rc"
}

# 1. dark, scale 1
scenario dark dark 1 --output HEADLESS-1
if [ "$IDLE" -gt 0 ]; then
    sum() { # field over all threads
        cat /proc/"$BARPID"/task/*/status 2>/dev/null | awk -v k="$1" '$1==k":" {s+=$2} END{print s+0}'
    }
    rss=$(awk '/VmRSS/ {print $2}' /proc/"$BARPID"/status)
    t0=$(awk '{print $14+$15}' /proc/"$BARPID"/stat); v0=$(sum voluntary_ctxt_switches); n0=$(sum nonvoluntary_ctxt_switches)
    sleep "$IDLE"
    t1=$(awk '{print $14+$15}' /proc/"$BARPID"/stat); v1=$(sum voluntary_ctxt_switches); n1=$(sum nonvoluntary_ctxt_switches)
    hz=$(getconf CLK_TCK)
    echo "IDLE ${IDLE}s: rss_kb=$rss cpu_ticks=$((t1 - t0)) (clk_tck $hz) voluntary_ctxt=$((v1 - v0)) nonvoluntary_ctxt=$((n1 - n0))"
    kill -0 "$BARPID" 2>/dev/null && pass "idle: still alive after ${IDLE}s" || failm "idle: bar died"
fi
finish_bar dark
stop_sway

# 2. fractional scale 1.5
scenario scale1_5 dark 1.5 --output HEADLESS-1
finish_bar scale1_5
stop_sway

# 3. light variant
scenario light light 1 --output HEADLESS-1
finish_bar light
stop_sway

# 4. hotplug: no --output, a second output appears then goes away
echo "== scenario hotplug"
LOG="$RT/bar-hotplug.log"
start_sway 1 && {
    "$BAR" 2>"$LOG" &
    BARPID=$!
    wait_until 15 logged "bar added on output HEADLESS-1" || failm "hotplug: first bar missing"
    swaymsg create_output >/dev/null 2>&1
    wait_until 10 logged "bar added on output HEADLESS-2" && pass "hotplug: bar created for new output" || failm "hotplug: no bar for HEADLESS-2"
    wait_until 10 sh -c 'swaymsg -t get_tree | python3 "$0" ws-rect HEADLESS-2 | grep -q "^0 40 "' "$HERE/e2e_helper.py"
    swaymsg -t get_tree | helper ws-rect HEADLESS-2 | sed 's/^/hotplug: HEADLESS-2 workspace rect: /'
    assert_eq "hotplug: second output usable y" 40 "$(ws_rect HEADLESS-2 | awk '{print $2}')"
    swaymsg output HEADLESS-2 unplug >/dev/null 2>&1
    wait_until 10 logged "bar removed from output HEADLESS-2" && pass "hotplug: bar destroyed with its output" || failm "hotplug: bar not removed"
    sleep 1
    kill -0 "$BARPID" 2>/dev/null && pass "hotplug: bar survives unplug" || failm "hotplug: bar died on unplug"
    assert_eq "hotplug: first output usable y still" 40 "$(ws_rect HEADLESS-1 | awk '{print $2}')"
    if grep -qiE "unable to assign|qml:|ReferenceError|TypeError|warning" "$LOG"; then failm "hotplug: QML or Qt warnings in the bar log"; sed 's/^/    /' "$LOG"; else pass "hotplug: no QML warnings in the log"; fi
    finish_bar hotplug
    stop_sway
}

echo
if [ "$fails" -eq 0 ]; then echo "E2E OK"; exit 0; fi
echo "E2E FAILED: $fails assertion(s)"
exit 1
