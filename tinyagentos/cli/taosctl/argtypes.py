"""Shared argparse `type=` validators for taosctl command groups.

Pagination arguments (`--limit`, `--offset`) are uniform across the command
groups: a limit must be a positive count and an offset a non-negative index.
Validating at parse time rejects 0/negative input in the CLI instead of
forwarding a guaranteed-invalid value to the server.
"""
from __future__ import annotations

import argparse


def positive_int(value: str) -> int:
    """An integer > 0 (e.g. a result limit). Rejects 0 and negatives."""
    iv = int(value)
    if iv <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return iv


def nonneg_int(value: str) -> int:
    """An integer >= 0 (e.g. a pagination offset). Rejects negatives."""
    iv = int(value)
    if iv < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return iv
