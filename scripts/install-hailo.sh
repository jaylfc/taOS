#!/usr/bin/env bash
# TinyAgentOS Hailo-10H + hailo-ollama installer
#
# Detects a Hailo-10H NPU (the AI HAT+2 on a Raspberry Pi 5), installs the
# HailoRT runtime + firmware through the platform package flow (the user
# accepts Hailo's EULA there; taOS never redistributes proprietary Hailo
# binaries), installs hailo-ollama at a pinned ref listening on the taOS
# port 7836, wires up a systemd unit whose ExecStartPre reaps any orphan
# process still holding the port, and waits for the Ollama-compatible API
# to answer on /api/tags.
#
# This mirrors scripts/install-rknpu.sh component for component, but for the
# Hailo-10H LLM backend instead of the Rockchip rkllama backend. The design
# is in docs/design/hailo-llm-backend.md (section C, slice S2).
#
# Usage:
#     # interactive (asks for confirmation before touching the system)
#     sudo bash scripts/install-hailo.sh
#
#     # headless / curl|bash (no TTY)
#     TAOS_HAILO_SETUP=1 sudo bash scripts/install-hailo.sh
#     sudo bash scripts/install-hailo.sh --yes
#
#     # one-liner
#     curl -sSL https://raw.githubusercontent.com/jaylfc/taOS/master/scripts/install-hailo.sh \
#       | TAOS_HAILO_SETUP=1 sudo bash
#
# Environment overrides:
#     TAOS_HAILO_SETUP        set to 1/true to skip interactive confirmation
#     TAOS_FORCE_HAILO        set to 1/true to force the 10H branch on bench
#                             boxes without a /dev/hailo0 node (mirrors
#                             TAOS_FORCE_RKNPU)
#     TAOS_HAILO_OLLAMA_DIR   install dir (default: ~<user>/hailo-ollama)
#     TAOS_HAILO_OLLAMA_REPO  git remote (default: https://github.com/hailo-ai/hailo-ollama.git)
#     TAOS_HAILO_OLLAMA_REF   git ref  (default: pinned, see below)
#     TAOS_HAILO_OLLAMA_PORT  HTTP port (default: 7836)
#
# Safety:
#   - Gated on confirmation / env var. Non-interactive without the env var
#     prints the install command and exits 0.
#   - Idempotent: re-running after success is a no-op.
#   - sudo is used only for apt (HailoRT) and systemd; everything else runs
#     as the invoking user.
#   - Fail-soft: actionable messages on missing prerequisites, no silent
#     partial state left behind.

set -euo pipefail

# -------- Config ----------------------------------------------------------

# hailo-ollama is mirrored into a TAOS-controlled location only at install
# time (the git remote above is fetched at a pinned ref). Nothing proprietary
# from Hailo (HailoRT, firmware, Dataflow Compiler) is ever mirrored or
# committed: the HailoRT step drives the platform package flow and the user
# accepts Hailo's terms there. See docs/design/hailo-llm-backend.md section G
# and the Security/licensing notes.
#
# TODO(taOS): the exact pinned hailo-ollama ref must be confirmed against the
# community tester's install on #1771 (design Open Question 1). The default
# below is a placeholder that operators override with TAOS_HAILO_OLLAMA_REF.
# The Hailo-Ollama server is NOT a standalone repo: it ships inside the Hailo
# GenAI Model Zoo and is built from source there (its README: "It includes
# Hailo-Ollama, an Ollama-compatible API written in C++ on top of HailoRT").
# Pin a specific commit for reproducibility; override with TAOS_HAILO_OLLAMA_REF.
HAILO_OLLAMA_REPO="${TAOS_HAILO_OLLAMA_REPO:-https://github.com/hailo-ai/hailo_model_zoo_genai.git}"
HAILO_OLLAMA_REF="${TAOS_HAILO_OLLAMA_REF:-1a3ba6be4af93dc58e675662c946a1a65198ec31}"

# Port 7836 is the next free slot in the taOS service block (7832 qmd,
# 7833 rkllama, 7834 LiteLLM, 7835 llama-cpp). Upstream hailo-ollama listens
# on 0.0.0.0:8000, which is banned by taOS port hygiene (it is the Django
# slot) and is already probed as a llama-cpp/vllm candidate. Remapping to
# 7836 keeps the 8000 probes unambiguous and keeps the backend off a reserved
# port. Honours the same override contract as TAOS_RKLLAMA_PORT.
HAILO_OLLAMA_PORT="${TAOS_HAILO_OLLAMA_PORT:-7836}"

