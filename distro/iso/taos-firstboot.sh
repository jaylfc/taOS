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

# Detect whether the installer requires root or can run as a sudo-capable user.
# We read the embedded install.sh (and install-server.sh if present) to check
# for an EUID/root guard. If the script exits when not root, we must run as root.
# If it refuses root (rare), we run as the first normal user (UID 1000).
# install.sh (line 27-30): checks EUID and exits 1 if not root -> requires root.
# install-server.sh: uses sudo throughout, intended to be run with sudo.
run_as_root=1
if grep -q 'EUID.*-ne.*0' "$INSTALL_SCRIPT" 2>/dev/null; then
    run_as_root=1
elif grep -q 'refuse.*root\|not.*run.*as.*root' "$INSTALL_SCRIPT" 2>/dev/null; then
    run_as_root=0
fi

log "Running taOS installer (TAOS_DESKTOP=local)..."

if [[ $run_as_root -eq 1 ]]; then
    # The installer requires root; we are already root in this systemd oneshot
    if ! bash "$INSTALL_SCRIPT" 2>&1 | tee -a "$LOG_FILE"; then
        die "Installer failed"
    fi
else
    # Installer refuses root; run as first normal user (UID 1000)
    first_user=$(getent passwd 1000 | cut -d: -f1)
    if [[ -z "$first_user" ]]; then
        die "No user with UID 1000 found"
    fi
    log "Installer refuses root; running as user: $first_user"
    if ! runuser -u "$first_user" -- env TAOS_DESKTOP=local bash "$INSTALL_SCRIPT" 2>&1 | tee -a "$LOG_FILE"; then
        die "Installer failed"
    fi
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
