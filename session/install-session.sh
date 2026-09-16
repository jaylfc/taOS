#!/bin/sh
# Close the black gap between the boot splash and the kiosk.
#
# Run as root ON the device. Idempotent.
#
# The splash now works (see ~/.taos-team/SPLASH-*.md), but the user still saw
# splash -> ~16s of BLACK -> kiosk. That gap is sway painting nothing: swaybg
# was never installed, so there was no background between plymouth quitting and
# chromium's first frame. Giving sway the splash's own final frame as its
# background makes the handover invisible.
#
# This does NOT hold plymouth open past its quit, deliberately. Keeping
# plymouthd alive to cover the gap means it holds DRM master while sway wants
# it, and if the handover script ever failed the splash would cover a working
# kiosk with no way for the user to get past it. A background image has no such
# failure mode: worst case it is missing and we are back to today's black.
set -eu

HERE="$(dirname "$0")"
CONF_SRC="$HERE/etc/sway-kiosk.conf"
WORDMARK_SRC="$HERE/../../plymouth/taos/wordmark.png"
WORDMARK_DEST=/usr/share/taos/boot-wordmark.png

[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
[ -r "$CONF_SRC" ] || { echo "FAIL: $CONF_SRC not readable"; exit 1; }
[ -r "$WORDMARK_SRC" ] || { echo "FAIL: $WORDMARK_SRC not readable"; exit 1; }

# 1. swaybg. It is a separate package from sway.
if ! apk info -e swaybg >/dev/null 2>&1; then
    echo "installing swaybg..."
    apk add swaybg
else
    echo "swaybg already installed"
fi

# The kiosk runs with PATH=/usr/local/bin:/usr/bin (read from the live process
# environ), so a swaybg that landed only in /usr/sbin would be invisible to it
# and sway would keep logging "failed to execute 'swaybg'" while apk reported a
# clean install. Check the path the kiosk actually searches, not apk's word.
SWAYBG=""
for c in /usr/local/bin/swaybg /usr/bin/swaybg; do
    [ -x "$c" ] && SWAYBG="$c" && break
done
[ -n "$SWAYBG" ] || {
    echo "FAIL: swaybg not on the kiosk PATH (/usr/local/bin:/usr/bin)"
    echo "      found instead: $(ls /usr/sbin/swaybg 2>/dev/null || echo nowhere)"
    exit 1
}
echo "PASS: swaybg reachable at $SWAYBG"

# 2. The wordmark, at a taOS-owned path. Deliberately NOT referenced straight
#    out of the plymouth theme directory: that is plymouth's to reorganise.
install -d -m 755 /usr/share/taos
install -m 644 "$WORDMARK_SRC" "$WORDMARK_DEST"
echo "installed $WORDMARK_DEST"

# 3. The sway config.
install -d -m 755 /etc/taos
install -m 644 "$CONF_SRC" /etc/taos/sway-kiosk.conf
echo "installed /etc/taos/sway-kiosk.conf"

# --- verification -----------------------------------------------------------
# Each check needs a control, or "absent" and "never looked" read the same.

BG_LINE="$(grep -E '^[[:space:]]*output[[:space:]]+\*[[:space:]]+bg' /etc/taos/sway-kiosk.conf || true)"
[ -n "$BG_LINE" ] || { echo "FAIL: no 'output * bg' line in the installed config"; exit 1; }
# Control: a line we did NOT add must also be there, proving we are reading the
# real kiosk config and not a stub.
grep -q 'taos-kiosk-app.conf' /etc/taos/sway-kiosk.conf || {
    echo "FAIL: control line missing - /etc/taos/sway-kiosk.conf is not the kiosk config"
    exit 1
}
echo "PASS: $BG_LINE"

BG_PATH="$(printf '%s\n' "$BG_LINE" | awk '{print $4}')"
[ -r "$BG_PATH" ] || { echo "FAIL: config points at $BG_PATH, which is not readable"; exit 1; }
echo "PASS: background image exists at $BG_PATH ($(stat -c%s "$BG_PATH") bytes)"

echo
echo "NOT PROVEN by this script, and it must not claim to be:"
echo "  - that the gap is actually closed. That needs a REBOOT and human eyes."
echo "  After the next boot, check the failure that was there before is gone:"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -c \"failed to execute 'swaybg'\"   # expect 0"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -ci taos-kiosk                      # control: >0"
echo "  and that swaybg is actually running (by name, never pgrep -f, which"
echo "  matches its own command line):"
echo "      ps -eo comm | grep -x swaybg"
