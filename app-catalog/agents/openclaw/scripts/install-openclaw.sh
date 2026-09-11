#!/usr/bin/env bash
# tinyagentos installer for OpenClaw (https://github.com/openclaw/openclaw)
# ---------------------------------------------------------------------------
# OpenClaw — full-featured agent framework with multi-channel support
# (Discord, Telegram, Slack, Signal). License: MIT.
# Clones the repo into /opt/openclaw and installs with pip.
# ---------------------------------------------------------------------------
set -euo pipefail

OPENCLAW_VERSION="${TAOS_OPENCLAW_VERSION:-main}"
OPENCLAW_REPO="https://github.com/openclaw/openclaw.git"
OPENCLAW_HOME="/opt/openclaw"

log() { echo -e "\033[1;34m[openclaw]\033[0m $*"; }
die() { echo -e "\033[1;31m[openclaw]\033[0m $*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found — install git and retry"
command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3.10+ and retry"

# --- clone / update --------------------------------------------------------
if [[ -d "${OPENCLAW_HOME}/.git" ]]; then
    log "openclaw already cloned at ${OPENCLAW_HOME}; updating"
    git -C "$OPENCLAW_HOME" fetch origin "$OPENCLAW_VERSION"
    git -C "$OPENCLAW_HOME" checkout "$OPENCLAW_VERSION"
    git -C "$OPENCLAW_HOME" pull origin "$OPENCLAW_VERSION" || true
else
    log "cloning openclaw ${OPENCLAW_VERSION} into ${OPENCLAW_HOME}"
    sudo mkdir -p "$OPENCLAW_HOME"
    sudo chown "$(whoami)" "$OPENCLAW_HOME"
    git clone --branch "$OPENCLAW_VERSION" "$OPENCLAW_REPO" "$OPENCLAW_HOME"
fi

# --- install with pip ------------------------------------------------------
log "installing openclaw with pip"
cd "$OPENCLAW_HOME"
pip install -e . || die "pip install failed in ${OPENCLAW_HOME}"

log "openclaw ${OPENCLAW_VERSION} installed at ${OPENCLAW_HOME}"
