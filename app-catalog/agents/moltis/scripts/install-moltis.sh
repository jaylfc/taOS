#!/usr/bin/env bash
# install-moltis.sh — moltis agent-framework runtime installer
# Runs once inside a fresh Debian bookworm LXC container, as root.
# Idempotent: safe to re-run on an already-provisioned container.
#
# Installs UPSTREAM moltis from its official GitHub release (prebuilt Linux
# binary + bundled web/wasm assets). moltis is a single self-contained Rust
# binary — "secure persistent personal agent server in Rust" — so there is no
# Node/Cargo toolchain to install at deploy time.
#
# Upstream (verified): https://github.com/moltis-org/moltis  (MIT, active, 2.7k★)
#   NOTE: the taOS catalog manifest lists homepage github.com/moltis-ai/moltis
#   (404) and license Apache-2.0 — both are INCORRECT. The real project is
#   moltis-org/moltis under MIT. This installer targets the real repo.
# Release pinned:    https://github.com/moltis-org/moltis/releases/tag/20260603.01
set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned release + verified SHA-256 checksums (published by upstream as
# <asset>.sha256 alongside each release asset; fetched and embedded here so the
# install is deterministic and does not trust the network at run time).
#   https://github.com/moltis-org/moltis/releases/download/20260603.01/
#       moltis-20260603.01-x86_64-unknown-linux-gnu.tar.gz.sha256
#       moltis-20260603.01-aarch64-unknown-linux-gnu.tar.gz.sha256
# ---------------------------------------------------------------------------
MOLTIS_VERSION="20260603.01"
MOLTIS_BASE_URL="https://github.com/moltis-org/moltis/releases/download/${MOLTIS_VERSION}"

SHA256_X86_64="c3756a9d32fba331354f037fb90250f734dcecec9c2f953b6939711916f0da49"
SHA256_AARCH64="c102dca3865a106349bf1e78c4d42fb5133d0a967603115d577c90cd4e47f03e"

PREFIX="/opt/moltis"
INSTALL_DIR="${PREFIX}/${MOLTIS_VERSION}"   # versioned install root (binary + share/)
BIN_LINK="/usr/local/bin/moltis"

die() { echo "[moltis] FATAL: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Idempotency: already installed at this exact version -> done.
# ---------------------------------------------------------------------------
if [ -x "${INSTALL_DIR}/moltis" ] && [ -L "${BIN_LINK}" ] \
   && [ "$(readlink -f "${BIN_LINK}")" = "${INSTALL_DIR}/moltis" ]; then
  echo "[moltis] already installed at ${INSTALL_DIR} (version ${MOLTIS_VERSION}); nothing to do"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Select the release asset for this architecture.
#    Release ships x86_64 and aarch64 GNU/Linux tarballs. The moltis binary is
#    dynamically linked against glibc >= 2.34; Debian bookworm ships glibc 2.36.
# ---------------------------------------------------------------------------
ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64)
    ASSET="moltis-${MOLTIS_VERSION}-x86_64-unknown-linux-gnu.tar.gz"
    EXPECTED_SHA="${SHA256_X86_64}"
    ;;
  aarch64|arm64)
    ASSET="moltis-${MOLTIS_VERSION}-aarch64-unknown-linux-gnu.tar.gz"
    EXPECTED_SHA="${SHA256_AARCH64}"
    ;;
  *)
    die "unsupported architecture '${ARCH}' (upstream provides x86_64 and aarch64 Linux builds only)"
    ;;
esac

