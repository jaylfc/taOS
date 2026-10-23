#!/usr/bin/env python3
"""Shard manifest guard.

Validates the pytest-split duration manifest (tests/.test_durations) against
the currently collected test id set, and checks that the configured splitting
algorithm keeps the shard totals balanced.

The manifest records per-test durations from a real suite run. It must cover the
collected ids; otherwise pytest-split falls back to an even split with no error
beyond a log line, which can leave the slowest shard several times the fastest.
This guard turns that silent fallback into a hard failure.

CLI:
    python scripts/check_shard_manifest.py \
        --manifest tests/.test_durations --collected-file collected.txt \
        --splits 4 --max-ratio 2.0 [--splitting-algorithm duration_based_chunks]

stdlib plus pytest-split.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest_split.algorithms as split_algo

MIN_COVERAGE = 0.95
DEFAULT_MAX_RATIO = 2.0
DEFAULT_SPLITS = 4
DEFAULT_MANIFEST = "tests/.test_durations"
DEFAULT_ALGORITHM = "duration_based_chunks"


class _Item:
    """Lightweight item with nodeid attribute for pytest-split algorithms."""

    __slots__ = ("nodeid",)

    def __init__(self, nodeid: str):
        self.nodeid = nodeid

    def __str__(self):
        return self.nodeid


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


def collected_ids(pytest_args=None) -> list[str]:
    """Collect test node ids by invoking pytest --collect-only.

    Runs: python -m pytest --collect-only -q -p no:cacheprovider <args>
    and returns the lines containing '::' in output order with duplicates
    dropped (first occurrence kept). Default args are
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
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-10:])
        if tail:
            print(f"pytest collection failed (exit {result.returncode}):\n{tail}", file=sys.stderr)
        else:
            print(f"pytest collection failed (exit {result.returncode})", file=sys.stderr)
        raise SystemExit(2)
    return list(dict.fromkeys(
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line
    ))


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


def split_shards(manifest, collected, splits, algorithm_name) -> list[float]:
    """Compute shard totals using pytest-split's algorithm.

    Builds lightweight items in collection order, calls the chosen algorithm,
    and returns the per-shard duration totals.
    """
    algorithm = split_algo.Algorithms[algorithm_name].value
    items = [_Item(nodeid) for nodeid in collected]
    groups = algorithm(splits, items, manifest)
    return [g.duration for g in groups]


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
    parser.add_argument(
        "--splitting-algorithm",
        choices=["duration_based_chunks", "least_duration"],
        default=DEFAULT_ALGORITHM,
    )
    parser.add_argument("--collected-file", default=None)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)

    if args.collected_file:
        collected = list(dict.fromkeys(
            line.strip()
            for line in Path(args.collected_file).read_text().splitlines()
            if "::" in line
        ))
    else:
        collected = collected_ids()

    collected_set = set(collected)
    problems = check_keys(manifest, collected_set, min_coverage=MIN_COVERAGE)

    totals: list[float] = []
    ratio: float | None = None
    if manifest:
        try:
            totals = split_shards(
                manifest, collected, args.splits, args.splitting_algorithm
            )
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
        len(set(manifest) & collected_set) / len(collected_set) * 100 if collected_set else 0.0
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
