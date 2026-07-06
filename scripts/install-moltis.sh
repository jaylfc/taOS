#!/usr/bin/env bash
# tinyagentos installer for Moltis (https://github.com/moltis-org/moltis)
# ---------------------------------------------------------------------------
# Moltis — enterprise Rust agent framework. License: MIT.
# Installs via cargo (Rust toolchain required).
# ---------------------------------------------------------------------------
set -euo pipefail

MOLTIS_VERSION="${TAOS_MOLTIS_VERSION:-latest}"
MOLTIS_REPO="https://github.com/moltis-org/moltis.git"
MOLTIS_HOME="/opt/moltis"

log() { echo -e "\033[1;34m[moltis]\033[0m $*"; }
die() { echo -e "\033[1;31m[moltis]\033[0m $*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v cargo >/dev/null 2>&1 || die "cargo not found — install Rust (https://rustup.rs) and retry"

# --- install via cargo -----------------------------------------------------
if command -v moltis >/dev/null 2>&1; then
    log "moltis already installed: $(moltis --version 2>&1 | head -1)"
    exit 0
fi

# Try crates.io first (fast path)
if cargo install --list 2>/dev/null | grep -q '^moltis '; then
    log "moltis already installed via cargo"
    exit 0
fi

if [[ "$MOLTIS_VERSION" == "latest" ]]; then
    log "installing latest moltis from crates.io"
    cargo install moltis || die "cargo install moltis failed"
else
    log "installing moltis from git at ${MOLTIS_VERSION}"
    cargo install --git "$MOLTIS_REPO" --tag "$MOLTIS_VERSION" moltis \
        || die "cargo install moltis from git failed"
fi

log "moltis installed: $(moltis --version 2>&1 | head -1)"
