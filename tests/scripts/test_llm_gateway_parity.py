"""scripts/llm_gateway_parity.py: counting llm_call trace rows per agent."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "llm_gateway_parity.py"


@pytest.fixture(scope="module")
def parity():
    spec = importlib.util.spec_from_file_location("llm_gateway_parity", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["llm_gateway_parity"] = mod
    spec.loader.exec_module(mod)
    return mod


def _bucket(path: Path, llm_calls: int) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trace_events (id INTEGER PRIMARY KEY, kind TEXT)")
        conn.executemany("INSERT INTO trace_events (kind) VALUES (?)",
                         [("llm_call",)] * llm_calls + [("tool_call",)])


def test_legacy_bucket_without_trace_events_counts_zero(parity, tmp_path):
    """A pre-trace_events hourly bucket (live on the Pi: naira's
    2026-06-11T17.db) must not blank out the agent's real rows."""
    agent_dir = tmp_path / "trace" / "naira"
    agent_dir.mkdir(parents=True)
    with sqlite3.connect(agent_dir / "2026-06-11T17.db") as conn:
        conn.execute("CREATE TABLE something_else (x INTEGER)")
    _bucket(agent_dir / "2026-09-30T21.db", 2)
    assert parity._trace_calls(tmp_path, "naira") == 2


def test_unreadable_bucket_is_still_unknown(parity, tmp_path):
    """Any other sqlite error is unknown, not zero."""
    agent_dir = tmp_path / "trace" / "naira"
    agent_dir.mkdir(parents=True)
    _bucket(agent_dir / "2026-09-30T21.db", 2)
    (agent_dir / "2026-09-30T22.db").write_bytes(b"this is not a sqlite database" * 100)
    assert parity._trace_calls(tmp_path, "naira") is None
