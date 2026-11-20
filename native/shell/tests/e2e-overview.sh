#!/bin/sh
# End to end check of taos-overview under a HEADLESS sway (never the physical seat).
#
# Starts its own compositor in a private XDG_RUNTIME_DIR, opens five foot windows over two
# workspaces (one of them floating), starts taos-overview, drives the switcher and the overview
# through the control socket and the sway key bindings, takes grim screenshots of each state and
# asserts through `swaymsg -t get_tree` that focus really moved. Stops only what it started.
#
# Needs: sway (swayfx for the blur), swaymsg, grim, foot, python3, and the Qt runtime for
# taos-overview in the environment (LD_LIBRARY_PATH, QT_PLUGIN_PATH ... see dev-env.sh.example).
# wtype is optional: with it the Alt+Tab key bindings are exercised too.
#
#   TAOS_OVERVIEW_BIN=/path/to/taos-overview OUT_DIR=/path/for/shots tests/e2e-overview.sh
#
# Optional: E2E_RES=1920x1080 (output size, e.g. 3840x1200), E2E_RENDER_NODE=/dev/dri/renderD128,
#           E2E_IDLE_SECONDS=10 (measure the hidden daemon's CPU and RSS).
# Exits non-zero when any assertion fails.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SHELL_DIR=$(dirname "$HERE")
OV=${TAOS_OVERVIEW_BIN:?set TAOS_OVERVIEW_BIN to the taos-overview binary}
OUT=${OUT_DIR:-$PWD/e2e-overview-out}
RES=${E2E_RES:-1920x1080}
IDLE=${E2E_IDLE_SECONDS:-0}
mkdir -p "$OUT"
helper() { python3 "$HERE/overview_helper.py" "$@"; }

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
failm() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1 ($3)"; else failm "$1: expected '$2', got '$3'"; fi; }
assert_ge() { if awk "BEGIN{exit !($3 >= $2)}"; then pass "$1 ($3 >= $2)"; else failm "$1: expected >= $2, got '$3'"; fi; }

RT=$(mktemp -d "${TMPDIR:-/tmp}/taos-ov-e2e.XXXXXX") || exit 1
chmod 700 "$RT"
SWAYPID=""
OVPID=""
PIDS=""          # test clients we started
cleanup() {
    [ -n "$OVPID" ] && kill "$OVPID" 2>/dev/null
    for p in $PIDS; do kill "$p" 2>/dev/null; done
    [ -n "$SWAYPID" ] && kill "$SWAYPID" 2>/dev/null
    sleep 0.3
    rm -rf "$RT"
}
trap cleanup EXIT INT TERM

wait_until() { n=$(( $1 * 10 )); shift; while [ "$n" -gt 0 ]; do "$@" && return 0; sleep 0.1; n=$((n - 1)); done; return 1; }
status() { "$OV" status; }
field() { status | helper get "$1"; }
focused() { swaymsg -t get_tree | helper focused; }

# ---- compositor -----------------------------------------------------------------------------
cat >"$RT/sway.conf" <<EOC
output HEADLESS-1 resolution $RES position 0 0
default_border pixel 1
font pango:Inter 11
bindsym Mod4+Return exec true
include $SHELL_DIR/overview/sway/40-overview.conf
EOC
export XDG_RUNTIME_DIR="$RT"
unset WAYLAND_DISPLAY SWAYSOCK I3SOCK
if [ -n "${E2E_RENDER_NODE:-}" ]; then export WLR_RENDERER=gles2 WLR_RENDER_DRM_DEVICE="$E2E_RENDER_NODE"; fi
# the key bindings run `taos-overview ...`: make that name resolve to the binary under test
mkdir -p "$RT/bin"
ln -s "$OV" "$RT/bin/taos-overview"
PATH="$RT/bin:$PATH"; export PATH
WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 sway -c "$RT/sway.conf" >"$RT/sway.log" 2>&1 &
SWAYPID=$!
wait_until 15 sh -c 'ls "$0"/sway-ipc.*.sock >/dev/null 2>&1 && ls "$0"/wayland-[0-9]* >/dev/null 2>&1' "$RT" || { failm "sway did not start"; cat "$RT/sway.log"; exit 1; }
SWAYSOCK=$(ls "$RT"/sway-ipc.*.sock | head -n 1); export SWAYSOCK
WAYLAND_DISPLAY=$(basename "$(ls "$RT"/wayland-[0-9]* | grep -v '\.lock$' | head -n 1)"); export WAYLAND_DISPLAY
pass "headless sway up ($RES)"

