"""Tests for the approve-path grant expiry: duration_secs -> expires_at.

The store-level persistence of ``expires_at`` is covered by
``tests/test_agent_grants_store.py`` / ``tests/test_grant_expiry.py``. This
module asserts the *route-level* mapping injected by issue #2985: a scope
request carrying ``duration_secs`` must produce a future, timezone-aware expiry
on approval, and a request without it must stay unbounded.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tinyagentos.routes.agent_auth_requests import _expires_at_from_duration


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class TestExpiresAtFromDuration:
    def test_positive_duration_yields_future_expiry(self):
        now = datetime.now(timezone.utc)
        expires = _expires_at_from_duration(3600)
        assert expires is not None
        parsed = _parse(expires)
        assert parsed.tzinfo is not None, "expiry must be timezone-aware"
        delta = parsed - now
        # allow a small clock skew window around the intended 1 hour
        assert timedelta(minutes=59) < delta <= timedelta(hours=1, minutes=1)

    def test_none_duration_is_unbounded(self):
        assert _expires_at_from_duration(None) is None

    def test_zero_duration_is_unbounded(self):
        assert _expires_at_from_duration(0) is None

    def test_negative_duration_is_unbounded(self):
        assert _expires_at_from_duration(-60) is None

    def test_non_int_duration_is_unbounded(self):
        # a float or string must not produce a bogus expiry
        assert _expires_at_from_duration(3660.5) is None
        assert _expires_at_from_duration("3600") is None

    def test_short_duration_still_in_the_future(self):
        expires = _expires_at_from_duration(30)
        assert expires is not None
        parsed = _parse(expires)
        assert parsed > datetime.now(timezone.utc) - timedelta(seconds=1)