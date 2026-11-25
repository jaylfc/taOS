#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# End to end check of where new floating windows open, under a HEADLESS sway with a bar on top (never the physical seat).
# An app that asks for more than the screen, then one that asks for a normal size, two of the same app, and a reopened app.
#   TAOS_WINDOW_BIN=/path/to/taos-window OUT_DIR=/path E2E_RES=3840x1200 tests/e2e-placement.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
TW=${TAOS_WINDOW_BIN:?set TAOS_WINDOW_BIN}
OUT=${OUT_DIR:-$PWD/e2e-placement-out}; RES=${E2E_RES:-1920x1080}; SWAY=${E2E_SWAY:-sway}
mkdir -p "$OUT"
fails=0
pass() { printf 'PASS  %s\n' "$1"; }
failm() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }
wait_until() { n=$(( $1 * 10 )); shift; while [ "$n" -gt 0 ]; do "$@" && return 0; sleep 0.1; n=$((n - 1)); done; return 1; }
RT=$(mktemp -d "${E2E_TMP:-${TMPDIR:-/tmp}}/taos-place-e2e.XXXXXX") || exit 1
chmod 700 "$RT"; mkdir -p "$RT/tmp" "$RT/cfg/taos" "$RT/data" "$RT/cache" "$RT/state"
export XDG_RUNTIME_DIR="$RT" XDG_CONFIG_HOME="$RT/cfg" XDG_DATA_HOME="$RT/data" XDG_CACHE_HOME="$RT/cache" XDG_STATE_HOME="$RT/state" TMPDIR="$RT/tmp"
unset WAYLAND_DISPLAY SWAYSOCK I3SOCK
SWAYPID=""; DPID=""; PIDS=""
cleanup() { for p in $PIDS $DPID $SWAYPID; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; sleep 0.3; rm -rf "$RT"; }
trap cleanup EXIT INT TERM
cat >"$RT/sway.conf" <<EOC
output HEADLESS-1 resolution $RES position 0 0
default_border pixel 1
font pango:Inter 11
bar { position top
  height 48 }
for_window [app_id="^big.*"] floating enable
for_window [app_id="^dlg.*"] floating enable
EOC
[ -n "${E2E_RENDER_NODE:-}" ] && export WLR_RENDERER=gles2 WLR_RENDER_DRM_DEVICE="$E2E_RENDER_NODE"
WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 "$SWAY" -c "$RT/sway.conf" >"$RT/sway.log" 2>&1 &
SWAYPID=$!
wait_until 15 sh -c 'ls "$0"/sway-ipc.*.sock >/dev/null 2>&1 && ls "$0"/wayland-[0-9]* >/dev/null 2>&1' "$RT" || { failm "sway did not start"; exit 1; }
SWAYSOCK=$(ls "$RT"/sway-ipc.*.sock | head -n 1); export SWAYSOCK
WAYLAND_DISPLAY=$(basename "$(ls "$RT"/wayland-[0-9]* | grep -v '\.lock$' | head -n 1)"); export WAYLAND_DISPLAY
"$TW" mode-daemon >"$RT/daemon.log" 2>&1 &
DPID=$!
sleep 1
W=${RES%x*}; H=${RES#*x}
# usable area of the workspace as sway reports it, and a window's rect: "x y w h"
usable() { swaymsg -t get_tree | python3 -c 'import json,sys
def w(n):
    if n.get("type")=="workspace" and not n["name"].startswith("__"): r=n["rect"]; print(r["x"],r["y"],r["width"],r["height"]); return True
    return any(w(c) for c in n.get("nodes",[]))
w(json.load(sys.stdin))'; }
rect() { swaymsg -t get_tree | python3 -c 'import json,sys
def w(n,out):
    if n.get("pid") and n.get("app_id")==sys.argv[1]: out.append(n)
    for k in ("nodes","floating_nodes"):
        for c in n.get(k,[]): w(c,out)
o=[]; w(json.load(sys.stdin),o)
o.sort(key=lambda n:n["id"])
n=o[int(sys.argv[2])]; r=n["rect"]; print(r["x"],r["y"],r["width"],r["height"])' "$1" "${2:--1}"; }
open() { # app_id title WxH
    foot -a "$1" -T "$2" -w "$3" sleep 600 >/dev/null 2>&1 &
    PIDS="$PIDS $!"
    wait_until 10 sh -c 'swaymsg -t get_tree | grep -q "\"app_id\": \"$0\""' "$1" || failm "$1 did not open"
    sleep 1.2
}
inside() { # "x y w h" of window, "x y w h" of area -> ok when inside
    python3 -c 'import sys
x,y,w,h=map(int,sys.argv[1].split()); ax,ay,aw,ah=map(int,sys.argv[2].split())
sys.exit(0 if x>=ax and y>=ay and x+w<=ax+aw and y+h<=ay+ah else 1)' "$1" "$2"; }
AREA=$(usable)
echo "usable area: $AREA"
open bigone one 5000x3000
R=$(rect bigone -1); echo "asked 5000x3000 -> $R"
inside "$R" "$AREA" && pass "an app asking for more than the screen opens fully inside the usable area" || failm "outside: $R in $AREA"
python3 -c 'import sys
x,y,w,h=map(int,sys.argv[1].split()); ax,ay,aw,ah=map(int,sys.argv[2].split())
sys.exit(0 if w<=aw*0.9+2 and h<=ah*0.9+2 else 1)' "$R" "$AREA" && pass "at most 90 percent of the usable area" || failm "too large: $R"
grim "$OUT/placement-big-$RES.png" && pass "screenshot"
open bigtwo two 1500x950
R2=$(rect bigtwo -1); echo "asked 1500x950 -> $R2"
inside "$R2" "$AREA" && pass "1500x950 fully inside" || failm "outside: $R2"
open bigtwo two-b 1500x950
R3=$(rect bigtwo -1); echo "second of the same app -> $R3"
[ "$R3" != "$R2" ] && pass "the second window of an app is cascaded, not stacked" || failm "stacked on the first"
grim "$OUT/placement-cascade-$RES.png"
open dlgone dialog 500x300
R4=$(rect dlgone -1); echo "small dialog -> $R4"
inside "$R4" "$AREA" && pass "small window inside" || failm "outside: $R4"
# remembered: move the window, close it, open the app again
ID=$(swaymsg -t get_tree | python3 -c 'import json,sys
def w(n,o):
    if n.get("pid") and n.get("app_id")=="dlgone": o.append(n["id"])
    for k in ("nodes","floating_nodes"):
        for c in n.get(k,[]): w(c,o)
o=[]; w(json.load(sys.stdin),o); print(o[0])')
swaymsg "[con_id=$ID] move absolute position 123 234" >/dev/null; sleep 1.0
swaymsg workspace 2 >/dev/null; sleep 0.3; swaymsg workspace 1 >/dev/null; sleep 0.5   # a window event so the daemon sees the new place
swaymsg "[con_id=$ID] kill" >/dev/null; sleep 1.0
open dlgone dialog 500x300
R5=$(rect dlgone -1); echo "reopened -> $R5 (was moved to 123,234)"
case "$R5" in "123 234 "*) pass "the app's last place is remembered" ;; *) failm "not remembered: $R5" ;; esac
echo "e2e-placement: $fails failure(s)"
[ "$fails" -eq 0 ]
