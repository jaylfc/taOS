#!/usr/bin/env bash
# taOS service installer for stable-diffusion.cpp (id: stable-diffusion-cpp)
# ---------------------------------------------------------------------------
# Upstream: https://github.com/leejet/stable-diffusion.cpp  (MIT)
# Pure C/C++ image generation. Builds the `sd-server` HTTP server target and
# leaves it runnable on port 7861 (override: TAOS_SD_CPP_PORT).
#
# Every build step is taken verbatim from the OFFICIAL upstream docs:
#   - Clone / cmake build:  docs/build.md
#       https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/build.md
#       CPU=(no flag)  CUDA=-DSD_CUDA=ON  Vulkan=-DSD_VULKAN=ON
#       ROCm=-DSD_HIPBLAS=ON  Metal=-DSD_METAL=ON
#   - Server binary + flags: examples/server/README.md + examples/server/main.cpp
#       binary: build/bin/sd-server ; flags: --listen-ip / --listen-port
#       (upstream defaults 127.0.0.1:1234; we bind 0.0.0.0:7861)
#
# stable-diffusion.cpp has NO NPU backend (docs list only CUDA/Vulkan/Metal/
# SYCL/OpenCL/CPU), so the arm-npu and cpu-only tiers both build the CPU path.
#
# Pinned upstream release (update tag + SHA together when bumping):
#   SD_CPP_TAG  = release tag, verified to exist 2026-06-14
#   SD_CPP_SHA256 = sha256 of the GitHub source tarball for that tag
#   Verify with:
#     curl -fsSL https://github.com/leejet/stable-diffusion.cpp/archive/refs/tags/<TAG>.tar.gz | sha256sum
# ---------------------------------------------------------------------------
set -euo pipefail

# --- taOS contract --------------------------------------------------------
PROJECT_DIR="${1:-$PWD}"
SD_CPP_TAG="${TAOS_SD_CPP_TAG:-master-700-c2df4e1}"
SD_CPP_SHA256="${TAOS_SD_CPP_SHA256:-7b859e9d5cb5f84b86dcb8e2dd4badf49d8e53a9743f2d1551a9fbae8f011d83}"
SD_CPP_PORT="${TAOS_SD_CPP_PORT:-30450}"

# Install root lives under the taOS project_dir so it is self-contained.
SD_CPP_ROOT="${PROJECT_DIR}/services/stable-diffusion-cpp"
SRC_DIR="${SD_CPP_ROOT}/src"
BUILD_DIR="${SRC_DIR}/build"
SERVER_BIN="${BUILD_DIR}/bin/sd-server"
RUN_SCRIPT="${SD_CPP_ROOT}/run.sh"

log() { echo -e "\033[1;34m[sd-cpp]\033[0m $*"; }
die() { echo -e "\033[1;31m[sd-cpp]\033[0m $*" >&2; exit 1; }

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$file" | awk '{print $1}')"
    else
        die "no sha256sum/shasum available to verify $label"
    fi
    [[ "$actual" == "$expected" ]] \
        || die "sha256 mismatch for $label: expected $expected, got $actual — refusing to continue"
    log "sha256 ok for $label (${actual:0:16}…)"
}

# --- idempotency ----------------------------------------------------------
# If the server binary already exists and reports a version, we are done.
if [[ -x "$SERVER_BIN" ]] && "$SERVER_BIN" --version >/dev/null 2>&1; then
    log "sd-server already built at $SERVER_BIN — nothing to do"
    log "launch with: $RUN_SCRIPT   (port ${SD_CPP_PORT})"
    exit 0
fi

# --- prerequisites --------------------------------------------------------
for tool in git cmake curl; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool '$tool' not found on PATH"
done
command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || command -v clang >/dev/null 2>&1 \
    || die "no C compiler (cc/gcc/clang) found"

# --- backend autodetect ---------------------------------------------------
# Maps to docs/build.md cmake flags. Order: CUDA > ROCm > Vulkan > Metal > CPU.
CMAKE_BACKEND_FLAGS=()
BACKEND="cpu"
OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
    # Apple platforms: Metal per docs/build.md (-DSD_METAL=ON).
    CMAKE_BACKEND_FLAGS=(-DSD_METAL=ON)
    BACKEND="metal"
elif command -v nvcc >/dev/null 2>&1 || [[ -d /usr/local/cuda ]] || command -v nvidia-smi >/dev/null 2>&1; then
    CMAKE_BACKEND_FLAGS=(-DSD_CUDA=ON)
    BACKEND="cuda"
elif command -v hipcc >/dev/null 2>&1 || command -v rocminfo >/dev/null 2>&1; then
    # docs/build.md ROCm recipe: detect GPU target, build with clang + Ninja.
    GFX_NAME=""
    if command -v rocminfo >/dev/null 2>&1; then
        GFX_NAME="$(rocminfo 2>/dev/null | awk '/ *Name: +gfx[1-9]/ {print $2; exit}')"
    fi
    CMAKE_BACKEND_FLAGS=(-DSD_HIPBLAS=ON -DCMAKE_BUILD_TYPE=Release
                         -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON)
    command -v clang   >/dev/null 2>&1 && CMAKE_BACKEND_FLAGS+=(-DCMAKE_C_COMPILER=clang)
    command -v clang++ >/dev/null 2>&1 && CMAKE_BACKEND_FLAGS+=(-DCMAKE_CXX_COMPILER=clang++)
    if [[ -n "$GFX_NAME" ]]; then
        CMAKE_BACKEND_FLAGS+=(-DGPU_TARGETS="$GFX_NAME" -DAMDGPU_TARGETS="$GFX_NAME")
    fi
    command -v ninja >/dev/null 2>&1 && CMAKE_BACKEND_FLAGS+=(-G Ninja)
    BACKEND="rocm"
