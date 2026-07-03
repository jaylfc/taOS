#!/usr/bin/env bash
# taOS llama.cpp server installer — the default local LLM backend on
# NVIDIA CUDA, AMD ROCm, Apple Silicon (Metal), and x86 CPU-only.
#
# llama.cpp (MIT) ships a built-in "router mode" (llama-server
# --models-dir <dir>) that serves chat, /v1/embeddings and /v1/rerank
# from one process, auto-discovering whatever GGUF files are dropped into
# the models directory — no per-model config needed here. Router mode is
# young (announced ~May 2026; this build's own log literally says
# "router mode is experimental"), so this script health-gates hard on
# /health before ever reporting success.
#
# Previous version of this script built llama.cpp from source on every
# install (5-15 min, needs cmake + a C++ toolchain, and never created a
# service or health-gated anything). That's now a documented fallback
# only (see TAOS_LLAMACPP_BUILD_FROM_SOURCE below) — the default path
# downloads the pinned official release binary for the detected
# platform, matching the rest of the fleet's install scripts
# (install-rknpu.sh, install-rk-llama-cpp.sh).
#
# This is the Store's install.method=script entrypoint for the
# `llama-cpp` service manifest (app-catalog/services/llama-cpp/manifest.yaml)
# — the ScriptInstaller runs it non-interactively as
# `bash install-llama-cpp.sh <project_dir>`, mirroring
# install-rk-llama-cpp.sh's contract for the Rockchip backend. Rockchip
# NPU boards never reach this script: they keep rkllama / rk-llama.cpp
# (install-rknpu.sh / install-rk-llama-cpp.sh) untouched.
#
# Usage:
#     bash scripts/install-llama-cpp.sh [project_dir]
#
# Environment overrides:
#     TAOS_LLAMACPP_DIR          install dir for the binary (default: ~<user>/llama-cpp)
#     TAOS_LLAMACPP_MODELS_DIR   models dir router mode scans (default: <project_dir>/data/llama-cpp/models)
#     TAOS_LLAMACPP_PORT         HTTP port (default: 7835 — see port_allocator.py)
#     TAOS_LLAMACPP_HOST         bind address (default: 127.0.0.1 — loopback only)
#     TAOS_LLAMACPP_VARIANT      override auto-detected variant: cuda|rocm|apple-silicon|cpu
#
# Safety:
#   - No models are downloaded here — models go through the per-model
#     Store flow with their own license acceptance. This script installs
#     exactly one MIT-licensed binary.
#   - Idempotent: re-running skips the download if the binary is already
#     present, but always rewrites the unit/plist and (re)starts it — a
#     binary-present-but-service-missing-or-dead box self-heals.
#   - HEALTH GATE: exits non-zero (and does not report success) unless
#     the service actually answers GET /health within the timeout below.
#     Verified against a real b9867 macOS build: router mode answers
#     /health with {"status":"ok"} even with ZERO models in the
#     directory (llama-server logs "Available models (0)" and keeps
#     serving) — so the gate below is a plain health check, no
#     "will start once a model exists" fallback needed.
set -euo pipefail

log()  { printf '\033[1;34m[llama-cpp]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[llama-cpp]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[llama-cpp]\033[0m %s\n' "$*" >&2; exit 1; }

PROJECT_DIR="${1:-$(pwd)}"

# -------- pinned release ---------------------------------------------------
# llama.cpp (ggml-org/llama.cpp) tags a new bNNNN build several times a
# day — there is no separate "stable" channel. b9867 was the newest tag
# at the time this pin was written (2026-07-03); router mode is
# experimental upstream, so every asset below was hand-verified (download
# + sha256 + a real run of llama-server --models-dir against an empty
# directory) before being pinned here. Bump LLAMACPP_VERSION (and the
# per-variant sha256 below) deliberately, not automatically.
LLAMACPP_VERSION="b9867"
BASE_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMACPP_VERSION}"

PORT="${TAOS_LLAMACPP_PORT:-7835}"
HOST="${TAOS_LLAMACPP_HOST:-127.0.0.1}"

# -------- sha256 helper (Linux sha256sum vs macOS shasum) -------------------
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "neither sha256sum nor shasum available — cannot verify download integrity"
    fi
}

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    actual="$(sha256_of "$file")"
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — corrupted download or tampered release, refusing to install"
    fi
    log "sha256 ok for $label (${actual:0:12}…)"
}

