#!/usr/bin/env bash
# TinyAgentOS controller installer — Linux + macOS
# Bootstraps the TinyAgentOS controller: clones the repo, creates a Python
# venv, installs all controller deps, and registers tinyagentos.service and
# qmd.service as systemd units so the web UI is accessible at
# http://<host>:6969 immediately after the script exits.
#
# Usage:
#     curl -fsSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh | sudo bash
#
# or download + inspect + run:
#     curl -O https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh
#     chmod +x install-server.sh
#     sudo ./install-server.sh
#
# Environment overrides:
#     TAOS_INSTALL_DIR    where to install (default: ~/tinyagentos)
#     TAOS_BRANCH         git branch or tag (default: master)
#     TAOS_REPO           git remote (default: https://github.com/jaylfc/tinyagentos)
#     TAOS_PORT           controller listen port (default: 6969)
#     TAOS_QMD_PORT       qmd model service port (default: 7832)
#     TAOS_SERVICE        install as system service: auto (default), system, user, skip
#     TAOS_SKIP_QMD       if set, skip qmd.service install (useful for boxes without a model backend)
#     TAOS_RKNPU_SETUP    if set to 1, auto-run install-rknpu.sh when RKNPU is detected but rkllama is missing
set -euo pipefail

INSTALL_DIR="${TAOS_INSTALL_DIR:-$HOME/tinyagentos}"
BRANCH="${TAOS_BRANCH:-master}"
REPO="${TAOS_REPO:-https://github.com/jaylfc/tinyagentos}"
TAOS_PORT="${TAOS_PORT:-6969}"
TAOS_QMD_PORT="${TAOS_QMD_PORT:-7832}"
SERVICE_MODE="${TAOS_SERVICE:-auto}"

os_name="$(uname -s)"
arch="$(uname -m)"

log()  { printf '\033[1;34m[server-install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[server-install]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[server-install]\033[0m %s\n' "$*" >&2; exit 1; }

log "os=$os_name arch=$arch"
log "install_dir=$INSTALL_DIR branch=$BRANCH port=$TAOS_PORT qmd_port=$TAOS_QMD_PORT"

# --- system dependencies --------------------------------------------------

ensure_linux_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        log "installing apt deps (python3, venv, git, curl, libtorrent, sqlite3, sqlcipher)"
        # nodejs/npm are intentionally excluded here: ensure_node22() installs
        # Node 22 via NodeSource immediately after. Including apt's nodejs/npm
        # causes "held broken packages" when NodeSource's nodejs is already
        # present, because apt's npm conflicts with NodeSource's bundled npm.
        sudo apt-get update -qq
        # vulkan-tools provides vulkaninfo, which the runtime hardware
        # probe shells out to for iGPU detection. Without it, taOS
        # silently reports vulkan: false on Intel / AMD iGPUs (#354).
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            python3 python3-venv python3-pip git curl ca-certificates \
            libtorrent-rasterbar-dev libboost-python-dev sqlite3 libsqlcipher-dev \
            vulkan-tools
    elif command -v dnf >/dev/null 2>&1; then
        log "installing dnf deps (python3, git, curl, libtorrent, nodejs, sqlcipher, vulkan-tools)"
        # rb_libtorrent: Fedora's libtorrent-rasterbar is split into the C++
        # library (rb_libtorrent), its devel headers (rb_libtorrent-devel), and
        # the Python bindings (rb_libtorrent-python3). The bindings are NOT on
        # PyPI for Fedora's Python 3.14; install them as a system package and
        # rely on --system-site-packages in the venv below.
        # python3-virtualenv is intentionally omitted — `python3 -m venv` from
        # stdlib is sufficient and avoids a name that's been renamed/removed
        # across recent Fedora releases.
        sudo dnf install -y -q python3 python3-pip git curl \
            rb_libtorrent-devel rb_libtorrent-python3 boost-python3 \
            sqlite nodejs npm sqlcipher-devel vulkan-tools
    elif command -v pacman >/dev/null 2>&1; then
        log "installing pacman deps"
        sudo pacman -Sy --noconfirm --needed python python-pip git curl \
            libtorrent-rasterbar boost sqlite nodejs npm sqlcipher vulkan-tools
    elif command -v apk >/dev/null 2>&1; then
        log "installing apk deps"
        sudo apk add --no-cache python3 py3-pip git curl libtorrent-rasterbar sqlite nodejs npm sqlcipher-dev vulkan-tools
    else
        warn "unrecognised package manager — assuming python3/git/curl/libtorrent/nodejs already present"
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
    if command -v brew >/dev/null 2>&1; then
        if ! brew list libtorrent-rasterbar >/dev/null 2>&1; then
            log "installing libtorrent-rasterbar via brew"
            brew install libtorrent-rasterbar || warn "brew install libtorrent-rasterbar failed — torrent path will be unavailable"
        fi
        if ! command -v node >/dev/null 2>&1; then
            log "installing node via brew (required for qmd)"
            brew install node || warn "brew install node failed — qmd service will be unavailable"
        fi
    fi
}

case "$os_name" in
    Linux)  ensure_linux_deps ;;
    Darwin) ensure_macos_deps ;;
    *)      die "unsupported OS: $os_name" ;;
esac

# --- node.js version guard ---------------------------------------------------
# qmd requires Node >=22. Debian/Ubuntu (and Armbian vendor-kernel images) ship
# Node 18 from apt, which causes node-llama-cpp to attempt a multi-hour native
# compile from source instead of downloading a prebuilt binary. Detect this and
# upgrade via NodeSource (ARM64 + x86_64 binaries, no kernel changes required).

