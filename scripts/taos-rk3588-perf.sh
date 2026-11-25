#!/bin/sh
# taos-rk3588-perf — governor control for taos-rk3588-perf.service (#361).
#
#   taos-rk3588-perf start   record current governors, set performance
#   taos-rk3588-perf stop    restore the recorded governors
#
# The two sysfs families are NOT interchangeable, and treating them as
# one loop was the original bug: devfreq domains (NPU/GPU/DMC) expose a
# `governor` file, while cpufreq policy dirs expose `scaling_governor`.
# Writing $policy/governor silently no-ops (the file does not exist),
# and `simple_ondemand` — the devfreq default — is not a valid cpufreq
# governor at all. So each family is driven through its own file, and
# stop restores the value start recorded for that domain, with a
# per-family fallback when nothing was recorded (unit stopped without
# a matching start, state dir lost): devfreq -> simple_ondemand (the
# kernel default this tuning deviates from), cpufreq -> schedutil.
#
# Recorded values live in the state dir (default /run/taos-rk3588-perf,
# tmpfs, so a reboot clears them along with the governors themselves).
#
# The three path roots are env-overridable so the shell test can run
# start/stop against a fake sysfs tree; the defaults are the real
# system paths and are what the installed unit uses.
#
# Scope: RK3588 / RK3576 / RK3568 only. start re-checks the device-tree
# compatible string (the unit's ConditionPathExists is only the first
# gate) and exits 0 without touching anything on other boards.

SYSFS_ROOT="${TAOS_PERF_SYSFS_ROOT:-/sys}"
COMPATIBLE_FILE="${TAOS_PERF_COMPATIBLE_FILE:-/proc/device-tree/compatible}"
STATE_DIR="${TAOS_PERF_STATE_DIR:-/run/taos-rk3588-perf}"

is_rockchip() {
    compat=""
    if [ -r "$COMPATIBLE_FILE" ]; then
        compat="$(tr -d '\000' < "$COMPATIBLE_FILE")"
    fi
    echo "$compat" | grep -qE "rockchip,rk(3588|3576|3568)"
}

# $1 = governor file, $2 = state file, $3 = value to write.
# Records the file's current value into $2 first (first record wins, so
# a repeated start cannot record "performance" as the previous value).
# The governor is only changed once a non-empty record exists: without
# one, stop would restore the fallback instead of the real previous
# value, so a failed recording leaves the governor untouched.
record_and_set() {
    if [ -w "$1" ]; then
        if [ ! -s "$2" ]; then
            cat "$1" > "$2" 2>/dev/null || true
        fi
        if [ ! -s "$2" ]; then
            rm -f "$2"
            echo "  $1: could not record current governor — leaving it unchanged" >&2
            return 1
        fi
        echo "$3" > "$1" 2>/dev/null || true
        echo "  $1: $(cat "$2" 2>/dev/null || echo '?') -> $(cat "$1" 2>/dev/null || echo '?')"
    fi
}

# $1 = governor file, $2 = state file, $3 = fallback when unrecorded.
# The record is deleted only once the value is actually written back:
# on a failed write it stays, so a later stop retries with the real
# previous value instead of the fallback, and the failure propagates.
restore() {
    if [ -w "$1" ]; then
        prev="$(cat "$2" 2>/dev/null || true)"
        if [ -z "$prev" ]; then
            prev="$3"
        fi
        if echo "$prev" > "$1" 2>/dev/null; then
            rm -f "$2"
            echo "  $1: restored $prev"
        else
            echo "  $1: FAILED to restore $prev — recorded value kept" >&2
            return 1
        fi
    fi
}

devfreq_domains() {
    for domain in "$SYSFS_ROOT"/class/devfreq/*.npu \
                  "$SYSFS_ROOT"/class/devfreq/*.gpu \
                  "$SYSFS_ROOT"/class/devfreq/*.dmc; do
        echo "$domain"
    done
}

cpu_policies() {
    for policy in "$SYSFS_ROOT"/devices/system/cpu/cpufreq/policy0 \
                  "$SYSFS_ROOT"/devices/system/cpu/cpufreq/policy4; do
        echo "$policy"
    done
}

do_start() {
    if ! is_rockchip; then
        echo "taos-rk3588-perf: not a Rockchip board — exiting"
        exit 0
    fi
    echo "taos-rk3588-perf: setting performance governors for Rockchip NPU"
    mkdir -p "$STATE_DIR"
    # devfreq: NPU throughput drops ~5x on simple_ondemand; GPU (Mali)
    # Vulkan compute and the DMC (LPDDR bandwidth is the bottleneck for
    # large models) benefit from fixed clocks too.
    devfreq_domains | while read -r domain; do
        record_and_set "$domain/governor" "$STATE_DIR/devfreq.$(basename "$domain")" "performance"
    done
    # cpufreq: RK3588 big.LITTLE — performance on both clusters; the
    # big cluster (policy4, cpu4-7) is where rkllama.cpp pins its
    # threads via taskset.
    cpu_policies | while read -r policy; do
        record_and_set "$policy/scaling_governor" "$STATE_DIR/cpu.$(basename "$policy")" "performance"
    done
    echo "taos-rk3588-perf: governors applied"
}

do_stop() {
    # Same hardware guard as start: on other boards start recorded
    # nothing, and restoring would write the fallback governors over
    # whatever the board is actually running.
    if ! is_rockchip; then
        echo "taos-rk3588-perf: not a Rockchip board — exiting"
        exit 0
    fi
    echo "taos-rk3588-perf: restoring recorded governors"
    failed=0
    for domain in $(devfreq_domains); do
        restore "$domain/governor" "$STATE_DIR/devfreq.$(basename "$domain")" "simple_ondemand" || failed=1
    done
    for policy in $(cpu_policies); do
        restore "$policy/scaling_governor" "$STATE_DIR/cpu.$(basename "$policy")" "schedutil" || failed=1
    done
    rmdir "$STATE_DIR" 2>/dev/null || true
    if [ "$failed" -ne 0 ]; then
        echo "taos-rk3588-perf: some governors could not be restored — recorded values kept for a retry" >&2
        exit 1
    fi
    echo "taos-rk3588-perf: governors restored"
}

case "${1:-}" in
    start) do_start ;;
    stop) do_stop ;;
    *)
        echo "usage: taos-rk3588-perf {start|stop}" >&2
        exit 2
        ;;
esac
