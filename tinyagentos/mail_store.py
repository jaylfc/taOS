from __future__ import annotations

import time
import uuid

from tinyagentos.base_store import BaseStore

MAIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    email_address TEXT NOT NULL,
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL DEFAULT 993,
    imap_security TEXT NOT NULL DEFAULT 'ssl',
    smtp_host TEXT NOT NULL,
    smtp_port INTEGER NOT NULL DEFAULT 587,
    smtp_security TEXT NOT NULL DEFAULT 'starttls',
    username TEXT NOT NULL,
    secret_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mail_accounts_user
    ON mail_accounts(user_id);
"""


def _row_to_account(row) -> dict:
    """Map an aiosqlite row to an account dict. The password is never stored
    here; only ``secret_name`` (a pointer into SecretsStore) is kept."""
    return {
        "id": row[0],
        "user_id": row[1],
        "display_name": row[2],
        "email_address": row[3],
        "imap_host": row[4],
        "imap_port": row[5],
        "imap_security": row[6],
        "smtp_host": row[7],
        "smtp_port": row[8],
        "smtp_security": row[9],
        "username": row[10],
        "secret_name": row[11],
        "created_at": row[12],
        "updated_at": row[13],
    }


_COLUMNS = (
    "id, user_id, display_name, email_address, imap_host, imap_port, "
    "imap_security, smtp_host, smtp_port, smtp_security, username, "
    "secret_name, created_at, updated_at"
)


class MailAccountStore(BaseStore):
    """Per-user metadata for configured email accounts.

    The account password is NOT stored in this table. The caller stores the
    password in the SecretsStore and passes the resulting ``secret_name`` here,
    so this store only ever holds a pointer to the credential.
    """

    SCHEMA = MAIL_SCHEMA

    @staticmethod
    def secret_name_for(account_id: str) -> str:
        """Canonical SecretsStore key for an account's password."""
        return f"mail:account:{account_id}:password"

    async def add(
        self,
        *,
        user_id: str,
        display_name: str,
        email_address: str,
        imap_host: str,
        imap_port: int,
        imap_security: str,
        smtp_host: str,
        smtp_port: int,
        smtp_security: str,
        username: str,
        secret_name: str,
    ) -> dict:
        account_id = str(uuid.uuid4())
        now = int(time.time())
        await self._db.execute(
            f"INSERT INTO mail_accounts ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                user_id,
                display_name,
                email_address,
                imap_host,
                imap_port,
                imap_security,
                smtp_host,
                smtp_port,
                smtp_security,
                username,
                secret_name,
                now,
                now,
            ),
        )
        await self._db.commit()
        account = await self.get(account_id, user_id)
        assert account is not None  # just inserted
        return account

    async def list_for_user(self, user_id: str) -> list[dict]:
        async with self._db.execute(
            f"SELECT {_COLUMNS} FROM mail_accounts "
            "WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_account(r) for r in rows]

    async def get(self, account_id: str, user_id: str) -> dict | None:
        """Fetch a single account scoped to its owner. Returns None if the
        account does not exist or belongs to a different user."""
        async with self._db.execute(
            f"SELECT {_COLUMNS} FROM mail_accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_account(row) if row else None

    async def delete(self, account_id: str, user_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM mail_accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0
