"""Tests for Fernet encryption + XOR→Fernet migration in SecretsStore (Fix B / issue #637)."""
from __future__ import annotations

import base64
import stat

import pytest
import pytest_asyncio

import tinyagentos.secrets as sec_mod
from tinyagentos.secrets import (
    SecretsStore,
    _FERNET_PREFIX,
    _encrypt,
    _decrypt,
    _get_fernet_key,
    _xor_key,
)


@pytest.fixture(autouse=True)
def clear_fernet_cache():
    sec_mod._fernet_key_cache.clear()
    yield
    sec_mod._fernet_key_cache.clear()


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SecretsStore(tmp_path / "secrets.db")
    await s.init()
    yield s
    await s.close()


class TestFernetKeyFile:
    def test_key_file_created_on_first_use(self, tmp_path):
        _get_fernet_key(tmp_path)
        assert (tmp_path / ".secrets_key").exists()

    def test_key_file_is_32_bytes(self, tmp_path):
        _get_fernet_key(tmp_path)
        raw = (tmp_path / ".secrets_key").read_bytes()
        assert len(raw) == 32

    def test_key_file_mode_600(self, tmp_path):
        _get_fernet_key(tmp_path)
        mode = stat.S_IMODE((tmp_path / ".secrets_key").stat().st_mode)
        assert mode == 0o600

    def test_key_stable_across_calls(self, tmp_path):
        k1 = _get_fernet_key(tmp_path)
        k2 = _get_fernet_key(tmp_path)
        assert k1 == k2

    def test_key_stable_after_cache_clear(self, tmp_path):
        k1 = _get_fernet_key(tmp_path)
        sec_mod._fernet_key_cache.clear()
        k2 = _get_fernet_key(tmp_path)
        assert k1 == k2

    def test_malformed_key_file_raises_not_regenerates(self, tmp_path):
        """A key file with wrong length must raise ValueError, not silently regenerate.

        If we regenerated, every already-encrypted secret would become
        unrecoverable (silent data loss).
        """
        key_path = tmp_path / ".secrets_key"
        key_path.write_bytes(b"tooshort")
        with pytest.raises(ValueError, match="Corrupt Fernet key file"):
            _get_fernet_key(tmp_path)
        # The bad file must not have been overwritten.
        assert key_path.read_bytes() == b"tooshort"

    def test_empty_key_file_raises(self, tmp_path):
        """Zero-length key file is also malformed — must not regenerate."""
        key_path = tmp_path / ".secrets_key"
        key_path.write_bytes(b"")
        with pytest.raises(ValueError, match="Corrupt Fernet key file"):
            _get_fernet_key(tmp_path)


class TestFernetEncryptDecrypt:
    def test_roundtrip(self, tmp_path):
        enc = _encrypt("my-api-key", key_dir=tmp_path)
        assert _decrypt(enc, key_dir=tmp_path) == "my-api-key"

    def test_ciphertext_has_prefix(self, tmp_path):
        enc = _encrypt("hello", key_dir=tmp_path)
        assert enc.startswith(_FERNET_PREFIX)

    def test_two_encryptions_differ(self, tmp_path):
        """Fernet uses a random IV per encryption — same plaintext → different token."""
        enc1 = _encrypt("same", key_dir=tmp_path)
        enc2 = _encrypt("same", key_dir=tmp_path)
        assert enc1 != enc2

    def test_xor_migration_transparent(self, tmp_path):
        """_decrypt handles an old XOR blob transparently when key_dir is given."""
        key = _xor_key()
        data = b"old-secret-value"
        xor_blob = base64.b64encode(
            bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        ).decode()
        assert not xor_blob.startswith(_FERNET_PREFIX)
        assert _decrypt(xor_blob, key_dir=tmp_path) == "old-secret-value"


class TestSecretsStoreFernet:
    @pytest.mark.asyncio
    async def test_new_secrets_encrypted_with_fernet(self, store, tmp_path):
        """Secrets added after the upgrade are stored with Fernet ciphertext."""
        await store.add("API_KEY", "sk-newvalue")
        # Inspect raw DB value.
        async with store._db.execute(
            "SELECT value FROM secrets WHERE name = 'API_KEY'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0].startswith(_FERNET_PREFIX)

    @pytest.mark.asyncio
    async def test_fernet_roundtrip_via_store(self, store):
        await store.add("FERNET_KEY", "fernet-test-value")
        result = await store.get("FERNET_KEY")
        assert result is not None
        assert result["value"] == "fernet-test-value"

    @pytest.mark.asyncio
    async def test_xor_migration_on_get(self, store, tmp_path):
        """A secret stored with XOR is transparently migrated to Fernet on first get()."""
        # Insert a raw XOR ciphertext directly into the DB.
        key = _xor_key()
        plaintext = b"legacy-api-key"
        xor_blob = base64.b64encode(
            bytes(b ^ key[i % len(key)] for i, b in enumerate(plaintext))
        ).decode()
        import time as _time
        now = int(_time.time())
        await store._db.execute(
            "INSERT INTO secrets (name, category, value, description, created_at, updated_at) "
            "VALUES (?, 'general', ?, '', ?, ?)",
            ("LEGACY", xor_blob, now, now),
        )
        await store._db.commit()

        # get() should decrypt and return the plaintext.
        result = await store.get("LEGACY")
        assert result is not None
        assert result["value"] == "legacy-api-key"

        # After the first get(), the stored value should now use Fernet.
        async with store._db.execute(
            "SELECT value FROM secrets WHERE name = 'LEGACY'"
        ) as cur:
            row = await cur.fetchone()
        assert row[0].startswith(_FERNET_PREFIX), (
            "XOR ciphertext should have been migrated to Fernet on first read"
        )

    @pytest.mark.asyncio
    async def test_xor_migration_preserves_value_on_second_get(self, store, tmp_path):
        """After migration, get() still returns the correct plaintext."""
        key = _xor_key()
        plaintext = b"migrate-me"
        xor_blob = base64.b64encode(
            bytes(b ^ key[i % len(key)] for i, b in enumerate(plaintext))
        ).decode()
        import time as _time
        now = int(_time.time())
        await store._db.execute(
            "INSERT INTO secrets (name, category, value, description, created_at, updated_at) "
            "VALUES (?, 'general', ?, '', ?, ?)",
            ("MIGRATE", xor_blob, now, now),
        )
        await store._db.commit()

        first = await store.get("MIGRATE")
        second = await store.get("MIGRATE")
        assert first["value"] == "migrate-me"
        assert second["value"] == "migrate-me"

    @pytest.mark.asyncio
    async def test_update_re_encrypts_with_fernet(self, store):
        await store.add("UPKEY", "old-val")
        await store.update("UPKEY", value="new-val")
        async with store._db.execute(
            "SELECT value FROM secrets WHERE name = 'UPKEY'"
        ) as cur:
            row = await cur.fetchone()
        assert row[0].startswith(_FERNET_PREFIX)
        result = await store.get("UPKEY")
        assert result["value"] == "new-val"

    @pytest.mark.asyncio
    async def test_agent_secrets_fernet(self, store):
        await store.add("AGENT_KEY", "agent-val", agents=["bot-1"])
        secrets = await store.get_agent_secrets("bot-1")
        assert len(secrets) == 1
        assert secrets[0]["value"] == "agent-val"