# ---- five windows over two workspaces, one floating -------------------------------------------------
mkwin() { # title colour workspace [app_id]
    swaymsg workspace number "$3" >/dev/null
    # shellcheck disable=SC2086
    foot ${4:+-a $4} -T "$1" -o "colors.background=$2" -o colors.foreground=ffffff -o font=monospace:size=26 \
        "$HERE/loop-window.sh" "$1" >/dev/null 2>&1 &
    PIDS="$PIDS $!"
    wait_until 10 sh -c 'swaymsg -t get_tree | grep -q "\"name\": \"$0\""' "$1" || failm "window $1 did not appear"
    sleep 0.3
}
mkwin "build log" 1d3557 1
mkwin "notes" 3a1d57 1
mkwin "server" 14532d 2
mkwin "editor" 57331d 2
mkwin "floating calc" 571d3a 2 floatapp
swaymsg "[app_id=floatapp] floating enable, resize set 700 400" >/dev/null
swaymsg workspace number 1 >/dev/null
sleep 0.5
assert_eq "five windows" 5 "$(swaymsg -t get_tree | helper count)"
assert_eq "two workspaces hold them" "1=2 2=3" "$(swaymsg -t get_tree | helper workspaces)"
swaymsg "[title=notes] focus" >/dev/null
sleep 0.3
FIRST=$(focused | cut -d' ' -f1)

# ---- the daemon --------------------------------------------------------------------------------
LOG="$RT/overview.log"
"$OV" daemon >"$LOG" 2>&1 &
OVPID=$!
wait_until 15 grep -q "ready in" "$LOG" || { failm "taos-overview did not start"; cat "$LOG"; exit 1; }
pass "daemon started: $(grep -o 'ready in [0-9]* ms' "$LOG")"
assert_eq "starts hidden" hidden "$(field mode)"
wait_until 5 sh -c '[ "$("$0" status | python3 "$1" get known)" = 5 ]' "$OV" "$HERE/overview_helper.py"
assert_eq "knows five windows" 5 "$(field known)"
assert_eq "capture protocols present" True "$(field capture)"
assert_eq "socket mode" 600 "$(stat -c %a "$RT/taos-shell/overview.sock")"
if "$OV" bogus 2>/dev/null; then failm "unknown command accepted"; else pass "unknown command refused"; fi

