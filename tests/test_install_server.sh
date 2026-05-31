#!/usr/bin/env bash
# Regression tests for install-server.sh GPU capability tool installation.
# Covers: NVIDIA nvidia-utils, AMD rocm-smi, and RK3588 perf service.
# NOTE: Intel Mesa Vulkan tests are in PR #508 (mesa-vulkan-drivers).
set -euo pipefail
SCRIPT=scripts/install-server.sh

echo "test: bash -n syntax"
bash -n "$SCRIPT"

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

echo "all tests passed"
