#!/usr/bin/env bash
# TinyAgentOS Rockchip NPU + rkllama installer
#
# Pins /usr/lib/librknnrt.so to the 2.3.0 runtime required by the LCM
# Dreamshaper UNet (see README: "RK3588 NPU Image Generation — Runtime
# Version Pin"), installs the jaylfc fork of rkllama with the rerank /
# per-model-locking / KV-cache-fix patches needed by qmd, pulls the
# three preloaded models (embedding, reranker, query expansion), and
# wires up a systemd unit whose ExecStart matches the one already in
# production on the controller Orange Pi 5 Plus.
#
# Usage:
#     # interactive (asks for confirmation before touching /usr/lib)
#     sudo bash scripts/install-rknpu.sh
#
#     # headless / curl|bash (no TTY)
#     TAOS_RKNPU_SETUP=1 sudo bash scripts/install-rknpu.sh
#     sudo bash scripts/install-rknpu.sh --yes
#
#     # one-liner
#     curl -sSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-rknpu.sh \
#       | TAOS_RKNPU_SETUP=1 sudo bash
#
# Environment overrides:
#     TAOS_RKNPU_SETUP        set to 1/true to skip interactive confirmation
#     TAOS_RKLLAMA_DIR        install dir (default: ~<user>/rkllama)
#     TAOS_RKLLAMA_REPO       git remote (default: https://github.com/jaylfc/rkllama.git)
#     TAOS_RKLLAMA_REF        git ref  (default: 06cf874d8b29767729ec06547cf02fc92acd875c)
#     TAOS_RKLLAMA_PORT       HTTP port (default: 7833)
#     TAOS_QMD_EXPANSION_URL  override URL for qmd-query-expansion-1.7B-rk3588.rkllm
#                             (default is the TAOS HF mirror at
#                             jaysom/tinyagentos-rockchip-mirror; only set
#                             this if you are self-hosting a different copy)
#
# Safety:
#   - Gated on confirmation / env var. Non-interactive without the env
#     var prints the install command and exits 0.
#   - Backs up /usr/lib/librknnrt.so before overwriting, and restores
#     the backup automatically on any subsequent failure.
#   - Idempotent: re-running after success is a no-op.
#   - sudo is used only for librknnrt + systemd; everything else runs
#     as the invoking user.

set -euo pipefail

# -------- Config ----------------------------------------------------------

# Every binary required for the verified RK3588 install path is mirrored in
# the TAOS-controlled HuggingFace repo `jaysom/tinyagentos-rockchip-mirror`.
# The mirror exists so that third-party upstream repos (dulimov/, happyme531/,
# darkbit1001/) disappearing or silently changing contents cannot break the
# install for Rockchip users. See docs/mirror-policy.md. The original
# upstream URLs are preserved in comments below as documented fallbacks.

TAOS_MIRROR_REPO="jaysom/tinyagentos-rockchip-mirror"
TAOS_MIRROR_BASE="https://huggingface.co/${TAOS_MIRROR_REPO}/resolve/main"

# librknnrt 2.3.0 (build c949ad889d, 2024-11-07)
# Upstream fallback: https://huggingface.co/darkbit1001/Stable-Diffusion-1.5-LCM-ONNX-RKNN2/resolve/main/librknnrt.so
LIBRKNNRT_URL="${TAOS_MIRROR_BASE}/librknnrt-2.3.0-c949ad889d-20241107.so"
LIBRKNNRT_SHA256="73993ed4b440460825f21611731564503cc1d5a0c123746477da6cd574f34885"
LIBRKNNRT_DEST="/usr/lib/librknnrt.so"
LIBRKNNRT_EXPECTED_VERSION="2.3.0"

RKLLAMA_REPO="${TAOS_RKLLAMA_REPO:-https://github.com/jaylfc/rkllama.git}"
RKLLAMA_REF="${TAOS_RKLLAMA_REF:-06cf874d8b29767729ec06547cf02fc92acd875c}"
RKLLAMA_PORT="${TAOS_RKLLAMA_PORT:-7833}"

