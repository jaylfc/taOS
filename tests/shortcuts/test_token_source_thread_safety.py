"""M1 — thread-safety of _cache under concurrent invalidate/read."""
from __future__ import annotations

import subprocess
import threading
import time
from unittest.mock import MagicMock

from tinyagentos.shortcuts.token_source import (
    _cache,
    read_token_source,
    invalidate_agent_cache,
)

AGENT = "thread-test-agent"
OTHER = "other-thread-agent"


def test_concurrent_invalidate_while_reading():
    """invalidate_agent_cache racing with read_token_source must not raise."""
    _cache.clear()
    call_log: list[str] = []
    lock = threading.Lock()

    def fake_run(args, **kwargs):
        name = args[2]
        with lock:
            call_log.append(name)
        time.sleep(0.01)  # simulate slow incus
        return MagicMock(stdout="token\n", returncode=0)

    # Monkey-patch subprocess.run in the module
    import tinyagentos.shortcuts.token_source as ts_mod
    original_run = ts_mod.subprocess.run
    ts_mod.subprocess.run = fake_run  # type: ignore[assignment]

    try:
        source = {"kind": "container_env", "var": "TOK"}

        errors: list[Exception] = []

        def reader():
            try:
                for _ in range(20):
                    read_token_source(AGENT, source)
            except Exception as exc:
                errors.append(exc)

        def invalidator():
            try:
                for _ in range(20):
                    invalidate_agent_cache(AGENT)
                    time.sleep(0.005)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=invalidator) for _ in range(2)]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Thread errors: {errors}"
    finally:
        ts_mod.subprocess.run = original_run  # type: ignore[assignment]
        _cache.clear()
