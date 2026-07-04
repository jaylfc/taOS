import pytest
import pytest_asyncio

from tinyagentos.governance.policy_store import ExecutionPolicyStore
from tinyagentos.governance.action_classes import SENSITIVE_ACTION_CLASSES, classify


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ExecutionPolicyStore(tmp_path / "execution_policies.db")
    await s.init()
    yield s
    await s.close()


class TestActionClasses:
    def test_code_exec_classified(self):
        assert classify("code_exec") == "code-exec"

    def test_http_request_classified(self):
        assert classify("http_request") == "external-network"

    def test_unknown_skill_unclassified(self):
        assert classify("file_read") is None
        assert classify("nonexistent_tool") is None


@pytest.mark.asyncio
class TestConservativeSeed:
    async def test_seeded_global_rows_require_approval(self, store):
        policies = await store.list_policies()
        seeded_classes = {p["action_class"] for p in policies if p["scope"] == "global"}
        assert seeded_classes == set(SENSITIVE_ACTION_CLASSES)
        assert all(p["effect"] == "require_approval" for p in policies)

    async def test_sensitive_classes_default_to_require_approval(self, store):
        for action_class in SENSITIVE_ACTION_CLASSES:
            assert await store.effective_effect("agent-a", action_class) == "require_approval"

    async def test_unclassified_action_class_defaults_to_allow(self, store):
        assert await store.effective_effect("agent-a", "some-harmless-class") == "allow"

    async def test_reinit_does_not_duplicate_seed(self, tmp_path):
        db_path = tmp_path / "policies.db"
        s1 = ExecutionPolicyStore(db_path)
        await s1.init()
        await s1.close()
        s2 = ExecutionPolicyStore(db_path)
        await s2.init()
        policies = await s2.list_policies()
        assert len(policies) == len(SENSITIVE_ACTION_CLASSES)
        await s2.close()


@pytest.mark.asyncio
class TestEffectivePrecedence:
    async def test_per_agent_override_wins_over_global(self, store):
        # Global default for code-exec is require_approval (seeded).
        assert await store.effective_effect("agent-a", "code-exec") == "require_approval"
        await store.set_policy("code-exec", "allow", agent_name="agent-a")
        assert await store.effective_effect("agent-a", "code-exec") == "allow"
        # Other agents are unaffected by the per-agent override.
        assert await store.effective_effect("agent-b", "code-exec") == "require_approval"

    async def test_global_override_changes_default_for_all_agents(self, store):
        await store.set_policy("code-exec", "deny")
        assert await store.effective_effect("agent-a", "code-exec") == "deny"
        assert await store.effective_effect("agent-b", "code-exec") == "deny"

    async def test_set_policy_upserts_not_duplicates(self, store):
        await store.set_policy("code-exec", "allow", agent_name="agent-a")
        await store.set_policy("code-exec", "deny", agent_name="agent-a")
        policies = await store.list_policies(agent_name="agent-a")
        agent_rows = [p for p in policies if p["scope"] == "agent"]
        assert len(agent_rows) == 1
        assert agent_rows[0]["effect"] == "deny"

    async def test_invalid_effect_rejected(self, store):
        with pytest.raises(ValueError):
            await store.set_policy("code-exec", "bogus")

    async def test_delete_policy(self, store):
        p = await store.set_policy("code-exec", "allow", agent_name="agent-a")
        assert await store.delete_policy(p["id"]) is True
        assert await store.effective_effect("agent-a", "code-exec") == "require_approval"
        assert await store.delete_policy(p["id"]) is False


@pytest.mark.asyncio
class TestGrants:
    async def test_no_grant_by_default(self, store):
        assert await store.has_live_grant("agent-a", "code-exec") is False

    async def test_live_grant_satisfies_gate(self, store):
        await store.add_grant("agent-a", "code-exec", decision_id="dec-1", ttl_s=300)
        assert await store.has_live_grant("agent-a", "code-exec") is True

    async def test_expired_grant_does_not_satisfy_gate(self, store):
        await store.add_grant("agent-a", "code-exec", decision_id="dec-1", ttl_s=-1)
        assert await store.has_live_grant("agent-a", "code-exec") is False

    async def test_grant_scoped_to_agent_and_action_class(self, store):
        await store.add_grant("agent-a", "code-exec", decision_id="dec-1", ttl_s=300)
        assert await store.has_live_grant("agent-b", "code-exec") is False
        assert await store.has_live_grant("agent-a", "external-network") is False

    async def test_grant_at_explicit_now(self, store):
        import time
        now = time.time()
        await store.add_grant("agent-a", "code-exec", decision_id="dec-1", ttl_s=100)
        assert await store.has_live_grant("agent-a", "code-exec", now=now + 50) is True
        assert await store.has_live_grant("agent-a", "code-exec", now=now + 200) is False
