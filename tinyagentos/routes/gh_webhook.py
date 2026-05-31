"""GitHub webhook receiver — signature verification + JSONL event log."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_url(payload: dict, event_type: str) -> str:
    repo = payload.get("repository", {})
    repo_url = repo.get("html_url", "")
    if event_type == "pull_request_review":
        return payload.get("review", {}).get("html_url", repo_url)
    if event_type == "pull_request":
        return payload.get("pull_request", {}).get("html_url", repo_url)
    if event_type == "issue_comment":
        return payload.get("comment", {}).get("html_url", repo_url)
    if event_type == "push":
        return payload.get("compare", repo_url)
    return repo_url


def _verify_signature(secret: str, body: bytes, sig: str) -> bool:
    if not sig.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@router.post("/api/webhooks/github")
async def github_webhook(request: Request):
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    signature = request.headers.get("X-Hub-Signature-256", "")
    if secret:
        if not signature:
            logger.warning("gh_webhook: missing X-Hub-Signature-256 header")
            return JSONResponse({"error": "missing signature"}, status_code=403)
        body = await request.body()
        if not _verify_signature(secret, body, signature):
            logger.warning("gh_webhook: invalid signature")
            return JSONResponse({"error": "invalid signature"}, status_code=403)
    else:
        logger.warning("gh_webhook: GITHUB_WEBHOOK_SECRET not set - accepting events without verification (dev mode)")
        body = await request.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    event_type = request.headers.get("X-GitHub-Event", "")
    event = {
        "event": event_type,
        "action": payload.get("action", ""),
        "repo": payload.get("repository", {}).get("full_name", ""),
        "sender": payload.get("sender", {}).get("login", ""),
        "url": _extract_url(payload, event_type),
        "timestamp": time.time(),
    }
    data_dir: Path = request.app.state.data_dir
    log_path = data_dir / "github_events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    logger.debug("gh_webhook: logged event %s from %s", event_type, event["repo"])
    return JSONResponse({"status": "ok"})
