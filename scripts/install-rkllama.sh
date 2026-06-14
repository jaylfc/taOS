#!/usr/bin/env bash
# Store entrypoint for the rkllama (RK3588 NPU LLM) service.
#
# This is the script the App Store's `rkllama` service manifest points at
# (install.method: script). The store's ScriptInstaller invokes it
# non-interactively as `bash install-rkllama.sh <project_dir>`, so this
# wrapper must be headless, idempotent, and must never report success
# without actually installing.
#
# It is a thin wrapper over the verified NPU installer (install-rknpu.sh):
#   1. If rkllama already answers locally, exit 0 (idempotent no-op).
#   2. Otherwise delegate to install-rknpu.sh in headless mode. We set
#      TAOS_RKNPU_SETUP=1 explicitly so install-rknpu.sh does NOT take its
#      "non-interactive shell, nothing to confirm -> exit 0" path, which
#      would otherwise return success while installing nothing.
#
# install-rknpu.sh handles board detection (it dies on non-RK3588 hosts)
# and uses sudo only for the privileged librknnrt + systemd steps; in a
# store context without a TTY those sudo calls fail loudly (non-zero),
# which ScriptInstaller correctly surfaces as an install failure.
set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
PORT="${TAOS_RKLLAMA_PORT:-7833}"
LEGACY_PORT=8080

# 1. Idempotent short-circuit: a live rkllama already satisfies the install.
for p in "$PORT" "$LEGACY_PORT"; do
    if curl -fsS --max-time 2 "http://localhost:${p}/api/tags" >/dev/null 2>&1; then
        echo "rkllama already running on port ${p} — nothing to install"
        exit 0
    fi
done

NPU_SCRIPT="${PROJECT_DIR}/scripts/install-rknpu.sh"
if [[ ! -f "$NPU_SCRIPT" ]]; then
    echo "install-rkllama.sh: expected NPU installer at ${NPU_SCRIPT}" >&2
    exit 1
fi

# 2. Delegate to the verified installer in headless mode. TAOS_RKNPU_SETUP=1
#    skips the interactive confirmation AND the false-success exit-0 path.
echo "rkllama not detected — running NPU installer (${NPU_SCRIPT})"
exec env TAOS_RKNPU_SETUP=1 TAOS_RKLLAMA_PORT="${PORT}" \
    bash "$NPU_SCRIPT" --yes
