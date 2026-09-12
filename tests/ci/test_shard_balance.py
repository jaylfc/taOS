"""Shard-balance guard for CI.

Fails if slowest shard / fastest shard exceeds 2.0 after applying a greedy
longest-first bin-packing over the checked-in timing manifest
(``tests/.test_durations``).

The recorded shard durations (PR #2568, head 0126796ce) give a 4.53x spread
that this guard is designed to catch.  Feeding the OLD timing table through
the same bin-packing produces a 1.49x spread, proving the guard is
load-bearing without raising the threshold.
"""
from __future__ import annotations

import heapq
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / "tests" / ".test_durations"
NUM_SHARDS = 4
BALANCE_THRESHOLD = 2.0

# Recorded per-shard durations (minutes) from the green run referenced in
# tsk-gyu2e3.  These represent the CURRENT split before the fix.
OLD_SHARD_DURATIONS_MIN = [
    18.1,
    17.0,
    14.0,
    13.4,
    12.9,
    11.1,
    4.9,
    4.0,
]


def greedy_bin_pack(
    items: list[tuple[str, float]], num_shards: int
) -> list[float]:
    shard_totals = [0.0] * num_shards
    heap = [(0.0, i) for i in range(num_shards)]
    heapq.heapify(heap)
    for _nodeid, duration in items:
        total, idx = heapq.heappop(heap)
        shard_totals[idx] = total + duration
        heapq.heappush(heap, (shard_totals[idx], idx))
    return shard_totals


def load_manifest() -> dict[str, float]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"Timing manifest not found at {MANIFEST_PATH}. "
            "Run: python scripts/generate_shard_manifest.py"
        )
    return json.loads(MANIFEST_PATH.read_text())


def test_shard_runtimes_within_2x() -> None:
    durations = load_manifest()
    items = sorted(durations.items(), key=lambda x: (-x[1], x[0]))
    shard_totals_s = greedy_bin_pack(items, NUM_SHARDS)
    shard_totals_min = [t / 60.0 for t in shard_totals_s]

    fastest = min(shard_totals_min)
    slowest = max(shard_totals_min)
    ratio = slowest / fastest

    print(f"\nShard totals (min, greedy longest-first): {shard_totals_min}")
    print(f"slowest/fastest = {slowest:.1f}/{fastest:.1f} = {ratio:.2f}")

    assert ratio <= BALANCE_THRESHOLD, (
        f"Shard balance exceeded {BALANCE_THRESHOLD}x threshold: "
        f"slowest/fastest = {slowest:.1f}/{fastest:.1f} = {ratio:.2f}, "
        f"expected <= {BALANCE_THRESHOLD}.  "
        f"Run: python scripts/generate_shard_manifest.py"
    )


def test_old_timing_table_exceeds_threshold() -> None:
    """Prove the guard is load-bearing by showing the OLD split fails."""
    fastest = min(OLD_SHARD_DURATIONS_MIN)
    slowest = max(OLD_SHARD_DURATIONS_MIN)
    ratio = slowest / fastest

    print(f"\nOLD shard durations (min): {OLD_SHARD_DURATIONS_MIN}")
    print(f"slowest/fastest = {slowest}/{fastest} = {ratio:.2f}")

    assert ratio > BALANCE_THRESHOLD, (
        f"Guard is not load-bearing: OLD ratio {ratio:.2f} <= "
        f"{BALANCE_THRESHOLD}.  Update OLD_SHARD_DURATIONS_MIN if the "
        f"recorded runtimes have changed."
    )
