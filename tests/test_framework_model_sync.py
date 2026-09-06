"""Unit tests for tinyagentos.framework_model_sync."""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.framework_model_sync import (
    OPENCLAW_MODEL_DEFAULTS,
    FrameworkModelReconciler,
    build_openclaw_models,
    patch_hermes_default,
    patch_openclaw_config,
    read_hermes_default,
    read_openclaw_primary,
)

# ---------------------------------------------------------------------------
# Sample Hermes YAML used across multiple tests
# ---------------------------------------------------------------------------

HERMES_YAML = """\
agent:
  memory_enabled: true
model:
  api_key: sk-x
  base_url: http://127.0.0.1:4000/v1
  default: old-model
  provider: custom
platform_toolsets:
  cli:
  - hermes-cli
"""


# ---------------------------------------------------------------------------
# build_openclaw_models
# ---------------------------------------------------------------------------


def test_build_openclaw_models_shape():
    result = build_openclaw_models(["llama3", "qwen3"])
    assert len(result) == 2
    for entry, mid in zip(result, ["llama3", "qwen3"]):
        assert entry["id"] == mid
        assert entry["name"] == mid
        assert entry["contextWindow"] == OPENCLAW_MODEL_DEFAULTS["contextWindow"]
        assert entry["maxTokens"] == OPENCLAW_MODEL_DEFAULTS["maxTokens"]
        assert entry["input"] == OPENCLAW_MODEL_DEFAULTS["input"]
        assert entry["reasoning"] == OPENCLAW_MODEL_DEFAULTS["reasoning"]


def test_build_openclaw_models_empty():
    assert build_openclaw_models([]) == []


# ---------------------------------------------------------------------------
# patch_openclaw_config
# ---------------------------------------------------------------------------


def test_patch_openclaw_config_minimal_cfg():
    cfg = patch_openclaw_config({}, "llama3", ["llama3", "qwen3"])
    assert cfg["agents"]["defaults"]["model"]["primary"] == "litellm/llama3"
    models = cfg["models"]["providers"]["litellm"]["models"]
    assert len(models) == 2
    ids = [m["id"] for m in models]
    assert ids == ["llama3", "qwen3"]


def test_patch_openclaw_config_nested_structure_created():
    cfg = patch_openclaw_config({}, "my-model", ["my-model"])
    # All nesting must exist
    assert "models" in cfg
    assert "providers" in cfg["models"]
    assert "litellm" in cfg["models"]["providers"]
    assert "models" in cfg["models"]["providers"]["litellm"]
    assert "agents" in cfg
    assert "defaults" in cfg["agents"]
    assert "model" in cfg["agents"]["defaults"]


def test_patch_openclaw_config_fixed_metadata():
    cfg = patch_openclaw_config({}, "m1", ["m1"])
    entry = cfg["models"]["providers"]["litellm"]["models"][0]
    assert entry["contextWindow"] == 128000
    assert entry["maxTokens"] == 16384
    assert entry["input"] == ["text"]
    assert entry["reasoning"] is False


# ---------------------------------------------------------------------------
# read_openclaw_primary
# ---------------------------------------------------------------------------


def test_read_openclaw_primary_strips_prefix():
    cfg = {"agents": {"defaults": {"model": {"primary": "litellm/llama3"}}}}
    assert read_openclaw_primary(cfg) == "llama3"


def test_read_openclaw_primary_no_prefix():
    cfg = {"agents": {"defaults": {"model": {"primary": "qwen3"}}}}
    assert read_openclaw_primary(cfg) == "qwen3"


def test_read_openclaw_primary_missing_returns_none():
    assert read_openclaw_primary({}) is None
    assert read_openclaw_primary({"agents": {}}) is None
    assert read_openclaw_primary({"agents": {"defaults": {}}}) is None


# ---------------------------------------------------------------------------
# patch_hermes_default
# ---------------------------------------------------------------------------


def test_patch_hermes_default_replaces_value():
    result = patch_hermes_default(HERMES_YAML, "new-model")
    assert "default: new-model" in result


def test_patch_hermes_default_preserves_other_lines():
    result = patch_hermes_default(HERMES_YAML, "new-model")
    assert "base_url: http://127.0.0.1:4000/v1" in result
    assert "provider: custom" in result
    assert "memory_enabled: true" in result
    assert "- hermes-cli" in result
    assert "api_key: sk-x" in result


def test_patch_hermes_default_does_not_duplicate_lines():
    result = patch_hermes_default(HERMES_YAML, "new-model")
    # Only one default: line should exist
    default_lines = [l for l in result.splitlines() if "default:" in l]
    assert len(default_lines) == 1


def test_patch_hermes_default_old_value_gone():
    result = patch_hermes_default(HERMES_YAML, "new-model")
    assert "old-model" not in result


