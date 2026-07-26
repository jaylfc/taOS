"""Routes for the in-OS Feedback feature.

POST /api/feedback  -- submit a bug report or feature request
GET  /api/feedback  -- list the current user's past submissions (no screenshot blob)
GET  /api/feedback/{id} -- fetch a single submission including its screenshot
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from tinyagentos.auth import get_current_user
from tinyagentos.feedback_store import MAX_BODY_LEN, MAX_FEEDBACK_PER_USER_PER_DAY, MAX_SCREENSHOT_LEN

router = APIRouter()

VALID_TYPES = {"bug", "feature"}


class FeedbackSubmit(BaseModel):
    type: str
    title: str
    body: str = ""
    screenshot: str = ""
    app: str = ""

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(VALID_TYPES)}")
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        if len(v) > 300:
            raise ValueError("title is too long (max 300 chars)")
        return v

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        if len(v) > MAX_BODY_LEN:
            raise ValueError(f"body exceeds the maximum length of {MAX_BODY_LEN} characters")
        return v

    @field_validator("screenshot")
    @classmethod
    def validate_screenshot(cls, v: str) -> str:
        if v and len(v) > MAX_SCREENSHOT_LEN:
            raise ValueError(
                f"screenshot is too large (max {MAX_SCREENSHOT_LEN // 1_000_000} MB)"
            )
        return v


@router.post("/api/feedback", status_code=201)
async def submit_feedback(
    payload: FeedbackSubmit,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Create a new feedback submission for the authenticated user."""
    store = request.app.state.feedback_store
    since = datetime.now(timezone.utc) - timedelta(days=1)
    # Cap enforcement lives in the store as one statement. Counting here and
    # inserting after would let concurrent requests all read the same count and
    # all insert, which a user with several tabs open trips without trying.
    item = await store.create_within_cap(
        since=since.isoformat(),
        cap=MAX_FEEDBACK_PER_USER_PER_DAY,
        user_id=current_user["id"],
        type=payload.type,
        title=payload.title,
        body=payload.body,
        screenshot=payload.screenshot,
        app=payload.app,
    )
    if item is None:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many feedback submissions. "
                f"Maximum {MAX_FEEDBACK_PER_USER_PER_DAY} per 24 hours."
            ),
        )
    # Return without the screenshot blob to keep the response light.
    return {
        "id": item["id"],
        "type": item["type"],
        "title": item["title"],
        "body": item["body"],
        "app": item["app"],
        "created_at": item["created_at"],
        "has_screenshot": bool(item["screenshot"]),
    }


@router.get("/api/feedback")
async def list_feedback(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> list[dict]:
    """List the current user's feedback submissions, most recent first.

    Screenshots are omitted; use GET /api/feedback/{id} to retrieve one.
    """
    store = request.app.state.feedback_store
    return await store.list_for_user(current_user["id"])


@router.get("/api/feedback/{item_id}")
async def get_feedback(
    item_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Return a single feedback submission including its screenshot."""
    store = request.app.state.feedback_store
    item = await store.get_by_id(item_id, current_user["id"])
    if item is None:
        raise HTTPException(status_code=404, detail="Feedback item not found")
    return item
