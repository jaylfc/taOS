#!/usr/bin/env bash
# TinyAgentOS worker installer — Linux + macOS
# Bootstraps a worker daemon that connects to a controller, reports live
# backend capabilities, and runs inference work dispatched by the cluster
# scheduler.
#
# Usage:
#     curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-worker.sh | bash -s -- http://controller:6969
#
# or download + inspect + run:
#     curl -O https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-worker.sh
#     chmod +x install-worker.sh
#     ./install-worker.sh http://controller:6969
#
# Environment overrides:
#     TAOS_CONTROLLER_URL     controller URL (default: first positional arg)
#     TAOS_WORKER_NAME        worker display name (default: hostname)
#     TAOS_INSTALL_DIR        where to install (default: ~/.local/share/tinyagentos-worker)
#     TAOS_BRANCH             git branch or tag (default: master)
#     TAOS_REPO               git remote (default: https://github.com/jaylfc/tinyagentos)
#     TAOS_SKIP_BENCHMARK     if set, skip the on-join benchmark run
#     TAOS_SERVICE            install as system service: auto (default), user, skip
set -euo pipefail

# ---------------------------------------------------------------------------
# Phase detection (worker-as-LXC).
#
# install-worker.sh runs in two phases:
#   1. Bare host: creates the privileged worker LXC, port-forwards :8443,
#      and incus exec's itself into the LXC for phase 2.
#   2. Inside worker LXC: installs nested incus + bees + registers with
#      controller.
#
# TAOS_INSIDE_WORKER=1 in the environment means "we're in phase 2".
# ---------------------------------------------------------------------------
PHASE="${TAOS_INSIDE_WORKER:+inside}"
PHASE="${PHASE:-host}"

CONTROLLER_URL="${TAOS_CONTROLLER_URL:-${1:-}}"
if [[ -z "$CONTROLLER_URL" ]]; then
    echo "usage: install-worker.sh <controller_url>" >&2
    echo "example: install-worker.sh http://10.0.0.5:6969" >&2
    exit 2
fi

WORKER_NAME="${TAOS_WORKER_NAME:-$(hostname -s)}"
INSTALL_DIR="${TAOS_INSTALL_DIR:-$HOME/.local/share/tinyagentos-worker}"
BRANCH="${TAOS_BRANCH:-master}"
REPO="${TAOS_REPO:-https://github.com/jaylfc/tinyagentos}"
SERVICE_MODE="${TAOS_SERVICE:-auto}"

os_name="$(uname -s)"
arch="$(uname -m)"

log() { printf '\033[1;34m[worker-install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[worker-install]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[worker-install]\033[0m %s\n' "$*" >&2; exit 1; }

log "os=$os_name arch=$arch controller=$CONTROLLER_URL name=$WORKER_NAME"
log "install_dir=$INSTALL_DIR branch=$BRANCH"

# --- helpers (defined early so later stages can use them) ----------------

have_root_or_sudo() {
    if [[ "$(id -u)" = "0" ]]; then
        return 0
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        return 0
    fi
    return 1
}

# --- flat-mode refusal ----------------------------------------------------

refuse_flat_mode_install() {
    if ! command -v incus >/dev/null 2>&1; then
        return 0
    fi
    local existing
    existing="$(incus list --format=csv -c n 2>/dev/null | grep '^taos-agent-' || true)"
    if [[ -n "$existing" ]]; then
        die "$(cat <<EOF
Existing flat-mode install detected. Worker-LXC mode is the new default.
Found existing flat-mode agent containers:

$(echo "$existing" | sed 's/^/  /')

To convert: stop all agents, run 'taos worker convert-to-lxc' on the
controller, then re-run install-worker.sh on a clean host.

This is destructive — agent containers will be recreated, but agent
identity and memory on shared cluster storage survive.
EOF
)"
    fi
}

# --- phase 1: kernel / host checks ----------------------------------------

check_kernel_features() {
    local missing=()
    [[ -f /sys/fs/cgroup/cgroup.controllers ]] || missing+=("cgroup v2")
    if ! grep -qE '^[a-z]+ +btrfs' /proc/filesystems && ! modprobe -n btrfs >/dev/null 2>&1; then
        missing+=("btrfs (in-kernel module or btrfs-progs)")
    fi
    command -v nft >/dev/null 2>&1 || missing+=("nftables")
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing kernel/system features: ${missing[*]}"
    fi
    log "kernel features OK (cgroup v2, btrfs, nftables)"
}

worker_disk_cap() {
    local override="${TAOS_WORKER_DISK_CAP:-}"
    if [[ -n "$override" ]]; then
        local value="${override%[GTM]B}"
        local unit="${override#"$value"}"
        case "$unit" in
            "GB") echo $((value * 1024**3)) ;;
            "TB") echo $((value * 1024**4)) ;;
            "MB") echo $((value * 1024**2)) ;;
            "")   echo "$value" ;;
            *)    die "unsupported worker-disk-cap unit: $unit" ;;
        esac
        return
    fi
    local free_kb
    free_kb="$(df -k --output=avail /var/lib 2>/dev/null | tail -1)"
    echo $(( free_kb * 1024 * 90 / 100 ))
}

create_btrfs_loopback() {
    if sudo incus storage list --format=csv 2>/dev/null | awk -F',' '{print $1}' | grep -q '^taos-worker-pool$'; then
        log "incus storage pool 'taos-worker-pool' already exists; reusing"
        return 0
    fi
    local cap_bytes cap_gb
    cap_bytes="$(worker_disk_cap)"
    # Convert to GB for incus size= parameter (round down, minimum 5GB)
    cap_gb=$(( cap_bytes / 1024**3 ))
    [[ "$cap_gb" -lt 5 ]] && cap_gb=5
    log "creating btrfs storage pool 'taos-worker-pool' (${cap_gb}GB)"
    sudo incus storage create taos-worker-pool btrfs "size=${cap_gb}GB"
}