# ---------------------------------------------------------------------------
# 2. Runtime prerequisites.
#    - curl: fetch the release asset.
#    - ca-certificates: moltis makes TLS calls to LLM providers (OpenSSL is
#      vendored into the binary, but it still needs the system CA bundle).
#    - tar / zstd / xz: extract the .tar.gz (tar+gzip suffices for *.tar.gz).
# ---------------------------------------------------------------------------
echo "[moltis] ensuring runtime prerequisites (curl, ca-certificates, tar)"
NEED_PKGS=()
command -v curl  >/dev/null 2>&1 || NEED_PKGS+=(curl)
command -v tar   >/dev/null 2>&1 || NEED_PKGS+=(tar)
[ -e /etc/ssl/certs/ca-certificates.crt ] || NEED_PKGS+=(ca-certificates)
if [ "${#NEED_PKGS[@]}" -gt 0 ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends "${NEED_PKGS[@]}" \
    || die "apt-get install of [${NEED_PKGS[*]}] failed"
fi
command -v sha256sum >/dev/null 2>&1 || die "sha256sum not available (coreutils missing)"

# ---------------------------------------------------------------------------
# 3. Download the pinned release asset and verify its checksum BEFORE extract.
# ---------------------------------------------------------------------------
TMP_DIR="$(mktemp -d /tmp/moltis-install.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT
TARBALL="${TMP_DIR}/${ASSET}"

echo "[moltis] downloading ${ASSET} (release ${MOLTIS_VERSION})"
curl -fsSL --retry 3 --retry-delay 2 -o "${TARBALL}" "${MOLTIS_BASE_URL}/${ASSET}" \
  || die "download failed: ${MOLTIS_BASE_URL}/${ASSET}"

echo "[moltis] verifying SHA-256 checksum"
echo "${EXPECTED_SHA}  ${TARBALL}" | sha256sum -c - \
  || die "checksum mismatch for ${ASSET} — refusing to install (expected ${EXPECTED_SHA})"

# ---------------------------------------------------------------------------
# 4. Extract into the versioned install dir. The tarball lays out:
#       moltis                       (the binary)
#       share/moltis/wasm/*.wasm     (sandboxed tool modules)
#       share/moltis/web/...         (web UI assets)
#    moltis resolves these assets via MOLTIS_SHARE_DIR (set in the env file
#    below). We extract atomically into a temp dir then move into place.
# ---------------------------------------------------------------------------
echo "[moltis] installing into ${INSTALL_DIR}"
EXTRACT_DIR="${TMP_DIR}/extract"
mkdir -p "${EXTRACT_DIR}"
tar -xzf "${TARBALL}" -C "${EXTRACT_DIR}" \
  || die "extract failed for ${ASSET}"
[ -f "${EXTRACT_DIR}/moltis" ] || die "extracted archive has no top-level 'moltis' binary"
[ -d "${EXTRACT_DIR}/share/moltis" ] || die "extracted archive missing share/moltis assets"

mkdir -p "${PREFIX}"
rm -rf "${INSTALL_DIR}.new"
mv "${EXTRACT_DIR}" "${INSTALL_DIR}.new"
chmod 0755 "${INSTALL_DIR}.new/moltis"
rm -rf "${INSTALL_DIR}"
mv "${INSTALL_DIR}.new" "${INSTALL_DIR}"

# Stable symlink on PATH (atomic swap via -f).
ln -sfn "${INSTALL_DIR}/moltis" "${BIN_LINK}"

# ---------------------------------------------------------------------------
# 5. Verify the binary actually runs in this container.
# ---------------------------------------------------------------------------
if ! "${BIN_LINK}" --version >/dev/null 2>&1; then
  die "moltis binary installed but '--version' failed (glibc/runtime mismatch?)"
fi
echo "[moltis] install OK: $("${BIN_LINK}" --version 2>/dev/null | head -1)"

# ---------------------------------------------------------------------------
# 6. Config + data + env. moltis is configured via MOLTIS_* env vars:
#      MOLTIS_SHARE_DIR  — where it finds the bundled web/wasm assets
#      MOLTIS_CONFIG_DIR — config root
#      MOLTIS_DATA_DIR   — persistent data (sqlite db, memory, sessions)
#      MOLTIS_NO_TLS     — disable moltis' own TLS; taOS fronts it on loopback
#    Values come from env the deployer set (incus config set environment.*),
#    with safe dev/test defaults. Stored inside the container rootfs so they
#    travel with snapshot-based archives.
# ---------------------------------------------------------------------------
mkdir -p /root/.moltis/config /root/.moltis/data
chmod 700 /root/.moltis

: "${TAOS_AGENT_NAME:=unknown}"
: "${TAOS_MODEL:=}"
: "${LITELLM_API_KEY:=}"
: "${OPENAI_API_KEY:=}"
: "${OPENAI_BASE_URL:=http://127.0.0.1:4000/v1}"
: "${MOLTIS_HOST:=127.0.0.1}"
: "${MOLTIS_PORT:=32213}"

cat > /root/.moltis/env <<ENV_EOF
MOLTIS_SHARE_DIR=${INSTALL_DIR}/share/moltis
MOLTIS_CONFIG_DIR=/root/.moltis/config
MOLTIS_DATA_DIR=/root/.moltis/data
MOLTIS_NO_TLS=1
MOLTIS_HOST=${MOLTIS_HOST}
MOLTIS_PORT=${MOLTIS_PORT}
TAOS_AGENT_NAME=${TAOS_AGENT_NAME}
TAOS_MODEL=${TAOS_MODEL}
LITELLM_API_KEY=${LITELLM_API_KEY}
OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_BASE_URL=${OPENAI_BASE_URL}
ENV_EOF
chmod 600 /root/.moltis/env

# ---------------------------------------------------------------------------
# 7. systemd unit. Enable but do NOT start — the deployer starts moltis after
#    writing the LLM key/config (matches the openclaw deploy contract).
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/moltis.service <<'UNIT'
[Unit]
Description=moltis agent server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-/root/.moltis/env
ExecStart=/usr/local/bin/moltis serve
Restart=on-failure
RestartSec=3
WorkingDirectory=/root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable moltis.service

echo "[moltis] install complete (version ${MOLTIS_VERSION}; service enabled, start deferred to deployer)"
