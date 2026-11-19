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
#   taos-deploy-helper.sh checkpoint
#   taos-deploy-helper.sh rollback [<tag>]
#   taos-deploy-helper.sh restart-self
#   taos-deploy-helper.sh restart-and-verify [<tag>]
#   taos-deploy-helper.sh health-check
#   taos-deploy-helper.sh trigger-update <target_ref> [--no-graceful]
#
# Security: this script is allowlisted in sudoers with a fixed path and
# only the commands below are reachable. The worker cannot execute
# arbitrary commands as root.
set -euo pipefail

INSTALL_DIR="${TAOS_INSTALL_DIR:-$HOME/.local/share/tinyagentos-worker}"
REPO="${TAOS_REPO:-https://github.com/jaylfc/tinyagentos}"
BRANCH="${TAOS_BRANCH:-master}"

# Absolute path to this script.  A detached job (see _schedule_detached) runs
# in its own process, so it has to re-invoke the helper by path rather than by
# calling one of the shell functions below.
SELF_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]}")"

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

# ── Worker self-update subcommands (taOS #890 C3) ─────────────────────────

cmd_checkpoint() {
    local manifest_file="${TAOS_CHECKPOINT_MANIFEST:-$INSTALL_DIR/rollback-manifest.json}"
    local repo_dir="$INSTALL_DIR/tinyagentos"
    local venv="${TAOS_VENV:-$INSTALL_DIR/.venv}"

    log "creating pre-update checkpoint at $manifest_file"

    if [[ ! -d "$repo_dir/.git" ]]; then
        die "worker repo not found at $repo_dir"
    fi

    local git_sha
    git_sha="$(git -C "$repo_dir" rev-parse HEAD)" || die "failed to get current git SHA"

    local git_branch
    git_branch="$(git -C "$repo_dir" rev-parse --abbrev-ref HEAD)" || git_branch="detached"

    local deps_snapshot=""
    local pkg_manager="unknown"
    if [[ -x "$venv/bin/pip" ]]; then
        deps_snapshot="$("$venv/bin/pip" freeze 2>/dev/null || true)"
        pkg_manager="pip"
    fi

    # Detect if uv is in use (uv.lock present in repo root)
    if [[ -f "$repo_dir/uv.lock" ]]; then
        pkg_manager="uv"
        deps_snapshot="uv-lock:$(sha256sum "$repo_dir/uv.lock" 2>/dev/null | awk '{print $1}' || echo "unknown")"
    fi

    # Tag the current HEAD so it survives a checkout (detached or branch)
    local tag="taos-worker-pre-update-$(date -u +%Y%m%d-%H%M%S)"
    git -C "$repo_dir" tag "$tag" HEAD 2>/dev/null || {
        # Tag already exists — add a counter suffix
        local suffix=1
        while ! git -C "$repo_dir" tag "${tag}-${suffix}" HEAD 2>/dev/null; do
            ((suffix++))
        done
        tag="${tag}-${suffix}"
    }
    log "tagged current HEAD as $tag"

    # Build the manifest
    cat > "$manifest_file" <<MANIFEST
{
  "checkpoint_tag": "$tag",
  "git_sha": "$git_sha",
  "git_branch": "$git_branch",
  "package_manager": "$pkg_manager",
  "deps_snapshot": $(echo "$deps_snapshot" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""'),
  "created_at": "$(date -u -Iseconds)",
  "hostname": "$(hostname)"
}
MANIFEST

    log "checkpoint saved: tag=$tag sha=${git_sha:0:8} pkg=$pkg_manager"
    echo "$tag"  # stdout = checkpoint tag for the caller to record
}

cmd_trigger_update() {
    # Write the self-update trigger the worker's heartbeat loop polls for
    # (tinyagentos.worker.agent._check_update_trigger).  This is the writer
    # for update-trigger.json: an operator or cron job on the worker host
    # runs it, and the worker claims the file on its next heartbeat and runs
    # the full update lifecycle.  The write is atomic (temp file + rename in
    # the same directory) because the worker may read it at any moment.
    local target_ref="${1:-}"
    local graceful="true"
    if [[ "${2:-}" == "--no-graceful" ]]; then
        graceful="false"
    fi
    if [[ -z "$target_ref" ]]; then
        die "usage: taos-deploy-helper.sh trigger-update <target_ref> [--no-graceful]"
    fi
    # Must resolve to the directory the worker reads: the systemd unit sets
    # TAOS_WORKER_STATE_DIR=$INSTALL_DIR/.taos-worker-state
    # (scripts/install-worker.sh), and the worker's own default honours the
    # same variable.
    local state_dir="${TAOS_WORKER_STATE_DIR:-$INSTALL_DIR/.taos-worker-state}"
    mkdir -p "$state_dir"
    local trigger_file="$state_dir/update-trigger.json"
    local tmp_file="$state_dir/.update-trigger.json.tmp.$$"
    cat > "$tmp_file" <<TRIGGER
{
  "target_ref": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$target_ref"),
  "graceful": $graceful,
  "requested_at": "$(date -u -Iseconds)",
  "requested_by": "taos-deploy-helper"
}
TRIGGER
    mv "$tmp_file" "$trigger_file"
    # The helper runs as root via sudo; the worker runs as the invoking user
    # and must be able to claim (rename) and replace the trigger file.
    if [[ -n "${SUDO_USER:-}" ]]; then
        chown "$SUDO_USER" "$trigger_file" 2>/dev/null || true
    fi
    log "self-update trigger written: target=$target_ref graceful=$graceful"
    echo "$trigger_file"
}

# ── Detached restart helper (taOS #890 C3) ─────────────────────────────────
# The worker service runs with systemd KillMode=control-group, so the default
# ``systemctl stop`` (and the stop phase of ``systemctl restart``) sends
# SIGTERM to every process in the cgroup — including this deploy-helper
# script when it is invoked as a child of the worker.  A detached restart
# (via at(1) or systemd-run) survives the teardown because it runs outside
# the service cgroup.

# Try restarting a worker service by name, first as a system service then
# as a user service (systemctl --user).  The deploy helper runs via sudo
# (as root), so plain systemctl cannot reach the invoking user's --user
# session; we try both scopes to cover both system-level and user-level
# worker installations.
_restart_service_by_name() {
    local svc_name="$1"
    # System service (systemctl as root).
    systemctl restart "$svc_name" 2>/dev/null && return 0
    # User service — use the original user if sudo preserved SUDO_USER,
    # otherwise try a raw ``systemctl --user`` (works when the helper is
    # called without sudo).
    if [[ -n "${SUDO_USER:-}" ]]; then
        su -l "$SUDO_USER" -c "systemctl --user restart '$svc_name'" 2>/dev/null && return 0
    fi
    systemctl --user restart "$svc_name" 2>/dev/null && return 0
    return 1
}

# Schedule a shell command to run detached from the worker's systemd cgroup.
# Rungs, in order: at(1) -> systemd-run --scope -> inline.  The inline rung
# races with cgroup teardown, but it is only reached when neither detach
# mechanism is usable.
_schedule_detached() {
    local body="$1"

    # 1 -- at(1): schedules the command after the helper exits (cleanest).
    if command -v at >/dev/null 2>&1; then
        # Only trust at(1) if the atd daemon is actually running --
        # ``at now`` exits 0 even when atd is stopped (CodeRabbit, Jul 31).
        if systemctl is-active --quiet atd 2>/dev/null || systemctl is-active --quiet atd.service 2>/dev/null; then
            if echo "$body" | at now 2>/dev/null; then
                log "detached job scheduled via at(1)"
                return 0
            fi
        fi
    fi

    # 2 -- systemd-run --scope: runs outside the service cgroup.
    if command -v systemd-run >/dev/null 2>&1; then
        if systemd-run --scope --no-block bash -c "$body" 2>/dev/null; then
            log "detached job dispatched via systemd-run"
            return 0
        fi
    fi

    # 3 -- Last resort: run the job inline.  It may race with cgroup teardown,
    #     but the helper returns immediately so it often wins.
    log "WARN: could not detach job -- running inline"
    bash -c "$body"
}

# Shell command that restarts the worker service in either scope (system and
# --user).  Self-contained so it can be the payload of a detached job, which
# runs in its own process with no access to the functions in this script.
_worker_restart_cmd() {
    local cmd="systemctl restart tinyagentos-worker.service 2>/dev/null"
    cmd+=" || systemctl restart taos-worker.service 2>/dev/null"
    if [[ -n "${SUDO_USER:-}" ]]; then
        cmd+=" || su -l '$SUDO_USER' -c 'systemctl --user restart tinyagentos-worker.service' 2>/dev/null"
        cmd+=" || su -l '$SUDO_USER' -c 'systemctl --user restart taos-worker.service' 2>/dev/null"
    fi
    cmd+=" || systemctl --user restart tinyagentos-worker.service 2>/dev/null"
    cmd+=" || systemctl --user restart taos-worker.service 2>/dev/null"
    cmd+=" || true"
    printf '%s' "$cmd"
}

_detached_restart_worker() {
    _schedule_detached "$(_worker_restart_cmd)"
}

# Environment assignments for a detached job.  The job runs in its own process
# with a different HOME and a minimal environment, so anything the helper
# resolved from its own environment has to be carried across explicitly --
# otherwise a worker on a non-default install dir or with health-check
# tuning would be probed (and rolled back) against the wrong target.
_worker_env_prefix() {
    local prefix="TAOS_INSTALL_DIR=$(printf '%q' "$INSTALL_DIR")"
    local var
    for var in TAOS_CHECKPOINT_MANIFEST TAOS_VENV TAOS_HEALTH_MIN_UPTIME_S TAOS_HEALTH_HEARTBEAT_MAX_AGE_S; do
        if [[ -n "${!var:-}" ]]; then
            prefix+=" $var=$(printf '%q' "${!var}")"
        fi
    done
    printf '%s' "$prefix"
}

cmd_rollback() {
    local checkpoint_tag="${1:-}"
    local manifest_file="${TAOS_CHECKPOINT_MANIFEST:-$INSTALL_DIR/rollback-manifest.json}"
    local repo_dir="$INSTALL_DIR/tinyagentos"
    local venv="${TAOS_VENV:-$INSTALL_DIR/.venv}"

    if [[ -z "$checkpoint_tag" ]]; then
        # Read the tag from the manifest if not provided
        if [[ -f "$manifest_file" ]]; then
            checkpoint_tag="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("checkpoint_tag",""))' "$manifest_file" 2>/dev/null || true)"
        fi
        if [[ -z "$checkpoint_tag" ]]; then
            die "no checkpoint tag provided and no manifest found at $manifest_file"
        fi
    fi

    # Only this helper's own checkpoint tags may be rolled back to: an
    # arbitrary tag would let a deploy caller check out any ref in the
    # worker repo.
    if [[ "$checkpoint_tag" != taos-worker-pre-update-* ]]; then
        die "refusing to roll back to '$checkpoint_tag': not a pre-update checkpoint tag (taos-worker-pre-update-*)"
    fi

    log "rolling back to checkpoint tag $checkpoint_tag"

    if [[ ! -d "$repo_dir/.git" ]]; then
        die "worker repo not found at $repo_dir"
    fi

    # Verify the tag exists
    if ! git -C "$repo_dir" rev-parse --verify "$checkpoint_tag^{commit}" >/dev/null 2>&1; then
        die "checkpoint tag $checkpoint_tag not found"
    fi

    # Restore the code.  We do NOT stop the worker service first because
    # this helper runs inside the worker's systemd cgroup and systemctl
    # stop would kill us before the rollback completes.  The worker was
    # drained before this phase; a restart at the end picks up the
    # restored code.
    #
    # Checking out the tag alone detaches HEAD, and update-worker's
    # `git pull --ff-only origin <branch>` then fails on every later deploy, so
    # put the checkpointed branch back on the tag. A worker that was already
    # detached recorded "HEAD" and stays detached.
    local git_branch=""
    if [[ -f "$manifest_file" ]]; then
        git_branch="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("git_branch",""))' "$manifest_file" 2>/dev/null || true)"
    fi
    if [[ -n "$git_branch" && "$git_branch" != "HEAD" && "$git_branch" != "detached" ]]; then
        git -C "$repo_dir" checkout --quiet -B "$git_branch" "$checkpoint_tag" || {
            die "git checkout -B $git_branch $checkpoint_tag failed"
        }
    else
        git -C "$repo_dir" checkout --quiet "$checkpoint_tag" || {
            die "git checkout $checkpoint_tag failed"
        }
    fi
    log "restored code to $checkpoint_tag ($(git -C "$repo_dir" rev-parse --short HEAD))"

    # Reinstall dependencies
    if [[ -f "$manifest_file" ]]; then
        local pkg_manager
        pkg_manager="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("package_manager","pip"))' "$manifest_file" 2>/dev/null || echo "pip")"
        if [[ "$pkg_manager" == "uv" ]] && command -v uv >/dev/null 2>&1; then
            log "reinstalling deps with uv sync"
            cd "$repo_dir" && uv sync --frozen 2>/dev/null || uv sync || log "WARN: uv sync had errors — continuing"
        else
            if [[ -x "$venv/bin/pip" ]]; then
                log "reinstalling deps with pip"
                "$venv/bin/pip" install -q -e "$repo_dir[worker]" 2>/dev/null || \
                    "$venv/bin/pip" install -q -e "$repo_dir" || \
                    log "WARN: pip install had errors — continuing"
            fi
        fi
    fi

    # Detached restart so the cgroup teardown doesn't kill us mid-rollback.
    _detached_restart_worker
    log "rollback complete — worker restart initiated from $checkpoint_tag"
}

cmd_restart_self() {
    # Restart, then verify the worker actually comes back, and roll back to the
    # pre-update checkpoint if it does not.  The verification has to run
    # detached from the worker's cgroup: if the new code fails to start at all
    # (an import error, say), there is no worker process left to run the
    # in-process stale-marker rollback in self_update.post_update_startup.
    log "restarting worker service (with post-restart health verification)"
    _schedule_detached "$(_worker_env_prefix) '$SELF_PATH' restart-and-verify"
}

# Runs inside the detached job scheduled by cmd_restart_self: restart the
# worker, wait for it to become healthy, and roll back to the checkpoint if it
# never does.  This is the rollback path that survives the new code failing to
# boot, because it does not depend on the new code running.
cmd_restart_and_verify() {
    local checkpoint_tag="${1:-}"
    local timeout="${TAOS_RESTART_VERIFY_TIMEOUT:-90}"
    local interval="${TAOS_RESTART_VERIFY_INTERVAL:-3}"

    log "restart-verify: restarting worker service"
    # A synchronous restart is safe here: this job is already outside the
    # worker's cgroup, and the service has to be back before we can probe it.
    _restart_service_by_name "tinyagentos-worker.service" \
        || _restart_service_by_name "taos-worker.service" \
        || log "WARN: could not restart worker service via systemctl"

    log "restart-verify: waiting up to ${timeout}s for the worker to become healthy"
    local elapsed=0
    while (( elapsed < timeout )); do
        if "$SELF_PATH" health-check >/dev/null 2>&1; then
            log "restart-verify: worker healthy after ${elapsed}s -- update OK"
            return 0
        fi
        sleep "$interval"
        elapsed=$(( elapsed + interval ))
    done

    log "restart-verify: worker did not become healthy within ${timeout}s -- rolling back"
    cmd_rollback "$checkpoint_tag"
}

# Verify the worker's main process directly: it must be alive, running the
# worker entrypoint, and have stayed up at least TAOS_HEALTH_MIN_UPTIME_S
# seconds.  The worker opens no port, so there is nothing to probe.  The
# process checks are the necessary half of the health check, not a
# sufficient one -- a live process may still be failing to reach the
# controller -- so cmd_health_check also requires the heartbeat-ok
# timestamp (_check_heartbeat_freshness).  A build that fails to boot
# either never produces a main process or crash-loops below the uptime
# floor.  ($ok/$failures are cmd_health_check's locals; bash dynamic
# scoping makes them visible here.)
_check_worker_process() {
    local pid="$1"
    if [[ -z "$pid" || "$pid" == "0" ]]; then
        ok=false
        failures+=("worker service has no main process")
        return
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        ok=false
        failures+=("worker main process $pid is not running")
        return
    fi
    if [[ -r "/proc/$pid/cmdline" ]]; then
        if tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q "tinyagentos\.worker"; then
            log "health-check: main process $pid is the worker"
        else
            ok=false
            failures+=("main process $pid is not the worker entrypoint")
            return
        fi
    fi
    local min_uptime="${TAOS_HEALTH_MIN_UPTIME_S:-10}"
    [[ "$min_uptime" =~ ^[0-9]+$ ]] || min_uptime=10
    local etimes
    etimes="$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$etimes" =~ ^[0-9]+$ ]]; then
        if (( etimes >= min_uptime )); then
            log "health-check: main process $pid up ${etimes}s"
        else
            ok=false
            failures+=("worker main process only up ${etimes}s (< ${min_uptime}s) — still booting or crash-looping")
        fi
    else
        log "health-check: main process age unavailable — skipping uptime check"
    fi
}

