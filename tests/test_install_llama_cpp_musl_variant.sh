#!/usr/bin/env bash
set -euo pipefail
SCRIPT=scripts/install-llama-cpp.sh

unset TAOS_LLAMACPP_VARIANT

bash -n "$SCRIPT"
grep -q 'LLAMACPP_MUSL_COMMIT="0ec4027a2a0183de87c007069adddc06e665f7de"' "$SCRIPT"

# Extract is_musl function for testing
FN_IS_MUSL="$(sed -n '/^is_musl() {/,/^}/p' "$SCRIPT")"
# Extract has_rocm function for testing
FN_HAS_ROCM="$(sed -n '/^has_rocm() {/,/^}/p' "$SCRIPT")"
# Extract has_nvidia function for testing
FN_HAS_NVIDIA="$(sed -n '/^has_nvidia() {/,/^}/p' "$SCRIPT")"

# Extract build_from_source_musl function for testing
FN_BUILD="$(sed -n '/^build_from_source_musl() {/,/^}/p' "$SCRIPT")"

# Regression test: copy-failure in build_from_source_musl must die
# Simulate a failure when cp is called for a .so file
out=$(
  (
    log() { :; }
    die() { echo "DIE: $*"; exit 1; }
    LLAMACPP_MUSL_TAG=b12020
    LLAMACPP_MUSL_COMMIT="0ec4027a2a0183de87c007069adddc06e665f7de"
    INSTALL_DIR="/tmp/fake-install"
    SUDO=""

    # Mock commands
    apk() { echo "apk $*" >> /tmp/mock_calls; return 0; }
    mktemp() { echo "/tmp/fake-src"; }
    git() {
      case "$1" in
        clone) echo "git clone" >> /tmp/mock_calls; mkdir -p "/tmp/fake-src/build"; return 0 ;;
        -C) shift; shift; git "$@"; return $? ;;
        rev-parse) echo "0ec4027a2a0183de87c007069adddc06e665f7de"; return 0 ;;
        *) command git "$@" ;;
      esac
    }
    cmake() { echo "cmake" >> /tmp/mock_calls; mkdir -p "/tmp/fake-src/build"; touch "/tmp/fake-src/build/build.ninja"; return 0; }
    ninja() { echo "ninja" >> /tmp/mock_calls; return 0; }
    nproc() { echo 2; }
    nice() { shift 2; "$@"; }
    run_as_user() { "$@"; }
    find() {
      # Simulate finding one .so file
      if [[ "$*" == *"-name lib*.so*"* ]]; then
        printf "%s\0" "/tmp/fake-src/build/libggml.so"
      fi
    }
    cp() {
      # Fail on the .so copy (args: -P src dst)
      if [[ "$2" == "/tmp/fake-src/build/libggml.so" ]]; then
        return 1
      fi
      command cp "$@"
    }
    mkdir() { command mkdir "$@"; }
    chmod() { command chmod "$@"; }
    rm() { command rm "$@"; }
    trap() { :; }
    sudo() { "$@"; }

    eval "$FN_BUILD"
    build_from_source_musl
    echo "FAIL: build_from_source_musl should have died on copy failure"
    exit 1
  ) 2>&1 || true
)
echo "$out" | grep -q "DIE: copying /tmp/fake-src/build/libggml.so failed" || { echo "FAIL: copy-failure check"; exit 1; }

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
  # Use real is_musl probe with a test-controlled path
  MUSL_LINKER_PATH="$(mktemp -u)/ld-musl-aarch64.so.1"
  mkdir -p "$(dirname "$MUSL_LINKER_PATH")"
  touch "$MUSL_LINKER_PATH"
  is_musl() { [[ -e "$MUSL_LINKER_PATH" ]]; }
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
    # Real is_musl probe: no linker file -> returns false
    MUSL_LINKER_PATH="$(mktemp -u)/ld-musl-aarch64.so.1"
    is_musl() { [[ -e "$MUSL_LINKER_PATH" ]]; }
    uname() { case "$1" in -s) echo Linux;; -m) echo aarch64;; *) command uname "$@";; esac; }
    eval "$FN_DETECT"
    detect_variant
  ) 2>&1 || true
)
echo "$out" | grep -q "DIE:" || { echo "FAIL: non-musl aarch64 should die"; exit 1; }

(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  # Use real uname (Linux x86_64 on this system)
  # Use production helper functions with test-controlled paths
  eval "$FN_DETECT"
  eval "$FN_IS_MUSL"
  eval "$FN_HAS_ROCM"
  eval "$FN_HAS_NVIDIA"
  # Override helpers with test-controlled versions
  MUSL_LINKER_PATH="$(mktemp -u)/ld-musl-aarch64.so.1"
  is_musl() { [[ -e "$MUSL_LINKER_PATH" ]]; }
  ROCM_PATH="$(mktemp -u)/rocm"
  has_rocm() { [[ -d "$ROCM_PATH" ]]; }
  # Mock command to hide nvidia-smi, and control proc directory
  NVIDIA_PROC_PATH="$(mktemp -u)/proc/driver/nvidia/gpus"
  command() {
    case "$1" in
      -v)
        case "$2" in
          nvidia-smi) return 1 ;;
        esac ;;
    esac
    builtin command "$@"
  }
  has_nvidia() { command -v nvidia-smi >/dev/null 2>&1 || [[ -d "$NVIDIA_PROC_PATH" ]]; }
  unset TAOS_LLAMACPP_VARIANT
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

