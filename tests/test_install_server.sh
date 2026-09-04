#!/usr/bin/env bash
# Regression tests for install-server.sh GPU capability tool installation.
# Covers: NVIDIA nvidia-utils, AMD rocm-smi, RK3588 perf service, and
# Intel GPU Mesa Vulkan driver support (mesa-vulkan-drivers).
set -euo pipefail
SCRIPT=scripts/install-server.sh

echo "test: bash -n syntax"
bash -n "$SCRIPT"

echo "test: ensure_linux_deps includes vulkan-tools in apt path"
grep -q "vulkan-tools" "$SCRIPT"

echo "test: Intel GPU block installs mesa-vulkan-drivers via apt when available"
grep -q "apt-cache show mesa-vulkan-drivers" "$SCRIPT"

echo "test: Intel GPU block installs mesa-vulkan-drivers via dnf when available"
grep -q "dnf.*mesa-vulkan-drivers" "$SCRIPT"

echo "test: Intel GPU block installs vulkan-intel via pacman when available"
grep -q "vulkan-intel" "$SCRIPT"
grep -q "vulkan-mesa-layers" "$SCRIPT"

echo "test: Intel GPU block installs mesa-vulkan-intel via apk when available"
grep -q "mesa-vulkan-intel" "$SCRIPT"

echo "test: Intel GPU detection runs before mesa install (ordering)"
lspci_line=$(grep -n 'lspci.*Intel Corporation' "$SCRIPT" | head -1 | cut -d: -f1)
mesa_line=$(grep -n 'apt-cache show mesa-vulkan-drivers' "$SCRIPT" | head -1 | cut -d: -f1)
(( lspci_line < mesa_line ))

# ── NVIDIA nvidia-utils ────────────────────────────────────────────

echo "test: NVIDIA block installs nvidia-utils via apt when available"
grep -q "apt-cache show nvidia-utils" "$SCRIPT"

echo "test: NVIDIA block installs nvidia-smi via dnf when available"
grep -q "dnf list nvidia-smi" "$SCRIPT"

echo "test: NVIDIA block installs nvidia-utils via pacman when available"
grep -q "pacman -Si nvidia-utils" "$SCRIPT"

echo "test: NVIDIA block warns when dnf package not found (RPM Fusion missing)"
grep -q "enable RPM Fusion nonfree" "$SCRIPT"

echo "test: NVIDIA block only runs after driver + device check"
nv_drv_line=$(grep -n 'nv_driver && nv_devices' "$SCRIPT" | head -1 | cut -d: -f1)
nv_utils_line=$(grep -n 'apt-cache show nvidia-utils' "$SCRIPT" | head -1 | cut -d: -f1)
(( nv_drv_line < nv_utils_line ))

# ── AMD rocm-smi ───────────────────────────────────────────────────

echo "test: AMD block installs rocm-smi-lib via apt when available"
grep -q "apt-cache show rocm-smi-lib" "$SCRIPT"

echo "test: AMD block installs rocm-smi via dnf when available"
grep -q "dnf list rocm-smi" "$SCRIPT"

echo "test: AMD block installs rocm-smi-lib via pacman when available"
grep -q "pacman -Si rocm-smi-lib" "$SCRIPT"

echo "test: AMD block warns when package not found on any package manager"
grep -q "rocm-smi not installed" "$SCRIPT"

echo "test: AMD block only runs after kfd + ROCm check"
amd_rocm_line=$(grep -n 'amd_rocm && amd_drm' "$SCRIPT" | head -1 | cut -d: -f1)
amd_smi_line=$(grep -n 'apt-cache show rocm-smi-lib' "$SCRIPT" | head -1 | cut -d: -f1)
(( amd_rocm_line < amd_smi_line ))

# ── RK3588 perf service ─────────────────────────────────────────────

echo "test: install-server.sh references taos-rk3588-perf.service"
grep -q "taos-rk3588-perf.service" "$SCRIPT"

echo "test: perf service only installed when RKNPU_PENDING_INSTALL=1"
grep -q "RKNPU_PENDING_INSTALL.*!=.*1" "$SCRIPT"

echo "test: perf service respects TAOS_NO_RKNPU_PERF opt-out"
grep -q "TAOS_NO_RKNPU_PERF" "$SCRIPT"

echo "test: perf service calls systemctl daemon-reload + enable"
grep -q "systemctl daemon-reload" "$SCRIPT"
grep -q "systemctl enable taos-rk3588-perf.service" "$SCRIPT"

echo "test: perf service install runs after rkllama install"
rknpu_line=$(grep -n "install_rknpu_if_pending" "$SCRIPT" | head -1 | cut -d: -f1)
perf_call_line=$(grep -n "install_rk3588_perf_if_needed" "$SCRIPT" | head -1 | cut -d: -f1)
(( rknpu_line < perf_call_line ))

# ── Post-install hardware capability verification ──────────────────

echo "test: verify_hardware_capabilities function exists"
grep -q "verify_hardware_capabilities()" "$SCRIPT"

echo "test: verification calls hardware/refresh API endpoint"
grep -q "api/system/hardware/refresh" "$SCRIPT"

echo "test: verification parses vulkan capability from JSON"
grep -q "vulkan.*true.*claimed_vulkan" "$SCRIPT"

