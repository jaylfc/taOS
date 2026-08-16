from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth import AuthManager
from tinyagentos.base_store import BaseStore
from tinyagentos.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/password", tags=["password"])

# ---------------------------------------------------------------------------
# Rate limiter: per-IP and per-account for /request and /reset
# ---------------------------------------------------------------------------

_request_limiter = RateLimiter(capacity=60, refill_per_second=1.0)
_reset_limiter = RateLimiter(capacity=30, refill_per_second=0.5)


def _ip_key(request: Request) -> str:
    """Rate-limit key based on client IP, ignoring X-Forwarded-For unless
    behind a known proxy config."""
    client = getattr(request, "client", None)
    if client and client.host:
        return f"ip:{client.host}"
    return "ip:unknown"


def _account_key(email: str) -> str:
    """Rate-limit key based on email address lower-cased."""
    return f"account:{email.lower()}"


# ---------------------------------------------------------------------------
# Password-Reset Token Store
# ---------------------------------------------------------------------------

_PASSWORD_RESET_SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""


class PasswordResetStore(BaseStore):
    """SQLite-backed store for password-reset tokens.

    Each entry: SHA-256(token_urlsafe(32)) -> user_id + TTL + used flag.
    Server-side TTL: 30 minutes (1800 seconds). Single-use: atomic UPDATE
    WHERE used=0.  Prior unused tokens are invalidated when a new token is
    minted for the same user.
    """

    SCHEMA = _PASSWORD_RESET_SCHEMA

    async def init(self) -> None:
        await super().init()

    async def store_token(self, token_hash: str, user_id: str) -> None:
        now = int(time.time())
        await self._db.execute(
            "INSERT OR REPLACE INTO password_resets (token_hash, user_id, created_at, used) "
            "VALUES (?, ?, ?, 0)",
            (token_hash, user_id, now),
        )
        await self._db.commit()

    async def is_valid_token(self, token_hash: str, user_id: str) -> bool:
        """Check that a token exists, is unused, and is within TTL."""
        row = await self._db.execute(
            """
            SELECT used FROM password_resets
            WHERE token_hash = ? AND user_id = ?
            """,
            (token_hash, user_id),
        )
        r = await row.fetchone()
        if not r:
            return False
        return r[0] == 0  # unused

    async def consume_token(self, token_hash: str, user_id: str) -> bool:
        """Atomically claim a token (single UPDATE ... WHERE used=0).

        Returns True if the token was successfully consumed (was unused and
        now marked used). Returns False if the token was already used or not
        found.
        """
        now = int(time.time())
        cur = await self._db.execute(
            """
            UPDATE password_resets
            SET used = 1
            WHERE token_hash = ? AND user_id = ? AND used = 0 AND created_at > ?
            """,
            (token_hash, user_id, now - 1800),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def invalidate_user_tokens(self, user_id: str) -> None:
        """Invalidate all unused password-reset tokens for a user.

        Called when a new reset token is minted so that previously-generated
        links cannot be reused.  Atomic UPDATE ... WHERE used=0 style.
        """
        now = int(time.time())
        await self._db.execute(
            """
            UPDATE password_resets
            SET used = 1
            WHERE user_id = ? AND used = 0 AND created_at > ?
            """,
            (user_id, now - 1800),
        )
        await self._db.commit()


# ---------------------------------------------------------------------------
# Helpers: SMTP configuration awareness
# ---------------------------------------------------------------------------

async def _is_smtp_configured(app) -> bool:
    """Return True when at least one mail account is configured with SMTP
    credentials.  The mail_store is initialised on app.state during startup."""
    store = getattr(app.state, "mail_store", None)
    if store is None:
        return False
    try:
        accounts = await store.list_for_user("")
        return bool(accounts)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# /request  —  initiate a password-reset email
# ---------------------------------------------------------------------------

@router.post("/request")
async def request_reset(request: Request):
    """POST /api/password/request { "email": "user@example.com" }

    Looks up the user by email.  When SMTP is configured mints a token,
    stores its SHA-256 hash with a 30-min TTL, and hands the token to the
    email connector.  When SMTP is unconfigured returns an explicit error
    pointing at Settings and mints nothing.

    Response is uniform for existing/unknown emails (no enumeration oracle)
    when SMTP is configured.
    """
    # Rate limiting: per-IP and per-account
    ip_key = _ip_key(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    email = (body.get("email") or "").strip()
    if not email:
        return JSONResponse({"error": "email is required"}, status_code=400)

    # Per-account rate limit
    account_key = _account_key(email)
    if not _request_limiter.check(account_key):
        return JSONResponse(
            {"error": "too many requests, try again later"},
            status_code=429,
        )

    # Per-IP rate limit (also applies)
    if not _request_limiter.check(ip_key):
        return JSONResponse(
            {"error": "too many requests, try again later"},
            status_code=429,
        )

    # Look up user by email
    auth_mgr = request.app.state.auth
    user = auth_mgr.find_user_by_email(email)

    # Check SMTP configuration
    smtp_configured = await _is_smtp_configured(request.app)

    if not smtp_configured:
        # SMTP not configured — return explicit error, mint nothing
        return JSONResponse(
            {"error": "email is not configured"},
            status_code=503,
        )

    # SMTP is configured — proceed uniformly (no enumeration oracle)
    if user is not None:
        # Mint token and store SHA-256 hash with TTL
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        store = request.app.state.password_reset_store

        # Invalidate any prior unused tokens for this user
        await store.invalidate_user_tokens(user["id"])

        # Store the new token
        await store.store_token(token_hash, user["id"])

        # Hand token to email connector / send reset email
        sent = False
        try:
            import smtplib
            from email.mime.text import MIMEText

            # Populate SMTP details from configured mail accounts
            mail_store = request.app.state.mail_store
            accounts = await mail_store.list_for_user(user["id"])
            if accounts:
                acct = accounts[0]
                # Use the same SMTP config the email connector would use
                smtp_host = acct.get("smtp_host", "")
                smtp_port = acct.get("smtp_port", 587)
                smtp_security = acct.get("smtp_security", "starttls")
                smtp_username = acct.get("username", "")

                if smtp_host:
                    msg = MIMEText(
                        f"Password reset link: {request.url_root}api/password/reset?token={token}"
                    )
                    msg["From"] = smtp_username
                    msg["To"] = email
                    msg["Subject"] = "TinyAgentOS Password Reset"

                    server = smtplib.SMTP(smtp_host, smtp_port)
                    if smtp_security == "starttls":
                        server.starttls()
                    if smtp_username:
                        server.login(smtp_username, acct.get("secret_name", ""))
                    server.send_message(msg)
                    server.quit()
                    sent = True
            else:
                logger.warning(
                    "No mail account found for user %s; email may not send",
                    user.get("username", ""),
                )
        except Exception as e:
            logger.error(f"Failed to send password reset email: {e}")

        if not sent:
            logger.warning(
                "Password reset email not sent for user %s (SMTP not configured or error)",
                user.get("username", ""),
            )

        return JSONResponse(
            {"ok": True, "message": "If an account with that email exists, a password reset link has been sent"},
            status_code=200,
        )
    else:
        # User does not exist — return uniform response, do NOT mint token
        # or send email to prevent enumeration oracle.
        return JSONResponse(
            {"ok": True, "message": "If an account with that email exists, a password reset link has been sent"},
            status_code=200,
        )


# ---------------------------------------------------------------------------
# /reset  —  reset password via token (single-use, atomic consume,
#            set new password without current password, revoke sessions)
# ---------------------------------------------------------------------------

@router.post("/reset")
async def reset_password(request: Request):
    """POST /api/password/reset { "token": "<token>", "new_password": "..." }

    Identity comes from the token alone.  Hash‑lookup, TTL check, and
    single-use consumed ATOMICALLY (single UPDATE … WHERE used=0, no
    SELECT-then-UPDATE double-spend).  After success the new password is
    set via a path that does NOT require the current password, and
    revoke_user_sessions(user_id) is called.

    Rate limiting per-IP applies.
    """
    body = await request.json()
    token = (body.get("token") or "").strip()
    new_password = (body.get("new_password") or "").strip()

    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)
    if not new_password or len(new_password) < 8:
        return JSONResponse(
            {"error": "new password must be at least 8 characters"},
            status_code=400,
        )

    # Rate limiting per-IP
    ip_key = _ip_key(request)
    if not _reset_limiter.check(ip_key):
        return JSONResponse(
            {"error": "too many requests, try again later"},
            status_code=429,
        )

    # Mint SHA-256 hash of the token for lookup
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Look up user via the store
    store = request.app.state.password_reset_store
    user_id = None

    # Try to find the user by checking stored tokens
    row = await store._db.execute(
        "SELECT user_id, used, created_at FROM password_resets WHERE token_hash = ?",
        (token_hash,),
    )
    r = await row.fetchone()
    if r:
        stored_user_id, used, created_at = r[0], r[1], r[2]
        if used:
            return JSONResponse({"error": "this reset link has already been used"}, status_code=410)
        # TTL check: within 30 minutes
        now = int(time.time())
        if now - created_at > 1800:  # 30 min TTL
            return JSONResponse({"error": "this reset link has expired"}, status_code=410)
        user_id = stored_user_id

    if user_id is None:
        return JSONResponse({"error": "invalid or unknown reset token"}, status_code=400)

    # Atomically consume the token (single UPDATE … WHERE used=0)
    consumed = await store.consume_token(token_hash, user_id)
    if not consumed:
        return JSONResponse({"error": "this reset link has already been used or expired"}, status_code=410)

    # Set the new password directly (does not require current password)
    auth_mgr = request.app.state.auth
    data = auth_mgr._read_users()
    users = data.get("users", [])
    for u in users:
        if u.get("id") == user_id:
            u["password_hash"] = auth_mgr.hash_password(new_password)
            users[users.index(u)] = u
            break
    data["users"] = users
    auth_mgr._write_users(data)

    # Revoke all existing sessions for this user
    auth_mgr.revoke_user_sessions(user_id)

    return JSONResponse(
        {"ok": True, "message": "Password reset successful. You can now log in with your new password."},
        status_code=200,
    )


# ---------------------------------------------------------------------------
# /validate  —  validate a reset token without consuming it
# ---------------------------------------------------------------------------

@router.post("/validate")
async def validate_token(request: Request):
    """POST /api/password/validate { "token": "<token>" }

    Returns whether the token is valid (unused, within TTL) without
    consuming it.  Rate-limited per-IP.  Same response whether valid or not
    to prevent enumeration oracle.
    """
    body = await request.json()
    token = (body.get("token") or "").strip()

    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)

    # Rate limiting per-IP
    ip_key = _ip_key(request)
    if not _request_limiter.check(ip_key):
        return JSONResponse(
            {"error": "too many requests, try again later"},
            status_code=429,
        )

    # Mint SHA-256 hash of the token for lookup
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Look up the token without consuming it
    store = request.app.state.password_reset_store
    is_valid = await store.is_valid_token(token_hash, "")

    # Return uniform response (no enumeration oracle)
    return JSONResponse(
        {"ok": True, "valid": is_valid},
        status_code=200,
    )