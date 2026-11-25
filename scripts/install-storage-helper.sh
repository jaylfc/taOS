#!/usr/bin/env bash
# Install the root storage helper (taos-storage-helper) and its sudoers entry.
#
# Run as root, once, by the machine owner (or by install-server.sh, which calls it):
#
#     sudo bash scripts/install-storage-helper.sh --user taos
#
# What it installs, all root-owned and not writable by the service account:
#   /usr/local/sbin/taos-storage-helper             the helper (the only code that formats a disk)
#   /usr/local/lib/taos-storage/storage_classifier.py   the safety classifier the helper imports
#   /etc/sudoers.d/taos-storage                     lets ONLY the named account run ONLY the helper
#   /etc/taos/keys (0700), /etc/taos/storage/areas, /srv/taos/storage
#
# --user NAME   the controller's service account (required)
# --stage DIR   write everything under DIR instead of /, without needing root
#               (used by the tests; the sudoers file is still checked with visudo)
set -euo pipefail
PATH="$PATH:/usr/sbin:/sbin"

die() { echo "install-storage-helper: $*" >&2; exit 1; }
log() { echo "install-storage-helper: $*"; }

SVC_USER="" STAGE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) SVC_USER="${2:?}"; shift 2 ;;
        --stage) STAGE="${2:?}"; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done

[[ -n "$SVC_USER" ]] || die "pass --user NAME (the controller's service account)"
[[ "$SVC_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "bad account name"
[[ "$SVC_USER" != "root" ]] || die "the service account must not be root"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="${STAGE%/}"
OWN=(-o root -g root)
if [[ -z "$STAGE" ]]; then
    [[ "$(id -u)" == "0" ]] || die "run as root: sudo bash $0 --user $SVC_USER"
    id -u "$SVC_USER" >/dev/null 2>&1 || die "no such account: $SVC_USER"
else
    OWN=()
    mkdir -p "$ROOT"
fi
command -v visudo >/dev/null 2>&1 || die "visudo is required to check the sudoers entry"

for f in scripts/taos-storage-helper tinyagentos/storage_classifier.py packaging/taos-storage.sudoers; do
    [[ -f "$SRC/$f" ]] || die "missing $f in $SRC"
done

# Render and validate the sudoers entry BEFORE anything is installed.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
sed "s/@TAOS_USER@/$SVC_USER/g" "$SRC/packaging/taos-storage.sudoers" > "$TMP"
visudo -c -f "$TMP" >/dev/null || die "the rendered sudoers entry failed visudo -c; nothing was installed"

# Standard system directories are created if missing but never re-moded
# (/etc/sudoers.d in particular is not 0755 on every system).
mkdir -p "$ROOT/usr/local/sbin" "$ROOT/etc/sudoers.d" "$ROOT/etc/taos/storage" "$ROOT/srv/taos"
install -d "${OWN[@]}" -m 0755 "$ROOT/usr/local/lib/taos-storage" "$ROOT/etc/taos/storage/areas" "$ROOT/srv/taos/storage"
install -d "${OWN[@]}" -m 0700 "$ROOT/etc/taos/keys"
install "${OWN[@]}" -m 0644 "$SRC/tinyagentos/storage_classifier.py" "$ROOT/usr/local/lib/taos-storage/storage_classifier.py"
install "${OWN[@]}" -m 0755 "$SRC/scripts/taos-storage-helper" "$ROOT/usr/local/sbin/taos-storage-helper"
install "${OWN[@]}" -m 0440 "$TMP" "$ROOT/etc/sudoers.d/taos-storage"
log "installed; $SVC_USER may run /usr/local/sbin/taos-storage-helper through sudo"
