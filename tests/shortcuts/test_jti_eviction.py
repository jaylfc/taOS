import time
import pytest
from tinyagentos.shortcuts.tickets import JtiTracker, _GLOBAL_JTI_TRACKER


def test_global_tracker_is_jti_tracker_instance():
    assert isinstance(_GLOBAL_JTI_TRACKER, JtiTracker)


def test_eviction_removes_expired_entries():
    tracker = JtiTracker()
    past_exp = int(time.time()) - 1
    tracker.record("expired-jti", exp=past_exp)
    tracker.seen("other-jti")
    assert "expired-jti" not in tracker._seen


def test_eviction_keeps_live_entries():
    tracker = JtiTracker()
    future_exp = int(time.time()) + 60
    tracker.record("live-jti", exp=future_exp)
    tracker.seen("probe")
    assert "live-jti" in tracker._seen


def test_seen_returns_false_after_eviction():
    tracker = JtiTracker()
    past_exp = int(time.time()) - 1
    tracker.record("stale-jti", exp=past_exp)
    assert tracker.seen("stale-jti") is False
