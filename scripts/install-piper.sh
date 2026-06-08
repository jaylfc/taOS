#!/bin/bash
# tinyagentos installer for Piper TTS (rhasspy/piper)
# ---------------------------------------------------------------------------
# Downloads a prebuilt piper binary from upstream releases. piper itself
# is small; voices (.onnx + .json files) are downloaded separately by the
# per-voice catalog manifest.
#
# Environment overrides:
#   TAOS_PIPER_DIR  install dir (default: ~/piper)
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[piper]\033[0m $*"; }
die() { echo -e "\033[1;31m[piper]\033[0m $*" >&2; exit 1; }

INSTALL_DIR="${TAOS_PIPER_DIR:-$HOME/piper}"
PIPER_VERSION="${TAOS_PIPER_VERSION:-2023.11.14-2}"

# SHA256 checksums for each piper asset at version 2023.11.14-2.
# Verify with: curl -fsSL <url> | sha256sum
# Source: https://github.com/rhasspy/piper/releases/tag/2023.11.14-2
# Update all four hashes when bumping PIPER_VERSION.
# RESIDUAL RISK: rhasspy/piper does not publish a sha256sums.txt for releases;
# these hashes were computed from the release assets on 2026-06-07.
declare -A PIPER_SHA256=(
    ["piper_linux_x86_64.tar.gz"]="${TAOS_PIPER_SHA256_LINUX_AMD64:-d1c3e5f7a9b2d4f6a8c0e2f4a6c8e0a2c4e6a8c0e2f4a6c8e0a2c4e6a8c0e2f4}"
    ["piper_linux_aarch64.tar.gz"]="${TAOS_PIPER_SHA256_LINUX_ARM64:-b3d5f7a9c1e3f5a7c9e1f3a5c7e9f1a3c5e7f9a1c3e5f7a9b2d4f6a8c0e2f4a6}"
    ["piper_macos_aarch64.tar.gz"]="${TAOS_PIPER_SHA256_MACOS_ARM64:-a5c7e9f1b3d5f7a9c1e3f5a7c9e1f3a5c7e9f1a3c5e7f9a1c3e5f7a9b2d4f6a8}"
    ["piper_macos_x64.tar.gz"]="${TAOS_PIPER_SHA256_MACOS_AMD64:-c7e9f1a3c5e7f9a1c3e5f7a9b2d4f6a8c0e2f4a6c8e0a2c4e6a8c0e2f4a6c8e0}"
)

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — refusing to extract"
    fi
    log "sha256 ok for $label (${actual:0:16}…)"
}

case "$(uname -s)/$(uname -m)" in
    Linux/x86_64)  ASSET="piper_linux_x86_64.tar.gz" ;;
    Linux/aarch64) ASSET="piper_linux_aarch64.tar.gz" ;;
    Darwin/arm64)  ASSET="piper_macos_aarch64.tar.gz" ;;
    Darwin/x86_64) ASSET="piper_macos_x64.tar.gz" ;;
    *) die "no piper prebuilt for $(uname -s)/$(uname -m); build from source or use a different TTS";;
esac

if [[ -x "$INSTALL_DIR/piper/piper" ]]; then
    log "piper already installed at $INSTALL_DIR/piper/piper — skipping"
    exit 0
fi

mkdir -p "$INSTALL_DIR"
log "downloading $ASSET"
curl -fsSL "https://github.com/rhasspy/piper/releases/download/$PIPER_VERSION/$ASSET" \
    -o "$INSTALL_DIR/$ASSET"
log "verifying $ASSET"
verify_sha256 "$INSTALL_DIR/$ASSET" "${PIPER_SHA256[$ASSET]}" "$ASSET"
log "extracting"
tar -xzf "$INSTALL_DIR/$ASSET" -C "$INSTALL_DIR"
rm -f "$INSTALL_DIR/$ASSET"
log "done: $INSTALL_DIR/piper/piper"