# -------- (1) platform + variant detection ----------------------------------
#
# nvidia -> CUDA build, amd -> ROCm/HIP build, apple-silicon -> Metal,
# else -> CPU. TAOS_LLAMACPP_VARIANT overrides detection entirely.
detect_variant() {
    if [[ -n "${TAOS_LLAMACPP_VARIANT:-}" ]]; then
        VARIANT="$TAOS_LLAMACPP_VARIANT"
        log "variant forced via TAOS_LLAMACPP_VARIANT=$VARIANT"
        return
    fi

    local os arch
    os="$(uname -s)"
    arch="$(uname -m)"

    if [[ "$os" == "Darwin" ]]; then
        [[ "$arch" == "arm64" ]] || die "install-llama-cpp.sh on macOS only supports Apple Silicon (arm64) — got $arch"
        VARIANT="apple-silicon"
        log "detected platform: macOS Apple Silicon -> Metal build"
        return
    fi

    [[ "$os" == "Linux" ]] || die "unsupported OS: $os (install-llama-cpp.sh supports Linux and macOS only)"
    [[ "$arch" == "x86_64" ]] || die "install-llama-cpp.sh only supports x86_64 Linux — got $arch. Rockchip ARM boards use install-rknpu.sh / install-rk-llama-cpp.sh instead."

    if [[ -d /opt/rocm ]]; then
        VARIANT="rocm"
        log "detected platform: Linux x86_64, /opt/rocm present -> ROCm build"
    elif command -v nvidia-smi >/dev/null 2>&1 || [[ -d /proc/driver/nvidia/gpus ]]; then
        VARIANT="cuda"
        log "detected platform: Linux x86_64, NVIDIA driver present -> CUDA-target build"
    else
        VARIANT="cpu"
        log "detected platform: Linux x86_64, no GPU driver found -> CPU-only build"
    fi
}

# -------- (2) resolve asset + sha256 for the detected variant ---------------
resolve_asset() {
    case "$VARIANT" in
        apple-silicon)
            ASSET="llama-${LLAMACPP_VERSION}-bin-macos-arm64.tar.gz"
            ASSET_SHA256="8614dce043dcf54150185c6568c0fa092f8cfd2944617aac305e70a8ce1027e3"
            ;;
        rocm)
            # AMD-validated official ROCm/HIP Linux build.
            ASSET="llama-${LLAMACPP_VERSION}-bin-ubuntu-rocm-7.2-x64.tar.gz"
            ASSET_SHA256="53b1c78c0afd096febea64f323f6cb33fe707fb3f5ece384dd48646901108c69"
            ;;
        cuda)
            # ggml-org/llama.cpp does not currently publish a prebuilt
            # Linux+CUDA binary (only Windows CUDA zips exist in release
            # b9867's asset list — verified directly against the GitHub
            # API). Use the official Linux Vulkan build instead: it runs
            # on NVIDIA GPUs via the system's Vulkan driver, needs no CUDA
            # toolkit, and is still an official prebuilt (not a source
            # build). If you specifically need a true CUDA build, set
            # TAOS_LLAMACPP_BUILD_FROM_SOURCE=1 (documented fallback,
            # below) rather than waiting on an upstream Linux+CUDA asset.
            ASSET="llama-${LLAMACPP_VERSION}-bin-ubuntu-vulkan-x64.tar.gz"
            ASSET_SHA256="f436e38d10eb53815ee5d6af14f286a7f90339960d060c1391bbf41a84d4a018"
            warn "ggml-org/llama.cpp publishes no Linux+CUDA release binary; using the official Vulkan build (runs on NVIDIA GPUs, no CUDA toolkit required)"
            ;;
        cpu)
            ASSET="llama-${LLAMACPP_VERSION}-bin-ubuntu-x64.tar.gz"
            ASSET_SHA256="a2dc4404fb43f2bb7818fd3a0602b0e59dfe67ab972ce79b3479ad764249fbd4"
            ;;
        *)
            die "unknown variant: $VARIANT (expected cuda|rocm|apple-silicon|cpu)"
            ;;
    esac
}

# Documented fallback, not default (per taOS policy): a true source build
# with CUDA support, only when explicitly requested.
#
#   TAOS_LLAMACPP_BUILD_FROM_SOURCE=1 bash scripts/install-llama-cpp.sh
#
# would need, roughly (not implemented here — advanced users only):
#   git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
#   git checkout "$LLAMACPP_VERSION"
#   cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
#   cmake --build build --config Release -j"$(nproc)" --target llama-server
if [[ "${TAOS_LLAMACPP_BUILD_FROM_SOURCE:-0}" == "1" ]]; then
    die "TAOS_LLAMACPP_BUILD_FROM_SOURCE=1 is a documented manual fallback, not an automated path — see the comment above main() in this script for the exact cmake invocation, then point TAOS_LLAMACPP_DIR/llama-server at the result."
fi

# -------- (3) resolve install dir + models dir ------------------------------
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    TARGET_USER="$SUDO_USER"
else
    TARGET_USER="$(id -un)"
fi
TARGET_HOME="$(eval echo "~$TARGET_USER")"
[[ -d "$TARGET_HOME" ]] || die "cannot resolve home directory for user $TARGET_USER"
TARGET_GROUP="$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")"

INSTALL_DIR="${TAOS_LLAMACPP_DIR:-$TARGET_HOME/llama-cpp}"
MODELS_DIR="${TAOS_LLAMACPP_MODELS_DIR:-$PROJECT_DIR/data/llama-cpp/models}"

