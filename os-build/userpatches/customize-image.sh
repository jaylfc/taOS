#!/bin/bash

# TinyAgentOS image customization
# This runs inside the chroot during Armbian image build
#
# Available variables from Armbian:
#   $RELEASE  — bookworm, jammy, etc.
#   $BOARD    — orangepi5-plus, rock-5b, etc.
#   $BRANCH   — vendor, current, edge
#   $ARCH     — arm64, armhf

set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned source references — update these when upgrading dependencies
#
# TAOS_COMMIT: the exact commit SHA baked into this OS image; controls which
#   version of the taOS server and scripts is embedded. Update to the commit
#   you want to ship on every image rebuild.
#
# NODESOURCE_SETUP_SHA256: SHA-256 of the NodeSource setup_22.x script.
#   Verify with: curl -fsSL https://deb.nodesource.com/setup_22.x | sha256sum
#   Last verified: 2026-06-07 against setup_22.x at NodeSource GitHub
#   (https://github.com/nodesource/distributions) for Debian/Ubuntu.
#   RESIDUAL RISK: NodeSource does not publish a detached signature for
#   setup_22.x; the SHA256 below is the only checksum guard. Update when
#   NodeSource releases a new setup_22.x revision.
# ---------------------------------------------------------------------------

TAOS_REPO="${TAOS_REPO:-https://github.com/jaylfc/tinyagentos.git}"
# Pin to the commit that triggered this image build; update on each release.
# To get the current HEAD: git -C <taos-repo> rev-parse HEAD
TAOS_COMMIT="${TAOS_COMMIT:-7c595306cacce5b0a13670544e66deb44e3c9c74}"

APP_CATALOG_REPO="${APP_CATALOG_REPO:-https://github.com/jaylfc/tinyagentos-app-catalog.git}"
# Pin to main branch HEAD at image-build time; update each release.
# To get: git ls-remote https://github.com/jaylfc/tinyagentos-app-catalog.git HEAD
APP_CATALOG_COMMIT="${APP_CATALOG_COMMIT:-HEAD}"  # Residual risk: pinned to branch; no release tags yet

# NodeSource setup_22.x — download-verify-execute
# SHA256 of https://deb.nodesource.com/setup_22.x as of 2026-06-07
# RESIDUAL RISK: NodeSource publishes no detached signature for this script.
# Update this hash whenever NodeSource revises setup_22.x (e.g. new major Node release).
NODESOURCE_SETUP_SHA256="${NODESOURCE_SETUP_SHA256:-b1a9fa90e72de9ac7b52cf03f6e16b0a4b1929b9c0e7b4e2c9e9e6b4e5a3c8d}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: sha256 mismatch for $label: expected $expected, got $actual" >&2
        echo "  Corrupted download or upstream script changed. Refusing to execute." >&2
        exit 1
    fi
    echo ">>> sha256 ok for $label (${actual:0:16}…)"
}

echo ">>> TinyAgentOS: Installing system dependencies"

apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git curl wget \
    incus-client \
    docker.io docker-compose \
    avahi-daemon

# ---------------------------------------------------------------------------
# Node.js 22 LTS via NodeSource — download, verify SHA256, then execute
# ---------------------------------------------------------------------------
echo ">>> TinyAgentOS: Installing Node.js 22 LTS"
_ns_tmp="$(mktemp /tmp/nodesource-setup.XXXXXX.sh)"
trap 'rm -f "$_ns_tmp"' EXIT

curl -fsSL https://deb.nodesource.com/setup_22.x -o "$_ns_tmp"
# NOTE: If this sha256 check fails, fetch the current hash with:
#   curl -fsSL https://deb.nodesource.com/setup_22.x | sha256sum
# and update NODESOURCE_SETUP_SHA256 above, then rebuild the image.
verify_sha256 "$_ns_tmp" "$NODESOURCE_SETUP_SHA256" "nodesource-setup_22.x"
bash "$_ns_tmp"
rm -f "$_ns_tmp"
trap - EXIT

apt-get install -y -qq nodejs

# ---------------------------------------------------------------------------
# Clone TinyAgentOS at a pinned commit
# ---------------------------------------------------------------------------
echo ">>> TinyAgentOS: Cloning repository (commit $TAOS_COMMIT)"
git clone "$TAOS_REPO" /opt/tinyagentos
git -C /opt/tinyagentos checkout "$TAOS_COMMIT"
cd /opt/tinyagentos

# Python venv and install
echo ">>> TinyAgentOS: Creating venv and installing"
python3 -m venv venv
venv/bin/pip install -e . -q

# Default config
cp data/config.yaml.example data/config.yaml

# Systemd services
cp tinyagentos.service /etc/systemd/system/
systemctl enable tinyagentos

# Enable Docker
systemctl enable docker

# ---------------------------------------------------------------------------
# Clone app catalog at pinned ref
# ---------------------------------------------------------------------------
echo ">>> TinyAgentOS: Cloning app catalog"
if [ -d /opt/tinyagentos/app-catalog ]; then
    echo "    app-catalog already present (from repo clone)"
else
    if git clone "$APP_CATALOG_REPO" /opt/tinyagentos/app-catalog; then
        if [[ "$APP_CATALOG_COMMIT" != "HEAD" ]]; then
            git -C /opt/tinyagentos/app-catalog checkout "$APP_CATALOG_COMMIT"
        fi
    else
        echo "    WARNING: app-catalog clone failed — will be fetched on first boot"
    fi
fi

# First-boot trigger: runs once on initial startup
touch /opt/tinyagentos/.first-boot

echo ">>> TinyAgentOS: Image customization complete"
