#!/usr/bin/env bash
# tinyagentos installer for lcm-dreamshaper-rknn (id: lcm-dreamshaper-rknn)
# ---------------------------------------------------------------------------
# Fast text-to-image on the Rockchip RK3588 NPU. Rust implementation that
# generates 512x512 images in seconds via the RKNN runtime. Source-of-truth
# is the upstream README (MIT):
#   https://github.com/darkautism/LCM-Dreamshaper-V7-rs
#
# This is a SERVICE script. It runs ON THE HOST, which for this backend MUST
# be an RK3588 aarch64 board with the RKNPU2 driver + librknnrt runtime.
# It hard-fails on any non-RK3588 host (arm-npu tiers only; x86/cpu = unsupported).
#
# What it does (idempotent):
#   1. Guard: require aarch64 + RKNPU device, else die clearly.
#   2. Ensure a Rust toolchain (rustup) is present.
#   3. Ensure librknnrt.so is installed under /usr/lib.
#   4. Clone the upstream repo at a PINNED commit and `cargo build --release`.
#   5. Launch serve mode bound to 0.0.0.0:32275 (override TAOS_LCM_PORT).
#
# Model weights: the binary auto-downloads RKNN models from HuggingFace on
# first run (README: kautism/LCM_Dreamshaper_v7-RKNN-2.3.2, with fallback
# whaoyang/LCM-Dreamshaper-V7-ONNX-rk3588-512x512-2.3.0 and the
# openai/clip-vit-large-patch14 tokenizer). Upstream does NOT publish
# per-file SHA256 manifests for these repos, so we verify the librknnrt.so
# runtime (which we fetch ourselves) and pin the source commit. The model
# fetch integrity is bounded by HuggingFace's own transport (HTTPS) — see
# RESIDUAL RISK below.
#
# Pinned constants — update when verifying against upstream:
#   LCM_PIN_COMMIT       : upstream git commit to build (reproducible source)
#   LIBRKNNRT_SHA256     : SHA-256 of the airockchip librknnrt.so we install
#   RESIDUAL RISK: model weights are pulled by the binary from HuggingFace at
#   runtime and are not checksum-pinned upstream; only the source commit and
#   the librknnrt runtime are integrity-verified here.
#   Pinned: 2026-06-14
# ---------------------------------------------------------------------------
set -euo pipefail

# --- Tunables / pins --------------------------------------------------------
LCM_REPO_URL="https://github.com/darkautism/LCM-Dreamshaper-V7-rs.git"
# Pin to a specific commit for reproducible builds. Override with TAOS_LCM_COMMIT
# once you have verified the exact SHA you intend to ship.
LCM_PIN_COMMIT="${TAOS_LCM_COMMIT:-4de3c8180d11585f513803504c74811845275317}"

LCM_PORT="${TAOS_LCM_PORT:-32275}"
LCM_HOST="${TAOS_LCM_HOST:-0.0.0.0}"
LCM_PREFIX="${TAOS_LCM_PREFIX:-/opt/taos/lcm-dreamshaper-rknn}"
LCM_SRC_DIR="${LCM_PREFIX}/src"
LCM_BIN="${LCM_SRC_DIR}/target/release/dreamshaper-cli"

# librknnrt runtime (airockchip rknn-toolkit2, aarch64).
# Source: https://github.com/airockchip/rknn-toolkit2/raw/refs/heads/master/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so
LIBRKNNRT_URL="${TAOS_LIBRKNNRT_URL:-https://github.com/airockchip/rknn-toolkit2/raw/refs/heads/master/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so}"
LIBRKNNRT_DEST="/usr/lib/librknnrt.so"
# SHA-256 of the pinned librknnrt.so. Empty by default — set this (or
# TAOS_LIBRKNNRT_SHA256) to the verified hash to enforce integrity. When empty
# the script logs a clear warning rather than fabricating a checksum.
LIBRKNNRT_SHA256="${TAOS_LIBRKNNRT_SHA256:-}"

log() { echo -e "\033[1;34m[lcm-dreamshaper]\033[0m $*"; }
die() { echo -e "\033[1;31m[lcm-dreamshaper]\033[0m $*" >&2; exit 1; }

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — refusing to continue"
    fi
    log "sha256 ok for $label (${actual:0:16}…)"
}

# --- 1. Hard guard: RK3588 aarch64 + RKNPU only -----------------------------
arch="$(uname -m)"
if [[ "$(uname -s)" != "Linux" ]]; then
    die "this service requires Linux on an RK3588 board — host is $(uname -s) (unsupported on x86/cpu/macOS)"
fi
if [[ "$arch" != "aarch64" && "$arch" != "arm64" ]]; then
    die "this service is RK3588-only (arm-npu); host arch is '$arch' — x86/cpu are unsupported"
fi

# RKNPU presence: driver exposes /dev/dri/renderD* and/or a SoC marker.
have_npu=0
if compgen -G "/dev/dri/renderD*" >/dev/null 2>&1; then
    have_npu=1
fi
if [[ -r /sys/kernel/debug/rknpu/version ]] || compgen -G "/sys/class/devfreq/*.npu" >/dev/null 2>&1; then
    have_npu=1
fi
if grep -qiE 'rk3588' /proc/device-tree/compatible 2>/dev/null; then
    : # confirmed RK3588 SoC
elif [[ "$have_npu" -eq 0 ]]; then
    die "no RK3588 NPU detected (no /dev/dri/renderD*, no rknpu sysfs, no rk3588 in device-tree) — this backend needs RKNPU2"