launch_worker_lxc() {
    if sudo incus list --format=csv -c n 2>/dev/null | grep -q '^taos-worker$'; then
        log "worker LXC 'taos-worker' already exists; reusing"
        sudo incus start taos-worker 2>/dev/null || true
        return 0
    fi
    log "launching taos-worker (Ubuntu 24.04, privileged, nesting)"
    # Redirect stdin from /dev/null: when invoked via "curl | bash", the
    # script's stdin is the curl pipe; incus launch reads from stdin for YAML
    # config and would slurp the rest of the script, causing a parse error.
    sudo incus launch images:ubuntu/24.04 taos-worker \
        --storage taos-worker-pool \
        --config security.privileged=true \
        --config security.nesting=true < /dev/null
    log "waiting for taos-worker to come up..."
    for _i in $(seq 1 30); do
        if sudo incus exec taos-worker -- true 2>/dev/null; then
            log "taos-worker reachable"
            return 0
        fi
        sleep 2
    done
    die "taos-worker did not become reachable in 60 seconds"
}

phase1_host_prep() {
    check_kernel_features
    refuse_flat_mode_install
    log "installing host packages (incus, nftables)"
    if command -v apt >/dev/null 2>&1; then
        sudo apt update -y
        sudo apt install -y incus nftables curl btrfs-progs
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y incus nftables curl btrfs-progs
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm incus nftables curl btrfs-progs
    else
        die "unsupported package manager"
    fi
    sudo systemctl enable --now incus
    sudo incus admin init --minimal < /dev/null 2>/dev/null || true
    create_btrfs_loopback
    launch_worker_lxc
}

# --- phase 1: nftables port-forward + re-exec -----------------------------

setup_port_forward() {
    local worker_ip
    # The LXC may take a few seconds to acquire a DHCP address after the
    # exec-reachability check in launch_worker_lxc, so retry for up to 30s.
    for _i in $(seq 1 15); do
        worker_ip="$(sudo incus list taos-worker --format=csv -c 4 2>/dev/null | head -1 | awk '{print $1}')"
        [[ -n "$worker_ip" ]] && break
        sleep 2
    done
    if [[ -z "$worker_ip" ]]; then
        die "could not determine taos-worker IP for port-forward"
    fi
    log "forwarding host :8443 → ${worker_ip}:8443 via nftables"
    sudo nft add table ip taos 2>/dev/null || true
    sudo nft 'add chain ip taos prerouting { type nat hook prerouting priority -100 ; }' 2>/dev/null || true
    sudo nft 'flush chain ip taos prerouting' 2>/dev/null || true
    sudo nft "add rule ip taos prerouting tcp dport 8443 dnat to ${worker_ip}:8443"
    sudo bash -c 'nft list ruleset > /etc/nftables.conf'
    sudo systemctl enable --now nftables 2>/dev/null || true
}

reexec_into_worker_lxc() {
    log "re-execing install-worker.sh inside taos-worker for phase 2"
    sudo incus exec taos-worker -- bash -c "
        set -e
        export TAOS_INSIDE_WORKER=1
        export TAOS_CONTROLLER_URL='${CONTROLLER_URL}'
        export TAOS_WORKER_NAME='${WORKER_NAME}'
        curl -sL '${REPO}/raw/${BRANCH}/scripts/install-worker.sh' | bash -s -- '${CONTROLLER_URL}'
    "
}

# --- phase 2: nested incus + bees + register (runs inside worker LXC) -----

phase2_inside_lxc() {
    log "phase 2: nested incus + bees + register"

    # 1. Nested incus + bees
    apt update -y
    apt install -y incus bees curl

    if incus list >/dev/null 2>&1; then
        log "nested incus already initialised"
    else
        incus admin init --minimal < /dev/null
    fi

    # 2. bees systemd unit (default-on; opt-out via TAOS_NO_DEDUP)
    if [[ -z "${TAOS_NO_DEDUP:-}" ]]; then
        # bees needs UUID of the storage pool's btrfs filesystem.
        # The nested incus default pool is at /var/lib/incus/storage-pools/default.
        local pool_uuid
        pool_uuid="$(blkid -s UUID -o value /var/lib/incus/storage-pools/default 2>/dev/null || echo default)"
        mkdir -p /etc/bees /var/lib/bees
        cat > /etc/bees/${pool_uuid}.conf <<EOF
DB_SIZE=33554432
WORKAROUND_BTRFS_SEND=1
EOF
        cat > /etc/systemd/system/bees.service <<'BEESEOF'
[Unit]
Description=bees - btrfs deduplication daemon
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/bees /var/lib/incus/storage-pools/default
Restart=on-failure
Nice=19
IOSchedulingClass=idle
CPUWeight=10

[Install]
WantedBy=multi-user.target
BEESEOF
        systemctl daemon-reload
        systemctl enable --now bees.service || warn "bees enable failed (ok if storage not ready yet)"
    else
        log "TAOS_NO_DEDUP set; skipping bees"
    fi

    # 3. Determine host LAN IP (the bare host's IP, used for registration).
    #    The worker LXC sees its enclosing host via the gateway address on
    #    incusbr0 (typically the .1 of the bridge subnet). We use that as the
    #    host_lan_ip — it's how the controller's port-forward reaches us.
    local host_lan_ip
    host_lan_ip="$(ip route show default 2>/dev/null | awk '/via/ {print $3; exit}')"
    if [[ -z "$host_lan_ip" ]]; then
        host_lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    export TAOS_HOST_LAN_IP="$host_lan_ip"

    # 4. Register with controller (reuse existing function from the rest of
    #    install-worker.sh; it does POST /api/cluster/workers + /incus-enroll).
    install_and_enroll_incus
}

# --- phase 1 entry-point --------------------------------------------------
# On a bare host (PHASE=host, Linux only) run host prep and exit.
# Phase 2 (inside the worker LXC) falls through to the rest of the script.

if [[ "$os_name" == "Linux" && "$PHASE" == "host" ]]; then
    phase1_host_prep
    setup_port_forward
    reexec_into_worker_lxc
    log "install complete (phase 1 + phase 2)"
    exit 0
fi

if [[ "$os_name" == "Linux" && "$PHASE" == "inside" ]]; then
    phase2_inside_lxc
    log "phase 2 complete; worker LXC registered with controller"
    exit 0
