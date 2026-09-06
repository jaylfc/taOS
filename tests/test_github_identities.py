"""Unit tests for GitHubIdentitiesStore add/list/get_token/delete."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tinyagentos.github_identities import GitHubIdentitiesStore


async def _store(tmp_path):
    s = GitHubIdentitiesStore(tmp_path / "github_identities.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_add_returns_public_fields_without_token(tmp_path):
    store = await _store(tmp_path)
    try:
        with patch("tinyagentos.github_identities.time.time", return_value=1000):
            identity = await store.add("octocat", "https://avatars/octocat.png", "gho_secret", "repo")

        assert set(identity.keys()) == {"id", "login", "avatar_url", "created_at"}
        assert identity["login"] == "octocat"
        assert identity["avatar_url"] == "https://avatars/octocat.png"
        assert identity["created_at"] == 1000
        assert "token" not in identity
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_excludes_token(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.add("octocat", "https://avatars/octocat.png", "gho_secret", "repo")
        identities = await store.list()

        assert len(identities) == 1
        assert set(identities[0].keys()) == {"id", "login", "avatar_url", "created_at"}
        assert "token" not in identities[0]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_orders_by_created_at_desc(tmp_path):
    store = await _store(tmp_path)
    try:
        with patch("tinyagentos.github_identities.time.time", return_value=100):
            await store.add("first", "", "tok1", "")
        with patch("tinyagentos.github_identities.time.time", return_value=300):
            await store.add("third", "", "tok3", "")
        with patch("tinyagentos.github_identities.time.time", return_value=200):
            await store.add("second", "", "tok2", "")

        identities = await store.list()
        assert [i["login"] for i in identities] == ["third", "second", "first"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconnect_same_login_updates_in_place(tmp_path):
    store = await _store(tmp_path)
    try:
        with patch("tinyagentos.github_identities.time.time", return_value=1000):
            first = await store.add("octocat", "a1", "gho_token1", "repo")
        second = await store.add("octocat", "a2", "gho_token2", "repo,user")

        assert first["id"] == second["id"]
        assert first["created_at"] == second["created_at"]
        assert second["avatar_url"] == "a2"
        assert len(await store.list()) == 1
        assert await store.get_token(first["id"]) == "gho_token2"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_token_decrypts_stored_token(tmp_path):
    store = await _store(tmp_path)
    try:
        identity = await store.add("octocat", "", "gho_plaintext", "repo")
        assert await store.get_token(identity["id"]) == "gho_plaintext"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_token_returns_none_for_unknown_id(tmp_path):
    store = await _store(tmp_path)
    try:
        assert await store.get_token("missing-id") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_returns_true_and_removes_row(tmp_path):
    store = await _store(tmp_path)
    try:
        identity = await store.add("octocat", "", "gho_secret", "repo")
        deleted = await store.delete(identity["id"])

        assert deleted is True
        assert await store.list() == []
        assert await store.get_token(identity["id"]) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_returns_false_for_missing_id(tmp_path):
    store = await _store(tmp_path)
    try:
        assert await store.delete("missing-id") is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_token_encrypted_at_rest(tmp_path):
    store = await _store(tmp_path)
    try:
        identity = await store.add("octocat", "", "gho_plaintexttoken", "repo")
        async with store._db.execute(
            "SELECT token FROM github_identities WHERE id = ?", (identity["id"],)
        ) as cur:
            row = await cur.fetchone()

        assert row[0] != "gho_plaintexttoken"
        assert "gho_plaintexttoken" not in row[0]
        assert await store.get_token(identity["id"]) == "gho_plaintexttoken"
    finally:
        await store.close()