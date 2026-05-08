#!/bin/bash
# tinyagentos rk-llama.cpp installer
# ---------------------------------------------------------------------------
# Downloads the pre-compiled rk-llama.cpp binary for RK3588 (Orange Pi 5+
# with the rknpu kernel driver) and installs it as a systemd unit. This is
# a second NPU backend alongside rkllama — useful for models that the
# rkllm-toolkit doesn't yet support (Gemma 4, Qwen 3.5+, etc).
#
# Requirements:
#   * RK3588 board (Orange Pi 5 / 5+ / 5 Max etc)
#   * librknnrt.so installed at /usr/lib/librknnrt.so (install-rknpu.sh
#     does this; running this script before that will fail)
#
# Environment overrides:
#   TAOS_RKLLAMACPP_DIR     install dir (default: ~<user>/rk-llama.cpp)
#   TAOS_RKLLAMACPP_PORT    server port (default: 8090)
#   TAOS_MIRROR_BASE        binary mirror base URL
#   TAOS_RKLLAMACPP_SHA256  override the expected tarball checksum (advanced)
# ---------------------------------------------------------------------------
set -euo pipefail

log()  { echo -e "\033[1;34m[rkllamacpp]\033[0m $*"; }
warn() { echo -e "\033[1;33m[rkllamacpp]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[rkllamacpp]\033[0m $*" >&2; exit 1; }

# -------- target user resolution -----------------------------------------
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    TARGET_USER="$SUDO_USER"
else
    TARGET_USER="$(id -un)"
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -d "$TARGET_HOME" ]] || die "cannot resolve home for user $TARGET_USER"
TARGET_GROUP="$(id -gn "$TARGET_USER")"

INSTALL_DIR="${TAOS_RKLLAMACPP_DIR:-$TARGET_HOME/rk-llama.cpp}"
PORT="${TAOS_RKLLAMACPP_PORT:-8090}"
# llama-server's --host bind. Default to loopback so the API isn't
# exposed to the LAN without explicit opt-in (CodeRabbit on PR #339).
# Override via TAOS_RKLLAMACPP_HOST=0.0.0.0 to allow LAN access — only
# do this if a reverse proxy with auth sits in front, or you trust the
# entire LAN.
HOST="${TAOS_RKLLAMACPP_HOST:-127.0.0.1}"
MIRROR_BASE="${TAOS_MIRROR_BASE:-https://huggingface.co/jaylfc/tinyagentos-rockchip-mirror/resolve/main}"
TARBALL_URL="${MIRROR_BASE}/binaries/rkllamacpp-aarch64-rk3588.tar.gz"

# Pinned SHA-256 of the published binary tarball. If you change the
# tarball, regenerate this hash and bump it here. Set
# TAOS_RKLLAMACPP_SHA256 to override (e.g., when testing a fork mirror).
EXPECTED_SHA256="${TAOS_RKLLAMACPP_SHA256:-4ea99886e874a2a4f934f4eade1f289a5ae516cfba9556acf57aa83cfa86f261}"

run_as_user() {
    if [[ "$(id -un)" == "$TARGET_USER" ]]; then
        "$@"
    else
        sudo -u "$TARGET_USER" -H "$@"
    fi
}

# -------- precondition checks --------------------------------------------
[[ "$(uname -m)" == "aarch64" ]] || die "rk-llama.cpp is aarch64-only (got $(uname -m))"
[[ -f /usr/lib/librknnrt.so ]] || die "librknnrt.so missing — run install-rknpu.sh first"
command -v sha256sum >/dev/null || die "sha256sum not found — install coreutils"

# -------- download + verify ----------------------------------------------
log "preparing $INSTALL_DIR for user $TARGET_USER"
run_as_user mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/models" "$INSTALL_DIR/lib"

TMP_TARBALL=$(mktemp -t rkllamacpp.XXXXXX.tar.gz)
trap 'rm -f "$TMP_TARBALL"' EXIT

log "downloading $TARBALL_URL"
curl -sSfL "$TARBALL_URL" -o "$TMP_TARBALL" \
    || die "failed to download $TARBALL_URL"

ACTUAL_SHA256=$(sha256sum "$TMP_TARBALL" | cut -d' ' -f1)
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
    die "checksum mismatch — expected $EXPECTED_SHA256, got $ACTUAL_SHA256. Refusing to extract a binary that doesn't match the pinned hash."
fi
log "checksum ok: $ACTUAL_SHA256"

# Make the tarball readable by TARGET_USER. mktemp creates files with
# 0600 permissions owned by the current shell (root when invoked via
# sudo), so a subsequent `run_as_user tar` would fail with "Permission
# denied". Either fix-up route works; chown is the cleanest.
if [[ "$(id -un)" != "$TARGET_USER" ]]; then
    chown "$TARGET_USER:$TARGET_GROUP" "$TMP_TARBALL"
fi

log "extracting into $INSTALL_DIR"
run_as_user tar -xzf "$TMP_TARBALL" -C "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/bin/llama-server" 2>/dev/null || true

# Default active.alias — rkllamacpp installer rewrites this file every
# time a model becomes active so /v1/models advertises the manifest id
# (e.g. gemma-4-e2b-gguf) instead of the generic "active.gguf". The
# systemd unit reads it via EnvironmentFile= and passes the value as
# --alias. Pre-create with a sentinel so the unit starts cleanly even
# before the first model install (llama-server will refuse to load
# without -m, which is fine — the unit is disabled until the store
# install activates it anyway).
sudo -u "$TARGET_USER" tee "$INSTALL_DIR/active.alias" > /dev/null <<'ALIAS'
TAOS_ACTIVE_ALIAS=active.gguf
ALIAS

# -------- systemd unit ---------------------------------------------------
UNIT_PATH="/etc/systemd/system/rkllamacpp.service"
log "writing $UNIT_PATH (port $PORT)"
sudo tee "$UNIT_PATH" > /dev/null << UNIT
[Unit]
Description=rk-llama.cpp (llama-server on RK3588 NPU)
After=network.target

[Service]
Type=simple
User=$TARGET_USER
Group=$TARGET_GROUP
WorkingDirectory=$INSTALL_DIR
Environment=LD_LIBRARY_PATH=$INSTALL_DIR/lib
EnvironmentFile=$INSTALL_DIR/active.alias
LimitNOFILE=65536
ExecStart=$INSTALL_DIR/bin/llama-server -m $INSTALL_DIR/active.gguf --alias \${TAOS_ACTIVE_ALIAS} --host $HOST --port $PORT
Restart=on-failure
RestartSec=5
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
log "systemd unit installed (disabled by default; first model install via Store enables it)"
log "done. install dir: $INSTALL_DIR  port: $PORT"
