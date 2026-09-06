import pytest
from tinyagentos.prompt_assembly import assemble_system_prompt, STRICT_READ_DIRECTIVE

class _Agent:
    def __init__(self, slug="atlas", soul="", agent_md="", memory_plugin="taosmd"):
        self.slug = slug
        self.soul_md = soul
        self.agent_md = agent_md
        self.memory_plugin = memory_plugin

def test_directive_always_first():
    out = assemble_system_prompt(_Agent(memory_plugin="none"))
    assert out.startswith(STRICT_READ_DIRECTIVE)

def test_taosmd_rules_included_and_substituted(monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.prompt_assembly._load_agent_rules",
        lambda: "rules for <your-agent-name>",
    )
    out = assemble_system_prompt(_Agent(slug="atlas", memory_plugin="taosmd"))
    assert "rules for atlas" in out
    assert "<your-agent-name>" not in out

def test_memory_none_skips_rules(monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.prompt_assembly._load_agent_rules",
        lambda: "rules for <your-agent-name>",
    )
    out = assemble_system_prompt(_Agent(memory_plugin="none"))
    assert "rules for" not in out

def test_soul_and_agent_md_concatenated_in_order(monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.prompt_assembly._load_agent_rules",
        lambda: "MEMORY",
    )
    out = assemble_system_prompt(_Agent(soul="SOUL", agent_md="AGENT"))
    idx_mem = out.index("MEMORY")
    idx_soul = out.index("SOUL")
    idx_agent = out.index("AGENT")
    assert idx_mem < idx_soul < idx_agent

def test_empty_soul_and_agent_md_are_skipped(monkeypatch):
    monkeypatch.setattr("tinyagentos.prompt_assembly._load_agent_rules", lambda: "M")
    out = assemble_system_prompt(_Agent(soul="", agent_md=""))
    # Only directive + memory block
    assert out.count("\n\n---\n\n") == 1

def test_missing_agent_rules_logs_and_returns_empty(monkeypatch, caplog):
    monkeypatch.setattr(
        "tinyagentos.prompt_assembly._load_agent_rules",
        lambda: "",  # missing
    )
    out = assemble_system_prompt(_Agent(soul="SOUL"))
    assert "SOUL" in out
    # When memory rules missing, the directive still ships.
    assert out.startswith(STRICT_READ_DIRECTIVE)

def test_load_agent_rules_warns_when_taosmd_missing(monkeypatch, caplog):
    import sys
    from tinyagentos import prompt_assembly
    monkeypatch.setitem(sys.modules, "taosmd", None)
    caplog.set_level("WARNING", logger="tinyagentos.prompt_assembly")
    out = prompt_assembly._load_agent_rules()
    assert out == ""
    assert any("taosmd" in r.message for r in caplog.records)

def test_load_agent_rules_warns_when_file_missing(monkeypatch, tmp_path, caplog):
    import sys, types
    from tinyagentos import prompt_assembly
    fake_taosmd_pkg_dir = tmp_path / "taosmd"
    fake_taosmd_pkg_dir.mkdir()
    (fake_taosmd_pkg_dir / "__init__.py").write_text("")
    fake_mod = types.ModuleType("taosmd")
    fake_mod.__file__ = str(fake_taosmd_pkg_dir / "__init__.py")
    monkeypatch.setitem(sys.modules, "taosmd", fake_mod)
    caplog.set_level("WARNING", logger="tinyagentos.prompt_assembly")
    out = prompt_assembly._load_agent_rules()
    assert out == ""
    assert any("agent-rules.md" in r.message for r in caplog.records)
