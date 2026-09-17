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
WORDMARK_SRC="$HERE/boot-wordmark.png"   # pre-rendered full-panel; see tools/render-kiosk-background.py
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

# 2. The background, at a taOS-owned path. This is a FULL-PANEL image with the
#    wordmark already scaled and centred the way taos.script draws it, NOT the
#    raw wordmark: swaybg's `center` does not scale, so the raw 1479px-wide
#    wordmark on a 1080px panel came out enlarged and cropped (seen on the
#    glass). Regenerate with tools/render-kiosk-background.py, which reads the
#    scale factor out of taos.script so the two cannot drift.
install -d -m 755 /usr/share/taos
install -m 644 "$WORDMARK_SRC" "$WORDMARK_DEST"
echo "installed $WORDMARK_DEST"

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
for helper in taos-kiosk-idle taos-kiosk-power taos-kiosk-screen \
              taos-kiosk-dt2w taos-kiosk-dt2w-run taos-kiosk-power-hold \
              taos-kiosk-volume taos-sensord \
              taos-power-apply; do
    install -m 755 "$HERE/bin/$helper" "/usr/local/bin/$helper"
    echo "installed /usr/local/bin/$helper"
done

# Every path the config execs must now exist and be executable. The loop above
# is not the check: it installs what THIS script knows about, while the config
# is what actually gets run, so a helper added to the config and forgotten here
# is exactly the failure this catches.
MISSING=""
for path in $(grep -oE '/usr/local/bin/taos-kiosk-[a-z0-9-]+' "$CONF_SRC" | sort -u); do
    [ -x "$path" ] || MISSING="$MISSING $path"
done
[ -z "$MISSING" ] || { echo "FAIL: sway config execs missing binaries:$MISSING"; exit 1; }
echo "PASS: every taos-kiosk-* path named by the config is installed and executable"

# Control: prove the grep above actually found paths. An empty list would make
# the check pass by looking at nothing -- the same vacuous green that has bitten
# this repo before.
EXEC_COUNT="$(grep -coE '/usr/local/bin/taos-kiosk-[a-z0-9-]+' "$CONF_SRC" || true)"
[ "${EXEC_COUNT:-0}" -ge 2 ] || {
    echo "FAIL: only ${EXEC_COUNT:-0} taos-kiosk-* paths found in the config - the check above measured nothing"
    exit 1; }
echo "PASS: config names $EXEC_COUNT taos-kiosk-* paths"

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
# ⚠ THIS IS NOT ENOUGH ON ITS OWN AND MUST NOT BE READ AS "AUDIO WORKS". With
# both fixed, WirePlumber runs, creates the pmOS role-loopback filters, and
# still instantiates NO ALSA device for card NP1 (sm8250) -- even though the
# UCM profile resolves (`alsaucm -c NP1 list _verbs` -> HiFi), the SPA ALSA
# plugin is installed, and the card enumerates. That last gap is device audio
# bring-up and is tracked separately.
if ! id taos 2>/dev/null | grep -q '(audio)'; then
    addgroup taos audio 2>/dev/null || usermod -aG audio taos 2>/dev/null || true
    echo "added taos to the audio group"
fi
id taos | grep -q '(audio)' && echo "PASS: taos is in the audio group" || {
    echo "FAIL: taos is not in the audio group - the kiosk cannot open /dev/snd"; exit 1; }

# Lingering so the session manager is not torn down between logins.
loginctl enable-linger taos >/dev/null 2>&1 || true
su taos -s /bin/sh -c     'XDG_RUNTIME_DIR=/run/user/999 systemctl --user enable pipewire.socket pipewire.service wireplumber.service'     >/dev/null 2>&1 || true
echo "enabled pipewire + wireplumber for the kiosk session"

# 4. The sway config.
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

# The whole point is that it matches the panel, so check that rather than mere
# existence. PNG stores width/height as big-endian u32 at byte 16.
#
# Read it as raw hex, NOT with `od --endian=big`: busybox od has no --endian,
# so that flag made od fail, left both values EMPTY, and the comparison then
# reported "background is x but the panel is 1080x2400". The check failed
# closed, which is the right direction, but it had measured nothing at all.
PNG_HDR=$(od -An -tx1 -j16 -N8 "$BG_PATH" 2>/dev/null | tr -d " \n")
[ ${#PNG_HDR} -eq 16 ] || { echo "FAIL: could not read PNG header - measured nothing"; exit 1; }
PNG_W=$(( 0x$(printf '%s' "$PNG_HDR" | cut -c1-8) ))
PNG_H=$(( 0x$(printf '%s' "$PNG_HDR" | cut -c9-16) ))
PANEL=$(cat /sys/class/drm/*/modes 2>/dev/null | head -1)
[ -n "$PANEL" ] || { echo "FAIL: could not read the panel mode - measured nothing"; exit 1; }
if [ "${PNG_W}x${PNG_H}" = "$PANEL" ]; then
    echo "PASS: background is ${PNG_W}x${PNG_H}, panel is $PANEL"
else
    echo "FAIL: background is ${PNG_W}x${PNG_H} but the panel is $PANEL"
    echo "      re-render: python3 tools/render-kiosk-background.py ${PANEL%x*} ${PANEL#*x}"
    exit 1
fi

echo
echo "NOT PROVEN by this script, and it must not claim to be:"
echo "  - that the gap is actually closed. That needs a REBOOT and human eyes."
echo "  After the next boot, check the failure that was there before is gone:"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -c \"failed to execute 'swaybg'\"   # expect 0"
echo "      journalctl -b 0 | grep -v COMMAND= | grep -ci taos-kiosk                      # control: >0"
echo "  and that swaybg is actually running (by name, never pgrep -f, which"
echo "  matches its own command line):"
echo "      ps -eo comm | grep -x swaybg"
