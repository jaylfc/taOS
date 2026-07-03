#!/usr/bin/env bash
# Syntax + health-gate + variant-detection behaviour tests for the
# generalized llama.cpp install script (per-platform one-tap backend).
set -euo pipefail
SCRIPT=scripts/install-llama-cpp.sh

echo "test: bash -n syntax (install-llama-cpp.sh)"
bash -n "$SCRIPT"

echo "test: wait_for_health fails loudly when nothing answers on the port"
# Extract just the wait_for_health() function and shrink its retry loop
# (60 -> 2 iterations) so this runs in ~2s instead of a full minute — the
# failure path under test (curl never succeeds -> return non-zero) is
# unchanged.
FN="$(sed -n '/^wait_for_health() {/,/^}/p' "$SCRIPT" | sed 's/i < 60/i < 2/')"
if [ -z "$FN" ]; then
  echo "FAIL: could not extract wait_for_health() from $SCRIPT"
  exit 1
fi

out="$(
  (
    log()  { :; }
    eval "$FN"
    # Port 1 (tcpmux) is privileged and never bound by llama-server in any
    # test environment, so the curl probe reliably fails without touching
    # the network.
    HOST=127.0.0.1
    PORT=1
    if wait_for_health; then
      echo "UNEXPECTED_SUCCESS"
    else
      echo "EXPECTED_FAILURE"
    fi
  ) 2>&1
)"

if ! grep -q "EXPECTED_FAILURE" <<<"$out"; then
  echo "FAIL: wait_for_health reported success despite nothing listening"
  echo "$out"
  exit 1
fi
echo "PASS: wait_for_health returns non-zero when the service never answers"

echo "test: variant detection maps forced TAOS_LLAMACPP_VARIANT through to the resolved asset"
for pair in "cuda:ubuntu-vulkan-x64" "rocm:ubuntu-rocm-7.2-x64" "apple-silicon:macos-arm64" "cpu:ubuntu-x64"; do
  variant="${pair%%:*}"
  expect_substr="${pair##*:}"
  FN2="$(sed -n '/^resolve_asset() {/,/^}/p' "$SCRIPT")"
  out2="$(
    (
      warn() { :; }
      die()  { printf 'DIE:%s\n' "$*"; exit 1; }
      LLAMACPP_VERSION="bTEST"
      VARIANT="$variant"
      eval "$FN2"
      resolve_asset
      echo "$ASSET"
    ) 2>&1
  )"
  if ! grep -q "$expect_substr" <<<"$out2"; then
    echo "FAIL: variant=$variant expected asset to contain '$expect_substr', got: $out2"
    exit 1
  fi
done
echo "PASS: resolve_asset() maps every supported variant to the expected release asset"

echo "test: install-llama-cpp.sh installs + enables + starts the systemd unit (Linux)"
if ! grep -q "systemctl enable --now llama-cpp.service" "$SCRIPT"; then
  echo "FAIL: install-llama-cpp.sh no longer enables+starts llama-cpp.service"
  exit 1
fi

echo "test: install-llama-cpp.sh installs a launchd agent (macOS)"
if ! grep -q "launchctl bootstrap" "$SCRIPT"; then
  echo "FAIL: install-llama-cpp.sh no longer bootstraps a launchd agent for macOS"
  exit 1
fi

echo "test: no models are downloaded by this script (backend-only install)"
if grep -iE '^\s*curl' "$SCRIPT" | grep -qiE '\.gguf|MODELS_DIR'; then
  echo "FAIL: install-llama-cpp.sh appears to curl-download a model file — it must only install the server binary"
  exit 1
fi
echo "PASS: script installs the server binary only, no models"

echo "all tests passed"
