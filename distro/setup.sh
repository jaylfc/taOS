#!/bin/sh
# taOS desktop setup: the single source of truth that turns a minimal Debian 13
# (trixie) install into taOS. The amd64 ISO runs this same script. Idempotent:
# safe to re-run after every pull. Run as root from a checkout of this repo.
set -eu
export DEBIAN_FRONTEND=noninteractive
here=$(cd "$(dirname "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
    echo "setup.sh: run as root (sudo $0)" >&2
    exit 1
fi

# Step 1: identity (taos-branding).
"$here/build-debs.sh" >/dev/null
apt-get install -y --reinstall "$here"/dist/taos-branding_*_all.deb
. /etc/os-release
echo "setup.sh: now running $PRETTY_NAME"

# Step 2: shell (taos-shell): zsh, starship prompt, fastfetch, taOS motd.
apt-get install -y --reinstall "$here"/dist/taos-shell_*_all.deb
# The static /etc/motd depends on whether the dynamic motd exists; refresh it.
/usr/lib/taos-branding/apply

# Existing human users: zsh login shell and the taOS ~/.zshrc if they have none.
zsh_bin=/usr/bin/zsh
getent passwd | while IFS=: read -r user _ uid _ _ home shell; do
    [ "$uid" -ge 1000 ] && [ "$uid" -le 59999 ] || continue
    [ -d "$home" ] || continue
    case "$shell" in
        */bash|*/sh) ;;
        *) continue ;;
    esac
    chsh -s "$zsh_bin" "$user" </dev/null
    if [ ! -e "$home/.zshrc" ]; then
        cp /etc/skel/.zshrc "$home/.zshrc"
        chown "$user" "$home/.zshrc"
    fi
done

# New users get zsh too. Replace the key if present (even commented), else append.
set_key() { # file key value
    if grep -q "^#\{0,1\}[[:space:]]*$2=" "$1" 2>/dev/null; then
        sed -i "s|^#\{0,1\}[[:space:]]*$2=.*|$2=$3|" "$1"
    else
        echo "$2=$3" >> "$1"
    fi
}
set_key /etc/adduser.conf DSHELL "$zsh_bin"
set_key /etc/default/useradd SHELL "$zsh_bin"

# Console font: the default is unreadably small on a high resolution display.
# Height of the largest connected display, from the first (native) mode of each
# connected DRM connector. TAOS_DRM_SYSFS points at a fake sysfs in tests.
display_max_height() {
    best=0
    for c in "${TAOS_DRM_SYSFS:-/sys/class/drm}"/card*-*; do
        [ -r "$c/status" ] && [ -r "$c/modes" ] || continue
        [ "$(cat "$c/status")" = connected ] || continue
        mode=$(head -n 1 "$c/modes")
        h=${mode#*x}
        h=$(printf '%s' "$h" | tr -cd '0-9')
        [ -n "$h" ] || continue
        [ "$h" -gt "$best" ] && best=$h
    done
    echo "$best"
}
if [ "$(display_max_height)" -ge 1100 ]; then
    apt-get install -y console-setup
    cs=/etc/default/console-setup
    before=$(cksum < "$cs" 2>/dev/null || echo none)
    set_key "$cs" FONTFACE '"Terminus"'
    set_key "$cs" FONTSIZE '"16x32"'
    after=$(cksum < "$cs")
    if [ "$before" != "$after" ]; then
        setupcon --save || echo "setup.sh: setupcon failed (not on a console?); font applies at next boot" >&2
    fi
fi

# Step 3: display GPU (taos-gpu). NVIDIA cards stay compute only by default.
apt-get install -y --reinstall "$here"/dist/taos-gpu_*_all.deb
if [ -n "${TAOS_DISPLAY_GPU:-}" ]; then
    taos-gpu set-display "$TAOS_DISPLAY_GPU"
elif [ -t 0 ] && [ "$(taos-gpu list | wc -l)" -gt 1 ] \
        && taos-gpu status | grep -q '^policy: auto$'; then
    taos-gpu list
    printf 'Display GPU [auto]: '
    ans=""
    read -r ans || ans=""
    if [ -n "$ans" ] && [ "$ans" != auto ]; then
        taos-gpu set-display "$ans" || echo "setup.sh: keeping display GPU on auto" >&2
    fi
fi
taos-gpu status

# Step 4: GPU drivers. An NVIDIA GPU gets NVIDIA's official driver from NVIDIA's
# own apt repo by default (compute profile unless it drives the display).
# TAOS_NVIDIA_DRIVER=open keeps the open-source driver, =skip does nothing.
# Idempotent: a second run neither re-downloads the keyring nor re-imports the key.
if taos-gpu list | grep -q '  NVIDIA  '; then
    case "${TAOS_NVIDIA_DRIVER:-official}" in
        skip) echo "setup.sh: TAOS_NVIDIA_DRIVER=skip, leaving the NVIDIA driver alone" ;;
        open)
            taos-gpu driver set nvidia open
            ;;
        *) taos-gpu driver install nvidia ;;
    esac
fi
taos-gpu driver status

# Step 5: tools (taos-tools): taos-screenshot for the Wayland session or console.
apt-get install -y --reinstall "$here"/dist/taos-tools_*_all.deb

# Step 6: power (taos-power). A taOS machine does not sleep by default (it
# serves models to other devices); a battery machine keeps suspend allowed.
# TAOS_POWER_PROFILE=always-on|battery chooses non-interactively.
apt-get install -y --reinstall "$here"/dist/taos-power_*_all.deb
if [ -n "${TAOS_POWER_PROFILE:-}" ]; then
    taos-power set "$TAOS_POWER_PROFILE"
fi
taos-power status
