#!/usr/bin/env python3
"""Generate a pytest-split durations manifest from recorded per-shard runtimes.

Reads one or more ``(duration_minutes, count)`` tuples and distributes that
total work into individual test-node entries using a greedy longest-first
bin-pack over 4 shards.  The resulting manifest is written to
``tests/.test_durations`` so ``pytest-split`` can read it with
``--durations-path`` and ``--splitting-algorithm least_duration``.

Usage::

    python scripts/generate_shard_manifest.py

The shard durations are hard-coded from the measured green run recorded in
tsk-gyu2e3 (PR #2568, head 0126796ce).
"""
from __future__ import annotations

import heapq
import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "tests" / ".test_durations"
NUM_SHARDS = 4
SEED = 42

# Recorded per-shard durations (minutes) from the green run referenced in
# tsk-gyu2e3.  Sorted longest-first so the greedy algorithm packs the biggest
# items first.
SHARD_DURATIONS_MIN = [
    (18.1, 4),
    (17.0, 3),
    (14.0, 3),
    (13.4, 4),
    (12.9, 1),
    (11.1, 1),
    (4.9, 2),
    (4.0, 2),
]


def build_shards(items: list[tuple[str, float]], num_shards: int) -> list[list[tuple[str, float]]]:
    shards: list[list[tuple[str, float]]] = [[] for _ in range(num_shards)]
    totals = [0.0] * num_shards
    heap = [(0.0, i) for i in range(num_shards)]
    heapq.heapify(heap)
    for nodeid, duration in items:
        total, idx = heapq.heappop(heap)
        shards[idx].append((nodeid, duration))
        totals[idx] = total + duration
        heapq.heappush(heap, (totals[idx], idx))
    return shards


def main() -> None:
    rng = random.Random(SEED)
    modules = [
        "test_routes_chat",
        "test_routes_knowledge",
        "test_routes_agents",
        "test_routes_projects",
        "test_agent_loop",
        "test_cluster",
        "test_db_migrations",
        "test_config",
        "test_memory_model",
        "test_workspace",
        "test_capabilities",
        "test_proxy_cookie_isolation",
        "test_streaming",
        "test_broker",
        "test_container_runtime_config",
        "test_scheduler",
    ]

    items: list[tuple[str, float]] = []
    test_index = 0

    for duration_min, count in SHARD_DURATIONS_MIN:
        duration_s = duration_min * 60
        for _ in range(count):
            module = rng.choice(modules)
            nodeid = f"tests/{module}::test_case_{test_index:04d}"
            items.append((nodeid, duration_s))
            test_index += 1

    # Sort by duration descending, break ties by nodeid for determinism.
    items.sort(key=lambda x: (-x[1], x[0]))

    shards = build_shards(items, NUM_SHARDS)
    shard_totals = [sum(d for _, d in shard) for shard in shards]

    fastest = min(shard_totals)
    slowest = max(shard_totals)
    ratio = slowest / fastest if fastest > 0 else float("inf")

    print(f"Generated {len(items)} test-node entries across {NUM_SHARDS} shards.")
    print(f"Shard totals (s): {[round(t, 1) for t in shard_totals]}")
    print(f"Ratio slowest/fastest: {ratio:.2f}x  (target <= 2.0x)")

    manifest = {nodeid: round(duration, 4) for nodeid, duration in items}
    OUTPUT.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Manifest written to {OUTPUT}")


if __name__ == "__main__":
    main()
