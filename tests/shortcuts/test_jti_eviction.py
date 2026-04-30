import threading
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


# ---------------------------------------------------------------------------
# Atomic record_if_new tests (C2 fix)
# ---------------------------------------------------------------------------

def test_record_if_new_returns_true_first_time():
    tracker = JtiTracker()
    exp = int(time.time()) + 30
    assert tracker.record_if_new("jti-abc", exp) is True


def test_record_if_new_returns_false_on_duplicate():
    tracker = JtiTracker()
    exp = int(time.time()) + 30
    tracker.record_if_new("jti-dup", exp)
    assert tracker.record_if_new("jti-dup", exp) is False


def test_record_if_new_concurrent_only_one_wins():
    """Two threads racing on the same JTI: exactly one must win."""
    tracker = JtiTracker()
    exp = int(time.time()) + 30
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _race():
        barrier.wait()  # both threads start at roughly the same time
        results.append(tracker.record_if_new("shared-jti", exp))

    t1 = threading.Thread(target=_race)
    t2 = threading.Thread(target=_race)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one True and one False
    assert sorted(results) == [False, True]


def test_record_if_new_evicts_expired_before_check():
    tracker = JtiTracker()
    past_exp = int(time.time()) - 1
    # Pre-insert with expired timestamp
    tracker._seen["stale"] = past_exp
    # record_if_new should evict the expired entry, then record fresh
    assert tracker.record_if_new("stale", int(time.time()) + 30) is True
