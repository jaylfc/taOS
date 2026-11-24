#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Dev tooling for the headless screenshots in frame/README.md: an isolated taOS session (own HOME, runtime dir, D-Bus) with
# the real /etc/taos config and these builds first on PATH. Needs W (work dir) and QT_ENV_SH. Not part of the install.
# Builds the isolated session directory: W/home, W/xdg, W/shim (PATH shim), W/shell.env.
set -e
W=${W:?set W to a private work directory}
B=$W/build
rm -rf $W/shim; mkdir -p $W/shim $W/home/.config/taos $W/xdg-run $W/shots
chmod 700 $W/xdg-run
for f in /opt/taos/shell/bin/*; do case $(basename $f) in *.prev-*) ;; *) ln -s $f $W/shim/$(basename $f);; esac; done
for f in /opt/taos/apps/bin/*; do ln -sf $f $W/shim/$(basename $f); done
ln -sf $B/frame/taos-frame $W/shim/taos-frame
ln -sf $B/common/taos-theme $W/shim/taos-theme
ln -sf $B/window/taos-window $W/shim/taos-window 2>/dev/null || ln -sf $(find $B -name taos-window -type f | head -1) $W/shim/taos-window
[ -x $B/frame/taos-frame-vptool ] && ln -sf $B/frame/taos-frame-vptool $W/shim/vptool
[ -x $W/settings-build/taos-settings ] && ln -sf $W/settings-build/taos-settings $W/shim/taos-settings
# window-session-hook from my worktree copy (the real one, unchanged logic)
cat > $W/shim/dbus-update-activation-environment <<'S'
#!/bin/sh
exit 0
S
chmod +x $W/shim/dbus-update-activation-environment
cat > $W/shell.env <<S
. ${QT_ENV_SH:?set QT_ENV_SH to the Qt environment file}
TAOS_SHELL_BIN_DIR=$W/shim
export TAOS_SHELL_BIN_DIR
TAOS_SHELL_COMPONENTS="taos-bar taos-overview taos-dock taos-launcher taos-frame"
export TAOS_SHELL_COMPONENTS
S
echo prepared
