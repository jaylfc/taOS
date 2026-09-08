"""PasswordResetStore tests.

These reach the store ONLY through the real ``create_app`` factory wiring
(``app.state.password_reset``), never by constructing ``PasswordResetStore``
directly for the behaviour under test. Constructing a bare instance is used
in exactly one place -- ``test_uninitialised_store_raises`` -- to prove the
guard on an uninitialised store; that is the narrow exception, not the rule.
"""
from __future__ import annotations

import asyncio
import hashlib

import aiosqlite
import pytest
import pytest_asyncio

from tinyagentos.app import create_app
from tinyagentos.password_reset_store import PasswordResetStore


def _hash(token: str) -> str:
    """Reproduce the store's token hash (sha256) so tests can address a
    token's row. Mirrors what the /api/password route does: hash inline, no
    method dependency on the store under test."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def password_store(app):
    """Reach the store through the real app factory wiring."""
    store = app.state.password_reset
    await store.init()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# (a) DOUBLE-SPEND: two concurrent consumes, exactly one wins -- proven by a
# race, not by calling them in sequence. The atomic UPDATE ... WHERE used=0
# must lose the second one.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_spend_allows_exactly_one(password_store):
    token = await password_store.mint("user-1")
    token_hash = _hash(token)

    # Gather runs both consumes against the SAME store concurrently; the
    # conditional UPDATE is the only thing that can make this lose one.
    results = await asyncio.gather(
        password_store.consume(token_hash),
        password_store.consume(token_hash),
    )
    successes = sum(1 for ok in results if ok)
    assert successes == 1


# ---------------------------------------------------------------------------
# (b) LOOKUP by token_hash ALONE -- never by an empty/placeholder user_id.
# A reset flow is unauthenticated, so the caller has no user_id to supply.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_without_user_id(password_store):
    token = await password_store.mint("the-real-user")
    token_hash = _hash(token)

    row = await password_store.get_by_token_hash(token_hash)
    assert row is not None
    assert row["user_id"] == "the-real-user"
    assert row["used"] == 0
    assert "token_hash" in row and row["token_hash"] == token_hash


# ---------------------------------------------------------------------------
# (c) PRIOR-TOKEN INVALIDATION: a freshly minted token kills the user's
# earlier unused token.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prior_token_invalidation(password_store):
    first = await password_store.mint("user-1")
    second = await password_store.mint("user-1")
    h1 = _hash(first)
    h2 = _hash(second)

    # Minting `second` invalidated `first` atomically.
    assert await password_store.is_valid(h1) is False
    assert await password_store.consume(h1) is False

    # The newer token must still be good and consumable.
    assert await password_store.is_valid(h2) is True
    assert await password_store.consume(h2) is True


# ---------------------------------------------------------------------------
# (d) NO PLAINTEXT: the minted token value never appears in the stored row.
# Asserted on the DB contents (an independent connection), not the return.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_plaintext(password_store, tmp_data_dir):
    token = await password_store.mint("user-1")
    token_hash = _hash(token)

    # Independent connection -- does not go through the store's API at all.
    async with aiosqlite.connect(str(password_store.db_path)) as db:
        row = await (
            await db.execute("SELECT * FROM password_resets")
        ).fetchone()
    assert row is not None
    stored = " ".join("" if v is None else str(v) for v in row)

    assert token_hash in stored       # the hash IS persisted
    assert token not in stored        # the plaintext is NOT persisted anywhere


# ---------------------------------------------------------------------------
# Correctness guards (green on both the defective and correct shapes, so they
# do not pollute the defect-specific red window).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roundtrip_valid_then_consumed(password_store):
    token = await password_store.mint("user-1")
    h = _hash(token)

    assert await password_store.is_valid(h) is True
    assert await password_store.consume(h) is True
    assert await password_store.is_valid(h) is False
    assert await password_store.consume(h) is False


@pytest.mark.asyncio
async def test_unknown_token_is_invalid(password_store):
    assert await password_store.is_valid("does-not-exist") is False
    assert await password_store.consume("does-not-exist") is False


@pytest.mark.asyncio
async def test_expired_token_is_invalid(password_store):
    token = await password_store.mint("user-1", ttl_seconds=0)
    h = _hash(token)

    assert await password_store.is_valid(h) is False
    assert await password_store.consume(h) is False


@pytest.mark.asyncio
async def test_mint_invalidates_only_self_user(password_store):
    other_first = await password_store.mint("user-other")
    mine = await password_store.mint("user-mine")
    _more = await password_store.mint("user-mine")  # invalidates `mine`

    # Another user's token is untouched by our new mint.
    assert await password_store.is_valid(_hash(other_first)) is True
    # Our earlier token was invalidated by the re-mint.
    assert await password_store.is_valid(_hash(mine)) is False


@pytest.mark.asyncio
async def test_uninitialised_store_raises(tmp_path):
    store = PasswordResetStore(tmp_path / "password_resets.db")
    with pytest.raises(RuntimeError, match="init"):
        await store.is_valid("x")
    with pytest.raises(RuntimeError, match="init"):
        await store.consume("x")
    with pytest.raises(RuntimeError, match="init"):
        await store.mint("user-1")
    await store.close()
