import pytest
from tinyagentos.config import AppConfig, unique_agent_slug


def _config(*names: str) -> AppConfig:
    """Build a minimal AppConfig with agents having the given names."""
    cfg = AppConfig()
    cfg.agents = [{"name": n} for n in names]
    return cfg


class TestUniqueAgentSlug:
    def test_no_collision_returns_plain_slug(self):
        cfg = _config()
        assert unique_agent_slug(cfg, "My Agent") == "my-agent"

    def test_one_collision_returns_suffix_2(self):
        cfg = _config("my-agent")
        assert unique_agent_slug(cfg, "My Agent") == "my-agent-2"

    def test_two_collisions_returns_suffix_3(self):
        cfg = _config("my-agent", "my-agent-2")
        assert unique_agent_slug(cfg, "My Agent") == "my-agent-3"

    def test_many_collisions_increments_correctly(self):
        occupied = ["my-agent"] + [f"my-agent-{i}" for i in range(2, 10)]
        cfg = _config(*occupied)
        assert unique_agent_slug(cfg, "My Agent") == "my-agent-10"

    def test_cap_100_raises_value_error(self):
        occupied = ["my-agent"] + [f"my-agent-{i}" for i in range(2, 101)]
        cfg = _config(*occupied)
        with pytest.raises(ValueError, match="unique agent slug"):
            unique_agent_slug(cfg, "My Agent")

    def test_cap_99_still_succeeds(self):
        # Slots my-agent through my-agent-98 are taken; my-agent-99 is free.
        occupied = ["my-agent"] + [f"my-agent-{i}" for i in range(2, 99)]
        cfg = _config(*occupied)
        assert unique_agent_slug(cfg, "My Agent") == "my-agent-99"

    def test_unrelated_agents_do_not_block(self):
        cfg = _config("other-agent", "another-one")
        assert unique_agent_slug(cfg, "My Agent") == "my-agent"

    def test_special_characters_in_display_name(self):
        cfg = _config()
        assert unique_agent_slug(cfg, "Agent_42!") == "agent-42"

    def test_emoji_display_name(self):
        cfg = _config()
        assert unique_agent_slug(cfg, "🚀 Alpha v2") == "alpha-v2"
