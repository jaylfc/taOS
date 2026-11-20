#!/bin/sh
# Install the generic taOS kiosk session: sway config, launcher, session
# helpers, chromium policy. Device layers (a handset, a desk) install their own
# files afterwards, including /etc/taos/sway.d/*.conf drop-ins and the
# background image the config names.
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

# --keyboard opts this device in to the system on-screen keyboard (squeekboard).
# Without it nothing keyboard-related is installed or enabled.
KEYBOARD=0
for arg in "$@"; do
    case "$arg" in
        --keyboard) KEYBOARD=1 ;;
        *) echo "usage: $0 [--keyboard]" >&2; exit 2 ;;
    esac
done

HERE="$(dirname "$0")"
CONF_SRC="$HERE/etc/sway-kiosk.conf"
DESTDIR="${TAOS_SESSION_DESTDIR:-}"  # test/staging prefix; empty on a real install

[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
[ -r "$CONF_SRC" ] || { echo "FAIL: $CONF_SRC not readable"; exit 1; }

# Install a package with whichever package manager this distro has: apk
# (Alpine, postmarketOS) or apt (Debian).
pkg_install() {
    if command -v apk >/dev/null 2>&1; then
        if apk info -e "$1" >/dev/null 2>&1; then
            echo "$1 already installed"
        else
            echo "installing $1..."
            apk add "$1"
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        if dpkg -s "$1" >/dev/null 2>&1; then
            echo "$1 already installed"
        else
            echo "installing $1..."
            apt-get install -y "$1"
        fi
    else
        echo "FAIL: neither apk nor apt-get found, cannot install $1"
        exit 1
    fi
}

# 1. swaybg. It is a separate package from sway.
pkg_install swaybg

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

# 2. The background image is NOT installed here. Its size has to match the
#    panel, so the device layer renders and installs it at the path the
#    config's `output * bg` line names; until it does, sway paints black.

# 3. The launcher, which no longer blocks on the taOS server BEFORE starting
#    the compositor. That wait used to run with nothing on screen at all -- the
#    splash had quit and sway had not started -- which was ~10s of the black gap
#    on its own. It now runs inside the session, so the wordmark is up while it
#    waits.
install -m 755 "$HERE/bin/taos-kiosk-launch" /usr/local/bin/taos-kiosk-launch
echo "installed /usr/local/bin/taos-kiosk-launch"
sh -n /usr/local/bin/taos-kiosk-launch || { echo "FAIL: installed launcher is not valid sh"; exit 1; }
grep -q 'taos-kiosk-app.sh' /usr/local/bin/taos-kiosk-launch || {
    echo "FAIL: installed launcher has no runtime app script - wrong version"; exit 1; }
echo "PASS: launcher installed and parses"

# 3b. The session helpers the sway config execs by absolute path.
#
# These were NOT installed here before, so a reflash produced a config naming
# /usr/local/bin/taos-kiosk-idle and friends with nothing at those paths: sway
# came up, the execs failed silently (a bindsym's exec output goes nowhere), and
# the phone had no idle blanking and no power key. Anything the config names has
# to be installed by the same script that installs the config.
install -d -m 755 "$DESTDIR/usr/local/bin"
for helper in "$HERE"/bin/*; do
    [ -f "$helper" ] || continue
    name="$(basename "$helper")"
    install -m 755 "$helper" "$DESTDIR/usr/local/bin/$name"
    echo "installed $DESTDIR/usr/local/bin/$name"
done
install -m 755 "$HERE/shell/taos-shelld.py" "$DESTDIR/usr/local/bin/taos-shelld"
echo "installed $DESTDIR/usr/local/bin/taos-shelld"

# Every path the config execs must now exist and be executable. The loop above
# is not the check: it installs what THIS script knows about, while the config
# is what actually gets run, so a helper added to the config and forgotten here
# is exactly the failure this catches.
MISSING=""
for path in $(grep -oE '/usr/local/bin/taos-[a-z0-9-]+' "$CONF_SRC" | sort -u); do
    [ -x "$DESTDIR$path" ] || MISSING="$MISSING $path"
done
[ -z "$MISSING" ] || { echo "FAIL: sway config execs missing binaries:$MISSING"; exit 1; }
echo "PASS: every taos-* path named by the config is installed and executable"

# Control: prove the grep above actually found paths. An empty list would make
# the check pass by looking at nothing -- the same vacuous green that has bitten
# this repo before.
EXEC_COUNT="$(grep -coE '/usr/local/bin/taos-[a-z0-9-]+' "$CONF_SRC" || true)"
[ "${EXEC_COUNT:-0}" -ge 3 ] || {
    echo "FAIL: only ${EXEC_COUNT:-0} taos-* paths found in the config - the check above measured nothing"
    exit 1; }
echo "PASS: config names $EXEC_COUNT taos-* paths"

# Install the kiosk systemd unit.
install -d -m 755 "$DESTDIR/etc/systemd/system"
install -m 644 "$HERE/systemd/taos-kiosk.service" "$DESTDIR/etc/systemd/system/taos-kiosk.service"
echo "installed $DESTDIR/etc/systemd/system/taos-kiosk.service"
# Only reload systemd on a real install (DESTDIR empty) and when systemctl exists.
if [ -z "$DESTDIR" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi
# 3b-end.

# 3b2. Session settings: the theme/keyboard command, theme packages and the
#      theme packages. Inert until a device opts in to the keyboard.
TAOS_UID="$(id -u taos)"
install -m 755 "$HERE/bin/taos-session" /usr/local/bin/taos-session
sh -n /usr/local/bin/taos-session || { echo "FAIL: installed taos-session is not valid sh"; exit 1; }
install -d -m 755 /usr/share/taos/themes
cp -R "$HERE/themes/." /usr/share/taos/themes/
chmod -R a+rX /usr/share/taos/themes
# /var/lib/taos may already exist with another owner: create it if missing,
# never re-own it. Only the session directory belongs to the session user.
[ -d /var/lib/taos ] || install -d -m 755 /var/lib/taos
install -d -m 755 -o taos /var/lib/taos/session
echo "installed taos-session and the theme packages"

# The keyboard wrapper is inert without the sway drop-in, and `taos-session
# keyboard on` refuses to run without it, so it is always installed.
install -m 755 "$HERE/bin/taos-kiosk-keyboard" /usr/local/bin/taos-kiosk-keyboard
sh -n /usr/local/bin/taos-kiosk-keyboard || { echo "FAIL: installed taos-kiosk-keyboard is not valid sh"; exit 1; }

# 3b3. The system keyboard, only when asked for.
if [ "$KEYBOARD" = 1 ]; then
    pkg_install squeekboard
    # The same candidates taos-kiosk-keyboard probes: Alpine's /usr/sbin is a
    # symlink to /usr/bin, Debian's is not.
    SQUEEKBOARD=""
    for c in /usr/bin/squeekboard /usr/local/bin/squeekboard /usr/sbin/squeekboard; do
        [ -x "$c" ] && SQUEEKBOARD="$c" && break
    done
    [ -n "$SQUEEKBOARD" ] || {
        echo "FAIL: squeekboard not found in /usr/bin, /usr/local/bin or /usr/sbin"; exit 1; }
    echo "PASS: squeekboard reachable at $SQUEEKBOARD"
    # Writes the sway drop-in and the screen-keyboard-enabled gsetting, then
    # reads the gsetting back and fails if it did not stick.
    /usr/local/bin/taos-session keyboard on || {
        echo "FAIL: taos-session keyboard on failed (is the taos session running, with a user bus?)"; exit 1; }
    # The drop-in, not the main config, names this helper, so the check in 3b
    # does not cover it.
    for path in $(grep -oE '/usr/local/bin/taos-kiosk-[a-z0-9-]+' /etc/taos/sway.d/50-keyboard.conf | sort -u); do
        [ -x "$path" ] || { echo "FAIL: keyboard drop-in names missing $path"; exit 1; }
    done
    echo "PASS: keyboard drop-in installed and its helper is executable"
else
    echo "system keyboard not requested (pass --keyboard to install it)"
fi

# 3c. Audio for the KIOSK session.
#
# MEASURED, because the symptom is misleading: PipeWire answers
# `wpctl get-volume` with a real number while `wpctl status` lists NO sinks, so
# a volume control appears to work and drives nothing.
#
# Two things were wrong and both are fixed here. The kiosk user had no session
# manager -- pipewire/wireplumber ran only in the desktop user's session -- and
# it was not in the `audio` group. It DOES hold logind ACLs on /dev/snd (its
# session is the active seat0 one), which is why `aplay -l` already listed the
# card while the desktop user, with no ACL and no group, could not touch it.
#
if ! id taos 2>/dev/null | grep -q '(audio)'; then
    addgroup taos audio 2>/dev/null || usermod -aG audio taos 2>/dev/null || true
    echo "added taos to the audio group"
fi
id taos | grep -q '(audio)' && echo "PASS: taos is in the audio group" || {
    echo "FAIL: taos is not in the audio group - the kiosk cannot open /dev/snd"; exit 1; }

# Lingering so the session manager is not torn down between logins.
loginctl enable-linger taos >/dev/null 2>&1 || true
su taos -s /bin/sh -c "XDG_RUNTIME_DIR=/run/user/$TAOS_UID systemctl --user enable pipewire.socket pipewire.service wireplumber.service" >/dev/null 2>&1 || true
echo "enabled pipewire + wireplumber for the kiosk session"

# 3d. Chromium's own prompts stay out of the kiosk.
#
# Jay, after signing in on the handset: "there is a chrome save password dialog
# (all chrome dialogs like this should be suppressed)". Managed policy is the
# one switch chromium honours for its bubbles -- save password, autofill,
# translate, sign-in/sync, promos, surveys, the default-browser nag -- where
# flags cover some and change between releases. Notifications, microphone and
# camera are GRANTED to taOS's own origin rather than blocked, because taOS's
# features use them; a prompt for them was the same tell (Jay 2026-10-11).
install -d -m 755 /etc/chromium/policies/managed
install -m 644 "$HERE/etc/chromium-policy-taos-kiosk.json" \
    /etc/chromium/policies/managed/taos-kiosk.json
echo "installed chromium policy /etc/chromium/policies/managed/taos-kiosk.json"

# 4. The sway config.
install -d -m 755 /etc/taos
install -m 644 "$CONF_SRC" /etc/taos/sway-kiosk.conf
echo "installed /etc/taos/sway-kiosk.conf"

# 5. Display timeout config (phone layer shares this setting).
# Only install if missing so a chosen value survives an update.
if [ ! -f /etc/taos/display.conf ]; then
    install -m 644 "$HERE/etc/display.conf" /etc/taos/display.conf
    echo "installed /etc/taos/display.conf"
else
    echo "/etc/taos/display.conf already exists, keeping current value"
fi

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

echo
echo "NOT PROVEN by this script, and it must not claim to be:"
echo "  - that the gap is actually closed. That needs a REBOOT and human eyes."
echo "  After the next boot, check the failure that was there before is gone:"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -c \"failed to execute 'swaybg'\"   # expect 0"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -ci taos-kiosk                      # control: >0"
echo "  and that swaybg is actually running (by name, never pgrep -f, which"
echo "  matches its own command line):"
echo "      ps -eo comm | grep -x swaybg"
