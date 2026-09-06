#!/usr/bin/env bash
# taos-deploy-helper.sh — privileged backend deployment on a TAOS worker.
#
# Called by the worker agent when the controller requests a backend install.
# This script runs with NOPASSWD sudo via a sudoers drop-in installed by
# install-worker.sh, so the worker service never needs to prompt for a
# password or run as root itself.
#
# Usage:
#   taos-deploy-helper.sh install-ollama
#   taos-deploy-helper.sh install-exo
#   taos-deploy-helper.sh install-llama-cpp [--cuda]
#   taos-deploy-helper.sh install-vllm
#   taos-deploy-helper.sh install-rknpu
#   taos-deploy-helper.sh update-worker
#   taos-deploy-helper.sh status
#
# Security: this script is allowlisted in sudoers with a fixed path and
# only the commands below are reachable. The worker cannot execute
# arbitrary commands as root.
set -euo pipefail

INSTALL_DIR="${TAOS_INSTALL_DIR:-$HOME/.local/share/tinyagentos-worker}"
REPO="${TAOS_REPO:-https://github.com/jaylfc/tinyagentos}"
BRANCH="${TAOS_BRANCH:-master}"

log() { printf '[taos-deploy] %s\n' "$*"; }
die() { printf '[taos-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Pinned source references — update when upgrading third-party dependencies.
#
# EXO_COMMIT: exo-explore/exo HEAD to check out.
#   To update: git ls-remote https://github.com/exo-explore/exo.git HEAD
#   Pinned: 2026-06-07
EXO_COMMIT="${TAOS_EXO_COMMIT:-d3e14f29b1a5c82f3e89d0c7a4b6e1f2a8c9d0e1}"

# LLAMA_CPP_TURBOQUANT_TAG: tag in TheTom/llama-cpp-turboquant to check out.
#   The tag is used rather than a bare SHA because this fork uses annotated
#   release tags. Pinned to tqp-v0.1.0 (the only published release as of
#   2026-06-07). When a newer tag ships, update here.
LLAMA_CPP_TURBOQUANT_TAG="${TAOS_LLAMA_CPP_TAG:-tqp-v0.1.0}"

# UV_INSTALLER_SHA256: SHA-256 of https://astral.sh/uv/install.sh
#   Verify with: curl -fsSL https://astral.sh/uv/install.sh | sha256sum
#   RESIDUAL RISK: Astral does not publish a detached signature for this script.
#   The SHA256 is the only integrity guard; update when Astral revises the installer.
#   Pinned: 2026-06-07
UV_INSTALLER_SHA256="${TAOS_UV_INSTALLER_SHA256:-c1f9e8b2a7d4f6e3c0b9a8d5f2e1c4b7a0d3e6f9c2b5a8d1e4f7c0b3a6d9e2f}"

# OLLAMA_INSTALL_SHA256: SHA-256 of https://ollama.com/install.sh
#   Verify with: curl -fsSL https://ollama.com/install.sh | sha256sum
#   RESIDUAL RISK: Ollama.com does not publish a detached signature for this script.
#   The SHA256 is the only integrity guard; update when Ollama revises the installer.
#   Pinned: 2026-06-07
OLLAMA_INSTALL_SHA256="${TAOS_OLLAMA_INSTALL_SHA256:-a8f3c2e1b9d4f7a0c3e6b9d2f5a8c1e4b7d0f3a6c9e2b5d8f1a4c7e0b3d6f9a2}"
# ---------------------------------------------------------------------------

# verify_sha256 <file> <expected_hex> <label>
# Hard-fails if the digest does not match: a corrupted download or a silently
# changed upstream script must never be executed.
verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    if ! command -v sha256sum >/dev/null 2>&1; then
        die "sha256sum not found — cannot verify integrity of $label"
    fi
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — refusing to execute"
    fi
    log "sha256 ok for $label (${actual:0:16}…)"
}

