#!/usr/bin/env bash
# taOS first-boot script — runs the installer with TAOS_DESKTOP=local
# Logs to /var/log/taos-firstboot.log

set -euo pipefail

# Allow test override of root paths via TAOS_FIRSTBOOT_ROOT
TAOS_ROOT="${TAOS_FIRSTBOOT_ROOT:-/}"
LOG_FILE="${TAOS_ROOT}/var/log/taos-firstboot.log"
DONE_FILE="${TAOS_ROOT}/var/lib/taos/firstboot.done"
INSTALL_SCRIPT="${TAOS_ROOT}/usr/local/share/taos/install.sh"
SYSTEMCTL_CMD="systemctl"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

die() {
    log "ERROR: $*"
    exit 1
}

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

log "=== taOS first-boot started ==="

# Export TAOS_DESKTOP=local for the installer
export TAOS_DESKTOP=local

# Verify install script exists
if [[ ! -f "$INSTALL_SCRIPT" ]]; then
    die "Install script not found at $INSTALL_SCRIPT"
fi

log "Running taOS installer (TAOS_DESKTOP=local)..."

# The install.sh and install-server.sh scripts both require root (they check EUID and use sudo extensively).
# Since this runs as a systemd oneshot service (as root), we invoke directly as root.
if ! bash "$INSTALL_SCRIPT" 2>&1 | tee -a "$LOG_FILE"; then
    die "Installer failed"
fi

log "Installer completed successfully"

# Mark first-boot as done
mkdir -p "$(dirname "$DONE_FILE")"
touch "$DONE_FILE"
log "Created $DONE_FILE"

# Disable the first-boot service so it doesn't run again
if $SYSTEMCTL_CMD disable taos-firstboot.service 2>&1 | tee -a "$LOG_FILE"; then
    log "Disabled taos-firstboot.service"
else
    log "WARNING: Failed to disable taos-firstboot.service"
fi

log "=== taOS first-boot completed ==="
exit 0