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
# NODESOURCE_REPO_KEY_FP: GPG fingerprint of the NodeSource apt repo signing key.
#   Verify with: curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | \
#     gpg --with-colons --import-options show-only --import | awk -F: '/^fpr:/{print $10}' | head -1
#   Last verified: 2026-06-08 against https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key
#   Update if NodeSource rotates their signing key.
# ---------------------------------------------------------------------------

TAOS_REPO="${TAOS_REPO:-https://github.com/jaylfc/tinyagentos.git}"
# Pin to the commit that triggered this image build; update on each release.
# To get the current HEAD: git -C <taos-repo> rev-parse HEAD
TAOS_COMMIT="${TAOS_COMMIT:-7c595306cacce5b0a13670544e66deb44e3c9c74}"

APP_CATALOG_REPO="${APP_CATALOG_REPO:-https://github.com/jaylfc/tinyagentos-app-catalog.git}"
# Pin to main branch HEAD at image-build time; update each release.
# To get: git ls-remote https://github.com/jaylfc/tinyagentos-app-catalog.git HEAD
APP_CATALOG_COMMIT="${APP_CATALOG_COMMIT:-HEAD}"  # Residual risk: pinned to branch; no release tags yet

# NodeSource repo GPG key fingerprint — verified 2026-06-08 against
# https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key
# and confirmed against NodeSource's official documentation at
# https://github.com/nodesource/distributions
# Update if NodeSource rotates their signing key.
NODESOURCE_REPO_KEY_FP="${NODESOURCE_REPO_KEY_FP:-6F71F525282841EEDAF851B42F59B5F99B1BE0B4}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

verify_gpg_fingerprint() {
    local keyfile="$1" expected_fp="$2" label="$3" actual_fp
    actual_fp="$(gpg --with-colons --import-options show-only --import "$keyfile" 2>/dev/null \
        | awk -F: '/^fpr:/{print $10}' | head -1)"
    actual_fp="${actual_fp//[[:space:]]/}"
    if [[ "$actual_fp" != "$expected_fp" ]]; then
        echo "ERROR: GPG fingerprint mismatch for $label: expected $expected_fp, got $actual_fp" >&2
        echo "  Refusing to import — check the key source or update the pinned fingerprint." >&2
        exit 1
    fi
    echo ">>> GPG fingerprint ok for $label (${actual_fp:0:16}…)"
}

echo ">>> TinyAgentOS: Installing system dependencies"

apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git curl wget gnupg \
    incus-client \
    docker.io docker-compose \
    avahi-daemon

# ---------------------------------------------------------------------------
# Node.js 22 LTS via NodeSource GPG-signed apt repository
# No curl-pipe-bash: we verify the key fingerprint then add the signed repo.
# ---------------------------------------------------------------------------
echo ">>> TinyAgentOS: Installing Node.js 22 LTS"
_ns_key_tmp="$(mktemp /tmp/nodesource-key.XXXXXX.asc)"
trap 'rm -f "$_ns_key_tmp"' EXIT

curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o "$_ns_key_tmp"
verify_gpg_fingerprint "$_ns_key_tmp" "$NODESOURCE_REPO_KEY_FP" "nodesource-repo.gpg.key"

mkdir -p /usr/share/keyrings
gpg --dearmor -o /usr/share/keyrings/nodesource.gpg < "$_ns_key_tmp"
chmod 644 /usr/share/keyrings/nodesource.gpg
rm -f "$_ns_key_tmp"
trap - EXIT

printf 'Types: deb\nURIs: https://deb.nodesource.com/node_22.x\nSuites: nodistro\nComponents: main\nSigned-By: /usr/share/keyrings/nodesource.gpg\n' \
    > /etc/apt/sources.list.d/nodesource.sources

apt-get update -qq
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
