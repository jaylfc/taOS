#!/bin/sh
# End to end check of taos-greeter in demo mode under a HEADLESS sway (never the
# physical seat, never the real greetd).
#
# Needs: sway, grim, wtype, python3 (only for the idle measurement), and the Qt
# runtime in the environment (LD_LIBRARY_PATH, QT_PLUGIN_PATH, QML_IMPORT_PATH).
#
#   TAOS_GREETER_BIN=/path/to/taos-greeter OUT_DIR=/path/for/shots tests/e2e-greeter.sh
#
# Optional: E2E_RENDER_NODE=/dev/dri/renderD128, E2E_SIZES="3840x1200 ...",
#           E2E_IDLE_SECONDS=20 (measure RSS and idle CPU at the first size).
# Weather shown comes from an invented fixture (place "Example Town").
# Exits non-zero when any assertion fails.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${TAOS_GREETER_BIN:?set TAOS_GREETER_BIN to the taos-greeter binary}
OUT=${OUT_DIR:-$PWD/e2e-greeter-out}
SIZES=${E2E_SIZES:-"3840x1200 3840x2160 1920x1080 1080x2400"}
IDLE=${E2E_IDLE_SECONDS:-0}
mkdir -p "$OUT"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
failm() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

RT=$(mktemp -d "${TMPDIR:-/tmp}/taos-greeter-e2e.XXXXXX") || exit 1
chmod 700 "$RT"
SWAYPID=""
GPID=""
FPID=""
KBPID=""
cleanup() {
    [ -n "$FPID" ] && kill "$FPID" 2>/dev/null
    [ -n "$KBPID" ] && kill "$KBPID" 2>/dev/null
    [ -n "$GPID" ] && kill "$GPID" 2>/dev/null
    [ -n "$SWAYPID" ] && kill "$SWAYPID" 2>/dev/null
    sleep 0.3
    rm -rf "$RT"
}
trap cleanup EXIT INT TERM

wait_until() { # seconds, command...
    n=$(( $1 * 10 )); shift
    while [ "$n" -gt 0 ]; do "$@" && return 0; sleep 0.1; n=$((n - 1)); done
    return 1
}

start_sway() { # WxH
    cat >"$RT/sway.conf" <<EOC
output HEADLESS-1 resolution $1 position 0 0
default_border none
EOC
    export XDG_RUNTIME_DIR="$RT"
    unset WAYLAND_DISPLAY SWAYSOCK
    if [ -n "${E2E_RENDER_NODE:-}" ]; then export WLR_RENDERER=gles2 WLR_RENDER_DRM_DEVICE="$E2E_RENDER_NODE"; fi
    WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 sway -c "$RT/sway.conf" >"$RT/sway.log" 2>&1 &
    SWAYPID=$!
    wait_until 15 sh -c 'ls "$0"/sway-ipc.*.sock >/dev/null 2>&1 && ls "$0"/wayland-[0-9]* >/dev/null 2>&1' "$RT" || { failm "sway did not start"; cat "$RT/sway.log"; return 1; }
    SWAYSOCK=$(ls "$RT"/sway-ipc.*.sock | head -n 1); export SWAYSOCK
    WAYLAND_DISPLAY=$(basename "$(ls "$RT"/wayland-[0-9]* | grep -v '\.lock$' | head -n 1)"); export WAYLAND_DISPLAY
    # A headless seat has no keyboard: the window would lose keyboard focus every
    # time a wtype call ends. Hold one virtual keyboard open for the whole scenario.
    wtype -s 600000 &
    KBPID=$!
    sleep 0.5
}
stop_sway() {
    [ -n "$KBPID" ] && kill "$KBPID" 2>/dev/null
    KBPID=""
    [ -n "$SWAYPID" ] && kill "$SWAYPID" 2>/dev/null
    [ -n "$SWAYPID" ] && wait "$SWAYPID" 2>/dev/null
    SWAYPID=""
    rm -f "$RT"/sway-ipc.*.sock "$RT"/wayland-[0-9]*
}

# Weather fixture: fresh, invented place.
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"place":"Example Town","temperature":14.2,"units":"metric","code":2,"is_day":1,"condition":"Partly cloudy","updated":"%s"}\n' "$now" >"$RT/weather.json"
export TAOS_WEATHER_FILE="${E2E_WEATHER_FILE:-$RT/weather.json}"
export TAOS_GREETER_NO_DBUS=1

shot() { grim -o HEADLESS-1 "$OUT/$1.png" && pass "screenshot $1"; }
# A fresh wtype uploads its own keymap and the first event after it can be lost,
# so every call starts with a short sleep, and a whole sequence is ONE call.
key() { wtype -s 250 "$@"; }
launched() { grep -q "^taos-greeter " "$LOG" 2>/dev/null; }

