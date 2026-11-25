#!/usr/bin/env bash
# Tests for taos-rk3588-perf.service and its governor helper script.
#
# Static checks pin the unit's shape and the polkit rule's scope; the
# functional checks run the helper's start/stop against a fake sysfs
# tree and prove the devfreq/cpufreq split jaylfc asked for in PR #4506:
# devfreq domains expose `governor`, cpufreq policy dirs expose
# `scaling_governor`, and stop restores the value recorded at start
# (fallbacks: simple_ondemand for devfreq, schedutil for cpufreq).
set -euo pipefail
UNIT="scripts/systemd/taos-rk3588-perf.service"
SCRIPT="scripts/taos-rk3588-perf.sh"

echo "test: service file exists and is readable"
test -f "$UNIT" && test -r "$UNIT"

echo "test: ConditionPathExists gate set (non-RK3588 boards silently skip)"
grep -q "ConditionPathExists" "$UNIT"

echo "test: Type=oneshot with RemainAfterExit=yes"
grep -q "Type=oneshot" "$UNIT"
grep -q "RemainAfterExit=yes" "$UNIT"

echo "test: unit delegates governor control to the helper script"
grep -q "ExecStart=/usr/local/bin/taos-rk3588-perf start" "$UNIT"
grep -q "ExecStop=/usr/local/bin/taos-rk3588-perf stop" "$UNIT"

echo "test: helper script exists, is executable and parses as sh"
test -f "$SCRIPT" && test -x "$SCRIPT"
sh -n "$SCRIPT"

echo "test: Rockchip device-tree check in helper script"
grep -q "rockchip,rk" "$SCRIPT"

echo "test: helper covers NPU, GPU, DMC devfreq domains and CPU policies"
grep -q "class/devfreq" "$SCRIPT"
grep -q '\*.npu' "$SCRIPT"
grep -q '\*.gpu' "$SCRIPT"
grep -q '\*.dmc' "$SCRIPT"
grep -q "cpufreq/policy0" "$SCRIPT"
grep -q "cpufreq/policy4" "$SCRIPT"

echo "test: cpufreq policies are driven via scaling_governor, never governor"
grep -q "scaling_governor" "$SCRIPT"

RULE="scripts/systemd/50-taos-rk3588-perf.rules"

echo "test: polkit rule file exists and is readable"
test -f "$RULE" && test -r "$RULE"

echo "test: polkit rule is scoped to the perf unit only"
grep -q 'action.lookup("unit") == "taos-rk3588-perf.service"' "$RULE"

echo "test: polkit rule is scoped to the controller user only"
grep -q 'subject.user == "taos"' "$RULE"

echo "test: polkit rule grants the manage-units action the Settings toggle needs"
grep -q "org.freedesktop.systemd1.manage-units" "$RULE"

echo "test: polkit rule does not grant the unit-files action (it carries no unit detail to scope by)"
if grep -q "org.freedesktop.systemd1.manage-unit-files" "$RULE"; then
    echo "FAIL: polkit rule grants the unit-files action, which cannot be scoped to one unit" >&2
    exit 1
fi

echo "test: unit gates boot start on the persisted toggle flag"
grep -q "ConditionPathExists=/var/lib/taos-rk3588-perf/enabled" "$UNIT"

# ── Functional: fake sysfs ──────────────────────────────────────────
# Seeded "previous" governors are deliberately NOT the stop fallbacks
# (devfreq powersave vs fallback simple_ondemand; cpufreq ondemand /
# powersave vs fallback schedutil), so a restore that writes the
# fallback instead of the recorded value fails these checks. Each
# cpufreq policy dir also carries a `governor` sentinel file: cpufreq
# has no such file on real hardware, and the helper must never write
# one — that was the original bug (enable and restore silently no-oped
# on the CPUs because the loop wrote $domain/governor for both kinds).

FAKE="$(mktemp -d)"
trap 'rm -rf "$FAKE"' EXIT
SYS="$FAKE/sys"
STATE="$FAKE/run/taos-rk3588-perf"
COMPAT="$FAKE/proc/device-tree/compatible"
mkdir -p "$SYS/class/devfreq/fdab0000.npu" \
         "$SYS/class/devfreq/fd900000.gpu" \
         "$SYS/class/devfreq/ff920000.dmc" \
         "$SYS/devices/system/cpu/cpufreq/policy0" \
         "$SYS/devices/system/cpu/cpufreq/policy4" \
         "$FAKE/proc/device-tree"
printf 'rockchip,rk3588\0' > "$COMPAT"

