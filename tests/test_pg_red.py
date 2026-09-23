import asyncio
import tempfile
from pathlib import Path

from tinyagentos.litellm_migrate import migrate


def test_postgres_optin_does_not_hard_fail():
    d = Path(tempfile.mkdtemp())
    (d / ".litellm_db_url").write_text("postgresql://taos:pw@localhost:5432/litellm")
    result = asyncio.run(migrate(d))
    assert result, f"migrate returned {result!r}"