ensure_node22() {
    local node_major=0
    if command -v node >/dev/null 2>&1; then
        node_major="$(node --version 2>/dev/null | sed 's/v//' | cut -d. -f1)"
    fi

    if [[ "$node_major" -ge 22 ]]; then
        log "node $(node --version) — ok (>=22 required)"
        return 0
    fi

    log "node ${node_major:-not found} is too old (need >=22) — upgrading to Node 22 LTS via NodeSource"

    if command -v apt-get >/dev/null 2>&1; then
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - \
            || die "NodeSource setup script failed"
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs \
            || die "apt-get install nodejs (22) failed"
    elif command -v dnf >/dev/null 2>&1; then
        curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo -E bash - \
            || die "NodeSource setup script failed"
        sudo dnf install -y nodejs \
            || die "dnf install nodejs (22) failed"
    elif command -v pacman >/dev/null 2>&1; then
        # Arch ships a current node in extra — just upgrade whatever is installed
        sudo pacman -Sy --noconfirm nodejs npm \
            || warn "pacman upgrade of nodejs failed — qmd install may fail on old Node"
    else
        warn "cannot auto-upgrade Node $node_major — unsupported package manager"
        warn "  install Node 22 manually then re-run this script"
        warn "  see: https://nodejs.org/en/download"
    fi

    # Re-check after upgrade
    if command -v node >/dev/null 2>&1; then
        log "node now at $(node --version)"
    fi
}

