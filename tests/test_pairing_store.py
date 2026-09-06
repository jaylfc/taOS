import hashlib
import time

import pytest

from tinyagentos.cluster.pairing_store import ClusterPairingStore, _EXPIRY_SECS, _MAX_ATTEMPTS


async def _store(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    await s.init()
    return s


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


# ------------------------------------------------------------------
# announce
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_announce_creates_row(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    row = await s._fetch_row("w1")
    assert row is not None
    assert row["name"] == "w1"
    assert row["pending_url"] == "http://w1:8080"
    assert row["pending_platform"] == "linux"
    assert row["pending_code_hash"] == _hash("code1")
    assert row["confirmed"] == 0
    assert row["claim_attempts"] == 0
    assert row["signing_key"] is None
    await s.close()


@pytest.mark.asyncio
async def test_announce_resets_confirmed_and_attempts(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    row = await s._fetch_row("w1")
    assert row["confirmed"] == 1

    # re-announce should reset confirmed and attempts
    await s.announce("w1", "http://w1:9090", "macos", _hash("code2"))
    row = await s._fetch_row("w1")
    assert row["confirmed"] == 0
    assert row["claim_attempts"] == 0
    assert row["pending_url"] == "http://w1:9090"
    assert row["pending_platform"] == "macos"
    assert row["pending_code_hash"] == _hash("code2")
    # signing_key from the first confirm must survive the re-announce
    assert row["signing_key"] is not None
    await s.close()


# ------------------------------------------------------------------
# confirm
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_success(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    result = await s.confirm("w1", "code1")
    assert result is True
    row = await s._fetch_row("w1")
    assert row["confirmed"] == 1
    assert row["signing_key"] is not None
    assert len(row["signing_key"]) == 32
    assert row["confirmed_ts"] is not None
    await s.close()


@pytest.mark.asyncio
async def test_confirm_wrong_code_returns_false_and_increments_attempts(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    result = await s.confirm("w1", "wrong")
    assert result is False
    row = await s._fetch_row("w1")
    assert row["claim_attempts"] == 1
    assert row["confirmed"] == 0
    assert row["signing_key"] is None
    await s.close()


@pytest.mark.asyncio
async def test_confirm_unknown_name_returns_false(tmp_path):
    s = await _store(tmp_path)
    result = await s.confirm("nonexistent", "code1")
    assert result is False
    await s.close()


@pytest.mark.asyncio
async def test_confirm_expired_returns_false(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    # force the pending_ts to be past the expiry window
    old_ts = time.time() - _EXPIRY_SECS - 1
    await s._db.execute(
        "UPDATE cluster_pairings SET pending_ts = ? WHERE name = ?",
        (old_ts, "w1"),
    )
    await s._db.commit()
    result = await s.confirm("w1", "code1")
    assert result is False
    await s.close()


@pytest.mark.asyncio
async def test_confirm_max_attempts_returns_false(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    # exhaust attempts with wrong codes
    for _ in range(_MAX_ATTEMPTS):
        await s.confirm("w1", "wrong")
    row = await s._fetch_row("w1")
    assert row["claim_attempts"] == _MAX_ATTEMPTS
    # even the right code should now fail
    result = await s.confirm("w1", "code1")
    assert result is False
    await s.close()


# ------------------------------------------------------------------
# claim
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_success_returns_key_and_clears_pending(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    key = await s.claim("w1", "code1")
    assert key is not None
    assert isinstance(key, bytes)
    assert len(key) == 32
    row = await s._fetch_row("w1")
    assert row["pending_code_hash"] is None
    assert row["pending_url"] is None
    assert row["pending_platform"] is None
    assert row["pending_ts"] is None
    assert row["confirmed"] == 0
    assert row["claim_attempts"] == 0
    # signing_key stays in the db after claim
    assert row["signing_key"] is not None
    await s.close()


@pytest.mark.asyncio
async def test_claim_before_confirm_returns_none(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    key = await s.claim("w1", "code1")
    assert key is None
    await s.close()


@pytest.mark.asyncio
async def test_claim_wrong_code_returns_none_and_increments_attempts(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    key = await s.claim("w1", "wrong")
    assert key is None
    row = await s._fetch_row("w1")
    assert row["claim_attempts"] == 1
    await s.close()


@pytest.mark.asyncio
async def test_claim_unknown_name_returns_none(tmp_path):
    s = await _store(tmp_path)
    key = await s.claim("nonexistent", "code1")
    assert key is None
    await s.close()


@pytest.mark.asyncio
async def test_claim_expired_returns_none(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    old_ts = time.time() - _EXPIRY_SECS - 1
    await s._db.execute(
        "UPDATE cluster_pairings SET pending_ts = ? WHERE name = ?",
        (old_ts, "w1"),
    )
    await s._db.commit()
    key = await s.claim("w1", "code1")
    assert key is None
    await s.close()


@pytest.mark.asyncio
async def test_claim_max_attempts_returns_none(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    # exhaust attempts with wrong codes
    for _ in range(_MAX_ATTEMPTS):
        await s.claim("w1", "wrong")
    key = await s.claim("w1", "code1")
    assert key is None
    await s.close()


# ------------------------------------------------------------------
# get_signing_key
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_signing_key_returns_none_when_not_paired(tmp_path):
    s = await _store(tmp_path)
    key = await s.get_signing_key("w1")
    assert key is None
    await s.close()


@pytest.mark.asyncio
async def test_get_signing_key_returns_none_before_confirm(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    key = await s.get_signing_key("w1")
    assert key is None
    await s.close()


@pytest.mark.asyncio
async def test_get_signing_key_returns_key_after_confirm(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    key = await s.get_signing_key("w1")
    assert key is not None
    assert isinstance(key, bytes)
    assert len(key) == 32
    await s.close()


@pytest.mark.asyncio
async def test_get_signing_key_persists_after_claim(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    claimed = await s.claim("w1", "code1")
    key = await s.get_signing_key("w1")
    assert key is not None
    assert key == claimed
    await s.close()


# ------------------------------------------------------------------
# record_failed_attempt
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_failed_attempt_increments_counter(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.record_failed_attempt("w1")
    row = await s._fetch_row("w1")
    assert row["claim_attempts"] == 1
    await s.close()


@pytest.mark.asyncio
async def test_record_failed_attempt_on_unknown_name_noop(tmp_path):
    s = await _store(tmp_path)
    # should not raise
    await s.record_failed_attempt("nonexistent")
    await s.close()


# ------------------------------------------------------------------
# pairing_state
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pairing_state_returns_none_for_unknown(tmp_path):
    s = await _store(tmp_path)
    state = await s.pairing_state("nonexistent")
    assert state is None
    await s.close()


@pytest.mark.asyncio
async def test_pairing_state_after_announce(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    state = await s.pairing_state("w1")
    assert state is not None
    assert state["has_pending"] is True
    assert state["confirmed"] is False
    assert state["expired"] is False
    assert state["attempts_capped"] is False
    await s.close()


@pytest.mark.asyncio
async def test_pairing_state_after_confirm(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    state = await s.pairing_state("w1")
    assert state["has_pending"] is True
    assert state["confirmed"] is True
    assert state["expired"] is False
    assert state["attempts_capped"] is False
    await s.close()


@pytest.mark.asyncio
async def test_pairing_state_expired(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    old_ts = time.time() - _EXPIRY_SECS - 1
    await s._db.execute(
        "UPDATE cluster_pairings SET pending_ts = ? WHERE name = ?",
        (old_ts, "w1"),
    )
    await s._db.commit()
    state = await s.pairing_state("w1")
    assert state["expired"] is True
    await s.close()


@pytest.mark.asyncio
async def test_pairing_state_attempts_capped(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    for _ in range(_MAX_ATTEMPTS):
        await s.confirm("w1", "wrong")
    state = await s.pairing_state("w1")
    assert state["attempts_capped"] is True
    await s.close()


@pytest.mark.asyncio
async def test_pairing_state_no_pending_after_claim(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.confirm("w1", "code1")
    await s.claim("w1", "code1")
    state = await s.pairing_state("w1")
    assert state["has_pending"] is False
    assert state["confirmed"] is False
    await s.close()


# ------------------------------------------------------------------
# list_pending
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_empty(tmp_path):
    s = await _store(tmp_path)
    pending = await s.list_pending()
    assert pending == []
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_returns_announced_workers(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.announce("w2", "http://w2:8080", "macos", _hash("code2"))
    pending = await s.list_pending()
    assert len(pending) == 2
    names = {p["name"] for p in pending}
    assert names == {"w1", "w2"}
    for p in pending:
        assert "url" in p
        assert "platform" in p
        assert "announced_at" in p
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_excludes_confirmed(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.announce("w2", "http://w2:8080", "macos", _hash("code2"))
    await s.confirm("w1", "code1")
    pending = await s.list_pending()
    assert len(pending) == 1
    assert pending[0]["name"] == "w2"
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_excludes_expired(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    old_ts = time.time() - _EXPIRY_SECS - 1
    await s._db.execute(
        "UPDATE cluster_pairings SET pending_ts = ? WHERE name = ?",
        (old_ts, "w1"),
    )
    await s._db.commit()
    pending = await s.list_pending()
    assert pending == []
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_excludes_max_attempts(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    for _ in range(_MAX_ATTEMPTS):
        await s.confirm("w1", "wrong")
    pending = await s.list_pending()
    assert pending == []
    await s.close()


@pytest.mark.asyncio
async def test_list_pending_ordered_by_announced_at(tmp_path):
    s = await _store(tmp_path)
    await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))
    await s.announce("w2", "http://w2:8080", "macos", _hash("code2"))
    # force distinct pending_ts so ordering is deterministic regardless of
    # clock granularity
    older_ts = time.time() - 10
    newer_ts = time.time()
    await s._db.execute(
        "UPDATE cluster_pairings SET pending_ts = ? WHERE name = ?",
        (older_ts, "w1"),
    )
    await s._db.execute(
        "UPDATE cluster_pairings SET pending_ts = ? WHERE name = ?",
        (newer_ts, "w2"),
    )
    await s._db.commit()
    pending = await s.list_pending()
    assert pending[0]["name"] == "w1"
    assert pending[1]["name"] == "w2"
    assert pending[0]["announced_at"] <= pending[1]["announced_at"]
    await s.close()


# ------------------------------------------------------------------
# uninitialised store raises
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_announce_without_init_raises(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    with pytest.raises(RuntimeError, match="not initialised"):
        await s.announce("w1", "http://w1:8080", "linux", _hash("code1"))


@pytest.mark.asyncio
async def test_confirm_without_init_raises(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    with pytest.raises(RuntimeError, match="not initialised"):
        await s.confirm("w1", "code1")


@pytest.mark.asyncio
async def test_claim_without_init_raises(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    with pytest.raises(RuntimeError, match="not initialised"):
        await s.claim("w1", "code1")


@pytest.mark.asyncio
async def test_get_signing_key_without_init_raises(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    with pytest.raises(RuntimeError, match="not initialised"):
        await s.get_signing_key("w1")


@pytest.mark.asyncio
async def test_pairing_state_without_init_raises(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    with pytest.raises(RuntimeError, match="not initialised"):
        await s.pairing_state("w1")


@pytest.mark.asyncio
async def test_list_pending_without_init_raises(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairings.db")
    with pytest.raises(RuntimeError, match="not initialised"):
        await s.list_pending()


# ------------------------------------------------------------------
# manual pairing (free-tier: admin authorises {url, code}, worker polls)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_claim_before_authorize_is_none(tmp_path):
    s = await _store(tmp_path)
    # Worker polls before the admin has entered the IP + code.
    assert await s.manual_claim("w1", "ABCD2345") is None
    await s.close()


@pytest.mark.asyncio
async def test_manual_authorize_then_claim_returns_key_and_url(tmp_path):
    s = await _store(tmp_path)
    await s.manual_authorize("http://192.168.1.50:6970", "ABCD2345")
    res = await s.manual_claim("w1", "ABCD2345")
    assert res is not None
    key, url = res
    assert len(key) == 32
    assert url == "http://192.168.1.50:6970"
    # The key is persisted under the worker name so its signed requests authenticate.
    assert await s.get_signing_key("w1") == key
    await s.close()


@pytest.mark.asyncio
async def test_manual_claim_is_single_use(tmp_path):
    s = await _store(tmp_path)
    await s.manual_authorize("http://10.0.0.9:6970", "WXYZ7890")
    first = await s.manual_claim("w1", "WXYZ7890")
    assert first is not None
    # A second claim of the same code must fail (single-use).
    assert await s.manual_claim("w1", "WXYZ7890") is None
    await s.close()


@pytest.mark.asyncio
async def test_manual_claim_wrong_code_is_none(tmp_path):
    s = await _store(tmp_path)
    await s.manual_authorize("http://10.0.0.9:6970", "RIGHT000")
    assert await s.manual_claim("w1", "WRONG000") is None
    # The right code still works afterward (a wrong guess does not consume it).
    assert await s.manual_claim("w1", "RIGHT000") is not None
    await s.close()


@pytest.mark.asyncio
async def test_manual_authorize_replaces_prior_for_same_code(tmp_path):
    s = await _store(tmp_path)
    await s.manual_authorize("http://1.1.1.1:6970", "SAMECODE")
    await s.manual_authorize("http://2.2.2.2:6970", "SAMECODE")
    res = await s.manual_claim("w1", "SAMECODE")
    assert res is not None
    _, url = res
    assert url == "http://2.2.2.2:6970"
    await s.close()


@pytest.mark.asyncio
async def test_manual_claim_expired_is_none(tmp_path, monkeypatch):
    s = await _store(tmp_path)
    await s.manual_authorize("http://10.0.0.9:6970", "EXPIRE00")
    import tinyagentos.cluster.pairing_store as ps
    base = time.time()
    monkeypatch.setattr(ps, "_now", lambda: base + _EXPIRY_SECS + 1)
    assert await s.manual_claim("w1", "EXPIRE00") is None
    await s.close()