# Minimum firmware floor: 5.1.0 is the version the community tester on #1771
# has confirmed running LLMs. Older firmware is unknown; v1 enforces >= 5.1.0
# and can relax later with evidence (design Open Question 3).
HAILO_MIN_FIRMWARE="5.1.0"

# PCI vendor/device id for Hailo (1e60). The 10H reports "10h" / "hailo-10"
# in `lspci -d 1e60:` output; anything else with a /dev/hailo* node is an
# 8-class (vision) device that must NOT trigger the LLM installer.
HAILO_PCI="1e60:"

# -------- pretty printing -------------------------------------------------

log()  { printf '\033[1;34m[hailo]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[hailo]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[hailo]\033[0m %s\n' "$*" >&2; exit 1; }

# version_ge <a> <b> -> true when a >= b (semver-ish, via sort -V)
version_ge() {
    [[ "$1" == "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" ]]
}

# Environment banner: every user-posted install log should self-describe the
# machine it ran on (distro, kernel, board, current Hailo device arch +
# firmware) so a failure report is diagnosable without a round-trip.
if [[ -r /etc/os-release ]]; then
    _distro="$(grep -E '^(PRETTY_NAME|ID)=' /etc/os-release | head -n1 | cut -d= -f2- | tr -d '"')"
    log "distro=${_distro:-unknown}"
fi
log "kernel=$(uname -r) arch=$(uname -m)"
if [[ -r /proc/device-tree/model ]]; then
    log "board=$(tr -d '\0' < /proc/device-tree/model)"
fi
if command -v hailortcli >/dev/null 2>&1; then
    log "hailortcli identify:"
    hailortcli fw-control identify 2>&1 | sed 's/^/    /' || true
else
    log "hailortcli=not installed (will be provided by HailoRT in step 2)"
fi

# -------- consent gate helpers -------------------------------------------

is_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

want_yes=0
if is_truthy "${TAOS_HAILO_SETUP:-}"; then
    want_yes=1
fi
for arg in "$@"; do
    case "$arg" in
        -y|--yes) want_yes=1 ;;
    esac
done

confirm_or_exit() {
    if (( want_yes )); then
        return 0
    fi
    if [[ ! -t 0 || ! -t 1 ]]; then
        warn "non-interactive shell and TAOS_HAILO_SETUP is not set -- not touching anything"
        warn "to opt in, re-run as:"
        warn "    TAOS_HAILO_SETUP=1 sudo bash scripts/install-hailo.sh"
        exit 0
    fi
    echo
    echo "This script will:"
    echo "  * install HailoRT + firmware (>= $HAILO_MIN_FIRMWARE) via the platform package flow"
    echo "  * clone hailo_model_zoo_genai (pinned ref ${HAILO_OLLAMA_REF:0:12}) and build the"
    echo "    Hailo-Ollama server from it with cmake, installing it system-wide"
    echo "  * install + enable a systemd unit hailo-ollama.service on port $HAILO_OLLAMA_PORT"
    echo
    read -r -p "Proceed? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *) log "aborted by user"; exit 0 ;;
    esac
}

# -------- resolve target user + home -------------------------------------

# Resolved lazily: only after a Hailo-10H is confirmed, so a non-Hailo host
# exits without ever touching the system (or requiring getent to exist).
resolve_target() {
    # When invoked under sudo we want the clone / build to land in the calling
    # user's home, not /root. SUDO_USER gives us that.
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        TARGET_USER="$SUDO_USER"
    else
        TARGET_USER="$(id -un)"
    fi
    if command -v getent >/dev/null 2>&1; then
        TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
    else
        TARGET_HOME="$(eval echo "~$TARGET_USER")"
    fi
    [[ -d "$TARGET_HOME" ]] || die "cannot resolve home directory for user $TARGET_USER"
    TARGET_GROUP="$(id -gn "$TARGET_USER")"
    HAILO_OLLAMA_DIR="${TAOS_HAILO_OLLAMA_DIR:-$TARGET_HOME/hailo_model_zoo_genai}"
}

# run_as_user <cmd...> -- run a command as the unprivileged target user
run_as_user() {
    if [[ "$(id -un)" == "$TARGET_USER" ]]; then
        "$@"
    else
        sudo -u "$TARGET_USER" -H "$@"
    fi
}

# -------- (1) 10H detection ----------------------------------------------

