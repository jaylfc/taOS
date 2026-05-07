#!/bin/bash
# tinyagentos installer for ezrknpu helpers (Rockchip RK3588 NPU)
# ---------------------------------------------------------------------------
# ezrknpu is the umbrella for the Rockchip-specific helper scripts and
# Python wrappers that live alongside the rknpu kernel driver. The actual
# RKNN runtime lib (librknnrt.so) is provided by install-rknpu.sh.
#
# This installer does the user-space Python parts:
#   - rknn-toolkit-lite2 (inference-only Python bindings)
#   - rknnpu-helpers (taOS helpers that wrap toolkit-lite calls)
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[ezrknpu]\033[0m $*"; }
die() { echo -e "\033[1;31m[ezrknpu]\033[0m $*" >&2; exit 1; }

[[ "$(uname -m)" == "aarch64" ]] || die "ezrknpu is aarch64-only (RK3588). Got $(uname -m)."
[[ -f /usr/lib/librknnrt.so ]] || die "librknnrt.so missing — run install-rknpu.sh first"

PY="${TAOS_PYTHON:-python3}"

if $PY -c "import rknnlite" 2>/dev/null; then
    log "rknnlite already installed"
else
    log "installing rknn-toolkit-lite2 wheel"
    # rknn-toolkit-lite2 wheels are published by airockchip; use the
    # version that matches the installed librknnrt.so. The current
    # libraries shipping with install-rknpu.sh target rknn-toolkit2 v2.3.x.
    $PY -m pip install --user "rknn-toolkit-lite2"
fi

log "ezrknpu user-space install complete; runtime drivers from install-rknpu.sh"
