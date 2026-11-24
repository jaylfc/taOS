#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# install-update.sh: update a RUNNING taOS desktop from build directories. Run by the person who administers the
# machine, with sudo. It is install-desktop.sh (staged in a temporary copy, the complete sway config validated with the
# real compositor before anything is touched, timestamped backups, a rollback script, themes with their wallpapers and
# bundled fonts, fontconfig drop-in, sway snippets, shell pieces, apps) plus a report at the end that says exactly what
# each change needs: a compositor config reload, a restart of the shell pieces, or nothing.
#
#   sudo native/install-update.sh --shell-build DIR --apps-build DIR [--apps-build DIR]... \
#        [--sway /opt/taos/compositor/bin/sway] [--root DIR] [--check-only] [--reload] [--restart-pieces]
#
#   --check-only      stage and validate, install nothing (the report is not printed: nothing changed)
#   --root DIR        update a copy of the system directories instead of / (a dry run; no sudo needed)
#   --restart-pieces  AFTER a successful install, restart the shell pieces as the logged-in user
#                     (native/restart-desktop-pieces.sh: no window is closed). Without this flag nothing is restarted.
#   --reload          with --restart-pieces: also `swaymsg reload` (needed for new sway snippets). Never done otherwise.
#   -h, --help        this text
#
# Without --restart-pieces and --reload this script restarts and reloads NOTHING; it only installs and reports.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
sway=/opt/taos/compositor/bin/sway
restart=0
reload=0
pass=""
root=""
while [ $# -gt 0 ]; do
    case $1 in
        --sway) sway=${2:?}; shift 2 ;;
        --root) root=${2:?}; pass="$pass --root $2"; shift 2 ;;
        --restart-pieces) restart=1; shift ;;
        --reload) reload=1; shift ;;
        --shell-build|--apps-build|--validate-with) pass="$pass $1 ${2:?}"; shift 2 ;;
        --check-only) pass="$pass --check-only"; shift ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "install-update: unknown option $1" >&2; exit 2 ;;
    esac
done
[ "$reload" -eq 0 ] || [ "$restart" -eq 1 ] || { echo "install-update: --reload goes with --restart-pieces" >&2; exit 2; }
[ -z "$root" ] || { [ "$restart" -eq 0 ] || { echo "install-update: --restart-pieces does not combine with --root (a dry run)" >&2; exit 2; }; }

# shellcheck disable=SC2086
sh "$here/install-desktop.sh" --sway "$sway" $pass

if [ "$restart" -eq 1 ]; then
    user=${SUDO_USER:-}
    [ -n "$user" ] && [ "$user" != root ] || { echo "install-update: --restart-pieces needs to know the logged-in user (run it with sudo)" >&2; exit 1; }
    uid=$(id -u "$user")
    rt=/run/user/$uid
    sock=$(ls "$rt"/sway-ipc."$uid".*.sock 2>/dev/null | head -n 1 || true)
    [ -n "$sock" ] || { echo "install-update: no running sway session for $user; installed, not restarted" >&2; exit 1; }
    echo "restarting the shell pieces for $user (--restart-pieces)"
    flag=""
    [ "$reload" -eq 0 ] || flag="--reload"
    # shellcheck disable=SC2086
    runuser -u "$user" -- env XDG_RUNTIME_DIR="$rt" SWAYSOCK="$sock" sh "$here/restart-desktop-pieces.sh" $flag
fi
