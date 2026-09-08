"""Tests for tinyagentos.knowledge_fetchers.github."""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tinyagentos.knowledge_fetchers.github import (
    _auth_headers,
    extract_metadata,
    fetch_issue,
    fetch_releases,
    fetch_repo,
    fetch_starred,
    parse_github_url,
)


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

class TestParseGithubUrlRepo:
    def test_plain_owner_repo(self):
        assert parse_github_url("https://github.com/owner/repo") == ("owner", "repo", "repo", None)

    def test_no_scheme(self):
        assert parse_github_url("github.com/owner/repo") == ("owner", "repo", "repo", None)

    def test_trailing_slash(self):
        assert parse_github_url("https://github.com/owner/repo/") == ("owner", "repo", "repo", None)

    def test_extra_path_treated_as_repo(self):
        # e.g. /tree/main should still resolve to repo
        owner, repo, ctype, number = parse_github_url("https://github.com/owner/repo/tree/main")
        assert owner == "owner"
        assert repo == "repo"
        assert ctype == "repo"
        assert number is None


class TestParseGithubUrlIssue:
    def test_numbered_issue(self):
        assert parse_github_url("https://github.com/owner/repo/issues/123") == (
            "owner", "repo", "issue", 123,
        )

    def test_issue_list(self):
        owner, repo, ctype, number = parse_github_url("https://github.com/owner/repo/issues")
        assert ctype == "issue"
        assert number is None

    def test_issue_with_query(self):
        owner, repo, ctype, number = parse_github_url(
            "https://github.com/owner/repo/issues/42?q=test"
        )
        assert number == 42


class TestParseGithubUrlPull:
    def test_numbered_pr(self):
        assert parse_github_url("https://github.com/owner/repo/pull/456") == (
            "owner", "repo", "pull", 456,
        )

    def test_pulls_list(self):
        _, _, ctype, number = parse_github_url("https://github.com/owner/repo/pulls")
        assert ctype == "pull"
        assert number is None

    def test_pr_with_fragment(self):
        _, _, ctype, number = parse_github_url(
            "https://github.com/owner/repo/pull/7#issuecomment-12345"
        )
        assert number == 7


class TestParseGithubUrlReleases:
    def test_releases_list(self):
        _, _, ctype, number = parse_github_url("https://github.com/owner/repo/releases")
        assert ctype == "releases"
        assert number is None

    def test_specific_release_tag(self):
        _, _, ctype, number = parse_github_url(
            "https://github.com/owner/repo/releases/tag/v1.2.3"
        )
        assert ctype == "release"
        assert number is None


class TestParseGithubUrlHostCheck:
    """R2-14: parse_github_url must only accept github.com hosts."""

    def test_rejects_non_github_host(self):
        with pytest.raises(ValueError):
            parse_github_url("https://gitlab.com/owner/repo")

    def test_rejects_non_github_host_with_path(self):
        with pytest.raises(ValueError):
            parse_github_url("https://gitlab.com/owner/repo/issues/123")

    def test_still_accepts_www_github(self):
        assert parse_github_url("https://www.github.com/owner/repo") == (
            "owner", "repo", "repo", None,
        )

    def test_still_accepts_no_scheme(self):
        assert parse_github_url("github.com/owner/repo") == (
            "owner", "repo", "repo", None,
        )


# ---------------------------------------------------------------------------
# Helpers for mocking httpx.AsyncClient
# ---------------------------------------------------------------------------

def _make_response(data, status_code: int = 200):
    """Create a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=data)
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else data
    resp.raise_for_status = MagicMock()
    return resp


def _make_async_client(*side_effects):
    """Return a mock AsyncClient whose .get() calls return side_effects in order."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(side_effects))
    return client


# ---------------------------------------------------------------------------
# fetch_repo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_repo():
    meta_data = {
        "name": "myrepo",
        "owner": {"login": "alice"},
        "description": "A test repo",
        "stargazers_count": 42,
        "forks_count": 7,
        "language": "Python",
        "license": {"name": "MIT"},
        "topics": ["ai", "ml"],
        "updated_at": "2026-01-01T00:00:00Z",
    }
    readme_text = "# MyRepo\nThis is a readme."

    meta_resp = _make_response(meta_data)
    readme_resp = MagicMock()
    readme_resp.status_code = 200
    readme_resp.text = readme_text
    readme_resp.raise_for_status = MagicMock()

    client = _make_async_client(meta_resp, readme_resp)

    result = await fetch_repo("alice", "myrepo", "token123", client)

    assert result["name"] == "myrepo"
    assert result["owner"] == "alice"
    assert result["description"] == "A test repo"
    assert result["stars"] == 42
    assert result["forks"] == 7
    assert result["language"] == "Python"
    assert result["license"] == "MIT"
    assert result["topics"] == ["ai", "ml"]
    assert result["readme_content"] == readme_text
    assert result["updated_at"] == "2026-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_fetch_repo_missing_readme():
    """README fetch returning 404 should not raise, readme_content stays empty."""
    meta_data = {
        "name": "norepo",
        "owner": {"login": "bob"},
        "description": None,
        "stargazers_count": 0,
        "forks_count": 0,
        "language": None,
        "license": None,
        "topics": [],
        "updated_at": None,
    }
    meta_resp = _make_response(meta_data)
    readme_resp = MagicMock()
    readme_resp.status_code = 404
    readme_resp.raise_for_status = MagicMock()

    client = _make_async_client(meta_resp, readme_resp)
    result = await fetch_repo("bob", "norepo", "token", client)
    assert result["readme_content"] == ""