echo "test: verification parses cuda capability from JSON"
grep -q "cuda.*true.*claimed_cuda" "$SCRIPT"

echo "test: verification parses rocm capability from JSON"
grep -q "rocm.*true.*claimed_rocm" "$SCRIPT"

echo "test: verification parses rknpu capability from JSON"
grep -q "rknpu.*claimed_rknpu" "$SCRIPT"

echo "test: verification detects Apple Silicon for MLX"
grep -q "Darwin.*claimed_mlx" "$SCRIPT"

echo "test: verification is non-blocking (return 0 on skip, not die)"
grep -q "verification skipped" "$SCRIPT" && grep -q "return 0" "$SCRIPT"

echo "test: verification counts verified_ok and verified_warn"
grep -q "verified_ok=" "$SCRIPT" && grep -q "verified_warn=" "$SCRIPT"

echo "test: verification only runs when SERVICE_MODE != skip"
grep -A 3 'SERVICE_MODE.*!=.*skip' "$SCRIPT" | grep -q "verify_hardware_capabilities"

# ── Controller readiness wait (taOS#2) ─────────────────────────────────

echo "test: controller wait uses a 240 s ready timeout"
grep -q "_READY_WAIT=240" "$SCRIPT"

echo "test: controller wait uses a 60 s port-open timeout"
grep -q "_PORT_WAIT=60" "$SCRIPT"

echo "test: controller wait checks /api/health for port-open phase"
grep -q "curl.*localhost:\$TAOS_PORT/api/health" "$SCRIPT"

echo "test: controller wait checks /api/cluster/workers for ready phase"
grep -q "curl.*localhost:\$TAOS_PORT/api/cluster/workers" "$SCRIPT"

echo "test: controller wait names first-boot init in the timeout message"
grep -q "first-boot init may still be running" "$SCRIPT"

echo "test: port-open loop caps curl probe by remaining phase time"
grep -q '_remaining=$(( _port_deadline - SECONDS ))' "$SCRIPT"

echo "test: port-open phase deadline is anchored once, before the loop"
# One absolute deadline per phase; every guard inside the loop reads the
# clock against it rather than re-deriving the phase budget.
[[ $(grep -c '_port_deadline=$(( SECONDS + _PORT_WAIT ))' "$SCRIPT") -eq 1 ]]

echo "test: port-open loop passes a positive timeout to curl --max-time"
# Scoped to the /api/health curl so a regression on the ready loop cannot
# satisfy this assertion (and vice versa).
awk '/_port_deadline - SECONDS/{port=1} port && /--max-time/{print; exit}' "$SCRIPT" \
    | grep -q -- '--max-time "[^"]*"'

echo "test: port-open loop breaks before sleep when remaining <= 1"
awk '/while.*_port_tries.*do/{port=1; next} port && /curl.*api.health/{health=1; next} port && health && /_remaining -le 1/{print; exit}' "$SCRIPT" \
    | grep -q '_remaining -le 1'

echo "test: port-open loop re-reads the clock after the probe, before sleeping"
# A slow curl can consume the whole remaining budget, so reusing the
# pre-probe `_remaining` for the post-probe guard lets the follow-up
# sleep run past _PORT_WAIT. Assert the recompute sits between the curl
# and the sleep.
awk '/while.*_port_tries.*do/{p=1; next}
     p && /curl.*api.health/{c=1; next}
     p && c && index($0, "_remaining=$(( _port_deadline - SECONDS ))"){found=1}
     p && c && /sleep 1/{exit}
     END{exit !found}' "$SCRIPT"

echo "test: port-open loop floors curl --max-time at 1 s"
# Ensures a future edit cannot weaken the deadline guard and silently
# disable curl's per-attempt timeout (finding #3 / #4 invariant).
grep -q '_curl_timeout=$(( _remaining > 1 ? _remaining : 1 ))' "$SCRIPT"

echo "test: ready loop caps curl probe by remaining phase time"
grep -q '_remaining=$(( _ready_deadline - SECONDS ))' "$SCRIPT"

echo "test: ready phase deadline is anchored once, before the loop"
[[ $(grep -c '_ready_deadline=$(( SECONDS + _READY_WAIT ))' "$SCRIPT") -eq 1 ]]

echo "test: ready loop passes a positive timeout to curl --max-time"
awk '/_ready_deadline - SECONDS/{ready=1} ready && /--max-time/{print; exit}' "$SCRIPT" \
    | grep -q -- '--max-time "[^"]*"'

echo "test: ready loop breaks before sleep when remaining <= 1"
awk '/while.*_ready_tries.*do/{ready=1; next} ready && /curl.*cluster.workers/{workers=1; next} ready && workers && /_remaining -le 1/{print; exit}' "$SCRIPT" \
    | grep -q '_remaining -le 1'

echo "test: ready loop re-reads the clock after the probe, before sleeping"
# Same stale-value regression as the port-open loop.
awk '/while.*_ready_tries.*do/{r=1; next}
     r && /curl.*cluster.workers/{c=1; next}
     r && c && index($0, "_remaining=$(( _ready_deadline - SECONDS ))"){found=1}
     r && c && /sleep 1/{exit}
     END{exit !found}' "$SCRIPT"

echo "all tests passed"
