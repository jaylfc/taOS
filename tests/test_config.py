import pytest
import yaml
from pathlib import Path
from tinyagentos.config import AppConfig, load_config, save_config, validate_config, normalize_agent, _LITELLM_PORT_NEW, _LITELLM_PORT_LEGACY

class TestLoadConfig:
    def test_loads_valid_config(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        assert config.server["host"] == "0.0.0.0"
        assert config.server["port"] == 6969
        assert len(config.backends) == 1
        assert config.backends[0]["name"] == "test-backend"
        assert config.qmd["url"] == "http://localhost:7832"
        assert len(config.agents) == 1
        assert config.agents[0]["name"] == "test-agent"

    def test_returns_defaults_when_file_missing(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.server["port"] == 6969
        assert config.backends == []
        assert config.agents == []

    def test_rejects_invalid_yaml(self, tmp_path):
        bad = tmp_path / "config.yaml"
        bad.write_text(": : : not valid yaml [[[")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_config(bad)

class TestSaveConfig:
    def test_roundtrip(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        config.agents.append({"name": "new-agent", "host": "10.0.0.1", "qmd_index": "new", "color": "#fff"})
        save_config(config, tmp_data_dir / "config.yaml")
        reloaded = load_config(tmp_data_dir / "config.yaml")
        assert len(reloaded.agents) == 2
        assert reloaded.agents[1]["name"] == "new-agent"

class TestValidateConfig:
    def test_valid_config_passes(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        errors = validate_config(config)
        assert errors == []

    def test_missing_backend_url(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        del config.backends[0]["url"]
        errors = validate_config(config)
        assert any("url" in e for e in errors)

    def test_invalid_backend_type(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        config.backends[0]["type"] = "unsupported"
        errors = validate_config(config)
        assert any("type" in e for e in errors)

    def test_duplicate_agent_names(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        config.agents.append(config.agents[0].copy())
        errors = validate_config(config)
        assert any("duplicate" in e.lower() for e in errors)

    def test_invalid_on_worker_failure(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        config.agents[0]["on_worker_failure"] = "magic"
        errors = validate_config(config)
        assert any("on_worker_failure" in e for e in errors)

    def test_valid_on_worker_failure_values(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        for value in ("pause", "fallback", "escalate-immediately"):
            config.agents[0]["on_worker_failure"] = value
            errors = validate_config(config)
            assert not any("on_worker_failure" in e for e in errors), \
                f"Expected '{value}' to be valid but got errors: {errors}"

    def test_fallback_models_must_be_list(self, tmp_data_dir):
        config = load_config(tmp_data_dir / "config.yaml")
        config.agents[0]["fallback_models"] = "not-a-list"
        errors = validate_config(config)
        assert any("fallback_models" in e for e in errors)


class TestWorkerFailureDefaults:
    def test_old_config_gets_defaults_on_load(self, tmp_path):
        """An old config without the new fields should load without error and have defaults."""
        old_config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [
                {"name": "b", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
            ],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {"name": "legacy-agent", "host": "192.168.1.50", "color": "#abc123"}
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(old_config))
        config = load_config(p)
        agent = config.agents[0]
        assert agent["on_worker_failure"] == "pause"
        assert agent["fallback_models"] == []
        assert agent["paused"] is False

    def test_old_config_with_fallback_models_defaults_to_fallback_policy(self, tmp_path):
        """Old config that somehow has fallback_models but no policy defaults to 'fallback'."""
        old_config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {
                    "name": "agent-with-fallbacks",
                    "host": "10.0.0.1",
                    "color": "#fff",
                    "fallback_models": ["phi3", "llama3"],
                }
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(old_config))
        config = load_config(p)
        agent = config.agents[0]
        assert agent["on_worker_failure"] == "fallback"

    def test_existing_policy_not_overwritten(self, tmp_path):
        """Explicitly set policy is preserved through load."""
        cfg_data = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {
                    "name": "explicit-agent",
                    "host": "10.0.0.2",
                    "color": "#fff",
                    "on_worker_failure": "escalate-immediately",
                    "fallback_models": ["gpt-4o"],
                    "paused": False,
                }
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg_data))
        config = load_config(p)
        agent = config.agents[0]
        assert agent["on_worker_failure"] == "escalate-immediately"
        assert agent["fallback_models"] == ["gpt-4o"]

    def test_roundtrip_preserves_new_fields(self, tmp_path):
        """save_config + load_config preserves the new fields correctly."""
        p = tmp_path / "config.yaml"
        config = AppConfig(
            agents=[
                {
                    "name": "roundtrip-agent",
                    "host": "10.0.0.3",
                    "color": "#fff",
                    "on_worker_failure": "fallback",
                    "fallback_models": ["mistral", "phi3"],
                    "paused": False,
                }
            ],
            config_path=p,
        )
        save_config(config, p)
        reloaded = load_config(p)
        agent = reloaded.agents[0]
        assert agent["on_worker_failure"] == "fallback"
        assert agent["fallback_models"] == ["mistral", "phi3"]
        assert agent["paused"] is False

    def test_normalize_agent_idempotent(self):
        """Calling normalize_agent twice gives the same result."""
        agent = {"name": "x", "host": "h", "color": "#fff"}
        normalize_agent(agent)
        first = dict(agent)
        normalize_agent(agent)
        assert agent == first

class TestKvCacheQuantField:
    def test_old_config_gets_kv_quant_default(self, tmp_path):
        """Old config without any kv_cache_quant fields loads with fp16 defaults."""
        old_config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [{"name": "a", "host": "h", "color": "#abc"}],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(old_config))
        config = load_config(p)
        agent = config.agents[0]
        assert agent["kv_cache_quant_k"] == "fp16"
        assert agent["kv_cache_quant_v"] == "fp16"
        assert agent["kv_cache_quant_boundary_layers"] == 0
        # Legacy single-field key should be removed after normalisation.
        assert "kv_cache_quant" not in agent

    def test_legacy_single_field_migrates_to_split(self, tmp_path):
        """A config with the pre-split kv_cache_quant field gets migrated to both K and V."""
        old_config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [{
                "name": "legacy",
                "host": "h",
                "color": "#abc",
                "kv_cache_quant": "q8_0",
            }],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(old_config))
        config = load_config(p)
        agent = config.agents[0]
        assert agent["kv_cache_quant_k"] == "q8_0"
        assert agent["kv_cache_quant_v"] == "q8_0"
        assert "kv_cache_quant" not in agent

    def test_explicit_split_values_preserved(self, tmp_path):
        cfg_data = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [{
                "name": "a",
                "host": "h",
                "color": "#abc",
                "kv_cache_quant_k": "q8_0",
                "kv_cache_quant_v": "turbo3",
                "kv_cache_quant_boundary_layers": 2,
            }],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg_data))
        config = load_config(p)
        agent = config.agents[0]
        assert agent["kv_cache_quant_k"] == "q8_0"
        assert agent["kv_cache_quant_v"] == "turbo3"
        assert agent["kv_cache_quant_boundary_layers"] == 2

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "config.yaml"
        config = AppConfig(
            agents=[{
                "name": "kv-agent",
                "host": "10.0.0.1",
                "color": "#fff",
                "kv_cache_quant_k": "turbo3",
                "kv_cache_quant_v": "turbo2",
                "kv_cache_quant_boundary_layers": 2,
            }],
            config_path=p,
        )
        save_config(config, p)
        reloaded = load_config(p)
        agent = reloaded.agents[0]
        assert agent["kv_cache_quant_k"] == "turbo3"
        assert agent["kv_cache_quant_v"] == "turbo2"
        assert agent["kv_cache_quant_boundary_layers"] == 2

    def test_normalize_agent_idempotent_with_kv_quant(self):
        agent = {
            "name": "x",
            "host": "h",
            "color": "#fff",
            "kv_cache_quant_k": "fp16",
            "kv_cache_quant_v": "fp16",
            "kv_cache_quant_boundary_layers": 0,
        }
        normalize_agent(agent)
        first = dict(agent)
        normalize_agent(agent)
        assert agent == first

    def test_any_string_accepted_no_validation(self, tmp_path):
        """validate_config does not restrict kv_cache_quant_k/v to a fixed list."""
        cfg_data = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [{
                "name": "future-agent",
                "host": "10.0.0.1",
                "color": "#fff",
                "kv_cache_quant_k": "some-future-k-scheme",
                "kv_cache_quant_v": "some-future-v-scheme",
            }],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg_data))
        config = load_config(p)
        errors = validate_config(config)
        # No error for an unknown KV quant value, worker probe is source of truth.
        assert not any("kv_cache_quant" in e for e in errors)


    def test_paused_field_defaults_to_false(self, tmp_path):
        old_config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [{"name": "a", "host": "h", "color": "#abc"}],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(old_config))
        config = load_config(p)
        assert config.agents[0]["paused"] is False


