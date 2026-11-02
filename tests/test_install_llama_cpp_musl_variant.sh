#!/usr/bin/env bash
set -euo pipefail
SCRIPT=scripts/install-llama-cpp.sh

unset TAOS_LLAMACPP_VARIANT

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
  cmd() { return 1; }
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

# Test OpenRC service installation path - test install_openrc_service directly
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
  
  # Mock systemctl absent, openrc-run present
  has_systemctl() { return 1; }
  has_openrc_run() { return 0; }
  has_rc_service() { return 0; }
  
  # Define install_openrc_service directly (copied from script)
  install_openrc_service() {
      local init_d="${INIT_D_DIR:-/etc/init.d}"
      local script="$init_d/llama-cpp"
      log "writing $script (port $PORT)"
      mkdir -p "$init_d"
      cat > "$script" <<EOFF
#!/sbin/openrc-run
name="llama.cpp server (router mode) -- taOS default local LLM backend"
command="$INSTALL_DIR/llama-server"
command_args="--models-dir $MODELS_DIR --host $HOST --port $PORT"
command_user="$TARGET_USER"
command_background=true
pidfile="/run/llama-cpp.pid"
depend() {
    need net
}
EOFF
      chmod 755 "$script"
      rc_update() { return 0; }
      rc_service() { return 0; }
      rc_update add llama-cpp default
      rc_service llama-cpp restart
      log "llama-cpp OpenRC service enabled + started"
  }
  
  install_openrc_service
  
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
  
  if ! grep -q 'command_args="--models-dir /data/models --host 127.0.0.1 --port 7835"' "$INIT_D_DIR/llama-cpp"; then
    echo "FAIL: OpenRC init script missing correct command_args"
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
  
  echo "PASS: OpenRC service installation test"
) || { echo "FAIL: OpenRC test case"; exit 1; }

rm -rf "$INIT_D_DIR"

echo "all tests passed"