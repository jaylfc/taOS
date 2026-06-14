#!/usr/bin/env bash
# tinyagentos installer for MusicGPT (https://github.com/gabotechs/MusicGPT)
# ---------------------------------------------------------------------------
# MusicGPT generates music from text using Meta's MusicGen, shipped as a
# self-contained Rust binary (no Python runtime). taOS catalog id: musicgpt.
#
# This is a HOST-side SERVICE installer. It receives the taOS project_dir as
# $1 and installs the `musicgpt` binary, then records how to launch its web
# UI / server on port 8882 (override with TAOS_MUSICGPT_PORT).
#
# Strategy (per official README + GitHub Releases):
#   1. Prefer the prebuilt release binary for the host arch, pinned to a
#      version tag, with SHA256 verified before install.
#   2. Fall back to `cargo install musicgpt` where no prebuilt binary exists
#      (e.g. Linux aarch64) or as a last resort.
#
# Sources (verified 2026-06-14):
#   README install:   https://github.com/gabotechs/MusicGPT (Homebrew / cargo / prebuilt binaries)
#   Releases (pinned): https://github.com/gabotechs/MusicGPT/releases/tag/v0.3.28
#   CLI flags from src/cli.rs @ v0.3.28:
#     --ui-port <N>  (default 8642)  port for the web app
#     --ui-expose    bind 0.0.0.0 instead of 127.0.0.1
#     --ui-no-open   do not auto-open a browser (headless host)
#     --data-path    override default data storage path
#     --gpu          experimental GPU inference (CUDA/Vulkan/Metal where built)
#
# Tiers: x86 + CUDA/Vulkan -> full (use --gpu); arm / cpu -> degraded (CPU only).
#
# NOTE ON CHECKSUMS: upstream publishes NO SHA256 checksum files. The pinned
# hashes below were computed from the v0.3.28 release assets at pin time and
# are the integrity guard. Update both the tag and hashes together when bumping.
#   Pinned: 2026-06-14
# ---------------------------------------------------------------------------
set -euo pipefail

# --- pinned release -------------------------------------------------------
MUSICGPT_VERSION="v0.3.28"
MUSICGPT_REPO="gabotechs/MusicGPT"
# SHA256 of each pinned prebuilt asset (computed from the v0.3.28 release).
MUSICGPT_SHA256_LINUX_X86_64="4f7beeda4dfb04210692d2053435930e4e0a745947915cbabd5399215187fe3f"
MUSICGPT_SHA256_DARWIN_AARCH64="eebc080ad944bf4a3f89222e569f3b9bf785259db991b961f53fd76a2231c389"

# --- taOS wiring ----------------------------------------------------------
PROJECT_DIR="${1:-}"
MUSICGPT_PORT="${TAOS_MUSICGPT_PORT:-30264}"
INSTALL_DIR="${TAOS_MUSICGPT_BIN_DIR:-/usr/local/bin}"
BIN_PATH="${INSTALL_DIR}/musicgpt"

log() { echo -e "\033[1;34m[musicgpt]\033[0m $*"; }
die() { echo -e "\033[1;31m[musicgpt]\033[0m $*" >&2; exit 1; }

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$file" | awk '{print $1}')"
    else
        die "no sha256sum/shasum available to verify $label"
    fi
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — refusing to install"
    fi
    log "sha256 ok for $label (${actual:0:16}…)"
}

# data-path for MusicGPT models/output, kept inside the taOS project dir
DATA_PATH=""
if [[ -n "$PROJECT_DIR" ]]; then
    DATA_PATH="${PROJECT_DIR%/}/musicgpt-data"
fi

# --- idempotency ----------------------------------------------------------
if command -v musicgpt >/dev/null 2>&1; then
    EXISTING="$(command -v musicgpt)"
    log "musicgpt already installed at ${EXISTING}: $(musicgpt --version 2>&1 | head -1)"
    log "serve with: musicgpt --ui-port ${MUSICGPT_PORT} --ui-expose --ui-no-open${DATA_PATH:+ --data-path ${DATA_PATH}}"
    exit 0
