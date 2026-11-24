#!/bin/sh
# restart-desktop-pieces.sh: pick up what install-desktop.sh installed, for the logged-in user, in the running
# session. Run it from a terminal inside the desktop session (it needs the session's SWAYSOCK).
#
#   restart-desktop-pieces.sh [--reload]
#
# - restarts the shell components (bar, overview, dock, launcher) through `taos-shell-session stop` and `start`;
#   the components come from TAOS_SHELL_COMPONENTS in /etc/taos/shell.env
# - starts `taos-window mode-daemon` and `taos-window-session-hook watch` when they are not running (never restarted)
# - never closes an app window and never kills anything by name or pattern: the session script stops
#   components by the pid files it wrote, and "is it running" is answered by comparing whole command lines
# - `swaymsg reload` runs ONLY with --reload (the new sway snippets and key bindings need a reload to apply)
set -u

reload=0
for a in "$@"; do
    case $a in
        --reload) reload=1 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "restart-desktop-pieces: unknown option $a" >&2; exit 2 ;;
    esac
done

die() { echo "restart-desktop-pieces: $*" >&2; exit 1; }
[ "$(id -u)" -ne 0 ] || die "run this as the logged-in user, not root"
[ -n "${SWAYSOCK:-}" ] && [ -S "$SWAYSOCK" ] || die "no sway session here (SWAYSOCK is not set): run it from a terminal inside the desktop"

SESSION=$(command -v taos-shell-session) || die "taos-shell-session is not installed"

# True when this user has a process whose whole command line is exactly "$1" (words separated by spaces),
# with or without a directory on the first word. Compares whole lines: no pattern can match this script.
running() {
    want=$1
    me=$(id -u)
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        [ "$pid" != "$$" ] || continue
        [ -r "$d/cmdline" ] || continue
        [ "$(stat -c %u "$d" 2>/dev/null)" = "$me" ] || continue
        line=$(tr '\0' ' ' <"$d/cmdline" 2>/dev/null)
        line=${line% }
        first=${line%% *}
        rest=${line#"$first"}
        [ "${first##*/}$rest" = "$want" ] && return 0
    done
    return 1
}

echo "stopping the shell components"
"$SESSION" stop
echo "starting them"
# a new session of its own: the supervisors must outlive this terminal
if command -v setsid >/dev/null 2>&1; then
    setsid -f "$SESSION" start >/dev/null 2>&1 </dev/null
else
    nohup "$SESSION" start >/dev/null 2>&1 </dev/null &
fi
sleep 2
"$SESSION" status || true

if running "taos-window mode-daemon"; then
    echo "taos-window mode-daemon: already running (left alone)"
else
    if command -v taos-window >/dev/null 2>&1; then
        if command -v setsid >/dev/null 2>&1; then
            setsid -f taos-window mode-daemon >/dev/null 2>&1 </dev/null
        else
            nohup taos-window mode-daemon >/dev/null 2>&1 </dev/null &
        fi
        echo "taos-window mode-daemon: started"
    else
        echo "taos-window is not installed: mode-daemon not started" >&2
    fi
fi

# the session hook publishes the appearance (dark, light, auto) to GTK and Chromium through gsettings, and the window
# button side to GTK and foot: it is started once and never restarted
if running "taos-window-session-hook watch"; then
    echo "taos-window-session-hook: already running (left alone)"
elif command -v taos-window-session-hook >/dev/null 2>&1; then
    if command -v setsid >/dev/null 2>&1; then
        setsid -f taos-window-session-hook watch >/dev/null 2>&1 </dev/null
    else
        nohup taos-window-session-hook watch >/dev/null 2>&1 </dev/null &
    fi
    echo "taos-window-session-hook: started"
else
    echo "taos-window-session-hook is not installed: not started" >&2
fi

if [ "$reload" -eq 1 ]; then
    echo "reloading sway"
    swaymsg reload
else
    echo "sway was not reloaded: the new key bindings apply after 'swaymsg reload' (re-run with --reload) or the next login"
fi
