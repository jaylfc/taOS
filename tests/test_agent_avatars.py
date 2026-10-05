"""Tests for tinyagentos.agent_avatars module (avatar_source_path + avatar_hash)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tinyagentos import agent_avatars as aa
from tinyagentos.agent_avatars import avatar_hash, avatar_source_path
from tinyagentos.routes.auth import _avatar_slug


class TestAvatarSourcePathAndHash:

    def test_no_jpg_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(aa, "LOCK_AVATAR_DIR", str(tmp_path))
        assert avatar_source_path("Some Agent") is None
        assert avatar_hash("Some Agent") is None

    def test_existing_jpg_returns_path_and_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(aa, "LOCK_AVATAR_DIR", str(tmp_path))
        name = "Some Agent"
        slug = _avatar_slug(name)
        (tmp_path / f"{slug}.jpg").write_bytes(b"one")
        assert avatar_source_path(name) == tmp_path / f"{slug}.jpg"
        h = avatar_hash(name)
        assert isinstance(h, str)
        assert len(h) == 16
        assert h == hashlib.sha256(b"one").hexdigest()[:16]

    def test_hash_changes_when_file_changes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(aa, "LOCK_AVATAR_DIR", str(tmp_path))
        name = "Some Agent"
        slug = _avatar_slug(name)
        p = tmp_path / f"{slug}.jpg"
        p.write_bytes(b"one")
        first = avatar_hash(name)
        p.write_bytes(b"two")
        second = avatar_hash(name)
        assert first != second

    def test_imported_slug_matches_module_slug(self):
        name = "Some Agent"
        assert _avatar_slug(name) == aa._avatar_slug(name)
