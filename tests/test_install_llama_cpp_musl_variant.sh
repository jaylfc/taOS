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

echo "all tests passed"