# run_greeter <log-name> args...  (sets GPID, LOG, STDOUT)
run_greeter() {
    name=$1; shift
    LOG="$RT/$name.err"; STDOUT="$RT/$name.out"
    "$BIN" "$@" >"$STDOUT" 2>"$LOG" &
    GPID=$!
    wait_until 20 launched || { failm "$name: greeter did not start"; cat "$LOG"; return 1; }
    sleep 1.6   # first frame, appear animation, wallpaper upload
}
alive() { kill -0 "$GPID" 2>/dev/null; }
finish_login() { # name : types the demo password and asserts a clean exit
    key -d 40 demo -s 250 -k Return
    if wait_until 8 sh -c '! kill -0 "$0" 2>/dev/null' "$GPID"; then
        wait "$GPID"; rc=$?
        GPID=""
        [ "$rc" -eq 0 ] && pass "$1: exit 0 after login" || failm "$1: exit code $rc"
        grep -q '^demo: session start: taos-desktop-session (user ' "$STDOUT" && pass "$1: printed the session command" || { failm "$1: no session command in stdout"; cat "$STDOUT"; }
    else
        failm "$1: greeter still running after login"; kill "$GPID" 2>/dev/null; GPID=""
    fi
}

for size in $SIZES; do
    echo "== $size"
    start_sway "$size" || continue
    for users in 1 3; do
        t="$size-u$users"
        run_greeter "$t" --demo --demo-users "$users" || continue
        shot "$t-dark-idle"
        if [ "$users" -gt 1 ]; then
            key -k Right; sleep 0.5; shot "$t-dark-focus"
            key -k Return
        fi
        sleep 1.2
        shot "$t-dark-selected"
        key -d 40 wrong; sleep 0.6; shot "$t-dark-typed"
        key -k Return; sleep 0.5; shot "$t-dark-busy"
        sleep 1.4; shot "$t-dark-wrong"
        # Wrong then right in the same conversation (cancel_session in between).
        finish_login "$t"
        [ -n "$GPID" ] && { kill "$GPID" 2>/dev/null; GPID=""; }
    done
    # Light variant.
    run_greeter "$size-light" --demo --demo-users 3 --variant light && {
        shot "$size-u3-light-idle"
        key -k Return; sleep 1.2
        shot "$size-u3-light-selected"
        key -d 40 wrong -s 250 -k Return; sleep 2.4; shot "$size-u3-light-wrong"
        kill "$GPID" 2>/dev/null; wait "$GPID" 2>/dev/null; GPID=""
    }
    stop_sway
done