class TestLitellmPortPin:
    def test_from_disk_without_litellm_port_pins_legacy_and_persists(self, tmp_path):
        """Existing install: config.yaml has no litellm_port -> pinned to 4000 on load
        and the pin is persisted so subsequent boots don't toggle."""
        old_cfg = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(old_cfg))
        config = load_config(p)
        assert config.server["litellm_port"] == _LITELLM_PORT_LEGACY
        # Pin must be persisted so the next boot reads a concrete value.
        on_disk = yaml.safe_load(p.read_text())
        assert on_disk["server"]["litellm_port"] == _LITELLM_PORT_LEGACY

    def test_fresh_install_records_new_port(self, tmp_path):
        """No config file -> fresh install defaults record 7834 (not 4000)."""
        config = load_config(tmp_path / "config.yaml")
        assert config.server["litellm_port"] == _LITELLM_PORT_NEW

    def test_explicit_existing_value_is_untouched(self, tmp_path):
        """An explicit litellm_port in config (e.g. 5000) is preserved as-is."""
        existing_cfg = {
            "server": {"host": "0.0.0.0", "port": 6969, "litellm_port": 5000},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(existing_cfg))
        original_mtime = p.stat().st_mtime
        config = load_config(p)
        assert config.server["litellm_port"] == 5000
        # File must not be rewritten when no pin was applied.
        assert p.stat().st_mtime == original_mtime


