#!/usr/bin/env bash
#
# taOS MLX (Apple Silicon) serving runtime: the launchd agent for mlx_lm.server.
#
# MLX is Apple's array framework for Apple Silicon and `mlx_lm.server` is the
# OpenAI-compatible HTTP server on top of it (the surface the taOS `mlx` backend
# adapter talks to: /v1/chat/completions, /v1/completions, /v1/models). Unlike
# llama.cpp there is no router mode: ONE `mlx_lm.server` process serves exactly
# ONE model, so this script pins a model in its own user launchd agent instead
# of pointing a server at a directory of them. One agent per model is what makes
# serving several MLX models at once possible -- installing model B must not
# re-point (and silently stop) the agent that serves model A:
#
#   ~/Library/LaunchAgents/com.taos.mlx-server-<app_id>.plist
#     <venv>/bin/mlx_lm.server --model <model dir> --host 127.0.0.1 --port <port>
#
# The label is derived from the model directory's name (the app_id in
# <models root>/mlx/<family>/<app_id>), so N installs collide neither in the
# label nor in the plist. The port is chosen by MLXInstaller (one per model,
# 7837 for the first / only model, a free high port afterwards) and passed in
# with --port. A pre-existing single-agent install from taOS #3337
# (`com.taos.mlx-server.plist`, the legacy label) is retired when it pins the
# same model, so the upgrade lands on the per-model label without serving the
# model twice.
#
# RunAtLoad + KeepAlive bring a model back after a re-login or a crash, the same
# way scripts/install-llama-cpp.sh's unit does on Linux and install-worker.sh
# does for the worker. The venv is the pinned, hash-verified runtime MLXInstaller
# provisions (`<install root>/apps/mlx-runtime/venv`, override TAOS_MLX_VENV).
#
# Usage:
#   scripts/install-mlx-server.sh --model <dir> [--venv <dir>] [--port 7837]
#                                 [--host 127.0.0.1] [--health-timeout 90]
#   scripts/install-mlx-server.sh --uninstall [--model <dir>]
#
# Exit 0 means the agent is loaded AND `GET /v1/models` answered on the port.
# Anything else exits non-zero and says why, so a model install that cannot get
# the server up reports no endpoint at all, never an endpoint nothing serves.
# `--uninstall` uses the same idea for its three outcomes: 0 when nothing serves
# that model any more (unloaded, or there was no agent), 3 when an agent was
# left running because it pins a different model, 1 on error.
set -euo pipefail

# The legacy single-agent label from taOS #3337. New installs use a per-model
# label; this one is still recognised so an upgrade retires it cleanly.
LEGACY_LABEL="com.taos.mlx-server"
LABEL=""
DEFAULT_PORT=7837
HOST="127.0.0.1"
PORT="${TAOS_MLX_PORT:-$DEFAULT_PORT}"
MODEL_DIR=""
VENV_DIR="${TAOS_MLX_VENV:-}"
HEALTH_TIMEOUT=90
UNINSTALL=0

log()  { printf '\033[1;34m[mlx-server]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mlx-server]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[mlx-server]\033[0m %s\n' "$*" >&2; exit 1; }

# The plist is XML, so any value interpolated into it has to be escaped: a
# perfectly legal model directory like "Models & Data" would otherwise produce
# malformed XML and launchctl would refuse to bootstrap the agent. The same
# escaping is applied when matching a path against an existing plist below.
xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# The per-model launchd label. The model directory's basename is the app_id in
# the shared `<models root>/mlx/<family>/<app_id>` layout, and launchd label
# characters are restricted to [A-Za-z0-9._-], so anything else becomes '-'.
label_for_model() {
    local slug
    if [[ -z "$MODEL_DIR" ]]; then
        printf '%s\n' "$LEGACY_LABEL"
        return
    fi
    slug="$(basename "$MODEL_DIR")"
    slug="$(printf '%s' "$slug" | sed -e 's/[^A-Za-z0-9._-]/-/g')"
    [[ -n "$slug" ]] || slug="model"
    printf '%s\n' "${LEGACY_LABEL}-${slug}"
}

usage() {
    cat <<'EOF'
taOS MLX serving runtime: a launchd agent that serves one MLX model.

  install-mlx-server.sh --model <dir> [--venv <dir>] [--port 7837]
                        [--host 127.0.0.1] [--health-timeout 90]
  install-mlx-server.sh --uninstall [--model <dir>]

  --model <dir>       model directory mlx_lm.server should serve (required)
  --venv <dir>        runtime venv holding bin/mlx_lm.server
                      (default: TAOS_MLX_VENV, else <repo>/apps/mlx-runtime/venv)
  --port <n>          port to serve on (default: TAOS_MLX_PORT, else 7837)
  --host <addr>       address to bind (default: 127.0.0.1)
  --health-timeout <n>  seconds to wait for GET /v1/models (default: 90)
  --uninstall         unload the agent (and remove its plist); with --model,
                      only the agents that pin that model
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --model)          MODEL_DIR="${2:-}"; shift 2 ;;
            --venv)           VENV_DIR="${2:-}"; shift 2 ;;
            --port)           PORT="${2:-}"; shift 2 ;;
            --host)           HOST="${2:-}"; shift 2 ;;
            --health-timeout) HEALTH_TIMEOUT="${2:-}"; shift 2 ;;
            --uninstall)      UNINSTALL=1; shift ;;
            -h|--help)        usage; exit 0 ;;
            *)                die "unknown argument: $1 (try --help)" ;;
        esac
    done
}

