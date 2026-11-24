#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# End to end for taos-notifyd: the daemon started by taos-shell-session, a private D-Bus session,
# a fake controller, and a notification from an ordinary client producing exactly one row.
#   e2e-notifyd.sh <path to taos-notifyd>
# Optional: TAOS_E2E_SWAY=<sway binary> runs the whole thing inside a headless sway session
# (the session script then ties the daemon to that compositor, as on a desktop). Without it the
# same steps run without a compositor (CI). Needs dbus-run-session and python3; the client is
# notify-send when present, else gdbus.
set -eu
BIN=${1:?path to taos-notifyd}
HERE=$(cd "$(dirname "$0")" && pwd)
case $BIN in /*) ;; *) BIN=$(pwd)/$BIN ;; esac

# Never touch a real session bus: always run inside our own.
if [ -z "${TAOS_NOTIFYD_E2E_INNER:-}" ]; then
    command -v dbus-run-session >/dev/null || { echo "SKIP: dbus-run-session not installed"; exit 77; }
    exec env -u DBUS_SESSION_BUS_ADDRESS TAOS_NOTIFYD_E2E_INNER=1 dbus-run-session -- sh "$0" "$BIN"
fi

W=$(mktemp -d "${TAOS_E2E_WORKDIR:-${TMPDIR:-/tmp}}/taos-notifyd-e2e.XXXXXX")
chmod 700 "$W"
PIDS=""
fail=0
ok() { printf 'ok   - %s\n' "$1"; }
bad() { printf 'FAIL - %s\n' "$1"; fail=1; }
cleanup() {
    [ -n "${SESS_STARTED:-}" ] && "$HERE/../../taos-shell-session" stop >/dev/null 2>&1 || true
    for p in $PIDS; do kill "$p" 2>/dev/null || true; done
    rm -rf "$W"
}
trap cleanup EXIT

# A private world: nothing here can reach the person's real apps, state or bus.
export HOME=$W/home XDG_RUNTIME_DIR=$W/run XDG_STATE_HOME=$W/state XDG_CONFIG_HOME=$W/config \
       XDG_DATA_HOME=$W/data XDG_CACHE_HOME=$W/cache TMPDIR=$W/tmp
mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "$XDG_STATE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$TMPDIR"
chmod 700 "$XDG_RUNTIME_DIR"
STATE=$XDG_STATE_HOME/taos/notifyd
H="python3 $HERE/e2e_notifyd_helper.py"

python3 -u "$HERE/../../../testing/fake_controller.py" --port 0 >"$W/fake.out" 2>"$W/fake.err" &
PIDS="$PIDS $!"
i=0; while ! grep -q LISTENING "$W/fake.out" 2>/dev/null; do i=$((i+1)); [ $i -gt 100 ] && { echo "fake controller did not start"; exit 1; }; sleep 0.1; done
PORT=$(sed -n 's/^LISTENING //p' "$W/fake.out" | head -n 1)
$H seed "$PORT" "$XDG_RUNTIME_DIR"

if [ -n "${TAOS_E2E_SWAY:-}" ]; then
    : >"$W/sway.conf"
    WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 "$TAOS_E2E_SWAY" -c "$W/sway.conf" >"$W/sway.log" 2>&1 &
    PIDS="$PIDS $!"
    i=0; while ! ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock >/dev/null 2>&1; do i=$((i+1)); [ $i -gt 100 ] && { echo "sway did not start"; exit 1; }; sleep 0.1; done
    SWAYSOCK=$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock | head -n 1); export SWAYSOCK
    ok "headless sway session up"
fi

# The daemon, started the way the desktop starts it.
export TAOS_SHELL_ENV_FILE=/dev/null TAOS_SHELL_COMPONENTS=taos-notifyd TAOS_SHELL_BIN_DIR=$(dirname "$BIN")
"$HERE/../../taos-shell-session" start >"$W/session.out" 2>&1
SESS_STARTED=1
have_name() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner org.freedesktop.Notifications 2>/dev/null | grep -q true; }
i=0; until have_name; do i=$((i+1)); [ $i -gt 100 ] && { bad "daemon never owned the name"; cat "$W/session.out"; exit 1; }; sleep 0.1; done
ok "taos-notifyd owns org.freedesktop.Notifications"

send() { # summary [body]
    if command -v notify-send >/dev/null 2>&1; then notify-send -a e2e "$1" "${2:-}"
    else gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications \
        --method org.freedesktop.Notifications.Notify e2e 0 "" "$1" "${2:-}" '[]' '{}' 5000 >/dev/null; fi
}
rows() { $H rows "$PORT" "$1"; }
wait_rows() { # title count seconds
    i=0; while [ "$(rows "$1")" != "$2" ]; do i=$((i+1)); [ $i -gt $(($3 * 10)) ] && return 1; sleep 0.1; done; return 0
}
buffered() { ls "$STATE"/*.json 2>/dev/null | wc -l; }

# 1. one notification from an ordinary client: exactly one row
send from-os-app "hello"
if wait_rows from-os-app 1 10; then ok "notify -> one row"; else bad "notify -> one row (got $(rows from-os-app))"; fi
sleep 0.5
[ "$(rows from-os-app)" = 1 ] && ok "still one row after settling" || bad "duplicate row appeared"
[ "$(buffered)" = 0 ] && ok "buffer empty after confirm" || bad "buffer not emptied"

# 2. the controller fails twice, then works: forced retries, still one row
$H config "$PORT" '{"fail":{"path":"/api/notifications/os","status":503,"times":2}}'
send retried
if wait_rows retried 1 20; then ok "retried through two 503s -> one row"; else bad "retry did not deliver"; fi
sleep 0.5
[ "$(rows retried)" = 1 ] && ok "retry stored once" || bad "retry stored $(rows retried) rows"

# 3. the controller is down: the notification is kept on disk, then delivered once
$H config "$PORT" '{"fail":{"path":"/api/notifications/os","status":503,"times":100000}}'
send buffered
sleep 1
[ "$(rows buffered)" = 0 ] && [ "$(buffered)" = 1 ] && ok "controller down: kept on disk, no row" || bad "controller down: buffer=$(buffered) rows=$(rows buffered)"

# 4. the daemon is killed hard between accept and send: the record survives and is delivered once
send crash
sleep 0.3
DPID=$(head -n 1 "$XDG_RUNTIME_DIR/taos-shell/taos-notifyd.pid")
kill -9 "$DPID" 2>/dev/null || true
$H config "$PORT" '{"fail":null}'
if wait_rows crash 1 30 && wait_rows buffered 1 30; then ok "kill -9 then restart: both records delivered"; else bad "after kill -9: crash=$(rows crash) buffered=$(rows buffered)"; fi
sleep 1
[ "$(rows crash)" = 1 ] && [ "$(rows buffered)" = 1 ] && [ "$(buffered)" = 0 ] && ok "exactly one row each, buffer empty" || bad "duplicates or leftovers"

# 5. a second instance does not take the name over
i=0; until have_name; do i=$((i+1)); [ $i -gt 100 ] && break; sleep 0.1; done
if "$BIN" >"$W/second.out" 2>&1; then ok "second instance exits 0"; else bad "second instance failed"; fi
grep -q "not taking it over" "$W/second.out" && ok "second instance says it is not taking the name" || bad "second instance message"
send after-second
wait_rows after-second 1 10 && ok "first instance still serving" || bad "first instance lost the name"

# 6. the session ends: the daemon goes and the name is free
"$HERE/../../taos-shell-session" stop >/dev/null 2>&1; SESS_STARTED=
i=0; while have_name; do i=$((i+1)); [ $i -gt 50 ] && break; sleep 0.1; done
have_name && bad "name still owned after the session stopped" || ok "session stop released the name"

# 7. the bus goes away (the session ends under it): the daemon exits by itself
dbus-daemon --session --nofork --address="unix:path=$W/b2.sock" >"$W/b2.out" 2>&1 &
BUS2=$!; PIDS="$PIDS $BUS2"
i=0; while [ ! -S "$W/b2.sock" ]; do i=$((i+1)); [ $i -gt 50 ] && break; sleep 0.1; done
DBUS_SESSION_BUS_ADDRESS="unix:path=$W/b2.sock" "$BIN" >"$W/third.out" 2>&1 &
N3=$!; PIDS="$PIDS $N3"
sleep 1
kill -0 "$N3" 2>/dev/null && ok "daemon running on the second private bus" || bad "daemon did not start on the second bus"
kill "$BUS2" 2>/dev/null || true
i=0; while kill -0 "$N3" 2>/dev/null; do i=$((i+1)); [ $i -gt 50 ] && break; sleep 0.1; done
kill -0 "$N3" 2>/dev/null && bad "daemon outlived its bus" || ok "daemon exited when its bus went away"

[ "$fail" = 0 ] && echo "e2e: PASS" || echo "e2e: FAIL"
exit $fail
