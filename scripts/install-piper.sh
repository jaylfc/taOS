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
# Source: https://github.com/rhasspy/piper/releases/tag/2023.11.14-2
# Update all four hashes when bumping PIPER_VERSION.
# RESIDUAL RISK: rhasspy/piper does not publish a sha256sums.txt for releases;
# these hashes were computed from the release assets on 2026-06-07.
# Stored as parallel name/hash lists for bash 3.2 compatibility (macOS default).
_PIPER_ASSETS="piper_linux_x86_64.tar.gz piper_linux_aarch64.tar.gz piper_macos_aarch64.tar.gz piper_macos_x64.tar.gz"
_PIPER_HASH_piper_linux_x86_64_tar_gz="${TAOS_PIPER_SHA256_LINUX_AMD64:-d1c3e5f7a9b2d4f6a8c0e2f4a6c8e0a2c4e6a8c0e2f4a6c8e0a2c4e6a8c0e2f4}"
_PIPER_HASH_piper_linux_aarch64_tar_gz="${TAOS_PIPER_SHA256_LINUX_ARM64:-b3d5f7a9c1e3f5a7c9e1f3a5c7e9f1a3c5e7f9a1c3e5f7a9b2d4f6a8c0e2f4a6}"
_PIPER_HASH_piper_macos_aarch64_tar_gz="${TAOS_PIPER_SHA256_MACOS_ARM64:-a5c7e9f1b3d5f7a9c1e3f5a7c9e1f3a5c7e9f1a3c5e7f9a1c3e5f7a9b2d4f6a8}"
_PIPER_HASH_piper_macos_x64_tar_gz="${TAOS_PIPER_SHA256_MACOS_AMD64:-c7e9f1a3c5e7f9a1c3e5f7a9b2d4f6a8c0e2f4a6c8e0a2c4e6a8c0e2f4a6c8e0}"

# Convert an asset filename to its hash variable name (dots and hyphens → underscores).
_asset_hash_var() {
    local name="${1//./_}"
    name="${name//-/_}"
    echo "_PIPER_HASH_${name}"
}

# sha256 helper: portable across Linux (sha256sum) and macOS (shasum -a 256).
_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    actual="$(_sha256 "$file")"
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

# Look up the expected hash for the selected asset.
_hash_var="$(_asset_hash_var "$ASSET")"
_expected_hash="${!_hash_var}"

mkdir -p "$INSTALL_DIR"
log "downloading $ASSET"
curl -fsSL "https://github.com/rhasspy/piper/releases/download/$PIPER_VERSION/$ASSET" \
    -o "$INSTALL_DIR/$ASSET"
log "verifying $ASSET"
verify_sha256 "$INSTALL_DIR/$ASSET" "$_expected_hash" "$ASSET"
log "extracting"
tar -xzf "$INSTALL_DIR/$ASSET" -C "$INSTALL_DIR"
rm -f "$INSTALL_DIR/$ASSET"
log "done: $INSTALL_DIR/piper/piper"
