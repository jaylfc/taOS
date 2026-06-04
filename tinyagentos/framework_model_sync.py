"""Keep each framework's native model config in sync with taOS's permitted
set and primary model.

Two directions:
- Forward push: when taOS updates an agent's model or permitted set, push
  the change into the framework's config file inside its container so the
  framework's own model picker reflects the new state immediately.
- Reverse reconcile: a background interval reads each framework's live
  primary-model field and updates the taOS record when it changed (the user
  switched the model from the framework's native TUI).

Frameworks supported: openclaw (JSON), hermes (YAML).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# Fixed metadata applied to every entry in OpenClaw's models array.
OPENCLAW_MODEL_DEFAULTS: dict = {
    "contextWindow": 128000,
    "maxTokens": 16384,
    "input": ["text"],
    "reasoning": False,
}

# ---------------------------------------------------------------------------
# Pure functions — fully unit-testable
# ---------------------------------------------------------------------------


def build_openclaw_models(model_ids: list[str]) -> list[dict]:
    """Build the models array for OpenClaw's providers.litellm.models."""
    return [{"id": mid, "name": mid, **OPENCLAW_MODEL_DEFAULTS} for mid in model_ids]


def patch_openclaw_config(cfg: dict, primary: str, permitted: list[str]) -> dict:
    """Mutate *cfg* so it reflects *primary* and *permitted*, then return it.

    Creates any missing nested dicts via setdefault so it works on a minimal
    ``{}`` config as well as a real openclaw.json.
    """
    cfg.setdefault("models", {}).setdefault("providers", {}).setdefault(
        "litellm", {}
    )["models"] = build_openclaw_models(permitted)

    cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault(
        "model", {}
    )["primary"] = f"litellm/{primary}"

    return cfg


def read_openclaw_primary(cfg: dict) -> str | None:
    """Return the bare model id from ``agents.defaults.model.primary``.

    Strips a leading ``litellm/`` prefix if present.  Returns None when the
    key path is absent.
    """
    try:
        raw = cfg["agents"]["defaults"]["model"]["primary"]
    except (KeyError, TypeError):
        return None
    if isinstance(raw, str) and raw.startswith("litellm/"):
        return raw[len("litellm/"):]
    return raw if isinstance(raw, str) else None


def patch_hermes_default(text: str, primary: str) -> str:
    """Return *text* with the ``default:`` line inside the top-level
    ``model:`` block replaced to *primary*.

    Line-oriented: preserves every other line (including comments).
    If no ``model.default`` line exists the text is returned unchanged.
    """
    lines = text.splitlines(keepends=True)
    in_model_block = False
    result = []
    for line in lines:
        if re.match(r"^model:\s*$", line):
            in_model_block = True
            result.append(line)
            continue
        # A non-whitespace-leading line (other than the first) closes the block.
        if in_model_block and line and not line[0].isspace():
            in_model_block = False
        if in_model_block and re.match(r"^(\s+)default:\s*\S*", line):
            # Replace only the value; preserve indent and key name.
            line = re.sub(r"^(\s+default:\s*)\S*", rf"\g<1>{primary}", line)
        result.append(line)
    return "".join(result)


def read_hermes_default(text: str) -> str | None:
    """Parse the ``default:`` value from the top-level ``model:`` block.

    Returns None when absent.
    """
    in_model_block = False
    for line in text.splitlines():
        if re.match(r"^model:\s*$", line):
            in_model_block = True
            continue
        if in_model_block and line and not line[0].isspace():
            # Left the model block without finding default
            break
        if in_model_block:
            m = re.match(r"^\s+default:\s*(\S+)", line)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Container I/O — async, guarded, NEVER raise
# ---------------------------------------------------------------------------