seed_tree() {
    echo powersave       > "$SYS/class/devfreq/fdab0000.npu/governor"
    echo simple_ondemand > "$SYS/class/devfreq/fd900000.gpu/governor"
    echo powersave       > "$SYS/class/devfreq/ff920000.dmc/governor"
    echo ondemand        > "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor"
    echo powersave       > "$SYS/devices/system/cpu/cpufreq/policy4/scaling_governor"
    echo UNTOUCHED       > "$SYS/devices/system/cpu/cpufreq/policy0/governor"
    echo UNTOUCHED       > "$SYS/devices/system/cpu/cpufreq/policy4/governor"
}

run_perf() {
    TAOS_PERF_SYSFS_ROOT="$SYS" \
    TAOS_PERF_STATE_DIR="$STATE" \
    TAOS_PERF_COMPATIBLE_FILE="$COMPAT" \
        sh "$SCRIPT" "$1" > /dev/null
}

expect() { # expect <file> <value>
    local got
    got="$(cat "$1")"
    if [ "$got" != "$2" ]; then
        echo "FAIL: $1 is '$got', expected '$2'" >&2
        exit 1
    fi
}

echo "test: start sets performance via governor (devfreq) and scaling_governor (cpufreq)"
seed_tree
run_perf start
expect "$SYS/class/devfreq/fdab0000.npu/governor" performance
expect "$SYS/class/devfreq/fd900000.gpu/governor" performance
expect "$SYS/class/devfreq/ff920000.dmc/governor" performance
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" performance
expect "$SYS/devices/system/cpu/cpufreq/policy4/scaling_governor" performance

echo "test: start never writes a governor file under a cpufreq policy dir"
expect "$SYS/devices/system/cpu/cpufreq/policy0/governor" UNTOUCHED
expect "$SYS/devices/system/cpu/cpufreq/policy4/governor" UNTOUCHED

echo "test: stop restores the governors recorded at start, not the fallbacks"
run_perf stop
expect "$SYS/class/devfreq/fdab0000.npu/governor" powersave
expect "$SYS/class/devfreq/fd900000.gpu/governor" simple_ondemand
expect "$SYS/class/devfreq/ff920000.dmc/governor" powersave
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" ondemand
expect "$SYS/devices/system/cpu/cpufreq/policy4/scaling_governor" powersave
expect "$SYS/devices/system/cpu/cpufreq/policy0/governor" UNTOUCHED
expect "$SYS/devices/system/cpu/cpufreq/policy4/governor" UNTOUCHED

echo "test: stop with nothing recorded falls back per family"
seed_tree
rm -rf "$FAKE/run"
run_perf stop
expect "$SYS/class/devfreq/fdab0000.npu/governor" simple_ondemand
expect "$SYS/class/devfreq/fd900000.gpu/governor" simple_ondemand
expect "$SYS/class/devfreq/ff920000.dmc/governor" simple_ondemand
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" schedutil
expect "$SYS/devices/system/cpu/cpufreq/policy4/scaling_governor" schedutil

echo "test: start on a non-Rockchip board writes nothing"
seed_tree
printf 'raspberrypi,5-model-b\0' > "$COMPAT"
run_perf start
expect "$SYS/class/devfreq/fdab0000.npu/governor" powersave
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" ondemand

echo "test: stop on a non-Rockchip board writes nothing"
seed_tree
rm -rf "$FAKE/run"
printf 'raspberrypi,5-model-b\0' > "$COMPAT"
run_perf stop
expect "$SYS/class/devfreq/fdab0000.npu/governor" powersave
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" ondemand
expect "$SYS/devices/system/cpu/cpufreq/policy4/scaling_governor" powersave

echo "test: start changes no governor when recording the previous value fails"
printf 'rockchip,rk3588\0' > "$COMPAT"
seed_tree
rm -rf "$FAKE/run"
touch "$FAKE/state-blocker"
TAOS_PERF_SYSFS_ROOT="$SYS" \
TAOS_PERF_STATE_DIR="$FAKE/state-blocker/state" \
TAOS_PERF_COMPATIBLE_FILE="$COMPAT" \
    sh "$SCRIPT" start > /dev/null
expect "$SYS/class/devfreq/fdab0000.npu/governor" powersave
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" ondemand
expect "$SYS/devices/system/cpu/cpufreq/policy4/scaling_governor" powersave

echo "test: stop keeps the recorded governor and fails when the restore write fails"
seed_tree
rm -rf "$FAKE/run"
run_perf start
expect "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor" performance
rm "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor"
mkdir "$SYS/devices/system/cpu/cpufreq/policy0/scaling_governor"
rc=0
run_perf stop || rc=$?
if [ "$rc" -eq 0 ]; then
    echo "FAIL: stop reported success although a restore write failed" >&2
    exit 1
fi
expect "$STATE/cpu.policy0" ondemand
expect "$SYS/class/devfreq/fdab0000.npu/governor" powersave

echo "all tests passed"
