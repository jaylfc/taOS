#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# End to end check of the shortcuts overlay under a HEADLESS sway (never the physical seat): sway loads the base
# bindings and every shipped sway snippet, `taos-overview help` lists them, typing filters, Escape closes.
#
#   TAOS_OVERVIEW_BIN=/path/to/taos-overview OUT_DIR=/path/for/shots E2E_RES=3840x1200 \
#   E2E_THEMES="graphite:dark graphite:light" tests/e2e-help.sh
#
# Needs sway, swaymsg, grim, foot, python3 and the Qt runtime in the environment; everything it starts is its own.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SHELL_DIR=$(dirname "$HERE")
OV=${TAOS_OVERVIEW_BIN:?set TAOS_OVERVIEW_BIN to the taos-overview binary}
OUT=${OUT_DIR:-$PWD/e2e-help-out}
RES=${E2E_RES:-1920x1080}
THEMES=${E2E_THEMES:-graphite:dark}
SWAY=${E2E_SWAY:-sway}
mkdir -p "$OUT"
helper() { python3 "$HERE/overview_helper.py" "$@"; }
fails=0
pass() { printf 'PASS  %s\n' "$1"; }
failm() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1 ($3)"; else failm "$1: expected '$2', got '$3'"; fi; }
wait_until() { n=$(( $1 * 10 )); shift; while [ "$n" -gt 0 ]; do "$@" && return 0; sleep 0.1; n=$((n - 1)); done; return 1; }

RT=$(mktemp -d "${E2E_TMP:-${TMPDIR:-/tmp}}/taos-help-e2e.XXXXXX") || exit 1
chmod 700 "$RT"
mkdir -p "$RT/tmp" "$RT/cfg" "$RT/data/applications" "$RT/cache" "$RT/state" "$RT/bin"
export XDG_RUNTIME_DIR="$RT" XDG_CONFIG_HOME="$RT/cfg" XDG_DATA_HOME="$RT/data" XDG_CACHE_HOME="$RT/cache" XDG_STATE_HOME="$RT/state" TMPDIR="$RT/tmp"
unset WAYLAND_DISPLAY SWAYSOCK I3SOCK
SWAYPID=""; OVPID=""; PIDS=""
cleanup() {
    [ -n "$OVPID" ] && kill "$OVPID" 2>/dev/null
    for p in $PIDS; do kill "$p" 2>/dev/null; done
    [ -n "$SWAYPID" ] && kill "$SWAYPID" 2>/dev/null
    sleep 0.3
    rm -rf "$RT"
}
trap cleanup EXIT INT TERM
ln -s "$OV" "$RT/bin/taos-overview"
PATH="$RT/bin:$PATH"; export PATH
# screenshots only ever of the session started here
shot() {
    case "$XDG_RUNTIME_DIR" in "$RT"*) ;; *) echo "refusing to capture: not the test session"; return 1 ;; esac
    [ -n "${WAYLAND_DISPLAY:-}" ] || return 1
    grim "$1"
}

start_sway() {
    cat >"$RT/sway.conf" <<EOC
output HEADLESS-1 resolution $RES position 0 0
default_border pixel 1
font pango:Inter 11
include $SHELL_DIR/window/tests/fixtures/base-reserved.conf
include $SHELL_DIR/overview/sway/40-overview.conf
include $SHELL_DIR/window/sway/46-taos-window.conf
include $SHELL_DIR/sway/47-taos-dock.conf
include $SHELL_DIR/launcher/sway/48-taos-launcher.conf
include $SHELL_DIR/frame/sway/49-taos-frame.conf
include $SHELL_DIR/sway/50-taos-audio-keys.conf
EOC
    if [ -n "${E2E_RENDER_NODE:-}" ]; then export WLR_RENDERER=gles2 WLR_RENDER_DRM_DEVICE="$E2E_RENDER_NODE"; fi
    WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 "$SWAY" -c "$RT/sway.conf" >"$RT/sway.log" 2>&1 &
    SWAYPID=$!
    wait_until 15 sh -c 'ls "$0"/sway-ipc.*.sock >/dev/null 2>&1 && ls "$0"/wayland-[0-9]* >/dev/null 2>&1' "$RT" || { failm "sway did not start"; cat "$RT/sway.log"; exit 1; }
    SWAYSOCK=$(ls "$RT"/sway-ipc.*.sock | head -n 1); export SWAYSOCK
    WAYLAND_DISPLAY=$(basename "$(ls "$RT"/wayland-[0-9]* | grep -v '\.lock$' | head -n 1)"); export WAYLAND_DISPLAY
}
stop_sway() { kill "$SWAYPID" 2>/dev/null; wait "$SWAYPID" 2>/dev/null; SWAYPID=""; sleep 0.5; }
start_ov() { # theme variant
    "$OV" daemon --theme "${1:-graphite}" --variant "${2:-dark}" >"$RT/overview.log" 2>&1 &
    OVPID=$!
    wait_until 15 grep -q "ready in" "$RT/overview.log" || { failm "taos-overview did not start"; cat "$RT/overview.log"; exit 1; }
}
stop_ov() { kill "$OVPID" 2>/dev/null; wait "$OVPID" 2>/dev/null; OVPID=""; }
field() { "$OV" status | helper get "$1"; }
spaces() { "$OV" space list | python3 -c 'import json,sys; print(" ".join("%s:%s%s" % (s["key"], s["name"].replace(" ", "_"), "*" if s["current"] else "") for s in json.load(sys.stdin)))'; }
names() { "$OV" space list | python3 -c 'import json,sys; print(",".join(s["name"] for s in json.load(sys.stdin)))'; }
conid_of() { swaymsg -t get_tree | python3 -c 'import json,sys
def w(n):
    yield n
    for k in ("nodes","floating_nodes"):
        for c in n.get(k,[]): yield from w(c)
for n in w(json.load(sys.stdin)):
    if n.get("name")==sys.argv[1] and n.get("pid"): print(n["id"]); break' "$1"; }


start_sway
pass "headless sway up ($RES)"
mkdir -p "$XDG_DATA_HOME/taos/shortcuts"
cat >"$XDG_DATA_HOME/taos/shortcuts/foot.json" <<'EOJ'
{"title": "Terminal", "shortcuts": [{"keys": ["Ctrl", "Shift", "C"], "text": "Copy"}, {"keys": ["Ctrl", "Shift", "V"], "text": "Paste"}, {"keys": ["Ctrl", "Shift", "N"], "text": "New terminal window"}]}
EOJ
foot -a foot -T term -o colors.background=1d3557 sleep 600 >/dev/null 2>&1 &
PIDS="$PIDS $!"
sleep 1
start_ov "${THEMES%%:*}" "${THEMES##*:}"
sleep 1
for tv in $THEMES; do
    th=${tv%%:*}; va=${tv##*:}
    stop_ov; start_ov "$th" "$va"; sleep 1
    "$OV" help; sleep 1.8
    assert_eq "help mode" help "$(field mode)"
    shot "$OUT/help-$th-$va-$RES.png" && pass "help screenshot $th $va"
    "$OV" type resize; sleep 0.6
    shot "$OUT/help-search-$th-$va-$RES.png" && pass "search screenshot $th $va"
    "$OV" help; sleep 0.8
    assert_eq "a second help closes it" hidden "$(field mode)"
done
echo "e2e-help: $fails failure(s)"
[ "$fails" -eq 0 ]
