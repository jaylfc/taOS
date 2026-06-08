import pytest
import pytest_asyncio

from tinyagentos.secrets import (
    SecretsStore,
    _FERNET_PREFIX,
    _encrypt,
    _decrypt,
    _xor_decrypt,
    _xor_key,
)
import base64


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SecretsStore(tmp_path / "secrets.db")
    await s.init()
    yield s
    await s.close()


class TestEncryption:
    def test_encrypt_decrypt_roundtrip_xor(self):
        """Legacy XOR path (no key_dir) must still round-trip for migration."""
        original = "sk-abc123-secret-key"
        encrypted = _encrypt(original)
        assert encrypted != original
        assert _decrypt(encrypted) == original

    def test_encrypt_produces_base64_xor(self):
        """XOR path output is valid base64."""
        encrypted = _encrypt("hello")
        base64.b64decode(encrypted)  # should not raise

    def test_encrypt_different_values_differ(self):
        a = _encrypt("value-a")
        b = _encrypt("value-b")
        assert a != b

    def test_fernet_encrypt_decrypt_roundtrip(self, tmp_path):
        """Fernet path round-trips correctly."""
        original = "sk-fernet-test-value"
        encrypted = _encrypt(original, key_dir=tmp_path)
        assert encrypted.startswith(_FERNET_PREFIX)
        assert _decrypt(encrypted, key_dir=tmp_path) == original

    def test_fernet_ciphertext_differs_from_plaintext(self, tmp_path):
        encrypted = _encrypt("hello", key_dir=tmp_path)
        assert encrypted != "hello"

    def test_fernet_key_persists_across_calls(self, tmp_path):
        """Same key_dir produces consistent decryption across two calls."""
        enc = _encrypt("persist-test", key_dir=tmp_path)
        assert _decrypt(enc, key_dir=tmp_path) == "persist-test"

    def test_xor_to_fernet_migration_decrypt(self, tmp_path):
        """_decrypt transparently decodes an old XOR ciphertext when key_dir given."""
        # Produce a raw XOR ciphertext (no prefix, no key_dir).
        key = _xor_key()
        data = b"legacy-secret"
        xor_blob = base64.b64encode(
            bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        ).decode()
        # Should decode without error even with key_dir supplied.
        assert _decrypt(xor_blob, key_dir=tmp_path) == "legacy-secret"


@pytest.mark.asyncio
class TestSecretsStore:
    async def test_add_and_get(self, store):
        sid = await store.add("MY_KEY", "secret-value", category="api-keys", description="Test key")
        assert sid > 0
        secret = await store.get("MY_KEY")
        assert secret is not None
        assert secret["name"] == "MY_KEY"
        assert secret["value"] == "secret-value"
        assert secret["category"] == "api-keys"
        assert secret["description"] == "Test key"
        assert isinstance(secret["agents"], list)

    async def test_get_nonexistent(self, store):
        result = await store.get("DOES_NOT_EXIST")
        assert result is None

    async def test_add_with_agents(self, store):
        await store.add("AGENT_KEY", "val", agents=["agent-a", "agent-b"])
        secret = await store.get("AGENT_KEY")
        assert set(secret["agents"]) == {"agent-a", "agent-b"}

    async def test_list_all(self, store):
        await store.add("KEY_A", "a", category="api-keys")
        await store.add("KEY_B", "b", category="tokens")
        results = await store.list()
        assert len(results) == 2
        # List should NOT include 'value' field (no decrypted values)
        names = {r["name"] for r in results}
        assert names == {"KEY_A", "KEY_B"}

    async def test_list_by_category(self, store):
        await store.add("KEY_A", "a", category="api-keys")
        await store.add("KEY_B", "b", category="tokens")
        results = await store.list(category="api-keys")
        assert len(results) == 1
        assert results[0]["name"] == "KEY_A"

    async def test_update_value(self, store):
        await store.add("UPD_KEY", "old-value")
        result = await store.update("UPD_KEY", value="new-value")
        assert result is True
        secret = await store.get("UPD_KEY")
        assert secret["value"] == "new-value"

    async def test_update_category(self, store):
        await store.add("CAT_KEY", "val", category="general")
        await store.update("CAT_KEY", category="tokens")
        secret = await store.get("CAT_KEY")
        assert secret["category"] == "tokens"

    async def test_update_agents(self, store):
        await store.add("AGT_KEY", "val", agents=["old-agent"])
        await store.update("AGT_KEY", agents=["new-agent-1", "new-agent-2"])
        secret = await store.get("AGT_KEY")
        assert set(secret["agents"]) == {"new-agent-1", "new-agent-2"}

    async def test_update_nonexistent(self, store):
        result = await store.update("NOPE", value="x")
        assert result is False

    async def test_delete(self, store):
        await store.add("DEL_KEY", "val")
        deleted = await store.delete("DEL_KEY")
        assert deleted is True
        assert await store.get("DEL_KEY") is None

    async def test_delete_nonexistent(self, store):
        deleted = await store.delete("NOPE")
        assert deleted is False

    async def test_get_agent_secrets(self, store):
        await store.add("KEY_1", "v1", agents=["agent-x"])
        await store.add("KEY_2", "v2", agents=["agent-x", "agent-y"])
        await store.add("KEY_3", "v3", agents=["agent-y"])
        secrets = await store.get_agent_secrets("agent-x")
        names = {s["name"] for s in secrets}
        assert names == {"KEY_1", "KEY_2"}
        # Agent secrets should include decrypted values
        for s in secrets:
            assert "value" in s
            assert s["value"] != "***"

    async def test_get_categories(self, store):
        cats = await store.get_categories()
        names = {c["name"] for c in cats}
        assert "api-keys" in names
        assert "tokens" in names
        assert "general" in names

    async def test_delete_cascades_access(self, store):
        await store.add("CASCADE_KEY", "val", agents=["agent-z"])
        await store.delete("CASCADE_KEY")
        secrets = await store.get_agent_secrets("agent-z")
        assert len(secrets) == 0
