import pytest
import pytest_asyncio

from tinyagentos.mail_store import MailAccountStore
from tinyagentos.secrets import SecretsStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = MailAccountStore(tmp_path / "mail.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def secrets(tmp_path):
    s = SecretsStore(tmp_path / "secrets.db")
    await s.init()
    yield s
    await s.close()


def _account_kwargs(**overrides):
    base = dict(
        user_id="user-1",
        display_name="Jay",
        email_address="jay@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
        username="jay@example.com",
        secret_name="mail:account:pending:password",
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
class TestMailAccountStore:
    async def test_add_and_get(self, store):
        acct = await store.add(**_account_kwargs())
        assert acct["id"]
        assert acct["email_address"] == "jay@example.com"
        assert acct["imap_port"] == 993

        fetched = await store.get(acct["id"], "user-1")
        assert fetched is not None
        assert fetched["id"] == acct["id"]
        assert fetched["username"] == "jay@example.com"

    async def test_list_scoped_by_user(self, store):
        await store.add(**_account_kwargs(user_id="user-1"))
        await store.add(**_account_kwargs(user_id="user-2", email_address="b@x.com"))

        u1 = await store.list_for_user("user-1")
        u2 = await store.list_for_user("user-2")
        assert len(u1) == 1
        assert len(u2) == 1
        assert u1[0]["email_address"] == "jay@example.com"
        assert u2[0]["email_address"] == "b@x.com"

    async def test_get_rejects_other_user(self, store):
        acct = await store.add(**_account_kwargs(user_id="user-1"))
        assert await store.get(acct["id"], "user-2") is None

    async def test_delete_scoped(self, store):
        acct = await store.add(**_account_kwargs(user_id="user-1"))
        # Wrong user cannot delete.
        assert await store.delete(acct["id"], "user-2") is False
        assert await store.get(acct["id"], "user-1") is not None
        # Owner can delete.
        assert await store.delete(acct["id"], "user-1") is True
        assert await store.get(acct["id"], "user-1") is None

    async def test_no_plaintext_password_column(self, store):
        """The accounts table must never carry a password; only a secret
        pointer. Verify the schema has no password-like column."""
        async with store._db.execute("PRAGMA table_info(mail_accounts)") as cur:
            cols = [row[1] for row in await cur.fetchall()]
        assert "secret_name" in cols
        assert "password" not in cols
        assert "value" not in cols

    async def test_secret_indirection(self, store, secrets):
        """The store keeps only a secret name; the real password lives in the
        SecretsStore and is fetched back through it."""
        acct = await store.add(**_account_kwargs())
        secret_name = MailAccountStore.secret_name_for(acct["id"])
        await secrets.add(name=secret_name, value="hunter2", category="credentials")

        # The account row holds the pointer, not the value.
        row = await store.get(acct["id"], "user-1")
        assert row["secret_name"] == "mail:account:pending:password"  # as inserted
        # Resolving the canonical secret returns the plaintext only via secrets.
        rec = await secrets.get(secret_name)
        assert rec is not None
        assert rec["value"] == "hunter2"
