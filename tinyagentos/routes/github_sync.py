"""Route: sync a GitHub repo's issues onto a project board.

POST /api/projects/{project_id}/github/sync
  body: { "repo": "owner/name", "state": "all"|"open" (default "all"),
          "token": optional PAT for private repos }

The repo is remembered in the project's settings, so later calls can omit it.
Only issues are synced; pull requests are skipped (see github_sync module).
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import asyncio
import re

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.github_sync import sync_issues_to_board
from fastapi import Depends

router = APIRouter()

_GITHUB_API = "https://api.github.com"
_MAX_PAGES = 10  # cap at 1000 issues per sync
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Serialize syncs per project so two concurrent calls cannot both read the same
# pre-existing set and create duplicate cards for one issue.
_sync_locks: dict[str, asyncio.Lock] = {}


class GithubSyncIn(BaseModel):
    repo: str | None = None
    state: str = "all"
    token: str | None = None


async def _fetch_issues(repo: str, state: str, token: str | None) -> tuple[list[dict], bool]:
    """Return (issues, truncated). truncated is True if the page cap was hit."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    issues: list[dict] = []
    truncated = False
    async with httpx.AsyncClient(timeout=30) as client:
        for page in range(1, _MAX_PAGES + 1):
            resp = await client.get(
                f"{_GITHUB_API}/repos/{repo}/issues",
                params={"state": state, "per_page": 100, "page": page},
                headers=headers,
            )
            if resp.status_code != 200:
                # Do not echo GitHub's response body back to the caller; it can
                # carry token-scoped hints or unrelated detail. Surface the code.
                raise RuntimeError(f"GitHub fetch failed: HTTP {resp.status_code}")
            batch = resp.json()
            if not batch:
                break
            issues.extend(batch)
            if len(batch) < 100:
                break
            if page == _MAX_PAGES:
                truncated = True
    return issues, truncated


@router.post("/api/projects/{project_id}/github/sync")
async def github_sync(
    project_id: str,
    body: GithubSyncIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project = await pstore.get_project(project_id)
    if project is None or (not user.is_admin and user.user_id != project["user_id"]):
        return JSONResponse({"error": "not found"}, status_code=404)

    settings = project.get("settings") or {}
    repo = body.repo or settings.get("github_repo")
    if not repo:
        return JSONResponse(
            {"error": "no repo configured; pass 'repo' as owner/name"}, status_code=400
        )
    if not _REPO_RE.match(repo) or ".." in repo:
        return JSONResponse({"error": "repo must be 'owner/name'"}, status_code=400)

    try:
        issues, truncated = await _fetch_issues(repo, body.state, body.token)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    task_store = request.app.state.project_task_store
    lock = _sync_locks.setdefault(project_id, asyncio.Lock())
    async with lock:
        result = await sync_issues_to_board(task_store, project_id, issues)

    # Remember the repo so later syncs can omit it.
    if body.repo and settings.get("github_repo") != body.repo:
        settings["github_repo"] = body.repo
        await pstore.update_project(project_id, settings=settings)

    return {"repo": repo, "truncated": truncated, **result}