# ---- the switcher ----------------------------------------------------------------------------------
"$OV" switcher --sticky
wait_until 5 sh -c '[ "$("$0" status | python3 "$1" get mode)" = switcher ]' "$OV" "$HERE/overview_helper.py" || failm "switcher did not open"
sleep 1.5
assert_eq "switcher: second most recent window preselected" 1 "$(field selected)"
grim -t ppm "$RT/sw.ppm" && grim "$OUT/switcher-$RES.png" && pass "switcher screenshot"
status >/dev/null           # resets the frame counters
sleep 2.5
set -- $(status | helper fps)
assert_ge "switcher: live thumbnails (windows above 3 fps, including other workspaces)" 4 "$1"
pass "switcher: best thumbnail rate $2 fps"
W=${RES%x*}; H=${RES#*x}
# the panel sits in the middle: the foot windows' own colours must appear inside it
assert_ge "switcher: thumbnails painted (notes colour in the panel)" 0.01 "$(helper colorfrac "$RT/sw.ppm" $((W / 4)) $((H * 3 / 10)) $((W * 3 / 4)) $((H * 7 / 10)) 3a1d57 14)"
"$OV" next
sleep 0.4
assert_eq "switcher: next moves the selection" 2 "$(field selected)"
"$OV" prev; "$OV" prev
sleep 0.4
assert_eq "switcher: prev wraps" 0 "$(field selected)"
"$OV" hide
wait_until 5 sh -c '[ "$("$0" status | python3 "$1" get mode)" = hidden ]' "$OV" "$HERE/overview_helper.py" || failm "hide did not close"
sleep 0.5
assert_eq "hide leaves focus alone" "$FIRST" "$(focused | cut -d' ' -f1)"

# selecting another window really focuses it, on its own workspace
"$OV" switcher --sticky
sleep 0.8
"$OV" next
"$OV" next          # selected = 3
sleep 0.4
LIST=$(status | helper get list)
WANT=$(printf '%s' "$LIST" | python3 -c 'import json,sys; print(json.load(sys.stdin)[3]["con_id"])')
"$OV" commit
sleep 0.8
assert_eq "commit focuses the selection" "$WANT" "$(focused | cut -d' ' -f1)"
assert_eq "switcher closed after commit" hidden "$(field mode)"

# ---- the overview ----------------------------------------------------------------------------------
"$OV" overview
wait_until 5 sh -c '[ "$("$0" status | python3 "$1" get mode)" = overview ]' "$OV" "$HERE/overview_helper.py" || failm "overview did not open"
sleep 1.5
grim -t ppm "$RT/ov.ppm" && grim "$OUT/overview-$RES.png" && pass "overview screenshot"
assert_ge "overview: windows of the current workspace laid out" 1 "$(field layout)"
"$OV" type serv
sleep 0.8
assert_eq "overview: typing filters across workspaces" 1 "$(field layout)"
grim "$OUT/overview-filter-$RES.png" && pass "overview filter screenshot"
"$OV" hide
sleep 0.6
"$OV" overview
sleep 0.8
"$OV" overview          # a second call closes it
sleep 0.8
assert_eq "overview toggles closed" hidden "$(field mode)"

# ---- key bindings (needs wtype) ------------------------------------------------------------------------
if command -v wtype >/dev/null 2>&1; then
    swaymsg "[title=notes] focus" >/dev/null; sleep 0.3
    BEFORE=$(focused | cut -d' ' -f1)
    # Alt held, Tab twice: each Tab is a sway binding that runs `taos-overview switcher`; then Alt goes up
    wtype -M alt -k Tab -s 500 -k Tab -s 500 -m alt -s 150 -k Alt_L
    sleep 1.0
    assert_eq "Alt+Tab+Tab then Alt release: overlay closed" hidden "$(field mode)"
    NOW=$(focused | cut -d' ' -f1)
    if [ "$NOW" = none ] || [ "$NOW" = "$BEFORE" ]; then failm "Alt+Tab+Tab did not move focus (before $BEFORE, now $NOW)"; else pass "Alt+Tab+Tab moved focus ($BEFORE -> $NOW)"; fi
else
    echo "SKIP  key bindings (wtype not installed)"
fi

# ---- the Super tap: opens the overview, but never after Super+key ----------------------------------
if command -v wtype >/dev/null 2>&1; then
    wtype -P Super_L -s 100 -k Return -s 100 -p Super_L
    sleep 1.0
    assert_eq "Super+Return does not trigger the overview" hidden "$(field mode)"
    wtype -P Super_L -s 100 -k x -s 100 -p Super_L
    sleep 1.0
    assert_eq "Super+x (unbound) does not trigger the overview" hidden "$(field mode)"
    wtype -k Super_L
    sleep 1.0
    assert_eq "a Super tap opens the overview" overview "$(field mode)"
    wtype -k Super_L
    sleep 1.0
    assert_eq "a second Super tap closes it" hidden "$(field mode)"
fi

# ---- fail-safes: the overlay owns the keyboard, so it must never keep it ------------------------------
kill -TERM "$OVPID"; wait "$OVPID" 2>/dev/null; OVPID=""
TAOS_OVERVIEW_IDLE_MS=2500 "$OV" daemon >"$LOG" 2>&1 &
OVPID=$!
wait_until 15 grep -q "ready in" "$LOG" || failm "second daemon did not start"
wait_until 5 sh -c '[ "$("$0" status | python3 "$1" get known)" = 5 ]' "$OV" "$HERE/overview_helper.py"
"$OV" switcher --sticky
sleep 1.0
assert_eq "idle: open" switcher "$(field mode)"
sleep 3.5
assert_eq "idle: closes itself after a quiet spell" hidden "$(field mode)"
swaymsg "[title=notes] focus" >/dev/null; sleep 0.3
BEFORE=$(focused | cut -d' ' -f1)
"$OV" switcher --sticky
sleep 1.0
assert_eq "crash test: overlay holds the keyboard (no window has focus)" none "$(focused | cut -d' ' -f1)"
kill -9 "$OVPID"; wait "$OVPID" 2>/dev/null; OVPID=""
sleep 1.0
assert_eq "kill -9 while visible: the keyboard goes back to the window" "$BEFORE" "$(focused | cut -d' ' -f1)"
# and a fresh daemon starts over the stale socket
"$OV" daemon >"$LOG" 2>&1 &
OVPID=$!
wait_until 15 grep -q "ready in" "$LOG" || failm "daemon did not restart over the stale socket"
assert_eq "restart over a stale socket" hidden "$(field mode)"

# ---- idle cost of the hidden daemon --------------------------------------------------------------------------
if [ "$IDLE" -gt 0 ]; then
    sleep 2
    t0=$(awk '{print $14+$15}' "/proc/$OVPID/stat"); sleep "$IDLE"; t1=$(awk '{print $14+$15}' "/proc/$OVPID/stat")
    hz=$(getconf CLK_TCK)
    echo "IDLE  hidden for ${IDLE}s: cpu ticks $((t1 - t0)) (of $((IDLE * hz))), rss $(awk '/VmRSS/{print $2}' "/proc/$OVPID/status") KiB"
fi

if grep -qiE "ReferenceError|TypeError|unable to assign|QML .*: " "$LOG"; then failm "QML warnings in the daemon log"; sed 's/^/    /' "$LOG"; else pass "no QML warnings in the daemon log"; fi
kill -0 "$OVPID" 2>/dev/null && pass "daemon alive at the end" || failm "daemon died"
kill -TERM "$OVPID"; wait "$OVPID"; rc=$?; OVPID=""
assert_eq "SIGTERM exits cleanly" 0 "$rc"
[ "$fails" -eq 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
exit "$fails"