async def push_model_config_to_framework(
    slug: str, framework: str, primary: str, permitted: list[str]
) -> bool:
    """Push the current model config into the framework's config file.

    Returns True on success, False on any error (always guarded).
    """
    from tinyagentos.containers import exec_in_container, push_file

    container = f"taos-agent-{slug}"

    try:
        if framework == "openclaw":
            rc, out = await exec_in_container(
                container, ["cat", "/root/.openclaw/openclaw.json"]
            )
            if rc != 0:
                logger.warning(
                    "model sync: cannot read openclaw.json from %s (rc=%d): %s",
                    container, rc, out,
                )
                return False
            try:
                cfg = json.loads(out)
            except json.JSONDecodeError as exc:
                logger.warning("model sync: invalid JSON in openclaw.json for %s: %s", container, exc)
                return False
            patch_openclaw_config(cfg, primary, permitted)
            data = json.dumps(cfg, indent=2)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                rc, out = await push_file(container, tmp_path, "/root/.openclaw/openclaw.json")
                return rc == 0
            finally:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        elif framework == "hermes":
            rc, out = await exec_in_container(
                container, ["cat", "/root/.hermes/config.yaml"]
            )
            if rc != 0:
                logger.warning(
                    "model sync: cannot read config.yaml from %s (rc=%d): %s",
                    container, rc, out,
                )
                return False
            new_text = patch_hermes_default(out, primary)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as tmp:
                tmp.write(new_text)
                tmp_path = tmp.name
            try:
                rc, out = await push_file(container, tmp_path, "/root/.hermes/config.yaml")
                return rc == 0
            finally:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        else:
            return False

    except Exception:
        logger.exception(
            "model sync: push_model_config_to_framework failed for %s/%s",
            slug, framework,
        )
        return False


async def read_framework_primary(slug: str, framework: str) -> str | None:
    """Read the live primary model id from the framework's config.

    Returns None on any error.
    """
    from tinyagentos.containers import exec_in_container

    container = f"taos-agent-{slug}"
    try:
        if framework == "openclaw":
            rc, out = await exec_in_container(
                container, ["cat", "/root/.openclaw/openclaw.json"]
            )
            if rc != 0:
                return None
            try:
                cfg = json.loads(out)
            except json.JSONDecodeError:
                return None
            return read_openclaw_primary(cfg)

        elif framework == "hermes":
            rc, out = await exec_in_container(
                container, ["cat", "/root/.hermes/config.yaml"]
            )
            if rc != 0:
                return None
            return read_hermes_default(out)

        else:
            return None

    except Exception:
        logger.exception(
            "model sync: read_framework_primary failed for %s/%s", slug, framework
        )
        return None


# ---------------------------------------------------------------------------
# Interval reconciler — reverse sync (framework → taOS)
# ---------------------------------------------------------------------------

RECONCILE_INTERVAL = 60.0
RECONCILE_INITIAL_DELAY = 30.0


class FrameworkModelReconciler:
    """Background task: reads each framework's live primary model and
    updates the taOS agent record when it changed (the user switched
    the model in the framework's native TUI).

    Mirrors the lifecycle of CloudProviderRefresher (start/stop/loop).
    """

    def __init__(
        self,
        app_state,
        interval: float = RECONCILE_INTERVAL,
        initial_delay: float = RECONCILE_INITIAL_DELAY,
    ):
        self._state = app_state
        self._interval = interval
        self._initial_delay = initial_delay
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._loop(), name="framework-model-reconciler"
        )
        logger.info(
            "FrameworkModelReconciler started (interval=%ds)", self._interval
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._initial_delay)
            return  # stopped during initial delay
        except asyncio.TimeoutError:
            pass
        while True:
            try:
                await self._reconcile_once()
            except Exception:
                logger.exception("framework model reconciler iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                pass  # next cycle

    async def _reconcile_once(self) -> None:
        from tinyagentos.config import save_config_locked

        config = getattr(self._state, "config", None)
        if config is None:
            return

        changed: list[str] = []
        for agent in config.agents:
            framework = agent.get("framework")
            if framework not in ("openclaw", "hermes"):
                continue
            slug = agent.get("name")
            if not slug:
                continue
            try:
                live = await read_framework_primary(slug, framework)
            except Exception:
                logger.exception(
                    "framework reconciler: read_framework_primary failed for %s", slug
                )
                continue
            if live and live != agent.get("model"):
                old = agent.get("model")
                agent["model"] = live
                # Ensure the new primary is in the permitted set.
                permitted = agent.get("permitted_models") or []
                if live not in permitted:
                    agent["permitted_models"] = [live, *permitted]
                changed.append(slug)
                logger.info(
                    "framework reconciler: %s model updated %s → %s",
                    slug, old, live,
                )

        if changed and config.config_path:
            await save_config_locked(config, config.config_path)
            logger.info(
                "framework reconciler: saved config after updating %d agent(s): %s",
                len(changed), changed,
            )