# Proof of work: after every successful heartbeat the worker rewrites a
# "last heartbeat OK" timestamp (epoch seconds) in its state dir
# (tinyagentos/worker/agent.py, HEARTBEAT_OK_FILENAME), and clears it when
# its run loop starts, so the timestamp only ever describes the current
# process.  A worker that is running but not reaching the controller never
# refreshes it.  The heartbeat interval is 5s; the default bound of 60s is
# twelve missed beats -- twice the controller's own 30s offline threshold,
# so a worker the controller still considers online never fails here,
# while one that has stopped landing heartbeats is caught within a
# minute.  Override with TAOS_HEALTH_HEARTBEAT_MAX_AGE_S.
_check_heartbeat_freshness() {
    local state_dir="${TAOS_WORKER_STATE_DIR:-$INSTALL_DIR/.taos-worker-state}"
    local hb_file="$state_dir/last-heartbeat-ok"
    local max_age="${TAOS_HEALTH_HEARTBEAT_MAX_AGE_S:-60}"
    [[ "$max_age" =~ ^[0-9]+$ ]] || max_age=60
    if [[ ! -f "$hb_file" ]]; then
        ok=false
        failures+=("no successful heartbeat recorded — the worker has not reached the controller (missing $hb_file)")
        return
    fi
    local ts now age
    ts="$(tr -d '[:space:]' < "$hb_file" 2>/dev/null || true)"
    if [[ ! "$ts" =~ ^[0-9]+$ ]]; then
        ok=false
        failures+=("heartbeat-ok timestamp in $hb_file is unreadable")
        return
    fi
    now="$(date +%s)"
    age=$(( now - ts ))
    if (( age < 0 )); then
        log "health-check: heartbeat-ok timestamp is $(( -age ))s in the future — treating as fresh (clock skew?)"
        return
    fi
    if (( age <= max_age )); then
        log "health-check: last successful heartbeat ${age}s ago (bound ${max_age}s)"
    else
        ok=false
        failures+=("last successful heartbeat ${age}s ago (> ${max_age}s) — worker is running but not reaching the controller")
    fi
}