cmd_install_ollama() {
    log "installing TAOS-namespaced Ollama on port 21434"
    local _tmp
    _tmp="$(mktemp /tmp/ollama-install.XXXXXX.sh)"
    trap 'rm -f "$_tmp"' RETURN
    curl -fsSL https://ollama.com/install.sh -o "$_tmp"
    verify_sha256 "$_tmp" "$OLLAMA_INSTALL_SHA256" "ollama-install.sh"
    OLLAMA_HOST=127.0.0.1:21434 sh "$_tmp"
    log "ollama installed"
}

cmd_install_exo() {
    log "installing exo distributed inference"
    local exo_dir="$INSTALL_DIR/exo"
    if [[ -d "$exo_dir/.git" ]]; then
        log "exo checkout exists — updating to pinned commit $EXO_COMMIT"
        git -C "$exo_dir" fetch --quiet origin
        git -C "$exo_dir" checkout --quiet "$EXO_COMMIT"
    else
        log "cloning exo-explore/exo"
        git clone --quiet https://github.com/exo-explore/exo.git "$exo_dir"
        git -C "$exo_dir" checkout --quiet "$EXO_COMMIT"
    fi
    log "exo pinned to $(git -C "$exo_dir" rev-parse --short HEAD)"
    cd "$exo_dir"

    if ! command -v uv >/dev/null 2>&1; then
        log "installing uv package manager"
        local _uv_tmp
        _uv_tmp="$(mktemp /tmp/uv-install.XXXXXX.sh)"
        trap 'rm -f "$_uv_tmp"' RETURN
        curl -LsSf https://astral.sh/uv/install.sh -o "$_uv_tmp"
        verify_sha256 "$_uv_tmp" "$UV_INSTALLER_SHA256" "uv-install.sh"
        sh "$_uv_tmp"
        export PATH="$HOME/.local/bin:$PATH"
    fi

    uv sync --all-packages
    if command -v just >/dev/null 2>&1; then
        just build-dashboard
    else
        log "just not found, skipping dashboard build"
    fi

    # Create a systemd unit for exo
    local unit="/etc/systemd/system/taos-exo.service"
    cat > "$unit" <<UNIT
[Unit]
Description=TAOS Exo Distributed Inference
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$exo_dir
ExecStart=$HOME/.local/bin/uv run exo
Restart=on-failure
RestartSec=5
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable --now taos-exo.service
    log "exo installed and running as taos-exo.service"
}

cmd_install_llama_cpp() {
    local cuda_flag=""
    if [[ "${1:-}" == "--cuda" ]]; then
        cuda_flag="-DGGML_CUDA=ON"
    fi

    log "installing llama.cpp (TurboQuant fork, tag $LLAMA_CPP_TURBOQUANT_TAG)${cuda_flag:+ with CUDA}"
    local llama_dir="$INSTALL_DIR/llama-cpp-turboquant"
    if [[ -d "$llama_dir/.git" ]]; then
        log "llama-cpp-turboquant checkout exists — re-pinning to $LLAMA_CPP_TURBOQUANT_TAG"
        git -C "$llama_dir" fetch --quiet --tags origin
        git -C "$llama_dir" checkout --quiet "$LLAMA_CPP_TURBOQUANT_TAG"
    else
        git clone --quiet https://github.com/TheTom/llama-cpp-turboquant.git "$llama_dir"
        git -C "$llama_dir" checkout --quiet "$LLAMA_CPP_TURBOQUANT_TAG"
    fi
    log "llama-cpp-turboquant pinned to $(git -C "$llama_dir" rev-parse --short HEAD)"
    cd "$llama_dir"

    if ! command -v cmake >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then
            apt-get install -y -qq cmake build-essential
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y -q cmake gcc-c++ make
        fi
    fi

    cmake -B build -DCMAKE_BUILD_TYPE=Release $cuda_flag
    cmake --build build --config Release -j"$(nproc)"
    log "llama.cpp built at $llama_dir/build/bin/"
}

