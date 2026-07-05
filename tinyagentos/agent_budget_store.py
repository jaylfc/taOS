"""Cross-process per-agent LLM spend/budget store.

Mirrors ``tinyagentos.litellm_keystore`` in shape: raw sqlite3 with WAL so
the controller (writer) and the LiteLLM auth/callback hooks running in the
proxy subprocess (reader/writer) can share one file with no server process
and no prisma. This is deliberately its own store rather than an extension
of the key store: keys and budgets have different lifecycles (a key is
re-minted on re-scope; a budget persists and accumulates spend across many
keys/agent restarts).

Scope: set/get a cap, accumulate spend, check whether an agent is over
budget, reset spend. No period-based auto-reset and no auto-pause — those
are explicit follow-ups.
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_budgets (
    agent           TEXT PRIMARY KEY,
    max_budget_usd  REAL,
    spend_usd       REAL NOT NULL DEFAULT 0,
    updated_ts      REAL NOT NULL
);
"""


class AgentBudgetStore:
    """SQLite-backed per-agent budget store, safe for cross-process read/write.

    The controller sets/reads caps and can reset spend; the LiteLLM
    subprocess's callback increments spend on every completion and the auth
    hook reads ``is_over_budget`` before allowing a call through. WAL mode
    keeps a concurrent reader from blocking on a writer.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def get(self, agent: str) -> dict | None:
        """Return ``{agent, max_budget_usd, spend_usd}``, or None if unset."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT agent, max_budget_usd, spend_usd FROM agent_budgets WHERE agent = ?",
                (agent,),
            ).fetchone()
        if row is None:
            return None
        return {
            "agent": row["agent"],
            "max_budget_usd": row["max_budget_usd"],
            "spend_usd": row["spend_usd"],
        }

    def set_budget(self, agent: str, max_budget_usd: float | None) -> None:
        """Upsert an agent's cap. ``None`` clears it (unlimited).

        Preserves existing spend_usd; creates the row with spend_usd=0 if
        the agent has no row yet. A non-finite cap (NaN/inf) is rejected: a
        NaN cap would make ``is_over_budget`` always False and silently
        disable enforcement, so the store refuses to persist one.
        """
        if max_budget_usd is not None and not math.isfinite(max_budget_usd):
            raise ValueError("max_budget_usd must be finite or None")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_budgets (agent, max_budget_usd, spend_usd, updated_ts)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(agent) DO UPDATE SET
                    max_budget_usd = excluded.max_budget_usd,
                    updated_ts = excluded.updated_ts
                """,
                (agent, max_budget_usd, now),
            )

    def add_spend(self, agent: str, delta_usd: float) -> float:
        """Atomically increment an agent's spend by ``delta_usd``.

        Creates the row (max_budget_usd=NULL, i.e. unlimited) if absent.
        Non-positive deltas are ignored; returns the current spend (0 if no
        row exists) without writing.
        """
        if delta_usd <= 0:
            existing = self.get(agent)
            return existing["spend_usd"] if existing else 0.0
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_budgets (agent, max_budget_usd, spend_usd, updated_ts)
                VALUES (?, NULL, ?, ?)
                ON CONFLICT(agent) DO UPDATE SET
                    spend_usd = agent_budgets.spend_usd + excluded.spend_usd,
                    updated_ts = excluded.updated_ts
                """,
                (agent, delta_usd, now),
            )
            row = conn.execute(
                "SELECT spend_usd FROM agent_budgets WHERE agent = ?",
                (agent,),
            ).fetchone()
        return row["spend_usd"]

    def reset_spend(self, agent: str) -> None:
        """Zero an agent's spend, keeping its cap. No-op if no row exists."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_budgets SET spend_usd = 0, updated_ts = ? WHERE agent = ?",
                (time.time(), agent),
            )

    def is_over_budget(self, agent: str) -> bool:
        """True iff a row exists, has a cap set, and spend has reached it."""
        rec = self.get(agent)
        if rec is None or rec["max_budget_usd"] is None:
            return False
        return rec["spend_usd"] >= rec["max_budget_usd"]

    def list(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent, max_budget_usd, spend_usd FROM agent_budgets ORDER BY agent"
            ).fetchall()
        return [
            {"agent": r["agent"], "max_budget_usd": r["max_budget_usd"], "spend_usd": r["spend_usd"]}
            for r in rows
        ]

    def delete_agent(self, agent: str) -> bool:
        """Remove an agent's budget row. Returns True if it existed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM agent_budgets WHERE agent = ?", (agent,))
            return cur.rowcount > 0


def default_budget_path(data_dir: str | Path) -> Path:
    """Canonical budget store path for a controller data dir."""
    return Path(data_dir) / ".agent_budgets.db"
