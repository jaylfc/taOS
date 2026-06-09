"""Tests for tinyagentos/auth_context.py helpers.

Covers:
  - current_user: 401 when request.state.user_id is unset / falsy
  - current_user: returns correct CurrentUser with is_admin
  - require_owner_or_admin: owner allowed, admin allowed, stranger -> 403
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(user_id=None, is_admin=False):
    """Build a minimal mock Request whose state carries the given attrs."""
    state = MagicMock()
    # Simulate getattr(request.state, "user_id", None) behaviour
    state.user_id = user_id
    state.is_admin = is_admin
    req = MagicMock()
    req.state = state
    return req


# ---------------------------------------------------------------------------
# current_user dependency
# ---------------------------------------------------------------------------

class TestCurrentUser:

    def test_401_when_user_id_not_set(self):
        """No user_id on request.state -> 401."""
        req = MagicMock()
        # getattr(request.state, "user_id", None) falls through to default
        del req.state.user_id  # AttributeError -> getattr returns None
        req.state = MagicMock(spec=[])  # empty spec: no attributes
        with pytest.raises(HTTPException) as exc_info:
            current_user(req)
        assert exc_info.value.status_code == 401

    def test_401_when_user_id_is_none(self):
        req = _make_request(user_id=None, is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            current_user(req)
        assert exc_info.value.status_code == 401

    def test_401_when_user_id_is_empty_string(self):
        req = _make_request(user_id="", is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            current_user(req)
        assert exc_info.value.status_code == 401

    def test_returns_current_user_with_correct_fields(self):
        req = _make_request(user_id="uid-123", is_admin=True)
        user = current_user(req)
        assert user.user_id == "uid-123"
        assert user.is_admin is True

    def test_is_admin_false_for_non_admin(self):
        req = _make_request(user_id="uid-456", is_admin=False)
        user = current_user(req)
        assert user.user_id == "uid-456"
        assert user.is_admin is False

    def test_current_user_is_frozen(self):
        """CurrentUser is a frozen dataclass — mutation must raise."""
        user = CurrentUser(user_id="u1", is_admin=False)
        with pytest.raises((AttributeError, TypeError)):
            user.user_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# require_owner_or_admin
# ---------------------------------------------------------------------------

class TestRequireOwnerOrAdmin:

    def test_owner_is_allowed(self):
        user = CurrentUser(user_id="alice", is_admin=False)
        # Should not raise
        require_owner_or_admin(user, "alice")

    def test_admin_is_allowed_for_any_resource(self):
        user = CurrentUser(user_id="alice", is_admin=True)
        # Should not raise even for a resource owned by someone else
        require_owner_or_admin(user, "bob")

    def test_stranger_gets_403(self):
        user = CurrentUser(user_id="alice", is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(user, "bob")
        assert exc_info.value.status_code == 403

    def test_403_detail_is_forbidden(self):
        user = CurrentUser(user_id="alice", is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(user, "charlie")
        assert exc_info.value.detail == "forbidden"
