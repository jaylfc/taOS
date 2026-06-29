"""Action receipts: the per-action audit record at the OS boundary (#155).

A receipt is the portable, append-only record of a single agent action. It does
NOT duplicate the lower-level records other stores already keep; it LINKS them
(trace_id -> trace_store, board_audit_event_id -> board_audit, decision_id ->
decisions) and adds the per-action fields that no single existing store owns:
the canonical agent identity, the project/workspace hash, the allowed
capability used, input refs/hashes, the tool call requested, the output ref,
files changed, the stop reason, redactions, and whether a human approved a side
effect.

Like board_audit, this is append-only: there is deliberately no public update or
delete method. That is the seed of the replayable, auditable "time machine"
record (#103), and the reason the receipt belongs at the OS boundary rather than
in any one agent harness. Returned in insertion order (SQLite rowid) so history
is stable even when two receipts share a timestamp.

This module is the storage layer (slice 1). Automatic emission from the
fs-tools/tool-call loop, workspace/input hashing for replay, and the UI are
later slices; nothing writes receipts automatically yet, so adding this changes
no live behaviour.
"""

from __future__ import annotations

import json
import logging
import secrets
import time

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

RECEIPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id                   TEXT PRIMARY KEY,
    agent_canonical_id   TEXT NOT NULL,
    handle               TEXT NOT NULL DEFAULT '',
    project_id           TEXT NOT NULL DEFAULT '',
    workspace_hash       TEXT NOT NULL DEFAULT '',
    capability           TEXT NOT NULL DEFAULT '',
    capability_granted_at TEXT,
    tool_name            TEXT NOT NULL DEFAULT '',
    tool_args            TEXT NOT NULL DEFAULT '{}',
    input_refs           TEXT NOT NULL DEFAULT '[]',
    output_ref           TEXT NOT NULL DEFAULT '',
    result_summary       TEXT NOT NULL DEFAULT '',
    files_changed        TEXT NOT NULL DEFAULT '[]',
    stop_reason          TEXT NOT NULL DEFAULT '',
    redactions           TEXT NOT NULL DEFAULT '[]',
    human_approval       TEXT,
    trace_id             TEXT NOT NULL DEFAULT '',
    board_audit_event_id TEXT NOT NULL DEFAULT '',
    decision_id          TEXT NOT NULL DEFAULT '',
    created_at           REAL NOT NULL,
    created_by_user_id   TEXT NOT NULL DEFAULT '',
    metadata             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_receipts_agent ON receipts(agent_canonical_id);
CREATE INDEX IF NOT EXISTS idx_receipts_project ON receipts(project_id);
CREATE INDEX IF NOT EXISTS idx_receipts_trace ON receipts(trace_id);
CREATE INDEX IF NOT EXISTS idx_receipts_decision ON receipts(decision_id);
CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(created_by_user_id);
"""

_COLS = (
    "id, agent_canonical_id, handle, project_id, workspace_hash, capability, "
    "capability_granted_at, tool_name, tool_args, input_refs, output_ref, "
    "result_summary, files_changed, stop_reason, redactions, human_approval, "
    "trace_id, board_audit_event_id, decision_id, created_at, created_by_user_id, metadata"
)

# Fields persisted as JSON text and parsed back on read.
_JSON_FIELDS = ("tool_args", "input_refs", "files_changed", "redactions", "human_approval", "metadata")


def _new_id() -> str:
    return "rct-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _row(r) -> dict:
    d = {
        "id": r[0], "agent_canonical_id": r[1], "handle": r[2], "project_id": r[3],
        "workspace_hash": r[4], "capability": r[5], "capability_granted_at": r[6],
        "tool_name": r[7], "tool_args": r[8], "input_refs": r[9], "output_ref": r[10],
        "result_summary": r[11], "files_changed": r[12], "stop_reason": r[13],
        "redactions": r[14], "human_approval": r[15], "trace_id": r[16],
        "board_audit_event_id": r[17], "decision_id": r[18], "created_at": r[19],
        "created_by_user_id": r[20], "metadata": r[21],
    }
    for f in _JSON_FIELDS:
        if d.get(f) is not None:
            try:
                d[f] = json.loads(d[f])
            except (TypeError, ValueError):
                # An audit ledger must surface corruption, not silently pass a
                # malformed entry off as valid data. Keep the raw value (lossless)
                # but flag it so the bad row is visible, and log it.
                logger.warning("receipt %s: unparseable JSON in field %r", d.get("id"), f)
                d[f] = {"_unparsed": d[f]}
    return d


class ReceiptStore(BaseStore):
    """Append-only store of per-action receipts (#155).

    No public update or delete: a receipt, once written, is immutable history.
    Everything is link-by-id to the stores that own the underlying detail, so a
    receipt stays small and portable while remaining auditable.
    """

    SCHEMA = RECEIPTS_SCHEMA

    async def record(
        self,
        agent_canonical_id: str,
        *,
        handle: str = "",
        project_id: str = "",
        workspace_hash: str = "",
        capability: str = "",
        capability_granted_at: str | None = None,
        tool_name: str = "",
        tool_args: dict | None = None,
        input_refs: list | None = None,
        output_ref: str = "",
        result_summary: str = "",
        files_changed: list | None = None,
        stop_reason: str = "",
        redactions: list | None = None,
        human_approval: dict | None = None,
        trace_id: str = "",
        board_audit_event_id: str = "",
        decision_id: str = "",
        created_by_user_id: str = "",
        metadata: dict | None = None,
    ) -> str:
        """Append a receipt. Returns the receipt id. agent_canonical_id is the
        only required field, since a receipt must always attribute to a stable
        subject; everything else is optional so partial receipts are still valid
        history (the capture hooks fill in what each call site knows)."""
        if not agent_canonical_id:
            raise ValueError("agent_canonical_id is required")
        rid = _new_id()
        now = time.time()
        await self._db.execute(
            f"INSERT INTO receipts ({_COLS}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rid, agent_canonical_id, handle, project_id, workspace_hash, capability,
                capability_granted_at, tool_name, json.dumps(tool_args or {}),
                json.dumps(input_refs or []), output_ref, result_summary,
                json.dumps(files_changed or []), stop_reason, json.dumps(redactions or []),
                json.dumps(human_approval) if human_approval is not None else None,
                trace_id, board_audit_event_id, decision_id, now, created_by_user_id,
                json.dumps(metadata or {}),
            ),
        )
        await self._db.commit()
        return rid

    async def get(self, receipt_id: str) -> dict | None:
        async with self._db.execute(
            f"SELECT {_COLS} FROM receipts WHERE id = ?", (receipt_id,)
        ) as cur:
            r = await cur.fetchone()
        return _row(r) if r else None

    async def list(
        self,
        *,
        agent_canonical_id: str | None = None,
        project_id: str | None = None,
        trace_id: str | None = None,
        created_by_user_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Newest-first receipts (insertion order via rowid), optionally filtered.
        The result set is bounded so a long-lived ledger cannot return everything."""
        conds, params = [], []
        if agent_canonical_id is not None:
            conds.append("agent_canonical_id = ?"); params.append(agent_canonical_id)
        if project_id is not None:
            conds.append("project_id = ?"); params.append(project_id)
        if trace_id is not None:
            conds.append("trace_id = ?"); params.append(trace_id)
        if created_by_user_id is not None:
            conds.append("created_by_user_id = ?"); params.append(created_by_user_id)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        limit = max(1, min(int(limit), 1000))
        async with self._db.execute(
            f"SELECT {_COLS} FROM receipts{where} ORDER BY rowid DESC LIMIT ?",
            [*params, limit],
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]
