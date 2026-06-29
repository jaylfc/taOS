#!/usr/bin/env bash
# TinyAgentOS worker uninstaller - Linux (incus LXC) + macOS.
#
# Reverses install-worker.sh. On Linux the worker runs as the `taos-worker`
# incus LXC behind an nftables DNAT port-forward, so a real uninstall must tear
# those down, not just remove a host unit. Every step is guarded and idempotent:
# a missing component or a re-run never errors under `set -e`.
#
# By default this removes the worker container and the port-forwards but KEEPS
# the `taos-worker-pool` storage (which holds the worker's data). Pass --purge to
# also delete the storage pool (irreversible data loss).
set -euo pipefail

INSTALL_DIR="${TAOS_INSTALL_DIR:-$HOME/.local/share/tinyagentos-worker}"
os_name="$(uname -s)"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

log() { printf '\033[1;34m[worker-uninstall]\033[0m %s\n' "$*"; }

case "$os_name" in
    Linux)
        # Legacy flat-install path: a host user systemd unit (pre-LXC).
        # Harmless if absent; kept so older installs still uninstall cleanly.
        systemctl --user stop tinyagentos-worker 2>/dev/null || true
        systemctl --user disable tinyagentos-worker 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/tinyagentos-worker.service"
        systemctl --user daemon-reload 2>/dev/null || true

        # Two-phase incus-LXC teardown (the current architecture).
        if command -v incus >/dev/null 2>&1; then
            if sudo incus list --format=csv -c n 2>/dev/null | grep -q '^taos-worker$'; then
                log "deleting worker LXC 'taos-worker'"
                sudo incus delete taos-worker --force </dev/null 2>/dev/null || true
            fi
            if sudo incus storage list --format=csv 2>/dev/null | awk -F',' '{print $1}' | grep -q '^taos-worker-pool$'; then
                if [[ "$PURGE" == "1" ]]; then
                    log "purging storage pool 'taos-worker-pool' (--purge)"
                    sudo incus storage delete taos-worker-pool </dev/null 2>/dev/null || true
                else
                    log "keeping storage pool 'taos-worker-pool' (re-run with --purge to delete worker data)"
                fi
            fi
        fi

        # Remove ONLY the taos nftables table, which holds the worker DNAT
        # port-forwards (:8443, :21434). Deleting the table leaves every other
        # host firewall rule untouched (never rewrites the whole ruleset).
        if command -v nft >/dev/null 2>&1; then
            if sudo nft list table ip taos >/dev/null 2>&1; then
                log "removing nftables table 'ip taos' (worker port-forwards)"
                sudo nft delete table ip taos 2>/dev/null || true
            fi
        fi
        log "worker LXC + port-forwards removed"
        ;;
    Darwin)
        launchctl unload "$HOME/Library/LaunchAgents/com.tinyagentos.worker.plist" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/com.tinyagentos.worker.plist"
        log "removed launchd agent"
        ;;
esac

if [[ -d "$INSTALL_DIR" ]]; then
    log "removing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
fi

log "uninstall complete"