def test_patch_hermes_default_no_model_default_unchanged():
    yaml_no_default = """\
agent:
  memory_enabled: true
model:
  api_key: sk-x
  provider: custom
"""
    result = patch_hermes_default(yaml_no_default, "new-model")
    assert result == yaml_no_default


def test_patch_hermes_default_no_model_block_unchanged():
    yaml_no_block = "foo: bar\n"
    result = patch_hermes_default(yaml_no_block, "new-model")
    assert result == yaml_no_block


# ---------------------------------------------------------------------------
# read_hermes_default
# ---------------------------------------------------------------------------


def test_read_hermes_default_parses_value():
    assert read_hermes_default(HERMES_YAML) == "old-model"


def test_read_hermes_default_returns_none_when_absent():
    assert read_hermes_default("foo: bar\n") is None


def test_read_hermes_default_after_patch():
    patched = patch_hermes_default(HERMES_YAML, "new-model")
    assert read_hermes_default(patched) == "new-model"


# ---------------------------------------------------------------------------
# FrameworkModelReconciler
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, agents):
        self.agents = agents
        self.config_path = "/dev/null"


class _FakeState:
    def __init__(self, agents):
        self.config = _FakeConfig(agents)


async def _noop_save(*args, **kwargs):
    pass


@pytest.mark.asyncio
async def test_reconciler_updates_agent_record(monkeypatch):
    """When live primary differs from agent record, it must be updated."""
    agents = [
        {
            "name": "alpha",
            "framework": "openclaw",
            "model": "old-model",
            "permitted_models": ["old-model"],
        }
    ]
    state = _FakeState(agents)

    import tinyagentos.framework_model_sync as mod

    async def _fake_read(slug, framework):
        return "new-model"

    monkeypatch.setattr(mod, "read_framework_primary", _fake_read)
    monkeypatch.setattr(mod, "save_config_locked", _noop_save, raising=False)
    # Also patch the import inside _reconcile_once
    import tinyagentos.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config_locked", _noop_save)

    reconciler = FrameworkModelReconciler(state, interval=60.0, initial_delay=30.0)
    await reconciler._reconcile_once()

    assert agents[0]["model"] == "new-model"
    assert "new-model" in agents[0]["permitted_models"]


@pytest.mark.asyncio
async def test_reconciler_no_change_when_model_same(monkeypatch):
    agents = [
        {
            "name": "beta",
            "framework": "hermes",
            "model": "same-model",
            "permitted_models": ["same-model"],
        }
    ]
    state = _FakeState(agents)

    save_calls = []

    import tinyagentos.framework_model_sync as mod

    async def _fake_read(slug, framework):
        return "same-model"

    async def _capture_save(*args, **kwargs):
        save_calls.append(True)

    monkeypatch.setattr(mod, "read_framework_primary", _fake_read)
    import tinyagentos.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config_locked", _capture_save)

    reconciler = FrameworkModelReconciler(state, interval=60.0, initial_delay=30.0)
    await reconciler._reconcile_once()

    assert agents[0]["model"] == "same-model"
    assert not save_calls  # no save because nothing changed


@pytest.mark.asyncio
async def test_reconciler_skips_non_framework_agents(monkeypatch):
    agents = [
        {"name": "gamma", "framework": "none", "model": "x"},
        {"name": "delta", "model": "y"},  # no framework key
    ]
    state = _FakeState(agents)

    reads = []

    import tinyagentos.framework_model_sync as mod

    async def _fake_read(slug, framework):
        reads.append(slug)
        return "z"

    monkeypatch.setattr(mod, "read_framework_primary", _fake_read)
    import tinyagentos.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config_locked", _noop_save)

    reconciler = FrameworkModelReconciler(state, interval=60.0, initial_delay=30.0)
    await reconciler._reconcile_once()

    # Neither agent should have been read
    assert reads == []


@pytest.mark.asyncio
async def test_reconciler_start_stop_no_hang():
    """start() + stop() must complete without hanging."""
    state = _FakeState([])
    reconciler = FrameworkModelReconciler(state, interval=0.05, initial_delay=0.05)
    await reconciler.start()
    await asyncio.sleep(0.01)
    await reconciler.stop()  # must not block


@pytest.mark.asyncio
async def test_reconciler_appends_new_model_to_permitted(monkeypatch):
    """A live model not in permitted_models must be prepended."""
    agents = [
        {
            "name": "epsilon",
            "framework": "hermes",
            "model": "old",
            "permitted_models": ["old"],
        }
    ]
    state = _FakeState(agents)

    import tinyagentos.framework_model_sync as mod

    async def _fake_read(slug, framework):
        return "brand-new"

    monkeypatch.setattr(mod, "read_framework_primary", _fake_read)
    import tinyagentos.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config_locked", _noop_save)

    reconciler = FrameworkModelReconciler(state)
    await reconciler._reconcile_once()

    assert agents[0]["model"] == "brand-new"
    assert agents[0]["permitted_models"][0] == "brand-new"
    assert "old" in agents[0]["permitted_models"]