# Populates HAILO_ARCH and HAILO_FIRMWARE (best-effort). Sets the global
# HAILO_CLASS to "10h", "8class", or "none".
detect_hailo() {
    HAILO_CLASS="none"
    HAILO_ARCH=""
    HAILO_FIRMWARE=""

    if is_truthy "${TAOS_FORCE_HAILO:-}"; then
        log "TAOS_FORCE_HAILO=1 set -- forcing the 10H branch for bench setup"
        HAILO_CLASS="10h"
        return 0
    fi

    if [[ ! -e /dev/hailo0 ]]; then
        log "no Hailo-10H detected (/dev/hailo0 absent)"
        return 0
    fi

    local lspci_out=""
    if command -v lspci >/dev/null 2>&1; then
        lspci_out="$(lspci -d "$HAILO_PCI" 2>/dev/null || true)"
    fi

    if grep -Eiq '10h|hailo-10' <<<"$lspci_out"; then
        HAILO_CLASS="10h"
        log "detected Hailo-10H via lspci"
        return 0
    fi

    # lspci inconclusive: fall back to hailortcli and grep the reported
    # architecture. Where lspci is missing or silent this is the authority.
    if command -v hailortcli >/dev/null 2>&1; then
        local ident
        ident="$(hailortcli fw-control identify 2>/dev/null || true)"
        if grep -Eiq '10h|hailo-10' <<<"$ident"; then
            HAILO_CLASS="10h"
            HAILO_ARCH="$(printf '%s' "$ident" | grep -Ei 'architecture' | head -n1 | sed 's/.*://;s/^ *//')"
            HAILO_FIRMWARE="$(printf '%s' "$ident" | grep -Ei 'fw version' | head -n1 | sed 's/.*://;s/^ *//')"
            log "detected Hailo-10H via hailortcli identify (arch=${HAILO_ARCH:-?} fw=${HAILO_FIRMWARE:-?})"
            return 0
        fi
    fi

    # A /dev/hailo* node exists but is not a 10H: it is an 8-class (vision)
    # device, which has no LLM runtime. Print the vision-only notice and stop.
    HAILO_CLASS="8class"
    log "detected a Hailo device that is not a 10H (vision-class: no LLM support)"
    warn "This Hailo device is vision-only (8 / 8L class). The Hailo-10H LLM"
    warn "backend is not supported on it. See docs/design/hailo-llm-backend.md."
    return 0
}

# -------- (2) HailoRT + firmware -----------------------------------------

ensure_hailort() {
    local fw="${HAILO_FIRMWARE:-}"
    # If detection already reported a firmware, enforce the floor early so we
    # fail with an actionable message before touching packages.
    if [[ -n "$fw" ]]; then
        if ! version_ge "$fw" "$HAILO_MIN_FIRMWARE"; then
            die "Hailo firmware $fw is below the minimum $HAILO_MIN_FIRMWARE required for LLMs. Update HailoRT/firmware (see docs/design/hailo-llm-backend.md) and re-run."
        fi
        log "Hailo firmware $fw satisfies the >= $HAILO_MIN_FIRMWARE floor"
    fi

    if command -v hailortcli >/dev/null 2>&1; then
        # Re-check: the running firmware may differ from the banner snapshot.
        local live_fw
        live_fw="$(hailortcli fw-control identify 2>/dev/null | grep -Ei 'fw version' | head -n1 | sed 's/.*://;s/^ *//' || true)"
        if [[ -n "$live_fw" ]] && ! version_ge "$live_fw" "$HAILO_MIN_FIRMWARE"; then
            die "Hailo firmware $live_fw is below the minimum $HAILO_MIN_FIRMWARE required for LLMs. Update HailoRT/firmware and re-run."
        fi
        log "hailortcli present (fw=${live_fw:-unknown}); skipping HailoRT package install"
        return 0
    fi

    log "installing HailoRT + firmware"
    if ! command -v apt-get >/dev/null 2>&1; then
        die "no apt-get on this host. Install HailoRT from the Hailo Developer Zone (https://hailo.ai/developer-zone/) for your distro, accept Hailo's EULA there, then re-run this script."
    fi

    # Detect Raspberry Pi OS to prefer the first-party `hailo-all` package
    # from the Raspberry Pi repository; otherwise point at the Hailo Developer
    # Zone. The user accepts Hailo's EULA during this flow; taOS never
    # bypasses it.
    local is_rpios=0
    if [[ -r /etc/os-release ]]; then
        local _id_like
        _id_like="$(grep -E '^(ID|ID_LIKE)=' /etc/os-release | cut -d= -f2- | tr -d '"' | tr '\n' ',')"
        case "$_id_like" in
            *raspbian*|*debian*rpi*|*rpios*) is_rpios=1 ;;
        esac
    fi

    if (( is_rpios )); then
        log "Raspberry Pi OS detected -- installing hailo-all from the Raspberry Pi repository"
        sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq \
            || warn "apt-get update failed -- package lists may be stale"
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y hailo-all \
            || die "failed to install hailo-all. Add the Raspberry Pi Hailo apt source, accept Hailo's EULA, then re-run."
    else
        die "this is not Raspberry Pi OS. Install HailoRT from the Hailo Developer Zone (https://hailo.ai/developer-zone/) for your distro, accept Hailo's EULA there, then re-run this script."
    fi

    if ! command -v hailortcli >/dev/null 2>&1; then
        die "hailortcli still not found after install. Reboot may be required for the Hailo driver; reboot and re-run."
    fi
    log "HailoRT installed"
}

