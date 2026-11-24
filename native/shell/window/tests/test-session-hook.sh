#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# taos-window-session-hook: GTK button-layout through gsettings and the foot csd file, on both sides.
# Usage: test-session-hook.sh <hook> <taos-theme>
hook=$1; theme=$2
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/cfg"
cp "$theme" "$tmp/bin/taos-theme"
cat >"$tmp/bin/gsettings" <<'G'
#!/bin/sh
echo "$@" >>"$GS_LOG"
G
chmod +x "$tmp/bin/gsettings"
export PATH="$tmp/bin:$PATH" XDG_CONFIG_HOME="$tmp/cfg" HOME="$tmp/home" TAOS_APPEARANCE="$tmp/cfg/taos/appearance.json" GS_LOG="$tmp/gs.log" LC_ALL=C.UTF-8
fail=0
check() { if eval "$2"; then echo "ok   - $1"; else echo "FAIL - $1"; fail=1; fi; }
sh "$hook" apply
check "left layout set" "grep -q \"org.gnome.desktop.wm.preferences button-layout close,minimize,maximize:\" $GS_LOG"
check "foot file written" "grep -q 'preferred=server' $tmp/cfg/foot/taos-csd.ini"
check "foot.ini created with the include" "grep -q 'include=.*taos-csd.ini' $tmp/cfg/foot/foot.ini"
taos-theme set-buttons right >/dev/null 2>&1
sh "$hook" apply
check "right layout set" "tail -n 1 $GS_LOG | grep -q ':minimize,maximize,close'"
# a foot.ini of the person's own is left alone
echo "font=mono" >"$tmp/cfg/foot/foot.ini"
sh "$hook" apply 2>"$tmp/err"
check "own foot.ini untouched" "[ \"\$(cat $tmp/cfg/foot/foot.ini)\" = 'font=mono' ]"
check "a hint is printed" "grep -q 'include=' $tmp/err"
# the colour scheme other toolkits follow: dark, light and auto
last_two() { tail -n 2 "$GS_LOG" | tr '\n' '|'; }
: >"$GS_LOG"
taos-theme set graphite --mode dark >/dev/null 2>&1
sh "$hook" apply
check "dark publishes prefer-dark" "grep -q '^set org.gnome.desktop.interface color-scheme prefer-dark' $GS_LOG"
check "dark publishes Adwaita-dark" "grep -q '^set org.gnome.desktop.interface gtk-theme Adwaita-dark' $GS_LOG"
: >"$GS_LOG"
taos-theme set graphite --mode light >/dev/null 2>&1
sh "$hook" apply
check "light publishes prefer-light" "grep -q '^set org.gnome.desktop.interface color-scheme prefer-light' $GS_LOG"
check "light publishes Adwaita" "grep -q '^set org.gnome.desktop.interface gtk-theme Adwaita$' $GS_LOG"
: >"$GS_LOG"
taos-theme set graphite --mode auto >/dev/null 2>&1
sh "$hook" apply
h=$(date +%H); h=${h#0}
if [ "$h" -ge 7 ] && [ "$h" -lt 19 ]; then want=prefer-light; else want=prefer-dark; fi
check "auto resolves to what is shown now ($want)" "grep -q '^set org.gnome.desktop.interface color-scheme $want' $GS_LOG"
# without gsettings: quiet no-op
saved=$PATH; mkdir -p "$tmp/nogs"; cp "$theme" "$tmp/nogs/taos-theme"
for t in sh sed head tail cmp mkdir mv rm stat sleep cat; do ln -sf "$(command -v $t)" "$tmp/nogs/$t" 2>/dev/null; done
PATH="$tmp/nogs"   # only the fake tools: the real gsettings, if there is one, must not be reachable
out=$(sh "$hook" scheme 2>&1); rc=$?
PATH=$saved
check "no gsettings: silent and fine" "[ \"$rc\" = 0 ] && [ -z \"$out\" ]"
# watch mode follows a change
taos-theme set graphite --mode dark >/dev/null 2>&1
: >"$GS_LOG"
sh "$hook" watch &
w=$!
sleep 1
taos-theme set-buttons left >/dev/null 2>&1
i=0; while [ $i -lt 20 ] && ! tail -n 1 "$GS_LOG" | grep -q '^set org.gnome.desktop.wm.preferences button-layout close'; do sleep 0.5; i=$((i+1)); done
check "watch follows appearance.json" "grep -q 'button-layout close,minimize,maximize:' $GS_LOG"
check "watch published the dark scheme at start" "grep -q 'color-scheme prefer-dark' $GS_LOG"
taos-theme set graphite --mode light >/dev/null 2>&1
i=0; while [ $i -lt 20 ] && ! grep -q 'color-scheme prefer-light' "$GS_LOG"; do sleep 0.5; i=$((i+1)); done
check "watch publishes the new mode when the file changes" "grep -q 'color-scheme prefer-light' $GS_LOG"
kill $w 2>/dev/null
wait $w 2>/dev/null
# single instance: while one watcher runs a second one gives up, and a watcher leaves when its compositor socket is gone
if command -v flock >/dev/null 2>&1; then
    export XDG_RUNTIME_DIR="$tmp/run"; mkdir -p "$XDG_RUNTIME_DIR"
    sock="$XDG_RUNTIME_DIR/sway.sock"
    python3 -c "import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); import time; time.sleep(60)" "$sock" &
    sp=$!
    i=0; while [ $i -lt 20 ] && [ ! -S "$sock" ]; do sleep 0.2; i=$((i+1)); done
    SWAYSOCK=$sock sh "$hook" watch &
    w1=$!
    sleep 1
    SWAYSOCK=$sock sh "$hook" watch &
    w2=$!
    i=0; while [ $i -lt 40 ] && kill -0 $w2 2>/dev/null; do sleep 0.25; i=$((i+1)); done
    check "a second watcher gives up while the first runs" "! kill -0 $w2 2>/dev/null && kill -0 $w1 2>/dev/null"
    kill $sp 2>/dev/null; rm -f "$sock"
    i=0; while [ $i -lt 20 ] && kill -0 $w1 2>/dev/null; do sleep 0.5; i=$((i+1)); done
    check "the watcher exits when its session socket is gone" "! kill -0 $w1 2>/dev/null"
    kill $w1 $w2 2>/dev/null; wait 2>/dev/null
fi
exit $fail