[[ "$os_name" == "Linux" ]] && ensure_node22

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
# loaded so the controller can use it. The user runs the install command
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
                warn "nvidia-smi is not installed — VRAM size will report as unknown to the controller"
                warn "  optional: install nvidia-utils-XXX matching your driver version"
            fi
        elif (( nv_on_bus )); then
            warn "NVIDIA GPU detected on the PCIe bus but the kernel module is not loaded"
            warn "  the controller will not be able to use it until the driver is installed"
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
            warn "  the controller will fall back to CPU until ROCm is set up"
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
    # Multi-signal detection: different RK3588 kernels expose the NPU at
    # different paths. /dev/rknpu and /sys/class/devfreq/*.npu are the
    # cleanest signals when present, but some kernel builds skip devfreq
    # or expose only the debugfs entry, and on others the user-space
    # device node is gated behind a module that isn't autoloaded. Fall
    # back to the device-tree compatible string (the SoC's bootloader
    # ID, always present on real RK35xx hardware) so we don't miss the
    # NPU on those boards.  TAOS_FORCE_RKNPU=1 forces this branch on
    # for testing or for kernels we don't yet recognise.
    local rknpu_present=0
    local rknpu_signal=""
    if [[ "${TAOS_FORCE_RKNPU:-}" == "1" || "${TAOS_FORCE_RKNPU:-}" == "true" ]]; then
        rknpu_present=1
        rknpu_signal="TAOS_FORCE_RKNPU"
    elif [[ -e /dev/rknpu ]]; then
        rknpu_present=1
        rknpu_signal="/dev/rknpu"
    elif [[ -d /sys/kernel/debug/rknpu ]] || [[ -e /sys/kernel/debug/rknpu/load ]]; then
        rknpu_present=1
        rknpu_signal="/sys/kernel/debug/rknpu"
    elif command -v lsmod >/dev/null 2>&1 && lsmod 2>/dev/null | awk '{print $1}' | grep -qx "rknpu"; then
        rknpu_present=1
        rknpu_signal="lsmod:rknpu"
    elif [[ -r /proc/device-tree/compatible ]] && tr -d '\0' < /proc/device-tree/compatible 2>/dev/null | grep -qE "rockchip,rk(3588|3576|3568)"; then
        rknpu_present=1
        rknpu_signal="device-tree:rockchip-rk3xxx"
    else
        for _npu_devfreq in /sys/class/devfreq/*.npu; do
            [[ -d "$_npu_devfreq" ]] && { rknpu_present=1; rknpu_signal="$_npu_devfreq"; break; }
        done
    fi
    if (( rknpu_present )); then
        log "rknpu: detected via $rknpu_signal"
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
            log "Rockchip NPU detected — auto-installing the jaylfc/rkllama backend"
            log "  (set TAOS_NO_RKNPU=1 to skip, or run install-rknpu.sh manually later)"
            log "  fork: https://github.com/jaylfc/rkllama"
            # Chained auto-install on by default for RK3588 hosts: if the
            # NPU is present and rkllama isn't installed yet, set up our
            # fork now so rkllama is already serving on :8080 before the
            # controller systemd unit lands. Opt-out via TAOS_NO_RKNPU=1.
            if [[ "${TAOS_NO_RKNPU:-}" == "1" || "${TAOS_NO_RKNPU:-}" == "true" ]]; then
                warn "TAOS_NO_RKNPU=1 — skipping rkllama install; controller will run CPU-only on this NPU box"
                warn "  to set up later: sudo bash scripts/install-rknpu.sh"
            else
                # Defer the actual install to after the repo clone — the
                # script we need lives at $INSTALL_DIR/scripts/install-rknpu.sh
                # and that path doesn't exist on this very first detection
                # pass. install_rknpu_if_pending below picks this up.
                RKNPU_PENDING_INSTALL=1
                log "rkllama install deferred until after repo clone"
            fi
        fi
    fi

    # ── Apple Silicon (handled in macOS path) ───────────────────────
    if (( ! found_any )); then
        log "no discrete accelerator detected — controller will run on CPU"
    fi
}

# Run the deferred rknpu auto-install once the repo is on disk. Called
# after `git clone $INSTALL_DIR`. Skips if no NPU was detected up front
# or if the user opted out via TAOS_NO_RKNPU=1.
install_rknpu_if_pending() {
    if [[ "${RKNPU_PENDING_INSTALL:-0}" != "1" ]]; then
        return 0
    fi
    local rknpu_script="$INSTALL_DIR/scripts/install-rknpu.sh"
    # We invoke via `bash "$rknpu_script"`, so executable bit isn't
    # required — only readable. Per CodeRabbit nit on #405.
    if [[ ! -f "$rknpu_script" || ! -r "$rknpu_script" ]]; then
        warn "install-rknpu.sh missing or unreadable at $rknpu_script — skipping rkllama auto-install"
        warn "  to set up rkllama later: sudo bash $INSTALL_DIR/scripts/install-rknpu.sh"
        return 0
    fi
    log "chaining into $rknpu_script (rkllama auto-install)"
    sudo -E bash "$rknpu_script" --yes \
        || warn "install-rknpu.sh failed — continuing controller install anyway"
}

detect_and_advise_accelerators

# --- container runtime — install Incus if nothing is present -------------
#
# taOS uses a container runtime (Incus, Docker, or Podman) to deploy
# worker containers. On macOS, the Apple Containerization framework
# (bundled with macOS 26) is used instead — no install needed here.
# On Linux, if no runtime is found, we install Incus via the system
# package manager on Debian/Ubuntu/Fedora. On Arch/Alpine we log a
# manual-install notice and continue — those distros have it in the
# repos but the AUR/apk setup varies too much to auto-invoke here.
# A failed Incus install is non-fatal: taOS still starts; cluster and
# worker-container features are simply unavailable until one is added.

ensure_container_runtime() {
    if [[ "$os_name" == "Darwin" ]]; then
        log "container runtime: macOS — Apple Containerization framework (macOS 26+) will be used"
        return 0
    fi

    # Check if any supported runtime is already present
    if command -v incus >/dev/null 2>&1; then
        log "container runtime: incus $(incus --version 2>/dev/null | head -1) — ok"
        return 0
    fi
    if command -v docker >/dev/null 2>&1; then
        log "container runtime: docker $(docker --version 2>/dev/null | head -1) — ok"
        return 0
    fi
    if command -v podman >/dev/null 2>&1; then
        log "container runtime: podman $(podman --version 2>/dev/null | head -1) — ok"
        return 0
    fi

    log "no container runtime found — installing Incus"

    local installed=0
    if command -v apt-get >/dev/null 2>&1; then
        # Try the distro's default repos first (Ubuntu 24.04+ ships incus in
        # universe; some Debian unstable releases also have it). Fall back
        # to Zabbly for older Debian/Ubuntu where the package isn't there.
        # apt-cache madison is the lightest "is the package available?"
        # probe — empty output means no candidate, which is what we want.
        sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>/dev/null || true
        if [[ -n "$(apt-cache madison incus 2>/dev/null)" ]]; then
            log "incus available in default apt repos — installing"
            if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq incus; then
                installed=1
                log "container runtime: incus installed from default apt repos"
            else
                warn "default apt install of incus failed — see /var/log/apt/term.log"
            fi
        else
            # Detect distro codename for Zabbly repo line.
            local codename=""
            if command -v lsb_release >/dev/null 2>&1; then
                codename=$(lsb_release -cs 2>/dev/null)
            elif [[ -f /etc/os-release ]]; then
                codename=$(. /etc/os-release && echo "${VERSION_CODENAME:-}")
            fi

            # Zabbly publishes for a fixed set of codenames. Listed at
            # https://github.com/zabbly/incus — keep this in sync with
            # whatever's in the repo's Release file. Adding an unsupported
            # codename to sources.list.d gives a "does not have a Release
            # file" error and breaks every subsequent apt run.
            local zabbly_supported=" bookworm trixie jammy noble "

            if [[ -z "$codename" ]]; then
                warn "couldn't detect distro codename — skipping Zabbly fallback"
                warn "  install Incus manually: https://github.com/zabbly/incus"
            elif [[ "$zabbly_supported" != *" $codename "* ]]; then
                warn "incus not in default repos and codename '$codename' isn't on Zabbly's supported list ($zabbly_supported)."
                warn "  if your distro is newer than Zabbly's supported set, install Incus from your package manager once it's available."
                warn "  otherwise: https://github.com/zabbly/incus"
            else
                log "incus not in default repos — using Zabbly for codename '$codename'"
                sudo install -d -m 0755 /etc/apt/keyrings
                sudo curl -fsSL https://pkgs.zabbly.com/key.asc -o /etc/apt/keyrings/zabbly.asc \
                    || { warn "failed to fetch Zabbly key — skipping Incus install"; return 0; }
                echo "deb [signed-by=/etc/apt/keyrings/zabbly.asc] https://pkgs.zabbly.com/incus/stable $codename main" \
                    | sudo tee /etc/apt/sources.list.d/zabbly-incus-stable.list > /dev/null
                sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
                if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq incus; then
                    installed=1
                    log "container runtime: incus installed via Zabbly"
                else
                    warn "Zabbly Incus install failed — see /var/log/apt/term.log"
                fi
            fi
        fi
    elif command -v dnf >/dev/null 2>&1; then
        log "installing incus via dnf"
        sudo dnf install -y -q incus \
            && installed=1 \
            || warn "dnf install incus failed — continuing without container support"
    elif command -v pacman >/dev/null 2>&1; then
        warn "container runtime: Arch detected — install Incus manually with:"
        warn "  sudo pacman -S incus"
        warn "  (or install Docker/Podman if you prefer)"
        warn "  worker containers will be unavailable until a runtime is installed"
    elif command -v apk >/dev/null 2>&1; then
        warn "container runtime: Alpine detected — install Incus manually with:"
        warn "  sudo apk add incus"
        warn "  worker containers will be unavailable until a runtime is installed"
    else
        warn "container runtime: unrecognised package manager — install Incus or Docker manually"
        warn "  worker containers will be unavailable until a runtime is installed"
    fi

    if (( installed )); then
        if command -v incus >/dev/null 2>&1; then
            log "container runtime: incus installed successfully"
        else
            warn "incus install reported success but binary not found on PATH — check your PATH"
        fi
    fi

    if (( installed )) && command -v incus >/dev/null 2>&1; then
        log "initialising Incus with default storage + network"
        if sudo incus admin init --auto >/dev/null 2>&1; then
            log "container runtime: incus initialised"
        else
            warn "incus admin init --auto failed — you may need to configure storage manually"
            warn "  see: https://linuxcontainers.org/incus/docs/main/howto/initialize/"
        fi
    fi
}

# True when the Docker daemon is reachable (don't trust a single start call's
# exit code — WSL2/systemd quirks make that unreliable).
_docker_running() {
    sudo docker info >/dev/null 2>&1
}

# Configure Docker so it coexists with incus networking.
#
# When Docker enables IP forwarding it sets the iptables FORWARD chain's
# default policy to DROP, which severs incus bridge (incusbr0) traffic and
# kills agent-container networking. The upstream-recommended fix (Incus
# firewall docs + Docker packet-filtering docs) is Docker's own switch
# `ip-forward-no-drop`, which tells Docker to never touch that policy — far
# cleaner than patching the FORWARD chain by hand (Docker reorders/reinserts
# its own rules, so hand-added FORWARD rules are fragile). We also enable IP
# forwarding via sysctl as a belt-and-suspenders for older Docker builds that
# predate the option. This must run BEFORE Docker's daemon first starts so the
# DROP policy is never set in the first place.
_configure_docker_incus_coexistence() {
    sudo mkdir -p /etc/docker
    # Merge "ip-forward-no-drop": true into any existing daemon.json. python3 is
    # a controller dependency installed earlier, so it's always available here.
    sudo python3 - <<'PY'
import json, os
path = "/etc/docker/daemon.json"
data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f) or {}
    except Exception:
        data = {}  # malformed/empty — start clean rather than fail the install
if data.get("ip-forward-no-drop") is not True:
    data["ip-forward-no-drop"] = True
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
PY
    log "set Docker ip-forward-no-drop=true for incus coexistence (/etc/docker/daemon.json)"

    # Independently ensure IPv4 forwarding is on; if it's already enabled when
    # Docker starts, Docker won't flip the FORWARD policy even on engines that
    # lack ip-forward-no-drop.
    printf 'net.ipv4.conf.all.forwarding=1\n' | sudo tee /etc/sysctl.d/99-taos-forwarding.conf >/dev/null
    sudo sysctl -p /etc/sysctl.d/99-taos-forwarding.conf >/dev/null 2>&1 || true
}

# Docker is the engine for Store "Docker apps" (manifest install.method: docker),
# including compose-style stacks like SearXNG / Perplexica. incus runs agent
# LXCs; Docker runs Docker apps. Installed by default — set TAOS_SKIP_DOCKER=1
# to opt out (Store Docker apps then stay unavailable).
ensure_docker_for_apps() {
    if [[ "${TAOS_SKIP_DOCKER:-0}" == "1" ]]; then
        log "TAOS_SKIP_DOCKER=1 — skipping Docker (Store Docker apps will be unavailable)"
        return 0
    fi
    # macOS: the Docker Engine can't run natively (it needs a Linux VM), so the
    # server doesn't install it here — agents use the Apple Containerization
    # framework, and Docker apps need a user-provided Docker (Desktop/colima).
    if [[ "$(uname -s)" == "Darwin" ]]; then
        command -v docker >/dev/null 2>&1 \
            && log "macOS: using existing Docker ($(docker --version 2>/dev/null | head -1))" \
            || log "macOS: provide Docker (Desktop or colima) for Store Docker apps; agents use Apple Containerization"
        return 0
    fi

    local had_docker=0
    command -v docker >/dev/null 2>&1 && had_docker=1

    # Configure Docker↔incus coexistence BEFORE the daemon's first start so
    # Docker never sets the FORWARD policy to DROP. Only relevant when incus is
    # also present (it runs the agent containers).
    if command -v incus >/dev/null 2>&1; then
        _configure_docker_incus_coexistence
    fi

    # Linux, including WSL2: install the native Docker Engine + CLI in-distro.
    # We deliberately use the in-distro engine rather than Docker Desktop on
    # WSL2 so the controller is self-contained and headless; the daemon-start
    # ladder below covers WSL2 setups where systemd isn't enabled.
    if (( had_docker )); then
        log "docker present: $(docker --version 2>/dev/null | head -1)"
    else
        # Install the engine AND the Compose v2 plugin — taOS deploys Store
        # Docker apps via `docker compose`, and most distro 'docker' packages
        # (e.g. Ubuntu's docker.io) don't bundle compose, which otherwise fails
        # with "unknown command: docker compose".
        log "installing Docker Engine + Compose plugin (for Store Docker apps)"
        if command -v apt-get >/dev/null 2>&1; then
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 \
                || warn "apt install docker.io/docker-compose-v2 failed — Store Docker apps will be unavailable"
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y -q moby-engine docker-compose \
                || warn "dnf install moby-engine/docker-compose failed — Store Docker apps will be unavailable"
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -Sy --noconfirm --needed docker docker-compose \
                || warn "pacman install docker/docker-compose failed — Store Docker apps will be unavailable"
        elif command -v apk >/dev/null 2>&1; then
            sudo apk add --no-cache docker docker-cli-compose \
                || warn "apk add docker/docker-cli-compose failed — Store Docker apps will be unavailable"
        else
            warn "unrecognised package manager — install Docker + the compose plugin manually for Store Docker apps"
            return 0
        fi
    fi

    # Ensure the Compose v2 plugin (taOS deploys apps via `docker compose`).
    # This also covers the case where Docker was ALREADY installed but without
    # the plugin — the fresh-install branch above bundles it, but a pre-existing
    # Docker (the `had_docker` path) may lack it, so install it here too.
    if ! docker compose version >/dev/null 2>&1; then
        log "installing the Docker Compose v2 plugin"
        if command -v apt-get >/dev/null 2>&1; then
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2 || true
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y -q docker-compose || true
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -Sy --noconfirm --needed docker-compose || true
        elif command -v apk >/dev/null 2>&1; then
            sudo apk add --no-cache docker-cli-compose || true
        fi
        docker compose version >/dev/null 2>&1 \
            || warn "the 'docker compose' plugin isn't available — Store Docker apps need it (install docker-compose-v2 / docker-compose-plugin manually)"
    fi

    command -v docker >/dev/null 2>&1 || { warn "docker not on PATH after install — skipping daemon/group setup"; return 0; }

    # Start + enable the daemon across init systems: systemd (most distros),
    # SysV 'service' (e.g. WSL2 without systemd), then OpenRC (Alpine). Verify
    # reachability afterwards rather than trusting any single call's exit code.
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl enable --now docker >/dev/null 2>&1 || true
    fi
    if ! _docker_running && command -v service >/dev/null 2>&1; then
        sudo service docker start >/dev/null 2>&1 || true
    fi
    if ! _docker_running && command -v rc-update >/dev/null 2>&1; then
        sudo rc-update add docker default >/dev/null 2>&1 || true
        sudo service docker start >/dev/null 2>&1 || true
    fi
    _docker_running \
        && log "docker daemon is running" \
        || warn "docker installed but the daemon isn't reachable — start it manually (e.g. sudo systemctl start docker)"

    # Let the controller's user run docker without sudo.
    local duser="${SUDO_USER:-$USER}"
    if [[ -n "$duser" && "$duser" != "root" ]]; then
        sudo groupadd -f docker >/dev/null 2>&1 || true
        sudo usermod -aG docker "$duser" >/dev/null 2>&1 \
            && log "added '$duser' to the docker group (re-login for it to take effect)" \
            || warn "could not add '$duser' to the docker group"
    fi

    # Keep incus agent networking alive alongside Docker. ip-forward-no-drop
    # (set above, before first start) stops Docker setting FORWARD=DROP going
    # forward. For a Docker that was already running, restart so it re-reads the
    # config, then normalise any DROP a prior start may have left (idempotent;
    # ACCEPT is ip-forward-no-drop's intended end-state — Docker still filters
    # its own containers via the DOCKER/DOCKER-USER chains).
    if command -v incus >/dev/null 2>&1; then
        if (( had_docker )) && command -v systemctl >/dev/null 2>&1; then
            sudo systemctl restart docker >/dev/null 2>&1 || true
        fi
        if command -v iptables >/dev/null 2>&1; then
            sudo iptables -P FORWARD ACCEPT 2>/dev/null || true
        fi
    fi
}

ensure_container_runtime
ensure_docker_for_apps

# --- clone / update the repo ---------------------------------------------

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    log "cloning $REPO into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
else
    log "updating existing checkout"
    (cd "$INSTALL_DIR" && git fetch --depth 1 origin "$BRANCH" && git reset --hard "origin/$BRANCH")
fi

cd "$INSTALL_DIR"

# Now that the repo is on disk, run any accelerator-backend install
# that was deferred up front (e.g. rkllama on a Rockchip NPU host).
install_rknpu_if_pending

# --- python venv + controller deps ---------------------------------------

if [[ ! -d .venv ]]; then
    log "creating venv"
    # On distros that ship Python 3.14+ (Arch) or where libtorrent's Python
    # binding is only available as a system package (Fedora — see dnf branch
    # above), we need the venv to inherit system site-packages so `import
    # libtorrent` resolves against the OS-installed binding. PyPI does not
    # publish libtorrent wheels for 3.14 yet.
    if command -v pacman >/dev/null 2>&1 || [[ -f /etc/fedora-release ]]; then
        python3 -m venv --system-site-packages .venv
    else
        python3 -m venv .venv
    fi
fi

log "installing controller python deps into .venv (pip install -e '.[proxy]')"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e ".[proxy]"

# yt-dlp is needed for YouTube and X content ingestion (runs as subprocess)
if ! command -v yt-dlp &>/dev/null; then
    log "installing yt-dlp for YouTube/X content ingestion"
    ./.venv/bin/pip install --quiet yt-dlp || log "WARN: yt-dlp install failed — YouTube ingest will not work"
fi

log "verifying controller import"
if ! ./.venv/bin/python -c "import tinyagentos.app; tinyagentos.app.create_app()" 2>/dev/null; then
    # Run again without redirecting stderr so the error is visible
    ./.venv/bin/python -c "import tinyagentos.app; tinyagentos.app.create_app()" || \
        die "controller import verification failed — see error above"
fi
log "controller import ok"

# --- initialize data dir -------------------------------------------------

if [[ ! -d data ]]; then
    log "creating data/ directory"
    mkdir -p data
fi

if [[ -f data/config.yaml.example && ! -f data/config.yaml ]]; then
    log "copying data/config.yaml.example → data/config.yaml (first-run defaults)"
    cp data/config.yaml.example data/config.yaml
fi

# --- LiteLLM virtual-key store (Postgres) --------------------------------
#
# LiteLLM uses a Postgres-backed key store to mint per-agent virtual keys
# via /key/generate. Without it, agent deploys log a "DB not connected"
# error and fall back to a shared master key (functional, but every agent
# in this controller authenticates as the same identity). We install a
# small local Postgres and write data/.litellm_db_url so the controller
# picks it up on next start. Idempotent: re-runs leave the existing URL
# in place — bumping the password would invalidate any keys already
# minted against it.

ensure_litellm_postgres() {
    if [[ -f data/.litellm_db_url && -s data/.litellm_db_url ]]; then
        log "litellm postgres: data/.litellm_db_url present — skipping setup"
        return 0
    fi

    if ! command -v sudo >/dev/null 2>&1 && [[ "$(id -u)" != "0" ]]; then
        warn "litellm postgres: sudo not available and not running as root — skipping"
        warn "  per-agent virtual keys will not work; deployer will use the shared master key"
        return 0
    fi

    if ! command -v psql >/dev/null 2>&1; then
        log "installing postgresql for LiteLLM virtual keys"
        if command -v apt-get >/dev/null 2>&1; then
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql \
                || { warn "apt install postgresql failed — skipping virtual key setup"; return 0; }
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y -q postgresql postgresql-server \
                || { warn "dnf install postgresql failed — skipping virtual key setup"; return 0; }
            # Fedora/RHEL ship Postgres uninitialised — set up the data
            # cluster before systemd can start it.
            if [[ ! -d /var/lib/pgsql/data/base ]]; then
                sudo postgresql-setup --initdb >/dev/null 2>&1 || true
            fi
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -S --noconfirm postgresql \
                || { warn "pacman -S postgresql failed — skipping virtual key setup"; return 0; }
            # Arch ships postgresql binaries without an initialised data
            # directory; systemd refuses to start until initdb has run.
            if [[ ! -d /var/lib/postgres/data/base ]]; then
                sudo mkdir -p /var/lib/postgres/data
                sudo chown postgres:postgres /var/lib/postgres/data
                sudo -u postgres initdb --locale=C.UTF-8 --encoding=UTF8 \
                    -D /var/lib/postgres/data >/dev/null 2>&1 || true
            fi
        else
            warn "litellm postgres: unrecognised package manager — install postgresql manually then re-run"
            return 0
        fi
    fi

    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl enable --now postgresql >/dev/null 2>&1 \
            || { warn "litellm postgres: could not start postgresql service — skipping"; return 0; }
    fi

    # Wait for Postgres to accept connections (cold start can take a few seconds).
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sudo -u postgres psql -tAc 'SELECT 1' >/dev/null 2>&1 && break
        sleep 1
    done

    local pw
    pw=$(openssl rand -hex 24 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)
    if [[ -z "$pw" ]]; then
        warn "litellm postgres: could not generate password — skipping"
        return 0
    fi

    # Pipe the password via stdin instead of -c "...PASSWORD '...'" so it
    # never appears in /proc/<pid>/cmdline. Quoting nuance: psql's :'var'
    # interpolation produces a properly-escaped SQL string literal.
    local role_sql
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='litellm'" 2>/dev/null | grep -q 1; then
        role_sql="CREATE ROLE litellm WITH LOGIN PASSWORD :'pw';"
    else
        role_sql="ALTER ROLE litellm WITH LOGIN PASSWORD :'pw';"
    fi
    if ! printf '%s\n' "$role_sql" \
            | sudo -u postgres psql -v ON_ERROR_STOP=1 -v "pw=${pw}" >/dev/null 2>&1; then
        warn "litellm postgres: role create/alter failed — skipping"
        return 0
    fi

    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='litellm'" 2>/dev/null | grep -q 1; then
        printf 'CREATE DATABASE litellm OWNER litellm;\n' \
            | sudo -u postgres psql -v ON_ERROR_STOP=1 >/dev/null 2>&1 \
            || { warn "litellm postgres: CREATE DATABASE failed — skipping"; return 0; }
    fi

    # Subshell scopes umask so we don't leak 077 into the rest of the
    # install script (the SPA build below relies on the calling shell's
    # default umask for npm-managed files). chmod is belt-and-braces.
    ( umask 077 && \
        printf 'postgresql://litellm:%s@127.0.0.1:5432/litellm\n' "$pw" > data/.litellm_db_url )
    chmod 600 data/.litellm_db_url
    log "litellm postgres: data/.litellm_db_url written — virtual keys enabled"
}

ensure_litellm_postgres

# --- desktop SPA bundle --------------------------------------------------
# Build the frontend unconditionally on every install / upgrade. The bundle
# is not committed to git (static/desktop/ is gitignored) so this is the
# only step that produces it. Skipping or making this conditional would
# leave new installs with no UI. ~50s on a Pi; essentially free on a laptop.

if command -v npm >/dev/null 2>&1; then
    log "building desktop SPA (cd desktop && npm install && npm run build)"
    (cd "$INSTALL_DIR/desktop" && npm install --silent && npm run build) \
        || die "desktop SPA build failed — see output above"
    log "desktop bundle built into static/desktop/"
else
    warn "npm not found on PATH — desktop UI bundle was not built"
    warn "  install Node.js (>=22), then run: cd desktop && npm install && npm run build"
    warn "  see: https://nodejs.org/en/download"
fi

# --- qmd.service install -------------------------------------------------

have_root_or_sudo() {
    if [[ "$(id -u)" = "0" ]]; then
        return 0
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        return 0
    fi
    return 1
}

if [[ -z "${TAOS_SKIP_QMD:-}" ]]; then
    log "installing qmd service"

    if ! command -v qmd >/dev/null 2>&1; then
        log "qmd not found on PATH — installing via npm"
        if command -v npm >/dev/null 2>&1; then
            # Run as root so npm can write to the system global prefix
            # (/usr/lib/node_modules on Debian).  HOME=/root ensures npm
            # uses root's own cache dir rather than the calling user's
            # ~/.npm directory (which root can't write to).
            #
            # --unsafe-perm: required on npm >= 10.  When npm runs as root and
            # the install dir has non-root ownership at any point during tar
            # extraction, npm drops privileges to `nobody` to run lifecycle
            # scripts.  `nobody` typically has no usable PATH/shell setup, so
            # better-sqlite3's postinstall fails with `spawn sh ENOENT`
            # (errno -2).  --unsafe-perm keeps npm running as root throughout,
            # which is the historical behaviour and the only thing that works
            # for native-binding packages on a system-global install.
            # Pre-clean a partial qmd install dir before we try again.
            # If a prior run failed mid-extraction, the leftover directory
            # makes npm's tar extractor stumble on a second attempt with
            # ENOENT errors that look like a fresh failure but are really
            # just unfinished cleanup.
            npm_prefix=$(npm config get prefix 2>/dev/null || echo "/usr")
            partial_qmd="${npm_prefix}/lib/node_modules/@jaylfc/qmd"
            if [[ -d "$partial_qmd" ]] && ! command -v qmd >/dev/null 2>&1; then
                log "removing partial qmd install at $partial_qmd before retry"
                sudo rm -rf "$partial_qmd"
            fi

            # Capture npm stderr so we can diagnose the failure class.
            # The TAR_ENTRY_ERROR / "spawn sh ENOENT" combo is the canonical
            # node-llama-cpp tar-extraction failure (issue #310). When we
            # see it, point the user at the partial-install path and the log
            # rather than dumping hundreds of lines of npm noise to stderr.
            # Pin qmd to a specific npm version to prevent surprise
            # version bumps on fresh installs.  The npm package is
            # pre-built (dist/ is not committed to the source repo),
            # so installing from a git SHA requires a TypeScript
            # build step — use the npm registry instead.
            # To update: verify the new version against taOS, then
            # bump the pinned version here.
            qmd_install_log=$(mktemp /tmp/taos-qmd-install.XXXXXX.log)
            log "npm install -g @jaylfc/qmd@2.1.1 (log: $qmd_install_log)"
            if ! sudo HOME=/root npm install -g --unsafe-perm "@jaylfc/qmd@2.1.1" >"$qmd_install_log" 2>&1; then
                if grep -q "TAR_ENTRY_ERROR" "$qmd_install_log" \
                   && grep -q "spawn sh" "$qmd_install_log"; then
                    warn "npm install of qmd hit the node-llama-cpp tar-extraction"
                    warn "failure mode (issue #310). Recovery:"
                    warn "  1. sudo rm -rf $partial_qmd"
                    warn "  2. Re-run install-server.sh"
                    warn "  3. If it still fails, share $qmd_install_log on issue #2"
                    warn "Do NOT run 'npm install -g npm@<newer>' as recovery — it can"
                    warn "hit the same failure mode against npm's own tarball and leave"
                    warn "your npm in a broken state requiring a full nodejs reinstall."
                fi
                tail -20 "$qmd_install_log" >&2
                die "npm install of qmd failed — see $qmd_install_log"
            fi
            rm -f "$qmd_install_log"
            # Confirm the binary is reachable before we proceed.
            if ! command -v qmd >/dev/null 2>&1; then
                die "qmd binary not found on PATH after npm install — check npm global prefix and PATH"
            fi
        else
            warn "npm not found — cannot install qmd; skipping qmd.service install"
            TAOS_SKIP_QMD=1
        fi
    else
        log "qmd already installed at $(command -v qmd) — skipping npm install"
    fi

    if [[ -z "${TAOS_SKIP_QMD:-}" ]]; then
        if have_root_or_sudo; then
            local_sudo=""
            if [[ "$(id -u)" != "0" ]]; then
                local_sudo="sudo"
            fi
            if [[ -f scripts/systemd/qmd.service ]]; then
                log "installing /etc/systemd/system/qmd.service"
                # The unit ships with __TAOS_USER__ / __TAOS_GROUP__ /
                # __TAOS_QMD_BIN__ placeholders. Substitute real values at
                # install time. User/group: prefer invoking sudo user so the
                # service runs as them; fall back to 'root' if invoked as
                # root directly. qmd binary path: discover via PATH (Ubuntu
                # puts it at /usr/bin/qmd via dpkg's npm prefix; Fedora's
                # npm uses /usr/local prefix and lands at /usr/local/bin/qmd).
                taos_user="${SUDO_USER:-root}"
                taos_group=$(id -gn "$taos_user" 2>/dev/null || echo "$taos_user")
                taos_qmd_bin=$(command -v qmd 2>/dev/null)
                if [[ -z "$taos_qmd_bin" ]]; then
                    # Fallback hunt — npm root -g + /bin/qmd, then common paths
                    for cand in "$(npm root -g 2>/dev/null)/@jaylfc/qmd/bin/qmd" \
                                /usr/local/bin/qmd /usr/bin/qmd; do
                        if [[ -x "$cand" ]]; then
                            taos_qmd_bin="$cand"
                            break
                        fi
                    done
                fi
                if [[ -z "$taos_qmd_bin" ]]; then
                    die "qmd binary not found after npm install — cannot render qmd.service"
                fi
                log "qmd binary at $taos_qmd_bin"
                $local_sudo sed \
                    -e "s|__TAOS_USER__|${taos_user}|g" \
                    -e "s|__TAOS_GROUP__|${taos_group}|g" \
                    -e "s|__TAOS_QMD_BIN__|${taos_qmd_bin}|g" \
                    scripts/systemd/qmd.service > /tmp/qmd.service.rendered
                $local_sudo install -m 0644 /tmp/qmd.service.rendered /etc/systemd/system/qmd.service
                $local_sudo rm -f /tmp/qmd.service.rendered
                $local_sudo systemctl daemon-reload
                $local_sudo systemctl enable --now qmd.service
                log "waiting for qmd to become ready on port $TAOS_QMD_PORT (up to 60 s)..."
                qmd_tries=0
                while [[ $qmd_tries -lt 60 ]]; do
                    if curl -sf "http://localhost:$TAOS_QMD_PORT/health" >/dev/null 2>&1; then
                        log "qmd is up"
                        break
                    fi
                    sleep 1
                    qmd_tries=$((qmd_tries + 1))
                done
                if [[ $qmd_tries -ge 60 ]]; then
                    warn "qmd did not respond within 60 seconds — it may still be starting"
                    warn "check: systemctl status qmd"
                    if command -v journalctl >/dev/null 2>&1; then
                        warn "last 10 lines of qmd journal:"
                        journalctl -u qmd --no-pager -n 10 2>/dev/null || true
                    fi
                fi
            else
                warn "scripts/systemd/qmd.service not found in repo — skipping qmd.service install"
            fi
        else
            warn "no root/sudo available — skipping qmd.service install (system unit requires root)"
            warn "  to install later: sudo cp scripts/systemd/qmd.service /etc/systemd/system/qmd.service && sudo systemctl enable --now qmd"
        fi
    fi
else
    log "TAOS_SKIP_QMD is set — skipping qmd.service install"
fi

# --- tinyagentos.service install -----------------------------------------

install_linux_systemd_system() {
    local unit="/etc/systemd/system/tinyagentos.service"
    local sudo_cmd=""
    if [[ "$(id -u)" != "0" ]]; then
        sudo_cmd="sudo"
    fi

    # Install graceful-stop script
    $sudo_cmd install -m 0755 "$INSTALL_DIR/scripts/taos-graceful-stop.sh" /usr/local/bin/taos-graceful-stop
    log "installed /usr/local/bin/taos-graceful-stop"

    # Stamp the template from the repo, substituting install-time variables.
    sed \
        -e "s|TAOS_USER|$USER|g" \
        -e "s|TAOS_GROUP|$(id -gn)|g" \
        -e "s|TAOS_INSTALL_DIR|$INSTALL_DIR|g" \
        -e "s|TAOS_PYTHON|$INSTALL_DIR/.venv/bin/python|g" \
        -e "s|TAOS_PORT|$TAOS_PORT|g" \
        -e "s|TAOS_STOP_SCRIPT|/usr/local/bin/taos-graceful-stop|g" \
        "$INSTALL_DIR/scripts/systemd/tinyagentos.service" \
        | $sudo_cmd tee "$unit" > /dev/null
    log "installed $unit (system unit, runs as $USER)"

    # Install pre-shutdown hook
    if [[ -f "$INSTALL_DIR/systemd/taos-pre-shutdown.service" ]]; then
        $sudo_cmd cp "$INSTALL_DIR/systemd/taos-pre-shutdown.service" /etc/systemd/system/taos-pre-shutdown.service
        log "installed /etc/systemd/system/taos-pre-shutdown.service"
    fi

    $sudo_cmd systemctl daemon-reload
    $sudo_cmd systemctl enable --now tinyagentos
    if [[ -f /etc/systemd/system/taos-pre-shutdown.service ]]; then
        $sudo_cmd systemctl enable taos-pre-shutdown.service
    fi
    log "controller running as system service"
    log "check: systemctl status tinyagentos"
    log "logs:  journalctl -u tinyagentos -f"
}

install_linux_systemd_user() {
    local unit_dir="$HOME/.config/systemd/user"
    local unit="$unit_dir/tinyagentos.service"
    mkdir -p "$unit_dir"
    # Install graceful-stop script to user-local bin if possible
    mkdir -p "$HOME/.local/bin"
    install -m 0755 "$INSTALL_DIR/scripts/taos-graceful-stop.sh" "$HOME/.local/bin/taos-graceful-stop"

    # User unit: no User=/Group= (inherits the running user), no ExecStartPre
    # for debugfs (that needs root). ExecReload/Restart=always still apply.
    sed \
        -e "s|TAOS_USER|$USER|g" \
        -e "s|TAOS_GROUP|$(id -gn)|g" \
        -e "s|TAOS_INSTALL_DIR|$INSTALL_DIR|g" \
        -e "s|TAOS_PYTHON|$INSTALL_DIR/.venv/bin/python|g" \
        -e "s|TAOS_PORT|$TAOS_PORT|g" \
        -e "s|TAOS_STOP_SCRIPT|$HOME/.local/bin/taos-graceful-stop|g" \
        -e "/^User=/d" \
        -e "/^Group=/d" \
        -e "s|WantedBy=multi-user.target|WantedBy=default.target|g" \
        -e "/ExecStartPre/,/|| true'$/d" \
        "$INSTALL_DIR/scripts/systemd/tinyagentos.service" \
        > "$unit"
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
        warn "to start manually: systemctl --user daemon-reload && systemctl --user enable --now tinyagentos"
        return 0
    fi
    systemctl --user enable --now tinyagentos
    log "controller running as user systemd service"
    log "check: systemctl --user status tinyagentos"
    log "logs:  journalctl --user -u tinyagentos -f"
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
    local plist="$plist_dir/com.tinyagentos.controller.plist"
    mkdir -p "$plist_dir"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.tinyagentos.controller</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/.venv/bin/python</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>tinyagentos.app:create_app</string>
        <string>--factory</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>$TAOS_PORT</string>
    </array>
    <key>WorkingDirectory</key><string>$INSTALL_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$INSTALL_DIR/controller.log</string>
    <key>StandardErrorPath</key><string>$INSTALL_DIR/controller.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
</dict>
</plist>
EOF
    log "installed $plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    log "controller running as launchd agent"
    log "check: launchctl list | grep tinyagentos"
    log "logs:  tail -f $INSTALL_DIR/controller.log"
}

if [[ "$SERVICE_MODE" == "skip" ]]; then
    log "TAOS_SERVICE=skip — not installing a service unit"
    log "run manually: cd $INSTALL_DIR && ./.venv/bin/python -m uvicorn tinyagentos.app:create_app --factory --host 0.0.0.0 --port $TAOS_PORT"
else
    case "$os_name" in
        Linux)  install_linux_systemd ;;
        Darwin) install_macos_launchd ;;
    esac
fi

# --- wait for controller to come up -------------------------------------

if [[ "$SERVICE_MODE" != "skip" ]]; then
    # 120s ceiling: cold-boot on a Pi 5 / Orange Pi 5 lands around 55-65s
    # (issue #337) so 60s was racing the actual ready state and printing
    # a false "controller did not respond" warning even on successful
    # installs. Doubling the cap keeps the safety net while removing the
    # false alarm. Loop continues to early-exit the moment /api/cluster/workers
    # answers, so this is just a higher ceiling, not slower steady state.
    log "waiting for controller to be ready on port $TAOS_PORT (up to 120 s)..."
    ctrl_tries=0
    ctrl_up=0
    while [[ $ctrl_tries -lt 120 ]]; do
        if curl -sf "http://localhost:$TAOS_PORT/api/cluster/workers" >/dev/null 2>&1; then
            ctrl_up=1
            break
        fi
        sleep 1
        ctrl_tries=$((ctrl_tries + 1))
    done

    if [[ $ctrl_up -eq 0 ]]; then
        warn "controller did not respond within 120 seconds"
        if command -v journalctl >/dev/null 2>&1; then
            warn "latest journal output:"
            journalctl -u tinyagentos --no-pager -n 30 2>/dev/null || true
        fi
        die "controller failed to start — check the journal above and fix before continuing"
    fi
    log "controller is up"
fi

# --- success summary -----------------------------------------------------

host_ip=""
if command -v hostname >/dev/null 2>&1; then
    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || host_ip="$(hostname)"
fi
[[ -z "$host_ip" ]] && host_ip="<host-ip>"

log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "  TinyAgentOS controller install complete"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log ""
log "  Web UI      : http://$host_ip:$TAOS_PORT"
log "  Localhost   : http://localhost:$TAOS_PORT"
log "  Install dir : $INSTALL_DIR"
log ""
if have_root_or_sudo; then
    log "  Manage services:"
    log "    systemctl status tinyagentos qmd"
    log "    journalctl -u tinyagentos -f"
    log "    journalctl -u qmd -f"
    log ""
    log "  Upgrade:"
    log "    cd $INSTALL_DIR && git pull && \\"
    log "      find . -name __pycache__ -type d -exec rm -rf {} + && \\"
    log "      sudo systemctl restart tinyagentos qmd"
else
    log "  Manage services:"
    log "    systemctl --user status tinyagentos"
    log "    journalctl --user -u tinyagentos -f"
    log ""
    log "  Upgrade:"
    log "    cd $INSTALL_DIR && git pull && \\"
    log "      find . -name __pycache__ -type d -exec rm -rf {} + && \\"
    log "      systemctl --user restart tinyagentos"
fi
log ""
log "  Now install workers on other machines with:"
log "    curl -fsSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-worker.sh | sudo bash -s -- http://$host_ip:$TAOS_PORT"
log ""
