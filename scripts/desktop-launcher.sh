#!/bin/bash
# taOS Desktop Launcher
# Installs or removes a system-wide taOS launcher (.desktop + icons) for an existing GNOME/KDE desktop.
# Usage: desktop-launcher.sh detect|install|remove
# Run with sudo for install/remove (unless TAOS_LAUNCHER_ROOT is set for testing).

set -euo pipefail

ROOT="${TAOS_LAUNCHER_ROOT:-}"

usage() {
    echo "Usage: $0 detect|install|remove" >&2
    exit 2
}

require_root_for_write() {
    if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
        echo "$0 $1 needs root: run it with sudo" >&2
        exit 1
    fi
}

cmd_detect() {
    local xsessions="$ROOT/usr/share/xsessions"
    local wayland_sessions="$ROOT/usr/share/wayland-sessions"
    local found=0

    if [ -d "$xsessions" ] && ls "$xsessions"/*.desktop >/dev/null 2>&1; then
        found=1
    fi
    if [ -d "$wayland_sessions" ] && ls "$wayland_sessions"/*.desktop >/dev/null 2>&1; then
        found=1
    fi

    if [ "$found" -eq 1 ]; then
        echo "existing desktop: yes"
        exit 0
    else
        echo "existing desktop: no"
        exit 1
    fi
}

find_browser() {
    for b in chromium-browser chromium google-chrome-stable; do
        if command -v "$b" >/dev/null 2>&1; then
            echo "$b"
            return 0
        fi
    done
    return 1
}

cmd_install() {
    require_root_for_write install

    local port="${TAOS_PORT:-6969}"
    local url="http://127.0.0.1:${port}/desktop/"

    local browser
    browser="$(find_browser)" || true

    local exec_line
    if [ -n "$browser" ]; then
        exec_line="$browser --app=$url --start-fullscreen"
    else
        exec_line="xdg-open $url"
    fi

    local desktop_dir="$ROOT/usr/share/applications"
    local desktop_file="$desktop_dir/taos.desktop"

    mkdir -p "$desktop_dir"

    cat > "$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=taOS
Comment=Open the taOS desktop
Exec=$exec_line
Icon=taos
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
    chmod 0644 "$desktop_file"

    local repo_root
    repo_root="$(cd "$(dirname "$0")/.." && pwd)"

    local icon_dir_512="$ROOT/usr/share/icons/hicolor/512x512/apps"
    local icon_dir_192="$ROOT/usr/share/icons/hicolor/192x192/apps"
    mkdir -p "$icon_dir_512" "$icon_dir_192"

    local icon_512_src="$repo_root/static/icon-512.png"
    local icon_192_src="$repo_root/static/icon-192.png"
    local icon_512_dst="$icon_dir_512/taos.png"
    local icon_192_dst="$icon_dir_192/taos.png"

    if [ -f "$icon_512_src" ]; then
        cp "$icon_512_src" "$icon_512_dst"
        chmod 0644 "$icon_512_dst"
    else
        echo "Warning: $icon_512_src not found, skipping 512px icon" >&2
    fi

    if [ -f "$icon_192_src" ]; then
        cp "$icon_192_src" "$icon_192_dst"
        chmod 0644 "$icon_192_dst"
    else
        echo "Warning: $icon_192_src not found, skipping 192px icon" >&2
    fi

    if [ -z "$ROOT" ]; then
        command -v update-desktop-database >/dev/null && update-desktop-database /usr/share/applications || true
        command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
    fi

    echo "launcher installed: $desktop_file"
}

cmd_remove() {
    require_root_for_write remove

    local desktop_file="$ROOT/usr/share/applications/taos.desktop"
    local icon_512="$ROOT/usr/share/icons/hicolor/512x512/apps/taos.png"
    local icon_192="$ROOT/usr/share/icons/hicolor/192x192/apps/taos.png"

    rm -f "$desktop_file" "$icon_512" "$icon_192"

    if [ -z "$ROOT" ]; then
        command -v update-desktop-database >/dev/null && update-desktop-database /usr/share/applications || true
        command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
    fi

    echo "launcher removed"
}

main() {
    if [ $# -ne 1 ]; then
        usage
    fi

    case "$1" in
        detect)
            cmd_detect
            ;;
        install)
            cmd_install
            ;;
        remove)
            cmd_remove
            ;;
        *)
            usage
            ;;
    esac
}

main "$@"