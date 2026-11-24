#!/bin/sh
# The snippet set must contain the session environment line (D-Bus activation environment), and the installer must ship it.
# Usage: check-session-env.sh <native dir>
n=$1
f=$n/shell/window/sway/05-session-env.conf
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=1; }
grep -q '^exec dbus-update-activation-environment --systemd WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland$' "$f" \
    && ok "05-session-env.conf has the activation environment line" || bad "05-session-env.conf lacks the exact exec line"
grep -q '^exec_always' "$f" && bad "must be exec, not exec_always" || ok "exec, not exec_always"
grep -q 'snip .*05-session-env.conf\" 05-session-env.conf' "$n/install-desktop.sh" \
    && ok "install-desktop.sh ships it as /etc/taos/sway.d/05-session-env.conf" || bad "install-desktop.sh does not ship 05-session-env.conf"
exit $fail