fi

# --- system dependencies --------------------------------------------------

ensure_linux_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        log "installing apt deps (python3, venv, git, curl, libtorrent)"
        sudo apt-get update -qq
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            python3 python3-venv python3-pip git curl ca-certificates \
            libtorrent-rasterbar-dev libboost-python-dev
    elif command -v dnf >/dev/null 2>&1; then
        log "installing dnf deps (python3, git, curl, libtorrent)"
        # Fedora 42+ dropped libtorrent-rasterbar-devel from the default
        # repos. Torrent is optional for the worker (only used for model
        # distribution) so we install it with --skip-unavailable and
        # fall back to the core deps on failure.
        sudo dnf install -y -q --skip-unavailable python3 python3-pip python3-virtualenv git curl \
            libtorrent-rasterbar-devel boost-python3-devel || \
        sudo dnf install -y -q python3 python3-pip python3-virtualenv git curl
    elif command -v pacman >/dev/null 2>&1; then
        log "installing pacman deps"
        sudo pacman -Sy --noconfirm --needed python python-pip git curl \
            libtorrent-rasterbar boost
    elif command -v apk >/dev/null 2>&1; then
        log "installing apk deps"
        sudo apk add --no-cache python3 py3-pip git curl libtorrent-rasterbar
    else
        warn "unrecognised package manager — assuming python3/git/curl/libtorrent already present"
    fi
}

ensure_macos_deps() {
    if ! command -v python3 >/dev/null 2>&1; then
        if command -v brew >/dev/null 2>&1; then
            log "installing brew python"
            brew install python git
        else
            die "python3 not found and homebrew missing. install from https://brew.sh first"
        fi
    fi
    if ! command -v git >/dev/null 2>&1; then
        die "git not found"
    fi
    # libtorrent for the model torrent mesh — brew ships it as
    # libtorrent-rasterbar with python bindings.
    if command -v brew >/dev/null 2>&1; then
        if ! brew list libtorrent-rasterbar >/dev/null 2>&1; then
            log "installing libtorrent-rasterbar via brew"
            brew install libtorrent-rasterbar || warn "brew install libtorrent-rasterbar failed — torrent path will be unavailable"
        fi
    fi
}

case "$os_name" in
    Linux) ensure_linux_deps ;;
    Darwin) ensure_macos_deps ;;
    *) die "unsupported OS: $os_name" ;;
esac

# --- accelerator detection (advisory only — never auto-installs drivers) ----
#
# We never apt/dnf/pacman a GPU driver: most boxes don't have an
# accelerator at all (Apple Silicon, Intel iGPU, ARM SBCs), and the
# ones that do typically already have the right driver from the OS
# vendor. Touching the kernel-module + DKMS stack on someone else's
# box without consent is rude.
#
# What we do instead: detect what's physically present, then surface
# clear advice if the hardware is on the bus but the driver isn't
# loaded so the worker can use it. The user runs the install command
# themselves.

