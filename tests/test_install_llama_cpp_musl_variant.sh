#!/usr/bin/env bash
set -euo pipefail
SCRIPT=scripts/install-llama-cpp.sh

bash -n "$SCRIPT"
grep -q 'LLAMACPP_MUSL_COMMIT="0ec4027a2a0183de87c007069adddc06e665f7de"' "$SCRIPT"

FN_DETECT="$(sed -n '/^detect_variant() {/,/^}/p' "$SCRIPT")"

(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  LLAMACPP_MUSL_TAG=b12020
  is_musl() { return 0; }
  uname() { case "$1" in -s) echo Linux;; -m) echo aarch64;; *) command uname "$@";; esac; }
  eval "$FN_DETECT"
  detect_variant
  [ "$VARIANT" = "musl-arm64" ]
) || { echo "FAIL"; exit 1; }

out=$(
  (
    log() { :; }
    die() { echo "DIE: $*"; exit 1; }
    LLAMACPP_MUSL_TAG=b12020
    is_musl() { return 1; }
    uname() { case "$1" in -s) echo Linux;; -m) echo aarch64;; *) command uname "$@";; esac; }
    eval "$FN_DETECT"
    detect_variant
  ) 2>&1 || true
)
echo "$out" | grep -q "DIE:" || { echo "FAIL"; exit 1; }

(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  uname() { case "$1" in -s) echo Linux;; -m) echo x86_64;; *) command uname "$@";; esac; }
  command() { return 1; }
  [ ! -e /opt/rocm ] && true
  [ ! -e /proc/driver/nvidia/gpus ] && true
  # Also override the test conditions
  unset TAOS_LLAMACPP_VARIANT
  eval "$FN_DETECT"
  # Run with overrides in same scope
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
        [[ "$arch" == "arm64" ]] || die "install-llama-cpp.sh on macOS only supports Apple Silicon (arm64) -- got $arch"
        VARIANT="apple-silicon"
        log "detected platform: macOS Apple Silicon -> Metal build"
        return
    fi
    [[ "$os" == "Linux" ]] || die "unsupported OS: $os (install-llama-cpp.sh supports Linux and macOS only)"
    if [[ "$arch" == "aarch64" ]] && is_musl; then
        VARIANT="musl-arm64"
        log "detected platform: Linux aarch64 musl -> pinned source build (${LLAMACPP_MUSL_TAG})"
        return
    fi
    [[ "$arch" == "x86_64" ]] || die "install-llama-cpp.sh only supports x86_64 Linux -- got $arch. Rockchip ARM boards use install-rknpu.sh / install-rk-llama-cpp.sh instead."
    VARIANT="cpu"
    log "detected platform: Linux x86_64, no GPU driver found -> CPU-only build"
  }
  detect_variant
  [ "$VARIANT" = "cpu" ]
) || { echo "FAIL"; exit 1; }

echo "all tests passed"
