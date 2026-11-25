#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# End to end check of the overview's desktops (spaces) under a HEADLESS sway (never the physical seat).
#
# Proves, with a real compositor and the real daemon: an emptied desktop survives sway dropping its workspace, the
# list survives a restart of both sway and the daemon, add / rename / reorder / remove / move-window through the command
# socket, and takes screenshots of the overview in every theme given.
#
#   TAOS_OVERVIEW_BIN=/path/to/taos-overview OUT_DIR=/path/for/shots E2E_RES=3840x1200 \
#   E2E_THEMES="graphite:dark graphite:light" tests/e2e-spaces.sh
#
# Needs sway, swaymsg, grim, foot, python3 and the Qt runtime in the environment; everything it starts is its own.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SHELL_DIR=$(dirname "$HERE")
OV=${TAOS_OVERVIEW_BIN:?set TAOS_OVERVIEW_BIN to the taos-overview binary}
OUT=${OUT_DIR:-$PWD/e2e-spaces-out}
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

RT=$(mktemp -d "${E2E_TMP:-${TMPDIR:-/tmp}}/taos-spaces-e2e.XXXXXX") || exit 1
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
bindsym Mod4+Return exec true
include $SHELL_DIR/overview/sway/40-overview.conf
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

# an app whose window says "Claude" and whose launcher says com.anthropic.Claude (the owner's case), with a real icon
python3 - "$RT/data" <<'EOP'
import struct, sys, zlib
d = sys.argv[1]
def png(path, w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
png(d + "/claude.png", 128, 128, (217, 119, 87))
open(d + "/applications/taos-appgroups-claude.desktop", "w").write(
    "[Desktop Entry]\nType=Application\nName=Claude\nExec=/usr/bin/true run claude %U\nIcon=" + d + "/claude.png\n"
    "StartupWMClass=com.anthropic.Claude\nX-taOS-AppId=com.anthropic.Claude\n")
EOP

start_sway
pass "headless sway up ($RES)"
mkwin() { # title colour workspace [app_id]
    swaymsg workspace number "$3" >/dev/null
    foot ${4:+-a $4} -T "$1" -o "colors.background=$2" -o colors.foreground=ffffff -o font=monospace:size=22 "$HERE/loop-window.sh" "$1" >/dev/null 2>&1 &
    PIDS="$PIDS $!"
    wait_until 10 sh -c 'swaymsg -t get_tree | grep -q "\"name\": \"$0\""' "$1" || failm "window $1 did not appear"
    sleep 0.3
}
mkwin "build log" 1d3557 1
mkwin "notes" 3a1d57 1
mkwin "server" 14532d 2
mkwin "Claude" 571d3a 2 Claude
mkwin "editor" 57331d 3
swaymsg workspace number 1 >/dev/null
start_ov graphite dark
wait_until 5 sh -c '[ "$("$0" status | python3 "$1" get known)" = 5 ]' "$OV" "$HERE/overview_helper.py"

# ---- the owner's case: a desktop is emptied and sway forgets its workspace --------------------------------
assert_eq "three desktops adopted from sway" "Desktop_1 Desktop_2 Desktop_3" "$(spaces | tr ' ' '\n' | sed 's/^[^:]*://; s/\*//' | tr '\n' ' ' | sed 's/ $//')"
"$OV" space rename 2 Mail >/dev/null
assert_eq "rename" "Desktop 1,Mail,Desktop 3" "$(names)"
swaymsg "[workspace=3] kill" >/dev/null
wait_until 8 sh -c '! swaymsg -t get_workspaces | grep -q "\"name\": \"3\""' || failm "workspace 3 still alive in sway"
assert_eq "an emptied desktop stays" "Desktop 1,Mail,Desktop 3" "$(names)"
"$OV" space add Scratch >/dev/null
assert_eq "add" "Desktop 1,Mail,Desktop 3,Scratch" "$(names)"
"$OV" space move 4 1 >/dev/null
assert_eq "reorder by position" "Scratch,Desktop 2,Mail,Desktop 4" "$(names)" 

# ---- restart: sway and the daemon both start again, the desktops are still there ---------------------------
stop_ov
swaymsg exit >/dev/null 2>&1; sleep 1; stop_sway
for p in $PIDS; do kill "$p" 2>/dev/null; done; PIDS=""
start_sway
start_ov graphite dark
sleep 1
assert_eq "desktops survive a restart of sway and the daemon" "Scratch,Desktop 2,Mail,Desktop 4" "$(names)"
assert_eq "no stray desktop from the fresh bare workspace" 4 "$("$OV" space list | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"

# ---- windows, move-window, remove ------------------------------------------------------------------------------
mkwin "build log" 1d3557 3
mkwin "notes" 3a1d57 3
mkwin "server" 14532d 1
mkwin "Claude" 571d3a 1 Claude
mkwin "editor" 57331d 2
swaymsg workspace number 1 >/dev/null
sleep 0.5
"$OV" space go 2 >/dev/null; sleep 0.4
ID=$(conid_of "server")
"$OV" space move-window "$ID" 3 >/dev/null; sleep 0.5
assert_eq "move-window sends it to that desktop" "2" "$(swaymsg -t get_tree | python3 -c 'import json,sys
def w(n,ws=None):
    if n.get("type")=="workspace": ws=n["name"]
    if n.get("pid") and n.get("name")=="server": print(ws)
    for k in ("nodes","floating_nodes"):
        for c in n.get(k,[]): w(c,ws)
w(json.load(sys.stdin))')"
"$OV" space remove 3 >/dev/null; sleep 0.8
assert_eq "remove drops the desktop" "Scratch,Desktop 2,Desktop 3" "$(names)"
assert_eq "its windows moved to a neighbour, none lost" 5 "$(swaymsg -t get_tree | python3 "$HERE/overview_helper.py" count)"
if "$OV" space remove 1 >/dev/null && "$OV" space remove 1 >/dev/null && "$OV" space remove 1 2>/dev/null; then failm "the last desktop was removed"; else pass "the last desktop cannot be removed"; fi
names >/dev/null
# bring back three for the pictures
"$OV" space add >/dev/null 2>&1; "$OV" space add >/dev/null 2>&1

# ---- the overview in every theme ----------------------------------------------------------------------------------
for tv in $THEMES; do
    th=${tv%%:*}; va=${tv##*:}
    stop_ov; start_ov "$th" "$va"; sleep 1
    "$OV" overview; sleep 1.8
    shot "$OUT/overview-$th-$va-$RES.png" && pass "overview screenshot $th $va"
    "$OV" type cla; sleep 0.8
    shot "$OUT/overview-search-$th-$va-$RES.png" && pass "search screenshot $th $va"
    assert_eq "search finds the Claude window" 1 "$(field layout)"
    "$OV" hide; sleep 0.6
done
echo "e2e-spaces: $fails failure(s)"
[ "$fails" -eq 0 ]