detect_and_advise_accelerators() {
    [[ "$os_name" != "Linux" ]] && return 0  # macOS detection lives elsewhere

    local found_any=0

    # ── NVIDIA ───────────────────────────────────────────────────────
    local nv_devices=0 nv_driver=0 nv_userspace=0
    [[ -e /dev/nvidia0 ]] && nv_devices=1
    [[ -d /proc/driver/nvidia ]] && nv_driver=1
    command -v nvidia-smi >/dev/null 2>&1 && nv_userspace=1

    local nv_on_bus=0
    if command -v lspci >/dev/null 2>&1; then
        if lspci 2>/dev/null | grep -qi "NVIDIA Corporation"; then
            nv_on_bus=1
        fi
    fi

    if (( nv_devices || nv_driver || nv_on_bus )); then
        found_any=1
        if (( nv_driver && nv_devices )); then
            log "nvidia: kernel module loaded + device nodes present (CUDA / Vulkan available)"
            if (( ! nv_userspace )); then
                # Driver is loaded but the userspace utils (nvidia-smi)
                # aren't installed. nvidia-utils is a thin userspace
                # wrapper that talks to an already-loaded driver — it
                # never touches the kernel module or DKMS, so installing
                # it is safe even though we have a 'never auto-install
                # nvidia-driver' rule. Without it, the worker falls back
                # to a known-cards lookup table for VRAM reporting,
                # which is approximate.
                local nv_driver_branch=""
                if [[ -r /proc/driver/nvidia/version ]]; then
                    # Match the dotted version like "580.126.18" anywhere
                    # in the line and grab the major (branch) component.
                    # The previous [^0-9]* pattern matched "86" out of
                    # "x86_64" which always appears before the real
                    # version on the NVRM line. The dotted-version
                    # anchor is unambiguous.
                    nv_driver_branch="$(grep -oP '\b\d+(?=\.\d+\.\d+)' /proc/driver/nvidia/version 2>/dev/null | head -1)"
                fi
                if command -v apt-get >/dev/null 2>&1 && [[ -n "$nv_driver_branch" ]]; then
                    log "installing nvidia-utils-$nv_driver_branch (matches loaded driver branch — safe userspace only)"
                    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "nvidia-utils-$nv_driver_branch" 2>/dev/null \
                        || warn "apt could not find nvidia-utils-$nv_driver_branch — VRAM will use the lookup table fallback"
                elif command -v dnf >/dev/null 2>&1; then
                    log "installing xorg-x11-drv-nvidia-cuda (provides nvidia-smi)"
                    sudo dnf install -y -q xorg-x11-drv-nvidia-cuda 2>/dev/null \
                        || warn "dnf could not install nvidia userspace — VRAM will use the lookup table fallback"
                elif command -v pacman >/dev/null 2>&1; then
                    log "installing nvidia-utils"
                    sudo pacman -S --noconfirm --needed nvidia-utils 2>/dev/null \
                        || warn "pacman could not install nvidia-utils — VRAM will use the lookup table fallback"
                else
                    warn "nvidia-smi is not installed and no compatible package manager found"
                    warn "  VRAM will be reported from the known-cards lookup table"
                fi
            fi
        elif (( nv_on_bus )); then
            warn "NVIDIA GPU detected on the PCIe bus but the kernel module is not loaded"
            warn "  the worker will not be able to use it until the driver is installed"
            if command -v apt-get >/dev/null 2>&1; then
                warn "  Debian / Ubuntu: sudo apt install nvidia-driver firmware-misc-nonfree && sudo reboot"
            elif command -v dnf >/dev/null 2>&1; then
                warn "  Fedora: enable RPM Fusion, then sudo dnf install akmod-nvidia xorg-x11-drv-nvidia-cuda && sudo reboot"
            elif command -v pacman >/dev/null 2>&1; then
                warn "  Arch: sudo pacman -S nvidia nvidia-utils && sudo reboot"
            else
                warn "  see your distro's NVIDIA driver documentation"
            fi
        fi
    fi

    # ── AMD ROCm / AMDGPU ───────────────────────────────────────────
    local amd_on_bus=0 amd_drm=0 amd_rocm=0
    if command -v lspci >/dev/null 2>&1; then
        if lspci 2>/dev/null | grep -qi "AMD/ATI" | head -1 >/dev/null \
           || lspci 2>/dev/null | grep -E "VGA|3D" | grep -qi "Advanced Micro Devices"; then
            amd_on_bus=1
        fi
    fi
    [[ -e /dev/kfd ]] && amd_drm=1
    [[ -d /opt/rocm ]] && amd_rocm=1

    if (( amd_on_bus || amd_drm )); then
        found_any=1
        if (( amd_rocm && amd_drm )); then
            log "amdgpu: kfd device + ROCm runtime present (HIP / Vulkan available)"
        elif (( amd_drm && ! amd_rocm )); then
            warn "AMD GPU detected with kfd device but ROCm is not installed"
            warn "  the worker will fall back to CPU until ROCm is set up"
            if command -v apt-get >/dev/null 2>&1; then
                warn "  Debian / Ubuntu: see https://rocm.docs.amd.com/projects/install-on-linux/en/latest/"
            elif command -v dnf >/dev/null 2>&1; then
                warn "  Fedora: sudo dnf install rocm-hip rocm-opencl"
            elif command -v pacman >/dev/null 2>&1; then
                warn "  Arch: sudo pacman -S rocm-hip-runtime rocm-opencl-runtime"
            fi
        elif (( amd_on_bus && ! amd_drm )); then
            warn "AMD GPU on the PCIe bus but the amdgpu kernel module is not loaded"
            warn "  ensure the amdgpu driver is enabled in your kernel and reboot"
        fi
    fi

    # ── Intel Arc / iGPU (Vulkan via Mesa) ──────────────────────────
    local intel_gpu=0
    if command -v lspci >/dev/null 2>&1; then
        if lspci 2>/dev/null | grep -E "VGA|3D" | grep -qi "Intel Corporation"; then
            intel_gpu=1
        fi
    fi
    if (( intel_gpu )); then
        found_any=1
        if [[ -d /sys/class/drm/card0 ]] || [[ -d /sys/class/drm/card1 ]]; then
            log "intel gpu: present (Vulkan via Mesa, no separate driver install needed on most distros)"
        else
            warn "Intel GPU detected on the PCIe bus but no DRM device — install mesa-vulkan-drivers"
        fi
    fi

    # ── Rockchip RKNPU ──────────────────────────────────────────────
    local rknpu_present=0
    if [[ -e /dev/rknpu ]]; then
        rknpu_present=1
    else
        for _npu_devfreq in /sys/class/devfreq/*.npu; do
            [[ -d "$_npu_devfreq" ]] && { rknpu_present=1; break; }
        done
    fi
    if (( rknpu_present )); then
        found_any=1
        # rkllama might be installed as a top-level command, or as a
        # venv-local entrypoint under ~/rkllama/rkllama-env/bin. Check
        # both — the install-rknpu.sh layout uses the venv path.
        local rkllama_found=0
        if command -v rkllama >/dev/null 2>&1; then
            rkllama_found=1
        elif [[ -x "$HOME/rkllama/rkllama-env/bin/rkllama_server" ]]; then
            rkllama_found=1
        fi
        if (( rkllama_found )); then
            log "rknpu: device present + rkllama backend installed"
        else
            warn "Rockchip NPU detected but rkllama is not installed"
            warn "  worker will run without NPU acceleration until you install rkllama"
            warn "  run: sudo bash scripts/install-rknpu.sh    (or set TAOS_RKNPU_SETUP=1 before re-running this installer to opt in automatically)"
            warn "  see: https://github.com/notpunchnox/rkllama"
            # Chained auto-install: if the caller opted in via env var,
            # run scripts/install-rknpu.sh now so rkllama is already
            # serving on :8080 before the worker systemd unit lands.
            if [[ "${TAOS_RKNPU_SETUP:-}" == "1" || "${TAOS_RKNPU_SETUP:-}" == "true" ]]; then
                local rknpu_script=""
                if [[ -x "$(dirname "$0")/install-rknpu.sh" ]]; then
                    rknpu_script="$(dirname "$0")/install-rknpu.sh"
                elif [[ -x "$INSTALL_DIR/scripts/install-rknpu.sh" ]]; then
                    rknpu_script="$INSTALL_DIR/scripts/install-rknpu.sh"
                fi
                if [[ -n "$rknpu_script" ]]; then
                    log "TAOS_RKNPU_SETUP=1 — chaining into $rknpu_script"
                    TAOS_RKNPU_SETUP=1 sudo -E bash "$rknpu_script" --yes \
                        || warn "install-rknpu.sh failed — continuing worker install anyway"
                else
                    warn "TAOS_RKNPU_SETUP=1 but install-rknpu.sh not found locally yet"
                    warn "  it will be available after the worker repo is cloned; run it then"
                fi
            fi
        fi
    fi

    # ── Apple Silicon (handled in macOS path) ───────────────────────
    if (( ! found_any )); then
        log "no discrete accelerator detected — worker will run on CPU"
    fi
}

detect_and_advise_accelerators

# --- incus install + controller enrollment --------------------------------
#
# Installs incus on Linux workers, configures an HTTPS listener on :8443,
# generates a one-shot trust token, and POSTs it to the controller so the
# controller can reach this worker's incus daemon for LXC service deployment.
#
# Skip with TAOS_SKIP_INCUS=1 (set automatically on macOS, where incus is
# Linux-only).  Re-running the installer is safe: each step checks whether
# it is already done before acting.

# Detect macOS — incus is Linux-only
if [[ "$os_name" == "Darwin" ]]; then
    log "macOS detected — incus is Linux-only; skipping LXC enrollment"
    log "  set TAOS_SKIP_INCUS=1 to suppress this message on future runs"
    TAOS_SKIP_INCUS=1
fi

install_and_enroll_incus() {
    # ── 1. Install incus if absent ──────────────────────────────────────
    if command -v incus >/dev/null 2>&1; then
        log "incus already installed at $(command -v incus)"
    else
        log "installing incus"
        # Determine distro ID
        local distro_id=""
        if [[ -f /etc/os-release ]]; then
            distro_id="$(. /etc/os-release && echo "${ID:-}")"
        fi

        case "$distro_id" in
            ubuntu|debian)
                # Prefer the official incus package; fall back to the
                # zabbly repo for older releases that don't ship it yet.
                if apt-cache show incus >/dev/null 2>&1; then
                    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq incus
                else
                    log "incus not in default apt — adding zabbly repo"
                    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl gpg
                    curl -fsSL https://pkgs.zabbly.com/key.asc \
                        | sudo gpg --dearmor -o /usr/share/keyrings/zabbly.gpg
                    # Resolve the release codename for the apt sources line
                    local codename
                    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")"
                    echo "deb [signed-by=/usr/share/keyrings/zabbly.gpg] https://pkgs.zabbly.com/incus/stable ${codename} main" \
                        | sudo tee /etc/apt/sources.list.d/zabbly-incus-stable.list > /dev/null
                    sudo apt-get update -qq
                    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq incus
                fi
                ;;
            fedora|rhel|centos|rocky|almalinux)
                sudo dnf install -y -q incus
                ;;
            arch|manjaro|endeavouros)
                sudo pacman -S --noconfirm --needed incus
                ;;
            *)
                warn "unrecognised distro '$distro_id' — cannot auto-install incus"
                warn "  install incus manually then re-run this script, or set TAOS_SKIP_INCUS=1 to skip LXC enrollment"
                return 0
                ;;
        esac
    fi

    # ── 2. Add user to incus-admin group ───────────────────────────────
    # Skip group management when running as root (e.g. inside the worker LXC);
    # root can reach the incus socket directly without group membership.
    local sg_incus
    if [[ "$(id -u)" == "0" ]]; then
        log "running as root — incus-admin group not needed"
        sg_incus="bash -c"
    else
        if groups "$USER" 2>/dev/null | grep -qw incus-admin; then
            log "user already in incus-admin group"
        else
            log "adding $USER to incus-admin group"
            sudo usermod -aG incus-admin "$USER"
            log "  NOTE: group change takes effect on next login"
            log "  using sg to run remaining incus commands in this session"
        fi
        # Run incus commands via sg so the group membership is effective
        # within this script session (avoids the user needing to re-login).
        sg_incus="sg incus-admin -c"
    fi

    # ── 3. First-time minimal init ──────────────────────────────────────
    if $sg_incus "incus list" >/dev/null 2>&1; then
        log "incus daemon already initialised"
    else
        log "running incus admin init --minimal (first-time setup)"
        $sg_incus "incus admin init --minimal < /dev/null"
    fi

    # ── 4. Enable HTTPS listener on :8443 ──────────────────────────────
    local current_addr
    current_addr="$($sg_incus "incus config get core.https_address" 2>/dev/null || true)"
    if [[ "$current_addr" == ":8443" ]]; then
        log "incus HTTPS listener already set to :8443"
    else
        log "enabling incus HTTPS listener on :8443"
        $sg_incus "incus config set core.https_address :8443"
    fi

    # ── 5. Generate a one-shot trust token ─────────────────────────────
    log "generating incus trust token for controller enrollment"
    local token_output
    token_output="$($sg_incus "incus config trust add controller-enroll" 2>&1)"
    # The token is the last non-empty line of the output
    local TOKEN
    TOKEN="$(echo "$token_output" | awk 'NF{last=$0} END{print last}')"
    if [[ -z "$TOKEN" ]]; then
        warn "failed to generate incus trust token — LXC enrollment skipped"
        warn "  to enroll manually: incus config trust add controller-enroll"
        warn "  then: curl -X POST $CONTROLLER_URL/api/cluster/workers/$WORKER_NAME/incus-enroll \\"
        warn "      -H 'Content-Type: application/json' \\"
        warn "      -d '{\"incus_url\": \"https://<LAN_IP>:8443\", \"token\": \"<TOKEN>\"}'"
        return 0
    fi

    # ── 6. Detect LAN IP ───────────────────────────────────────────────
    # Prefer the source address that the kernel would use to reach the
    # controller, so we don't accidentally pick up docker0 / incusbr0 /
    # Tailscale addresses that the controller can't reach back on.
    local LAN_IP=""
    local _ctrl_host
    _ctrl_host="$(printf '%s' "$CONTROLLER_URL" | sed 's|^[^/]*/*/||; s|[:/].*||')"
    if [[ -n "$_ctrl_host" ]] && command -v ip >/dev/null 2>&1; then
        LAN_IP="$(ip -4 route get "$_ctrl_host" 2>/dev/null \
            | awk '/src/{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
    fi
    if [[ -z "$LAN_IP" ]]; then
        # Fallback 1: first token from hostname -I (may include bridge IPs)
        LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    if [[ -z "$LAN_IP" ]]; then
        # Fallback 2: first non-loopback global IPv4
        LAN_IP="$(ip -4 addr show scope global 2>/dev/null \
            | awk '/inet /{print $2}' | head -1 | cut -d/ -f1)"
    fi
    if [[ -z "$LAN_IP" ]]; then
        warn "could not detect LAN IP — LXC enrollment skipped"
        warn "  to enroll manually: curl -X POST $CONTROLLER_URL/api/cluster/workers/$WORKER_NAME/incus-enroll \\"
        warn "      -H 'Content-Type: application/json' \\"
        warn "      -d '{\"incus_url\": \"https://<LAN_IP>:8443\", \"token\": \"$TOKEN\"}'"
        return 0
    fi

    log "LAN IP: $LAN_IP"

    # ── 7. Register worker with controller ─────────────────────────────
    # POST /api/cluster/workers so the controller knows this worker exists
    # before we try to /incus-enroll (that endpoint 404s for unknown workers).
    # Includes the new LXC-mode fields: host_lan_ip and worker_lxc_image_version.
    log "registering worker '${WORKER_NAME}' with controller at $CONTROLLER_URL"
    local reg_code
    reg_code="$(curl -sS -o /tmp/taos-worker-register.out -w "%{http_code}" \
        -X POST "$CONTROLLER_URL/api/cluster/workers" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"${WORKER_NAME}\",
            \"url\": \"https://${LAN_IP}:8443\",
            \"hardware\": {},
            \"backends\": [],
            \"capabilities\": [],
            \"platform\": \"linux\",
            \"models\": [],
            \"host_lan_ip\": \"${TAOS_HOST_LAN_IP:-${LAN_IP}}\",
            \"worker_lxc_image_version\": \"ubuntu/24.04/amd64\"
        }" \
        2>/tmp/taos-worker-register.err || true)"

    if [[ "$reg_code" == 2* ]]; then
        log "worker registered successfully (HTTP $reg_code)"
    elif [[ "$reg_code" == "409" ]]; then
        log "worker already registered (HTTP 409) — continuing to incus-enroll"
    else
        warn "worker registration returned HTTP $reg_code"
        warn "  response: $(cat /tmp/taos-worker-register.out 2>/dev/null)"
        warn "  continuing to incus-enroll anyway"
    fi

    # ── 8. POST to controller ───────────────────────────────────────────
    log "enrolling incus remote with controller at $CONTROLLER_URL"
    local http_code
    http_code="$(curl -sS -o /tmp/taos-incus-enroll.out -w "%{http_code}" \
        -X POST "$CONTROLLER_URL/api/cluster/workers/$WORKER_NAME/incus-enroll" \
        -H "Content-Type: application/json" \
        -d "{\"incus_url\": \"https://${LAN_IP}:8443\", \"token\": \"${TOKEN}\"}" \
        2>/tmp/taos-incus-enroll.err || true)"

    if [[ "$http_code" == 2* ]]; then
        log "incus remote enrolled successfully (HTTP $http_code)"
    else
        warn "incus enrollment returned HTTP $http_code"
        warn "  response: $(cat /tmp/taos-incus-enroll.out 2>/dev/null)"
        warn "  to retry manually:"
        warn "    TOKEN=\$(incus config trust add controller-enroll 2>&1 | tail -1)"
        warn "    curl -X POST $CONTROLLER_URL/api/cluster/workers/$WORKER_NAME/incus-enroll \\"
        warn "        -H 'Content-Type: application/json' \\"
        warn "        -d \"{\\\"incus_url\\\": \\\"https://$LAN_IP:8443\\\", \\\"token\\\": \\\"\$TOKEN\\\"}\" "
        warn "  set TAOS_SKIP_INCUS=1 to skip this block entirely on re-runs"
    fi
}

