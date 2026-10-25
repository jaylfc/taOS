from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

BUSCTL_TIMEOUT_S = 10


async def busctl(*args: str) -> tuple[int, object | None]:
    """Run busctl --json=short and return (rc, parsed JSON or None). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "busctl",
            "--json=short",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=BUSCTL_TIMEOUT_S)
        rc = proc.returncode if proc.returncode is not None else 1
        if rc != 0:
            return rc, None
        try:
            data = json.loads((out or b"").decode(errors="replace"))
            return rc, data
        except Exception:
            return 1, None
    except FileNotFoundError:
        return 127, None
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass
        return 124, None
    except Exception:
        return 1, None