# Qwen3-Embedding-0.6B rk3588 rkllm weights.
# Upstream fallback: https://huggingface.co/dulimov/Qwen3-Embedding-0.6B-rk3588-1.2.1/resolve/main/Qwen3-Embedding-0.6B-rk3588-w8a8-opt-1-hybrid-ratio-0.5.rkllm
EMBEDDING_URL="${TAOS_MIRROR_BASE}/models/qwen3-embedding-0.6b.rkllm"
EMBEDDING_SHA256="417d4a9d413b03089a2b9e4f31fb36a9ea3c45c92bcb19dcce6cc3873af88967"
EMBEDDING_LOCAL_NAME="Qwen3-Embedding-0.6B.rkllm"
EMBEDDING_HF_TOKENIZER="Qwen/Qwen3-Embedding-0.6B"

# Qwen3-Reranker-0.6B rk3588 rkllm weights.
# Upstream fallback: https://huggingface.co/dulimov/Qwen3-Reranker-0.6B-rk3588-1.2.1/resolve/main/Qwen3-Reranker-0.6B-rk3588-w8a8-opt-1-hybrid-ratio-0.5.rkllm
RERANKER_URL="${TAOS_MIRROR_BASE}/models/qwen3-reranker-0.6b.rkllm"
RERANKER_SHA256="192795fd984051c85ba4c2a75c6b97e7971d9b66964083e144c6d2db96c9176a"
RERANKER_LOCAL_NAME="Qwen3-Reranker-0.6B.rkllm"
RERANKER_HF_TOKENIZER="Qwen/Qwen3-Reranker-0.6B"

# qmd-query-expansion 1.7B rk3588 rkllm weights — no upstream mirror; the
# TAOS HF mirror is the canonical source. TAOS_QMD_EXPANSION_URL still lets
# advanced users point at a different host, but the default is the mirror.
QMD_EXPANSION_URL="${TAOS_QMD_EXPANSION_URL:-${TAOS_MIRROR_BASE}/models/qmd-query-expansion-1.7b.rkllm}"
QMD_EXPANSION_SHA256="1cbc71a05fc9c789c2ec12b72b580c4cad74d91f5d04679e8256cf4ecb6d712c"
QMD_EXPANSION_LOCAL_NAME="qmd-query-expansion-1.7B-rk3588.rkllm"
QMD_EXPANSION_HF_TOKENIZER="Qwen/Qwen3-1.7B"

# -------- pretty printing -------------------------------------------------

log()  { printf '\033[1;34m[rknpu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[rknpu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[rknpu]\033[0m %s\n' "$*" >&2; exit 1; }

# verify_sha256 <file> <expected_hex> <label>
# Hard-fails on mismatch: a corrupted download or a tampered mirror must
# never be silently accepted.
verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    if ! command -v sha256sum >/dev/null 2>&1; then
        die "sha256sum not available — cannot verify $label integrity"
    fi
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — corrupted download or tampered mirror, refusing to install"
    fi
    log "sha256 ok for $label (${actual:0:12}…)"
}

# -------- global rollback state ------------------------------------------

LIBRKNNRT_BACKUP=""
LIBRKNNRT_REPLACED=0

on_error() {
    local rc=$?
    if (( LIBRKNNRT_REPLACED )) && [[ -n "$LIBRKNNRT_BACKUP" && -f "$LIBRKNNRT_BACKUP" ]]; then
        warn "failure detected (exit $rc) — restoring previous librknnrt from $LIBRKNNRT_BACKUP"
        sudo cp -a "$LIBRKNNRT_BACKUP" "$LIBRKNNRT_DEST" || true
        sudo ldconfig || true
    fi
    exit $rc
}
trap on_error ERR

# -------- consent gate ---------------------------------------------------

is_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

