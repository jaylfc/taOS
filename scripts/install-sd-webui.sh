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

# Pinned stable-diffusion-webui release tag and corresponding commit SHA.
# Tags are listed at: https://github.com/AUTOMATIC1111/stable-diffusion-webui/tags
# Pinned: 2026-06-07 (v1.10.1 — most recent stable release at time of pinning)
# Commit SHA resolved 2026-06-08: git ls-remote --tags https://github.com/AUTOMATIC1111/stable-diffusion-webui v1.10.1
# RESIDUAL RISK: AUTOMATIC1111 does not publish SHA256 sums for release archives.
SDWEBUI_TAG="${TAOS_SDWEBUI_TAG:-v1.10.1}"
# Immutable commit SHA for v1.10.1 — guards against tag retargeting.
# Update together with SDWEBUI_TAG when bumping the pin.
SDWEBUI_COMMIT="${TAOS_SDWEBUI_COMMIT:-82a973c04367123ae98bd9abdf80d9eda9b910e2}"

INSTALL_DIR="${TAOS_SDWEBUI_DIR:-$HOME/stable-diffusion-webui}"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "sd-webui already cloned at $INSTALL_DIR — skipping clone"
else
    command -v git >/dev/null 2>&1 || die "git not installed"
    log "cloning AUTOMATIC1111/stable-diffusion-webui into $INSTALL_DIR (tag $SDWEBUI_TAG)"
    git clone --depth 1 --branch "$SDWEBUI_TAG" \
        https://github.com/AUTOMATIC1111/stable-diffusion-webui "$INSTALL_DIR"
    # Verify the cloned commit matches the pinned SHA to detect tag retargeting.
    _actual_commit="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
    if [[ "$_actual_commit" != "$SDWEBUI_COMMIT" ]]; then
        die "sd-webui tag $SDWEBUI_TAG commit mismatch: expected $SDWEBUI_COMMIT, got $_actual_commit — update SDWEBUI_COMMIT if this is intentional"
    fi
    log "sd-webui pinned to $SDWEBUI_COMMIT (tag $SDWEBUI_TAG)"
fi

log "first-run dep install handled by webui.sh — start with: cd $INSTALL_DIR && ./webui.sh --listen"
log "done"
