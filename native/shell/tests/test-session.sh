#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Tests for taos-shell-session using a stub component. Usage: test-session.sh <path to taos-shell-session>
set -u
SESSION=${1:?path to taos-shell-session}
fail=0
ok() { printf 'ok   - %s\n' "$1"; }
bad() { printf 'FAIL - %s\n' "$1"; fail=1; }
check() { # description, command...
    d=$1; shift
    if "$@"; then ok "$d"; else bad "$d"; fi
}

T=$(mktemp -d "${TMPDIR:-/tmp}/taos-session-test.XXXXXX") || exit 1
cleanup() {
    for f in "$T"/run/taos-shell/*.pid; do
        [ -e "$f" ] || continue
        p=$(head -n 1 "$f")
        [ -n "$p" ] && kill "$p" 2>/dev/null
    done
    rm -rf "$T"
}
trap cleanup EXIT

mkdir -p "$T/bin" "$T/run"
chmod 700 "$T/run"

# sleep stand-in: records its argument and returns at once
cat >"$T/fakesleep" <<EOS
#!/bin/sh
echo "\$1" >>"$T/sleeps"
EOS
chmod +x "$T/fakesleep"

# stub component: behaviour chosen by the file "$T/mode"
cat >"$T/bin/stubc" <<EOS
#!/bin/sh
echo run >>"$T/runs"
echo "\${TAOS_TEST_MARK:-unset}" >"$T/mark"
mode=\$(cat "$T/mode")
case \$mode in
    fail) exit 3 ;;
    clean) exit 0 ;;
    failtwice)
        n=\$(wc -l <"$T/runs")
        [ "\$n" -le 2 ] && exit 1 ;;
esac
trap 'kill \$p 2>/dev/null; exit 0' TERM
sleep 1000 >/dev/null 2>&1 &
p=\$!
wait \$p
EOS
chmod +x "$T/bin/stubc"

export XDG_RUNTIME_DIR="$T/run"
export TAOS_SHELL_BIN_DIR="$T/bin"
export TAOS_SHELL_COMPONENTS=stubc
export TAOS_SHELL_SLEEP_CMD="$T/fakesleep"
export TAOS_SHELL_ENV_FILE="$T/shell.env"
printf 'TAOS_TEST_MARK=from-env-file\n' >"$T/shell.env"
STATE="$T/run/taos-shell"

wait_for() { # seconds, test command...
    n=$(( $1 * 10 )); shift
    while [ "$n" -gt 0 ]; do
        "$@" && return 0
        sleep 0.1
        n=$((n - 1))
    done
    return 1
}
count_lines() { [ -e "$1" ] && wc -l <"$1" | tr -d ' ' || echo 0; }
reset() { rm -f "$T/runs" "$T/sleeps" "$T/mark"; }

# 1. always failing: backoff 1 2 5 30 30..., gives up at the 10th failure
echo fail >"$T/mode"; reset
"$SESSION" start 2>"$T/err1"
check "gives up after repeated failures" wait_for 15 test -e "$STATE/stubc.gaveup"
check "component ran exactly 10 times" test "$(count_lines "$T/runs")" = 10
check "backoff schedule is 1 2 5 30 30 30 30 30 30" test "$(tr '\n' ' ' <"$T/sleeps")" = "1 2 5 30 30 30 30 30 30 "
check "give-up is logged clearly" grep -q "giving up" "$T/err1"
check "status reports gave up" sh -c '"$0" status | grep -q "stubc: gave up"' "$SESSION"
check "env file reaches the component" test "$(cat "$T/mark")" = from-env-file
"$SESSION" stop 2>/dev/null
check "stop clears gave-up marker and pidfiles" sh -c '! ls "$0"/stubc.* "$0"/supervisor-* >/dev/null 2>&1' "$STATE"

# 2. crash twice, then run: restarts counted, then stop by pidfile
echo failtwice >"$T/mode"; reset
"$SESSION" start 2>"$T/err2"
check "recovers after two crashes" wait_for 15 sh -c 'test -e "$0/stubc.pid" && test "$(wc -l <"$1" | tr -d " ")" = 3' "$STATE" "$T/runs"
check "two restarts with delays 1 then 2" test "$(tr '\n' ' ' <"$T/sleeps")" = "1 2 "
check "status says running" sh -c '"$0" status | grep -q "stubc: running"' "$SESSION"
cpid=$(head -n 1 "$STATE/stubc.pid"); spid=$(head -n 1 "$STATE/supervisor-stubc.pid")
check "pidfiles name live processes" sh -c 'kill -0 "$0" && kill -0 "$1"' "$cpid" "$spid"
"$SESSION" start 2>"$T/err2b"
check "second start does not duplicate" grep -q "already supervised" "$T/err2b"
"$SESSION" stop 2>/dev/null
check "stop ends the component" sh -c '! kill -0 "$0" 2>/dev/null' "$cpid"
check "stop ends the supervisor" sh -c '! kill -0 "$0" 2>/dev/null' "$spid"
check "stop removes pidfiles" test ! -e "$STATE/stubc.pid" -a ! -e "$STATE/supervisor-stubc.pid"
check "status says stopped after stop" sh -c '"$0" status | grep -q "stubc: stopped"' "$SESSION"

# 3. a clean exit is not restarted
echo clean >"$T/mode"; reset
"$SESSION" start 2>"$T/err3"
check "clean exit is noticed" wait_for 10 grep -q "exited cleanly" "$T/err3"
check "clean exit ran once" test "$(count_lines "$T/runs")" = 1
"$SESSION" stop 2>/dev/null

# 4. reserved interface
"$SESSION" screen-off 2>"$T/e4"; rc=$?
check "screen-off exits 0" test "$rc" = 0
check "screen-off says not implemented" grep -q "not implemented" "$T/e4"
"$SESSION" screen-on 2>"$T/e5"; rc=$?
check "screen-on exits 0 with note" sh -c 'test "$0" = 0 && grep -q "not implemented" "$1"' "$rc" "$T/e5"
"$SESSION" bogus 2>/dev/null; rc=$?
check "unknown command exits 2" test "$rc" = 2

# 6. tied to its desktop session: a supervisor from an ended session stops,
#    and a new session replaces a leftover supervisor at once
echo run >"$T/mode"; reset
sleep 1000 >/dev/null 2>&1 & fakecomp1=$!
TAOS_SHELL_SESSION_PID=$fakecomp1 "$SESSION" start 2>"$T/err6"
check "session 1 component running" wait_for 10 test -e "$STATE/stubc.pid"
sp1=$(head -n 1 "$STATE/supervisor-stubc.pid"); cp1=$(head -n 1 "$STATE/stubc.pid")
check "supervisor records its session" test "$(cat "$STATE/supervisor-stubc.session")" = "$fakecomp1"
sleep 1000 >/dev/null 2>&1 & fakecomp2=$!
TAOS_SHELL_SESSION_PID=$fakecomp2 "$SESSION" start 2>"$T/err6b"
check "new session replaces the leftover supervisor" grep -q "replacing the supervisor from an earlier session" "$T/err6b"
check "old supervisor is gone" wait_for 10 sh -c '! kill -0 "$0" 2>/dev/null' "$sp1"
check "old component is gone" wait_for 10 sh -c '! kill -0 "$0" 2>/dev/null' "$cp1"
check "new session component running" wait_for 10 sh -c 'test -e "$0/stubc.pid" && test "$(cat "$0/supervisor-stubc.session")" = "$1"' "$STATE" "$fakecomp2"
# the compositor of session 2 ends and the component crashes: no restart
cp2=$(head -n 1 "$STATE/stubc.pid"); sp2=$(head -n 1 "$STATE/supervisor-stubc.pid")
kill "$fakecomp2" 2>/dev/null; wait "$fakecomp2" 2>/dev/null
kill -9 "$cp2" 2>/dev/null
check "supervisor of an ended session exits" wait_for 10 sh -c '! kill -0 "$0" 2>/dev/null' "$sp2"
check "says why it stopped" wait_for 5 grep -q "desktop session (compositor pid $fakecomp2) has ended" "$T/err6b"
kill "$fakecomp1" 2>/dev/null
"$SESSION" stop 2>/dev/null

# 5. the script never signals by name
check "no pkill or killall in the script" sh -c '! grep -E "(^|[^A-Za-z_-])(pkill|killall)([^A-Za-z_-]|$)" "$0"' "$SESSION"
check "script is POSIX sh" sh -c 'head -n 1 "$0" | grep -q "^#!/bin/sh"' "$SESSION"

exit $fail
