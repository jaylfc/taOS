"""Route-level tests for POST /api/agent-model-keys (mint) — agent_id validation.

The mint endpoint must reject agent_ids that contain path traversal characters
before they ever reach the store, regardless of authentication.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _ensure_agent_model_key_store(client, tmp_path_factory):
    """Init app.state.agent_model_keys on a fresh DB; the test client registers
    the store but does not run the lifespan that init()s it (production does).
    Uses async fixture so init runs on the same event loop as the tests."""
    store = client._transport.app.state.agent_model_keys
    if store._db is not None:
        try:
            await store.close()
        except Exception:
            pass
    tmp_dir = tmp_path_factory.mktemp("agent_model_keys_route_test")
    store.db_path = tmp_dir / "agent_model_keys.db"
    await store.init()
    yield
    try:
        await store.close()
    except Exception:
        pass


@pytest.mark.asyncio
class TestAgentModelKeysRouteValidation:
    """Route-level input validation for POST /api/agent-model-keys."""

    @pytest.mark.parametrize(
        "agent_ids",
        [
            ["../../x"],
            ["a/b"],
            ["openai/gpt-4o"],       # real-world LiteLLM model name with /
            ["a\\b"],
            ["/etc/passwd"],
            ["valid", "../../escape"],
            ["a b"],
            [""],
        ],
    )
    async def test_mint_rejects_unsafe_agent_ids(self, client, agent_ids):
        """Handler-level _validate_agent_id rejects path traversal with 400."""
        resp = await client.post(
            "/api/agent-model-keys",
            json={"agent_ids": agent_ids},
        )
        assert resp.status_code == 400, (
            f"expected 400 for agent_ids={agent_ids!r}, "
            f"got {resp.status_code}: {resp.text}"
        )
        # The error body should contain the offending id string (or the
        # "required" message when the list is empty).  The handler uses
        # {v!r} in the ValueError, so special chars (backslashes) appear
        # escaped — use repr(aid) to match.
        body = resp.json()
        assert "error" in body
        if any(aid for aid in agent_ids):
            # At least one non-empty id — check it appears in the error.
            assert any(
                repr(aid).strip("'\"") in body["error"]
                for aid in agent_ids if aid
            ), f"error body missing offending id, body={body}"
        else:
            # All ids are empty strings (e.g. [\"\"]) — error is about
            # invalid format, not a "required" message.
            assert "invalid" in body["error"], (
                f"error body should mention 'invalid', body={body}"
            )

    async def test_mint_allows_safe_agent_ids(self, client):
        """Valid slug-format agent ids pass handler validation and reach the store.
        With the store properly initialised, a successful mint returns the token."""
        resp = await client.post(
            "/api/agent-model-keys",
            json={"agent_ids": ["safe-agent-1", "gpt_4o.test"]},
        )
        # A 422 would mean the regex is too strict and safe slugs are blocked.
        # A 400 would mean handler validation blocked legitimate slugs.
        # A 200 means validation passed and the store minted successfully.
        assert resp.status_code == 200, (
            f"safe slugs should pass validation and mint, "
            f"got {resp.status_code}: {resp.text}"
        )
        assert "key" in resp.json()

    async def test_mint_rejects_empty_agent_ids(self, client):
        """Empty list is rejected with 400 by handler validation."""
        resp = await client.post(
            "/api/agent-model-keys",
            json={"agent_ids": []},
        )
        assert resp.status_code == 400