# --- bundled Ollama backend (TAOS-namespaced) ----------------------------
#
# install-worker.sh installs a TAOS-namespaced Ollama by default so a
# fresh worker comes up with at least one functional inference backend
# out of the box. Without this, the worker registers with the controller
# but reports zero backends until the user manually installs something.
#
# Crucially, this is *not* a system-wide Ollama install:
#
#   - Binary lives at $INSTALL_DIR/backends/ollama/bin/ollama
#       NOT /usr/local/bin/ollama
#   - Models live at $INSTALL_DIR/backends/ollama/models/
#       NOT ~/.ollama/models
#   - Listens on TAOS_OLLAMA_PORT (default 21434)
#       NOT 11434
#   - Runs as a dedicated systemd unit named taos-ollama.service
#       NOT ollama.service
#   - Removing the worker removes only the namespaced files
#
# This means a user with an existing ollama install (their own models,
# their own port, their own service) keeps everything they had — TAOS
# adds its own ollama alongside on a different port. The worker
# auto-detects both and the controller routes between them.
#
# Skip with TAOS_NO_OLLAMA=1 if you have an existing ollama you want
# to be the only one, or you don't need any LLM backend at all (e.g.
# this worker only does image gen via the NPU).

install_taos_ollama() {
    if [[ "${TAOS_NO_OLLAMA:-}" == "1" || "${TAOS_NO_OLLAMA:-}" == "true" ]]; then
        log "TAOS_NO_OLLAMA=1 — skipping bundled Ollama install"
        return 0
    fi

    local ollama_dir="$INSTALL_DIR/backends/ollama"
    local ollama_bin="$ollama_dir/bin/ollama"
    local ollama_models="$ollama_dir/models"
    local ollama_port="${TAOS_OLLAMA_PORT:-21434}"

    if [[ -x "$ollama_bin" ]]; then
        log "TAOS-namespaced Ollama already installed at $ollama_bin — skipping download"
    else
        log "installing TAOS-namespaced Ollama into $ollama_dir (will not touch any existing system Ollama)"
        mkdir -p "$ollama_dir/bin" "$ollama_models"

        # Pull the static linux binary directly. Avoids the upstream
        # install.sh which auto-installs cuda-drivers system-wide and
        # creates /usr/local/bin/ollama + /etc/systemd/system/ollama.service —
        # both things we never want.
        local arch_suffix
        case "$arch" in
            x86_64)  arch_suffix="amd64" ;;
            aarch64|arm64) arch_suffix="arm64" ;;
            *) warn "unsupported arch '$arch' for bundled Ollama — skipping"; return 0 ;;
        esac

        local ollama_url="https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${arch_suffix}.tar.zst"
        local tmp_dir="$ollama_dir/.download"
        mkdir -p "$tmp_dir"

        # zstd may not be installed by default on minimal images
        if ! command -v zstd >/dev/null 2>&1; then
            if command -v apt-get >/dev/null 2>&1; then
                sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd
            elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y -q zstd
            elif command -v pacman >/dev/null 2>&1; then
                sudo pacman -S --noconfirm --needed zstd
            elif command -v apk >/dev/null 2>&1; then
                sudo apk add --no-cache zstd
            fi
        fi

        if ! curl -fsSL --retry 3 -o "$tmp_dir/ollama.tar.zst" "$ollama_url"; then
            warn "failed to download bundled Ollama from $ollama_url"
            warn "  worker will start with no LLM backend; install one manually"
            return 0
        fi

        # Extract — Ollama tar.zst contains bin/ + lib/ at the root
        if ! (cd "$tmp_dir" && tar --use-compress-program=unzstd -xf ollama.tar.zst); then
            warn "failed to extract Ollama tarball"
            return 0
        fi

        # Move the binary + libs into the namespaced dir
        if [[ -f "$tmp_dir/bin/ollama" ]]; then
            mv "$tmp_dir/bin/ollama" "$ollama_bin"
            chmod +x "$ollama_bin"
        else
            warn "Ollama tarball did not contain bin/ollama at the expected path"
            return 0
        fi
        if [[ -d "$tmp_dir/lib" ]]; then
            mkdir -p "$ollama_dir/lib"
            cp -r "$tmp_dir/lib/." "$ollama_dir/lib/"
        fi
        rm -rf "$tmp_dir"

        log "TAOS Ollama installed at $ollama_bin"
    fi

    # systemd unit — system-level if we have sudo, user-level otherwise.
    # Names the unit 'taos-ollama' explicitly so it doesn't collide
    # with the user's own ollama.service if they have one.
    local unit_path="/etc/systemd/system/taos-ollama.service"
    local sudo_cmd=""
    [[ "$(id -u)" != "0" ]] && sudo_cmd="sudo"

    if have_root_or_sudo; then
        $sudo_cmd tee "$unit_path" > /dev/null <<EOF
