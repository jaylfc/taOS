#!/bin/bash
# taOS Kiosk Mode Setup
# Boots the Pi directly into a fullscreen Chromium pointing at the taOS desktop.
# Run: sudo bash scripts/kiosk-setup.sh
#
# Requires: chromium-browser (or chromium), a display server (cage/wlroots recommended)
# Works on: Armbian, Raspberry Pi OS, Debian, Ubuntu

set -e

TAOS_URL="${1:-http://localhost:6969/desktop/}"
TAOS_USER="${SUDO_USER:-$(whoami)}"

echo "=== taOS Kiosk Mode Setup ==="
echo "URL: $TAOS_URL"
echo "User: $TAOS_USER"

# Refresh the package index once, unconditionally: a later install must never
# fail on a stale index just because an earlier package happened to be present.
apt-get update -qq

# Install cage (minimal Wayland compositor) if not present
if ! command -v cage &>/dev/null; then
    echo "Installing cage (Wayland kiosk compositor)..."
    apt-get install -y -qq cage || {
        echo "cage not in repos — trying weston as fallback..."
        apt-get install -y -qq weston
    }
fi

# Install seatd (seat driver) for kiosk mode authentication
if ! command -v seatd &>/dev/null; then
    echo "Installing seatd (seat driver)..."
    apt-get install -y -qq seatd || {
        echo "seatd not in repos — skipping seat configuration" >&2
    }
fi

# Configure the seat whenever seatd is available, including when it was already
# installed before this run.
if command -v seatd &>/dev/null; then
    echo "Enabling seatd..."
    if ! systemctl enable --now seatd; then
        echo "Failed to enable seatd.service" >&2
        exit 1
    fi
    if ! getent group seat >/dev/null; then
        echo "Creating missing seat group..."
        if ! groupadd -r seat; then
            echo "Failed to create seat group" >&2
            exit 1
        fi
    fi
    echo "Adding user $TAOS_USER to seat group..."
    if ! usermod -a -G seat "$TAOS_USER"; then
        echo "Failed to add $TAOS_USER to the seat group" >&2
        exit 1
    fi
fi

# Install chromium if not present
BROWSER=""
for b in chromium-browser chromium google-chrome-stable; do
    if command -v "$b" &>/dev/null; then
        BROWSER="$b"
        break
    fi
done
if [ -z "$BROWSER" ]; then
    echo "Installing chromium..."
    apt-get install -y -qq chromium-browser 2>/dev/null || apt-get install -y -qq chromium
    BROWSER="chromium-browser"
fi
echo "Browser: $BROWSER"

# Create the kiosk systemd service
cat > /etc/systemd/system/taos-kiosk.service << EOF
[Unit]
Description=taOS Kiosk Mode
After=tinyagentos.service network-online.target seatd.service
Wants=tinyagentos.service seatd.service

[Service]
Type=simple
User=$TAOS_USER
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u "$TAOS_USER")
Environment=WLR_LIBINPUT_NO_DEVICES=1

# Use cage as the Wayland compositor (minimal, no desktop)
ExecStart=/usr/bin/cage -- $BROWSER \\
    --kiosk \\
    --no-first-run \\
    --disable-translate \\
    --disable-infobars \\
    --disable-session-crashed-bubble \\
    --disable-component-update \\
    --noerrdialogs \\
    --enable-features=OverlayScrollbar \\
    --ozone-platform=wayland \\
    $TAOS_URL

Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

echo "Created: /etc/systemd/system/taos-kiosk.service"

# Create a convenience script to toggle kiosk mode
cat > /usr/local/bin/taos-kiosk << 'SCRIPT'
#!/bin/bash
case "${1:-status}" in
    start)
        sudo systemctl start taos-kiosk
        echo "Kiosk started"
        ;;
    stop)
        sudo systemctl stop taos-kiosk
        echo "Kiosk stopped"
        ;;
    enable)
        sudo systemctl enable taos-kiosk
        sudo systemctl set-default graphical.target
        echo "Kiosk enabled on boot"
        ;;
    disable)
        sudo systemctl disable taos-kiosk
        echo "Kiosk disabled"
        ;;
    status)
        systemctl is-active taos-kiosk && echo "Kiosk: running" || echo "Kiosk: stopped"
        systemctl is-enabled taos-kiosk 2>/dev/null && echo "Boot: enabled" || echo "Boot: disabled"
        ;;
    *)
        echo "Usage: taos-kiosk {start|stop|enable|disable|status}"
        ;;
esac
SCRIPT
chmod +x /usr/local/bin/taos-kiosk

echo ""
echo "=== Setup complete ==="

# Install ble extra into the existing venv so fresh handset installs get it
# This ensures that on a fresh handset where taos-kiosk.service doesn't exist yet,
# the ble extra is still installed when install-server.sh's pip step runs.

# Determine taOS installation directory (honour TAOS_INSTALL_DIR, then the
# running service's WorkingDirectory, then /opt, then $HOME).
TAOS_DIR="${TAOS_INSTALL_DIR:-}"
if [ -z "$TAOS_DIR" ]; then
    if command -v systemctl >/dev/null 2>&1; then
        _wd="$(systemctl show tinyagentos -p WorkingDirectory --value 2>/dev/null || true)"
        if [ -n "$_wd" ]; then
            TAOS_DIR="$_wd"
        fi
    fi
fi
if [ -z "$TAOS_DIR" ]; then
    if [ -d /opt/tinyagentos ]; then
        TAOS_DIR="/opt/tinyagentos"
    else
        TAOS_DIR="$HOME/tinyagentos"
    fi
fi

# Install ble extra only when not explicitly disabled via TAOS_EXTRAS_BLE=0/1
if [ "${TAOS_EXTRAS_BLE:-1}" != "0" ] && [ "${TAOS_EXTRAS_BLE:-1}" != "false" ]; then
    if [ -f "$TAOS_DIR/.venv/bin/pip" ]; then
        echo "Installing ble extra into taOS venv at $TAOS_DIR/.venv"
        # Get the venv owner (the service user) so pip doesn't hit permission errors
        venv_owner=""
        if command -v systemctl >/dev/null 2>&1; then
            venv_owner="$(systemctl show tinyagentos -p User --value 2>/dev/null || true)"
        fi
        if [[ -z "$venv_owner" ]] && command -v stat >/dev/null 2>&1; then
            venv_owner="$(stat -c %U "$TAOS_DIR/.venv" 2>/dev/null)"
        fi
        
        if [[ -n "$venv_owner" ]] && [[ "$venv_owner" != "$(whoami)" ]]; then
            sudo -u "$venv_owner" "$TAOS_DIR/.venv/bin/pip" install --quiet -e "$TAOS_DIR[ble]"
        else
            "$TAOS_DIR/.venv/bin/pip" install --quiet -e "$TAOS_DIR[ble]"
        fi
    else
        echo "Warning: taOS venv not found at $TAOS_DIR/.venv, ble extra not installed"
    fi
else
    echo "TAOS_EXTRAS_BLE=${TAOS_EXTRAS_BLE:-0} — skipping ble extra install"
fi

echo ""
echo "Commands:"
echo "  taos-kiosk start    — launch kiosk now"
echo "  taos-kiosk stop     — exit kiosk"
echo "  taos-kiosk enable   — auto-start on boot"
echo "  taos-kiosk disable  — don't auto-start"
echo "  taos-kiosk status   — check state"
echo ""
echo "To enable kiosk on boot: taos-kiosk enable"