fi
if [[ -x "$BIN_PATH" ]]; then
    log "musicgpt already installed at ${BIN_PATH}: $("$BIN_PATH" --version 2>&1 | head -1)"
    exit 0
fi

[[ -n "$DATA_PATH" ]] && mkdir -p "$DATA_PATH"

OS="$(uname -s)"
ARCH="$(uname -m)"

# --- prebuilt-binary install ---------------------------------------------
ASSET=""
EXPECTED_SHA=""
case "${OS}/${ARCH}" in
    Linux/x86_64|Linux/amd64)
        ASSET="musicgpt-x86_64-unknown-linux-gnu"
        EXPECTED_SHA="$MUSICGPT_SHA256_LINUX_X86_64"
        ;;
    Darwin/arm64|Darwin/aarch64)
        ASSET="musicgpt-aarch64-apple-darwin"
        EXPECTED_SHA="$MUSICGPT_SHA256_DARWIN_AARCH64"
        ;;
    *)
        # No prebuilt binary upstream for this target (e.g. Linux aarch64,
        # Intel macOS). Fall through to the cargo build path below.
        log "no prebuilt musicgpt binary for ${OS}/${ARCH}; will try cargo"
        ;;
esac

install_prebuilt() {
    local url="https://github.com/${MUSICGPT_REPO}/releases/download/${MUSICGPT_VERSION}/${ASSET}"
    local tmp
    tmp="$(mktemp /tmp/musicgpt.XXXXXX)"
    trap 'rm -f "$tmp"' RETURN
    log "downloading ${ASSET} (${MUSICGPT_VERSION})"
    curl -fsSL "$url" -o "$tmp" || die "download failed: $url"
    verify_sha256 "$tmp" "$EXPECTED_SHA" "$ASSET"
    chmod +x "$tmp"
    if mkdir -p "$INSTALL_DIR" 2>/dev/null && [[ -w "$INSTALL_DIR" ]]; then
        mv "$tmp" "$BIN_PATH"
    else
        log "elevating to install into ${INSTALL_DIR} (sudo)"
        sudo mkdir -p "$INSTALL_DIR"
        sudo mv "$tmp" "$BIN_PATH"
        sudo chmod +x "$BIN_PATH"
    fi
    trap - RETURN
    log "installed musicgpt -> ${BIN_PATH}"
}

# --- cargo fallback -------------------------------------------------------
install_cargo() {
    command -v cargo >/dev/null 2>&1 \
        || die "no prebuilt binary for ${OS}/${ARCH} and cargo not found — install Rust from https://rustup.rs or use a supported platform"
    log "building musicgpt ${MUSICGPT_VERSION} from crates.io via cargo (this can take a while)"
    # --root puts the binary in ${INSTALL_DIR%/bin}/bin == ${INSTALL_DIR}
    local root="${INSTALL_DIR%/bin}"
    cargo install musicgpt --version "${MUSICGPT_VERSION#v}" --locked --root "$root" \
        || cargo install musicgpt --locked --root "$root" \
        || die "cargo install musicgpt failed"
    log "installed musicgpt via cargo -> ${root}/bin/musicgpt"
}

if [[ -n "$ASSET" ]]; then
    install_prebuilt
else
    install_cargo
fi

# --- verify + report ------------------------------------------------------
if [[ -x "$BIN_PATH" ]]; then
    log "musicgpt installed: $("$BIN_PATH" --version 2>&1 | head -1)"
elif command -v musicgpt >/dev/null 2>&1; then
    log "musicgpt installed: $(musicgpt --version 2>&1 | head -1)"
else
    die "musicgpt not found after install"
fi

GPU_HINT=""
if [[ "$ARCH" == "x86_64" || "$ARCH" == "amd64" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    GPU_HINT=" --gpu"   # x86 + CUDA -> full tier
fi

log "service ready on port ${MUSICGPT_PORT}"
log "start with: musicgpt --ui-port ${MUSICGPT_PORT} --ui-expose --ui-no-open${DATA_PATH:+ --data-path ${DATA_PATH}}${GPU_HINT}"
exit 0