# ---------------------------------------------------------------------------
# fetch_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_issue_with_comments():
    issue_data = {
        "number": 99,
        "title": "Bug: something broken",
        "state": "open",
        "user": {"login": "carol"},
        "body": "It does not work.",
        "labels": [{"name": "bug"}, {"name": "help wanted"}],
        "created_at": "2026-02-01T12:00:00Z",
    }
    comments_data = [
        {
            "id": 1001,
            "user": {"login": "dave"},
            "body": "I can reproduce this.",
            "created_at": "2026-02-02T08:00:00Z",
            "updated_at": "2026-02-02T08:00:00Z",
        },
        {
            "id": 1002,
            "user": {"login": "carol"},
            "body": "Fixed in #100.",
            "created_at": "2026-02-03T10:00:00Z",
            "updated_at": "2026-02-03T10:00:00Z",
        },
    ]

    client = _make_async_client(
        _make_response(issue_data),
        _make_response(comments_data),
    )

    result = await fetch_issue("owner", "repo", 99, "token", client)

    assert result["number"] == 99
    assert result["title"] == "Bug: something broken"
    assert result["state"] == "open"
    assert result["author"] == "carol"
    assert result["body"] == "It does not work."
    assert result["labels"] == ["bug", "help wanted"]
    assert len(result["comments"]) == 2
    assert result["comments"][0]["author"] == "dave"
    assert result["comments"][1]["body"] == "Fixed in #100."
    assert result["is_pull_request"] is False


@pytest.mark.asyncio
async def test_fetch_issue_is_pull_request():
    issue_data = {
        "number": 200,
        "title": "Add feature",
        "state": "open",
        "user": {"login": "eve"},
        "body": "PR body.",
        "labels": [],
        "created_at": "2026-03-01T00:00:00Z",
        "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/200"},
    }
    client = _make_async_client(
        _make_response(issue_data),
        _make_response([]),
    )

    result = await fetch_issue("owner", "repo", 200, "token", client)
    assert result["is_pull_request"] is True


# ---------------------------------------------------------------------------
# fetch_releases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_releases():
    releases_data = [
        {
            "tag_name": "v2.0.0",
            "name": "Version 2.0.0",
            "body": "Major release.",
            "author": {"login": "frank"},
            "published_at": "2026-01-15T00:00:00Z",
            "assets": [
                {"name": "app-v2.tar.gz", "size": 1024000, "download_count": 500},
            ],
            "prerelease": False,
        },
        {
            "tag_name": "v2.1.0-beta",
            "name": "Beta release",
            "body": "Beta notes.",
            "author": {"login": "frank"},
            "published_at": "2026-02-01T00:00:00Z",
            "assets": [],
            "prerelease": True,
        },
    ]

    client = _make_async_client(_make_response(releases_data))

    result = await fetch_releases("owner", "repo", "token", client, limit=10)

    assert len(result) == 2
    assert result[0]["tag"] == "v2.0.0"
    assert result[0]["name"] == "Version 2.0.0"
    assert result[0]["prerelease"] is False
    assert len(result[0]["assets"]) == 1
    assert result[0]["assets"][0]["name"] == "app-v2.tar.gz"
    assert result[0]["assets"][0]["download_count"] == 500
    assert result[1]["tag"] == "v2.1.0-beta"
    assert result[1]["prerelease"] is True


# ---------------------------------------------------------------------------
# fetch_starred
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_starred():
    """Fetch starred repos — single page with 2 items (has_more=False)."""
    starred_data = [
        {
            "name": "cool-lib",
            "owner": {"login": "greta"},
            "full_name": "greta/cool-lib",
            "description": "A cool library",
            "stargazers_count": 1000,
            "language": "Rust",
            "updated_at": "2026-03-01T00:00:00Z",
            "html_url": "https://github.com/greta/cool-lib",
        },
        {
            "name": "another-tool",
            "owner": {"login": "hank"},
            "full_name": "hank/another-tool",
            "description": None,
            "stargazers_count": 50,
            "language": None,
            "updated_at": "2026-02-01T00:00:00Z",
            "html_url": "https://github.com/hank/another-tool",
        },
    ]

    client = _make_async_client(_make_response(starred_data))

    repos, has_more = await fetch_starred("token", client, page=1)

    assert len(repos) == 2
    assert repos[0]["name"] == "cool-lib"
    assert repos[0]["owner"] == "greta"
    assert repos[0]["stars"] == 1000
    assert repos[1]["description"] == ""
    assert has_more is False  # fewer than 30 results


