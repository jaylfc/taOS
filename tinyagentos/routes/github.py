"""API routes for the GitHub Browser.

All routes live under /api/github/. The router reads state from
``request.app.state``:

- ``secrets``     -- SecretsStore instance (key: "github_token")
- ``http_client`` -- shared httpx.AsyncClient

Token resolution order:
1. SecretsStore key "github_token"
2. ``gh auth token`` subprocess fallback
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.knowledge_fetchers.github import (
    fetch_issue,
    fetch_notifications,
    fetch_releases,
    fetch_repo,
    fetch_starred,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Token resolution helper
# ---------------------------------------------------------------------------

async def _get_token(
    request: Request,
    user_scoped: bool = False,
) -> str | None:
    """Return a GitHub token or None.

    Resolution order:
    1. SecretsStore key ``github_token`` (PAT from device flow) — user's own
       identity takes precedence so personal repos and rate limits apply.
    2. GitHub App installation token (when github_app_id and key are configured)
       — fallback for repos accessible to the installed app.
       **Skipped when ``user_scoped=True``** because installation tokens cannot
       serve user-scoped endpoints (``/user/starred``, ``/notifications``, etc.).
    3. ``gh auth token`` subprocess fallback
    """
    # 1. Try SecretsStore PAT (user's own GitHub identity)
    secrets_store = getattr(request.app.state, "secrets", None)
    if secrets_store is not None:
        try:
            secret = await secrets_store.get("github_token")
            if secret and secret.get("value"):
                return secret["value"]
        except Exception as exc:
            logger.warning("SecretsStore lookup for github_token failed: %s", exc)

    # 2. Try GitHub App installation token (fallback; skipped for user-scoped endpoints)
    if not user_scoped:
        token = await _get_app_installation_token(request)
        if token:
            return token

    # 3. Fallback: gh CLI
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "auth", "token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        token = stdout.decode().strip()
        if token:
            return token
    except Exception as exc:
        logger.debug("gh auth token fallback failed: %s", exc)

    return None


async def _get_app_installation_token(
    request: Request, installation_id: int | None = None
) -> str | None:
    """Mint a short-lived installation token from the configured GitHub App.

    If *installation_id* is provided, uses that specific installation.
    Otherwise, uses the first active installation (backward-compatible default).

    Returns None if GitHub App is not configured or no installations exist.
    """
    cfg = getattr(request.app.state, "config", None)
    if not cfg or not cfg.github_app_id:
        return None

    secrets = getattr(request.app.state, "secrets", None)
    if secrets is None:
        return None
    try:
        rec = await secrets.get("github-app-private-key")
    except Exception:
        return None
    if not rec or not rec.get("value"):
        return None
    private_key = rec["value"]

    installs = getattr(request.app.state, "github_app_installations", None)
    if installs is None:
        return None

    if installation_id is not None:
        inst = installs.get(installation_id)
        if not inst:
            return None
        http_client = request.app.state.http_client
        from tinyagentos.github_app import get_installation_token

        token = await get_installation_token(
            cfg.github_app_id, private_key, installation_id,
            http_client,
        )
        if token:
            logger.debug("Using GitHub App installation token (installation %s)", installation_id)
            return token
        return None

    active = installs.list_all()
    if not active:
        return None

    http_client = request.app.state.http_client
    from tinyagentos.github_app import get_installation_token

    # Use the first active installation. The caller may pass an explicit
    # installation_id to select a specific installation (e.g. scoped by repo).
    for inst in active:
        iid = inst.get("installation_id")
        if not iid:
            continue
        token = await get_installation_token(
            cfg.github_app_id, private_key, iid, http_client,
        )
        if token:
            logger.debug("Using GitHub App installation token (installation %s)", iid)
            return token

    return None


def _no_token_response() -> JSONResponse:
    return JSONResponse(
        {"error": "No GitHub token configured. Add 'github_token' to secrets or run 'gh auth login'."},
        status_code=401,
    )


# ---------------------------------------------------------------------------
# Auth status
# ---------------------------------------------------------------------------

@router.get("/api/github/auth/status")
async def github_auth_status(request: Request):
    """Return whether a GitHub token is available for user-scoped API calls."""
    token = await _get_token(request, user_scoped=True)
    if token:
        return {"authenticated": True, "source": "token"}
    return {"authenticated": False, "source": None}


# ---------------------------------------------------------------------------
# Starred repos
# ---------------------------------------------------------------------------

@router.get("/api/github/starred")
async def github_starred(request: Request, page: int = 1):
    """Return paginated starred repositories for the authenticated user."""
    token = await _get_token(request, user_scoped=True)
    if not token:
        return _no_token_response()

    http_client = request.app.state.http_client
    try:
        repos, has_more = await fetch_starred(token, http_client, page=page)
        return {"repos": repos, "page": page, "has_more": has_more}
    except Exception as exc:
        logger.exception("github_starred failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@router.get("/api/github/notifications")
async def github_notifications(request: Request):
    """Return unread notifications for the authenticated user."""
    token = await _get_token(request, user_scoped=True)
    if not token:
        return _no_token_response()

    http_client = request.app.state.http_client
    try:
        raw_notifications = await fetch_notifications(token, http_client)
        # Map notification objects to GitHubIssue shape expected by the frontend.
        # GitHub notifications don't include state/comments/labels, so we use safe defaults.
        notifications = [
            {
                "number": 0,
                "title": n.get("subject_title", ""),
                "state": "open",
                "author": "",
                "body": "",
                "labels": [],
                "comments": [],
                "created_at": n.get("updated_at", ""),
                "repo": n.get("repo_full_name", ""),
                "is_pull_request": n.get("subject_type", "") == "PullRequest",
            }
            for n in raw_notifications
        ]
        unread_count = sum(1 for n in raw_notifications if n.get("unread", False))
        return {"notifications": notifications, "unread_count": unread_count}
    except Exception as exc:
        logger.exception("github_notifications failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Repo metadata
# ---------------------------------------------------------------------------

@router.get("/api/github/repo/{owner}/{repo}")
async def github_repo(request: Request, owner: str, repo: str):
    """Return metadata and README for a GitHub repository."""
    token = await _get_token(request)
    if not token:
        return _no_token_response()

    http_client = request.app.state.http_client
    try:
        data = await fetch_repo(owner, repo, token, http_client)
        return data
    except Exception as exc:
        logger.exception("github_repo failed for %s/%s: %s", owner, repo, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Issues list
# ---------------------------------------------------------------------------

@router.get("/api/github/repo/{owner}/{repo}/issues")
async def github_issues_list(
    request: Request,
    owner: str,
    repo: str,
    state: str = "open",
    page: int = 1,
):
    """Return a paginated list of issues for a repository."""
    token = await _get_token(request)
    if not token:
        return _no_token_response()

    http_client = request.app.state.http_client
    from tinyagentos.knowledge_fetchers.github import _GH_API, _auth_headers

    headers = _auth_headers(token)
    try:
        resp = await http_client.get(
            f"{_GH_API}/repos/{owner}/{repo}/issues",
            headers=headers,
            params={"state": state, "per_page": 30, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()

        issues = [
            {
                "number": i.get("number"),
                "title": i.get("title", ""),
                "state": i.get("state", ""),
                "author": i.get("user", {}).get("login", ""),
                "labels": [lbl.get("name", "") for lbl in i.get("labels", [])],
                "comments": i.get("comments", 0),
                "created_at": i.get("created_at", ""),
                "updated_at": i.get("updated_at", ""),
                "is_pull_request": "pull_request" in i,
            }
            for i in raw
        ]
        return {"issues": issues, "page": page, "has_more": len(raw) == 30}
    except Exception as exc:
        logger.exception("github_issues_list failed for %s/%s: %s", owner, repo, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Single issue / PR
# ---------------------------------------------------------------------------

@router.get("/api/github/repo/{owner}/{repo}/issues/{number}")
async def github_issue(
    request: Request,
    owner: str,
    repo: str,
    number: int,
):
    """Return a single issue or PR with its comments."""
    token = await _get_token(request)
    if not token:
        return _no_token_response()

    http_client = request.app.state.http_client
    try:
        data = await fetch_issue(owner, repo, number, token, http_client)
        return data
    except Exception as exc:
        logger.exception(
            "github_issue failed for %s/%s#%d: %s", owner, repo, number, exc
        )
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------

@router.get("/api/github/repo/{owner}/{repo}/releases")
async def github_releases(
    request: Request,
    owner: str,
    repo: str,
    limit: int = 10,
):
    """Return releases for a repository."""
    token = await _get_token(request)
    if not token:
        return _no_token_response()

    http_client = request.app.state.http_client
    try:
        releases = await fetch_releases(owner, repo, token, http_client, limit=limit)
        return {"releases": releases}
    except Exception as exc:
        logger.exception("github_releases failed for %s/%s: %s", owner, repo, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