# (a) install_openrc_service as uid 0 with no sudo on PATH must succeed.
# On postmarketOS/Alpine running as root, sudo is often not installed, so
# the bare `sudo tee`/`sudo chmod`/`sudo rc-update`/`sudo rc-service`
# calls used to die here. The function must detect uid 0 and use empty SUDO.
INIT_D_DIR="$(mktemp -d)"
(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  TARGET_USER="root"
  TARGET_GROUP="root"
  INSTALL_DIR="/opt/llama-cpp"
  MODELS_DIR="/data/models"
  PORT=7835
  HOST="127.0.0.1"

  # No sudo anywhere: not as a function, and not on PATH.
  unset -f sudo 2>/dev/null || true
  id() { echo 0; }
  rc-update() { echo "rc-update $*" >> "$INIT_D_DIR/calls2"; }
  rc-service() { echo "rc-service $*" >> "$INIT_D_DIR/calls2"; }
  install_linux_service() { echo "WRONG: systemd path" >> "$INIT_D_DIR/calls2"; return 1; }
  install_macos_service() { return 1; }
  uname() { echo Linux; }

  command() {
    case "$1" in
      -v)
        case "$2" in
          systemctl) return 1 ;;
          openrc-run) return 0 ;;
          sudo) return 1 ;;
        esac ;;
    esac
    builtin command "$@"
  }

  eval "$FN_SVC"
  eval "$FN_OPENRC"
  install_service

  if [[ ! -f "$INIT_D_DIR/llama-cpp" ]]; then
    echo "FAIL: uid-0 OpenRC install did not write init script"
    exit 1
  fi
  if ! grep -q '#!/sbin/openrc-run' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: uid-0 OpenRC init script missing shebang"
    exit 1
  fi
  if ! grep -q 'rc-update add llama-cpp default' "$INIT_D_DIR/calls2"; then
    echo "FAIL: uid-0 OpenRC install did not call rc-update add"
    cat "$INIT_D_DIR/calls2" 2>/dev/null
    exit 1
  fi
  if ! grep -q 'rc-service llama-cpp restart' "$INIT_D_DIR/calls2"; then
    echo "FAIL: uid-0 OpenRC install did not call rc-service restart"
    cat "$INIT_D_DIR/calls2" 2>/dev/null
    exit 1
  fi
  if grep -q 'WRONG: systemd path' "$INIT_D_DIR/calls2"; then
    echo "FAIL: uid-0 install leaked into the systemd path"
    exit 1
  fi
  echo "PASS: uid-0 OpenRC install (no sudo) succeeds"
) || { echo "FAIL: uid-0 OpenRC install test case"; exit 1; }

# (b) a MODELS_DIR containing a double quote must make install_openrc_service die.
INIT_D_DIR="$(mktemp -d)"
b_out="$(mktemp)"
(
  log() { :; }
  die() { echo "DIE: $*"; exit 1; }
  TARGET_USER="testuser"
  TARGET_GROUP="testgroup"
  INSTALL_DIR="/opt/llama-cpp"
  MODELS_DIR='/data/models with "quote"'
  PORT=7835
  HOST="127.0.0.1"

  sudo() { "$@"; }
  rc-update() { echo "rc-update $*" >> "$INIT_D_DIR/calls3"; }
  rc-service() { echo "rc-service $*" >> "$INIT_D_DIR/calls3"; }
  install_linux_service() { return 1; }
  install_macos_service() { return 1; }
  uname() { echo Linux; }

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
  echo "FAIL: MODELS_DIR with double quote did not die"
  exit 1
) >"$b_out" 2>&1 || true
if ! grep -q 'DIE: MODELS_DIR contains a double quote' "$b_out"; then
  echo "FAIL: MODELS_DIR double-quote guard"
  cat "$b_out"
  rm -f "$b_out"
  rm -rf "$INIT_D_DIR"
  exit 1
fi
# Assert that the rejected MODELS_DIR leaves no init script
if [[ -f "$INIT_D_DIR/llama-cpp" ]]; then
  echo "FAIL: rejected MODELS_DIR left init script at $INIT_D_DIR/llama-cpp"
  rm -f "$b_out"
  rm -rf "$INIT_D_DIR"
  exit 1
fi
rm -f "$b_out"
rm -rf "$INIT_D_DIR"

echo "all tests passed"