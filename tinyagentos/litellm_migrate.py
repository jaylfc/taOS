"""LiteLLM startup guard for the Postgres/prisma path.

taOS never runs LiteLLM's Postgres-backed virtual-key mode, so this module
no longer shells out to ``prisma generate``.  If a ``.litellm_db_url`` file
is present the operator has explicitly opted into Postgres mode.  taOS
authorizes per-agent keys through its own SQLite keystore, so we log a
warning and continue rather than failing startup.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def migrate(data_dir: Path) -> str:
    """Gate Postgres mode and report status.

    Returns:
        "no-db-configured" — no ``.litellm_db_url`` file, nothing to do.
        "postgres-not-supported" — ``.litellm_db_url`` exists but Postgres
        virtual-key mode is not supported; the keystore is authoritative.
    """
    db_url_path = data_dir / ".litellm_db_url"
    if not db_url_path.exists():
        logger.info("litellm_migrate: no .litellm_db_url — skipping")
        return "no-db-configured"
    db_url = db_url_path.read_text().strip()
    if not db_url:
        logger.info("litellm_migrate: .litellm_db_url empty — skipping")
        return "no-db-configured"

    logger.warning(
        "litellm_migrate: .litellm_db_url is present but Postgres virtual-key "
        "mode is not supported; taOS keystore is authoritative for per-agent "
        "keys. LiteLLM will start without DATABASE_URL."
    )
    return "postgres-not-supported"
