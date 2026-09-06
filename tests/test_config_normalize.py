from tinyagentos.config import normalize_agent


def test_normalize_agent_adds_persona_fields_with_defaults():
    agent = {"name": "atlas", "display_name": "Atlas", "framework": "openclaw"}
    normalize_agent(agent)
    assert agent["soul_md"] == ""
    assert agent["agent_md"] == ""
    assert agent["memory_plugin"] == "taosmd"
    assert agent["source_persona_id"] is None
    assert agent["migrated_to_v2_personas"] is False


def test_normalize_agent_preserves_existing_persona_fields():
    agent = {
        "name": "atlas", "display_name": "Atlas", "framework": "openclaw",
        "soul_md": "You are Atlas", "agent_md": "Always use tools",
        "memory_plugin": "none", "source_persona_id": "builtin:research",
        "migrated_to_v2_personas": True,
    }
    normalize_agent(agent)
    assert agent["soul_md"] == "You are Atlas"
    assert agent["agent_md"] == "Always use tools"
    assert agent["memory_plugin"] == "none"
    assert agent["source_persona_id"] == "builtin:research"
    assert agent["migrated_to_v2_personas"] is True


def test_normalize_agent_adds_framework_update_fields_with_defaults():
    agent = {"name": "atlas", "display_name": "Atlas", "framework": "openclaw"}
    normalize_agent(agent)
    assert agent["framework_version_tag"] is None
    assert agent["framework_version_sha"] is None
    assert agent["framework_update_status"] == "idle"
    assert agent["framework_update_started_at"] is None
    assert agent["framework_update_last_error"] is None
    assert agent["framework_last_snapshot"] is None
    assert agent["bootstrap_last_seen_at"] is None

def test_normalize_agent_preserves_existing_framework_update_fields():
    agent = {
        "name": "atlas", "framework": "openclaw",
        "framework_version_tag": "20260419T100000",
        "framework_version_sha": "abc1234",
        "framework_update_status": "failed",
        "framework_update_started_at": 1800000000,
        "framework_update_last_error": "timed out",
        "framework_last_snapshot": "pre-framework-update-x",
        "bootstrap_last_seen_at": 1800000005,
    }
    normalize_agent(agent)
    assert agent["framework_update_status"] == "failed"
    assert agent["framework_last_snapshot"] == "pre-framework-update-x"
    assert agent["bootstrap_last_seen_at"] == 1800000005
