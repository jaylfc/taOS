#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Installs the taOS mesh engine's user unit (and, with --with-firmware, the firmware manager's units) for the person
# running it. Nothing is enabled or started unless you say so.
#
#   install-user-units.sh --mesh-bin PATH [--firmware-bin PATH --with-firmware] [--enable-mesh] [--enable-firmware-refresh]
#                         [--no-systemctl]
#
#   --mesh-bin PATH         the taos-mesh binary; copied to ~/.local/bin/taos-mesh (the unit runs it from there)
#   --firmware-bin PATH     the taos-firmware binary; copied to ~/.local/bin/taos-firmware
#   --with-firmware         also install taos-firmware.service, taos-firmware-refresh.service and .timer (not enabled)
#   --enable-mesh           enable and start taos-mesh (receive-only: see the unit for how to allow sending)
#   --enable-firmware-refresh   enable the daily library refresh timer
#   --no-systemctl          copy files only (used by the tests)
# HOME, XDG_CONFIG_HOME and XDG_DATA_HOME are honoured.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
fwdir="$here/../../firmware/systemd"
mesh_bin=""; fw_bin=""; with_fw=0; en_mesh=0; en_fw=0; sctl=1
while [ $# -gt 0 ]; do
    case $1 in
        --mesh-bin) mesh_bin=${2:?}; shift 2 ;;
        --firmware-bin) fw_bin=${2:?}; shift 2 ;;
        --with-firmware) with_fw=1; shift ;;
        --enable-mesh) en_mesh=1; shift ;;
        --enable-firmware-refresh) en_fw=1; shift ;;
        --no-systemctl) sctl=0; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[ -n "$mesh_bin" ] || { echo "usage: $0 --mesh-bin PATH [options]" >&2; exit 2; }
[ -x "$mesh_bin" ] || { echo "not an executable: $mesh_bin" >&2; exit 2; }
bin="$HOME/.local/bin"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$bin" "$units" "${XDG_DATA_HOME:-$HOME/.local/share}/taos-mesh"
# copy beside, then rename: a running copy keeps working
install_bin() { cp "$1" "$bin/.$2.new" && chmod 755 "$bin/.$2.new" && mv -f "$bin/.$2.new" "$bin/$2"; }
install_bin "$mesh_bin" taos-mesh
cp "$here/taos-mesh.service" "$units/taos-mesh.service"
if [ "$with_fw" = 1 ]; then
    [ -n "$fw_bin" ] && [ -x "$fw_bin" ] || { echo "--with-firmware needs --firmware-bin PATH" >&2; exit 2; }
    install_bin "$fw_bin" taos-firmware
    for u in taos-firmware.service taos-firmware-refresh.service taos-firmware-refresh.timer; do
        sed "s#/opt/taos/bin/taos-firmware#$bin/taos-firmware#" "$fwdir/$u" > "$units/$u"
    done
fi
if [ "$sctl" = 1 ]; then
    systemctl --user daemon-reload
    [ "$en_mesh" = 1 ] && systemctl --user enable --now taos-mesh.service
    [ "$en_fw" = 1 ] && systemctl --user enable --now taos-firmware-refresh.timer
fi
echo "installed to $units (not enabled unless asked)"
