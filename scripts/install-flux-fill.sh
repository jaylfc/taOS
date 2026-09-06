#!/bin/bash
# tinyagentos installer for the FLUX.1-Fill GPU image-edit backend (id: flux-fill)
# ---------------------------------------------------------------------------
# This is the QUALITY inpaint/outpaint tier for the taOS Images Studio. It runs
# a leejet/stable-diffusion.cpp `sd-server` (A1111-compatible: serves
# /sdapi/v1/img2img) hosting the FLUX.1-Fill-dev model on an NVIDIA GPU. The
# config captured here was MANUALLY VERIFIED WORKING on a 12GB RTX 3060: it
# returns a real inpainted 512px image in ~73s.
#
# It reuses the same CUDA image and FLUX support files as the sd-cpp generation
# backend (taos-sdcpp:cuda, built by scripts/install-sd-cpp.sh /
# stable-diffusion.cpp with GGML_CUDA=ON and CMAKE_CUDA_ARCHITECTURES=86). The
# server binds a HIGH-POOL port (38298 - the deterministic slot for app_id
# "flux-fill", computed by tinyagentos.installers.port_allocator). The
# container's internal port stays 7864; we map host->7864.
#
# CRITICAL GOTCHA (do not remove --clip-on-cpu / --vae-on-cpu):
#   The FLUX text encoders MUST run on CPU or the 12GB card OOMs at inference.
#   With --clip-on-cpu the GPU resident is ~6.5GB, leaving headroom; WITHOUT it
#   inference OOMs while allocating the t5 graph. This is the key reason the
#   verified run works on a 12GB card.
#
# Model source (non-gated mirror):
#   flux1-fill-dev-Q4_K_S.gguf (6.8GB) from YarvixPA/FLUX.1-Fill-dev-gguf.
#   The official black-forest-labs / city96 repos are GATED; an operator with an
#   accepted HF license + token can substitute the gated source by overriding
#   TAOS_FLUX_FILL_GGUF_URL.
#
# Encoders + VAE (ae.safetensors, clip_l.safetensors, t5xxl-Q3_K_M.gguf) are the
# standard FLUX support files, shared with the sd-cpp generation backend's
# models dir. They are fetched here only if missing.
#
# Pinned filenames - bump together when validating a newer FLUX Fill build.
# GPU-host ONLY: the script exits early if no CUDA GPU is detected.
# ---------------------------------------------------------------------------
set -euo pipefail

FLUX_FILL_PORT="${TAOS_FLUX_FILL_PORT:-38298}"

# Host interface that Docker publishes the backend on.
# SECURITY: sd-server has NO authentication. This backend is for taOS's OWN use
# only, so by default we publish on 127.0.0.1 (loopback). Docker then exposes the
# port on loopback alone, so the unauthenticated server is never reachable over
# the network. This is the safe default and resolves the prior 0.0.0.0 exposure.
# Operators running taOS in a SPLIT/cluster layout (controller on one host, GPU
# on another) who need cross-host access must EITHER set TAOS_FLUX_FILL_BIND to a
# specific private interface IP (e.g. a tailnet address) OR, preferably, keep the
# loopback default and reach the server via an SSH tunnel. Do not default to
# 0.0.0.0. (Mirrors the bind note in install-sd-cpp.sh.)
FLUX_FILL_BIND="${TAOS_FLUX_FILL_BIND:-127.0.0.1}"
FLUX_FILL_CONTAINER="${TAOS_FLUX_FILL_CONTAINER:-taos-sdcpp-fill}"
FLUX_FILL_IMAGE="${TAOS_FLUX_FILL_IMAGE:-taos-sdcpp:cuda}"
MODELS_DIR="${TAOS_FLUX_FILL_MODELS_DIR:-$HOME/.cache/taos-sdcpp/models}"

# Pinned model + support file names (these are the filenames the verified run used).
FLUX_FILL_GGUF="flux1-fill-dev-Q4_K_S.gguf"
VAE_FILE="ae.safetensors"
CLIP_L_FILE="clip_l.safetensors"
T5XXL_FILE="t5xxl-Q3_K_M.gguf"

# Non-gated source mirrors (override the GGUF URL to use an accepted-license gated source).
FLUX_FILL_GGUF_URL="${TAOS_FLUX_FILL_GGUF_URL:-https://huggingface.co/YarvixPA/FLUX.1-Fill-dev-gguf/resolve/main/flux1-fill-dev-Q4_K_S.gguf}"
VAE_URL="${TAOS_FLUX_VAE_URL:-https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors}"
CLIP_L_URL="${TAOS_FLUX_CLIP_L_URL:-https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors}"
T5XXL_URL="${TAOS_FLUX_T5XXL_URL:-https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q3_K_M.gguf}"

log() { echo -e "\033[1;34m[flux-fill]\033[0m $*"; }
die() { echo -e "\033[1;31m[flux-fill]\033[0m $*" >&2; exit 1; }

# --- GPU guard: this backend is CUDA-only -----------------------------------
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
    die "no CUDA GPU detected (nvidia-smi unavailable) - flux-fill is the GPU quality tier and requires an NVIDIA GPU. Use the iopaint backend on CPU/ARM hosts."
fi
command -v docker >/dev/null 2>&1 || die "docker not found - install Docker with the NVIDIA container toolkit first"
# curl is used by the idempotency probe below; wget by the model fetch further down.
for tool in curl wget; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool '$tool' not found on PATH"
done