def test_load_config_migrates_legacy_rkllama_port(tmp_path):
    """An install seeded before the taOS default moved to :7833 keeps a stale
    localhost:8080 rkllama provider; load_config heals it to :7833 (#1697)."""
    import yaml
    from tinyagentos.config import load_config

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "backends": [
            {"name": "rkllama", "type": "rkllama", "url": "http://localhost:8080"},
            {"name": "custom-rk", "type": "rkllama", "url": "http://10.0.0.5:8080"},
            {"name": "ollama", "type": "ollama", "url": "http://localhost:8080"},
        ]
    }))
    cfg = load_config(cfg_path)
    by_name = {b["name"]: b["url"] for b in cfg.backends}
    # auto-seeded localhost rkllama -> healed to 7833
    assert by_name["rkllama"] == "http://localhost:7833"
    # deliberate custom host -> untouched
    assert by_name["custom-rk"] == "http://10.0.0.5:8080"
    # non-rkllama backend on 8080 -> untouched
    assert by_name["ollama"] == "http://localhost:8080"


def test_load_config_migrates_legacy_rkllama_backend_name(tmp_path):
    """An install seeded before #1710 names the rkllama backend local-npu,
    whose service-id (npu) never matches the RKLLM manifests'
    requires.backends id (rkllama), so installed chat models get no LiteLLM
    alias. load_config renames it to local-rkllama so the service-id matches."""
    import yaml
    from tinyagentos.config import load_config

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "backends": [
            {"name": "local-npu", "type": "rkllama", "url": "http://localhost:7833"},
            {"name": "custom-rk", "type": "rkllama", "url": "http://10.0.0.5:8080"},
            {"name": "local-npu", "type": "ollama", "url": "http://localhost:11434"},
        ]
    }))
    cfg = load_config(cfg_path)
    rkllama_names = [b["name"] for b in cfg.backends if b["type"] == "rkllama"]
    # The auto-seeded rkllama local-npu is renamed to local-rkllama.
    assert "local-rkllama" in rkllama_names
    assert "local-npu" not in rkllama_names
    # A deliberately custom rkllama name is untouched.
    assert "custom-rk" in rkllama_names
    # A non-rkllama backend that happens to be named local-npu is untouched.
    ollama = [b for b in cfg.backends if b["type"] == "ollama"][0]
    assert ollama["name"] == "local-npu"


