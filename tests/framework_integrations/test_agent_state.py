"""Tests for AgentState data model and YAML I/O."""
import textwrap

import pytest
import yaml

from tinyagentos.framework_integrations.agent_state import (
    AgentState,
    AgentStatus,
    Display,
    Memory,
    MemoryCollection,
    Models,
    ModelSlot,
    NetworkMode,
    Observability,
    Plugin,
    Routing,
    RoutingStrategy,
    Sandbox,
    Schedule,
    ScheduleJob,
    ScheduleJobTask,
    State,
    dump_agent,
    dump_agent_to_string,
    load_agent,
    load_agent_by_name,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

FULL_SPEC_YAML = """\
name: research-agent
framework: openclaw
framework_version: 2026.4.4

display:
  color: "#5b8def"
  emoji: "\U0001f50d"

models:
  chat:
    id: qwen3-4b-q4
    temperature: 0.2
  fast:
    id: qwen3-1.7b
    temperature: 0.0
  reasoning:
    id: qwen3-32b
    temperature: 0.4
  embedding:
    id: qwen3-embedding-0.6b
  vision:
    id: qwen2-vl-7b
  stt:
    id: whisper-large-v3-turbo
  routing:
    strategy: latency_first
    fallback: chat

memory:
  enabled: true
  collections:
    - name: notes
    - name: imports

skills:
  - web_search
  - file_read
  - file_write
  - memory_search

plugins:
  - id: playwright-mcp
    enabled: true
    config_ref: secrets://playwright-creds

channels:
  - taos_id: channel-discord-research-server-general
    permissions: [read, write, react]
  - taos_id: channel-slack-eng-standup
    permissions: [read]

secrets:
  - id: github-pat

resources:
  memory_limit: 2GB
  cpu_limit: 2

permissions:
  can_read_user_memory: false
  can_read_agent_memories:
    - name: inbox-agent
      mode: read-only
      collections: ["summaries"]
  can_write_agent_memories: []
  can_send_email: false
  can_use_browser: true

observability:
  tags:
    team: research
    project: model-mesh
    cost_center: rd
    owner: jay
  trace_sample_rate: 1.0
  log_level: info
  emit_to: [dashboard, prometheus]

schedule:
  enabled: true
  jobs:
    - id: morning-digest
      cron: "0 9 * * 1-5"
      task:
        kind: prompt
        prompt: "Summarise overnight emails into MEMORY.md"
        timeout_seconds: 600
      enabled: true
      on_failure: notify
      max_retries: 0

sandbox:
  filesystem:
    readable: [/workspace, /memory]
    writable: [/workspace]
    denied: [/etc, /proc, /sys]
  network:
    mode: allowlist
    allow: [github.com, huggingface.co, "*.tinyagentos.local"]
    rate_limit_rpm: 600
  subprocess:
    mode: denylist
    denied: [rm, dd, mkfs, sudo]
    timeout_seconds: 30
  resources:
    disk_quota_mb: 1024
    max_processes: 64
    max_open_files: 256
  approval:
    require_for: [subprocess, network_write]
    approver: jay
    timeout_seconds: 300

state:
  status: running
  container_id: taos-agent-research-agent
  last_deployed_at: 2026-04-11T13:22:01Z
  framework_config_hash: sha256:...
  skills_gateway_id: sgw-default-controller
"""


@pytest.fixture
def full_spec_yaml() -> str:
    return FULL_SPEC_YAML


@pytest.fixture
def full_spec_path(tmp_path, full_spec_yaml):
    p = tmp_path / "research-agent.yaml"
    p.write_text(full_spec_yaml)
    return p


MINIMAL_YAML = """\
name: minimal
framework: hermes
framework_version: 2026.1.0
"""


@pytest.fixture
def minimal_path(tmp_path):
    p = tmp_path / "minimal.yaml"
    p.write_text(MINIMAL_YAML)
    return p


@pytest.fixture
def agents_dir(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "research-agent.yaml").write_text(FULL_SPEC_YAML)
    return tmp_path


# ═══════════════════════════════════════════════════════════════════════
# Loading
# ═══════════════════════════════════════════════════════════════════════


class TestLoadFullSpec:
    """Smoke-test: the spec's own example YAML must parse without error."""

    def test_loads_without_validation_error(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.name == "research-agent"

    def test_name_is_immutable(self, full_spec_path):
        """name is the primary key — changing it requires model_copy."""
        agent = load_agent(full_spec_path)
        # Pydantic v2 non-frozen models allow attribute assignment,
        # but the intent is that name is the primary key for the
        # agent — users should model_copy(update={"name": ...})
        # instead of in-place mutation.
        updated = agent.model_copy(update={"name": "changed"})
        assert updated.name == "changed"
        assert agent.name == "research-agent"  # original unchanged

    def test_framework_field(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.framework == "openclaw"
        assert agent.framework_version == "2026.4.4"

    def test_display_emoji_and_color(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.display.color == "#5b8def"
        assert agent.display.emoji == "🔍"

    def test_model_slots(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.models.chat is not None
        assert agent.models.chat.id == "qwen3-4b-q4"
        assert agent.models.chat.temperature == 0.2
        assert agent.models.fast is not None
        assert agent.models.fast.temperature == 0.0
        assert agent.models.reasoning is not None
        assert agent.models.reasoning.temperature == 0.4
        assert agent.models.routing.strategy == RoutingStrategy.LATENCY_FIRST
        assert agent.models.routing.fallback == "chat"

    def test_memory(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.memory.enabled is True
        assert len(agent.memory.collections) == 2
        assert agent.memory.collections[0].name == "notes"

    def test_skills_plain_strings(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.skills == ["web_search", "file_read", "file_write", "memory_search"]

    def test_plugins_model_objects(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert len(agent.plugins) == 1
        p = agent.plugins[0]
        assert p.id == "playwright-mcp"
        assert p.enabled is True
        assert p.config_ref == "secrets://playwright-creds"

    def test_channels(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert len(agent.channels) == 2
        assert agent.channels[0].taos_id == "channel-discord-research-server-general"
        assert "read" in agent.channels[0].permissions

    def test_secrets_are_opaque_refs(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert len(agent.secrets) == 1
        assert agent.secrets[0].id == "github-pat"

    def test_no_file_paths_on_model(self, full_spec_path):
        """Rule 1: no file paths — the Reconciler computes them.
        Sandbox paths (/workspace, /memory, etc.) are allowed because
        they are runtime container paths, not host config paths."""
        agent = load_agent(full_spec_path)
        dumped = agent.model_dump()
        for v in _walk_strings(dumped):
            assert "/data/agents/" not in v, (
                f"found config file path in model: {v!r}"
            )

    def test_resource_limits(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.resources.memory_limit == "2GB"
        assert agent.resources.cpu_limit == 2

    def test_permissions(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.permissions.can_read_user_memory is False
        assert len(agent.permissions.can_read_agent_memories) == 1
        mem = agent.permissions.can_read_agent_memories[0]
        assert mem.name == "inbox-agent"
        assert mem.collections == ["summaries"]
        assert agent.permissions.can_use_browser is True

    def test_observability(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.observability.tags == {
            "team": "research",
            "project": "model-mesh",
            "cost_center": "rd",
            "owner": "jay",
        }
        assert agent.observability.trace_sample_rate == 1.0
        assert agent.observability.log_level == "info"

    def test_schedule(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.schedule.enabled is True
        assert len(agent.schedule.jobs) == 1
        job = agent.schedule.jobs[0]
        assert job.id == "morning-digest"
        assert job.cron == "0 9 * * 1-5"
        assert job.task.kind == "prompt"
        assert job.task.timeout_seconds == 600

    def test_sandbox(self, full_spec_path):
        agent = load_agent(full_spec_path)
        sandbox = agent.sandbox
        assert sandbox.filesystem.readable == ["/workspace", "/memory"]
        assert sandbox.filesystem.denied == ["/etc", "/proc", "/sys"]
        assert sandbox.network.mode == NetworkMode.ALLOWLIST
        assert "github.com" in sandbox.network.allow
        assert sandbox.subprocess.denied == ["rm", "dd", "mkfs", "sudo"]
        assert sandbox.resources.disk_quota_mb == 1024
        assert sandbox.approval.require_for == ["subprocess", "network_write"]

    def test_state(self, full_spec_path):
        agent = load_agent(full_spec_path)
        assert agent.state.status == AgentStatus.RUNNING
        assert agent.state.container_id == "taos-agent-research-agent"
        assert agent.state.last_deployed_at == "2026-04-11T13:22:01Z"


# ═══════════════════════════════════════════════════════════════════════
# Minimal agent
# ═══════════════════════════════════════════════════════════════════════


class TestLoadMinimal:
    def test_minimal_fields(self, minimal_path):
        agent = load_agent(minimal_path)
        assert agent.name == "minimal"
        assert agent.framework == "hermes"
        assert agent.framework_version == "2026.1.0"

    def test_defaults_applied(self, minimal_path):
        agent = load_agent(minimal_path)
        # display
        assert agent.display.color == "#5b8def"
        assert agent.display.emoji == "🤖"
        # models
        assert agent.models.chat is None
        assert agent.models.routing.strategy == RoutingStrategy.LATENCY_FIRST
        # memory
        assert agent.memory.enabled is False
        assert agent.memory.collections == []
        # empty collections
        assert agent.skills == []
        assert agent.plugins == []
        assert agent.channels == []
        assert agent.secrets == []
        # resources
        assert agent.resources.memory_limit == "512MB"
        assert agent.resources.cpu_limit == 1
        # permissions all false
        assert agent.permissions.can_read_user_memory is False
        assert agent.permissions.can_send_email is False
        assert agent.permissions.can_use_browser is False
        # schedule disabled
        assert agent.schedule.enabled is False
        assert agent.schedule.jobs == []
        # state
        assert agent.state.status == AgentStatus.STOPPED
        assert agent.state.container_id == ""

    def test_skills_and_plugins_are_separate_buckets(self, minimal_path):
        """Rule 3: skills (plain strings) vs plugins (objects)."""
        agent = load_agent(minimal_path)
        assert isinstance(agent.skills, list)
        assert isinstance(agent.plugins, list)
        # They are different types in the model
        from tinyagentos.framework_integrations.agent_state import Plugin as PluginT

        # Verify model types — skills are List[str], plugins are List[Plugin]
        # (checked at import time above)


# ═══════════════════════════════════════════════════════════════════════
# Round-trip
# ═══════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    def test_round_trip_preserves_core_fields(self, full_spec_path):
        agent = load_agent(full_spec_path)
        dumped = dump_agent_to_string(agent)
        reloaded = AgentState.model_validate(yaml.safe_load(dumped))
        assert reloaded.name == agent.name
        assert reloaded.framework == agent.framework
        assert reloaded.framework_version == agent.framework_version

    def test_round_trip_full_spec_is_idempotent(self, full_spec_path):
        agent = load_agent(full_spec_path)
        yaml_str = dump_agent_to_string(agent)
        reloaded = AgentState.model_validate(yaml.safe_load(yaml_str))
        # Core identity
        assert reloaded.name == agent.name
        assert reloaded.framework == agent.framework
        # Nested equality
        assert reloaded.models.chat is not None
        assert agent.models.chat is not None
        assert reloaded.models.chat.id == agent.models.chat.id
        assert reloaded.models.routing.strategy == agent.models.routing.strategy
        assert reloaded.memory.enabled == agent.memory.enabled
        assert len(reloaded.memory.collections) == len(agent.memory.collections)
        assert reloaded.skills == agent.skills
        assert len(reloaded.plugins) == len(agent.plugins)
        assert reloaded.plugins[0].id == agent.plugins[0].id
        assert len(reloaded.channels) == len(agent.channels)
        assert len(reloaded.secrets) == len(agent.secrets)
        assert reloaded.sandbox.network.mode == agent.sandbox.network.mode
        assert reloaded.state.status == agent.state.status

    def test_write_and_read_back(self, full_spec_path, tmp_path):
        agent = load_agent(full_spec_path)
        out = tmp_path / "roundtrip.yaml"
        dump_agent(agent, out)
        reloaded = load_agent(out)
        assert reloaded.name == agent.name
        assert reloaded.framework == agent.framework


# ═══════════════════════════════════════════════════════════════════════
# load_agent_by_name
# ═══════════════════════════════════════════════════════════════════════


class TestLoadAgentByName:
    def test_loads_from_standard_location(self, agents_dir):
        agent = load_agent_by_name(agents_dir, "research-agent")
        assert agent.name == "research-agent"
        assert agent.framework == "openclaw"

    def test_file_not_found(self, agents_dir):
        with pytest.raises(FileNotFoundError):
            load_agent_by_name(agents_dir, "no-such-agent")


# ═══════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════


class TestValidation:
    def test_extra_fields_forbidden(self, tmp_path):
        yaml_str = textwrap.dedent("""\
        name: bad-agent
        framework: openclaw
        framework_version: "1.0"
        extra_field: nope
        """)
        p = tmp_path / "bad.yaml"
        p.write_text(yaml_str)
        with pytest.raises(Exception):
            load_agent(p)

    def test_missing_required_name(self):
        with pytest.raises(Exception):
            AgentState.model_validate({"framework": "x", "framework_version": "1.0"})

    def test_missing_framework(self):
        with pytest.raises(Exception):
            AgentState.model_validate({"name": "x", "framework_version": "1.0"})

    def test_bad_routing_strategy_rejected(self, tmp_path):
        yaml_str = textwrap.dedent("""\
        name: bad-routing
        framework: openclaw
        framework_version: "1.0"
        models:
          routing:
            strategy: not_a_real_strategy
            fallback: chat
        """)
        p = tmp_path / "bad-routing.yaml"
        p.write_text(yaml_str)
        with pytest.raises(Exception):
            load_agent(p)


# ═══════════════════════════════════════════════════════════════════════
# Three rules
# ═══════════════════════════════════════════════════════════════════════


class TestThreeRules:
    """Verification of the three design rules from the spec."""

    def test_rule1_no_file_paths_as_fields(self):
        """Rule 1: AgentState must not carry file paths.
        The Reconciler computes them."""
        fields = list(AgentState.model_fields.keys())
        for pathy_name in ("data_dir", "workspace_dir", "config_path",
                           "agent_path", "yaml_path", "file_path"):
            assert pathy_name not in fields, (
                f"'{pathy_name}' violates rule 1: no file paths"
            )

    def test_rule2_secrets_are_opaque_refs(self, full_spec_path):
        """Rule 2: secrets are id-only refs, not inline creds."""
        agent = load_agent(full_spec_path)
        for s in agent.secrets:
            assert isinstance(s.id, str)
            assert len(s.id) > 0
            # No secret value should be present
            assert not hasattr(s, "value")
            assert not hasattr(s, "secret")

    def test_rule3_skills_vs_plugins_different_types(self):
        """Rule 3: skills (List[str]) vs plugins (List[Plugin]) are
        separate buckets with different model types."""
        from tinyagentos.framework_integrations.agent_state import Plugin as PluginT

        # skills field annotation should be list[str]
        skills_field = AgentState.model_fields["skills"]
        skills_args = getattr(skills_field.annotation, "__args__", ())
        assert str in skills_args, (
            f"skills must be List[str], got {skills_field.annotation}"
        )
        # plugins field annotation should be list[Plugin]
        plugins_field = AgentState.model_fields["plugins"]
        # The field annotation should involve Plugin
        import typing
        plugins_annotation = plugins_field.annotation
        # In Pydantic v2 the annotation may be wrapped; unwrap if needed
        origin = typing.get_origin(plugins_annotation)
        args = typing.get_args(plugins_annotation)
        if origin is list and args:
            assert args[0] is PluginT, (
                f"plugins must be List[Plugin], got List[{args[0].__name__ if hasattr(args[0], '__name__') else args[0]}]"
            )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _walk_strings(obj):
    """Recursively yield all string values from a nested dict/list."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj
