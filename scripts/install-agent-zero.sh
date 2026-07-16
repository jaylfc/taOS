#!/usr/bin/env bash
# tinyagentos installer for Agent Zero (https://github.com/frdel/agent-zero)
# ---------------------------------------------------------------------------
# Agent Zero — autonomous AI agent with self-correcting workflows, tool
# creation, and computer control. License: MIT.
# Clones the repo into /opt/agent-zero and installs with pip.
# ---------------------------------------------------------------------------
set -euo pipefail

AGENT_ZERO_VERSION="${TAOS_AGENT_ZERO_VERSION:-main}"
AGENT_ZERO_REPO="https://github.com/frdel/agent-zero.git"
AGENT_ZERO_HOME="/opt/agent-zero"

log() { echo -e "\033[1;34m[agent-zero]\033[0m $*"; }
die() { echo -e "\033[1;31m[agent-zero]\033[0m $*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found — install git and retry"
command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3.10+ and retry"

# --- clone / update --------------------------------------------------------
if [[ -d "${AGENT_ZERO_HOME}/.git" ]]; then
    log "agent-zero already cloned at ${AGENT_ZERO_HOME}; updating"
    git -C "$AGENT_ZERO_HOME" fetch origin "$AGENT_ZERO_VERSION"
    git -C "$AGENT_ZERO_HOME" checkout "$AGENT_ZERO_VERSION"
    git -C "$AGENT_ZERO_HOME" pull origin "$AGENT_ZERO_VERSION" || true
else
    log "cloning agent-zero ${AGENT_ZERO_VERSION} into ${AGENT_ZERO_HOME}"
    sudo mkdir -p "$AGENT_ZERO_HOME"
    sudo chown "$(whoami)" "$AGENT_ZERO_HOME"
    git clone --branch "$AGENT_ZERO_VERSION" "$AGENT_ZERO_REPO" "$AGENT_ZERO_HOME"
fi

# --- install with pip ------------------------------------------------------
log "installing agent-zero with pip"
cd "$AGENT_ZERO_HOME"
pip install -e . || die "pip install failed in ${AGENT_ZERO_HOME}"

log "agent-zero ${AGENT_ZERO_VERSION} installed at ${AGENT_ZERO_HOME}"