[Unit]
Description=TinyAgentOS bundled Ollama (TAOS-namespaced)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$(id -gn)
Environment=OLLAMA_HOST=127.0.0.1:$ollama_port
Environment=OLLAMA_MODELS=$ollama_models
Environment=LD_LIBRARY_PATH=$ollama_dir/lib:$ollama_dir/lib/ollama
ExecStart=$ollama_bin serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        $sudo_cmd systemctl daemon-reload
        $sudo_cmd systemctl enable --now taos-ollama.service
        log "taos-ollama.service running on 127.0.0.1:$ollama_port"
        log "  models dir: $ollama_models"
        log "  to add a model:  OLLAMA_HOST=127.0.0.1:$ollama_port $ollama_bin pull qwen3:4b"
    else
        warn "no sudo available — skipping systemd unit install for taos-ollama"
        warn "  start manually: OLLAMA_HOST=127.0.0.1:$ollama_port OLLAMA_MODELS=$ollama_models $ollama_bin serve &"
    fi
}

# --- clone / update the repo ---------------------------------------------
# Must happen BEFORE install_taos_ollama so git clone gets an empty
# directory to work with. install_taos_ollama then drops its bundled
# Ollama into $INSTALL_DIR/backends/ollama inside the cloned repo.

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    log "cloning $REPO into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    # If INSTALL_DIR already exists but isn't a git repo (e.g. a previous
    # partial install), move it aside so the clone can proceed.
    if [[ -d "$INSTALL_DIR" ]]; then
        mv "$INSTALL_DIR" "${INSTALL_DIR}.old.$(date +%s)"
    fi
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
else
    log "updating existing checkout"
    (cd "$INSTALL_DIR" && git fetch --depth 1 origin "$BRANCH" && git reset --hard "origin/$BRANCH")
