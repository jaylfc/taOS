#!/bin/bash
# tinyagentos installer for whisper.cpp via pywhispercpp
# ---------------------------------------------------------------------------
# Cross-platform STT backend. Uses pywhispercpp (pip wheel) on most
# platforms, falls back to building from source if no wheel exists.
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[whisper-cpp]\033[0m $*"; }
die() { echo -e "\033[1;31m[whisper-cpp]\033[0m $*" >&2; exit 1; }

PY="${TAOS_WHISPER_PYTHON:-python3}"

$PY -c "import pywhispercpp; print('already installed:', pywhispercpp.__version__)" 2>/dev/null && {
    log "pywhispercpp already installed"; exit 0;
}

log "installing pywhispercpp via pip (may build from source on aarch64/other)"
$PY -m pip install --user pywhispercpp
log "done"
