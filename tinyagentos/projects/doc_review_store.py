from __future__ import annotations

import time

from tinyagentos.projects.ids import new_id
from tinyagentos.projects.tx import ProjectsDBStore

DOC_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    doc_path TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'awaiting_review',
    reviewed_by TEXT,
    reviewed_at REAL,
    changes_requested_by TEXT,
    changes_requested_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_reviews_project_path
    ON doc_reviews(project_id, doc_path);
CREATE INDEX IF NOT EXISTS idx_doc_reviews_state
    ON doc_reviews(project_id, review_state);
"""

VALID_TRANSITIONS: dict[str, list[str]] = {
    "awaiting_review": ["approved", "changes_requested"],
    "changes_requested": ["awaiting_review"],
    "approved": ["awaiting_review"],
}


class DocReviewStore(ProjectsDBStore):
    SCHEMA = DOC_REVIEW_SCHEMA

    def _row_to_review(self, row, description) -> dict:
        keys = [d[0] for d in description]
        return dict(zip(keys, row))

    async def get_review(self, project_id: str, doc_path: str) -> dict | None:
        async with self._read(
            "SELECT * FROM doc_reviews WHERE project_id = ? AND doc_path = ?",
            (project_id, doc_path),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return self._row_to_review(row, cur.description)

    async def set_review_state(
        self,
        project_id: str,
        doc_path: str,
        new_state: str,
        actor_id: str,
    ) -> dict:
        if new_state not in VALID_TRANSITIONS:
            raise ValueError(f"invalid review state: {new_state}")

        now = time.time()
        # Read the current state INSIDE the transaction that writes the next
        # one.  Validated outside it, two concurrent transitions both saw
        # `awaiting_review`, both passed the check, and the second landed a
        # transition that was never legal from the state it actually met --
        # `approved` overwritten by `changes_requested`, say.  BEGIN IMMEDIATE
        # holds the write lock across the read, so the second caller reads what
        # the first committed and is refused.
        async with self._tx():
            existing = await self.get_review(project_id, doc_path)

            if existing is None:
                if new_state != "awaiting_review" and new_state not in VALID_TRANSITIONS.get("awaiting_review", []):
                    raise ValueError(
                        f"invalid transition: (new) -> {new_state}; "
                        f"first state must be awaiting_review or a direct transition target"
                    )
                review_id = new_id("rev")
                reviewed_by = None
                reviewed_at = None
                changes_requested_by = None
                changes_requested_at = None
                if new_state == "approved":
                    reviewed_by = actor_id
                    reviewed_at = now
                elif new_state == "changes_requested":
                    changes_requested_by = actor_id
                    changes_requested_at = now
                await self._db.execute(
                    """INSERT INTO doc_reviews
                       (id, project_id, doc_path, review_state,
                        reviewed_by, reviewed_at,
                        changes_requested_by, changes_requested_at,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        review_id, project_id, doc_path, new_state,
                        reviewed_by, reviewed_at,
                        changes_requested_by, changes_requested_at,
                        now, now,
                    ),
                )
            else:
                current_state = existing["review_state"]
                allowed = VALID_TRANSITIONS.get(current_state, [])
                if new_state not in allowed:
                    raise ValueError(
                        f"invalid transition: {current_state} -> {new_state}"
                    )

                sets: list[str] = ["review_state = ?", "updated_at = ?"]
                params: list = [new_state, now]

                if new_state == "approved":
                    sets.append("reviewed_by = ?")
                    sets.append("reviewed_at = ?")
                    params.extend([actor_id, now])
                elif new_state == "changes_requested":
                    sets.append("changes_requested_by = ?")
                    sets.append("changes_requested_at = ?")
                    params.extend([actor_id, now])

                params.extend([project_id, doc_path])
                await self._db.execute(
                    f"UPDATE doc_reviews SET {', '.join(sets)} WHERE project_id = ? AND doc_path = ?",
                    params,
                )
        return await self.get_review(project_id, doc_path)

    async def list_reviews(
        self, project_id: str, *, state: str | None = None
    ) -> list[dict]:
        if state is not None:
            async with self._read(
                "SELECT * FROM doc_reviews WHERE project_id = ? AND review_state = ? ORDER BY doc_path",
                (project_id, state),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._read(
                "SELECT * FROM doc_reviews WHERE project_id = ? ORDER BY doc_path",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_review(r, cur.description) for r in rows]

    async def delete_review(self, project_id: str, doc_path: str) -> bool:
        async with self._tx():
            async with self._db.execute(
                "DELETE FROM doc_reviews WHERE project_id = ? AND doc_path = ?",
                (project_id, doc_path),
            ) as cur:
                deleted = cur.rowcount > 0
        return deleted
