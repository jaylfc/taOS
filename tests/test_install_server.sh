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

# ── Docker install fallback for Debian trixie / Armbian trixie (taOS#2) ──

echo "test: trixie fallback defines _apt_install_docker_official_repo"
grep -q "_apt_install_docker_official_repo()" "$SCRIPT"

echo "test: _apt_install_compose probes both docker-compose-plugin and docker-compose-v2"
grep -q "apt-cache madison docker-compose-plugin" "$SCRIPT"
grep -q "apt-cache madison docker-compose-v2" "$SCRIPT"

echo "test: _apt_install_compose returns a distinct code (2) for missing-package case"
grep -A20 'apt-cache madison docker-compose-plugin' "$SCRIPT" \
    | grep -q "return 2"

echo "test: trixie fallback uses Docker's official apt repo (download.docker.com)"
grep -q "download.docker.com/linux" "$SCRIPT"

echo "test: trixie fallback verifies Docker apt key fingerprint before importing"
grep -q "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" "$SCRIPT"
grep -q "Docker apt key fingerprint mismatch" "$SCRIPT"

echo "test: trixie fallback installs docker-ce + plugin from Docker's repo"
grep -q "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin" "$SCRIPT"

echo "test: trixie fallback is gated on the missing-package rc==2, not on a generic failure"
grep -A 6 "_apt_install_compose" "$SCRIPT" \
    | grep -q "_apt_compose_rc == 2"

echo "test: trixie fallback does NOT trigger on a generic install failure"
# When _apt_install_compose returns 1 (install failure), the script must
# surface the apt error and skip _apt_install_docker_official_repo. The
# elif arm is named _apt_compose_rc != 0 and sits between the rc==2
# branch and the next package manager (dnf).
sed -n '/_apt_compose_rc == 2/,/elif command -v dnf/p' "$SCRIPT" \
    | grep -q "_apt_compose_rc != 0"

echo "test: Docker keyring is written with mode 0644 (not destination umask)"
grep -q "install -m 0644.*docker.asc" "$SCRIPT"

echo "test: Docker curl download has bounded timeouts"
grep -q "curl -fsSL --connect-timeout 15 --max-time 60" "$SCRIPT"

echo "test: trixie fallback removes distro docker.io/containerd/runc before installing docker-ce"
grep -q "apt-get remove -y -qq docker.io containerd runc" "$SCRIPT"

echo "test: trixie fallback cleans up docker.list + docker.asc on apt-get update failure"
grep -q 'apt-get update failed after adding Docker' "$SCRIPT" \
    && grep -A3 'apt-get update failed after adding Docker' "$SCRIPT" \
        | grep -q "rm -f /etc/apt/sources.list.d/docker.list"

echo "test: trixie fallback cleans up docker.list + docker.asc on apt-get install failure"
grep -q 'apt install from Docker' "$SCRIPT" \
    && grep -A3 'apt install from Docker' "$SCRIPT" \
        | grep -q "rm -f /etc/apt/sources.list.d/docker.list"

echo "test: fingerprint mismatch warning points operators at the docker.com gpg URL"
grep -A4 'Docker apt key fingerprint mismatch' "$SCRIPT" \
    | grep -q "download.docker.com/linux/.*gpg"

echo "test: ensure_docker_for_apps call site tolerates fallback failure (no set -e abort)"
ensure_docker_call_line=$(grep -n "ensure_docker_for_apps || warn" "$SCRIPT" | head -1 | cut -d: -f1)
(( ensure_docker_call_line > 0 ))

echo "all tests passed"
