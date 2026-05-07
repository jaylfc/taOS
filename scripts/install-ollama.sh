#!/bin/bash
# tinyagentos installer for Ollama (https://ollama.com)
# ---------------------------------------------------------------------------
# Wraps the official curl-based installer with idempotency. Ollama runs
# its own systemd unit and listens on 127.0.0.1:11434 by default.
#
# Override OLLAMA_HOST to bind to other interfaces (the official installer
# respects the env var).
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[ollama]\033[0m $*"; }
die() { echo -e "\033[1;31m[ollama]\033[0m $*" >&2; exit 1; }

if command -v ollama >/dev/null 2>&1; then
    log "ollama already installed: $(ollama --version 2>&1 | head -1)"
    log "skipping install; pull models via 'ollama pull <model>'"
    exit 0
fi

case "$(uname -s)" in
    Linux)
        log "running official ollama installer (Linux)"
        curl -fsSL https://ollama.com/install.sh | sh
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