fi

cd "$INSTALL_DIR"

install_taos_ollama

# --- python venv + worker-only deps --------------------------------------

if [[ ! -d .venv ]]; then
    log "creating venv"
    python3 -m venv .venv
fi

log "installing worker python deps into .venv"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet \
    httpx \
    pydantic \
    psutil \
    fastapi \
    uvicorn \
    pyyaml \
    pillow

# libtorrent is optional (only used for model torrent mesh). It isn't on
# PyPI as a wheel for most platforms — it ships as the distro's
# libtorrent-rasterbar package. Try pip in case a wheel is available;
# silently skip if not. Worker still functions without it.
./.venv/bin/pip install --quiet libtorrent 2>/dev/null || \
    warn "libtorrent python bindings not available — torrent model mesh disabled (worker still functional)"

# --- first-boot benchmark -----------------------------------------------

if [[ -z "${TAOS_SKIP_BENCHMARK:-}" ]]; then
    log "running initial worker benchmark (first-join only — subsequent runs are manual)"
    ./.venv/bin/python -m tinyagentos.benchmark.runner \
        --report-to "$CONTROLLER_URL" \
        --worker-name "$WORKER_NAME" \
        --first-join \
    || warn "benchmark runner not available yet — skipping (worker will run without baseline scores)"
fi

# --- incus enrollment (after worker registration) ------------------------
# The controller's /incus-enroll endpoint returns 404 for unknown workers.
# The benchmark runner above registers the worker, so enrollment must come
# after it completes.

if [[ "${TAOS_SKIP_INCUS:-}" == "1" || "${TAOS_SKIP_INCUS:-}" == "true" ]]; then
    log "TAOS_SKIP_INCUS=1 — skipping incus install and enrollment"
else
    if [[ "$os_name" == "Linux" ]]; then
        install_and_enroll_incus
    fi
fi

# --- system service install ---------------------------------------------

# A system-level unit is preferred whenever the script has sudo access
# (which is always true on Linux because the apt/dnf/etc step earlier
# already used sudo). System units survive logout, run from boot, and
# avoid the PAM-session gymnastics required for `systemctl --user` on a
# fresh host where the install user has never had an active login.
#
# The user-mode path is kept as a fallback for the rare environment
# where sudo is genuinely unavailable.

