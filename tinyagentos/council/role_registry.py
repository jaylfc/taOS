from __future__ import annotations

import logging
from pathlib import Path

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

ROLE_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS council_roles (
    slug            TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    gauge_status    TEXT NOT NULL,
    suite_version   TEXT,
    builtin         INTEGER NOT NULL DEFAULT 1
);
"""

SEED_ROLES = [
    ("coder", "Coder", "Writes and modifies code within bounded repo tasks.", "proven", "1.0.0"),
    ("reviewer", "Reviewer", "Reviews merged PRs against a ground-truth review.", "proven", "1.0.0"),
    ("writer", "Writer", "Drafts content from briefs with rubric grading.", "designed", "1.0.0"),
    ("editor", "Editor", "Edits flawed drafts for clarity and correctness.", "designed", "1.0.0"),
    ("summarizer", "Summarizer", "Summarizes long documents with fact-recall grading.", "designed", "1.0.0"),
    ("translator", "Translator", "Translates text with round-trip verification.", "designed", "1.0.0"),
    ("researcher", "Researcher", "Answers questions with verifiable research.", "provisional", None),
    ("planner", "Planner", "Breaks goals into actionable task plans.", "provisional", None),
    ("critic", "Critic", "Critiques artifacts against known-flaw lists.", "provisional", None),
    ("data_analyst", "Data analyst", "Answers questions from CSV datasets.", "provisional", None),
]


class RoleRegistry(BaseStore):
    SCHEMA = ROLE_REGISTRY_SCHEMA

    async def _post_init(self) -> None:
        await self._seed()

    async def _seed(self) -> None:
        for slug, display_name, description, gauge_status, suite_version in SEED_ROLES:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO council_roles (slug, display_name, description, gauge_status, suite_version)
                VALUES (?, ?, ?, ?, ?)
                """,
                (slug, display_name, description, gauge_status, suite_version),
            )
        await self._db.commit()

    async def list_roles(self) -> list[dict]:
        async with self._db.execute(
            "SELECT slug, display_name, description, gauge_status, suite_version, builtin FROM council_roles ORDER BY slug"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "slug": r[0],
                "display_name": r[1],
                "description": r[2],
                "gauge_status": r[3],
                "suite_version": r[4],
                "builtin": bool(r[5]),
            }
            for r in rows
        ]

    async def get_role(self, slug: str) -> dict | None:
        async with self._db.execute(
            "SELECT slug, display_name, description, gauge_status, suite_version, builtin FROM council_roles WHERE slug = ?",
            (slug,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "slug": row[0],
            "display_name": row[1],
            "description": row[2],
            "gauge_status": row[3],
            "suite_version": row[4],
            "builtin": bool(row[5]),
        }
