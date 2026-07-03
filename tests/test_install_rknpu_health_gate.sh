#!/usr/bin/env bash
# Syntax + health-gate behaviour tests for the RKNPU/rkllama install scripts.
set -euo pipefail
NPU_SCRIPT=scripts/install-rknpu.sh
RKLLAMA_SCRIPT=scripts/install-rkllama.sh

echo "test: bash -n syntax (install-rknpu.sh)"
bash -n "$NPU_SCRIPT"

echo "test: bash -n syntax (install-rkllama.sh)"
bash -n "$RKLLAMA_SCRIPT"

echo "test: wait_for_rkllama fails loudly when nothing answers on the port"
# Extract just the wait_for_rkllama() function and shrink its retry loop
# (60 -> 2 iterations) so this runs in ~2s instead of a full minute — the
# failure path under test (curl never succeeds -> die with a clear message)
# is unchanged.
FN="$(sed -n '/^wait_for_rkllama() {/,/^}/p' "$NPU_SCRIPT" | sed 's/i < 60/i < 2/')"
if [ -z "$FN" ]; then
  echo "FAIL: could not extract wait_for_rkllama() from $NPU_SCRIPT"
  exit 1
fi

out="$(
  (
    log()  { :; }
    warn() { :; }
    die()  { printf '%s\n' "$*"; exit 1; }
    eval "$FN"
    # Port 1 (tcpmux) is privileged and never bound by rkllama in any test
    # environment, so the curl probe reliably fails without touching the
    # network.
    RKLLAMA_PORT=1
    wait_for_rkllama
  ) 2>&1
)" && rc=0 || rc=$?

if [ "$rc" -eq 0 ]; then
  echo "FAIL: wait_for_rkllama reported success despite nothing listening"
  exit 1
fi
if ! grep -q "did not come up" <<<"$out"; then
  echo "FAIL: expected a 'did not come up within 60s' message, got:"
  echo "$out"
  exit 1
fi
echo "PASS: wait_for_rkllama exits non-zero with a clear message when the service never answers"

echo "test: install-rkllama.sh delegates to install-rknpu.sh with TAOS_RKNPU_SETUP=1"
if ! grep -q "TAOS_RKNPU_SETUP=1" "$RKLLAMA_SCRIPT"; then
  echo "FAIL: install-rkllama.sh no longer forces TAOS_RKNPU_SETUP=1 on delegation"
  exit 1
fi

echo "test: install-rknpu.sh installs + enables + starts the systemd unit"
if ! grep -q "systemctl enable --now rkllama.service" "$NPU_SCRIPT"; then
  echo "FAIL: install-rknpu.sh no longer enables+starts rkllama.service"
  exit 1
fi

echo "all tests passed"