# -------- (3) hailo-ollama clone + pin -----------------------------------

install_hailo_ollama() {
    if [[ -d "$HAILO_OLLAMA_DIR/.git" ]]; then
        log "hailo-ollama checkout exists at $HAILO_OLLAMA_DIR -- fetching + checking out ref"
        run_as_user git -C "$HAILO_OLLAMA_DIR" fetch --all --tags --quiet
    else
        log "cloning $HAILO_OLLAMA_REPO -> $HAILO_OLLAMA_DIR"
        run_as_user mkdir -p "$(dirname "$HAILO_OLLAMA_DIR")"
        run_as_user git clone --quiet "$HAILO_OLLAMA_REPO" "$HAILO_OLLAMA_DIR"
        _fetch_err="$(run_as_user git -C "$HAILO_OLLAMA_DIR" fetch --quiet origin "$HAILO_OLLAMA_REF" 2>&1)" \
            || log "note: direct fetch of the pinned ref failed (${_fetch_err:-no detail}); relying on the refs the clone brought down"
    fi

    if ! run_as_user git -C "$HAILO_OLLAMA_DIR" cat-file -e "${HAILO_OLLAMA_REF}^{commit}" 2>/dev/null; then
        die "pinned hailo-ollama ref ${HAILO_OLLAMA_REF:0:12} is not reachable from any branch or tag of $HAILO_OLLAMA_REPO (the fork's history may have been rewritten). Override with TAOS_HAILO_OLLAMA_REF=<ref>."
    fi
    run_as_user git -C "$HAILO_OLLAMA_DIR" checkout --quiet "$HAILO_OLLAMA_REF"
    log "hailo-ollama pinned to $(run_as_user git -C "$HAILO_OLLAMA_DIR" rev-parse --short HEAD)"

    # Build + install the Hailo-Ollama server. It is C++ on top of HailoRT, built
    # with cmake (per the repo README): configure -> build -> install. cmake
    # --install lands the `hailo-ollama` binary in <prefix>/bin (default
    # /usr/local/bin) and the model manifests under <prefix>/share/hailo-ollama.
    # cmake fetches its own libs (json, oatpp, eventpp) via cmake/external, but
    # the host still needs the C++ toolchain + OpenSSL headers.
    log "installing build tools (cmake, compiler, OpenSSL headers)"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
        cmake build-essential libssl-dev >/dev/null

    log "configuring hailo-ollama build (cmake, Release)"
    run_as_user sh -c "cd '$HAILO_OLLAMA_DIR' && cmake -B build -DCMAKE_BUILD_TYPE=Release"
    log "compiling hailo-ollama (C++; can take several minutes on the Pi)"
    run_as_user sh -c "cd '$HAILO_OLLAMA_DIR' && cmake --build build --config Release"
    log "installing hailo-ollama system-wide (cmake --install, needs sudo)"
    sudo cmake --install "$HAILO_OLLAMA_DIR/build"
}

# Resolve the hailo-ollama entrypoint into HAILO_OLLAMA_BIN.
resolve_bin() {
    # cmake --install placed the binary in <prefix>/bin (default /usr/local/bin).
    if command -v hailo-ollama >/dev/null 2>&1; then
        HAILO_OLLAMA_BIN="$(command -v hailo-ollama)"
    elif [[ -x /usr/local/bin/hailo-ollama ]]; then
        HAILO_OLLAMA_BIN="/usr/local/bin/hailo-ollama"
    else
        die "could not find a hailo-ollama executable after cmake --install (looked in PATH and /usr/local/bin)."
    fi
    log "hailo-ollama entrypoint: $HAILO_OLLAMA_BIN"
}

# -------- (4/5) systemd unit ---------------------------------------------

