#!/bin/bash
# TinyAgentOS installer — for users on stock Armbian/Debian who want to install without a pre-built image.
# Usage: curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/install.sh | bash
#
# Hailo-10H NPU backend (Raspberry Pi 5 + AI HAT+2):
#     TAOS_HAILO_SETUP    set to 1/true to auto-run scripts/install-hailo.sh when a Hailo-10H is detected
#     TAOS_FORCE_HAILO    set to 1/true to force the Hailo-10H branch on boxes without a /dev/hailo0 node

set -e

INSTALL_DIR="/opt/tinyagentos"
DATA_DIR="/opt/tinyagentos/data"
CATALOG_REPO="https://github.com/jaylfc/tinyagentos.git"

echo "======================================"
echo "  TinyAgentOS Installer"
echo "======================================"
echo ""

# Check for root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash install.sh"
    exit 1
fi

# Detect architecture
ARCH=$(uname -m)
echo "Detected architecture: $ARCH"

# Install system dependencies
echo ""
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nodejs npm git curl avahi-daemon

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python version: $PYTHON_VERSION"

# Clone or update TinyAgentOS
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating TinyAgentOS..."
    cd "$INSTALL_DIR"
    # Robust update — handles non-master branch, dirty tree, diverged history.
    # Every destructive step preceded by a recovery tag so nothing is lost silently.
    if ! git fetch origin master; then
        echo "WARNING: git fetch failed — cannot reach remote. Skipping update to avoid destroying local state."
    else
        _taos_ts=$(date +%s)
        _taos_branch=$(git rev-parse --abbrev-ref HEAD)

        # If on a non-master branch, tag and switch
        if [ "$_taos_branch" != "master" ]; then
            _taos_safe_branch=$(echo "$_taos_branch" | tr '/' '-')
            _taos_branch_tag="taos-pre-update-${_taos_safe_branch}-${_taos_ts}"
            git tag "$_taos_branch_tag" HEAD
            echo "Tagged non-master branch tip as: $_taos_branch_tag"
            git checkout master
        fi

        # Stash any dirty working tree (including untracked files)
        _taos_stashed=0
        if [ -n "$(git status --porcelain -u)" ]; then
            _taos_stash_msg="taos-update-${_taos_ts}"
            git stash push -u -m "$_taos_stash_msg"
            _taos_stashed=1
        fi

        # Attempt fast-forward; on failure tag and hard-reset
        if ! git merge --ff-only origin/master; then
            _taos_short=$(git rev-parse --short HEAD)
            _taos_recovery_tag="taos-pre-update-${_taos_short}-${_taos_ts}"
            git tag "$_taos_recovery_tag" HEAD
            echo "Diverged history detected. Local commits saved as tag: $_taos_recovery_tag"
            git reset --hard origin/master
        fi

        # Restore stash if we saved one
        if [ "$_taos_stashed" = "1" ]; then
            if ! git stash pop; then
                echo "WARNING: stash restore had conflicts. Your local changes are preserved in stash."
                echo "  Use 'git stash list' to find them (look for message: $_taos_stash_msg)."
                echo "  Apply manually with 'git stash pop' when ready."
            fi
        fi
    fi
else
    echo "Installing TinyAgentOS to $INSTALL_DIR..."
    git clone "$CATALOG_REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Create virtual environment
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

# Install TinyAgentOS
echo "Installing Python dependencies..."
"$INSTALL_DIR/venv/bin/pip" install -e "." -q

# Create data directory
mkdir -p "$DATA_DIR"

# Create default config if it doesn't exist
if [ ! -f "$DATA_DIR/config.yaml" ]; then
    echo "Creating default config..."
    cp "$INSTALL_DIR/data/config.yaml" "$DATA_DIR/config.yaml"
fi

# Run bridge subnet collision probe before touching the firewall.
if command -v incus >/dev/null 2>&1; then
    echo ""
    echo "Checking incusbr0 subnet for collisions..."
    chmod +x "$INSTALL_DIR/scripts/incus-bridge-probe.sh"
    bash "$INSTALL_DIR/scripts/incus-bridge-probe.sh"
fi

# Install host firewall scripts (allow incus bridge through docker's FORWARD DROP)
echo ""
echo "Installing host firewall scripts..."
mkdir -p /opt/tinyagentos/scripts
for fw_script in host-firewall-up.sh host-firewall-down.sh incus-bridge-probe.sh; do
    cp "$INSTALL_DIR/scripts/$fw_script" /opt/tinyagentos/scripts/
    chmod +x /opt/tinyagentos/scripts/$fw_script
done
for fw_unit in tinyagentos-host-firewall.service tinyagentos-host-firewall.path tinyagentos-host-firewall.timer; do
    cp "$INSTALL_DIR/systemd/$fw_unit" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now tinyagentos-host-firewall.service
systemctl enable --now tinyagentos-host-firewall.path
systemctl enable --now tinyagentos-host-firewall.timer
echo "Host firewall service status: $(systemctl is-active tinyagentos-host-firewall.service)"

