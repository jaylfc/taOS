#!/usr/bin/env bash
# taOS llama.cpp service uninstaller — tears down the systemd unit or OpenRC
# init script installed by scripts/install-llama-cpp.sh.
#
# Run as: bash scripts/uninstall-llama-cpp.sh [project_dir]
#
# Best effort: a missing unit / init script or an init system that isn't
# present never fails the uninstall.
set -uo pipefail

log()  { printf '[llama-cpp-uninstall] %s\n' "$*"; }
warn() { printf '[llama-cpp-uninstall] WARNING: %s\n' "$*" >&2; }

PROJECT_DIR="${1:-$(pwd)}"
SYSTEMD_DIR="${TAOS_SYSTEMD_DIR:-/etc/systemd/system}"
INITD_DIR="${TAOS_INITD_DIR:-/etc/init.d}"
UNIT_FILE="$SYSTEMD_DIR/llama-cpp.service"
INITD_FILE="$INITD_DIR/llama-cpp"

init_system="${TAOS_INIT_SYSTEM:-}"
if [[ -z "$init_system" ]]; then
    if command -v systemctl >/dev/null 2>&1; then
        init_system="systemd"
    elif command -v rc-service >/dev/null 2>&1; then
        init_system="openrc"
    else
        init_system="none"
    fi
fi

case "$init_system" in
    systemd)
        if [[ -f "$UNIT_FILE" ]] || systemctl is-enabled llama-cpp.service >/dev/null 2>&1; then
            log "stopping + disabling llama-cpp.service"
            systemctl disable --now llama-cpp.service 2>/dev/null || true
            rm -f "$UNIT_FILE"
            systemctl daemon-reload 2>/dev/null || true
            log "llama-cpp.service torn down"
        else
            log "llama-cpp.service not installed; nothing to tear down"
        fi
        ;;
    openrc)
        if [[ -f "$INITD_FILE" ]] || rc-service llama-cpp status >/dev/null 2>&1; then
            log "stopping + removing llama-cpp init script"
            rc-service llama-cpp stop 2>/dev/null || true
            rc-update del llama-cpp default 2>/dev/null || true
            rm -f "$INITD_FILE"
            log "llama-cpp init script torn down"
        else
            log "llama-cpp init script not installed; nothing to tear down"
        fi
        ;;
    *)
        log "no supported init system detected; nothing to tear down"
        ;;
esac
