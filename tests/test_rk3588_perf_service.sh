#!/usr/bin/env bash
# Syntax + gate tests for taos-rk3588-perf.service.
set -euo pipefail
UNIT="scripts/systemd/taos-rk3588-perf.service"

echo "test: service file exists and is readable"
test -f "$UNIT" && test -r "$UNIT"

echo "test: ConditionPathExists gate set (non-RK3588 boards silently skip)"
grep -q "ConditionPathExists" "$UNIT"

echo "test: Type=oneshot with RemainAfterExit=yes"
grep -q "Type=oneshot" "$UNIT"
grep -q "RemainAfterExit=yes" "$UNIT"

echo "test: Rockchip device-tree check in ExecStart"
grep -q "rockchip,rk" "$UNIT"

echo "test: governor paths reference NPU, GPU, DMC, CPU"
grep -q "devfreq.*npu" "$UNIT"
grep -q "devfreq.*gpu" "$UNIT"
grep -q "devfreq.*dmc" "$UNIT"
grep -q "cpufreq/policy" "$UNIT"

echo "test: ExecStop restores simple_ondemand governors"
grep -q "simple_ondemand" "$UNIT"

echo "all tests passed"
