import time
import pytest
from tinyagentos.chat.group_policy import GroupPolicy


class TestMaySend:
    def test_first_send_is_allowed(self):
        p = GroupPolicy()
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_cooldown_blocks_same_agent(self):
        p = GroupPolicy()
        p.record_send("ch1", "agent")
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is False

    def test_cooldown_allows_different_agent(self):
        p = GroupPolicy()
        p.record_send("ch1", "agent_a")
        assert p.may_send("ch1", "agent_b", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_cooldown_allows_different_channel(self):
        p = GroupPolicy()
        p.record_send("ch1", "agent")
        assert p.may_send("ch2", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_cooldown_expires_after_threshold(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "agent")
        t[0] = 1004.9
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is False
        t[0] = 1005.0
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_cooldown_boundary_exact(self, monkeypatch):
        p = GroupPolicy()
        t = [0.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "agent")
        t[0] = 5.0
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_rate_cap_blocks_when_full(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(20):
            t[0] += 0.1
            p.record_send("ch1", f"a{i}")
        t[0] += 0.1
        assert p.may_send("ch1", "new_agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is False

    def test_rate_cap_allows_below_cap(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(19):
            t[0] += 0.1
            p.record_send("ch1", f"a{i}")
        t[0] += 0.1
        assert p.may_send("ch1", "new_agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is True

    def test_rate_cap_window_slides(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(20):
            p.record_send("ch1", f"a{i}")
            t[0] += 0.1
        t[0] += 61.0
        assert p.may_send("ch1", "new_agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is True

    def test_rate_cap_does_not_affect_other_channels(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(20):
            t[0] += 0.1
            p.record_send("ch1", f"a{i}")
        t[0] += 0.1
        assert p.may_send("ch2", "agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is True

    def test_default_settings_when_empty_dict(self):
        p = GroupPolicy()
        assert p.may_send("ch1", "agent", {}) is True

    def test_default_cooldown_only(self):
        p = GroupPolicy()
        p.record_send("ch1", "agent")
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 5}) is False

    def test_default_rate_cap_only(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(20):
            t[0] += 0.1
            p.record_send("ch1", f"a{i}")
        t[0] += 0.1
        assert p.may_send("ch1", "new_agent", {"cooldown_seconds": 0}) is False

    def test_zero_cooldown_allows_immediate_resend(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "agent")
        t[0] += 0.001
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is True

    def test_zero_rate_cap_allows_when_no_prior_sends(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        assert p.may_send("ch1", "agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 0}) is True

    def test_zero_rate_cap_blocks_after_one_send(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "a1")
        t[0] += 0.1
        assert p.may_send("ch1", "a2", {"cooldown_seconds": 0, "rate_cap_per_minute": 0}) is False

    def test_cooldown_and_rate_cap_both_block(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(20):
            t[0] += 0.1
            p.record_send("ch1", f"a{i}")
        t[0] += 0.1
        p.record_send("ch1", "blocked_agent")
        assert p.may_send("ch1", "blocked_agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is False

    def test_cooldown_checked_before_rate_cap(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "agent")
        t[0] += 0.1
        result = p.may_send("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20})
        assert result is False

    def test_old_entries_expired_from_window(self, monkeypatch):
        p = GroupPolicy()
        t = [0.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(20):
            p.record_send("ch1", f"a{i}")
        t[0] = 61.0
        assert p.may_send("ch1", "new_agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is True

    def test_partial_window_expiry(self, monkeypatch):
        p = GroupPolicy()
        t = [0.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        for i in range(10):
            p.record_send("ch1", f"a{i}")
        t[0] = 30.0
        for i in range(10, 20):
            p.record_send("ch1", f"a{i}")
        t[0] = 61.0
        assert p.may_send("ch1", "new_agent", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}) is True

    def test_settings_with_none_values_raises_type_error(self):
        p = GroupPolicy()
        with pytest.raises(TypeError):
            p.may_send("ch1", "agent", {"cooldown_seconds": None, "rate_cap_per_minute": None})

    def test_settings_with_string_values_coerced_to_int(self):
        p = GroupPolicy()
        p.record_send("ch1", "agent")
        result = p.may_send("ch1", "agent", {"cooldown_seconds": "5", "rate_cap_per_minute": "20"})
        assert result is False


class TestRecordSend:
    def test_stores_last_send_timestamp(self, monkeypatch):
        p = GroupPolicy()
        t = [42.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "agent")
        assert p._last_send_at[("ch1", "agent")] == 42.0

    def test_updates_last_send_timestamp(self, monkeypatch):
        p = GroupPolicy()
        t = [100.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "agent")
        t[0] = 200.0
        p.record_send("ch1", "agent")
        assert p._last_send_at[("ch1", "agent")] == 200.0

    def test_appends_to_recent_sends_window(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "a1")
        t[0] = 1001.0
        p.record_send("ch1", "a2")
        window = p._recent_sends["ch1"]
        assert len(window) == 2
        assert window[0] == 1000.0
        assert window[1] == 1001.0

    def test_creates_new_window_for_new_channel(self):
        p = GroupPolicy()
        p.record_send("ch1", "agent")
        p.record_send("ch2", "agent")
        assert "ch1" in p._recent_sends
        assert "ch2" in p._recent_sends
        assert len(p._recent_sends["ch1"]) == 1
        assert len(p._recent_sends["ch2"]) == 1

    def test_window_has_maxlen_256(self):
        p = GroupPolicy()
        dq = p._recent_sends.setdefault("ch1", __import__("collections").deque(maxlen=256))
        assert dq.maxlen == 256

    def test_multiple_agents_same_channel(self, monkeypatch):
        p = GroupPolicy()
        t = [0.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.record_send("ch1", "a1")
        t[0] = 1.0
        p.record_send("ch1", "a2")
        t[0] = 2.0
        p.record_send("ch1", "a3")
        assert len(p._recent_sends["ch1"]) == 3
        assert p._last_send_at[("ch1", "a1")] == 0.0
        assert p._last_send_at[("ch1", "a2")] == 1.0
        assert p._last_send_at[("ch1", "a3")] == 2.0


class TestTryAcquire:
    def test_returns_true_on_first_call(self):
        p = GroupPolicy()
        assert p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_returns_false_when_cooldown_active(self):
        p = GroupPolicy()
        p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20})
        assert p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is False

    def test_records_on_success(self, monkeypatch):
        p = GroupPolicy()
        t = [500.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20})
        assert p._last_send_at[("ch1", "agent")] == 500.0
        assert len(p._recent_sends["ch1"]) == 1

    def test_does_not_record_on_failure(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20})
        p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20})
        assert len(p._recent_sends["ch1"]) == 1

    def test_rate_cap_blocks_acquire(self, monkeypatch):
        p = GroupPolicy()
        t = [1000.0]
        monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
        results = []
        for i in range(21):
            t[0] += 0.1
            results.append(p.try_acquire("ch1", f"a{i}", {"cooldown_seconds": 0, "rate_cap_per_minute": 20}))
        assert results[:20] == [True] * 20
        assert results[20] is False

    def test_different_channels_independent_acquire(self):
        p = GroupPolicy()
        assert p.try_acquire("ch1", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True
        assert p.try_acquire("ch2", "agent", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_different_agents_independent_acquire(self):
        p = GroupPolicy()
        assert p.try_acquire("ch1", "a1", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True
        assert p.try_acquire("ch1", "a2", {"cooldown_seconds": 5, "rate_cap_per_minute": 20}) is True

    def test_empty_settings_uses_defaults(self):
        p = GroupPolicy()
        assert p.try_acquire("ch1", "agent", {}) is True
        assert p.try_acquire("ch1", "agent", {}) is False


class TestGroupPolicyFreshInstance:
    def test_empty_state(self):
        p = GroupPolicy()
        assert p._last_send_at == {}
        assert p._recent_sends == {}

    def test_multiple_instances_are_independent(self):
        p1 = GroupPolicy()
        p2 = GroupPolicy()
        p1.record_send("ch1", "agent")
        assert ("ch1", "agent") not in p2._last_send_at
        assert "ch1" not in p2._recent_sends
