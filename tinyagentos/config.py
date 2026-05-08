from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

VALID_BACKEND_TYPES = {"rkllama", "ollama", "llama-cpp", "vllm", "exo", "mlx", "openai", "anthropic", "sd-cpp", "rknn-sd", "openrouter", "kilocode", "openai-compatible"}

VALID_ON_WORKER_FAILURE = {"pause", "fallback", "escalate-immediately"}

DEFAULT_CONFIG = {
    "server": {"host": "0.0.0.0", "port": 6969},
    "backends": [],
    "qmd": {"url": "http://localhost:7832"},
    "agents": [],
    "metrics": {"poll_interval": 30, "retention_days": 30},
    "webhooks": [],
}

_config_lock = asyncio.Lock()

DEFAULT_ARCHIVE_CONFIG = {
    # Where completed archive snapshots (and optional tarballs) live.
    # "pool:" means the snapshot lives in-pool alongside the container —
    # zero-copy on btrfs/ZFS, full rsync on dir-backed pools.
    # "path:/abs/path" exports an incus tarball to that directory.
    # "s3://bucket" is reserved (not yet implemented; taOS logs + skips).
    "target": "pool:",
}


@dataclass
class AppConfig:
    server: dict = field(default_factory=lambda: DEFAULT_CONFIG["server"].copy())
    backends: list[dict] = field(default_factory=list)
    qmd: dict = field(default_factory=lambda: DEFAULT_CONFIG["qmd"].copy())
    agents: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=lambda: DEFAULT_CONFIG["metrics"].copy())
    webhooks: list[dict] = field(default_factory=list)
    archived_agents: list[dict] = field(default_factory=list)
    archive: dict = field(default_factory=lambda: DEFAULT_ARCHIVE_CONFIG.copy())
    config_path: Path | None = None

    def to_dict(self) -> dict:
        d = {
            "server": self.server,
            "backends": self.backends,
            "qmd": self.qmd,
            "agents": self.agents,
            "metrics": self.metrics,
        }
        if self.webhooks:
            d["webhooks"] = self.webhooks
        if self.archived_agents:
            d["archived_agents"] = self.archived_agents
        archive_target = (self.archive or {}).get("target", "pool:")
        if archive_target != "pool:":
            d["archive"] = self.archive
        return d

def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig(config_path=path)
    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML: expected a mapping at top level")
    agents = data.get("agents", [])
    # Back-fill fields added in the worker-failure-handling update so old
    # config files without them get sensible defaults without error.
    for agent in agents:
        normalize_agent(agent)
    archive_raw = data.get("archive", {})
    archive_cfg = DEFAULT_ARCHIVE_CONFIG.copy()
    if isinstance(archive_raw, dict):
        archive_cfg.update(archive_raw)
    return AppConfig(
        server=data.get("server", DEFAULT_CONFIG["server"].copy()),
        backends=data.get("backends", []),
        qmd=data.get("qmd", DEFAULT_CONFIG["qmd"].copy()),
        agents=agents,
        metrics=data.get("metrics", DEFAULT_CONFIG["metrics"].copy()),
        webhooks=data.get("webhooks", []),
        archived_agents=data.get("archived_agents", []),
        archive=archive_cfg,
        config_path=path,
    )

AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

def validate_agent_name(name: str) -> str | None:
    """Validate agent display name. Accepts any non-empty string up to 64
    characters. The display name is what the user sees; for container and
    path operations, callers should derive a safe slug via
    ``slugify_agent_name``.
    """
    if not name or not name.strip():
        return "Agent name cannot be empty"
    if len(name) > 64:
        return "Agent name must be 64 characters or fewer"
    # Ensure the name produces a non-empty slug — otherwise we can't make
    # a container out of it (e.g. pure emoji or pure whitespace).
    if not slugify_agent_name(name):
        return "Agent name must contain at least one letter or number"
    return None