# ---- Scenarios at the first size only ---------------------------------------
first=${SIZES%% *}
echo "== extras at $first"
start_sway "$first" && {
    # No weather file: no placeholder, no error.
    TAOS_WEATHER_FILE=$RT/none.json run_greeter noweather --demo --demo-users 1 && { shot "$first-noweather"; kill "$GPID"; wait "$GPID" 2>/dev/null; GPID=""; }
    # Two-step PAM conversation (password, then a visible code).
    run_greeter otp --demo --demo-users 1 --demo-otp && {
        key -d 40 demo -s 250 -k Return; sleep 1.8
        shot "$first-otp-prompt"
        key -d 40 123456 -s 250 -k Return
        if wait_until 8 sh -c '! kill -0 "$0" 2>/dev/null' "$GPID"; then wait "$GPID"; [ $? -eq 0 ] && pass "otp: two-step login exits 0" || failm "otp exit code"; GPID=""; else failm "otp: still running"; kill "$GPID"; GPID=""; fi
    }
    # Reduced motion still logs in.
    run_greeter reduce --demo --demo-users 1 --reduce-motion && finish_login reduce-motion
    # Escape returns to the user row (several users).
    run_greeter esc --demo --demo-users 3 && {
        key -k Return; sleep 1.0; key -k Escape; sleep 1.0
        shot "$first-after-escape"
        kill "$GPID"; wait "$GPID" 2>/dev/null; GPID=""
    }
    # Idle numbers.
    if [ "$IDLE" -gt 0 ]; then
        run_greeter idle --demo --demo-users 3 && {
            sleep 3
            tck=$(getconf CLK_TCK)
            t0=$(awk '{print $14+$15}' /proc/$GPID/stat)
            sleep "$IDLE"
            t1=$(awk '{print $14+$15}' /proc/$GPID/stat)
            rss=$(awk '/VmRSS/ {print $2}' /proc/$GPID/status)
            echo "IDLE rss_kib=$rss cpu_ticks=$((t1 - t0)) over ${IDLE}s (tick=1/${tck}s) => cpu_percent=$(awk -v a="$t0" -v b="$t1" -v s="$IDLE" -v c="$tck" 'BEGIN{printf "%.2f", (b-a)/c/s*100}')"
            kill "$GPID"; wait "$GPID" 2>/dev/null; GPID=""
        }
    fi
    stop_sway
}
# ---- The production path: no --demo, a scripted greetd on GREETD_SOCK, users from
# a passwd fixture. Exercises the real discovery, the single-user auto-select and
# the real exit path. The power command is stubbed.
start_sway "$first" && {
    printf 'root:x:0:0:root:/root:/bin/bash\nsvc:x:998:998:Service:/var/lib/svc:/usr/sbin/nologin\nexampleuser:x:1000:1000:Example Person,,,:/home/exampleuser:/bin/bash\n' >"$RT/passwd"
    : >"$RT/greetd.log"
    python3 "$HERE/fake-greetd.py" "$RT/greetd.sock" "$RT/greetd.log" &
    FPID=$!
    sleep 0.5
    LOG="$RT/prod.err"; STDOUT="$RT/prod.out"
    GREETD_SOCK="$RT/greetd.sock" TAOS_GREETER_PASSWD="$RT/passwd" TAOS_GREETER_POWER_CMD=true "$BIN" >"$STDOUT" 2>"$LOG" &
    GPID=$!
    wait_until 20 launched; sleep 1.8
    grep -q "users=1 " "$LOG" && pass "production path: one user discovered from passwd" || failm "production path: user discovery"
    shot "$first-production-selected"
    key -d 40 wrong -s 250 -k Return; sleep 1.6
    key -d 40 demo -s 250 -k Return
    if wait_until 8 sh -c '! kill -0 "$0" 2>/dev/null' "$GPID"; then
        wait "$GPID"; rc=$?; GPID=""
        [ "$rc" -eq 0 ] && pass "production path: exit 0 after login" || failm "production path: exit code $rc"
    else
        failm "production path: still running after login"; kill "$GPID"; GPID=""
    fi
    wait "$FPID" 2>/dev/null
    FPID=""
    [ "$(grep -c '^create_session' "$RT/greetd.log")" -eq 2 ] && grep -q '^cancel_session' "$RT/greetd.log" && pass "production path: cancel_session sent between the attempts" || { failm "production path: wire log"; cat "$RT/greetd.log"; }
    grep -q '^start_session taos-desktop-session$' "$RT/greetd.log" && pass "production path: start_session carried the session command" || failm "production path: no start_session"
    # No users at all: a readable message, still running.
    printf 'root:x:0:0:root:/root:/bin/bash\n' >"$RT/passwd0"
    python3 "$HERE/fake-greetd.py" "$RT/greetd2.sock" "$RT/greetd2.log" &
    FPID=$!
    sleep 0.5
    LOG="$RT/nouser.err"; STDOUT="$RT/nouser.out"
    GREETD_SOCK="$RT/greetd2.sock" TAOS_GREETER_PASSWD="$RT/passwd0" TAOS_GREETER_POWER_CMD=true "$BIN" >"$STDOUT" 2>"$LOG" &
    GPID=$!
    wait_until 20 launched; sleep 1.8
    shot "$first-no-users"
    alive && pass "no users: keeps running with a message" || failm "no users: exited"
    kill "$GPID" 2>/dev/null; wait "$GPID" 2>/dev/null; GPID=""
    kill "$FPID" 2>/dev/null; wait "$FPID" 2>/dev/null; FPID=""
    stop_sway
}
# Missing GREETD_SOCK: a readable message, then a non-zero exit after about 5 s.
start_sway "$first" && {
    LOG="$RT/nosock.err"; STDOUT="$RT/nosock.out"
    env -u GREETD_SOCK "$BIN" >"$STDOUT" 2>"$LOG" &
    GPID=$!
    wait_until 20 launched; sleep 1.6
    shot "$first-no-greetd"
    if wait_until 9 sh -c '! kill -0 "$0" 2>/dev/null' "$GPID"; then wait "$GPID"; rc=$?; [ "$rc" -ne 0 ] && pass "no GREETD_SOCK: exits non-zero ($rc)" || failm "no GREETD_SOCK: exit 0"; GPID=""; else failm "no GREETD_SOCK: still running after 9 s"; kill "$GPID"; GPID=""; fi
    grep -q "GREETD_SOCK" "$LOG" && pass "no GREETD_SOCK: message logged" || failm "no message logged"
    stop_sway
}

echo "failures: $fails"
[ "$fails" -eq 0 ]
