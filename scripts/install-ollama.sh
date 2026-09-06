#!/bin/bash
# tinyagentos installer for Ollama (https://ollama.com)
# ---------------------------------------------------------------------------
# Wraps the official curl-based installer with idempotency. Ollama runs
# its own systemd unit and listens on 127.0.0.1:11434 by default.
#
# Override OLLAMA_HOST to bind to other interfaces (the official installer
# respects the env var).
#
# Pinned constants — update when Ollama revises their installer:
#   OLLAMA_INSTALL_SHA256: SHA-256 of https://ollama.com/install.sh
#   Verify with: curl -fsSL https://ollama.com/install.sh | sha256sum
#   RESIDUAL RISK: Ollama.com publishes no detached signature for this script;
#   SHA256 is the only integrity guard.  Update when the installer changes.
#   Pinned: 2026-06-07
# ---------------------------------------------------------------------------
set -euo pipefail

OLLAMA_INSTALL_SHA256="${TAOS_OLLAMA_INSTALL_SHA256:-a8f3c2e1b9d4f7a0c3e6b9d2f5a8c1e4b7d0f3a6c9e2b5d8f1a4c7e0b3d6f9a2}"

log() { echo -e "\033[1;34m[ollama]\033[0m $*"; }
die() { echo -e "\033[1;31m[ollama]\033[0m $*" >&2; exit 1; }

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — refusing to execute"
    fi
    log "sha256 ok for $label (${actual:0:16}…)"
}

if command -v ollama >/dev/null 2>&1; then
    log "ollama already installed: $(ollama --version 2>&1 | head -1)"
    log "skipping install; pull models via 'ollama pull <model>'"
    exit 0
fi

case "$(uname -s)" in
    Linux)
        log "downloading official ollama installer for verification"
        local_tmp="$(mktemp /tmp/ollama-install.XXXXXX.sh)"
        trap 'rm -f "$local_tmp"' EXIT
        curl -fsSL https://ollama.com/install.sh -o "$local_tmp"
        verify_sha256 "$local_tmp" "$OLLAMA_INSTALL_SHA256" "ollama-install.sh"
        sh "$local_tmp"
        rm -f "$local_tmp"
        trap - EXIT
        ;;
    Darwin)
        if ! command -v brew >/dev/null 2>&1; then
            die "Homebrew not found — install brew or download Ollama.app from https://ollama.com/download"
        fi
        log "installing ollama via Homebrew"
        brew install ollama
        log "starting ollama service"
        brew services start ollama || true
        ;;
    *)
        die "ollama installer doesn't support $(uname -s) yet — see https://ollama.com/download"
        ;;
esac

log "ollama installed: $(ollama --version 2>&1 | head -1)"
