# SPDX-License-Identifier: AGPL-3.0-or-later
# source me: environment to talk to MY headless session only
W=${W:?set W to a private work directory}
export XDG_RUNTIME_DIR=$W/xdg-run WAYLAND_DISPLAY=wayland-1
export SWAYSOCK=$(ls $XDG_RUNTIME_DIR/sway-ipc.*.sock | head -1)
export PATH=$W/shim:/opt/taos/compositor/bin:/usr/bin:/bin HOME=$W/home XDG_CONFIG_HOME=$W/home/.config
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent GSETTINGS_BACKEND=memory
sm() { /opt/taos/compositor/bin/swaymsg "$@"; }
shot() { grim "$W/shots/$1.png"; echo "$W/shots/$1.png"; }
tree() { sm -t get_tree -r | python3 -c "
import json,sys
t=json.load(sys.stdin)
def w(n,ws=''):
    if n['type']=='workspace': ws=n['name']
    if 'shell' in n: print(n['id'],repr(n.get('app_id') or (n.get('window_properties') or {}).get('class')),'border=',n['border'],'foc=',n['focused'],'fl=',n['floating'],'rect',n['rect']['x'],n['rect']['y'],n['rect']['width'],n['rect']['height'],'deco',n['deco_rect']['y'],n['deco_rect']['height'],'ws',ws,'marks',n['marks'])
    for k in n.get('nodes',[])+n.get('floating_nodes',[]): w(k,ws)
w(t)"; }
launch() { sm exec "bash -c '. ${QT_ENV_SH:?set QT_ENV_SH to the Qt environment file}; exec $*'"; }
