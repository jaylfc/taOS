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
grep -q '_docker_keyring=/etc/apt/keyrings/docker.asc' "$SCRIPT"
grep -q 'install -m 0644 "\$_docker_key_tmp" "\$_docker_keyring"' "$SCRIPT"

echo "test: Docker curl download has bounded timeouts"
grep -q "curl -fsSL --connect-timeout 15 --max-time 60" "$SCRIPT"

echo "test: trixie fallback removes distro docker.io/containerd/runc before installing docker-ce"
grep -q 'apt-get remove -y -qq \$_docker_removed_pkgs' "$SCRIPT"

echo "test: removed distro packages are reinstalled on every post-removal failure"
# The caller installs docker.io immediately before entering the fallback, so a
# failure AFTER the removal used to leave the host with no Docker at all.
# Behaviour is covered end to end by tests/test_install_server_docker_repo.py.
grep -q "^_docker_restore_distro_pkgs()" "$SCRIPT"
grep -A4 'apt-get update failed after adding Docker' "$SCRIPT" \
    | grep -q '_docker_restore_distro_pkgs "\$_docker_removed_pkgs"'
grep -A4 'apt install from Docker' "$SCRIPT" \
    | grep -q '_docker_restore_distro_pkgs "\$_docker_removed_pkgs"'

echo "test: the package rollback reinstalls exactly what was removed, not a fixed list"
grep -A12 '^_docker_restore_distro_pkgs()' "$SCRIPT" | grep -q 'local pkgs="\$1"'
grep -A12 '^_docker_restore_distro_pkgs()' "$SCRIPT" | grep -q '\[\[ -n "\$pkgs" \]\] || return 0'

echo "test: a failed reinstall warns loudly instead of narrating success"
grep -A22 '^_docker_restore_distro_pkgs()' "$SCRIPT" \
    | grep -q "this host has NO Docker now"

echo "test: trixie fallback rolls back docker.list + docker.asc on apt-get update failure"
# Rollback goes through _docker_apt_restore, which restores a pre-existing
# file from its backup and deletes ONLY a file this invocation created. An
# unconditional `rm -f` here is the bug this replaced: it destroyed a host's
# own customised Docker repo config. Behaviour is covered end to end by
# tests/test_install_server_docker_repo.py.
grep -q 'apt-get update failed after adding Docker' "$SCRIPT" \
    && grep -A3 'apt-get update failed after adding Docker' "$SCRIPT" \
        | grep -q '_docker_apt_restore "\$_docker_list"'

echo "test: trixie fallback rolls back docker.list + docker.asc on apt-get install failure"
grep -q 'apt install from Docker' "$SCRIPT" \
    && grep -A3 'apt install from Docker' "$SCRIPT" \
        | grep -q '_docker_apt_restore "\$_docker_keyring"'

echo "test: rollback never rm's an apt file it did not create"
# Every rm inside _docker_apt_restore must sit behind the created flag.
grep -A20 '^_docker_apt_restore()' "$SCRIPT" | grep -q 'elif (( created )); then'
grep -A20 '^_docker_apt_restore()' "$SCRIPT" | grep -q 'sudo cp -a "\$backup" "\$path"'

echo "test: pre-existing Docker apt files are backed up before being overwritten"
grep -q 'could not back up the existing \$_docker_keyring' "$SCRIPT"
grep -q 'could not back up the existing \$_docker_list' "$SCRIPT"
backup_line=$(grep -n 'cp -a "\$_docker_keyring" "\$_docker_keyring_backup"' "$SCRIPT" | head -1 | cut -d: -f1)
overwrite_line=$(grep -n 'install -m 0644 "\$_docker_key_tmp" "\$_docker_keyring"' "$SCRIPT" | head -1 | cut -d: -f1)
(( backup_line < overwrite_line ))

echo "test: backups land outside /etc/apt so apt never sees a stray file"
grep -q 'mktemp -d /tmp/taos-docker-apt' "$SCRIPT"
grep -A2 'mktemp -d /tmp/taos-docker-apt' "$SCRIPT" | grep -q "rm -rf .*_docker_bak_dir"

