"""Regression tests for per-agent local-token bindings store defects.

Two defects from the initial #2682 implementation:
  1. Unsynchronised read-modify-write: two concurrent deploys could interleave
     their read-read-write-write cycles and drop one another's binding.
  2. Corrupt or mis-shaped bindings file: JSONDecodeError / OSError / wrong
     top-level type silently reset the map to ``{}``, then overwrote the file,
     wiping every previously-bound agent's token.
"""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

from tinyagentos.auth import AuthManager, AuthStoreCorruptError


class TestBindLocalTokenAgentConcurrency:
    """Two concurrent deploys must both survive in the bindings file."""

    def test_two_racing_binds_leave_both_hashes(self, tmp_path):
        mgr = AuthManager(tmp_path)
        path = mgr._local_token_agent_path()
        errors: list[Exception] = []
        errors_lock = threading.Lock()

        def bind_alice():
            try:
                mgr.bind_local_token_agent("alice-token", "alice")
            except Exception as exc:
                with errors_lock:
                    errors.append(exc)

        def bind_bob():
            try:
                mgr.bind_local_token_agent("bob-token", "bob")
            except Exception as exc:
                with errors_lock:
                    errors.append(exc)

        t1 = threading.Thread(target=bind_alice)
        t2 = threading.Thread(target=bind_bob)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"bind raised: {errors}"

        data = json.loads(path.read_text())
        alice_hash = hashlib.sha256("alice-token".encode()).hexdigest()
        bob_hash = hashlib.sha256("bob-token".encode()).hexdigest()

        assert alice_hash in data, f"alice hash missing from {data}"
        assert bob_hash in data, f"bob hash missing from {data}"
        assert data[alice_hash] == "alice"
        assert data[bob_hash] == "bob"


class TestBindLocalTokenAgentCorruptStore:
    """A corrupt bindings file must not be wiped by the next bind."""

    def test_malformed_json_raises_and_preserves_file(self, tmp_path):
        mgr = AuthManager(tmp_path)
        path = mgr._local_token_agent_path()

        # Seed a valid binding first.
        mgr.bind_local_token_agent("first-token", "first")

        # Corrupt the file with truncated JSON.
        path.write_bytes(b"{ truncated")

        with pytest.raises(AuthStoreCorruptError):
            mgr.bind_local_token_agent("second-token", "second")

        # The file must NOT have been replaced with an empty dict.
        assert path.read_bytes() == b"{ truncated"

    def test_json_array_raises_and_preserves_file(self, tmp_path):
        mgr = AuthManager(tmp_path)
        path = mgr._local_token_agent_path()

        mgr.bind_local_token_agent("first-token", "first")

        # Overwrite with a JSON array (wrong top-level type).
        path.write_text('["not", "a", "dict"]')

        with pytest.raises(AuthStoreCorruptError):
            mgr.bind_local_token_agent("second-token", "second")

        assert path.read_text() == '["not", "a", "dict"]'

    def test_get_local_token_agent_array_shape_returns_none(self, tmp_path):
        mgr = AuthManager(tmp_path)
        path = mgr._local_token_agent_path()
        path.write_text('["not", "a", "dict"]')

        assert mgr.get_local_token_agent("any-token") is None

    def test_validate_local_token_array_shape_returns_false(self, tmp_path):
        mgr = AuthManager(tmp_path)
        path = mgr._local_token_agent_path()
        path.write_text('["not", "a", "dict"]')

        assert mgr.validate_local_token("any-token") is False

    def test_prior_valid_binding_survives_corrupt_overwrite_attempt(self, tmp_path):
        """If the file is corrupt, the next bind must not wipe it."""
        mgr = AuthManager(tmp_path)
        path = mgr._local_token_agent_path()

        mgr.bind_local_token_agent("first-token", "first")
        original_content = path.read_bytes()

        # Simulate corruption.
        path.write_bytes(b"{ truncated")

        with pytest.raises(AuthStoreCorruptError):
            mgr.bind_local_token_agent("second-token", "second")

        # The corrupt bytes must still be there (not replaced by {}).
        assert path.read_bytes() == b"{ truncated"

        # The original valid binding was stored correctly before corruption.
        alice_hash = hashlib.sha256("first-token".encode()).hexdigest()
        original_data = json.loads(original_content)
        assert original_data[alice_hash] == "first"
