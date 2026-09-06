"""One-off: repair existing cloud-provider entries that were saved
before the add-provider autofill/discovery landed.

For every backend in ``data/config.yaml`` whose ``type`` is a known
cloud type, ensures ``url`` is populated from the canonical catalog
when missing, and ensures ``models`` is populated by probing
``{url}/models`` (falling back to the per-type seed list if the probe
returns nothing).

Idempotent — safe to run repeatedly. Writes the config back in place
with a sibling ``.bak`` of the previous version for manual rollback.

Usage (from repo root):
    python3 scripts/repair_providers.py [--config data/config.yaml]
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import httpx
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("repair_providers")

# Kept in sync with tinyagentos.routes.providers. Duplicated here so
# this script can run from a plain venv without importing the app.
CLOUD_TYPES = {"openai", "anthropic", "openrouter", "kilocode"}

URL_DEFAULTS = {
    "kilocode": "https://api.kilo.ai/api/gateway",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

SEED_MODELS = {
    "kilocode": [{"id": "kilo-auto/free"}],
}


def _load_secret(data_dir: Path, secret_name: str) -> str | None:
    """Read an api key out of the secrets sqlite for probes that need
    auth. Best-effort — returns None on any failure (missing DB,
    encryption key not available, etc.) which is fine because most
    cloud /models endpoints respond 200 without auth."""
    if not secret_name:
        return None
    try:
        import sqlite3

        db = data_dir / "secrets.db"
        if not db.exists():
            return None
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT value FROM secrets WHERE name = ?", (secret_name,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        # Values are stored encrypted via tinyagentos.secrets._encrypt.
        # Decrypting here requires the app's key — skip and let the
        # unauthed probe handle it.
        return None
    except Exception:
        return None


def _probe_models(base_url: str, api_key: str | None) -> list[dict]:
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
    except Exception as exc:
        logger.warning("probe %s failed: %s", url, exc)
        return []
    if resp.status_code != 200:
        logger.warning("probe %s returned HTTP %d", url, resp.status_code)
        return []
    try:
        data = resp.json().get("data", [])
    except Exception:
        return []
    return [
        {"id": m["id"]}
        for m in data
        if isinstance(m, dict) and m.get("id")
    ]


def repair(config_path: Path) -> int:
    if not config_path.exists():
        logger.error("config not found: %s", config_path)
        return 1
    raw = yaml.safe_load(config_path.read_text()) or {}
    backends = raw.get("backends", [])
    data_dir = config_path.parent
    changed = 0

    for backend in backends:
        btype = backend.get("type")
        if btype not in CLOUD_TYPES:
            continue
        before = dict(backend)

        if not backend.get("url") and btype in URL_DEFAULTS:
            backend["url"] = URL_DEFAULTS[btype]
            logger.info("backend=%s filled url=%s", backend.get("name"), backend["url"])

        if not backend.get("models") and backend.get("url"):
            api_key = _load_secret(data_dir, backend.get("api_key_secret", ""))
            discovered = _probe_models(backend["url"], api_key)
            if discovered:
                backend["models"] = discovered
                logger.info(
                    "backend=%s discovered %d models via %s/models",
                    backend.get("name"), len(discovered), backend["url"],
                )
            elif btype in SEED_MODELS:
                backend["models"] = list(SEED_MODELS[btype])
                logger.info(
                    "backend=%s probe empty — seeded %s",
                    backend.get("name"), backend["models"],
                )
            else:
                logger.warning(
                    "backend=%s probe empty and no seed — user must add models manually",
                    backend.get("name"),
                )
        if backend != before:
            changed += 1

    if changed == 0:
        logger.info("nothing to repair — all cloud backends already have url+models")
        return 0

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy(config_path, backup)
    logger.info("backed up previous config to %s", backup)
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    logger.info("wrote %d repaired backend(s) to %s", changed, config_path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        default="data/config.yaml",
        help="Path to taOS config.yaml (default: data/config.yaml)",
    )
    args = ap.parse_args()
    return repair(Path(args.config).resolve())


if __name__ == "__main__":
    sys.exit(main())
