"""Tests for the Secrets Broker grant ledger + lifecycle service (P0)."""
import pytest

from tinyagentos.broker.providers import PROVIDERS, get_provider
from tinyagentos.broker.service import MAX_WINDOW_SECONDS, BrokerService
from tinyagentos.broker.store import BrokerStore

# A fixed injectable "now" so expiry tests are deterministic.
NOW = 1_000_000.0


async def _service(tmp_path):
    store = BrokerStore(tmp_path / "broker.db")
    await store.init()
    return BrokerService(store), store


@pytest.mark.asyncio
async def test_request_approve_authorizes_until_expiry(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "exa")
    await svc.approve(gid, 3600, now=NOW)

    assert await svc.is_authorized("agent-a", "exa", now=NOW) is True
    assert await svc.is_authorized("agent-a", "exa", now=NOW + 1800) is True
    # After expiry the grant is no longer authorized (check-time enforcement).
    assert await svc.is_authorized("agent-a", "exa", now=NOW + 3601) is False


@pytest.mark.asyncio
async def test_deny_blocks_authorization(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "exa")
    assert await svc.deny(gid) is True
    assert await svc.is_authorized("agent-a", "exa", now=NOW) is False


@pytest.mark.asyncio
async def test_revoke_blocks_and_deletes_virtual_creds(tmp_path):
    svc, store = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "openai")
    result = await svc.approve(gid, 3600, now=NOW)
    token = result["token"]
    assert await store.lookup_virtual_cred(token) is not None

    assert await svc.revoke(gid) is True
    assert await svc.is_authorized("agent-a", "openai", now=NOW) is False
    assert await store.lookup_virtual_cred(token) is None


@pytest.mark.asyncio
async def test_deny_by_default_no_grant(tmp_path):
    svc, _ = await _service(tmp_path)
    assert await svc.is_authorized("agent-a", "exa", now=NOW) is False


@pytest.mark.asyncio
async def test_approve_caps_window_at_seven_days(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "exa")
    result = await svc.approve(gid, MAX_WINDOW_SECONDS + 100_000, now=NOW)
    assert result["expires_at"] == NOW + MAX_WINDOW_SECONDS
    # Authorized at the cap boundary minus a tick, not beyond.
    assert await svc.is_authorized("agent-a", "exa", now=NOW + MAX_WINDOW_SECONDS - 1) is True
    assert await svc.is_authorized("agent-a", "exa", now=NOW + MAX_WINDOW_SECONDS + 1) is False


@pytest.mark.asyncio
async def test_approve_rejects_non_positive_window(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "exa")
    with pytest.raises(ValueError):
        await svc.approve(gid, 0, now=NOW)
    with pytest.raises(ValueError):
        await svc.approve(gid, -5, now=NOW)


@pytest.mark.asyncio
async def test_request_access_unknown_provider_raises(tmp_path):
    svc, _ = await _service(tmp_path)
    with pytest.raises(ValueError):
        await svc.request_access("agent-a", "not-a-provider")


@pytest.mark.asyncio
async def test_http_proxy_mints_and_authorizes_token(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "openai")
    result = await svc.approve(gid, 3600, now=NOW)
    token = result["token"]
    assert token.startswith("sk-taos-secret-")

    ctx = await svc.authorize_token(token, now=NOW)
    assert ctx == {
        "agent_identity": "agent-a",
        "provider_id": "openai",
        "scopes": get_provider("openai").default_scopes,
    }

    # After revoke the token resolves to nothing.
    await svc.revoke(gid)
    assert await svc.authorize_token(token, now=NOW) is None


@pytest.mark.asyncio
async def test_authorize_token_none_after_expiry(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "openai")
    token = (await svc.approve(gid, 3600, now=NOW))["token"]
    assert await svc.authorize_token(token, now=NOW) is not None
    assert await svc.authorize_token(token, now=NOW + 3601) is None


@pytest.mark.asyncio
async def test_authorize_token_unknown_returns_none(tmp_path):
    svc, _ = await _service(tmp_path)
    assert await svc.authorize_token("sk-taos-secret-nope", now=NOW) is None


@pytest.mark.asyncio
async def test_sweep_expired_flips_active_grants(tmp_path):
    svc, store = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "exa")
    await svc.approve(gid, 3600, now=NOW)

    # Nothing to sweep before expiry.
    assert await svc.sweep_expired(now=NOW) == 0
    # After expiry the active grant flips to expired and is counted once.
    assert await svc.sweep_expired(now=NOW + 3601) == 1
    grant = await store.get_grant(gid)
    assert grant["status"] == "expired"
    # Idempotent: a second sweep finds nothing.
    assert await svc.sweep_expired(now=NOW + 3601) == 0


@pytest.mark.asyncio
async def test_mcp_provider_approve_mints_no_token(tmp_path):
    svc, _ = await _service(tmp_path)
    assert PROVIDERS["exa"].kind == "mcp"
    gid = await svc.request_access("agent-a", "exa")
    result = await svc.approve(gid, 3600, now=NOW)
    assert "token" not in result


@pytest.mark.asyncio
async def test_empty_scope_grant_still_authorizes(tmp_path):
    svc, _ = await _service(tmp_path)
    gid = await svc.request_access("agent-a", "exa", scopes=[])
    await svc.approve(gid, 3600, now=NOW)
    assert await svc.is_authorized("agent-a", "exa", now=NOW) is True


@pytest.mark.asyncio
async def test_approve_rejects_non_pending_grant(tmp_path):
    """A denied or revoked grant can never be resurrected by approve."""
    svc, _ = await _service(tmp_path)
    # denied -> approve refused
    gid = await svc.request_access("agent-x", "exa")
    await svc.deny(gid)
    with pytest.raises(ValueError):
        await svc.approve(gid, 3600)
    assert await svc.is_authorized("agent-x", "exa") is False
    # revoked -> approve refused
    gid2 = await svc.request_access("agent-y", "exa")
    await svc.approve(gid2, 3600)
    await svc.revoke(gid2)
    with pytest.raises(ValueError):
        await svc.approve(gid2, 3600)
    assert await svc.is_authorized("agent-y", "exa") is False
