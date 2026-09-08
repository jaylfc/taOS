"""GitHub content fetcher for the Knowledge Base pipeline.

Functions to fetch repos, issues, PRs, releases, starred repos,
and notifications from the GitHub API.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_GH_API = "https://api.github.com"
_HEADERS_BASE = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "TinyAgentOS/1.0",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _auth_headers(token: str | None) -> dict:
    """Build request headers, omitting Authorization when no token is set.

    Emitting ``Authorization: Bearer None`` (the pre-fix behaviour) makes every
    unauthenticated GitHub call fail server-side, so the header is only added
    when a real token is present.
    """
    headers = dict(_HEADERS_BASE)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _retry_delay(resp: "httpx.Response") -> float | None:
    """Return a retry delay (seconds) from Retry-After / X-RateLimit-Reset.

    ``Retry-After`` may be delta-seconds or an HTTP-date; ``X-RateLimit-Reset``
    is an epoch-second timestamp. Returns ``None`` when neither header is
    present or parseable, so the caller only retries when GitHub asks it to.
    """
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(ra)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return max(0.0, float(reset) - time.time())
        except (ValueError, TypeError):
            return None
    return None


async def _get_with_rate_limit_retry(
    http_client: "httpx.AsyncClient",
    url: str,
    *,
    headers: dict,
    **kwargs,
) -> "httpx.Response":
    """GET ``url``, honouring Retry-After / X-RateLimit-Reset once on 403/429.

    The caller still owns ``raise_for_status``, so non-rate-limit error handling
    is unchanged. Only a single retry is performed (GitHub may send a reset far
    in the future, which we do not want to block on indefinitely).
    """
    resp = await http_client.get(url, headers=headers, timeout=30, **kwargs)
    if resp.status_code in (403, 429):
        delay = _retry_delay(resp)
        if delay is not None:
            logger.warning(
                "GitHub rate-limited on %s (status %s), retrying after %.1fs",
                url,
                resp.status_code,
                delay,
            )
            await asyncio.sleep(delay)
            resp = await http_client.get(url, headers=headers, timeout=30, **kwargs)
    return resp


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_github_url(url: str) -> tuple[str, str, str, int | None]:
    """Parse a github.com URL into (owner, repo, content_type, number).

    content_type is one of: "repo", "issue", "pull", "release", "releases".
    number is None when not applicable.

    Examples
    --------
    >>> parse_github_url("https://github.com/owner/repo")
    ('owner', 'repo', 'repo', None)
    >>> parse_github_url("https://github.com/owner/repo/issues/123")
    ('owner', 'repo', 'issue', 123)
    >>> parse_github_url("https://github.com/owner/repo/pull/456")
    ('owner', 'repo', 'pull', 456)
    >>> parse_github_url("https://github.com/owner/repo/releases/tag/v1.0")
    ('owner', 'repo', 'release', None)
    >>> parse_github_url("https://github.com/owner/repo/releases")
    ('owner', 'repo', 'releases', None)
    """
    # Normalise scheme so urlsplit() can recover the hostname, then require the
    # host to be github.com. A foreign host (e.g. gitlab.com) must not be
    # mis-parsed as a GitHub owner/repo -- the old regex stripped a literal
    # "github.com/" prefix and silently treated any host as GitHub.
    if "://" not in url:
        url = "https://" + url
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in ("github.com", "www.github.com"):
        raise ValueError(f"Not a GitHub URL: {url!r}")

    # Work from the path only; urlsplit() already separates query/fragment.
    path = parsed.path.lstrip("/")
    path = path.rstrip("/")
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse GitHub URL: {url!r}")

    owner = parts[0]
    repo = parts[1]

    if len(parts) == 2:
        return owner, repo, "repo", None

    # issues/123 or issues (list)
    if len(parts) >= 3 and parts[2] == "issues":
        if len(parts) >= 4:
            try:
                return owner, repo, "issue", int(parts[3])
            except ValueError:
                pass
        return owner, repo, "issue", None

    # pull/123 or pulls
    if len(parts) >= 3 and parts[2] in ("pull", "pulls"):
        if len(parts) >= 4:
            try:
                return owner, repo, "pull", int(parts[3])
            except ValueError:
                pass
        return owner, repo, "pull", None

    # releases/tag/v1.0 or releases
    if len(parts) >= 3 and parts[2] == "releases":
        if len(parts) >= 4:
            # specific release tag
            return owner, repo, "release", None
        return owner, repo, "releases", None

    # Fallback: treat as repo
    return owner, repo, "repo", None


# ---------------------------------------------------------------------------
# Repo fetcher
# ---------------------------------------------------------------------------

async def fetch_repo(
    owner: str,
    repo: str,
    token: str | None,
    http_client: "httpx.AsyncClient",
) -> dict:
    """Fetch repo metadata and README from the GitHub API.

    Returns a dict with keys: name, owner, description, stars, forks,
    language, license, topics, readme_content, updated_at.
    """
    headers = _auth_headers(token)

    meta_resp = await _get_with_rate_limit_retry(
        http_client,
        f"{_GH_API}/repos/{owner}/{repo}",
        headers=headers,
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    readme_content = ""
    try:
        readme_headers = {**headers, "Accept": "application/vnd.github.raw"}
        readme_resp = await _get_with_rate_limit_retry(
            http_client,
            f"{_GH_API}/repos/{owner}/{repo}/readme",
            headers=readme_headers,
        )
        if readme_resp.status_code == 200:
            readme_content = readme_resp.text
    except Exception as exc:
        logger.warning("Failed to fetch README for %s/%s: %s", owner, repo, exc)

    license_name = None
    if meta.get("license") and meta["license"].get("name"):
        license_name = meta["license"]["name"]

    return {
        "name": meta.get("name", repo),
        "owner": meta.get("owner", {}).get("login", owner),
        "description": meta.get("description") or "",
        "stars": meta.get("stargazers_count", 0),
        "forks": meta.get("forks_count", 0),
        "language": meta.get("language") or "",
        "license": license_name,
        "topics": meta.get("topics", []),
        "readme_content": readme_content,
        "updated_at": meta.get("updated_at") or "",
    }


# ---------------------------------------------------------------------------
# Issue / PR fetcher
# ---------------------------------------------------------------------------

async def fetch_issue(
    owner: str,
    repo: str,
    number: int,
    token: str | None,
    http_client: "httpx.AsyncClient",
) -> dict:
    """Fetch an issue (or PR) and its comments from the GitHub API.

    Returns a dict with keys: number, title, state, author, body, labels,
    comments (list of dicts), created_at, is_pull_request.
    """
    headers = _auth_headers(token)

    issue_resp = await _get_with_rate_limit_retry(
        http_client,
        f"{_GH_API}/repos/{owner}/{repo}/issues/{number}",
        headers=headers,
    )
    issue_resp.raise_for_status()
    issue = issue_resp.json()

    comments_resp = await _get_with_rate_limit_retry(
        http_client,
        f"{_GH_API}/repos/{owner}/{repo}/issues/{number}/comments",
        headers=headers,
    )
    comments_resp.raise_for_status()
    raw_comments = comments_resp.json()

    comments = [
        {
            "id": c.get("id"),
            "author": c.get("user", {}).get("login", ""),
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
            "updated_at": c.get("updated_at", ""),
        }
        for c in raw_comments
    ]

    return {
        "number": issue.get("number", number),
        "title": issue.get("title", ""),
        "state": issue.get("state", ""),
        "author": issue.get("user", {}).get("login", ""),
        "body": issue.get("body") or "",
        "labels": [lbl.get("name", "") for lbl in issue.get("labels", [])],
        "comments": comments,
        "created_at": issue.get("created_at", ""),
        "is_pull_request": "pull_request" in issue,
    }


# ---------------------------------------------------------------------------
# Releases fetcher
# ---------------------------------------------------------------------------

async def fetch_releases(
    owner: str,
    repo: str,
    token: str | None,
    http_client: "httpx.AsyncClient",
    limit: int = 10,
) -> list[dict]:
    """Fetch releases for a repo from the GitHub API.

    Returns a list of dicts with keys: tag, name, body, author,
    published_at, assets (list), prerelease.
    """
    headers = _auth_headers(token)

    resp = await _get_with_rate_limit_retry(
        http_client,
        f"{_GH_API}/repos/{owner}/{repo}/releases",
        headers=headers,
        params={"per_page": limit},
    )
    resp.raise_for_status()
    raw = resp.json()

    releases = []
    for r in raw:
        assets = [
            {
                "name": a.get("name", ""),
                "size": a.get("size", 0),
                "download_count": a.get("download_count", 0),
            }
            for a in r.get("assets", [])
        ]
        releases.append(
            {
                "tag": r.get("tag_name", ""),
                "name": r.get("name") or "",
                "body": r.get("body") or "",
                "author": r.get("author", {}).get("login", ""),
                "published_at": r.get("published_at") or "",
                "assets": assets,
                "prerelease": bool(r.get("prerelease", False)),
            }
        )
    return releases


# ---------------------------------------------------------------------------
# Starred repos fetcher
# ---------------------------------------------------------------------------

async def fetch_starred(
    token: str | None,
    http_client: "httpx.AsyncClient",
    page: int = 1,
) -> tuple[list[dict], bool]:
    """Fetch the authenticated user's starred repos (30 per page).

    Returns (repos, has_more) where has_more is True if there are more pages.
    """
    headers = _auth_headers(token)

    resp = await _get_with_rate_limit_retry(
        http_client,
        f"{_GH_API}/user/starred",
        headers=headers,
        params={"per_page": 30, "page": page},
    )
    resp.raise_for_status()
    raw = resp.json()

    repos = [
        {
            "name": r.get("name", ""),
            "owner": r.get("owner", {}).get("login", ""),
            "full_name": r.get("full_name", ""),
            "description": r.get("description") or "",
            "stars": r.get("stargazers_count", 0),
            "forks": r.get("forks_count", 0),
            "language": r.get("language") or "",
            "updated_at": r.get("updated_at") or "",
            "url": r.get("html_url", ""),
        }
        for r in raw
    ]

    # If we got exactly 30 items there may be more pages
    has_more = len(raw) == 30

    return repos, has_more


# ---------------------------------------------------------------------------
# Notifications fetcher
# ---------------------------------------------------------------------------

async def fetch_notifications(
    token: str | None,
    http_client: "httpx.AsyncClient",
) -> list[dict]:
    """Fetch unread notifications for the authenticated user.

    Returns a list of notification dicts.
    """
    headers = _auth_headers(token)

    resp = await _get_with_rate_limit_retry(
        http_client,
        f"{_GH_API}/notifications",
        headers=headers,
    )
    resp.raise_for_status()
    raw = resp.json()

    notifications = []
    for n in raw:
        subject = n.get("subject", {})
        repo = n.get("repository", {})
        notifications.append(
            {
                "id": n.get("id", ""),
                "reason": n.get("reason", ""),
                "unread": n.get("unread", True),
                "updated_at": n.get("updated_at", ""),
                "subject_type": subject.get("type", ""),
                "subject_title": subject.get("title", ""),
                "subject_url": subject.get("url", ""),
                "repo_full_name": repo.get("full_name", ""),
                "repo_url": repo.get("html_url", ""),
            }
        )
    return notifications


# ---------------------------------------------------------------------------
# Metadata extractor
# ---------------------------------------------------------------------------

def extract_metadata(data: dict, content_type: str) -> dict:
    """Map fetched GitHub data to KnowledgeItem metadata format.

    The returned dict is suitable for storing in knowledge_items.metadata.
    """
    base = {"source": "github", "content_type": content_type}

    if content_type == "repo":
        return {
            **base,
            "repo_name": data.get("name", ""),
            "repo_owner": data.get("owner", ""),
            "description": data.get("description", ""),
            "stars": data.get("stars", 0),
            "forks": data.get("forks", 0),
            "language": data.get("language", ""),
            "license": data.get("license"),
            "topics": data.get("topics", []),
            "updated_at": data.get("updated_at", ""),
        }

    if content_type in ("issue", "pull"):
        return {
            **base,
            "number": data.get("number"),
            "title": data.get("title", ""),
            "state": data.get("state", ""),
            "author": data.get("author", ""),
            "labels": data.get("labels", []),
            "comment_count": len(data.get("comments", [])),
            "created_at": data.get("created_at", ""),
            "is_pull_request": data.get("is_pull_request", content_type == "pull"),
        }

    if content_type in ("release", "releases"):
        if isinstance(data, list):
            return {**base, "release_count": len(data)}
        return {
            **base,
            "tag": data.get("tag", ""),
            "name": data.get("name", ""),
            "author": data.get("author", ""),
            "published_at": data.get("published_at", ""),
            "prerelease": data.get("prerelease", False),
        }

    return base