want_yes=0
if is_truthy "${TAOS_RKNPU_SETUP:-}"; then
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
        warn "non-interactive shell and TAOS_RKNPU_SETUP is not set — not touching anything"
        warn "to opt in, re-run as:"
        warn "    TAOS_RKNPU_SETUP=1 sudo bash scripts/install-rknpu.sh"
        exit 0
    fi
    echo
    echo "This script will:"
    echo "  * back up $LIBRKNNRT_DEST and replace it with the 2.3.0 pinned build"
    echo "  * clone rkllama into ~/rkllama (pinned ref ${RKLLAMA_REF:0:12})"
    echo "  * download ~2.8 GB of rk3588 RKLLM model files"
    echo "  * install + enable a systemd unit rkllama.service on port $RKLLAMA_PORT"
    echo
    read -r -p "Proceed? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *) log "aborted by user"; exit 0 ;;
    esac
}

# -------- resolve target user + home -------------------------------------

# When invoked under sudo we want the downloads / venv / models to land
# in the calling user's home, not /root. SUDO_USER gives us that.
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    TARGET_USER="$SUDO_USER"
else
    TARGET_USER="$(id -un)"
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -d "$TARGET_HOME" ]] || die "cannot resolve home directory for user $TARGET_USER"
TARGET_GROUP="$(id -gn "$TARGET_USER")"

RKLLAMA_DIR="${TAOS_RKLLAMA_DIR:-$TARGET_HOME/rkllama}"
RKLLAMA_VENV="$RKLLAMA_DIR/rkllama-env"
RKLLAMA_MODELS="$RKLLAMA_DIR/models"

# run_as_user <cmd...> — run a command as the unprivileged target user
run_as_user() {
    if [[ "$(id -un)" == "$TARGET_USER" ]]; then
        "$@"
    else
        sudo -u "$TARGET_USER" -H "$@"
    fi
}

# -------- (1) board detection --------------------------------------------

detect_board() {
    local model_raw compat soc=""
    if [[ ! -r /proc/device-tree/model ]]; then
        die "this does not look like a device-tree system — /proc/device-tree/model missing. install-rknpu.sh only supports Rockchip SBCs with an NPU."
    fi
    model_raw="$(tr -d '\000' </proc/device-tree/model)"
    compat=""
    if [[ -r /proc/device-tree/compatible ]]; then
        compat="$(tr '\000' '\n' </proc/device-tree/compatible)"
    fi

    if grep -qi 'rk3588' <<<"$compat$model_raw"; then
        soc="rk3588"
    elif grep -qi 'rk3576' <<<"$compat$model_raw"; then
        soc="rk3576"
    elif grep -qi 'rk3568' <<<"$compat$model_raw"; then
        soc="rk3568"
    else
        die "unsupported board: '$model_raw' — install-rknpu.sh supports rk3588 / rk3576 / rk3568 only"
    fi
    log "detected board: $model_raw (soc=$soc)"
    SOC="$soc"
}

# -------- (2) kernel driver detection ------------------------------------

