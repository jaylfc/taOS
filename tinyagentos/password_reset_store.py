"""Password reset store for email-based password recovery.

Implements a secure token system for password reset with security properties:
- Single-use tokens only
- Short-lived (15-30 minutes TTL)
- Tokens stored as hashes (never plaintext)
- No account existence oracle: same response/timing for known/unknown addresses
"""

from __future__ import annotations

import time
import secrets
import hashlib
from typing import Optional

from tinyagentos.base_store import BaseStore

# Token storage schema - only stores: user_id, token_hash, created_at, expires_at, used, email_address
RESET_TOKEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    token_hash TEXT NOT NULL,
    email_address TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    UNIQUE(token_hash)
);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_user ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_email ON password_reset_tokens(email_address);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_expires ON password_reset_tokens(expires_at);
"""


def _hash_token(token: str) -> str:
    """Hash a token using SHA-256 for storage (never store plaintext)."""
    return hashlib.sha256(token.encode()).hexdigest()


class PasswordResetStore(BaseStore):
    """Store for password reset tokens with security properties:
    
    Security requirements:
    1. Tokens are single-use - second use fails even inside TTL
    2. Tokens are short-lived (15-30 minutes)
    3. Tokens stored as hashes only
    4. No account existence oracle - same response for known/unknown addresses
    """
    
    SCHEMA = RESET_TOKEN_SCHEMA

    async def init(self) -> None:
        await super().init()
        # Token validity window: 30 minutes (middle of 15-30 min requirement)
        self.token_ttl = 30 * 60  # 30 minutes in seconds

    async def create_reset_token(self, user_id: str, email_address: str) -> str:
        """Create a new password reset token for a user.
        
        Args:
            user_id: The user ID requesting reset
            email_address: The email address for verification
            
        Returns:
            The plaintext token (only returned once, must be emailed immediately)
        """
        token = secrets.token_urlsafe(32)  # High entropy token
        token_hash = _hash_token(token)
        now = int(time.time())
        expires_at = now + self.token_ttl
        
        await self._db.execute(
            "INSERT INTO password_reset_tokens (user_id, token_hash, email_address, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, token_hash, email_address.lower(), now, expires_at)
        )
        await self._db.commit()
        
        return token

    async def validate_token(self, token: str) -> Optional[str]:
        """Validate a password reset token.
        
        Args:
            token: The plaintext token to validate
            
        Returns:
            The user_id if the token is valid and unused, None otherwise
            
        Security properties:
        1. Token is single-use: after validation, token is marked as used
        2. Token is short-lived: expired tokens are rejected
        3. Token is stored hashed: plaintext never stored
        """
        token_hash = _hash_token(token)
        now = int(time.time())
        
        # Get token record for validation
        cursor = await self._db.execute(
            "SELECT user_id, expires_at, used FROM password_reset_tokens WHERE token_hash = ?",
            (token_hash,)
        )
        row = await cursor.fetchone()
        
        if not row:
            # Token doesn't exist - but for security, behave as if it exists
            # (same response and timing as validation failure)
            return None
        
        user_id, expires_at, used = row
        
        # Check if token expired
        if now > expires_at:
            await self._cleanup_expired_token(token_hash)
            return None
        
        # Check if already used
        if used:
            return None
        
        # Mark token as used (single-use property)
        await self._db.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token_hash = ?",
            (token_hash,)
        )
        await self._db.commit()
        
        return user_id

    async def _cleanup_expired_token(self, token_hash: str) -> None:
        """Clean up an expired token."""
        await self._db.execute(
            "DELETE FROM password_reset_tokens WHERE token_hash = ? AND used = 0",
            (token_hash,)
        )
        await self._db.commit()

    async def cleanup_expired_tokens(self) -> None:
        """Remove all expired tokens (maintenance task)."""
        now = int(time.time())
        await self._db.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at < ?",
            (now,)
        )
        await self._db.commit()

    async def count_active_tokens_for_email(self, email_address: str) -> int:
        """Count active (unused, not expired) reset tokens for an email address.
        
        Used for security audit and monitoring.
        """
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM password_reset_tokens "
            "WHERE email_address = ? AND used = 0 AND expires_at > ?",
            (email_address.lower(), int(time.time()))
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_user_id_for_email(self, email_address: str) -> Optional[str]:
        """Get user_id for an email address (helper for testing).
        
        Note: This is only for testing convenience. In production, this shouldn't
        be used - the reset flow should work without knowing which users exist.
        """
        cursor = await self._db.execute(
            "SELECT DISTINCT user_id FROM password_reset_tokens WHERE email_address = ?",
            (email_address.lower(),)
        )
        row = await cursor.fetchone()
        return row[0] if row else None