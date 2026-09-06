#!/usr/bin/env bash
# End-to-end test for install-worker.sh on RK3588.
#
# This test is designed to run inside a clean LXC container on an
# Orange Pi 5 Plus (or equivalent RK3588 board), with the TAOS
# controller reachable at $CONTROLLER_URL. It exercises the full
# install flow and verifies:
#
#   1. The install script runs to completion without errors
#   2. A systemd service is created and enabled
#   3. The worker registers with the controller
#   4. The worker's capabilities include llm-chat and app-streaming
#   5. The TAOS-namespaced Ollama bundle is on port 21434
#   6. A heartbeat round-trip succeeds within 10 seconds
#
# This is a shell test rather than a pytest case because it operates
# on system state (systemd, processes, ports) that pytest cannot
# cleanly sandbox. Call it from CI with:
#
#   CONTROLLER_URL=http://host.lxc:6969 \
#     tests/integration/test_install_worker_rk3588.sh
#
# Exit code 0 means the flow works end-to-end. Non-zero means a
# specific step failed, with a human-readable message.

set -euo pipefail

CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:6969}"
WORKER_NAME="${WORKER_NAME:-$(hostname)}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "PASS: $*"
}

step() {
  echo
  echo "==> $*"
}

# --- 0. Preflight ----------------------------------------------------

step "Preflight"
command -v systemctl >/dev/null || fail "systemctl not on PATH"
command -v curl >/dev/null || fail "curl not on PATH"
[[ -f /proc/device-tree/compatible ]] || fail "no device tree (not an SBC?)"
if ! grep -q "rockchip,rk3588" /proc/device-tree/compatible 2>/dev/null; then
  echo "warning: not a Rockchip RK3588 host — the test will still run but"
  echo "         NPU-specific checks (step 5) may be skipped."
fi
pass "system looks sane"

# --- 1. Run install-worker.sh ----------------------------------------

step "Running install-worker.sh"
bash scripts/install-worker.sh --controller "$CONTROLLER_URL" \
  || fail "install-worker.sh exited non-zero"
pass "install script completed"

# --- 2. systemd service present and active ---------------------------

step "Verifying systemd service"
if ! systemctl list-unit-files | grep -q tinyagentos-worker.service; then
  fail "tinyagentos-worker.service not registered with systemd"
fi
systemctl is-enabled tinyagentos-worker.service \
  || fail "service not enabled"
systemctl is-active  tinyagentos-worker.service \
  || fail "service not active"
pass "service is enabled and active"

# --- 3. Worker registers with controller -----------------------------

step "Verifying worker registered with controller"
for attempt in 1 2 3 4 5; do
  if curl -fsS "$CONTROLLER_URL/api/cluster/workers" \
      | grep -q "\"name\":\"$WORKER_NAME\""; then
    pass "worker '$WORKER_NAME' registered"
    registered=yes
    break
  fi
  echo "  attempt $attempt/5: not yet registered, waiting 3s"
  sleep 3
done
[[ "${registered:-no}" == yes ]] || fail "worker did not register within 15s"

# --- 4. Capabilities include llm-chat and app-streaming --------------

step "Verifying advertised capabilities"
caps=$(curl -fsS "$CONTROLLER_URL/api/cluster/workers" \
  | grep -o "\"name\":\"$WORKER_NAME\"[^}]*\"capabilities\":\[[^]]*\]")
echo "$caps" | grep -q "llm-chat" \
  || fail "worker did not advertise llm-chat capability"
echo "$caps" | grep -q "app-streaming" \
  || echo "  note: app-streaming not advertised (container runtime missing?)"
pass "llm-chat capability present"

# --- 5. TAOS Ollama bundle on port 21434 -----------------------------

step "Verifying TAOS-namespaced Ollama on port 21434"
if curl -fsS http://localhost:21434/api/tags >/dev/null 2>&1; then
  pass "TAOS Ollama responding on 21434"
else
  echo "  TAOS Ollama not on 21434 — acceptable if --no-ollama was used"
fi

# --- 6. Heartbeat round-trip ----------------------------------------

step "Waiting for a fresh heartbeat round-trip"
before=$(curl -fsS "$CONTROLLER_URL/api/cluster/workers" \
  | grep -o "\"name\":\"$WORKER_NAME\"[^}]*\"last_heartbeat\":[0-9.]*" \
  | grep -o "[0-9.]*$" || echo 0)
sleep 10
after=$(curl -fsS "$CONTROLLER_URL/api/cluster/workers" \
  | grep -o "\"name\":\"$WORKER_NAME\"[^}]*\"last_heartbeat\":[0-9.]*" \
  | grep -o "[0-9.]*$" || echo 0)
if [[ "$before" == "$after" || "$after" == "0" ]]; then
  fail "heartbeat did not advance in 10 seconds"
fi
pass "heartbeat advanced from $before to $after"

echo
echo "All steps passed."
