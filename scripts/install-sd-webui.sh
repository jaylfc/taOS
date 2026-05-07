#!/bin/bash
# tinyagentos installer for AUTOMATIC1111 stable-diffusion-webui
# ---------------------------------------------------------------------------
# Heavyweight install. Uses the upstream webui.sh which manages its own
# Python venv and downloads model deps on first run.
#
# Environment overrides:
#   TAOS_SDWEBUI_DIR  install dir (default: ~/stable-diffusion-webui)
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[sd-webui]\033[0m $*"; }
die() { echo -e "\033[1;31m[sd-webui]\033[0m $*" >&2; exit 1; }

INSTALL_DIR="${TAOS_SDWEBUI_DIR:-$HOME/stable-diffusion-webui}"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "sd-webui already cloned at $INSTALL_DIR — skipping clone"
else
    command -v git >/dev/null 2>&1 || die "git not installed"
    log "cloning AUTOMATIC1111/stable-diffusion-webui into $INSTALL_DIR"
    git clone --depth 1 https://github.com/AUTOMATIC1111/stable-diffusion-webui "$INSTALL_DIR"
fi

log "first-run dep install handled by webui.sh — start with: cd $INSTALL_DIR && ./webui.sh --listen"
log "done"