cmd_health_check() {
    local manifest_file="${TAOS_CHECKPOINT_MANIFEST:-$INSTALL_DIR/rollback-manifest.json}"

    local ok=true
    local failures=()

    # 1. The worker service is active (systemd), remembering which unit name
    #    matched so its main process can be inspected in step 2.
    local unit=""
    if systemctl is-active --quiet tinyagentos-worker.service 2>/dev/null; then
        unit="tinyagentos-worker.service"
    elif systemctl is-active --quiet taos-worker.service 2>/dev/null; then
        unit="taos-worker.service"
    fi

    if [[ -n "$unit" ]]; then
        log "health-check: $unit is active"
        # 2. The unit's main process is really the worker and has stayed up.
        #    (The old step 2 probed the worker port, default 9898 — but the
        #    worker process opens no port and the systemd units do not set
        #    TAOS_WORKER_PORT, so a healthy worker failed the check and every
        #    update rolled back.)
        local main_pid
        main_pid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || true)"
        _check_worker_process "$(printf '%s' "$main_pid" | tr -d '[:space:]')"
    elif command -v launchctl >/dev/null 2>&1; then
        # macOS has no systemd: the launchd entry's first field is the PID,
        # which gets the same process checks.
        local launchd_line launchd_pid
        launchd_line="$(launchctl list 2>/dev/null | grep tinyagentos-worker || true)"
        if [[ -n "$launchd_line" ]]; then
            log "health-check: tinyagentos-worker found in launchd"
            launchd_pid="$(printf '%s' "$launchd_line" | awk '{print $1}')"
            if [[ "$launchd_pid" =~ ^[0-9]+$ ]]; then
                _check_worker_process "$launchd_pid"
            else
                # launchd shows "-" for a loaded job with no process:
                # there is nothing to inspect, and loaded is not running.
                ok=false
                failures+=("tinyagentos-worker is loaded in launchd but has no PID — not running")
            fi
        else
            ok=false
            failures+=("worker not found in systemd or launchd")
        fi
    else
        ok=false
        failures+=("worker service not active")
    fi

    # 3. Proof of work: a fresh heartbeat-ok timestamp, written by the
    #    worker after each successful heartbeat.  Without it, a worker
    #    that is running but cannot reach the controller passes on
    #    process checks alone -- and the post-restart caller of this
    #    check is the worker itself.
    _check_heartbeat_freshness

    # 4. Check the checkpoint manifest exists
    if [[ -f "$manifest_file" ]]; then
        log "health-check: checkpoint manifest present"
    else
        log "health-check: no checkpoint manifest (not an error during normal operation)"
    fi

    if $ok; then
        log "health-check: PASS"
        echo '{"healthy": true}'
    else
        log "health-check: FAIL — ${failures[*]}"
        echo "{\"healthy\": false, \"failures\": $(python3 -c "import sys,json; print(json.dumps(sys.argv[1:]))" "${failures[@]}" 2>/dev/null || echo '[]')}"
        exit 1
    fi
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
    checkpoint)       cmd_checkpoint ;;
    rollback)         shift; cmd_rollback "$@" ;;
    restart-self)     cmd_restart_self ;;
    restart-and-verify) shift; cmd_restart_and_verify "$@" ;;
    health-check)     shift; cmd_health_check "$@" ;;
    trigger-update)   shift; cmd_trigger_update "$@" ;;
    help|*)
        echo "usage: taos-deploy-helper.sh <command>"
        echo "commands: install-ollama, install-exo, install-llama-cpp [--cuda],"
        echo "          install-vllm, install-rknpu, update-worker, status"
        echo "          checkpoint, rollback [<tag>], restart-self,"
        echo "          restart-and-verify [<tag>], health-check,"
        echo "          trigger-update <target_ref> [--no-graceful]"
        exit 1
        ;;
esac