detect_rknpu_driver() {
    # The upstream rknpu kernel driver exposes itself via devfreq at
    # /sys/class/devfreq/*.npu — NOT via a /dev/rknpu node (that doesn't
    # exist on current Armbian / Joshua-Riek Ubuntu / Rockchip BSP
    # kernels). /dev/dri/renderD129 is the user-facing render node but
    # the authoritative check is the devfreq entry, since that's what's
    # present on every known working rk3588 rkllama box.
    local found=0
    for d in /sys/class/devfreq/*.npu; do
        [[ -d "$d" ]] && { found=1; break; }
    done
    if (( ! found )); then
        warn "no devfreq entry found for the NPU under /sys/class/devfreq/*.npu"
        warn "this usually means the rknpu kernel driver is not loaded."
        warn "install-rknpu.sh will not load kernel modules for you — that is your"
        warn "OS image vendor's job. On Orange Pi: use an Armbian / Joshua-Riek"
        warn "Ubuntu build that ships the rknpu driver in-tree, then re-run this."
        die "rknpu driver not detected"
    fi
    log "rknpu kernel driver present (devfreq entry found)"
}

# -------- (3) librknnrt 2.3.0 pin ----------------------------------------

librknnrt_current_version() {
    if [[ -f "$LIBRKNNRT_DEST" ]] && command -v strings >/dev/null 2>&1; then
        strings "$LIBRKNNRT_DEST" | awk '/librknnrt version: / { print $3; exit }'
    fi
}

pin_librknnrt() {
    local current
    current="$(librknnrt_current_version || true)"
    if [[ "$current" == "$LIBRKNNRT_EXPECTED_VERSION" ]]; then
        log "librknnrt already at $LIBRKNNRT_EXPECTED_VERSION — skipping pin"
        return 0
    fi

    if [[ -n "$current" ]]; then
        log "librknnrt currently at $current — pinning to $LIBRKNNRT_EXPECTED_VERSION"
    else
        log "no existing librknnrt detected — installing $LIBRKNNRT_EXPECTED_VERSION"
    fi

    # Download first so a failed fetch never leaves the system in a
    # degraded state.
    local tmp="/tmp/librknnrt.so.new.$$"
    log "downloading pinned runtime from $LIBRKNNRT_URL"
    if ! curl -fSL --retry 3 --retry-delay 2 -o "$tmp" "$LIBRKNNRT_URL"; then
        rm -f "$tmp"
        die "failed to download librknnrt from $LIBRKNNRT_URL"
    fi

    # Sanity: the real binary is ~54 MB. If we got back an HTML error
    # page or a redirect stub we're well under 1 MB.
    local sz
    sz="$(stat -c%s "$tmp" 2>/dev/null || wc -c <"$tmp")"
    if (( sz < 1048576 )); then
        rm -f "$tmp"
        die "downloaded librknnrt is only ${sz} bytes — looks like an error page, refusing to install"
    fi
    log "downloaded $(( sz / 1024 / 1024 )) MiB"

    verify_sha256 "$tmp" "$LIBRKNNRT_SHA256" "librknnrt.so"

    # Back up the existing file.
    if [[ -f "$LIBRKNNRT_DEST" ]]; then
        LIBRKNNRT_BACKUP="${LIBRKNNRT_DEST}.bak.$(date +%Y%m%d-%H%M%S)"
        log "backing up existing $LIBRKNNRT_DEST -> $LIBRKNNRT_BACKUP"
        sudo cp -a "$LIBRKNNRT_DEST" "$LIBRKNNRT_BACKUP"
    fi

    # Install the new one.
    sudo install -m 0644 "$tmp" "$LIBRKNNRT_DEST"
    LIBRKNNRT_REPLACED=1
    sudo ldconfig

    # Verify the version string as a belt-and-braces check. The SHA256 above
    # already proved $tmp is byte-for-byte the pinned runtime, so this is
    # secondary. It depends on `strings` (binutils), which minimal Pi images
    # often lack and which install_rkllama only installs LATER (step 4). A
    # missing `strings` here must therefore NOT fail the install (#783): on a
    # box without binutils the re-check returned empty and the script died
    # with "version after install is ''" even though librknnrt installed
    # correctly, which stopped rkllama from ever installing. Skip gracefully
    # when the tool is unavailable; only die on a genuine version mismatch.
    local verified=""
    if command -v strings >/dev/null 2>&1; then
        verified="$(strings "$tmp" | awk '/librknnrt version: / { print $3; exit }')"
        if [[ -z "$verified" ]]; then
            # Fall back to re-reading the installed path (legacy behaviour).
            verified="$(librknnrt_current_version || true)"
        fi
    fi
    rm -f "$tmp"
    if [[ -z "$verified" ]]; then
        log "librknnrt installed and SHA256-verified; skipping version-string re-check ('strings' not available yet)"
    elif [[ "$verified" != "$LIBRKNNRT_EXPECTED_VERSION" ]]; then
        die "librknnrt version after install is '$verified', expected $LIBRKNNRT_EXPECTED_VERSION"
    else
        log "librknnrt pinned to $verified (backup: ${LIBRKNNRT_BACKUP:-none})"
    fi
}

# -------- (4) rkllama clone + venv ---------------------------------------

install_rkllama() {
    if [[ -d "$RKLLAMA_DIR/.git" ]]; then
        log "rkllama checkout exists at $RKLLAMA_DIR — fetching + checking out ref"
        run_as_user git -C "$RKLLAMA_DIR" fetch --all --tags --quiet
    else
        log "cloning $RKLLAMA_REPO -> $RKLLAMA_DIR"
        run_as_user mkdir -p "$(dirname "$RKLLAMA_DIR")"
        run_as_user git clone --quiet "$RKLLAMA_REPO" "$RKLLAMA_DIR"
    fi
    run_as_user git -C "$RKLLAMA_DIR" checkout --quiet "$RKLLAMA_REF"
    log "rkllama pinned to $(run_as_user git -C "$RKLLAMA_DIR" rev-parse --short HEAD)"

    # rkllama's transitive deps (notably webrtcvad) need a C toolchain to
    # build wheels from source — Pi images often ship without these. Pull
    # them via apt before the venv install so `pip install -e .` doesn't
    # die with "Failed building wheel for webrtcvad" on a fresh box.
    if command -v apt-get >/dev/null 2>&1; then
        local _need=()
        command -v gcc >/dev/null 2>&1 || _need+=("build-essential")
        dpkg-query -W python3-dev >/dev/null 2>&1 || _need+=("python3-dev")
        dpkg-query -W libffi-dev  >/dev/null 2>&1 || _need+=("libffi-dev")
        # `strings` (binutils) is used by the librknnrt version checks; minimal
        # Pi images can lack it (#783). Ensure it is present.
        command -v strings >/dev/null 2>&1 || _need+=("binutils")
        if (( ${#_need[@]} )); then
            log "installing build deps for rkllama wheel compilation: ${_need[*]}"
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${_need[@]}" \
                || warn "apt-get install of build deps failed — wheel builds may still fail"
        fi
    fi

    # Build the venv in-place (matches the production layout).
    if [[ ! -d "$RKLLAMA_VENV" ]]; then
        log "creating rkllama venv at $RKLLAMA_VENV"
        run_as_user python3 -m venv "$RKLLAMA_VENV"
    fi
    run_as_user "$RKLLAMA_VENV/bin/pip" install --quiet --upgrade pip wheel
    # rkllama ships pyproject.toml — install it editable so the
    # rkllama_server entrypoint lands in the venv's bin/ dir. Run pip from
    # inside RKLLAMA_DIR because pyproject.toml uses relative `file:./...`
    # URLs for the rknn-toolkit-lite2 wheel, and pip resolves them against
    # the current working directory rather than the package source.
    log "installing rkllama into the venv (editable)"
    run_as_user sh -c "cd '$RKLLAMA_DIR' && '$RKLLAMA_VENV/bin/pip' install --quiet -e ."

    # huggingface-cli is used for model pulls below; it's a dep of
    # rkllama itself but we ensure it's callable from the venv.
    if ! run_as_user test -x "$RKLLAMA_VENV/bin/huggingface-cli"; then
        run_as_user "$RKLLAMA_VENV/bin/pip" install --quiet 'huggingface_hub[cli]'
    fi
}

# -------- (5) model pulls -------------------------------------------------

# rkllama expects <models_dir>/<model_name>/{Modelfile,<weight>.rkllm}.
# We create the directory, drop a Modelfile (so the tokenizer is pulled
# on first load from the Qwen upstream repo), and fetch the .rkllm
# weight file. Skipped cleanly if the weight already exists.
fetch_model() {
    local model_name="$1" local_weight="$2" url="$3" hf_tokenizer="$4"
    local system_prompt="$5" temperature="$6" expected_sha="$7"
    local dir="$RKLLAMA_MODELS/$model_name"
    local weight="$dir/$local_weight"

    run_as_user mkdir -p "$dir/cache"
    if [[ -f "$weight" ]]; then
        local sz
        sz="$(stat -c%s "$weight" 2>/dev/null || echo 0)"
        if (( sz > 100 * 1024 * 1024 )); then
            log "model $model_name already present ($(( sz / 1024 / 1024 )) MiB) — verifying checksum"
            verify_sha256 "$weight" "$expected_sha" "$model_name"
        else
            warn "$weight is only $sz bytes, looks truncated — re-downloading"
            run_as_user rm -f "$weight"
        fi
    fi

    if [[ ! -f "$weight" ]]; then
        log "downloading $model_name weight -> $weight"
        if ! run_as_user curl -fSL --retry 3 --retry-delay 2 \
                -o "$weight.part" "$url"; then
            run_as_user rm -f "$weight.part"
            die "failed to download $url — if there is no public RKLLM mirror for this model, set TAOS_QMD_EXPANSION_URL or pre-place the file at $weight and re-run"
        fi
        verify_sha256 "$weight.part" "$expected_sha" "$model_name"
        run_as_user mv "$weight.part" "$weight"
    fi

    # Always refresh the Modelfile so rkllama knows which HF repo to
    # pull the tokenizer/chat template from.
    run_as_user tee "$dir/Modelfile" >/dev/null <<EOF
FROM=$local_weight
HUGGINGFACE_PATH=$hf_tokenizer
SYSTEM=$system_prompt
TEMPERATURE=$temperature
EOF
}

pull_models() {
    run_as_user mkdir -p "$RKLLAMA_MODELS"

    fetch_model "qwen3-embedding-0.6b" "$EMBEDDING_LOCAL_NAME" "$EMBEDDING_URL" \
        "$EMBEDDING_HF_TOKENIZER" \
        "You are a helpful AI assistant." "0.7" "$EMBEDDING_SHA256"

    fetch_model "qwen3-reranker-0.6b" "$RERANKER_LOCAL_NAME" "$RERANKER_URL" \
        "$RERANKER_HF_TOKENIZER" \
        "You are a helpful AI assistant." "0.1" "$RERANKER_SHA256"

    fetch_model "qmd-query-expansion" "$QMD_EXPANSION_LOCAL_NAME" "$QMD_EXPANSION_URL" \
        "$QMD_EXPANSION_HF_TOKENIZER" \
        "You are a search query expansion assistant." "0.1" "$QMD_EXPANSION_SHA256"
}

# -------- (6) systemd unit -----------------------------------------------

install_systemd_unit() {
    local unit="/etc/systemd/system/rkllama.service"
    local exec_start
    # This ExecStart line is copy-of-truth from the live orange pi:
    #   rkllama_server --processor rk3588 --port 8080 \
    #     --models /home/jay/rkllama/models \
    #     --preload qwen3-embedding-0.6b,qwen3-reranker-0.6b,qmd-query-expansion
    exec_start="$RKLLAMA_VENV/bin/python $RKLLAMA_VENV/bin/rkllama_server --processor $SOC --port $RKLLAMA_PORT --models $RKLLAMA_MODELS --preload qwen3-embedding-0.6b,qwen3-reranker-0.6b,qmd-query-expansion"

    log "installing $unit"
    sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=rkllama — Rockchip NPU LLM / embedding / reranker server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$RKLLAMA_DIR
Environment=PYTHONUNBUFFERED=1
# rkllama spawns multiprocessing children that occasionally outlive the
# parent if it crashes (e.g. during NPU model load). Without this hook
# the orphans keep listening on the port and the next restart can't bind.
ExecStartPre=-/usr/bin/pkill -9 -f $RKLLAMA_VENV/bin/rkllama_server
ExecStart=$exec_start
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now rkllama.service
    log "rkllama.service enabled + started"
}

wait_for_rkllama() {
    local i
    for (( i = 0; i < 30; i++ )); do
        if curl -fs "http://localhost:$RKLLAMA_PORT/api/tags" >/dev/null 2>&1; then
            log "rkllama HTTP API is up on :$RKLLAMA_PORT"
            return 0
        fi
        sleep 1
    done
    die "rkllama HTTP API did not come up within 30s — check: sudo journalctl -u rkllama -n 100"
}

# -------- (7) end-to-end verify ------------------------------------------

verify_models() {
    local tags want model missing=0
    tags="$(curl -fs "http://localhost:$RKLLAMA_PORT/api/tags" || true)"
    log "models reported by rkllama:"
    echo "$tags"
    for want in qwen3-embedding-0.6b qwen3-reranker-0.6b qmd-query-expansion; do
        if ! grep -q "\"$want\"" <<<"$tags"; then
            warn "model missing from /api/tags: $want"
            missing=1
        fi
    done
    if (( missing )); then
        die "one or more preloaded models are missing — see list above"
    fi
    log "all three preloaded models are present"
}

# -------- (8) summary + idempotency check --------------------------------

already_installed() {
    # All of:
    #   * librknnrt at 2.3.0
    #   * rkllama checkout on the pinned ref
    #   * three model weights present
    #   * systemd unit enabled
    #   * HTTP API responding with all three models
    [[ "$(librknnrt_current_version 2>/dev/null || true)" == "$LIBRKNNRT_EXPECTED_VERSION" ]] || return 1
    [[ -d "$RKLLAMA_DIR/.git" ]] || return 1
    [[ "$(run_as_user git -C "$RKLLAMA_DIR" rev-parse HEAD 2>/dev/null || true)" == "$RKLLAMA_REF" ]] || return 1
    [[ -f "$RKLLAMA_MODELS/qwen3-embedding-0.6b/$EMBEDDING_LOCAL_NAME" ]] || return 1
    [[ -f "$RKLLAMA_MODELS/qwen3-reranker-0.6b/$RERANKER_LOCAL_NAME" ]] || return 1
    [[ -f "$RKLLAMA_MODELS/qmd-query-expansion/$QMD_EXPANSION_LOCAL_NAME" ]] || return 1
    systemctl is-enabled rkllama.service >/dev/null 2>&1 || return 1
    local tags
    tags="$(curl -fs "http://localhost:$RKLLAMA_PORT/api/tags" 2>/dev/null || true)"
    grep -q '"qwen3-embedding-0.6b"' <<<"$tags" || return 1
    grep -q '"qwen3-reranker-0.6b"' <<<"$tags" || return 1
    grep -q '"qmd-query-expansion"' <<<"$tags" || return 1
    return 0
}

print_summary() {
    cat <<EOF

  =================================================================
  rkllama + RKNPU runtime installed successfully
  =================================================================
    librknnrt:     $LIBRKNNRT_DEST  (version $LIBRKNNRT_EXPECTED_VERSION)
    backup:        ${LIBRKNNRT_BACKUP:-<none — no previous install>}
    rkllama dir:   $RKLLAMA_DIR
    rkllama ref:   ${RKLLAMA_REF:0:12}
    models dir:    $RKLLAMA_MODELS
    preloaded:     qwen3-embedding-0.6b, qwen3-reranker-0.6b, qmd-query-expansion
    HTTP endpoint: http://localhost:$RKLLAMA_PORT
    systemd unit:  /etc/systemd/system/rkllama.service

  Check status:  sudo systemctl status rkllama
  Tail logs:     sudo journalctl -u rkllama -f
  Restore libs:  sudo cp ${LIBRKNNRT_BACKUP:-<backup>} $LIBRKNNRT_DEST && sudo ldconfig

EOF
}

# -------- main ------------------------------------------------------------

main() {
    log "TinyAgentOS RKNPU + rkllama installer starting"
    log "binaries pulled from TAOS mirror at jaysom/tinyagentos-rockchip-mirror (HF) — see docs/mirror-policy.md"

    detect_board
    detect_rknpu_driver

    if already_installed; then
        log "already fully installed — nothing to do"
        print_summary
        exit 0
    fi

    confirm_or_exit

    pin_librknnrt
    install_rkllama
    pull_models
    install_systemd_unit
    wait_for_rkllama
    verify_models
    print_summary
}

main "$@"
