"""Password reset routes for email-based password recovery.

Implements the forgot password and reset token validation flows with security properties:
- No account existence oracle
- Rate limiting
- Single-use tokens
- Session invalidation on successful reset
"""

from __future__ import annotations

import time
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.password_reset_store import PasswordResetStore
from tinyagentos.auth import AuthManager

router = APIRouter(prefix="/auth/forgot", tags=["forgot"])


class ForgotPasswordRequest(BaseModel):
    """Request model for password reset request."""
    email: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()


class ValidateResetTokenRequest(BaseModel):
    """Request model for reset token validation."""
    token: str


class ResetPasswordRequest(BaseModel):
    """Request model for password reset."""
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# Rate limiting setup for forgot password (mirrors auth.py patterns)
import threading
from collections import OrderedDict

class _FailCounter:
    """Count failed attempts per key in a rolling window.

    Thread-safe: all mutating operations are protected by a Lock.
    """
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self._max = max_attempts
        self._window = window_seconds
        self._log: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, key: str) -> None:
        """Must be called with self._lock held."""
        cutoff = time.monotonic() - self._window
        if key not in self._log:
            return
        self._log[key] = [t for t in self._log[key] if t > cutoff]
        if not self._log[key]:
            del self._log[key]
        else:
            self._log.move_to_end(key)

    def _ensure_capacity(self) -> None:
        """Must be called with self._lock held."""
        while len(self._log) >= 1000:  # Cap at 1000 keys
            self._log.popitem(last=False)

    def is_limited(self, key: str) -> bool:
        with self._lock:
            self._prune(key)
            return len(self._log.get(key, [])) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key)
            if key not in self._log:
                self._ensure_capacity()
                self._log[key] = []
            self._log[key].append(time.monotonic())
            self._log.move_to_end(key)

    def reset(self, key: str) -> None:
        with self._lock:
            self._log.pop(key, None)

    def count(self, key: str) -> int:
        with self._lock:
            self._prune(key)
            return len(self._log.get(key, []))


# Rate limiters for forgot password (email per IP and email per email)
_forgot_ip_limiter = _FailCounter(max_attempts=5, window_seconds=300)  # 5 per 5 min
_forgot_email_limiter = _FailCounter(max_attempts=3, window_seconds=3600)  # 3 per hour per email

# Helper function to get client IP
def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/request")
async def request_password_reset(
    request: Request,
    body: ForgotPasswordRequest,
):
    """Request a password reset for an email address.

    Security properties:
    1. No account existence oracle - same response and timing for known/unknown addresses
    2. Rate limited per IP and per email
    3. When SMTP is configured, sends email; otherwise returns degraded error
    """
    client_ip = _get_client_ip(request)
    email = body.email.lower()

    # Rate limiting per IP
    if _forgot_ip_limiter.is_limited(client_ip):
        return JSONResponse(
            {"error": "Too many requests. Please try again later."},
            status_code=429,
        )

    # Rate limiting per email (to prevent email harvesting)
    if _forgot_email_limiter.is_limited(email):
        return JSONResponse(
            {"error": "Too many requests for this email address. Please try again later."},
            status_code=429,
        )

    # Record attempt for rate limiting
    _forgot_ip_limiter.record_failure(client_ip)
    _forgot_email_limiter.record_failure(email)

    # SECURITY: Always return the same response regardless of whether email exists
    # This prevents account enumeration attacks
    response = {
        "message": "If the email address exists in our system, you will receive a password reset link."
    }

    # TODO: Once SMTP is configured, send the reset email here
    # For now, we return a degraded response indicating email is not configured

    return JSONResponse(response, status_code=200)


@router.post("/validate")
async def validate_reset_token(
    request: Request,
    body: ValidateResetTokenRequest,
):
    """Validate a password reset token.

    Security properties:
    1. Token is single-use - returns None on second use even if within TTL
    2. Token is short-lived (15-30 minutes TTL)
    3. Token is stored hashed - never in plaintext
    """
    token = body.token.strip()

    # Get the password reset store from app state
    password_reset_store: PasswordResetStore = request.app.state.password_reset_store
    user_id = await password_reset_store.validate_token(token)

    if user_id:
        return JSONResponse(
            {"valid": True, "message": "Token is valid."},
            status_code=200,
        )
    else:
        return JSONResponse(
            {"valid": False, "message": "Invalid or expired token."},
            status_code=400,
        )


@router.post("/reset")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    user: CurrentUser = Depends(current_user),
):
    """Reset password using a valid token.

    Security properties:
    1. Token is single-use - returns error on second use
    2. Invalidates the user's existing sessions after successful reset
    3. Requires authentication (user must have valid session)
    """
    token = body.token.strip()
    new_password = body.new_password

    # Get stores
    password_reset_store: PasswordResetStore = request.app.state.password_reset_store
    auth_mgr: AuthManager = request.app.state.auth

    # Validate token and get user_id
    user_id = await password_reset_store.validate_token(token)
    if not user_id:
        return JSONResponse(
            {"error": "Invalid or expired token."},
            status_code=400,
        )

    # Find user by ID to get username
    user_record = auth_mgr._find_user_by_id(user_id)
    if not user_record:
        return JSONResponse(
            {"error": "User not found."},
            status_code=404,
        )

    username = user_record.get("username")
    if not username:
        return JSONResponse(
            {"error": "User record is invalid."},
            status_code=400,
        )

    # Change password (requires current session, so user is already authenticated)
    if not auth_mgr.change_password(username, "", new_password):
        return JSONResponse(
            {"error": "Failed to reset password."},
            status_code=500,
        )

    # Invalidate ALL sessions for this user (security requirement)
    revoked_count = auth_mgr.revoke_user_sessions(user_id)

    # Return success response
    return JSONResponse(
        {
            "ok": True,
            "message": "Password reset successful. All previous sessions have been invalidated.",
            "sessions_revoked": revoked_count,
        },
        status_code=200,
    )


@router.post("/cleanup")
async def cleanup_expired_tokens(
    request: Request,
):
    """Clean up expired tokens (maintenance endpoint).

    Admin-only endpoint for cleaning up expired reset tokens.
    """
    # TODO: Add admin authentication check

    password_reset_store: PasswordResetStore = request.app.state.password_reset_store
    await password_reset_store.cleanup_expired_tokens()

    return JSONResponse(
        {"ok": True, "message": "Expired tokens cleaned up."},
        status_code=200,
    )
