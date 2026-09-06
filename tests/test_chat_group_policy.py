import time
import pytest
from tinyagentos.chat.group_policy import GroupPolicy

SETTINGS = {"cooldown_seconds": 5, "rate_cap_per_minute": 20}


def test_first_send_allowed():
    p = GroupPolicy()
    assert p.may_send("ch1", "tom", SETTINGS) is True


def test_cooldown_blocks_same_agent():
    p = GroupPolicy()
    p.record_send("ch1", "tom")
    assert p.may_send("ch1", "tom", SETTINGS) is False


def test_cooldown_different_agents_independent():
    p = GroupPolicy()
    p.record_send("ch1", "tom")
    assert p.may_send("ch1", "don", SETTINGS) is True


def test_cooldown_different_channels_independent():
    p = GroupPolicy()
    p.record_send("ch1", "tom")
    assert p.may_send("ch2", "tom", SETTINGS) is True


def test_cooldown_elapses(monkeypatch):
    p = GroupPolicy()
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
    p.record_send("ch1", "tom")
    t[0] = 1004.9
    assert p.may_send("ch1", "tom", SETTINGS) is False
    t[0] = 1005.1
    assert p.may_send("ch1", "tom", SETTINGS) is True


def test_rate_cap_blocks_channel(monkeypatch):
    p = GroupPolicy()
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
    for i in range(20):
        t[0] += 0.1
        p.record_send("ch1", f"agent{i}")
    t[0] += 0.1
    assert p.may_send("ch1", "agent_new", SETTINGS) is False


def test_rate_cap_window_slides(monkeypatch):
    p = GroupPolicy()
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.group_policy._now", lambda: t[0])
    for i in range(20):
        p.record_send("ch1", f"agent{i}")
        t[0] += 0.1
    t[0] += 61.0
    assert p.may_send("ch1", "agent_new", SETTINGS) is True


def test_try_acquire_is_atomic():
    p = GroupPolicy()
    s = {"cooldown_seconds": 5, "rate_cap_per_minute": 20}
    assert p.try_acquire("c1", "tom", s) is True
    assert p.try_acquire("c1", "tom", s) is False  # cooldown blocks second try
