#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Own headless taOS session: the real /etc/taos config, my binaries first on PATH, everything private (HOME, runtime
# dir, D-Bus, gsettings). Nothing here talks to the live session.
W=${W:?set W to a private work directory}
exec env -i HOME=$W/home XDG_CONFIG_HOME=$W/home/.config XDG_DATA_HOME=$W/home/.local/share XDG_CACHE_HOME=$W/home/.cache \
  XDG_RUNTIME_DIR=$W/xdg-run PATH=$W/shim:/usr/bin:/bin \
  WLR_BACKENDS=headless WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 WLR_LIBINPUT_NO_DEVICES=1 WLR_HEADLESS_OUTPUTS=1 \
  GSETTINGS_BACKEND=memory LANG=C.UTF-8 TAOS_SHELL_ENV_FILE=$W/shell.env \
  dbus-run-session -- /opt/taos/compositor/bin/sway --config /etc/taos/sway/config-effects