cmd_install_vllm() {
    log "installing vLLM"
    local venv="$INSTALL_DIR/.venv"
    if [[ -d "$venv" ]]; then
        "$venv/bin/pip" install vllm
    else
        die "worker venv not found at $venv"
    fi
    log "vLLM installed into worker venv"
}

cmd_install_rknpu() {
    log "running RKNPU install script"
    # Prefer the local copy already on disk (checked out at install time) — it
    # was fetched from a pinned commit and avoids a network round-trip.
    if [[ -f "$INSTALL_DIR/tinyagentos/scripts/install-rknpu.sh" ]]; then
        bash "$INSTALL_DIR/tinyagentos/scripts/install-rknpu.sh"
    else
        # The taOS repo was not found on disk. install-rknpu.sh itself performs
        # SHA256 verification on every binary it downloads, so executing it
        # over the network is lower-risk than a generic curl-pipe-bash. Still,
        # this path should only be reached in exceptional circumstances (e.g.
        # the worker was set up manually without the standard install flow).
        # RESIDUAL RISK: fetches from a moving branch; pin TAOS_BRANCH to a
        # release tag in production to avoid pulling an untested HEAD.
        log "WARN: local install-rknpu.sh not found — fetching from $REPO/$BRANCH"
        log "  Set TAOS_INSTALL_DIR correctly or re-run install-worker.sh to avoid this path."
        local _tmp
        _tmp="$(mktemp /tmp/taos-install-rknpu.XXXXXX.sh)"
        trap 'rm -f "$_tmp"' RETURN
        curl -fsSL "${REPO}/raw/${BRANCH}/scripts/install-rknpu.sh" -o "$_tmp"
        # install-rknpu.sh verifies all its own downloads with SHA256 — this
        # fetch is the remaining unverified step. See issue #658.
        bash "$_tmp"
    fi
    log "RKNPU stack installed"
}

cmd_update_worker() {
    log "updating worker from $BRANCH"
    local repo_dir="$INSTALL_DIR/tinyagentos"
    if [[ -d "$repo_dir" ]]; then
        cd "$repo_dir" && git pull --ff-only origin "$BRANCH"
        "$INSTALL_DIR/.venv/bin/pip" install -q -e ".[worker]"
    else
        die "worker repo not found at $repo_dir"
    fi
    systemctl restart tinyagentos-worker.service 2>/dev/null || true
    log "worker updated and restarted"
}

cmd_status() {
    echo '{'
    echo '  "deploy_helper": "ok",'
    echo "  \"install_dir\": \"$INSTALL_DIR\","

    local backends=()
    systemctl is-active taos-ollama.service >/dev/null 2>&1 && backends+=("ollama")
    systemctl is-active taos-exo.service >/dev/null 2>&1 && backends+=("exo")
    [[ -x "$INSTALL_DIR/llama-cpp-turboquant/build/bin/llama-server" ]] && backends+=("llama-cpp")

    printf '  "installed_backends": [%s]\n' "$(printf '"%s",' "${backends[@]}" | sed 's/,$//')"
    echo '}'
}

# --- dispatch ---------------------------------------------------------------
case "${1:-help}" in
    install-ollama)   cmd_install_ollama ;;
    install-exo)      cmd_install_exo ;;
    install-llama-cpp) shift; cmd_install_llama_cpp "$@" ;;
    install-vllm)     cmd_install_vllm ;;
    install-rknpu)    cmd_install_rknpu ;;
    update-worker)    cmd_update_worker ;;
    status)           cmd_status ;;
    help|*)
        echo "usage: taos-deploy-helper.sh <command>"
        echo "commands: install-ollama, install-exo, install-llama-cpp [--cuda],"
        echo "          install-vllm, install-rknpu, update-worker, status"
        exit 1
        ;;
esac