echo "test: fingerprint mismatch warning points operators at the docker.com gpg URL"
grep -A4 'Docker apt key fingerprint mismatch' "$SCRIPT" \
    | grep -q "download.docker.com/linux/.*gpg"

echo "test: ensure_docker_for_apps call site tolerates fallback failure (no set -e abort)"
ensure_docker_call_line=$(grep -n "ensure_docker_for_apps || warn" "$SCRIPT" | head -1 | cut -d: -f1)
(( ensure_docker_call_line > 0 ))
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
# Ordering matters: whichever of the guard or the sleep comes first ends the
# scan, so a future edit that moves `_remaining -le 1` below `sleep 1` fails
# here instead of matching it further down the loop.
awk '/while.*_port_tries.*do/{p=1; next}
     p && /curl.*api.health/{c=1; next}
     p && c && /_remaining -le 1/{found=1; exit}
     p && c && /sleep 1/{exit}
     END{exit !found}' "$SCRIPT"

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
# disable curl's per-attempt timeout (finding #3 / #4 invariant). Scoped to
# the port-open loop and stopped at its curl so the ready loop's copy of the
# floor cannot satisfy this assertion, and so the floor must be set before
# the probe that uses it.
awk '/while.*_port_tries.*do/{p=1; next}
     p && index($0, "_curl_timeout=$(( _remaining > 1 ? _remaining : 1 ))"){found=1}
     p && /curl.*api.health/{exit}
     END{exit !found}' "$SCRIPT"

echo "test: ready loop caps curl probe by remaining phase time"
grep -q '_remaining=$(( _ready_deadline - SECONDS ))' "$SCRIPT"

echo "test: ready phase deadline is anchored once, before the loop"
[[ $(grep -c '_ready_deadline=$(( SECONDS + _READY_WAIT ))' "$SCRIPT") -eq 1 ]]

echo "test: ready loop passes a positive timeout to curl --max-time"
awk '/_ready_deadline - SECONDS/{ready=1} ready && /--max-time/{print; exit}' "$SCRIPT" \
    | grep -q -- '--max-time "[^"]*"'

echo "test: ready loop breaks before sleep when remaining <= 1"
# Same ordering enforcement as the port-open loop.
awk '/while.*_ready_tries.*do/{r=1; next}
     r && /curl.*cluster.workers/{c=1; next}
     r && c && /_remaining -le 1/{found=1; exit}
     r && c && /sleep 1/{exit}
     END{exit !found}' "$SCRIPT"

echo "test: ready loop re-reads the clock after the probe, before sleeping"
# Same stale-value regression as the port-open loop.
awk '/while.*_ready_tries.*do/{r=1; next}
     r && /curl.*cluster.workers/{c=1; next}
     r && c && index($0, "_remaining=$(( _ready_deadline - SECONDS ))"){found=1}
     r && c && /sleep 1/{exit}
     END{exit !found}' "$SCRIPT"

echo "test: ready loop floors curl --max-time at 1 s"
# Same invariant, asserted independently inside the ready loop so a
# regression that drops the floor from one loop cannot hide behind the other.
awk '/while.*_ready_tries.*do/{r=1; next}
     r && index($0, "_curl_timeout=$(( _remaining > 1 ? _remaining : 1 ))"){found=1}
     r && /curl.*cluster.workers/{exit}
     END{exit !found}' "$SCRIPT"

echo "test: Zabbly keyring installed with world-readable permissions"
# Reject old cp path; verify install -m 0644 is used
! grep -q 'cp.*zabbly.asc' "$SCRIPT" && grep -q 'install -m 0644.*zabbly.asc' "$SCRIPT"
# Exercise the install step with a temporary destination and check mode
tmp_src=$(mktemp)
tmp_dst=$(mktemp /tmp/zabbly_test.XXXXXX)
set +e
install -m 0644 "$tmp_src" "$tmp_dst"
actual_mode=$(stat -c %a "$tmp_dst")
set -e
rm -f "$tmp_src" "$tmp_dst"
[ "$actual_mode" = "644" ]

echo "all tests passed"