def slugify_agent_name(name: str) -> str:
    """Derive a container-safe slug from a free-form agent display name.

    Lowercases, replaces any run of non-alphanumeric characters with a
    single hyphen, trims leading/trailing hyphens, and truncates to 63
    chars. Returns an empty string if nothing survives — callers should
    handle that case.

    Examples:
        "Mary's Coding Buddy" -> "mary-s-coding-buddy"
        "🚀 Alpha v2" -> "alpha-v2"
        "Agent_42!" -> "agent-42"
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:63]


def _default_on_worker_failure(agent: dict) -> str:
    """Return the default on_worker_failure policy for an agent dict.

    Defaults to "fallback" when at least one fallback model is configured,
    "pause" otherwise.
    """
    if agent.get("fallback_models"):
        return "fallback"
    return "pause"


def normalize_agent(agent: dict) -> dict:
    """Apply defaults for fields added in successive updates.

    Safe to call on old config dicts that predate any of these fields.
    Mutates and returns the same dict so callers can do::

        normalize_agent(agent_dict)

    or::

        agent = normalize_agent({...})
    """
    import uuid as _uuid
    if not agent.get("id"):
        agent["id"] = _uuid.uuid4().hex[:12]
    if "fallback_models" not in agent:
        agent["fallback_models"] = []
    if "on_worker_failure" not in agent:
        agent["on_worker_failure"] = _default_on_worker_failure(agent)
    if "paused" not in agent:
        agent["paused"] = False
    # KV cache quantization config for this agent's inference calls. Split
    # into separate K and V fields plus a boundary-layer protect count
    # because research (NexusQuant llama.cpp#21591, Ziskind empirical)
    # shows symmetric K/V is a quality landmine and asymmetric Q8K + T3V
    # is the safe default, with Qwen2.5 specifically needing a 2-layer
    # fp16 boundary protection to survive turbo K quants.
    #
    # Defaults to fp16/fp16/0 so old configs and new configs with no
    # explicit preference behave identically. Values are free-form strings,
    # validated by the worker capability probe (the source of truth for
    # what the currently-loaded backend actually supports).
    #
    # The old single kv_cache_quant field is read as both _k and _v to keep
    # rolling updates safe. New writes always use the split fields.
    #
    # TODO: thread these fields through to the inference call path once
    # the first backend with real KV quant support lands. The call site is
    # tinyagentos/clients/ (or wherever the per-agent LLM client is
    # constructed). Track in #144.
    legacy = agent.pop("kv_cache_quant", None)
    if "kv_cache_quant_k" not in agent:
        agent["kv_cache_quant_k"] = legacy if legacy else "fp16"
    if "kv_cache_quant_v" not in agent:
        agent["kv_cache_quant_v"] = legacy if legacy else "fp16"
    if "kv_cache_quant_boundary_layers" not in agent:
        agent["kv_cache_quant_boundary_layers"] = 0
    agent.setdefault("soul_md", "")
    agent.setdefault("agent_md", "")
    agent.setdefault("memory_plugin", "taosmd")
    agent.setdefault("memory_config", None)  # device_id + tier_id; None → use global taosmd_default.json
    agent.setdefault("source_persona_id", None)
    # False for pre-existing rows; new deploys flip to True explicitly.
    agent.setdefault("migrated_to_v2_personas", False)
    agent.setdefault("framework_version_tag", None)
    agent.setdefault("framework_version_sha", None)
    agent.setdefault("framework_update_status", "idle")
    agent.setdefault("framework_update_started_at", None)
    agent.setdefault("framework_update_last_error", None)
    agent.setdefault("framework_last_snapshot", None)
    agent.setdefault("bootstrap_last_seen_at", None)
    return agent

def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".yaml.tmp")
    tmp_path.write_text(yaml.dump(config.to_dict(), default_flow_style=False, sort_keys=False))
    tmp_path.replace(path)

async def save_config_locked(config: AppConfig, path: Path) -> None:
    async with _config_lock:
        save_config(config, path)

def auto_register_from_manifest(manifest_path: Path, config: "AppConfig") -> bool:
    """Read a service manifest and add a backend entry to config if not already present.

    Returns True if a new entry was added, False if already registered.

    Supports two manifest formats:
    - Flat format: top-level ``type`` is the backend type, ``default_url`` is the URL.
    - Catalog format: ``type: service`` with ``lifecycle.backend_type`` and
      ``lifecycle.default_url`` (used by existing app-catalog manifests).
    """
    data = yaml.safe_load(manifest_path.read_text())
    lifecycle = data.get("lifecycle", {})

    # Resolve backend_type and default_url: for catalog manifests (type: service)
    # the actual backend type and URL live under the lifecycle block.
    # For flat manifests the top-level fields are used directly.
    top_type = data.get("type", "")
    is_catalog = top_type == "service"
    backend_type = lifecycle.get("backend_type") or (top_type if not is_catalog else "")
    default_url = (lifecycle.get("default_url") if is_catalog else None) or data.get("default_url", "")
    name = f"local-{data.get('id', backend_type)}"

    # Infrastructure services (e.g. gitea, code-server) have no backend_type — skip them.
    if not backend_type:
        return False

    # Reject backend types we don't have an adapter for. Otherwise the
    # entry survives in config.backends and every health-check round
    # raises ValueError in get_adapter() — a single bad manifest takes
    # the /api/backends endpoint with it. Better to surface this loudly
    # at startup than to limp along with a 500-ing endpoint.
    if backend_type not in VALID_BACKEND_TYPES:
        import logging
        logging.getLogger(__name__).warning(
            "auto_register_from_manifest: skipping %s — backend_type %r is not "
            "in VALID_BACKEND_TYPES %s. Update the manifest's lifecycle.backend_type "
            "or register an adapter in backend_adapters.py.",
            name, backend_type, sorted(VALID_BACKEND_TYPES),
        )
        return False

    if any(b.get("name") == name for b in config.backends):
        return False

    # keep_alive_minutes: 0 means "never auto-stop" (always on).
    # Downstream consumers must check `== 0` or `is not None`, NOT truthiness,
    # because 0 is a valid and intentional value.
    entry: dict = {
        "name": name,
        "type": backend_type,
        "url": default_url,
        "priority": 99,
        "enabled": True,
        "auto_manage": lifecycle.get("auto_manage", False),
        "keep_alive_minutes": lifecycle.get("keep_alive_minutes", 10),
    }
    if lifecycle.get("start_cmd"):
        entry["start_cmd"] = lifecycle["start_cmd"]
    if lifecycle.get("stop_cmd"):
        entry["stop_cmd"] = lifecycle["stop_cmd"]
    if lifecycle.get("startup_timeout_seconds") is not None:
        entry["startup_timeout_seconds"] = lifecycle["startup_timeout_seconds"]

    config.backends.append(entry)
    return True


def validate_config(config: AppConfig) -> list[str]:
    errors = []
    for i, b in enumerate(config.backends):
        if "url" not in b:
            errors.append(f"backends[{i}]: missing 'url'")
        if b.get("type") not in VALID_BACKEND_TYPES:
            errors.append(f"backends[{i}]: invalid type '{b.get('type')}', must be one of {VALID_BACKEND_TYPES}")
    seen_agents = set()
    for i, a in enumerate(config.agents):
        name = a.get("name", "")
        if name in seen_agents:
            errors.append(f"agents[{i}]: duplicate agent name '{name}'")
        seen_agents.add(name)
        owf = a.get("on_worker_failure")
        if owf is not None and owf not in VALID_ON_WORKER_FAILURE:
            errors.append(
                f"agents[{i}]: invalid on_worker_failure '{owf}', "
                f"must be one of {sorted(VALID_ON_WORKER_FAILURE)}"
            )
        fb = a.get("fallback_models")
        if fb is not None and not isinstance(fb, list):
            errors.append(f"agents[{i}]: fallback_models must be a list")
    return errors