run_as_user() {
    if [[ "$(id -un)" == "$TARGET_USER" ]]; then
        "$@"
    else
        sudo -u "$TARGET_USER" -H "$@"
    fi
}

# -------- (4) download + extract --------------------------------------------
download_and_extract() {
    local url="${BASE_URL}/${ASSET}"
    local tmp
    tmp="$(mktemp -t llamacpp.XXXXXX.tar.gz)"
    trap 'rm -f "$tmp"' RETURN

    log "downloading $url"
    curl -fSL --retry 3 --retry-delay 2 -o "$tmp" "$url" \
        || die "failed to download $url"
    verify_sha256 "$tmp" "$ASSET_SHA256" "$ASSET"

    run_as_user mkdir -p "$INSTALL_DIR"
    log "extracting into $INSTALL_DIR"
    # Release tarballs wrap everything in a top-level llama-b<ver>/ dir —
    # strip it so the binary + shared libs land directly under INSTALL_DIR.
    run_as_user tar -xzf "$tmp" -C "$INSTALL_DIR" --strip-components=1
    chmod +x "$INSTALL_DIR/llama-server" 2>/dev/null || true
}

# -------- (5) service unit (systemd on Linux, launchd on macOS) ------------
install_linux_service() {
    local unit="/etc/systemd/system/llama-cpp.service"
    log "writing $unit (port $PORT)"
    sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=llama.cpp server (router mode) — taOS default local LLM backend
After=network.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$INSTALL_DIR
Environment=LD_LIBRARY_PATH=$INSTALL_DIR
ExecStartPre=-/usr/bin/pkill -9 -f $INSTALL_DIR/llama-server
ExecStart=$INSTALL_DIR/llama-server --models-dir $MODELS_DIR --host $HOST --port $PORT
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now llama-cpp.service
    log "llama-cpp.service enabled + started"
}

install_macos_service() {
    local label="com.taos.llama-cpp"
    local agents_dir="$TARGET_HOME/Library/LaunchAgents"
    local plist="$agents_dir/${label}.plist"
    run_as_user mkdir -p "$agents_dir"

    log "writing $plist (port $PORT)"
    run_as_user tee "$plist" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/llama-server</string>
        <string>--models-dir</string><string>${MODELS_DIR}</string>
        <string>--host</string><string>${HOST}</string>
        <string>--port</string><string>${PORT}</string>
    </array>
    <key>WorkingDirectory</key><string>${INSTALL_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DYLD_LIBRARY_PATH</key><string>${INSTALL_DIR}</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>${INSTALL_DIR}/llama-cpp.log</string>
    <key>StandardErrorPath</key><string>${INSTALL_DIR}/llama-cpp.err.log</string>
</dict>
</plist>
EOF

    local uid
    uid="$(id -u "$TARGET_USER")"
    pkill -9 -f "$INSTALL_DIR/llama-server" 2>/dev/null || true
    run_as_user launchctl bootout "gui/$uid/${label}" 2>/dev/null || true
    run_as_user launchctl bootstrap "gui/$uid" "$plist"
    run_as_user launchctl enable "gui/$uid/${label}"
    run_as_user launchctl kickstart -k "gui/$uid/${label}"
    log "${label} loaded + started via launchd"
}

install_service() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        install_macos_service
    else
        install_linux_service
    fi
}

# -------- (6) health gate ----------------------------------------------------
wait_for_health() {
    local i
    for (( i = 0; i < 60; i++ )); do
        if curl -fs "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
            log "llama-server /health is up on http://${HOST}:${PORT}"
            return 0
        fi
        sleep 1
    done
    return 1
}

# -------- main ----------------------------------------------------------------
main() {
    detect_variant
    resolve_asset

    run_as_user mkdir -p "$MODELS_DIR"

    if [[ -x "$INSTALL_DIR/llama-server" ]]; then
        log "llama-server binary already present at $INSTALL_DIR — skipping download"
    else
        download_and_extract
    fi

    # Always (re)write + (re)enable the service — this is the self-heal
    # path: a binary present but a missing/dead unit gets recreated and
    # started, exactly like re-clicking Install in the Store.
    install_service

    if ! wait_for_health; then
        die "llama-server did not answer http://${HOST}:${PORT}/health within 60s — check the service logs (journalctl -u llama-cpp on Linux, $INSTALL_DIR/llama-cpp.err.log on macOS)"
    fi

    cat <<EOF

  =================================================================
  llama.cpp server installed successfully (router mode)
  =================================================================
    variant:       $VARIANT
    binary dir:    $INSTALL_DIR
    models dir:    $MODELS_DIR (drop GGUF files here — no models installed by this script)
    HTTP endpoint: http://${HOST}:${PORT}
    pinned build:  $LLAMACPP_VERSION

EOF
}

main "$@"
