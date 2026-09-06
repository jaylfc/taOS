#!/bin/bash
# tinyagentos installer for vLLM
# ---------------------------------------------------------------------------
# vLLM is x86 NVIDIA CUDA only (with experimental ROCm support). Refuses
# to install on other platforms with a clear message.
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[vllm]\033[0m $*"; }
die() { echo -e "\033[1;31m[vllm]\033[0m $*" >&2; exit 1; }

if [[ "$(uname -s)" != "Linux" ]] || [[ "$(uname -m)" != "x86_64" ]]; then
    die "vLLM requires x86_64 Linux (got $(uname -s) $(uname -m)). Use ollama or llama-cpp on this host instead."
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
    die "vLLM requires an NVIDIA GPU with CUDA — no nvidia-smi found. Use ollama or llama-cpp instead."
fi

PY="${TAOS_VLLM_PYTHON:-python3}"
$PY -c "import vllm; print('already installed:', vllm.__version__)" 2>/dev/null && {
    log "vllm already installed"; exit 0;
}

log "installing vllm via pip"
$PY -m pip install --user vllm
log "done: $($PY -c 'import vllm; print(vllm.__version__)')"