echo ""
echo "=== FS snapshot backstop (Layer 3 of recycle-bin) ==="
if [ -f "$INSTALL_DIR/scripts/fs-snapshot-install.sh" ]; then
  bash "$INSTALL_DIR/scripts/fs-snapshot-install.sh" || echo "fs-snapshot-install: non-fatal error; continuing install"
fi

# Install disk quota scanner script and systemd units
echo ""
echo "Installing disk quota scanner..."
cp "$INSTALL_DIR/scripts/disk-quota-scan.sh" /opt/tinyagentos/scripts/
chmod +x /opt/tinyagentos/scripts/disk-quota-scan.sh
for dq_unit in tinyagentos-disk-quota.service tinyagentos-disk-quota.timer; do
    cp "$INSTALL_DIR/systemd/$dq_unit" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now tinyagentos-disk-quota.timer
echo "Disk quota timer status: $(systemctl is-active tinyagentos-disk-quota.timer)"

# Migrate legacy trace paths (pre-pivot agent-home/*/.taos/trace → trace/{slug}/)
if [ -x "$INSTALL_DIR/scripts/migrate-trace-paths.sh" ]; then
  bash "$INSTALL_DIR/scripts/migrate-trace-paths.sh" "$DATA_DIR" || echo "migrate-trace-paths: non-fatal error; continuing install"
fi

# Install systemd service
echo "Installing systemd service..."
cat > /etc/systemd/system/tinyagentos.service << EOF
[Unit]
Description=TinyAgentOS Web GUI
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python -m uvicorn tinyagentos.app:create_app --factory --host 0.0.0.0 --port 6969
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tinyagentos

# Detect if rkllama is available (Rockchip NPU)
if [ -e "/dev/rknpu" ]; then
    echo ""
    echo "Rockchip NPU detected! Consider installing rkllama for NPU-accelerated inference."
    echo "  pip install rkllama"
fi

# Detect if a Hailo-10H NPU is available (Raspberry Pi 5 + AI HAT+2).
# Only the 10H runs LLMs; an 8/8L-class device is vision-only and is skipped.
if [ -e "/dev/hailo0" ]; then
    _hailo_10h=0
    if [ "${TAOS_FORCE_HAILO:-}" = "1" ] || [ "${TAOS_FORCE_HAILO:-}" = "true" ]; then
        _hailo_10h=1
    elif command -v lspci >/dev/null 2>&1 && lspci -d 1e60: 2>/dev/null | grep -Eiq '10h|hailo-10'; then
        _hailo_10h=1
    elif command -v hailortcli >/dev/null 2>&1 && hailortcli fw-control identify 2>/dev/null | grep -Eiq '10h|hailo-10'; then
        _hailo_10h=1
    fi
    if [ "$_hailo_10h" = "1" ]; then
        echo ""
        echo "Hailo-10H NPU detected! Consider installing hailo-ollama for NPU-accelerated LLM inference."
        echo "  sudo bash scripts/install-hailo.sh"
        if [ "${TAOS_HAILO_SETUP:-}" = "1" ] || [ "${TAOS_HAILO_SETUP:-}" = "true" ]; then
            echo "TAOS_HAILO_SETUP=1 - chaining into scripts/install-hailo.sh"
            bash "$INSTALL_DIR/scripts/install-hailo.sh" --yes \
                || echo "install-hailo.sh failed - continuing install anyway"
        fi
    else
        echo ""
        echo "Hailo device detected but not a 10H (vision-class: no LLM support). Skipping hailo-ollama install."
    fi
fi

# Start TinyAgentOS
echo ""
echo "Starting TinyAgentOS..."
systemctl start tinyagentos

# Connectivity smoke test: launch an ephemeral container and probe two key
# endpoints.  Failure warns but does not abort — the user can investigate
# and restart the firewall service manually.
if command -v incus >/dev/null 2>&1; then
    echo ""
    echo "=== connectivity smoke test ==="
    incus launch images:debian/bookworm taos-netcheck --ephemeral 2>&1 | tail -3
    sleep 2
    if incus exec taos-netcheck -- timeout 10 curl -sI https://github.com >/dev/null 2>&1 \
      && incus exec taos-netcheck -- timeout 10 curl -sI https://registry.npmjs.org >/dev/null 2>&1; then
        echo "[ok] containers can reach github.com and registry.npmjs.org"
    else
        echo "[WARN] connectivity test failed from a fresh container."
        echo "  Check: iptables -L DOCKER-USER -v -n"
        echo "  Try:   sudo systemctl restart tinyagentos-host-firewall.service"
    fi
    incus delete taos-netcheck --force 2>/dev/null || true
fi

# Get IP address
IP=$(hostname -I | awk '{print $1}')

echo ""
echo "======================================"
echo "  TinyAgentOS installed successfully!"
echo "======================================"
echo ""
echo "  Open: http://$IP:6969"
echo ""
echo "  Service: systemctl status tinyagentos"
echo "  Logs:    journalctl -u tinyagentos -f"
echo ""