# --- host + runtime gates ----------------------------------------------------
#
# MLX exists only on Apple Silicon: `mlx.core` needs a Metal device, so an arm64
# VM with none would install a server that can never load a model. Detect the
# device rather than the architecture, with the same probe as install-worker.sh's, and
# TAOS_FORCE_METAL=1 remains the escape hatch for a box the probe cannot see.
macos_metal_available() {
    if [[ -n "${TAOS_FORCE_METAL:-}" ]]; then
        return 0
    fi
    if ! command -v system_profiler >/dev/null 2>&1; then
        # Without system_profiler there is no way to tell a Metal GPU from an
        # arm64 VM that has none, so refuse to assume.
        warn "system_profiler not found: cannot verify Metal support"
        return 1
    fi
    system_profiler SPDisplaysDataType 2>/dev/null | grep -qiE 'Metal Support:[[:space:]]+Metal([[:space:]]+[0-9]+)?[[:space:]]*$'
}

require_apple_silicon() {
    local os_name arch
    os_name="$(uname -s)"
    [[ "$os_name" == "Darwin" ]] || die "MLX serving is macOS-only (this host is $os_name)"
    arch="$(uname -m)"
    [[ "$arch" == "arm64" ]] || die "MLX requires Apple Silicon (arm64); this Mac is $arch"
    macos_metal_available || die "no Metal device on this host: MLX cannot load a model here (set TAOS_FORCE_METAL=1 only on a box that really has one)"
}

resolve_venv() {
    if [[ -z "$VENV_DIR" ]]; then
        local script_dir
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        VENV_DIR="$(cd "$script_dir/.." && pwd)/apps/mlx-runtime/venv"
    fi
}

server_binary() { printf '%s\n' "${VENV_DIR}/bin/mlx_lm.server"; }

log_dir() { printf '%s\n' "$(dirname "$VENV_DIR")"; }

require_server_binary() {
    [[ -n "$MODEL_DIR" ]] || die "--model <dir> is required"
    [[ -d "$MODEL_DIR" ]] || die "model directory $MODEL_DIR does not exist"
    local binary
    binary="$(server_binary)"
    [[ -x "$binary" ]] || die "no MLX server at $binary: install a model on the 'mlx' backend first (that is what provisions the pinned runtime venv)"
}

# --- launchd agent -----------------------------------------------------------

plist_path() {
    local label="${1:-$LABEL}"
    printf '%s\n' "${HOME}/Library/LaunchAgents/${label}.plist"
}

agent_target() { printf 'gui/%s\n' "$(id -u)"; }

write_launchd_plist() {
    local plist log_suffix
    plist="$(plist_path)"
    # Per-model log files: two agents writing the same path would truncate each
    # other's output. The legacy label keeps the original name for continuity.
    log_suffix="${LABEL#${LEGACY_LABEL}}"
    log_suffix="${log_suffix#-}"
    log_suffix="${log_suffix:+-$log_suffix}"
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(xml_escape "$(server_binary)")</string>
        <string>--model</string><string>$(xml_escape "$MODEL_DIR")</string>
        <string>--host</string><string>$(xml_escape "$HOST")</string>
        <string>--port</string><string>$(xml_escape "$PORT")</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$(xml_escape "$(log_dir)/mlx-server${log_suffix}.log")</string>
    <key>StandardErrorPath</key><string>$(xml_escape "$(log_dir)/mlx-server${log_suffix}.err.log")</string>
</dict>
</plist>
EOF
    log "wrote $plist (model $MODEL_DIR, port $PORT)"
}

# Retire the legacy single-agent install (taOS #3337) when it pins the model we
# are about to serve under a per-model label. Without this an upgraded install
# would leave the old label registered against the same model and port, so the
# new agent could not bind. An agent that pins a DIFFERENT model is left alone:
# removing one model's server must never take another model's down.
retire_legacy_agent_for_model() {
    local plist
    plist="$(plist_path "$LEGACY_LABEL")"
    if [[ "$LABEL" == "$LEGACY_LABEL" ]]; then
        return 0
    fi
    if [[ ! -f "$plist" ]]; then
        return 0
    fi
    if ! grep -qF -- "<string>$(xml_escape "$MODEL_DIR")</string>" "$plist"; then
        return 0
    fi
    launchctl bootout "$(agent_target)/${LEGACY_LABEL}" 2>/dev/null || true
    if ! rm -f "$plist"; then
        warn "could not remove the legacy ${LEGACY_LABEL} agent; it may still serve $MODEL_DIR"
        return 0
    fi
    log "retired the legacy ${LEGACY_LABEL} agent (now served by ${LABEL})"
    return 0
}