# have_root_or_sudo is defined at the top of the script so early stages
# (ollama systemd install) can use it too. The stanza below is retained
# as a no-op so the original code structure stays intact.
_unused_have_root_or_sudo() {
    if [[ "$(id -u)" = "0" ]]; then
        return 0
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        return 0
    fi
    return 1
}

install_deploy_helper() {
    local sudo_cmd=""
    if [[ "$(id -u)" != "0" ]]; then sudo_cmd="sudo"; fi

    local helper_src="$INSTALL_DIR/tinyagentos/scripts/taos-deploy-helper.sh"
    local helper_dst="/usr/local/bin/taos-deploy-helper"

    if [[ -f "$helper_src" ]]; then
        $sudo_cmd cp "$helper_src" "$helper_dst"
        $sudo_cmd chmod 755 "$helper_dst"
        log "installed $helper_dst"
    else
        warn "deploy helper not found at $helper_src — remote backend deployment will not work"
        return
    fi

    # Sudoers drop-in: let the worker user run the deploy helper without a
    # password. Only this one script is allowed — the worker cannot execute
    # arbitrary commands as root.
    local sudoers="/etc/sudoers.d/taos-worker"
    $sudo_cmd tee "$sudoers" > /dev/null <<SUDOERS
# TinyAgentOS worker — passwordless backend deployment
# Allows the controller to install/update backends on this worker
# without SSH or interactive password prompts.
$USER ALL=(ALL) NOPASSWD: $helper_dst
SUDOERS
    $sudo_cmd chmod 440 "$sudoers"
    log "sudoers drop-in installed at $sudoers — $USER can run $helper_dst without password"
}

install_linux_systemd_system() {
    local unit="/etc/systemd/system/tinyagentos-worker.service"
    local sudo_cmd=""
    if [[ "$(id -u)" != "0" ]]; then
        sudo_cmd="sudo"
    fi

    # Deploy helper for remote backend management from the controller UI
    install_deploy_helper

    $sudo_cmd tee "$unit" > /dev/null <<EOF
[Unit]
Description=TinyAgentOS Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$(id -gn)
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m tinyagentos.worker $CONTROLLER_URL --name $WORKER_NAME
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    log "installed $unit (system unit, runs as $USER)"
    $sudo_cmd systemctl daemon-reload
    $sudo_cmd systemctl enable --now tinyagentos-worker
    log "worker running as system service"
    log "check: systemctl status tinyagentos-worker"
    log "logs:  journalctl -u tinyagentos-worker -f"
}

install_linux_systemd_user() {
    local unit_dir="$HOME/.config/systemd/user"
    local unit="$unit_dir/tinyagentos-worker.service"
    mkdir -p "$unit_dir"
    cat > "$unit" <<EOF
[Unit]
Description=TinyAgentOS Worker
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m tinyagentos.worker $CONTROLLER_URL --name $WORKER_NAME
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
    log "installed $unit (user unit fallback — sudo unavailable)"

    # Make the user manager start on boot without an active login. Must
    # happen BEFORE the systemctl --user calls so the user bus is up.
    loginctl enable-linger "$USER" 2>/dev/null || true

    # When run from a non-interactive context (curl|bash, ssh -c, etc),
    # XDG_RUNTIME_DIR may be unset and systemctl --user can't find the
    # user bus. Set it explicitly and wait briefly for the user manager
    # to come up after enable-linger.
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    local tries=0
    while [[ $tries -lt 10 ]] && ! systemctl --user is-system-running >/dev/null 2>&1; do
        sleep 1
        tries=$((tries + 1))
    done

    if ! systemctl --user daemon-reload 2>/dev/null; then
        warn "user systemd not reachable — leaving the unit on disk so it activates on next login"
        warn "to start manually: systemctl --user daemon-reload && systemctl --user enable --now tinyagentos-worker"
        return 0
    fi
    systemctl --user enable --now tinyagentos-worker
    log "worker running as user systemd service"
    log "check: systemctl --user status tinyagentos-worker"
    log "logs:  journalctl --user -u tinyagentos-worker -f"
}

install_linux_systemd() {
    if have_root_or_sudo; then
        install_linux_systemd_system
    else
        install_linux_systemd_user
    fi
}

install_macos_launchd() {
    local plist_dir="$HOME/Library/LaunchAgents"
    local plist="$plist_dir/com.tinyagentos.worker.plist"
    mkdir -p "$plist_dir"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.tinyagentos.worker</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/.venv/bin/python</string>
        <string>-m</string>
        <string>tinyagentos.worker</string>
        <string>$CONTROLLER_URL</string>
        <string>--name</string>
        <string>$WORKER_NAME</string>
    </array>
    <key>WorkingDirectory</key><string>$INSTALL_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$INSTALL_DIR/worker.log</string>
    <key>StandardErrorPath</key><string>$INSTALL_DIR/worker.err</string>
</dict>
</plist>
EOF
    log "installed $plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    log "worker running as launchd agent"
    log "check: launchctl list | grep tinyagentos"
    log "logs:  tail -f $INSTALL_DIR/worker.log"
}

if [[ "$SERVICE_MODE" == "skip" ]]; then
    log "TAOS_SERVICE=skip — not installing a service unit"
    log "run manually: cd $INSTALL_DIR && ./.venv/bin/python -m tinyagentos.worker $CONTROLLER_URL --name $WORKER_NAME"
else
    case "$os_name" in
        Linux) install_linux_systemd ;;
        Darwin) install_macos_launchd ;;
    esac
fi

log "install complete"
log "worker name: $WORKER_NAME"
log "controller:  $CONTROLLER_URL"
log "install dir: $INSTALL_DIR"
if have_root_or_sudo; then
    log "to upgrade later: cd $INSTALL_DIR && git pull && sudo systemctl restart tinyagentos-worker"
else
    log "to upgrade later: cd $INSTALL_DIR && git pull && systemctl --user restart tinyagentos-worker"
fi
