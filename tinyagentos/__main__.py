"""Module entry: ``python -m tinyagentos``.

Honours ``TAOS_HOST`` / ``TAOS_PORT`` env vars (used by the Mac launcher
to bind to a private 127.0.0.1 port) and falls back to ``data/config.yaml``
when they are unset (preserves the existing console-script behaviour).
"""
from __future__ import annotations

import os
from pathlib import Path

from tinyagentos.app import PROJECT_DIR, create_app, load_config


def main() -> None:
    import uvicorn

    env_host = os.environ.get("TAOS_HOST")
    env_port = os.environ.get("TAOS_PORT")
    env_data_dir = os.environ.get("TAOS_DATA_DIR")

    data_dir = Path(env_data_dir) if env_data_dir else None
    if data_dir is not None:
        _seed_data_dir(data_dir)

    config_path = (data_dir or (PROJECT_DIR / "data")) / "config.yaml"

    if env_host or env_port:
        host = env_host or "127.0.0.1"
        port = int(env_port) if env_port else 6969
    else:
        config = load_config(config_path)
        host = config.server.get("host", "0.0.0.0")
        port = config.server.get("port", 6969)

    app = create_app(data_dir=data_dir)
    # backlog=128 — see issue #323. Keeps the kernel accept queue from
    # silently growing into the thousands if the event loop ever wedges.
    uvicorn.run(app, host=host, port=port, backlog=128)


def _seed_data_dir(target: Path) -> None:
    """Copy bundled data/ skeleton into target on first run.

    Existing files are preserved; only missing ones get copied. This lets the
    embedded server boot in ~/Library/Application Support/taOS without the
    user supplying a config.yaml.
    """
    import shutil

    target.mkdir(parents=True, exist_ok=True)
    source = PROJECT_DIR / "data"
    if not source.exists():
        return
    for entry in source.rglob("*"):
        rel = entry.relative_to(source)
        dest = target / rel
        if entry.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dest)


if __name__ == "__main__":
    main()
