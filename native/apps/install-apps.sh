#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Install the native taOS apps (Dashboard, Notifications, Decisions, Settings, Agents, Messages, Store) so they launch from the dock.
#
#   install-apps.sh <prefix> [--app-dir DIR] [--runtime-app-dir DIR] [--bin-dir DIR]... [--no-icon-cache]
#
#   <prefix>      where the wrappers, .desktop files and icons go (for example /usr/local)
#   --app-dir     where the real binaries go (default /opt/taos/apps, giving DIR/bin/<name>)
#   --runtime-app-dir  the app directory as the running system sees it, written into the wrappers
#                 (default: --app-dir; differs only when installing into a staging root)
#   --bin-dir     a build directory to search for the binaries (repeatable; searched recursively)
#   --lib-dir     a directory whose lib*.so* files are copied to <app-dir>/lib (repeatable): the private copy of
#                 libzim and libxapian that Survival Studio loads through its RPATH ($ORIGIN/../lib)
#
# Installs, for each of taos-dashboard taos-notifications taos-decisions taos-settings taos-agents taos-messages taos-store:
#   <app-dir>/bin/<name>                         the real binary
#   <prefix>/bin/<name>                          a wrapper that sources /etc/taos/shell.env, then execs the binary
#   <prefix>/share/applications/my.taos.<x>.desktop
#   <prefix>/share/icons/hicolor/scalable/apps/my.taos.<x>.svg (and the fallback icon)
# Missing binaries are skipped with a warning (the exit status is 1 if none was found).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
prefix=${1:-}
[ -n "$prefix" ] || { echo "usage: $0 <prefix> [--app-dir DIR] [--bin-dir DIR]..." >&2; exit 2; }
shift
app_dir=/opt/taos/apps
runtime_dir=""
bin_dirs=""
lib_dirs=""
icon_cache=1
while [ $# -gt 0 ]; do
    case $1 in
        --app-dir) app_dir=${2:?}; shift 2 ;;
        --runtime-app-dir) runtime_dir=${2:?}; shift 2 ;;
        --bin-dir) bin_dirs="$bin_dirs $2"; shift 2 ;;
        --lib-dir) lib_dirs="$lib_dirs $2"; shift 2 ;;
        --no-icon-cache) icon_cache=0; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[ -n "$bin_dirs" ] || bin_dirs=$here
[ -n "$runtime_dir" ] || runtime_dir=$app_dir

apps="taos-dashboard:my.taos.dashboard taos-notifications:my.taos.notifications taos-decisions:my.taos.decisions taos-settings:my.taos.settings taos-agents:my.taos.agents taos-messages:my.taos.messages taos-store:my.taos.store taos-survival:my.taos.survival"
found=0
mkdir -p "$app_dir/bin" "$prefix/bin" "$prefix/share/applications" "$prefix/share/icons/hicolor/scalable/apps"
for pair in $apps; do
    name=${pair%%:*}
    id=${pair#*:}
    src=""
    for d in $bin_dirs; do
        s=$(find "$d" -type f -name "$name" -perm -u+x 2>/dev/null | head -n 1)
        [ -n "$s" ] && { src=$s; break; }
    done
    if [ -z "$src" ]; then
        echo "install-apps: $name not found under:$bin_dirs (skipped)" >&2
        continue
    fi
    found=$((found + 1))
    # copy beside, then rename: a running copy of the app keeps working (no "text file busy")
    install -m 0755 "$src" "$app_dir/bin/$name.new"
    mv -f "$app_dir/bin/$name.new" "$app_dir/bin/$name"
    sed -e "s|@NAME@|$name|g" -e "s|@APP_DIR@|$runtime_dir|g" "$here/packaging/app-wrapper.in" >"$prefix/bin/$name.tmp"
    chmod 0755 "$prefix/bin/$name.tmp"
    mv -f "$prefix/bin/$name.tmp" "$prefix/bin/$name"
    install -m 0644 "$here/desktop/$id.desktop" "$prefix/share/applications/$id.desktop"
    echo "installed $name -> $app_dir/bin/$name, wrapper $prefix/bin/$name"
done
# Command line tools of the apps: the binary and the same wrapper (so the Qt environment is set), no launcher entry.
#   taos-webapp   the web apps engine: web app instances launch as `taos-webapp launch <id>` from their .desktop files
for name in taos-webapp taos-survival-cli; do
    src=""
    for d in $bin_dirs; do
        s=$(find "$d" -type f -name "$name" -perm -u+x 2>/dev/null | head -n 1)
        [ -n "$s" ] && { src=$s; break; }
    done
    if [ -z "$src" ]; then
        echo "install-apps: $name not found under:$bin_dirs (skipped)" >&2
        continue
    fi
    install -m 0755 "$src" "$app_dir/bin/$name.new"
    mv -f "$app_dir/bin/$name.new" "$app_dir/bin/$name"
    sed -e "s|@NAME@|$name|g" -e "s|@APP_DIR@|$runtime_dir|g" "$here/packaging/app-wrapper.in" >"$prefix/bin/$name.tmp"
    chmod 0755 "$prefix/bin/$name.tmp"
    mv -f "$prefix/bin/$name.tmp" "$prefix/bin/$name"
    echo "installed $name -> $app_dir/bin/$name, wrapper $prefix/bin/$name"
done
# private shared libraries of an app (libzim, libxapian): copied beside the binaries, replaced by rename so a running
# app keeps the copy it has mapped
for d in $lib_dirs; do
    mkdir -p "$app_dir/lib"
    for so in "$d"/lib*.so*; do
        [ -e "$so" ] || continue
        cp -a "$so" "$app_dir/lib/$(basename "$so").new" && mv -f "$app_dir/lib/$(basename "$so").new" "$app_dir/lib/$(basename "$so")"
        echo "installed library $(basename "$so") -> $app_dir/lib"
    done
done
for svg in "$here"/icons/hicolor/scalable/apps/*.svg; do
    install -m 0644 "$svg" "$prefix/share/icons/hicolor/scalable/apps/$(basename "$svg")"
done
# bitmap sizes of icons that have them (Survival Studio): <prefix>/share/icons/hicolor/<N>x<N>/apps/
for png in "$here"/icons/hicolor/*/apps/*.png; do
    [ -e "$png" ] || continue
    sz=$(basename "$(dirname "$(dirname "$png")")")
    mkdir -p "$prefix/share/icons/hicolor/$sz/apps"
    install -m 0644 "$png" "$prefix/share/icons/hicolor/$sz/apps/$(basename "$png")"
done
if [ "$icon_cache" -eq 1 ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$prefix/share/icons/hicolor" 2>/dev/null || true
fi
[ "$found" -gt 0 ]