_HAILO_MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "app-catalog" / "services" / "hailo-ollama" / "manifest.yaml"
)


def _hailo10h_profile(ram_gb: int = 8):
    """Hardware profile for a Raspberry Pi 5 + Hailo-10H AI HAT+2."""
    from types import SimpleNamespace

    return SimpleNamespace(hardware={
        "cpu": {"arch": "aarch64"},
        "npu": {"type": "hailo10h"},
        "ram_mb": ram_gb * 1024,
    })


def _cpu_profile(ram_gb: int = 8):
    from types import SimpleNamespace

    return SimpleNamespace(hardware={
        "cpu": {"arch": "x86_64"},
        "npu": {"type": "none"},
        "ram_mb": ram_gb * 1024,
    })


def test_auto_register_hailo_ollama_seeds_local_backend_on_hailo10h(tmp_path):
    """S5: on a Hailo-10H host the hailo-ollama manifest seeds a
    local-hailo-ollama backend of type hailo-ollama (the local-<service-id>
    rule, mirroring rkllama's local-rkllama)."""
    from tinyagentos.config import AppConfig, auto_register_from_manifest

    cfg = AppConfig(config_path=tmp_path / "config.yaml")
    added = auto_register_from_manifest(
        _HAILO_MANIFEST, cfg, hardware_profile=_hailo10h_profile(),
    )
    assert added is True
    seeded = [b for b in cfg.backends if b["name"] == "local-hailo-ollama"]
    assert len(seeded) == 1
    assert seeded[0]["type"] == "hailo-ollama"
    assert seeded[0]["url"] == "http://localhost:7836"


def test_auto_register_hailo_ollama_skipped_without_hailo10h(tmp_path):
    """S5: on non-Hailo hardware (cpu-only tier) the manifest's
    cpu-only: unsupported tier must keep local-hailo-ollama unregistered."""
    from tinyagentos.config import AppConfig, auto_register_from_manifest

    cfg = AppConfig(config_path=tmp_path / "config.yaml")
    added = auto_register_from_manifest(
        _HAILO_MANIFEST, cfg, hardware_profile=_cpu_profile(),
    )
    assert added is False
    assert [b for b in cfg.backends if b["type"] == "hailo-ollama"] == []


class TestMemoryUrl:
    """Config persistence and defaults for memory_url (taOSmd)."""

    def test_default_memory_url(self):
        """memory_url defaults to http://localhost:7900."""
        cfg = AppConfig()
        assert cfg.memory_url == "http://localhost:7900"

    def test_custom_memory_url(self):
        """Custom memory_url survives to_dict + load_config roundtrip."""
        from tinyagentos.config import load_config

        cfg = AppConfig(memory_url="https://taosmd.example.com:7900")
        assert cfg.memory_url == "https://taosmd.example.com:7900"

        d = cfg.to_dict()
        assert d["memory_url"] == "https://taosmd.example.com:7900"

    def test_default_not_in_to_dict(self):
        """Default memory_url should not appear in to_dict output."""
        cfg = AppConfig()
        d = cfg.to_dict()
        assert "memory_url" not in d

    def test_roundtrip(self, tmp_path):
        """memory_url survives yaml save+load cycle."""
        from tinyagentos.config import load_config, save_config

        p = tmp_path / "config.yaml"
        cfg = AppConfig(
            memory_url="http://192.168.1.50:7900",
            config_path=p,
        )
        save_config(cfg, p)
        reloaded = load_config(p)
        assert reloaded.memory_url == "http://192.168.1.50:7900"

    def test_roundtrip_default_is_omitted(self, tmp_path):
        """Default memory_url not in YAML, load_config fills it in."""
        from tinyagentos.config import load_config, save_config

        p = tmp_path / "config.yaml"
        cfg = AppConfig(config_path=p)
        save_config(cfg, p)
        assert "memory_url" not in p.read_text()

        reloaded = load_config(p)
        assert reloaded.memory_url == "http://localhost:7900"