@pytest.mark.asyncio
async def test_fetch_starred_has_more():
    """Full page of 30 items means has_more=True."""
    starred_data = [
        {
            "name": f"repo-{i}",
            "owner": {"login": "user"},
            "full_name": f"user/repo-{i}",
            "description": "",
            "stargazers_count": i,
            "language": "Python",
            "updated_at": "2026-01-01T00:00:00Z",
            "html_url": f"https://github.com/user/repo-{i}",
        }
        for i in range(30)
    ]

    client = _make_async_client(_make_response(starred_data))

    _, has_more = await fetch_starred("token", client)
    assert has_more is True


# ---------------------------------------------------------------------------
# extract_metadata
# ---------------------------------------------------------------------------

class TestExtractMetadata:
    def test_repo_metadata(self):
        data = {
            "name": "myrepo",
            "owner": "alice",
            "description": "desc",
            "stars": 10,
            "forks": 2,
            "language": "Go",
            "license": "Apache-2.0",
            "topics": ["infra"],
            "updated_at": "2026-01-01T00:00:00Z",
        }
        result = extract_metadata(data, "repo")
        assert result["source"] == "github"
        assert result["content_type"] == "repo"
        assert result["repo_name"] == "myrepo"
        assert result["stars"] == 10
        assert result["topics"] == ["infra"]

    def test_issue_metadata(self):
        data = {
            "number": 5,
            "title": "Fix bug",
            "state": "closed",
            "author": "bob",
            "labels": ["bug"],
            "comments": [{"id": 1}, {"id": 2}],
            "created_at": "2026-01-01T00:00:00Z",
            "is_pull_request": False,
        }
        result = extract_metadata(data, "issue")
        assert result["content_type"] == "issue"
        assert result["number"] == 5
        assert result["comment_count"] == 2
        assert result["is_pull_request"] is False

    def test_pull_metadata(self):
        data = {
            "number": 99,
            "title": "New feature",
            "state": "open",
            "author": "carol",
            "labels": [],
            "comments": [],
            "created_at": "2026-02-01T00:00:00Z",
            "is_pull_request": True,
        }
        result = extract_metadata(data, "pull")
        assert result["content_type"] == "pull"
        assert result["is_pull_request"] is True

    def test_release_metadata(self):
        data = {
            "tag": "v1.0.0",
            "name": "First release",
            "author": "dave",
            "published_at": "2026-01-01T00:00:00Z",
            "prerelease": False,
        }
        result = extract_metadata(data, "release")
        assert result["content_type"] == "release"
        assert result["tag"] == "v1.0.0"
        assert result["prerelease"] is False

    def test_releases_list_metadata(self):
        data = [{"tag": "v1.0"}, {"tag": "v0.9"}]
        result = extract_metadata(data, "releases")
        assert result["release_count"] == 2


# ---------------------------------------------------------------------------
# R2-14: _auth_headers must not emit "Authorization: Bearer None"
# ---------------------------------------------------------------------------

def test_auth_headers_omits_authorization_when_no_token():
    """A missing or empty token must drop the Authorization header entirely,
    otherwise every GitHub fetch without a token fails server-side."""
    headers = _auth_headers(None)
    assert "Authorization" not in headers
    headers = _auth_headers("")
    assert "Authorization" not in headers


def test_auth_headers_includes_authorization_with_token():
    headers = _auth_headers("tok123")
    assert headers["Authorization"] == "Bearer tok123"


# ---------------------------------------------------------------------------
# R2-14: honour Retry-After / X-RateLimit-Reset once
# ---------------------------------------------------------------------------

_RELEASE_BODY = [
    {
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "body": "release notes",
        "author": {"login": "alice"},
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [],
        "prerelease": False,
    }
]


@pytest.mark.asyncio
async def test_fetch_releases_honours_retry_after():
    """A 403 carrying Retry-After is retried once; the delay is honoured and the
    second response is returned."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(403, headers={"Retry-After": "2"}, text="rate limited")
        return httpx.Response(200, json=_RELEASE_BODY)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        with patch(
            "tinyagentos.knowledge_fetchers.github.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            result = await fetch_releases("owner", "repo", "token", client, limit=10)
            mock_sleep.assert_awaited_once_with(2)
        assert attempts["n"] == 2
        assert len(result) == 1
        assert result[0]["tag"] == "v1.0.0"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_releases_honours_rate_limit_reset():
    """A 403 carrying X-RateLimit-Reset is also retried once."""
    attempts = {"n": 0}
    reset_ts = str(int(time.time()) + 3)

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                403, headers={"X-RateLimit-Reset": reset_ts}, text="rate limited"
            )
        return httpx.Response(200, json=_RELEASE_BODY)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        with patch(
            "tinyagentos.knowledge_fetchers.github.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            result = await fetch_releases("owner", "repo", "token", client, limit=10)
            mock_sleep.assert_awaited_once()
        assert attempts["n"] == 2
        assert len(result) == 1
    finally:
        await client.aclose()
