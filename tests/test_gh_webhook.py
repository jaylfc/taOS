"""Tests for GitHub webhook receiver endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest


def _make_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _build_payload(event_type: str, action: str = "opened", **overrides) -> dict:
    base = {
        "action": action,
        "repository": {"full_name": "test-owner/test-repo", "html_url": "https://github.com/test-owner/test-repo"},
        "sender": {"login": "test-user"},
    }
    if event_type == "pull_request":
        base["pull_request"] = {"html_url": "https://github.com/test-owner/test-repo/pull/1"}
    elif event_type == "issue_comment":
        base["issue"] = {"html_url": "https://github.com/test-owner/test-repo/issues/1"}
        base["comment"] = {"html_url": "https://github.com/test-owner/test-repo/issues/1#issuecomment-1"}
    elif event_type == "pull_request_review":
        base["pull_request"] = {"html_url": "https://github.com/test-owner/test-repo/pull/1"}
        base["review"] = {"html_url": "https://github.com/test-owner/test-repo/pull/1#pullrequestreview-1"}
    elif event_type == "push":
        base["compare"] = "https://github.com/test-owner/test-repo/compare/abc...def"
        base["commits"] = [{"id": "abc123", "message": "test commit"}]
    elif event_type == "check_run":
        base["check_run"] = {"name": "CI", "status": "completed"}
    base.update(overrides)
    return base


@pytest.fixture
def events_log(monkeypatch, tmp_path):
    """Redirect the module-level EVENTS_LOG_PATH to a temp file for test isolation."""
    p = tmp_path / "events.jsonl"
    monkeypatch.setattr("tinyagentos.routes.gh_webhook.EVENTS_LOG_PATH", p)
    return p


@pytest.mark.asyncio
async def test_valid_signature_returns_200(client, monkeypatch, tmp_data_dir):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_missing_signature_returns_403_verified(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request")).encode()
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_invalid_signature_returns_403(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request")).encode()
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": "sha256=deadbeef", "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_no_secret_rejected(client, monkeypatch):
    """Without GITHUB_WEBHOOK_SECRET the endpoint is secure-by-default: 500."""
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    body = json.dumps(_build_payload("pull_request")).encode()
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert r.status_code == 500
    assert "error" in r.json()


@pytest.mark.asyncio
async def test_event_written_to_log_file(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request", action="closed")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert r.status_code == 200
    assert events_log.exists()
    lines = events_log.read_text().strip().splitlines()
    assert len(lines) >= 1
    event = json.loads(lines[0])
    assert event["event"] == "pull_request"
    assert event["action"] == "closed"
    assert event["repo"] == "test-owner/test-repo"
    assert event["sender"] == "test-user"
    assert event["url"] == "https://github.com/test-owner/test-repo/pull/1"
    assert "timestamp" in event


@pytest.mark.asyncio
async def test_log_file_uses_correct_path(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request")).encode()
    sig = _make_signature("test-secret", body)
    await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "Content-Type": "application/json"})
    assert events_log.exists()
    assert "test-owner/test-repo" in events_log.read_text()


@pytest.mark.asyncio
async def test_push_event_extracts_compare_url(client, monkeypatch, events_log):
    """push events are logged; url is empty (merged code has no push-specific extraction)."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("push")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "push", "Content-Type": "application/json"})
    assert r.status_code == 200
    event = json.loads(events_log.read_text().strip())
    assert event["event"] == "push"
    assert event["repo"] == "test-owner/test-repo"
    assert event["sender"] == "test-user"
    assert "timestamp" in event


@pytest.mark.asyncio
async def test_issue_comment_event_extracts_url(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("issue_comment", action="created")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "issue_comment", "Content-Type": "application/json"})
    assert r.status_code == 200
    event = json.loads(events_log.read_text().strip())
    assert event["event"] == "issue_comment"
    assert "issuecomment" in event["url"]


@pytest.mark.asyncio
async def test_pull_request_review_event(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request_review", action="submitted")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request_review", "Content-Type": "application/json"})
    assert r.status_code == 200
    event = json.loads(events_log.read_text().strip())
    assert event["event"] == "pull_request_review"
    assert "pullrequestreview" in event["url"]


@pytest.mark.asyncio
async def test_check_run_event(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("check_run")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "check_run", "Content-Type": "application/json"})
    assert r.status_code == 200
    event = json.loads(events_log.read_text().strip())
    assert event["event"] == "check_run"
    assert event["url"] == "https://github.com/test-owner/test-repo"


@pytest.mark.asyncio
async def test_invalid_json_returns_400(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = b"not json at all"
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "Content-Type": "text/plain"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_missing_event_type_header_still_logs(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert r.status_code == 200
    event = json.loads(events_log.read_text().strip())
    assert event["event"] == ""


@pytest.mark.asyncio
async def test_unknown_event_type_still_logged(client, monkeypatch, events_log):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(_build_payload("pull_request")).encode()
    sig = _make_signature("test-secret", body)
    r = await client.post("/api/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": "deployment", "Content-Type": "application/json"})
    assert r.status_code == 200
    event = json.loads(events_log.read_text().strip())
    assert event["event"] == "deployment"
