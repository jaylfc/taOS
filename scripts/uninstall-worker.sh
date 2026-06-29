#!/usr/bin/env bash
# TinyAgentOS worker uninstaller - Linux (incus LXC) + macOS.
#
# Reverses install-worker.sh. On Linux the worker runs as the `taos-worker`
# incus LXC behind an nftables DNAT port-forward, so a real uninstall must tear
# those down, not just remove a host unit. Every step is guarded and idempotent:
# a missing component is skipped and a re-run is safe.
#
# By default this removes the worker container and the port-forwards but KEEPS
# the `taos-worker-pool` storage (which holds the worker's data). Pass --purge to
# also delete the storage pool (irreversible data loss).
#
# Destructive steps surface their own errors and flip a failure flag rather than
# swallowing failures, so a half-torn-down host is reported, not hidden.
set -euo pipefail

INSTALL_DIR="${TAOS_INSTALL_DIR:-$HOME/.local/share/tinyagentos-worker}"
NFT_CONF="${TAOS_NFT_CONF:-/etc/nftables.conf}"
os_name="$(uname -s)"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1
FAILED=0

log() { printf '\033[1;34m[worker-uninstall]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[worker-uninstall]\033[0m WARNING: %s\n' "$*"; FAILED=1; }

case "$os_name" in
    Linux)
        # Legacy flat-install path: a host user systemd unit (pre-LXC). "Not
        # found" is the norm here, so these stay best-effort and quiet.
        systemctl --user stop tinyagentos-worker 2>/dev/null || true
        systemctl --user disable tinyagentos-worker 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/tinyagentos-worker.service"
        systemctl --user daemon-reload 2>/dev/null || true

        # Two-phase incus-LXC teardown (the current architecture). Each step is
        # guarded by an existence check, so reaching the delete means the
        # resource really exists: a failure now is genuine, so surface it.
        if command -v incus >/dev/null 2>&1; then
            if sudo incus list --format=csv -c n 2>/dev/null | grep -q '^taos-worker$'; then
                log "deleting worker LXC 'taos-worker'"
                if ! sudo incus delete taos-worker --force </dev/null; then
                    warn "failed to delete LXC 'taos-worker'; it may still exist"
                fi
            fi
            if sudo incus storage list --format=csv 2>/dev/null | awk -F',' '{print $1}' | grep -q '^taos-worker-pool$'; then
                if [[ "$PURGE" == "1" ]]; then
                    log "purging storage pool 'taos-worker-pool' (--purge)"
                    if ! sudo incus storage delete taos-worker-pool </dev/null; then
                        warn "failed to delete storage pool 'taos-worker-pool'; it may still exist"
                    fi
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
                log "removing live nftables table 'ip taos' (worker port-forwards)"
                if ! sudo nft delete table ip taos; then
                    warn "failed to delete live nftables table 'ip taos'"
                fi
            fi
            # install-worker.sh persists the whole ruleset to $NFT_CONF, so the
            # taos table is saved there too; without this the forward returns on
            # the next reboot / nftables reload. Strip ONLY the 'table ip taos'
            # block from the saved file, leaving every other table intact.
            if [[ -f "$NFT_CONF" ]] && sudo grep -qE '^table ip taos[ {]' "$NFT_CONF" 2>/dev/null; then
                log "removing persisted 'table ip taos' from $NFT_CONF"
                # Write the cleaned ruleset to a sibling temp, then atomically
                # rename it into place: a crash mid-write leaves $NFT_CONF intact
                # (either the old file or the fully-written new one, never a
                # half-written ruleset). sudo tee writes the root-owned temp.
                nft_tmp="${NFT_CONF}.taos-uninstall.$$"
                if sudo awk '
                    skip==0 && /^table ip taos \{/ {skip=1; depth=1; next}
                    skip==1 {
                        depth += gsub(/\{/,"{");
                        depth -= gsub(/\}/,"}");
                        if (depth<=0) skip=0;
                        next
                    }
                    {print}
                ' "$NFT_CONF" | sudo tee "$nft_tmp" >/dev/null && sudo mv "$nft_tmp" "$NFT_CONF"; then
                    :
                else
                    warn "failed to rewrite $NFT_CONF; 'table ip taos' may persist across reboot"
                    sudo rm -f "$nft_tmp" 2>/dev/null || true
                fi
            fi
        fi
        [[ "$FAILED" -eq 0 ]] && log "worker LXC + port-forwards removed"
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

if [[ "$FAILED" -eq 1 ]]; then
    log "uninstall finished WITH ERRORS (see warnings above); host may be partly torn down"
    exit 1
fi
log "uninstall complete"
