"""Read a local worker manifest that declares which models this machine
*can* run, independently of whatever is currently loaded in RAM.

The manifest is a simple JSON file (default ``/etc/taos/worker-models.json``,
configurable via ``TAOS_WORKER_MANIFEST``).  Any external platform (Skald,
a custom scheduler, a config-management tool) may write it.  TAOS core
reads it to advertise *available* (not-yet-loaded) models alongside the
*loaded* models discovered by live backend probing.  The controller's
cluster view then knows what a worker *could* load without having to
consult an external catalog.

The file format is::

    {
      "resource_id": "<machine id>",
      "models": [
        {
          "model_id": "<logical name>",
          "software": "llamacpp|embed|kokoro|whisper",
          "port": 9090,
          "vram_required_gb": 5.3,
          "health_url": "http://127.0.0.1:9090/health",
          "capability": "text|embed|tts|asr"
        }
      ]
    }

When the file is absent the worker simply reports an empty available-models
list — no error, no behaviour change for deployments that do not use the
feature.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = "/etc/taos/worker-models.json"

# Map software names to TAOS backend types so the worker can attach manifest
# entries to the correct backend dict.
SOFTWARE_TO_BACKEND_TYPE: dict[str, str] = {
    "llamacpp": "llama-cpp",
    "embed": "llama-cpp",
    "kokoro": "kokoro",
    "whisper": "whisper",
}


def load_manifest(path: str | None = None) -> dict[str, Any]:
    """Read the worker-models manifest from disk.

    Args:
        path: Override path.  Falls back to ``TAOS_WORKER_MANIFEST`` env
              var, then ``/etc/taos/worker-models.json``.

    Returns:
        Dict with keys ``resource_id`` (str) and ``models`` (list of dicts).
        Returns an empty manifest when the file is absent OR unreadable/
        malformed. The manifest is external input (any platform may write
        it), so a typo in it must never take the worker down -- a bad file
        is logged and treated as no-manifest rather than raised.
    """
    manifest_path = Path(path or os.getenv("TAOS_WORKER_MANIFEST", DEFAULT_MANIFEST_PATH))
    empty: dict[str, Any] = {"resource_id": "", "models": []}
    if not manifest_path.exists():
        return empty
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "ignoring malformed worker manifest %s: %s", manifest_path, exc
        )
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("models", []), list):
        logger.warning(
            "ignoring worker manifest %s: top level must be an object with a "
            "'models' list", manifest_path,
        )
        return empty
    return data
