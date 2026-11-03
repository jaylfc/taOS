#!/usr/bin/env bash
set -euo pipefail
SCRIPT=scripts/install-llama-cpp.sh

unset TAOS_LLAMACPP_VARIANT

bash -n "$SCRIPT"
grep -q 'LLAMACPP_MUSL_COMMIT="0ec4027a2a0183de87c007069adddc06e665f7de"' "$SCRIPT"

FN_DETECT="$(sed -n '/^detect_variant() {/,/^}/p' "$SCRIPT")"
FN_OPENRC="$(awk '
  /^install_openrc_service\(\) {/ { p=1; d=1; print; next }
  p { print; d+=gsub(/{/,"{"); d-=gsub(/}/,"}"); if(d==0){p=0; exit} }
' "$SCRIPT")"
FN_SVC="$(sed -n '/^install_service() {/,/^}/p' "$SCRIPT")"

(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  LLAMACPP_MUSL_TAG=b12020
  is_musl() { return 0; }
  uname() { case "$1" in -s) echo Linux;; -m) echo aarch64;; *) command uname "$@";; esac; }
  eval "$FN_DETECT"
  detect_variant
  [ "$VARIANT" = "musl-arm64" ]
) || { echo "FAIL: musl-arm64 detection"; exit 1; }

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
echo "$out" | grep -q "DIE:" || { echo "FAIL: non-musl aarch64 should die"; exit 1; }

(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  uname() { case "$1" in -s) echo Linux;; -m) echo x86_64;; *) command uname "$@";; esac; }
  command() { return 1; }
  has_rocm() { return 1; }
  has_nvidia() { return 1; }
  is_musl() { return 1; }
  unset TAOS_LLAMACPP_VARIANT
  eval "$FN_DETECT"
  detect_variant
  [ "$VARIANT" = "cpu" ]
) || { echo "FAIL: x86_64 cpu variant"; exit 1; }

# Test that summary pin fallback string no longer appears in script
if grep -q 'LLAMACPP_MUSL_TAG:-' "$SCRIPT"; then
  echo "FAIL: summary pin fallback"
  exit 1
fi

# Test OpenRC service installation path via install_service dispatch
INIT_D_DIR="$(mktemp -d)"
(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  TARGET_USER="testuser"
  TARGET_GROUP="testgroup"
  INSTALL_DIR="/opt/llama-cpp"
  MODELS_DIR="/data/models"
  PORT=7835
  HOST="127.0.0.1"

  # Stub every host-touching command so nothing reaches the real system
  sudo() { "$@"; }
  rc-update() { echo "rc-update $*" >> "$INIT_D_DIR/calls"; }
  rc-service() { echo "rc-service $*" >> "$INIT_D_DIR/calls"; }
  install_linux_service() { echo "WRONG: systemd path" >> "$INIT_D_DIR/calls"; return 1; }
  install_macos_service() { return 1; }
  uname() { echo Linux; }

  # Make service-manager detection deterministic:
  # command -v systemctl -> 1 (absent), command -v openrc-run -> 0 (present)
  command() {
    case "$1" in
      -v)
        case "$2" in
          systemctl) return 1 ;;
          openrc-run) return 0 ;;
        esac ;;
    esac
    builtin command "$@"
  }

  eval "$FN_SVC"
  eval "$FN_OPENRC"
  install_service

  # Check if init script was created
  if [[ ! -f "$INIT_D_DIR/llama-cpp" ]]; then
    echo "FAIL: OpenRC init script not created at $INIT_D_DIR/llama-cpp"
    exit 1
  fi

  # Check content
  if ! grep -q '#!/sbin/openrc-run' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing shebang"
    exit 1
  fi

  if ! grep -q 'command="/opt/llama-cpp/llama-server"' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing correct command"
    cat "$INIT_D_DIR/llama-cpp"
    exit 1
  fi

  if ! grep -q 'command_args="--models-dir \\"/data/models\\" --host 127.0.0.1 --port 7835"' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing quoted models path in command_args"
    cat "$INIT_D_DIR/llama-cpp"
    exit 1
  fi

  if ! grep -q 'export LD_LIBRARY_PATH="/opt/llama-cpp"' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing LD_LIBRARY_PATH in start_pre"
    cat "$INIT_D_DIR/llama-cpp"
    exit 1
  fi

  if ! grep -q 'command_user="testuser"' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing command_user"
    exit 1
  fi

  if ! grep -q 'command_background=true' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing command_background"
    exit 1
  fi

  if ! grep -q 'pidfile="/run/llama-cpp.pid"' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing pidfile"
    exit 1
  fi

  if ! grep -q 'need net' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing depend()"
    exit 1
  fi

  # calls file must record the OpenRC enable + restart and must not
  # leak into the systemd path
  if ! grep -q 'rc-update add llama-cpp default' "$INIT_D_DIR/calls"; then
    echo "FAIL: calls missing rc-update add llama-cpp default"
    cat "$INIT_D_DIR/calls"
    exit 1
  fi

  if ! grep -q 'rc-service llama-cpp restart' "$INIT_D_DIR/calls"; then
    echo "FAIL: calls missing rc-service llama-cpp restart"
    cat "$INIT_D_DIR/calls"
    exit 1
  fi

  if grep -q 'WRONG: systemd path' "$INIT_D_DIR/calls"; then
    echo "FAIL: calls leaked into the systemd path"
    cat "$INIT_D_DIR/calls"
    exit 1
  fi

  echo "PASS: OpenRC service installation test"
) || { echo "FAIL: OpenRC test case"; exit 1; }

rm -rf "$INIT_D_DIR"

echo "all tests passed"