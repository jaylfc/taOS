from __future__ import annotations

import logging
from pathlib import Path

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)


PASSWORD_RESET_SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""


class PasswordResetStore(BaseStore):
    """SQLite-backed store for password-reset tokens.

    Each entry: SHA-256(token_urlsafe(32)) -> user_id + TTL + used flag.
    Server-side TTL: 30 minutes (1800 seconds). Single-use: atomic UPDATE
    WHERE used=0.
    """

    SCHEMA = PASSWORD_RESET_SCHEMA