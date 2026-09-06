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

echo "test: rkllama models land in the unified taOS tree (#1548)"
# The service must point rkllama at \$RKLLAMA_MODELS, and that path must be
# resolved from the unified root (TAOS_MODELS_ROOT / <project>/data/models),
# not the old per-install ~/rkllama/models, so the Models UI scan sees pulls.
if ! grep -q -- "--models \$RKLLAMA_MODELS" "$NPU_SCRIPT"; then
  echo "FAIL: rkllama.service ExecStart no longer uses --models \$RKLLAMA_MODELS"
  exit 1
fi
if ! grep -q 'TAOS_MODELS_ROOT' "$NPU_SCRIPT"; then
  echo "FAIL: install-rknpu.sh no longer honours TAOS_MODELS_ROOT for the unified model tree"
  exit 1
fi
if ! grep -q 'data/models/rkllama' "$NPU_SCRIPT"; then
  echo "FAIL: install-rknpu.sh no longer derives the unified <project>/data/models/rkllama path"
  exit 1
fi

echo "test: migrate_legacy_models moves legacy models, is idempotent, and never re-downloads"
MIG_FN="$(sed -n '/^migrate_legacy_models() {/,/^}/p' "$NPU_SCRIPT")"
if [ -z "$MIG_FN" ]; then
  echo "FAIL: could not extract migrate_legacy_models() from $NPU_SCRIPT"
  exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mig_out="$(
  (
    log() { :; }
    run_as_user() { "$@"; }  # tests run as the invoking user
    LEGACY_RKLLAMA_MODELS="$TMP/legacy"
    RKLLAMA_MODELS="$TMP/unified"
    mkdir -p "$LEGACY_RKLLAMA_MODELS/gemma3-270m" "$RKLLAMA_MODELS"
    echo weights > "$LEGACY_RKLLAMA_MODELS/gemma3-270m/model.rkllm"
    # Pre-existing model at destination must NOT be clobbered by a legacy copy.
    mkdir -p "$RKLLAMA_MODELS/qwen3-1.7b" "$LEGACY_RKLLAMA_MODELS/qwen3-1.7b"
    echo keep > "$RKLLAMA_MODELS/qwen3-1.7b/model.rkllm"
    echo stale > "$LEGACY_RKLLAMA_MODELS/qwen3-1.7b/model.rkllm"
    eval "$MIG_FN"
    migrate_legacy_models
    migrate_legacy_models  # second run must be a clean no-op
    [ -f "$RKLLAMA_MODELS/gemma3-270m/model.rkllm" ] || { echo "MISSING_MIGRATED"; exit 1; }
    [ ! -d "$LEGACY_RKLLAMA_MODELS/gemma3-270m" ] || { echo "LEGACY_NOT_MOVED"; exit 1; }
    [ "$(cat "$RKLLAMA_MODELS/qwen3-1.7b/model.rkllm")" = "keep" ] || { echo "CLOBBERED_EXISTING"; exit 1; }
    echo OK
  ) 2>&1
)" && mrc=0 || mrc=$?
if [ "$mrc" -ne 0 ] || ! grep -q '^OK$' <<<"$mig_out"; then
  echo "FAIL: migrate_legacy_models misbehaved: $mig_out"
  exit 1
fi
echo "PASS: migrate_legacy_models moves new models, keeps existing, idempotent"

echo "test: service user is granted render+video groups for NPU/GPU access"
# The unit runs rkllama as \$TARGET_USER (not root), so on RK3588 that user
# must be in render+video to reach the DRI/mpp device nodes.
if ! grep -q 'User=\$TARGET_USER' "$NPU_SCRIPT"; then
  echo "FAIL: rkllama.service no longer runs as \$TARGET_USER (would run as root)"
  exit 1
fi
if ! grep -qE 'for _grp in render video' "$NPU_SCRIPT"; then
  echo "FAIL: install-rknpu.sh no longer grants the service user render+video groups"
  exit 1
fi

echo "all tests passed"
