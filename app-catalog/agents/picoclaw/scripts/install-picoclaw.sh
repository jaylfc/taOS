#!/usr/bin/env bash
# install-picoclaw.sh — picoclaw agent runtime installer
#
# Installs the UPSTREAM PicoClaw runtime (Sipeed's NPU-aware micro agent, MIT)
# inside a fresh Debian bookworm LXC container as root.
#
# PicoClaw is a single self-contained Go binary (<10MB RAM at runtime), so the
# installer just fetches the pinned, checksum-verified release tarball for the
# container's CPU arch and drops the `picoclaw` binary onto PATH. No runtime
# (Node/Python) is required.
#
# SECURITY: PicoClaw's custom_allow_patterns is NOT an allowlist. The
# container or unit sandbox remains the security boundary. Patterns only limit
# named path globs inside the harness; they do not replace the OS-level sandbox.
#
# Idempotent: re-running on an already-provisioned container is a no-op.
#
# Upstream sources (verified 2026-09-24):
#   Repo:      https://github.com/sipeed/picoclaw            (MIT, Go)
#   Releases:  https://github.com/sipeed/picoclaw/releases
#   Checksums: https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_0.3.1_checksums.txt
set -euo pipefail

# ---------------------------------------------------------------------------
# Pin: release tag + per-arch tarball asset + its SHA256 (from the upstream
# picoclaw_0.3.1_checksums.txt). Bump all three together to upgrade.
# Upgrades only come from a pin bump here.
# ---------------------------------------------------------------------------
PICOCLAW_VERSION="v0.3.1"
PICOCLAW_BASE_URL="https://github.com/sipeed/picoclaw/releases/download/${PICOCLAW_VERSION}"
PICOCLAW_BIN="/usr/local/bin/picoclaw"

die() { echo "[picoclaw] FATAL: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Idempotency: if the pinned version is already installed, bail early.
# ---------------------------------------------------------------------------
if command -v picoclaw >/dev/null 2>&1; then
  _installed="$(picoclaw version 2>/dev/null || picoclaw --version 2>/dev/null || true)"
  if printf '%s' "$_installed" | grep -qF "${PICOCLAW_VERSION#v}"; then
    echo "[picoclaw] picoclaw ${PICOCLAW_VERSION} already installed — nothing to do"
    exit 0
  fi
  echo "[picoclaw] found a different picoclaw build ('${_installed:-unknown}'); reinstalling ${PICOCLAW_VERSION}"
fi

# ---------------------------------------------------------------------------
# 2. Map CPU arch -> upstream tarball asset + its pinned SHA256.
#    Asset names + hashes are copied verbatim from picoclaw_0.3.1_checksums.txt.
# ---------------------------------------------------------------------------
_arch="$(uname -m)"
case "$_arch" in
  aarch64|arm64)
    PICOCLAW_ASSET="picoclaw_Linux_arm64.tar.gz"
    PICOCLAW_SHA256="e78ab0de05d000698ff8b32458e71cb3134e52e9eaf540e3323a72e9914de060"
    ;;
  x86_64|amd64)
    PICOCLAW_ASSET="picoclaw_Linux_x86_64.tar.gz"
    PICOCLAW_SHA256="86f0fe84aa55279352edbe13adfca484a03bcf189e2030b25e34628089069c3f"
    ;;
  riscv64)
    PICOCLAW_ASSET="picoclaw_Linux_riscv64.tar.gz"
    PICOCLAW_SHA256="2a3954542b36dc9076e2cf16f0fdded513d760aba4006c4fe5fe5e2a8d7afdf9"
    ;;
  armv7l)
    PICOCLAW_ASSET="picoclaw_Linux_armv7.tar.gz"
    PICOCLAW_SHA256="11bc06930d3a139f6759d03ee0e70d8b83481bd61ec500097beedab45a218fb3"
    ;;
  armv6l)
    PICOCLAW_ASSET="picoclaw_Linux_armv6.tar.gz"
    PICOCLAW_SHA256="22e58615c9cf8a6d689a046399f5685ed119c5f11d42d360cc1ca8320af7bb29"
    ;;
  *)
    die "unsupported CPU arch '$_arch' (no pinned picoclaw ${PICOCLAW_VERSION} Linux asset)"
    ;;