load_launchd_agent() {
    local plist
    plist="$(plist_path)"
    # bootout first so a re-install of THE SAME model is replaced rather than
    # doubled up. bootout is scoped to this label, so another model's agent is
    # never touched -- that is what keeps N models served at once.
    launchctl bootout "$(agent_target)/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "$(agent_target)" "$plist"
    launchctl enable "$(agent_target)/${LABEL}"
    launchctl kickstart -k "$(agent_target)/${LABEL}"
    log "${LABEL} loaded + started via launchd"
}

wait_for_mlx_health() {
    local i
    for (( i = 0; i < HEALTH_TIMEOUT; i++ )); do
        if curl -fsS "http://${HOST}:${PORT}/v1/models" >/dev/null 2>&1; then
            log "mlx_lm.server answers http://${HOST}:${PORT}/v1/models"
            return 0
        fi
        sleep 1
    done
    return 1
}

uninstall_mlx_agent() {
    # Called from a model uninstall with --model: unload every agent that pins
    # THAT model (its per-model label, or the legacy label an upgrade left
    # behind). An agent pinning another model is left running -- removing one
    # model must not silently take the other's server down -- and that outcome
    # is reported as exit 3 so the caller can tell it apart from "nothing serves
    # this model any more" (exit 0).
    #
    # Without --model (a deliberate "stop all MLX serving") every agent is
    # unloaded.
    local plist found=0 left_running=0
    for plist in "${HOME}/Library/LaunchAgents"/com.taos.mlx-server*.plist; do
        [[ -f "$plist" ]] || continue
        if [[ -n "$MODEL_DIR" ]] && ! grep -qF -- "<string>$(xml_escape "$MODEL_DIR")</string>" "$plist"; then
            left_running=1
            continue
        fi
        found=1
        launchctl bootout "$(agent_target)/$(basename "$plist" .plist)" 2>/dev/null || true
        # bootout failing is normal when the agent was never loaded, so confirm
        # the real state instead of trusting its exit code: a label that is
        # still registered keeps restarting the server (KeepAlive) even after
        # the plist and the model directory are gone.
        local probe
        if probe="$(launchctl print "$(agent_target)/$(basename "$plist" .plist)" 2>&1)"; then
            warn "$(basename "$plist" .plist) is still loaded after bootout: not reporting it as unloaded"
            return 1
        fi
        # A failing `print` only means the agent is gone when launchctl says the
        # service is unknown (macOS: `Could not find service "..." in domain
        # ...`). Any other failure (a permission or session error) leaves the
        # unload unconfirmed, so it is an uninstall error rather than a silent
        # plist removal under an agent that may still be registered.
        if ! printf '%s' "$probe" | grep -qiE 'could not find service|service not found|no such process'; then
            warn "cannot confirm $(basename "$plist" .plist) is unloaded (launchctl print: ${probe}); leaving $plist in place"
            return 1
        fi
        # `set -e` is suspended in the caller's `uninstall_mlx_agent || rc=$?`
        # and in an `if`/`||` context, so a failed removal has to be checked
        # explicitly or the caller would hear "unloaded" while the plist is
        # still on disk.
        if ! rm -f "$plist"; then
            warn "failed to remove $plist"
            return 1
        fi
        log "removed $plist"
    done
    if [[ "$found" == "0" ]]; then
        if [[ "$left_running" == "1" ]]; then
            log "an agent serves a different model; leaving the agents running"
            return 3
        fi
        log "no com.taos.mlx-server agent installed"
    fi
    return 0
}

main() {
    parse_args "$@"
    resolve_venv
    # One launchd agent per model unless a caller pinned the label itself.
    LABEL="${LABEL:-$(label_for_model)}"

    if [[ "$UNINSTALL" == "1" ]]; then
        local rc=0
        uninstall_mlx_agent || rc=$?
        return "$rc"
    fi

    require_apple_silicon
    require_server_binary
    retire_legacy_agent_for_model
    write_launchd_plist
    load_launchd_agent

    if ! wait_for_mlx_health; then
        die "mlx_lm.server did not answer http://${HOST}:${PORT}/v1/models within ${HEALTH_TIMEOUT}s; see the agent's StandardErrorPath under $(log_dir) (a model too large for unified memory is the usual cause)"
    fi

    # Same suffix write_launchd_plist computed, so the summary names the log this
    # agent actually writes instead of the legacy shared name.
    local log_suffix="${LABEL#${LEGACY_LABEL}}"
    log_suffix="${log_suffix#-}"
    log_suffix="${log_suffix:+-$log_suffix}"

    cat <<EOF

  =================================================================
  MLX server installed (one agent per model, several models coexist)
  =================================================================
    model:         $MODEL_DIR
    runtime:       $(server_binary)
    HTTP endpoint: http://${HOST}:${PORT}/v1
    launchd agent: $(plist_path)
    logs:          $(log_dir)/mlx-server${log_suffix}.log

EOF
}

main "$@"
