#!/usr/bin/env bash
# tinyagentos installer for PicoClaw (https://github.com/sipeed/picoclaw)
# ---------------------------------------------------------------------------
# PicoClaw — Sipeed NPU-aware micro agent. License: MIT.
# Under 10MB RAM, designed for ARM boards with NPU acceleration.
# Clones the repo and builds for the target platform.
# ---------------------------------------------------------------------------
set -euo pipefail

PICOCLAW_VERSION="${TAOS_PICOCLAW_VERSION:-main}"
PICOCLAW_REPO="https://github.com/sipeed/picoclaw.git"
PICOCLAW_HOME="/opt/picoclaw"

log() { echo -e "\033[1;34m[picoclaw]\033[0m $*"; }
die() { echo -e "\033[1;31m[picoclaw]\033[0m $*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found — install git and retry"
command -v cmake >/dev/null 2>&1 || die "cmake not found — install cmake and retry"

# --- clone / update --------------------------------------------------------
if [[ -d "${PICOCLAW_HOME}/.git" ]]; then
    log "picoclaw already cloned at ${PICOCLAW_HOME}; updating"
    git -C "$PICOCLAW_HOME" fetch origin "$PICOCLAW_VERSION"
    git -C "$PICOCLAW_HOME" checkout "$PICOCLAW_VERSION"
    git -C "$PICOCLAW_HOME" pull origin "$PICOCLAW_VERSION" || true
else
    log "cloning picoclaw ${PICOCLAW_VERSION} into ${PICOCLAW_HOME}"
    sudo mkdir -p "$PICOCLAW_HOME"
    sudo chown "$(whoami)" "$PICOCLAW_HOME"
    git clone --branch "$PICOCLAW_VERSION" "$PICOCLAW_REPO" "$PICOCLAW_HOME"
fi

# --- build -----------------------------------------------------------------
log "building picoclaw"
cd "$PICOCLAW_HOME"
if [[ -f CMakeLists.txt ]]; then
    mkdir -p build && cd build
    cmake .. -DCMAKE_BUILD_TYPE=Release || die "cmake failed"
    make -j"$(nproc)" || die "make failed"
elif [[ -f Makefile ]]; then
    make -j"$(nproc)" || die "make failed"
else
    die "no recognized build system found in ${PICOCLAW_HOME}"
fi

# --- install ----------------------------------------------------------------
log "installing picoclaw to system"
if [[ -d build ]]; then
    cd "$PICOCLAW_HOME/build"
fi
if grep -q 'install' Makefile 2>/dev/null || grep -q 'install' ../Makefile 2>/dev/null; then
    sudo make install || log "make install failed — binary may need manual PATH setup"
else
    log "no install target in Makefile — picoclaw binary at ${PICOCLAW_HOME}/build"
fi

log "picoclaw ${PICOCLAW_VERSION} built at ${PICOCLAW_HOME}"
