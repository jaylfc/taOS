#!/bin/bash
# tinyagentos installer for IOPaint (https://github.com/Sanster/IOPaint)
# ---------------------------------------------------------------------------
# IOPaint is the maintained lama-cleaner successor: LaMa-based erase/inpaint
# plus rembg (RemoveBG) and RealESRGAN upscale plugins. It is the self-hosted
# image-editing backend for the taOS Images Studio.
#
# Installed via pip into the active environment. Started with `iopaint start`
# bound to a HIGH-POOL port (30493 — the deterministic slot for app_id
# "iopaint", computed by tinyagentos.installers.port_allocator). The LaMa erase
# model is the default; RemoveBG and RealESRGAN plugins are enabled so the
# /run_plugin_gen_image endpoint serves background-removal and upscale.
#
# Pinned version — bump when validating a newer IOPaint release:
#   IOPAINT_VERSION pins the PyPI release so installs are reproducible.
# ---------------------------------------------------------------------------
set -euo pipefail

IOPAINT_VERSION="${TAOS_IOPAINT_VERSION:-1.6.0}"
IOPAINT_PORT="${TAOS_IOPAINT_PORT:-30493}"
IOPAINT_HOST="${TAOS_IOPAINT_HOST:-0.0.0.0}"
IOPAINT_MODEL="${TAOS_IOPAINT_MODEL:-lama}"
IOPAINT_DEVICE="${TAOS_IOPAINT_DEVICE:-cpu}"
IOPAINT_MODEL_DIR="${TAOS_IOPAINT_MODEL_DIR:-$HOME/.cache/iopaint}"

log() { echo -e "\033[1;34m[iopaint]\033[0m $*"; }
die() { echo -e "\033[1;31m[iopaint]\033[0m $*" >&2; exit 1; }

PIP_BIN=""
pick_pip() {
    if command -v pip3 >/dev/null 2>&1; then PIP_BIN="pip3"
    elif command -v pip >/dev/null 2>&1; then PIP_BIN="pip"
    else die "pip not found — install Python 3 + pip first"; fi
}

# Idempotent: if iopaint is already installed, do not reinstall.
if command -v iopaint >/dev/null 2>&1; then
    log "iopaint already installed: $(iopaint --version 2>&1 | head -1)"
    log "skipping install"
    exit 0
fi

pick_pip
log "installing iopaint==$IOPAINT_VERSION (+ rembg, realesrgan plugins) via $PIP_BIN"
# rembg pulls the RemoveBG model deps; realesrgan pulls the upscaler deps.
"$PIP_BIN" install --no-input "iopaint==$IOPAINT_VERSION" rembg realesrgan \
    || die "pip install failed"

command -v iopaint >/dev/null 2>&1 || die "iopaint binary not on PATH after install"

mkdir -p "$IOPAINT_MODEL_DIR"

log "iopaint installed: $(iopaint --version 2>&1 | head -1)"
log "start it with:"
log "  iopaint start --model $IOPAINT_MODEL --device $IOPAINT_DEVICE \\"
log "    --host $IOPAINT_HOST --port $IOPAINT_PORT --model-dir $IOPAINT_MODEL_DIR \\"
log "    --enable-remove-bg --enable-realesrgan --realesrgan-model realesr_general_x4v3"