install_systemd_unit() {
    local unit="/etc/systemd/system/hailo-ollama.service"
    # The hailo-ollama server takes no serve subcommand or --port flag; it is
    # started bare and reads its listen address from the OLLAMA_HOST env var
    # (format host:port; default 0.0.0.0:8000). taOS port hygiene bans 8000, so
    # we bind localhost on the managed port 7836 (the taOS controller reaches it
    # on the same host; nothing needs it exposed on 0.0.0.0). See the repo's
    # docs/USAGE.rst "Environment Variables".
    local exec_start="$HAILO_OLLAMA_BIN"

    log "installing $unit"
    sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=hailo-ollama -- Hailo-10H NPU LLM server (Ollama-compatible)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$HAILO_OLLAMA_DIR
Environment=OLLAMA_HOST=127.0.0.1:$HAILO_OLLAMA_PORT
# hailo-ollama can leave a bare orphan process listening on the port if it
# crashes during model load (the adopt-an-orphan lesson from PR #1755 for
# rkllama). Reap any such process before (re)start so the bind cannot fail.
# Match the installed binary path, not any command line merely containing
# "hailo-ollama" (which would kill an editor or tail open on these files).
ExecStartPre=-/usr/bin/pkill -9 -f "$HAILO_OLLAMA_BIN"
ExecStart=$exec_start
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now hailo-ollama.service
    log "hailo-ollama.service enabled + started"
}

# -------- (6) health-wait ------------------------------------------------

wait_for_hailo_ollama() {
    local i url="http://localhost:$HAILO_OLLAMA_PORT/api/tags"
    for (( i = 0; i < 120; i++ )); do
        # 200 with a "models" body: the Ollama-compatible /api/tags answers.
        if curl -fs "$url" 2>/dev/null | grep -q '"models"'; then
            log "hailo-ollama HTTP API is up on :$HAILO_OLLAMA_PORT"
            return 0
        fi
        sleep 1
    done
    die "hailo-ollama HTTP API did not come up within 120s -- check: sudo journalctl -u hailo-ollama -n 100"
}

# -------- (7) summary -----------------------------------------------------

already_installed() {
    # All of:
    #   * hailo-ollama checkout on the pinned ref
    #   * systemd unit enabled
    #   * HTTP API responding with a "models" body
    [[ -d "$HAILO_OLLAMA_DIR/.git" ]] || return 1
    # Compare resolved commits, not HEAD-vs-ref-name: HAILO_OLLAMA_REF is usually
    # a branch/tag ("main"), so comparing it to rev-parse HEAD (a SHA) never
    # matched and every re-run needlessly re-fetched and re-installed.
    local _head _want
    _head="$(run_as_user git -C "$HAILO_OLLAMA_DIR" rev-parse HEAD 2>/dev/null || true)"
    _want="$(run_as_user git -C "$HAILO_OLLAMA_DIR" rev-parse "${HAILO_OLLAMA_REF}^{commit}" 2>/dev/null || true)"
    [[ -n "$_head" && "$_head" == "$_want" ]] || return 1
    systemctl is-enabled hailo-ollama.service >/dev/null 2>&1 || return 1
    local tags
    tags="$(curl -fs "http://localhost:$HAILO_OLLAMA_PORT/api/tags" 2>/dev/null || true)"
    grep -q '"models"' <<<"$tags" || return 1
    return 0
}

print_summary() {
    cat <<EOF

  =================================================================
  Hailo-10H + hailo-ollama installed successfully
  =================================================================
    hailo-ollama dir:  $HAILO_OLLAMA_DIR
    hailo-ollama ref:  ${HAILO_OLLAMA_REF:0:12}
    HTTP endpoint:     http://localhost:$HAILO_OLLAMA_PORT
    systemd unit:      /etc/systemd/system/hailo-ollama.service

  Check status:  sudo systemctl status hailo-ollama
  Tail logs:     sudo journalctl -u hailo-ollama -f

EOF
}

# -------- main ------------------------------------------------------------

main() {
    log "TinyAgentOS Hailo-10H + hailo-ollama installer starting"

    detect_hailo
    case "$HAILO_CLASS" in
        none)
            # Non-Hailo host: nothing to do, leave the system untouched.
            log "no Hailo-10H detected -- nothing to install"
            exit 0
            ;;
        8class)
            # Vision-only device: explicit "no LLM support" notice already
            # printed in detect_hailo, leave the system untouched.
            log "Hailo device present but not a 10H -- no LLM backend to install"
            exit 0
            ;;
        10h)
            log "Hailo-10H detected -- proceeding with install"
            ;;
    esac

    resolve_target

    if already_installed; then
        log "already fully installed -- nothing to do"
        print_summary
        exit 0
    fi

    confirm_or_exit

    ensure_hailort
    install_hailo_ollama
    resolve_bin
    install_systemd_unit
    wait_for_hailo_ollama
    print_summary
}

main "$@"
