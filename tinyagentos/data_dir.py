"""Shared data directory resolution for taOS."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent


def resolve_data_dir(data_dir: Path | None = None) -> Path:
    """Resolve the taOS data directory.

    Precedence: explicit ``data_dir`` argument > ``TAOS_DATA_DIR`` env var >
    ``<project>/data``.  If both the argument and the env var are set and they
    point at different directories, raise ``ValueError`` so the caller cannot
    silently pick one.
    """
    env_dir = os.environ.get("TAOS_DATA_DIR")
    if env_dir:
        env_path = Path(env_dir)
        if data_dir is not None and data_dir.resolve() != env_path.resolve():
            raise ValueError(
                f"TAOS_DATA_DIR={env_dir} conflicts with data_dir={data_dir}"
            )
        return env_path
    return data_dir or PROJECT_DIR / "data"
