#!/bin/bash
# tinyagentos installer for Piper TTS (rhasspy/piper)
# ---------------------------------------------------------------------------
# Downloads a prebuilt piper binary from upstream releases. piper itself
# is small; voices (.onnx + .json files) are downloaded separately by the
# per-voice catalog manifest.
#
# Environment overrides:
#   TAOS_PIPER_DIR  install dir (default: ~/piper)
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[piper]\033[0m $*"; }
die() { echo -e "\033[1;31m[piper]\033[0m $*" >&2; exit 1; }

INSTALL_DIR="${TAOS_PIPER_DIR:-$HOME/piper}"
PIPER_VERSION="${TAOS_PIPER_VERSION:-2023.11.14-2}"

case "$(uname -s)/$(uname -m)" in
    Linux/x86_64)  ASSET="piper_linux_x86_64.tar.gz" ;;
    Linux/aarch64) ASSET="piper_linux_aarch64.tar.gz" ;;
    Darwin/arm64)  ASSET="piper_macos_aarch64.tar.gz" ;;
    Darwin/x86_64) ASSET="piper_macos_x64.tar.gz" ;;
    *) die "no piper prebuilt for $(uname -s)/$(uname -m); build from source or use a different TTS";;
esac

if [[ -x "$INSTALL_DIR/piper/piper" ]]; then
    log "piper already installed at $INSTALL_DIR/piper/piper — skipping"
    exit 0
fi

mkdir -p "$INSTALL_DIR"
log "downloading $ASSET"
curl -fsSL "https://github.com/rhasspy/piper/releases/download/$PIPER_VERSION/$ASSET" \
    -o "$INSTALL_DIR/$ASSET"
log "extracting"
tar -xzf "$INSTALL_DIR/$ASSET" -C "$INSTALL_DIR"
rm -f "$INSTALL_DIR/$ASSET"
log "done: $INSTALL_DIR/piper/piper"
