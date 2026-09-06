#!/bin/bash
set -euo pipefail

# Usage: taos-framework-update <framework> <tag> <asset_url>
# Downloads the tarball, stops the service, replaces the install dir,
# writes the version marker, and restarts.

FRAMEWORK="${1:?framework name required}"
TAG="${2:?tag required}"
URL="${3:?asset url required}"

log() { echo "[$(date -u +%H:%M:%S)] $*" >&2; }

TARBALL="/tmp/${FRAMEWORK}-${TAG}.tgz"
INSTALL_DIR="/usr/lib/node_modules/${FRAMEWORK}"

log "downloading ${URL}"
curl -fsSL --retry 3 --max-time 60 "${URL}" -o "${TARBALL}"

log "stopping ${FRAMEWORK}.service"
systemctl stop "${FRAMEWORK}.service" || true

log "replacing ${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
tar -xzf "${TARBALL}" -C "${INSTALL_DIR}"
rm -f "${TARBALL}"

mkdir -p /opt/taos
echo "${TAG}" > /opt/taos/framework.version

log "starting ${FRAMEWORK}.service"
systemctl start "${FRAMEWORK}.service"

log "done"
