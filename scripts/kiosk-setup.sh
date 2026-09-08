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
echo ""
echo "Commands:"
echo "  taos-kiosk start    — launch kiosk now"
echo "  taos-kiosk stop     — exit kiosk"
echo "  taos-kiosk enable   — auto-start on boot"
echo "  taos-kiosk disable  — don't auto-start"
echo "  taos-kiosk status   — check state"
echo ""
echo "To enable kiosk on boot: taos-kiosk enable"