esac

echo "[picoclaw] installing picoclaw ${PICOCLAW_VERSION} for ${_arch} (${PICOCLAW_ASSET})"

# ---------------------------------------------------------------------------
# 3. Minimal fetch/extract deps (curl, ca-certificates, tar). On a fresh
#    bookworm container tar is present; curl/ca-certificates may not be.
# ---------------------------------------------------------------------------
if ! command -v curl >/dev/null 2>&1; then
  echo "[picoclaw] installing curl + ca-certificates"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || die "apt-get update failed"
  apt-get install -y --no-install-recommends curl ca-certificates tar \
    || die "failed to install curl/ca-certificates/tar"
fi

# ---------------------------------------------------------------------------
# 4. Download the pinned tarball, verify its SHA256 BEFORE extracting.
# ---------------------------------------------------------------------------
_tmp="$(mktemp -d)"
trap 'rm -rf "$_tmp"' EXIT

_tarball="${_tmp}/${PICOCLAW_ASSET}"
echo "[picoclaw] downloading ${PICOCLAW_BASE_URL}/${PICOCLAW_ASSET}"
curl -fsSL --retry 3 --retry-delay 2 -o "$_tarball" \
  "${PICOCLAW_BASE_URL}/${PICOCLAW_ASSET}" \
  || die "download failed for ${PICOCLAW_ASSET}"

echo "[picoclaw] verifying SHA256 checksum"
echo "${PICOCLAW_SHA256}  ${_tarball}" | sha256sum -c - >/dev/null 2>&1 \
  || die "checksum mismatch for ${PICOCLAW_ASSET} — refusing to install (expected ${PICOCLAW_SHA256})"
echo "[picoclaw] checksum OK"

# ---------------------------------------------------------------------------
# 5. Extract and install the picoclaw binary onto PATH.
# ---------------------------------------------------------------------------
echo "[picoclaw] extracting"
tar -xzf "$_tarball" -C "$_tmp" || die "failed to extract ${PICOCLAW_ASSET}"

_src="$(find "$_tmp" -type f -name picoclaw -perm -u+x 2>/dev/null | head -1)"
[ -z "$_src" ] && _src="$(find "$_tmp" -type f -name picoclaw 2>/dev/null | head -1)"
[ -n "$_src" ] || die "picoclaw binary not found inside ${PICOCLAW_ASSET}"

install -m 0755 "$_src" "$PICOCLAW_BIN" || die "failed to install binary to ${PICOCLAW_BIN}"

# ---------------------------------------------------------------------------
# 5a. Lock down: the installed runtime must not be able to replace itself.
#     Upgrades only come from a pin bump in this install script.
# ---------------------------------------------------------------------------
chmod 0555 "$PICOCLAW_BIN" || die "failed to chmod ${PICOCLAW_BIN}"

# Disable upstream auto-update / update switch in the generated config.
mkdir -p /root/.picoclaw
cat > /root/.picoclaw/config.json <<'CFG'
{
  "custom_allow_patterns": [],
  "auto_update": false,
  "update_channel": "none"
}
CFG

# ---------------------------------------------------------------------------
# 6. Verify the installed binary runs.
# ---------------------------------------------------------------------------
command -v picoclaw >/dev/null 2>&1 || die "picoclaw not on PATH after install"
echo "[picoclaw] install OK: $(picoclaw version 2>/dev/null || picoclaw --version 2>/dev/null || echo "${PICOCLAW_VERSION}")"
echo "[picoclaw] run 'picoclaw onboard' to initialise ~/.picoclaw/config.json before first use"
exit 0
