#!/usr/bin/env python3
"""Shard manifest guard.

Validates the pytest-split duration manifest (tests/.test_durations) against
the currently collected test id set, and checks that a greedy least-duration
split keeps the shard totals balanced.

The manifest records per-test durations from a real suite run. It must cover the
collected ids; otherwise pytest-split falls back to an even split with no error
beyond a log line, which can leave the slowest shard several times the fastest.
This guard turns that silent fallback into a hard failure.

CLI:
    python scripts/check_shard_manifest.py \
        --manifest tests/.test_durations --collected-file collected.txt \
        --splits 4 --max-ratio 2.0

stdlib only.
"""
from __future__ import annotations

import argparse
import heapq
import json
import subprocess
import sys
from pathlib import Path

MIN_COVERAGE = 0.95
DEFAULT_MAX_RATIO = 2.0
DEFAULT_SPLITS = 4
DEFAULT_MANIFEST = "tests/.test_durations"


def load_manifest(path) -> dict[str, float]:
    """Load a {nodeid: seconds} manifest from a JSON file.

    A missing or empty file yields an empty manifest so callers can report the
    absence of a recorded run as a problem instead of crashing on it.
    """
    p = Path(path)
    if not p.exists():
        return {}
    text = p.read_text()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError(f"manifest {path} is not a JSON object")
    return data


def collected_ids(pytest_args=None) -> set[str]:
    """Collect test node ids by invoking pytest --collect-only.

    Runs: python -m pytest --collect-only -q -p no:cacheprovider <args>
    and returns the lines containing '::'. Default args are
    ['tests/', '--ignore=tests/e2e'].
    """
    if pytest_args is None:
        pytest_args = ["tests/", "--ignore=tests/e2e"]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        *pytest_args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {line.strip() for line in result.stdout.splitlines() if "::" in line}


def check_keys(manifest, collected, min_coverage=MIN_COVERAGE) -> list[str]:
    """Return a list of manifest problems (empty when valid).

    Problems reported:
      - 'manifest is empty' when the manifest has no entries
      - 'N manifest keys match no collected test: <first 5>' when manifest
        keys are not a subset of the collected ids
      - 'coverage X% of collected ids below Y%' when the fraction of collected
        ids present in the manifest is below min_coverage
    """
    problems: list[str] = []
    if not manifest:
        problems.append("manifest is empty")
        return problems
    keys = set(manifest)
    unmatched = keys - collected
    if unmatched:
        first5 = sorted(unmatched)[:5]
        problems.append(
            f"{len(unmatched)} manifest keys match no collected test: {first5}"
        )
    if collected:
        coverage = len(keys & collected) / len(collected)
        if coverage < min_coverage:
            pct = coverage * 100
            thresh = min_coverage * 100
            problems.append(
                f"coverage {pct:.0f}% of collected ids below {thresh:.0f}%"
            )
    return problems


def least_duration_split(manifest, splits=DEFAULT_SPLITS) -> list[float]:
    """Greedy least-duration split, mirroring pytest-split's algorithm.

    Sort items by duration descending, then assign each to the shard with the
    smallest running total (kept in a min-heap). Returns the per-shard totals.
    """
    totals = [0.0] * splits
    heapq.heapify(totals)
    for _nodeid, duration in sorted(
        manifest.items(), key=lambda kv: kv[1], reverse=True
    ):
        smallest = heapq.heappop(totals)
        smallest += duration
        heapq.heappush(totals, smallest)
    return totals


def balance_ratio(totals) -> float:
    """max(totals)/min(totals); raises ValueError if any shard is empty."""
    minimum = min(totals)
    if minimum == 0:
        raise ValueError("min shard total is zero")
    return max(totals) / minimum


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--splits", type=int, default=DEFAULT_SPLITS)
    parser.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO)
    parser.add_argument("--collected-file", default=None)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)

    if args.collected_file:
        collected = {
            line.strip()
            for line in Path(args.collected_file).read_text().splitlines()
            if "::" in line
        }
    else:
        collected = collected_ids()

    problems = check_keys(manifest, collected, min_coverage=MIN_COVERAGE)

    totals: list[float] = []
    ratio: float | None = None
    if manifest:
        try:
            totals = least_duration_split(manifest, splits=args.splits)
            ratio = balance_ratio(totals)
        except ValueError:
            ratio = float("inf")
        if ratio > args.max_ratio:
            problems.append(
                f"shard imbalance ratio {ratio:.2f} exceeds max {args.max_ratio}"
            )

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1

    coverage = (
        len(set(manifest) & collected) / len(collected) * 100 if collected else 0.0
    )
    if ratio is None:
        print(
            f"OK: {len(manifest)} keys, coverage {coverage:.0f}%, "
            "shard totals [] min 0, ratio 0.00"
        )
    else:
        print(
            f"OK: {len(manifest)} keys, coverage {coverage:.0f}%, "
            f"shard totals {totals}, min {min(totals)}, ratio {ratio:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