# --- idempotency: skip if the container already answers on the port ---------
if curl -fsS --max-time 3 "http://127.0.0.1:${FLUX_FILL_PORT}/sdapi/v1/options" >/dev/null 2>&1; then
    log "flux-fill already serving on port ${FLUX_FILL_PORT} - nothing to do"
    exit 0
fi

# --- ensure the CUDA image exists -------------------------------------------
# Built by the sd-cpp docker setup (GGML_CUDA=ON, CMAKE_CUDA_ARCHITECTURES=86):
#   FROM nvidia/cuda:*-devel -> clone leejet/stable-diffusion.cpp ->
#   cmake -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -> runtime image with
#   sd-server + sd-cli. We do not rebuild it here; we require it to be present.
if ! docker image inspect "$FLUX_FILL_IMAGE" >/dev/null 2>&1; then
    die "docker image '$FLUX_FILL_IMAGE' not found - build the sd.cpp CUDA image first (see scripts/install-sd-cpp.sh / the taos-sdcpp:cuda Dockerfile: GGML_CUDA=ON, CMAKE_CUDA_ARCHITECTURES=86)"
fi

# --- fetch the model + support files (idempotent; wget -c resumes) ----------
mkdir -p "$MODELS_DIR"

# Resume-aware fetch. We never skip solely because the destination exists and is
# non-empty: a partial/corrupt prior download is non-empty too, and skipping it
# would leave a truncated model in place. Instead we ask the server for the
# expected size (Content-Length) and, when known, only treat the file as done if
# its on-disk size already matches; otherwise we always invoke `wget -c` so a
# partial file resumes from where it left off and a genuinely complete file is a
# fast no-op (wget -c sees nothing left to fetch and exits quickly).
fetch() {
    local name="$1" url="$2" dest="${MODELS_DIR}/$1"
    local expected="" actual=""
    if [[ -f "$dest" ]]; then
        expected="$(curl -fsSIL --max-time 30 "$url" 2>/dev/null \
            | awk 'BEGIN{IGNORECASE=1} /^content-length:/ {v=$2} END{gsub(/\r/,"",v); print v}')"
        if [[ -n "$expected" ]]; then
            actual="$(wc -c < "$dest" 2>/dev/null | tr -d ' ')"
            if [[ "$actual" == "$expected" ]]; then
                log "$name already complete (${actual} bytes) - skipping download"
                return 0
            fi
            # `wget -c` only resumes when the on-disk file is SHORTER than the
            # remote. A file that is the same length-or-longer can't be a valid
            # partial (it's corrupt/oversized), and -c would leave it untouched
            # forever. Delete it first so the next wget re-downloads cleanly.
            if [[ "${actual:-0}" -ge "$expected" ]]; then
                log "$name oversized/corrupt (${actual:-0} >= ${expected} bytes) - removing and re-downloading"
                rm -f "$dest"
            else
                log "$name incomplete (${actual:-0}/${expected} bytes) - resuming"
            fi
        fi
    fi
    log "downloading $name"
    wget -c -O "$dest" "$url" || die "download failed for $name ($url)"
}

fetch "$FLUX_FILL_GGUF" "$FLUX_FILL_GGUF_URL"   # 6.8GB FLUX.1-Fill-dev quant
fetch "$VAE_FILE"       "$VAE_URL"              # shared FLUX VAE
fetch "$CLIP_L_FILE"    "$CLIP_L_URL"           # shared FLUX clip_l encoder
fetch "$T5XXL_FILE"     "$T5XXL_URL"            # shared FLUX t5xxl encoder

# --- remove any stale stopped container with our name -----------------------
if docker ps -a --format '{{.Names}}' | grep -qx "$FLUX_FILL_CONTAINER"; then
    log "removing stale container $FLUX_FILL_CONTAINER"
    docker rm -f "$FLUX_FILL_CONTAINER" >/dev/null
fi

# --- start the sd-server container (EXACT verified flags) -------------------
# Encoders MUST stay on CPU (--clip-on-cpu --vae-on-cpu) or the 12GB card OOMs.
# Host ${FLUX_FILL_PORT} -> container 7864 (the server's internal listen port).
log "starting $FLUX_FILL_CONTAINER on port ${FLUX_FILL_PORT} (encoders pinned to CPU)"
docker run -d --gpus all --name "$FLUX_FILL_CONTAINER" \
    -p "${FLUX_FILL_BIND}:${FLUX_FILL_PORT}:7864" \
    -v "${MODELS_DIR}:/models" \
    "$FLUX_FILL_IMAGE" \
    sd-server --listen-ip 0.0.0.0 --listen-port 7864 \
        --diffusion-model "/models/${FLUX_FILL_GGUF}" \
        --vae "/models/${VAE_FILE}" \
        --clip_l "/models/${CLIP_L_FILE}" \
        --t5xxl "/models/${T5XXL_FILE}" \
        --clip-on-cpu --vae-on-cpu \
    || die "docker run failed"

log "flux-fill started: container $FLUX_FILL_CONTAINER -> http://0.0.0.0:${FLUX_FILL_PORT}"
log "A1111 img2img endpoint: http://0.0.0.0:${FLUX_FILL_PORT}/sdapi/v1/img2img"
log "GPU resident ~6.5GB with encoders on CPU; first inference compiles graphs and is slow"
log "done"
