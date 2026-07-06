#!/usr/bin/env bash
# tinyagentos installer for DeerFlow (https://github.com/bytedance/deer-flow)
# ---------------------------------------------------------------------------
# DeerFlow — ByteDance LangGraph SuperAgent harness. License: MIT.
# Clones the repo into /opt/deer-flow and provisions with uv (Python 3.12).
# ---------------------------------------------------------------------------
set -euo pipefail

DEERFLOW_VERSION="${TAOS_DEERFLOW_VERSION:-main}"
DEERFLOW_REPO="https://github.com/bytedance/deer-flow.git"
DEERFLOW_HOME="/opt/deer-flow"

log() { echo -e "\033[1;34m[deer-flow]\033[0m $*"; }
die() { echo -e "\033[1;31m[deer-flow]\033[0m $*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found — install git and retry"
command -v uv >/dev/null 2>&1 || die "uv not found — install uv (https://docs.astral.sh/uv/) and retry"

# --- clone / update --------------------------------------------------------
if [[ -d "${DEERFLOW_HOME}/.git" ]]; then
    log "deer-flow already cloned at ${DEERFLOW_HOME}; updating"
    git -C "$DEERFLOW_HOME" fetch origin "$DEERFLOW_VERSION"
    git -C "$DEERFLOW_HOME" checkout "$DEERFLOW_VERSION"
    git -C "$DEERFLOW_HOME" pull origin "$DEERFLOW_VERSION" || true
else
    log "cloning deer-flow ${DEERFLOW_VERSION} into ${DEERFLOW_HOME}"
    sudo mkdir -p "$DEERFLOW_HOME"
    sudo chown "$(whoami)" "$DEERFLOW_HOME"
    git clone --branch "$DEERFLOW_VERSION" "$DEERFLOW_REPO" "$DEERFLOW_HOME"
fi

# --- provision with uv -----------------------------------------------------
log "provisioning deer-flow backend with uv (Python 3.12)"
cd "$DEERFLOW_HOME"
uv sync --python 3.12 || die "uv sync failed in ${DEERFLOW_HOME}"

log "deer-flow ${DEERFLOW_VERSION} installed at ${DEERFLOW_HOME}"