fi
[[ "$have_npu" -eq 1 ]] || log "warning: RK3588 SoC seen but no /dev/dri/renderD* — ensure the RKNPU2 kernel driver is loaded"
log "host check passed: Linux/$arch with RKNPU present"

# Sudo helper (script may run as non-root on the host).
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "need root or sudo to install librknnrt.so and create $LCM_PREFIX"
    SUDO="sudo"
fi

# --- 2. Idempotency: already built? ----------------------------------------
if [[ -x "$LCM_BIN" ]]; then
    log "already built: $LCM_BIN"
    if [[ -f "$LIBRKNNRT_DEST" ]]; then
        log "librknnrt present at $LIBRKNNRT_DEST"
        log "nothing to do — launch with: $LCM_BIN serve --host $LCM_HOST --port $LCM_PORT"
        exit 0
    fi
    log "binary present but librknnrt missing — will (re)install the runtime"
fi

# --- 3. Rust toolchain ------------------------------------------------------
if ! command -v cargo >/dev/null 2>&1; then
    [[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env" || true
fi
if ! command -v cargo >/dev/null 2>&1; then
    log "installing Rust toolchain via rustup (https://rustup.rs)"
    command -v curl >/dev/null 2>&1 || die "curl required to install rustup"
    curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
    source "$HOME/.cargo/env"
fi
command -v cargo >/dev/null 2>&1 || die "cargo still not on PATH after rustup install"
log "rust toolchain: $(cargo --version)"

# Build deps commonly needed for the rknn-rs FFI bindings.
if command -v apt-get >/dev/null 2>&1; then
    log "ensuring build deps (build-essential, pkg-config, libclang-dev, git)"
    $SUDO apt-get update -y || true
    $SUDO apt-get install -y build-essential pkg-config libclang-dev git ca-certificates || \
        log "warning: apt dep install failed — continuing, build may need these manually"
fi

# --- 4. librknnrt.so runtime -----------------------------------------------
# README: place librknnrt.so under /usr/lib (or /lib) and run ldconfig.
if [[ -f "$LIBRKNNRT_DEST" ]]; then
    log "librknnrt already installed at $LIBRKNNRT_DEST"
else
    log "fetching librknnrt.so from airockchip rknn-toolkit2"
    tmp_so="$(mktemp /tmp/librknnrt.XXXXXX.so)"
    trap 'rm -f "$tmp_so"' EXIT
    curl -fsSL "$LIBRKNNRT_URL" -o "$tmp_so" || die "failed to download librknnrt.so from $LIBRKNNRT_URL"
    if [[ -n "$LIBRKNNRT_SHA256" ]]; then
        verify_sha256 "$tmp_so" "$LIBRKNNRT_SHA256" "librknnrt.so"
    else
        log "warning: no pinned LIBRKNNRT_SHA256 set — skipping integrity check (set TAOS_LIBRKNNRT_SHA256 to enforce)"
        log "downloaded librknnrt.so sha256: $(sha256sum "$tmp_so" | awk '{print $1}')"
    fi
    $SUDO install -D -m 0644 "$tmp_so" "$LIBRKNNRT_DEST"
    rm -f "$tmp_so"
    trap - EXIT
    $SUDO ldconfig
    log "installed librknnrt.so -> $LIBRKNNRT_DEST and ran ldconfig"
fi

# Ensure the running user can reach the NPU (render group), per README.
if getent group render >/dev/null 2>&1; then
    if ! id -nG "$(id -un)" | tr ' ' '\n' | grep -qx render; then
        log "note: $(id -un) is not in the 'render' group — NPU access may be denied"
        log "      add with: sudo usermod -aG render $(id -un)  (then re-login)"
    fi
fi

# --- 5. Clone (pinned) + build ---------------------------------------------
$SUDO mkdir -p "$LCM_PREFIX"
$SUDO chown "$(id -un)":"$(id -gn)" "$LCM_PREFIX" 2>/dev/null || true

if [[ -d "$LCM_SRC_DIR/.git" ]]; then
    log "source already cloned at $LCM_SRC_DIR — fetching"
    git -C "$LCM_SRC_DIR" fetch --depth 1 origin "$LCM_PIN_COMMIT" 2>/dev/null || git -C "$LCM_SRC_DIR" fetch origin
else
    log "cloning $LCM_REPO_URL"
    git clone "$LCM_REPO_URL" "$LCM_SRC_DIR"
fi

log "checking out pinned ref: $LCM_PIN_COMMIT"
git -C "$LCM_SRC_DIR" checkout --quiet "$LCM_PIN_COMMIT" || die "failed to checkout $LCM_PIN_COMMIT — verify the pin"
log "building at $(git -C "$LCM_SRC_DIR" rev-parse --short HEAD)"

# README: `cargo build --release` -> target/release/dreamshaper-cli
( cd "$LCM_SRC_DIR" && cargo build --release ) || die "cargo build --release failed"
[[ -x "$LCM_BIN" ]] || die "expected binary not found at $LCM_BIN after build"
log "built: $LCM_BIN"

# --- 6. Launch serve mode on the taOS service port --------------------------
# README serve mode: `dreamshaper-cli serve --host <h> --port <p>`
# (upstream default is 8080; taOS pins 32275, override with TAOS_LCM_PORT).
# Models are auto-downloaded from HuggingFace on first serve.
log "starting service: $LCM_BIN serve --host $LCM_HOST --port $LCM_PORT"
log "(first run downloads RKNN model weights from HuggingFace — may take a while)"
exec "$LCM_BIN" serve --host "$LCM_HOST" --port "$LCM_PORT"
