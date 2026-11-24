#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Stops only the taos-frame whose SWAYSOCK is MY session's, then starts the built one in my session.
W=${W:?set W}
for p in $(pgrep -u $(id -u) -x taos-frame); do
  if tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -qx "XDG_RUNTIME_DIR=$W/xdg-run"; then kill $p; fi
done
sleep 2
. $W/ctl.sh
sm exec "bash -c '. ${QT_ENV_SH:?set QT_ENV_SH to the Qt environment file}; exec taos-frame 2>> $W/frame.err'" >/dev/null
sleep 4
taos-frame state | head -c 60; echo
