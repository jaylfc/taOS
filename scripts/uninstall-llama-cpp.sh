#!/usr/bin/env bash
# taOS llama.cpp service uninstaller — tears down the systemd unit, OpenRC
# init script, or launchd plist installed by scripts/install-llama-cpp.sh.
#
# Run as: bash scripts/uninstall-llama-cpp.sh [project_dir]
#
# Best effort: a missing unit / init script / plist or an init system that
# isn't present never fails the uninstall.
set -uo pipefail

log()  { printf '[llama-cpp-uninstall] %s\n' "$*"; }
warn() { printf '[llama-cpp-uninstall] WARNING: %s\n' "$*" >&2; }

PROJECT_DIR="${1:-$(pwd)}"
SYSTEMD_DIR="${TAOS_SYSTEMD_DIR:-/etc/systemd/system}"
INITD_DIR="${TAOS_INITD_DIR:-/etc/init.d}"
UNIT_FILE="$SYSTEMD_DIR/llama-cpp.service"
INITD_FILE="$INITD_DIR/llama-cpp"
LAUNCHAGENTS_DIR="${TAOS_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_FILE="$LAUNCHAGENTS_DIR/com.taos.llama-cpp.plist"

# SUDO prefix: use TAOS_SUDO if set (even if empty), else "sudo -n"; empty when root
if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
else
    SUDO="${TAOS_SUDO-sudo -n}"
fi

init_system="${TAOS_INIT_SYSTEM:-}"
if [[ -z "$init_system" ]]; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
        init_system="launchd"
    elif command -v systemctl >/dev/null 2>&1; then
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
            $SUDO systemctl disable --now llama-cpp.service 2>/dev/null || true
            if ! $SUDO rm -f "$UNIT_FILE"; then
                warn "failed to remove $UNIT_FILE"
                exit 1
            fi
            if [[ -f "$UNIT_FILE" ]]; then
                warn "$UNIT_FILE still exists after removal attempt"
                exit 1
            fi
            $SUDO systemctl daemon-reload 2>/dev/null || true
            log "llama-cpp.service torn down"
        else
            log "llama-cpp.service not installed; nothing to tear down"
        fi
        ;;
    openrc)
        if [[ -f "$INITD_FILE" ]] || rc-service llama-cpp status >/dev/null 2>&1; then
            log "stopping + removing llama-cpp init script"
            $SUDO rc-service llama-cpp stop 2>/dev/null || true
            $SUDO rc-update del llama-cpp default 2>/dev/null || true
            if ! $SUDO rm -f "$INITD_FILE"; then
                warn "failed to remove $INITD_FILE"
                exit 1
            fi
            if [[ -f "$INITD_FILE" ]]; then
                warn "$INITD_FILE still exists after removal attempt"
                exit 1
            fi
            log "llama-cpp init script torn down"
        else
            log "llama-cpp init script not installed; nothing to tear down"
        fi
        ;;
    launchd)
        if [[ -f "$PLIST_FILE" ]]; then
            launchctl bootout "gui/$(id -u)/com.taos.llama-cpp" 2>/dev/null || true
            rm -f "$PLIST_FILE"
            log "com.taos.llama-cpp torn down"
        fi
        # If plist absent, log nothing and exit 0
        ;;
    *)
        log "no supported init system detected; nothing to tear down"
        ;;
esac