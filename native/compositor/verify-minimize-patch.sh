#!/bin/sh
# Reproduces the crash of stock swayfx 0.6.1 (scratchpad_minimize enabled by a reload while windows are mapped, then the
# windows are destroyed) against a given sway binary. Exit 0: the compositor survived (patched); 1: it died (stock).
#   verify-minimize-patch.sh <sway-binary> <taos-window-dummy>
# Headless only, a private XDG_RUNTIME_DIR, never the session's socket. Set the render environment the machine needs,
# for example WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 and __EGL_VENDOR_LIBRARY_FILENAMES=<mesa vendor json>.
sway=$1; dummy=$2
[ -x "$sway" ] && [ -x "$dummy" ] || { echo "usage: $0 <sway> <taos-window-dummy>" >&2; exit 2; }
rt=$(mktemp -d /tmp/minimize-check.XXXXXX); chmod 700 "$rt"
pids=""
cleanup() { [ -n "$pids" ] && kill $pids 2>/dev/null; kill "$swaypid" 2>/dev/null; sleep 1; kill -9 "$swaypid" 2>/dev/null; rm -rf "$rt"; }
trap cleanup EXIT
unset WAYLAND_DISPLAY SWAYSOCK I3SOCK
mkdir -p "$rt/inc"
printf 'default_border pixel 1\ninclude %s/inc/*.conf\n' "$rt" > "$rt/config"
export XDG_RUNTIME_DIR=$rt WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1
"$sway" -c "$rt/config" >"$rt/sway.log" 2>&1 &
swaypid=$!
i=0; while [ $i -lt 50 ] && ! ls "$rt"/sway-ipc.*.sock >/dev/null 2>&1; do sleep 0.2; i=$((i+1)); done
export SWAYSOCK=$(ls "$rt"/sway-ipc.*.sock | head -1) WAYLAND_DISPLAY=$rt/wayland-1
kill -0 "$swaypid" 2>/dev/null || { echo "sway did not start:"; tail -5 "$rt/sway.log"; exit 2; }
for n in one two three; do QT_QPA_PLATFORM=wayland "$dummy" taos.$n $n >/dev/null 2>&1 & pids="$pids $!"; sleep 1.5; done
echo "scratchpad_minimize yes" > "$rt/inc/minimize.conf"
"$(dirname "$sway")/swaymsg" reload >/dev/null 2>&1 || swaymsg reload >/dev/null 2>&1
sleep 2
kill $pids 2>/dev/null; pids=""
sleep 2
if kill -0 "$swaypid" 2>/dev/null; then echo "ALIVE: the compositor survived enabling scratchpad_minimize by reload and closing the windows"; exit 0; fi
echo "DEAD: the compositor crashed (stock swayfx 0.6.1 behaviour)"; exit 1
