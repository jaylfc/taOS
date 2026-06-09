from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    is_admin: bool


def current_user(request: Request) -> CurrentUser:
    """FastAPI dependency. 401 if no authenticated user on request.state."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    return CurrentUser(
        user_id=uid,
        is_admin=bool(getattr(request.state, "is_admin", False)),
    )


def require_owner_or_admin(user: CurrentUser, resource_user_id: str) -> None:
    """403 unless the caller owns the resource or is an admin."""
    if user.is_admin or user.user_id == resource_user_id:
        return
    raise HTTPException(status_code=403, detail="forbidden")