elif command -v glslc >/dev/null 2>&1 || command -v vulkaninfo >/dev/null 2>&1 \
        || ldconfig -p 2>/dev/null | grep -qi 'libvulkan\.so'; then
    CMAKE_BACKEND_FLAGS=(-DSD_VULKAN=ON)
    BACKEND="vulkan"
else
    # arm-npu and cpu-only tiers land here: upstream has no NPU backend.
    BACKEND="cpu"
fi
log "selected backend: ${BACKEND} (flags: ${CMAKE_BACKEND_FLAGS[*]:-none})"

# --- fetch pinned source (verified tarball, never curl|bash) ---------------
mkdir -p "$SD_CPP_ROOT"
if [[ ! -f "${SRC_DIR}/CMakeLists.txt" ]]; then
    log "downloading stable-diffusion.cpp ${SD_CPP_TAG}"
    TARBALL="$(mktemp "${TMPDIR:-/tmp}/sd-cpp.XXXXXX.tar.gz")"
    trap 'rm -f "$TARBALL"' EXIT
    curl -fsSL \
        "https://github.com/leejet/stable-diffusion.cpp/archive/refs/tags/${SD_CPP_TAG}.tar.gz" \
        -o "$TARBALL"
    verify_sha256 "$TARBALL" "$SD_CPP_SHA256" "stable-diffusion.cpp ${SD_CPP_TAG}"

    EXTRACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sd-cpp-x.XXXXXX")"
    tar -xzf "$TARBALL" -C "$EXTRACT_DIR"
    INNER="$(find "$EXTRACT_DIR" -maxdepth 1 -mindepth 1 -type d | head -1)"
    [[ -n "$INNER" && -f "${INNER}/CMakeLists.txt" ]] || die "unexpected tarball layout"
    rm -rf "$SRC_DIR"
    mv "$INNER" "$SRC_DIR"
    rm -rf "$EXTRACT_DIR"
    rm -f "$TARBALL"
    trap - EXIT
    log "source extracted to $SRC_DIR"
else
    log "source already present at $SRC_DIR"
fi

# NOTE: The verified GitHub *tarball* bundles the ggml submodule already; no
# network 'git submodule' step is needed (clone --recursive is only required
# for a fresh git clone per docs/build.md).

# --- build ----------------------------------------------------------------
# docs/build.md:  cmake .. <backend-flag> ; cmake --build . --config Release
# examples/server/CMakeLists.txt builds the `sd-server` target. We disable the
# pnpm-built JS frontend (-DSD_SERVER_BUILD_FRONTEND=OFF) so the build needs no
# Node toolchain; the HTTP API still serves fully.
JOBS="$( (command -v nproc >/dev/null 2>&1 && nproc) || sysctl -n hw.ncpu 2>/dev/null || echo 2 )"
log "configuring (cmake) — this can take a while for GPU backends"
cmake -S "$SRC_DIR" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DSD_SERVER_BUILD_FRONTEND=OFF \
    "${CMAKE_BACKEND_FLAGS[@]}"

log "building sd-server target (-j${JOBS})"
cmake --build "$BUILD_DIR" --config Release --target sd-server -j "${JOBS}"

[[ -x "$SERVER_BIN" ]] || {
    # Some generators place binaries under build/bin/Release; locate it.
    FOUND="$(find "$BUILD_DIR" -name 'sd-server' -type f -perm -u+x 2>/dev/null | head -1)"
    [[ -n "$FOUND" ]] || die "build finished but sd-server binary not found under $BUILD_DIR"
    SERVER_BIN="$FOUND"
}
log "built sd-server: $SERVER_BIN"

# --- runnable server wrapper bound to the service port --------------------
# examples/server/README.md:  --listen-ip <ip> --listen-port <port>
# (upstream requires listen-ip to be set; we bind all interfaces for taOS.)
cat > "$RUN_SCRIPT" <<RUN
#!/usr/bin/env bash
set -euo pipefail
PORT="\${TAOS_SD_CPP_PORT:-${SD_CPP_PORT}}"
HOST="\${TAOS_SD_CPP_HOST:-0.0.0.0}"
# Model is supplied at runtime, e.g.:
#   TAOS_SD_CPP_ARGS="--diffusion-model /path/model.gguf --vae /path/ae.sft --llm /path/te.safetensors"
exec "${SERVER_BIN}" --listen-ip "\$HOST" --listen-port "\$PORT" \${TAOS_SD_CPP_ARGS:-}
RUN
chmod +x "$RUN_SCRIPT"

log "install complete (backend: ${BACKEND})"
log "binary : ${SERVER_BIN}"
log "run    : ${RUN_SCRIPT}   →  http://0.0.0.0:${SD_CPP_PORT}"
log "models : pass models via TAOS_SD_CPP_ARGS (see run.sh header)"
exit 0
