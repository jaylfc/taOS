"""GitHub webhook receiver endpoint.

Receives webhook events at POST /api/webhooks/github, verifies the
HMAC-SHA256 signature, and appends a structured JSONL line to
~/.taos-gh-events.jsonl for later consumption by automation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter()

EVENTS_LOG_PATH = Path.home() / ".taos-gh-events.jsonl"


def _extract_event_data(event_type: str, payload: dict) -> dict | None:
    """Extract the canonical fields from a payload for the given event type.

    Returns a dict with event, action, timestamp, repo, sender, url
    or None if the payload does not match a known shape.
    """
    try:
        repo = payload.get("repository", {})
        repo_full_name = repo.get("full_name", "")
        sender = payload.get("sender", {})
        sender_login = sender.get("login", "")

        action = payload.get("action", "")

        # Resolve the best URL depending on event type
        url = ""
        if event_type == "pull_request_review_comment":
            comment = payload.get("comment", {})
            url = comment.get("html_url", "")
        elif event_type == "pull_request_review":
            review = payload.get("review", {})
            url = review.get("html_url", "")
        elif event_type == "issue_comment":
            comment = payload.get("comment", {})
            url = comment.get("html_url", "")
        elif event_type == "pull_request":
            pr = payload.get("pull_request", {})
            url = pr.get("html_url", "")
        elif event_type in ("check_run", "check_suite"):
            repo_url = repo.get("html_url", "")
            url = repo_url if repo_url else ""
        else:
            # Unknown event type — still log with what we have
            pass

        return {
            "event": event_type,
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo": repo_full_name,
            "sender": sender_login,
            "url": url,
        }
    except Exception:
        logger.exception("Failed to extract event data from payload")
        return None


@router.post("/api/webhooks/github")
async def github_webhook(request: Request) -> Response:
    """Receive and validate a GitHub webhook event."""

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — rejecting webhook")
        return JSONResponse(
            {"error": "webhook secret not configured"}, status_code=500
        )

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header")
        return JSONResponse({"error": "missing signature"}, status_code=403)

    # Read raw body as bytes for HMAC verification
    raw_body = await request.body()

    # Compute expected signature
    expected_signature = (
        "sha256=" + hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(expected_signature, signature_header):
        logger.warning("Webhook signature mismatch")
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    # Parse the JSON payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Webhook body is not valid JSON")
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    event_type = request.headers.get("X-GitHub-Event", "")

    event_data = _extract_event_data(event_type, payload)
    if event_data is None:
        # Could not extract canonical fields; still accept the event
        event_data = {
            "event": event_type,
            "action": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo": "",
            "sender": "",
            "url": "",
        }

    # Append as a single JSONL line
    jsonl_line = json.dumps(event_data, ensure_ascii=False) + "\n"
    try:
        EVENTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(jsonl_line)
    except OSError:
        logger.exception("Failed to write webhook event to %s", EVENTS_LOG_PATH)
        return JSONResponse(
            {"error": "failed to persist event"}, status_code=500
        )

    logger.info(
        "Webhook event persisted: event=%s action=%s repo=%s",
        event_type, event_data.get("action", ""), event_data.get("repo", ""),
    )

    return JSONResponse({"status": "ok"}, status_code=200)
