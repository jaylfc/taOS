"""AgentState data model — the framework-agnostic agent definition.

Lives at data/agents/{name}.yaml, owned by the TAOS UI, consumed by the
Reconciler.  See docs/superpowers/specs/2026-04-11-taos-framework-integration-bridge-design.md.

Three rules guiding the schema (enforced here):
1. No file paths.  The Reconciler computes them from ``name`` and the
   host's ``data_dir``.
2. Secrets and config refs are opaque references — the AgentState file
   only carries references; resolution happens at render time.
3. Skills and plugins are separate buckets.  *Skills* are TAOS-generic;
   *Plugins* are framework-specific extensions from the catalog.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════


class RoutingStrategy(str, Enum):
    LATENCY_FIRST = "latency_first"
    COST_FIRST = "cost_first"
    QUALITY_FIRST = "quality_first"
    MANUAL = "manual"


class AgentStatus(str, Enum):
    DEPLOYING = "deploying"
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"


class NetworkMode(str, Enum):
    ALLOWLIST = "allowlist"
    ALL = "all"
    NONE = "none"


class SubprocessMode(str, Enum):
    DENYLIST = "denylist"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ChannelPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    REACT = "react"


class ApprovalRequire(str, Enum):
    SUBPROCESS = "subprocess"
    NETWORK_WRITE = "network_write"


class EmitTarget(str, Enum):
    DASHBOARD = "dashboard"
    PROMETHEUS = "prometheus"


class MemoryAccessMode(str, Enum):
    READ_ONLY = "read-only"


class OnFailure(str, Enum):
    NOTIFY = "notify"


class TaskKind(str, Enum):
    PROMPT = "prompt"


# ═══════════════════════════════════════════════════════════════════════
# Nested models
# ═══════════════════════════════════════════════════════════════════════


class Display(BaseModel):
    color: str = "#5b8def"
    emoji: str = "🤖"


class ModelSlot(BaseModel):
    id: str
    temperature: float = 0.2


class Routing(BaseModel):
    strategy: RoutingStrategy = RoutingStrategy.LATENCY_FIRST
    fallback: str = "chat"


class Models(BaseModel):
    chat: Optional[ModelSlot] = None
    fast: Optional[ModelSlot] = None
    reasoning: Optional[ModelSlot] = None
    embedding: Optional[ModelSlot] = None
    vision: Optional[ModelSlot] = None
    stt: Optional[ModelSlot] = None
    routing: Routing = Field(default_factory=Routing)


class MemoryCollection(BaseModel):
    name: str


class Memory(BaseModel):
    enabled: bool = False
    collections: list[MemoryCollection] = Field(default_factory=list)


class Plugin(BaseModel):
    id: str
    enabled: bool = True
    config_ref: Optional[str] = None


class Channel(BaseModel):
    taos_id: str
    permissions: list[ChannelPermission] = Field(default_factory=list)


class SecretRef(BaseModel):
    id: str


class Resources(BaseModel):
    memory_limit: str = "512MB"
    cpu_limit: int = 1


class AgentMemoryAccess(BaseModel):
    name: str
    mode: MemoryAccessMode = MemoryAccessMode.READ_ONLY
    collections: list[str] = Field(default_factory=list)


class Permissions(BaseModel):
    can_read_user_memory: bool = False
    can_read_agent_memories: list[AgentMemoryAccess] = Field(default_factory=list)
    can_write_agent_memories: list[str] = Field(default_factory=list)
    can_send_email: bool = False
    can_use_browser: bool = False


class Observability(BaseModel):
    tags: dict[str, str] = Field(default_factory=dict)
    trace_sample_rate: float = 1.0
    log_level: LogLevel = LogLevel.INFO
    emit_to: list[EmitTarget] = Field(default_factory=list)


class ScheduleJobTask(BaseModel):
    kind: TaskKind
    prompt: str
    timeout_seconds: int = 600


class ScheduleJob(BaseModel):
    id: str
    cron: str
    task: ScheduleJobTask
    enabled: bool = True
    on_failure: OnFailure = OnFailure.NOTIFY
    max_retries: int = 0


class Schedule(BaseModel):
    enabled: bool = False
    jobs: list[ScheduleJob] = Field(default_factory=list)


class SandboxFilesystem(BaseModel):
    readable: list[str] = Field(default_factory=lambda: ["/workspace", "/memory"])
    writable: list[str] = Field(default_factory=lambda: ["/workspace"])
    denied: list[str] = Field(default_factory=lambda: ["/etc", "/proc", "/sys"])


class SandboxNetwork(BaseModel):
    mode: NetworkMode = NetworkMode.ALLOWLIST
    allow: list[str] = Field(default_factory=list)
    rate_limit_rpm: int = 600


class SandboxSubprocess(BaseModel):
    mode: SubprocessMode = SubprocessMode.DENYLIST
    denied: list[str] = Field(default_factory=lambda: ["rm", "dd", "mkfs", "sudo"])
    timeout_seconds: int = 30


class SandboxResources(BaseModel):
    disk_quota_mb: int = 1024
    max_processes: int = 64
    max_open_files: int = 256


class SandboxApproval(BaseModel):
    require_for: list[ApprovalRequire] = Field(default_factory=list)
    approver: str = ""
    timeout_seconds: int = 300


class Sandbox(BaseModel):
    filesystem: SandboxFilesystem = Field(default_factory=SandboxFilesystem)
    network: SandboxNetwork = Field(default_factory=SandboxNetwork)
    subprocess: SandboxSubprocess = Field(default_factory=SandboxSubprocess)
    resources: SandboxResources = Field(default_factory=SandboxResources)
    approval: SandboxApproval = Field(default_factory=SandboxApproval)


class State(BaseModel):
    status: AgentStatus = AgentStatus.STOPPED
    container_id: str = ""
    last_deployed_at: str = ""
    framework_config_hash: str = ""
    skills_gateway_id: str = ""

    @field_validator("last_deployed_at", mode="before")
    @classmethod
    def _coerce_datetime_to_str(cls, v: Any) -> str:
        """PyYAML parses ISO-8601 timestamps as datetime objects; we want
        storage as plain strings so git diffs stay clean."""
        if isinstance(v, datetime):
            return v.isoformat().replace("+00:00", "Z")
        return str(v) if v is not None else ""


# ═══════════════════════════════════════════════════════════════════════
# Top-level model
# ═══════════════════════════════════════════════════════════════════════


class AgentState(BaseModel):
    """Framework-agnostic agent definition.  Lives at data/agents/{name}.yaml.

    Immutable fields: ``name`` (primary key).  All other fields are
    optional and receive sensible defaults matching the design spec.
    """

    name: str
    framework: str
    framework_version: str

    display: Display = Field(default_factory=Display)
    models: Models = Field(default_factory=Models)
    memory: Memory = Field(default_factory=Memory)
    skills: list[str] = Field(default_factory=list)
    plugins: list[Plugin] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    secrets: list[SecretRef] = Field(default_factory=list)
    resources: Resources = Field(default_factory=Resources)
    permissions: Permissions = Field(default_factory=Permissions)
    observability: Observability = Field(default_factory=Observability)
    schedule: Schedule = Field(default_factory=Schedule)
    sandbox: Sandbox = Field(default_factory=Sandbox)
    state: State = Field(default_factory=State)

    model_config = {"extra": "forbid"}


# ═══════════════════════════════════════════════════════════════════════
# YAML I/O
# ═══════════════════════════════════════════════════════════════════════


def load_agent(path: Path) -> AgentState:
    """Read an AgentState YAML file from disk and return the validated model.

    Args:
        path: Absolute or relative path to the YAML file.

    Returns:
        A fully-validated ``AgentState`` instance.

    Raises:
        FileNotFoundError: The file does not exist.
        yaml.YAMLError: The file is not valid YAML.
        pydantic.ValidationError: The YAML matches the structure but one
            or more fields fail validation.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AgentState.model_validate(raw)


def load_agent_by_name(data_dir: Path, name: str) -> AgentState:
    """Load an agent by name from the standard location.

    This is a convenience wrapper that computes ``data_dir / "agents" /
    "{name}.yaml"`` and delegates to :func:`load_agent`.
    """
    return load_agent(data_dir / "agents" / f"{name}.yaml")


def dump_agent(agent: AgentState, path: Path) -> None:
    """Serialize an AgentState model to YAML and write it to disk.

    Args:
        agent: The validated AgentState to write.
        path: Destination file path (will be overwritten).

    Raises:
        OSError: The file cannot be written.
    """
    raw = agent.model_dump(exclude_unset=False, exclude_defaults=False, mode="json")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def dump_agent_to_string(agent: AgentState) -> str:
    """Serialize to a YAML string (for tests / previews)."""
    raw = agent.model_dump(exclude_unset=False, exclude_defaults=False, mode="json")
    return yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False)
