#!/bin/bash
# tinyagentos installer for llama.cpp
# ---------------------------------------------------------------------------
# Clones ggerganov/llama.cpp and builds llama-server with auto-detected
# acceleration (CUDA, Metal, Vulkan, or CPU). For Pi-NPU users, prefer
# install-rk-llama-cpp.sh which uses pre-compiled aarch64 binaries with
# the RKNPU2 ggml backend.
#
# Environment overrides:
#   TAOS_LLAMACPP_DIR     install dir (default: ~/llama.cpp)
#   TAOS_LLAMACPP_BACKEND force a specific backend (cuda|metal|vulkan|cpu)
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[llama-cpp]\033[0m $*"; }
die() { echo -e "\033[1;31m[llama-cpp]\033[0m $*" >&2; exit 1; }

INSTALL_DIR="${TAOS_LLAMACPP_DIR:-$HOME/llama.cpp}"

# Detect accel
detect_backend() {
    if [[ -n "${TAOS_LLAMACPP_BACKEND:-}" ]]; then
        echo "$TAOS_LLAMACPP_BACKEND"; return
    fi
    if [[ "$(uname -s)" == "Darwin" ]]; then echo "metal"; return; fi
    if command -v nvidia-smi >/dev/null 2>&1; then echo "cuda"; return; fi
    if command -v vulkaninfo >/dev/null 2>&1; then echo "vulkan"; return; fi
    echo "cpu"
}

BACKEND=$(detect_backend)
log "target backend: $BACKEND, install dir: $INSTALL_DIR"

if [[ -x "$INSTALL_DIR/build/bin/llama-server" ]]; then
    log "llama-server already built at $INSTALL_DIR/build/bin/llama-server — skipping"
    exit 0
fi

command -v cmake >/dev/null 2>&1 || die "cmake not installed (apt install cmake / brew install cmake)"
command -v git >/dev/null 2>&1 || die "git not installed"

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    log "cloning llama.cpp into $INSTALL_DIR"
    git clone --depth 1 https://github.com/ggerganov/llama.cpp "$INSTALL_DIR"
fi

log "configuring cmake (backend=$BACKEND)"
case "$BACKEND" in
    cuda)    CMAKE_FLAGS=(-DGGML_CUDA=ON) ;;
    metal)   CMAKE_FLAGS=(-DGGML_METAL=ON) ;;
    vulkan)  CMAKE_FLAGS=(-DGGML_VULKAN=ON) ;;
    cpu|*)   CMAKE_FLAGS=() ;;
esac

cmake -B "$INSTALL_DIR/build" -S "$INSTALL_DIR" "${CMAKE_FLAGS[@]}"
log "building llama-server (this may take 5-15 min)"
cmake --build "$INSTALL_DIR/build" --target llama-server -j"$(nproc 2>/dev/null || echo 4)"

log "done: $INSTALL_DIR/build/bin/llama-server"
