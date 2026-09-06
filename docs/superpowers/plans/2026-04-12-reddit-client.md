# Reddit Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Reddit Client app — a native Reddit browser inside TinyAgentOS with thread/comment viewing, Knowledge Base integration, and Reddit OAuth.

**Architecture:** `RedditClientApp.tsx` with internal view state (`feed` | `thread` | `diff`), `lib/reddit.ts` API helpers, `tinyagentos/knowledge_fetchers/reddit.py` backend fetcher, `tinyagentos/routes/reddit.py` proxy routes, registration in `app-registry.ts`. Follows LibraryApp patterns exactly: `useCallback`+`useEffect` data fetching, `isMobile` runtime check, barrel imports from `@/components/ui`.

**Tech Stack:** React, TypeScript, Tailwind CSS, Vitest, Python, pytest-asyncio, respx, lucide-react, shadcn-style UI

**Spec:** `docs/superpowers/specs/2026-04-12-reddit-client-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/knowledge_fetchers/__init__.py` | Package marker |
| Create | `tinyagentos/knowledge_fetchers/reddit.py` | RedditFetcher: fetch_thread, fetch_subreddit, fetch_saved, flatten_to_text, extract_metadata |
| Create | `tests/fixtures/reddit_thread.json` | Realistic Reddit .json response fixture |
| Create | `tests/test_knowledge_fetcher_reddit.py` | pytest-asyncio tests for fetcher |
| Create | `tinyagentos/routes/reddit.py` | /api/reddit/* proxy + OAuth routes |
| Create | `tests/test_routes_reddit.py` | Route-level tests |
| Modify | `tinyagentos/knowledge_ingest.py` | Wire reddit fetcher into _download() |
| Create | `desktop/src/lib/reddit.ts` | TypeScript fetch wrappers for /api/reddit/* |
| Create | `desktop/tests/reddit.test.ts` | Vitest tests for reddit.ts (mocked fetch) |
| Create | `desktop/src/apps/RedditClientApp.tsx` | Main app component |
| Modify | `desktop/src/registry/app-registry.ts` | Register reddit app entry |

---

## Task 1 — RedditFetcher Backend + Tests

**Files:**
- Create: `tinyagentos/knowledge_fetchers/__init__.py`
- Create: `tinyagentos/knowledge_fetchers/reddit.py`
- Create: `tests/fixtures/reddit_thread.json`
- Create: `tests/test_knowledge_fetcher_reddit.py`

### Step 1.1 — Write failing tests

Create `tests/fixtures/reddit_thread.json`:

```json
[
  {
    "kind": "Listing",
    "data": {
      "children": [
        {
          "kind": "t3",
          "data": {
            "id": "abc123",
            "subreddit": "LocalLLaMA",
            "title": "Running LLMs on Orange Pi 5 — full benchmark",
            "author": "techuser42",
            "selftext": "I ran several models on my Orange Pi 5 Plus with the RK3588 NPU.",
            "score": 842,
            "upvote_ratio": 0.97,
            "num_comments": 67,
            "created_utc": 1712880000.0,
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/running_llms_on_orange_pi_5/",
            "permalink": "/r/LocalLLaMA/comments/abc123/running_llms_on_orange_pi_5/",
            "link_flair_text": "Discussion",
            "is_self": true
          }
        }
      ]
    }
  },
  {
    "kind": "Listing",
    "data": {
      "children": [
        {
          "kind": "t1",
          "data": {
            "id": "cmt001",
            "author": "commentor1",
            "body": "Great writeup! Which NPU driver version?",
            "score": 120,
            "created_utc": 1712882000.0,
            "depth": 0,
            "parent_id": "t3_abc123",
            "edited": false,
            "distinguished": null,
            "replies": {
              "kind": "Listing",
              "data": {
                "children": [
                  {
                    "kind": "t1",
                    "data": {
                      "id": "cmt002",
                      "author": "techuser42",
                      "body": "Using rkllm v1.1.4 from the vendor repo.",
                      "score": 45,
                      "created_utc": 1712883000.0,
                      "depth": 1,
                      "parent_id": "t1_cmt001",
                      "edited": false,
                      "distinguished": null,
                      "replies": {
                        "kind": "Listing",
                        "data": { "children": [] }
                      }
                    }
                  }
                ]
              }
            }
          }
        },
        {
          "kind": "t1",
          "data": {
            "id": "cmt003",
            "author": "[deleted]",
            "body": "[deleted]",
            "score": -3,
            "created_utc": 1712881000.0,
            "depth": 0,
            "parent_id": "t3_abc123",
            "edited": false,
            "distinguished": null,
            "replies": {
              "kind": "Listing",
              "data": { "children": [] }
            }
          }
        }
      ]
    }
  }
]
```

Create `tests/test_knowledge_fetcher_reddit.py`:

```python
"""Unit tests for tinyagentos.knowledge_fetchers.reddit."""
from __future__ import annotations

import json
import pathlib
import pytest
import httpx
import respx

from tinyagentos.knowledge_fetchers.reddit import (
    fetch_thread,
    fetch_subreddit,
    flatten_to_text,
    extract_metadata,
    _normalise_url,
)

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "reddit_thread.json"
FIXTURE_DATA = json.loads(FIXTURE_PATH.read_text())

THREAD_URL = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/running_llms_on_orange_pi_5/"
THREAD_JSON_URL = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/running_llms_on_orange_pi_5/.json?limit=500"


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def test_url_normalisation_strips_query_params():
    url = "https://www.reddit.com/r/test/comments/abc/?utm_source=share&ref=foo"
    result = _normalise_url(url)
    assert "utm_source" not in result
    assert result.endswith(".json?limit=500")


def test_url_normalisation_no_duplicate_json():
    url = "https://www.reddit.com/r/test/comments/abc/.json"
    result = _normalise_url(url)
    assert result.count(".json") == 1


def test_url_normalisation_trailing_slash():
    url = "https://www.reddit.com/r/test/comments/abc/"
    result = _normalise_url(url)
    assert ".json?limit=500" in result


def test_url_normalisation_adds_json():
    url = "https://www.reddit.com/r/test/comments/abc"
    result = _normalise_url(url)
    assert result.endswith(".json?limit=500")


# ---------------------------------------------------------------------------
# fetch_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_thread_parses_post():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        post, comments = await fetch_thread(THREAD_URL, client)
    assert post.id == "abc123"
    assert post.subreddit == "LocalLLaMA"
    assert post.title == "Running LLMs on Orange Pi 5 — full benchmark"
    assert post.author == "techuser42"
    assert post.score == 842
    assert post.upvote_ratio == pytest.approx(0.97)
    assert post.num_comments == 67
    assert post.flair == "Discussion"
    assert post.is_self is True


@pytest.mark.asyncio
@respx.mock
async def test_fetch_thread_parses_top_level_comments():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        _, comments = await fetch_thread(THREAD_URL, client)
    # Fixture has 2 top-level comments
    top_level = [c for c in comments if c.depth == 0]
    assert len(top_level) == 2
    assert comments[0].author == "commentor1"
    assert comments[0].body == "Great writeup! Which NPU driver version?"
    assert comments[0].score == 120


@pytest.mark.asyncio
@respx.mock
async def test_fetch_thread_nested_comment_depth():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        _, comments = await fetch_thread(THREAD_URL, client)
    nested = [c for c in comments if c.depth == 1]
    assert len(nested) == 1
    assert nested[0].id == "cmt002"
    assert nested[0].parent_id == "t1_cmt001"
    assert nested[0].body == "Using rkllm v1.1.4 from the vendor repo."


@pytest.mark.asyncio
@respx.mock
async def test_deleted_comment_preserved():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        _, comments = await fetch_thread(THREAD_URL, client)
    deleted = [c for c in comments if c.author == "[deleted]"]
    assert len(deleted) == 1
    assert deleted[0].body == "[deleted]"
    assert deleted[0].depth == 0  # structure preserved


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_thread(THREAD_URL, client)


# ---------------------------------------------------------------------------
# flatten_to_text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_flatten_to_text_includes_title():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        post, comments = await fetch_thread(THREAD_URL, client)
    text = flatten_to_text(post, comments)
    assert post.title in text


@pytest.mark.asyncio
@respx.mock
async def test_flatten_to_text_includes_selftext():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        post, comments = await fetch_thread(THREAD_URL, client)
    text = flatten_to_text(post, comments)
    assert "RK3588 NPU" in text


@pytest.mark.asyncio
@respx.mock
async def test_flatten_to_text_includes_comments():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        post, comments = await fetch_thread(THREAD_URL, client)
    text = flatten_to_text(post, comments)
    assert "Which NPU driver version" in text


@pytest.mark.asyncio
@respx.mock
async def test_flatten_to_text_indents_nested():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        post, comments = await fetch_thread(THREAD_URL, client)
    text = flatten_to_text(post, comments)
    # depth-1 comment should be indented with 2 spaces
    assert "  rkllm v1.1.4" in text


# ---------------------------------------------------------------------------
# extract_metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_extract_metadata_fields():
    respx.get(THREAD_JSON_URL).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))
    async with httpx.AsyncClient() as client:
        post, _ = await fetch_thread(THREAD_URL, client)
    meta = extract_metadata(post)
    assert meta["subreddit"] == "LocalLLaMA"
    assert meta["score"] == 842
    assert meta["upvote_ratio"] == pytest.approx(0.97)
    assert meta["num_comments"] == 67
    assert "created_utc" in meta
    assert meta["flair"] == "Discussion"
```

- [ ] **Step 1.2 — Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos && python -m pytest tests/test_knowledge_fetcher_reddit.py -v 2>&1 | head -30
```

Expected: ImportError — module `tinyagentos.knowledge_fetchers.reddit` does not exist.

### Step 1.3 — Implement the fetcher

Create `tinyagentos/knowledge_fetchers/__init__.py`:

```python
"""Platform-specific content fetchers for the IngestPipeline."""
```

Create `tinyagentos/knowledge_fetchers/reddit.py`:

```python
"""Reddit content fetcher for the TinyAgentOS Knowledge Pipeline.

Fetches Reddit threads and subreddit listings via the public .json API
(no auth required for public content) or via OAuth for authenticated
endpoints.

No new dependencies — uses the shared httpx.AsyncClient from app state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

if TYPE_CHECKING:
    import httpx

_USER_AGENT = "TinyAgentOS/1.0"
_JSON_LIMIT = 500


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RedditPost:
    id: str
    subreddit: str
    title: str
    author: str
    selftext: str
    score: int
    upvote_ratio: float
    num_comments: int
    created_utc: float
    url: str
    permalink: str
    flair: str | None
    is_self: bool


@dataclass
class RedditComment:
    id: str
    author: str
    body: str
    score: int
    created_utc: float
    depth: int
    parent_id: str
    edited: bool | float
    distinguished: str | None
    replies: list["RedditComment"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def _normalise_url(url: str, token: str | None = None) -> str:
    """Return the .json URL for a Reddit thread URL.

    Strips query params, ensures the path ends with /.json, appends
    ?limit=500. If a token is provided, switches to the OAuth base domain.
    """
    parsed = urlparse(url)

    # Strip .json suffix if already present so we can add it cleanly
    path = parsed.path.rstrip("/")
    if path.endswith(".json"):
        path = path[: -len(".json")]

    if token:
        netloc = "oauth.reddit.com"
        scheme = "https"
    else:
        netloc = "www.reddit.com"
        scheme = "https"

    new_url = urlunparse((scheme, netloc, path + "/.json", "", f"limit={_JSON_LIMIT}", ""))
    return new_url


# ---------------------------------------------------------------------------
# Comment tree parser
# ---------------------------------------------------------------------------

def _parse_comments(children: list[dict], depth: int = 0) -> list[RedditComment]:
    """Recursively flatten a Reddit comment listing into a flat list preserving depth."""
    result: list[RedditComment] = []
    for child in children:
        kind = child.get("kind", "")
        if kind == "more":
            # Stub: record a placeholder so callers know more exist
            result.append(
                RedditComment(
                    id=child.get("data", {}).get("id", "more"),
                    author="",
                    body="[more]",
                    score=0,
                    created_utc=0.0,
                    depth=depth,
                    parent_id=child.get("data", {}).get("parent_id", ""),
                    edited=False,
                    distinguished=None,
                )
            )
            continue
        if kind != "t1":
            continue
        data = child.get("data", {})
        comment = RedditComment(
            id=data.get("id", ""),
            author=data.get("author", "[deleted]"),
            body=data.get("body", "[deleted]"),
            score=data.get("score", 0),
            created_utc=float(data.get("created_utc", 0)),
            depth=data.get("depth", depth),
            parent_id=data.get("parent_id", ""),
            edited=data.get("edited", False),
            distinguished=data.get("distinguished"),
        )
        # Recurse into replies
        replies_obj = data.get("replies")
        if isinstance(replies_obj, dict):
            reply_children = (
                replies_obj.get("data", {}).get("children", [])
            )
            comment.replies = _parse_comments(reply_children, depth + 1)
        result.append(comment)
        result.extend(comment.replies)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_thread(
    url: str,
    http_client: "httpx.AsyncClient",
    token: str | None = None,
) -> tuple[RedditPost, list[RedditComment]]:
    """Fetch a Reddit thread and return (post, flat comment list).

    Raises httpx.HTTPStatusError on non-2xx responses so callers can
    handle rate limits (429) or auth errors (401) explicitly.
    """
    json_url = _normalise_url(url, token)
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = await http_client.get(json_url, headers=headers, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    data = resp.json()
    # data[0] = post listing, data[1] = comment listing
    post_data = data[0]["data"]["children"][0]["data"]
    comment_children = data[1]["data"]["children"]

    post = RedditPost(
        id=post_data["id"],
        subreddit=post_data["subreddit"],
        title=post_data["title"],
        author=post_data.get("author", "[deleted]"),
        selftext=post_data.get("selftext", ""),
        score=post_data.get("score", 0),
        upvote_ratio=float(post_data.get("upvote_ratio", 0)),
        num_comments=post_data.get("num_comments", 0),
        created_utc=float(post_data.get("created_utc", 0)),
        url=post_data.get("url", ""),
        permalink=post_data.get("permalink", ""),
        flair=post_data.get("link_flair_text"),
        is_self=bool(post_data.get("is_self", False)),
    )

    comments = _parse_comments(comment_children)
    return post, comments


async def fetch_subreddit(
    subreddit: str,
    sort: str = "hot",
    after: str | None = None,
    http_client: "httpx.AsyncClient" = None,
    token: str | None = None,
) -> tuple[list[RedditPost], str | None]:
    """Fetch a subreddit listing. Returns (posts, next_after_cursor)."""
    base = "https://oauth.reddit.com" if token else "https://www.reddit.com"
    params = f"limit=25"
    if after:
        params += f"&after={after}"
    url = f"{base}/r/{subreddit}/{sort}.json?{params}"
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = await http_client.get(url, headers=headers, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    data = resp.json()
    children = data["data"]["children"]
    next_after = data["data"].get("after")

    posts = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        d = child["data"]
        posts.append(RedditPost(
            id=d["id"],
            subreddit=d["subreddit"],
            title=d["title"],
            author=d.get("author", "[deleted]"),
            selftext=d.get("selftext", ""),
            score=d.get("score", 0),
            upvote_ratio=float(d.get("upvote_ratio", 0)),
            num_comments=d.get("num_comments", 0),
            created_utc=float(d.get("created_utc", 0)),
            url=d.get("url", ""),
            permalink=d.get("permalink", ""),
            flair=d.get("link_flair_text"),
            is_self=bool(d.get("is_self", False)),
        ))
    return posts, next_after


async def fetch_saved(
    token: str,
    http_client: "httpx.AsyncClient",
    after: str | None = None,
) -> tuple[list[RedditPost], str | None]:
    """Fetch the authenticated user's saved posts. Requires OAuth token."""
    params = "limit=25&type=links"
    if after:
        params += f"&after={after}"
    url = f"https://oauth.reddit.com/user/me/saved?{params}"
    headers = {
        "User-Agent": _USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    resp = await http_client.get(url, headers=headers, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    data = resp.json()
    children = data["data"]["children"]
    next_after = data["data"].get("after")

    posts = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        d = child["data"]
        posts.append(RedditPost(
            id=d["id"],
            subreddit=d["subreddit"],
            title=d["title"],
            author=d.get("author", "[deleted]"),
            selftext=d.get("selftext", ""),
            score=d.get("score", 0),
            upvote_ratio=float(d.get("upvote_ratio", 0)),
            num_comments=d.get("num_comments", 0),
            created_utc=float(d.get("created_utc", 0)),
            url=d.get("url", ""),
            permalink=d.get("permalink", ""),
            flair=d.get("link_flair_text"),
            is_self=bool(d.get("is_self", False)),
        ))
    return posts, next_after


def flatten_to_text(post: RedditPost, comments: list[RedditComment]) -> str:
    """Return a markdown-formatted string of the full thread for the content field."""
    lines = [f"# {post.title}", ""]
    if post.selftext and post.selftext not in ("[deleted]", "[removed]", ""):
        lines.append(post.selftext)
        lines.append("")
    lines.append("---")
    lines.append("")
    for comment in comments:
        if comment.body == "[more]":
            continue
        indent = "  " * comment.depth
        prefix = f"{indent}u/{comment.author}  ↑{comment.score}"
        lines.append(prefix)
        for text_line in comment.body.splitlines():
            lines.append(f"{indent}{text_line}")
        lines.append("")
    return "\n".join(lines)


def extract_metadata(post: RedditPost) -> dict:
    """Return the metadata dict for a KnowledgeItem from a RedditPost."""
    return {
        "subreddit": post.subreddit,
        "score": post.score,
        "upvote_ratio": post.upvote_ratio,
        "num_comments": post.num_comments,
        "created_utc": post.created_utc,
        "flair": post.flair,
        "is_self": post.is_self,
        "reddit_id": post.id,
        "reddit_permalink": post.permalink,
    }
```

- [ ] **Step 1.4 — Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos && python -m pytest tests/test_knowledge_fetcher_reddit.py -v
```

Expected: All tests green.

### Step 1.5 — Wire fetcher into IngestPipeline

In `tinyagentos/knowledge_ingest.py`, find the `_download` method and replace the Reddit placeholder:

**Old code** (in `_download`):
```python
        if source_type == "article":
            return await self._download_article(url, title, metadata)
        # Placeholder for platform-specific downloaders (reddit, youtube, x, github)
        # added in build steps 3-6. Return empty so the item is stored with
        # status=ready and content="" until a platform adapter fills it.
        return "", title, "", metadata
```

**New code**:
```python
        if source_type == "article":
            return await self._download_article(url, title, metadata)
        if source_type == "reddit":
            return await self._download_reddit(url, title, metadata)
        # Placeholder for youtube, x, github — added in later build steps.
        return "", title, "", metadata
```

Then add the new method after `_download_article`:

```python
    async def _download_reddit(
        self, url: str, title: str, metadata: dict
    ) -> tuple[str, str, str, dict]:
        """Fetch a Reddit thread via the .json API and return pipeline tuple."""
        from tinyagentos.knowledge_fetchers.reddit import (
            fetch_thread,
            flatten_to_text,
            extract_metadata,
        )
        post, comments = await fetch_thread(url, self._http_client)
        content = flatten_to_text(post, comments)
        reddit_meta = extract_metadata(post)
        metadata = {**metadata, **reddit_meta}
        return content, post.title, post.author, metadata
```

---

## Task 2 — Reddit API Routes + Tests

**Files:**
- Create: `tinyagentos/routes/reddit.py`
- Create: `tests/test_routes_reddit.py`

### Step 2.1 — Write failing tests

Create `tests/test_routes_reddit.py`:

```python
"""Tests for /api/reddit/* routes."""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinyagentos.routes.reddit import router

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "reddit_thread.json"
FIXTURE_DATA = json.loads(FIXTURE_PATH.read_text())

SUBREDDIT_LISTING = {
    "data": {
        "after": "t3_next",
        "children": [
            {
                "kind": "t3",
                "data": {
                    "id": "post1",
                    "subreddit": "LocalLLaMA",
                    "title": "Test Post",
                    "author": "user1",
                    "selftext": "",
                    "score": 100,
                    "upvote_ratio": 0.95,
                    "num_comments": 10,
                    "created_utc": 1712880000.0,
                    "url": "https://reddit.com/r/LocalLLaMA/comments/post1/test/",
                    "permalink": "/r/LocalLLaMA/comments/post1/test/",
                    "link_flair_text": None,
                    "is_self": False,
                },
            }
        ],
    }
}


class MockSecretsStore:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        pass


class MockHttpClient:
    """Thin wrapper so the route can use app.state.http_client."""
    def __init__(self):
        self._client = httpx.AsyncClient()

    async def get(self, *args, **kwargs):
        return await self._client.get(*args, **kwargs)


def make_app():
    app = FastAPI()
    app.include_router(router)

    class State:
        pass

    app.state.secrets_store = MockSecretsStore()
    return app


@pytest.fixture
def client():
    app = make_app()
    return TestClient(app)


@respx.mock
def test_subreddit_returns_posts(client):
    respx.get(
        "https://www.reddit.com/r/LocalLLaMA/hot.json?limit=25"
    ).mock(return_value=httpx.Response(200, json=SUBREDDIT_LISTING))

    resp = client.get("/api/reddit/subreddit?name=LocalLLaMA&sort=hot")
    assert resp.status_code == 200
    body = resp.json()
    assert "posts" in body
    assert len(body["posts"]) == 1
    assert body["posts"][0]["title"] == "Test Post"
    assert body["after"] == "t3_next"


@respx.mock
def test_thread_returns_post_and_comments(client):
    json_url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/running_llms_on_orange_pi_5/.json?limit=500"
    respx.get(json_url).mock(return_value=httpx.Response(200, json=FIXTURE_DATA))

    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/running_llms_on_orange_pi_5/"
    resp = client.get(f"/api/reddit/thread?url={url}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["post"]["id"] == "abc123"
    assert isinstance(body["comments"], list)
    assert len(body["comments"]) > 0


def test_thread_missing_url_returns_422(client):
    resp = client.get("/api/reddit/thread")
    assert resp.status_code == 422


def test_auth_status_unauthenticated(client):
    resp = client.get("/api/reddit/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False


def test_saved_unauthenticated_returns_401(client):
    resp = client.get("/api/reddit/saved")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "not_authenticated"
```

- [ ] **Step 2.2 — Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos && python -m pytest tests/test_routes_reddit.py -v 2>&1 | head -20
```

Expected: ImportError.

### Step 2.3 — Implement the routes

Create `tinyagentos/routes/reddit.py`:

```python
"""API routes for Reddit browsing, proxied through the TinyAgentOS backend.

All routes live under /api/reddit/. The browser never calls Reddit directly,
avoiding CORS issues and centralising rate limiting.

Routes:
  GET /api/reddit/subreddit   — subreddit listing
  GET /api/reddit/thread      — single thread (post + comments)
  GET /api/reddit/search      — Reddit search
  GET /api/reddit/saved       — OAuth: user's saved posts
  GET /api/reddit/auth/status — whether OAuth token is stored
  GET /api/reddit/auth/start  — initiate OAuth flow
  GET /api/reddit/auth/callback — OAuth callback, store tokens

Auth tiers implemented:
  Tier 1 (default): unauthenticated .json API — public content
  Tier 2 (stored OAuth): token from SecretsStore
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tinyagentos.knowledge_fetchers.reddit import (
    fetch_thread,
    fetch_subreddit,
    fetch_saved,
    RedditPost,
    RedditComment,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_USER_AGENT = "TinyAgentOS/1.0"

# SecretsStore keys
_KEY_ACCESS_TOKEN = "reddit_access_token"
_KEY_REFRESH_TOKEN = "reddit_refresh_token"
_KEY_USERNAME = "reddit_username"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_to_dict(post: RedditPost) -> dict[str, Any]:
    return {
        "id": post.id,
        "subreddit": post.subreddit,
        "title": post.title,
        "author": post.author,
        "selftext": post.selftext,
        "score": post.score,
        "upvote_ratio": post.upvote_ratio,
        "num_comments": post.num_comments,
        "created_utc": post.created_utc,
        "url": post.url,
        "permalink": post.permalink,
        "flair": post.flair,
        "is_self": post.is_self,
    }


def _comment_to_dict(c: RedditComment) -> dict[str, Any]:
    return {
        "id": c.id,
        "author": c.author,
        "body": c.body,
        "score": c.score,
        "created_utc": c.created_utc,
        "depth": c.depth,
        "parent_id": c.parent_id,
        "edited": c.edited,
        "distinguished": c.distinguished,
    }


async def _get_token(request: Request) -> str | None:
    """Retrieve the stored Reddit OAuth access token, if any."""
    try:
        secrets = request.app.state.secrets_store
        return await secrets.get(_KEY_ACCESS_TOKEN)
    except Exception:
        return None


async def _get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the shared httpx client from app state, or create a temporary one."""
    try:
        return request.app.state.http_client
    except AttributeError:
        return httpx.AsyncClient()


# ---------------------------------------------------------------------------
# Browse routes
# ---------------------------------------------------------------------------

@router.get("/api/reddit/subreddit")
async def api_subreddit(
    request: Request,
    name: str = Query(..., description="Subreddit name without r/ prefix"),
    sort: str = Query("hot", description="hot | new | top | rising"),
    after: str | None = Query(None),
    limit: int = Query(25),
):
    """Fetch a subreddit listing. Returns posts + pagination cursor."""
    client = await _get_http_client(request)
    token = await _get_token(request)
    try:
        posts, next_after = await fetch_subreddit(
            subreddit=name,
            sort=sort,
            after=after,
            http_client=client,
            token=token,
        )
        return {"posts": [_post_to_dict(p) for p in posts], "after": next_after}
    except httpx.HTTPStatusError as exc:
        logger.warning("Reddit subreddit fetch failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Reddit subreddit unexpected error: %s", exc)
        return JSONResponse({"error": "fetch_failed"}, status_code=500)


@router.get("/api/reddit/thread")
async def api_thread(
    request: Request,
    url: str = Query(..., description="Full Reddit thread URL"),
):
    """Fetch a Reddit thread (post + all comments). No auth required for public threads."""
    client = await _get_http_client(request)
    token = await _get_token(request)
    try:
        post, comments = await fetch_thread(url, client, token=token)
        return {
            "post": _post_to_dict(post),
            "comments": [_comment_to_dict(c) for c in comments],
        }
    except httpx.HTTPStatusError as exc:
        logger.warning("Reddit thread fetch failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Reddit thread unexpected error: %s", exc)
        return JSONResponse({"error": "fetch_failed"}, status_code=500)


@router.get("/api/reddit/search")
async def api_search(
    request: Request,
    q: str = Query(...),
    subreddit: str | None = Query(None),
    sort: str = Query("relevance"),
    limit: int = Query(25),
    after: str | None = Query(None),
):
    """Search Reddit. Optionally scoped to a subreddit."""
    client = await _get_http_client(request)
    token = await _get_token(request)

    base = "https://oauth.reddit.com" if token else "https://www.reddit.com"
    path = f"/r/{subreddit}/search.json" if subreddit else "/search.json"
    params = f"q={q}&sort={sort}&limit={limit}&restrict_sr={'true' if subreddit else 'false'}"
    if after:
        params += f"&after={after}"

    headers: dict[str, str] = {"User-Agent": "TinyAgentOS/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await client.get(f"{base}{path}?{params}", headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        children = data["data"]["children"]
        next_after = data["data"].get("after")
        posts = []
        for child in children:
            if child.get("kind") != "t3":
                continue
            d = child["data"]
            from tinyagentos.knowledge_fetchers.reddit import RedditPost as RP
            posts.append(_post_to_dict(RP(
                id=d["id"], subreddit=d["subreddit"], title=d["title"],
                author=d.get("author", "[deleted]"), selftext=d.get("selftext", ""),
                score=d.get("score", 0), upvote_ratio=float(d.get("upvote_ratio", 0)),
                num_comments=d.get("num_comments", 0),
                created_utc=float(d.get("created_utc", 0)),
                url=d.get("url", ""), permalink=d.get("permalink", ""),
                flair=d.get("link_flair_text"), is_self=bool(d.get("is_self", False)),
            )))
        return {"posts": posts, "after": next_after}
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Reddit search failed: %s", exc)
        return JSONResponse({"error": "search_failed"}, status_code=500)


# ---------------------------------------------------------------------------
# OAuth / authenticated routes
# ---------------------------------------------------------------------------

@router.get("/api/reddit/saved")
async def api_saved(
    request: Request,
    after: str | None = Query(None),
):
    """Return the authenticated user's saved posts. Requires Reddit OAuth."""
    token = await _get_token(request)
    if not token:
        return JSONResponse(
            {"error": "not_authenticated", "auth_url": "/api/reddit/auth/start"},
            status_code=401,
        )
    client = await _get_http_client(request)
    try:
        posts, next_after = await fetch_saved(token=token, http_client=client, after=after)
        return {"posts": [_post_to_dict(p) for p in posts], "after": next_after}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            # Token expired — attempt one refresh
            refreshed = await _refresh_token(request)
            if refreshed:
                posts, next_after = await fetch_saved(token=refreshed, http_client=client, after=after)
                return {"posts": [_post_to_dict(p) for p in posts], "after": next_after}
            return JSONResponse({"error": "token_expired"}, status_code=401)
        return JSONResponse({"error": str(exc)}, status_code=exc.response.status_code)
    except Exception as exc:
        logger.exception("Reddit saved fetch failed: %s", exc)
        return JSONResponse({"error": "fetch_failed"}, status_code=500)


@router.get("/api/reddit/auth/status")
async def api_auth_status(request: Request):
    """Return whether an OAuth token is stored and the associated username."""
    try:
        secrets = request.app.state.secrets_store
        token = await secrets.get(_KEY_ACCESS_TOKEN)
        username = await secrets.get(_KEY_USERNAME)
        return {"authenticated": token is not None, "username": username}
    except Exception:
        return {"authenticated": False, "username": None}


@router.get("/api/reddit/auth/start")
async def api_auth_start(request: Request):
    """Initiate Reddit OAuth authorization_code flow.

    The client_id and client_secret must be stored in SecretsStore under
    'reddit_client_id' and 'reddit_client_secret'.
    """
    try:
        secrets = request.app.state.secrets_store
        client_id = await secrets.get("reddit_client_id")
        if not client_id:
            return JSONResponse(
                {"error": "no_client_id", "message": "Store reddit_client_id in Secrets app first."},
                status_code=400,
            )
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/api/reddit/auth/callback"
        scopes = "identity read history save"
        auth_url = (
            f"https://www.reddit.com/api/v1/authorize"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&state=taos"
            f"&redirect_uri={redirect_uri}"
            f"&duration=permanent"
            f"&scope={scopes.replace(' ', '+')}"
        )
        return RedirectResponse(url=auth_url)
    except Exception as exc:
        logger.exception("Reddit auth start failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/reddit/auth/callback")
async def api_auth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query("taos"),
    error: str | None = Query(None),
):
    """Exchange OAuth code for tokens and store in SecretsStore."""
    if error:
        return JSONResponse({"error": error}, status_code=400)

    try:
        secrets = request.app.state.secrets_store
        client_id = await secrets.get("reddit_client_id")
        client_secret = await secrets.get("reddit_client_secret") or ""
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/api/reddit/auth/callback"

        client = await _get_http_client(request)
        resp = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        token_data = resp.json()

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")

        # Fetch username
        me_resp = await client.get(
            "https://oauth.reddit.com/api/v1/me",
            headers={
                "User-Agent": _USER_AGENT,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=15,
        )
        username = ""
        if me_resp.is_success:
            me_data = me_resp.json()
            username = me_data.get("name", "")

        await secrets.set(_KEY_ACCESS_TOKEN, access_token)
        await secrets.set(_KEY_REFRESH_TOKEN, refresh_token)
        await secrets.set(_KEY_USERNAME, username)

        # Redirect back to the app
        return RedirectResponse(url="/#reddit")
    except Exception as exc:
        logger.exception("Reddit OAuth callback failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Token refresh helper
# ---------------------------------------------------------------------------

async def _refresh_token(request: Request) -> str | None:
    """Attempt to refresh the Reddit OAuth access token. Returns new token or None."""
    try:
        secrets = request.app.state.secrets_store
        refresh_token = await secrets.get(_KEY_REFRESH_TOKEN)
        client_id = await secrets.get("reddit_client_id")
        client_secret = await secrets.get("reddit_client_secret") or ""
        if not refresh_token or not client_id:
            return None

        client = await _get_http_client(request)
        resp = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id, client_secret),
            headers={"User-Agent": _USER_AGENT},
            timeout=30,
        )
        if not resp.is_success:
            await secrets.set(_KEY_ACCESS_TOKEN, "")
            return None

        token_data = resp.json()
        new_token = token_data.get("access_token", "")
        if new_token:
            await secrets.set(_KEY_ACCESS_TOKEN, new_token)
        return new_token or None
    except Exception as exc:
        logger.warning("Reddit token refresh failed: %s", exc)
        return None
```

- [ ] **Step 2.4 — Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos && python -m pytest tests/test_routes_reddit.py -v
```

Expected: All tests green.

### Step 2.5 — Register route in app

Find the route registration file (`tinyagentos/app.py` or equivalent) and add:

```python
from tinyagentos.routes.reddit import router as reddit_router
app.include_router(reddit_router)
```

Verify the route file used by the existing knowledge routes and mirror exactly.

---

## Task 3 — Frontend API Helpers + Tests

**Files:**
- Create: `desktop/src/lib/reddit.ts`
- Create: `desktop/tests/reddit.test.ts`

### Step 3.1 — Write failing tests

Create `desktop/tests/reddit.test.ts`:

```ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  fetchThread,
  fetchSubreddit,
  searchReddit,
  fetchSaved,
  getAuthStatus,
  saveToLibrary,
} from "../src/lib/reddit";
import type { RedditPost, RedditThread, RedditListing, RedditAuthStatus } from "../src/lib/reddit";

const MOCK_POST: RedditPost = {
  id: "abc123",
  subreddit: "LocalLLaMA",
  title: "Running LLMs on Orange Pi 5",
  author: "techuser42",
  selftext: "Some body text",
  score: 842,
  upvote_ratio: 0.97,
  num_comments: 67,
  created_utc: 1712880000,
  url: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
  permalink: "/r/LocalLLaMA/comments/abc123/",
  flair: "Discussion",
  is_self: true,
};

const MOCK_COMMENT = {
  id: "cmt001",
  author: "commentor1",
  body: "Great writeup!",
  score: 120,
  created_utc: 1712882000,
  depth: 0,
  parent_id: "t3_abc123",
  edited: false,
  distinguished: null,
};

function mockFetchJson(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: () => Promise.resolve(data),
  });
}

function mockFetchFail() {
  return vi.fn().mockRejectedValue(new Error("network error"));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("fetchThread", () => {
  it("returns post and comments", async () => {
    globalThis.fetch = mockFetchJson({ post: MOCK_POST, comments: [MOCK_COMMENT] });
    const result = await fetchThread("https://www.reddit.com/r/LocalLLaMA/comments/abc123/");
    expect(result).not.toBeNull();
    expect(result!.post.id).toBe("abc123");
    expect(result!.comments).toHaveLength(1);
    expect(result!.comments[0].author).toBe("commentor1");
  });

  it("passes url as query param", async () => {
    globalThis.fetch = mockFetchJson({ post: MOCK_POST, comments: [] });
    await fetchThread("https://reddit.com/r/test/comments/xyz/");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/reddit/thread");
    expect(url).toContain("url=");
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchThread("https://reddit.com/r/test/");
    expect(result).toBeNull();
  });

  it("returns null on non-2xx response", async () => {
    globalThis.fetch = mockFetchJson({ error: "not found" }, 404);
    const result = await fetchThread("https://reddit.com/r/test/");
    expect(result).toBeNull();
  });
});

describe("fetchSubreddit", () => {
  it("returns posts and after cursor", async () => {
    globalThis.fetch = mockFetchJson({ posts: [MOCK_POST], after: "t3_next" });
    const result = await fetchSubreddit("LocalLLaMA");
    expect(result.posts).toHaveLength(1);
    expect(result.after).toBe("t3_next");
  });

  it("passes sort and after params", async () => {
    globalThis.fetch = mockFetchJson({ posts: [], after: null });
    await fetchSubreddit("LocalLLaMA", "new", "t3_cursor");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("sort=new");
    expect(url).toContain("after=t3_cursor");
  });

  it("returns empty on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await fetchSubreddit("LocalLLaMA");
    expect(result.posts).toEqual([]);
    expect(result.after).toBeNull();
  });
});

describe("searchReddit", () => {
  it("returns posts for a query", async () => {
    globalThis.fetch = mockFetchJson({ posts: [MOCK_POST], after: null });
    const result = await searchReddit("Orange Pi LLM");
    expect(result.posts).toHaveLength(1);
  });

  it("passes subreddit scope", async () => {
    globalThis.fetch = mockFetchJson({ posts: [], after: null });
    await searchReddit("benchmark", "LocalLLaMA");
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("subreddit=LocalLLaMA");
  });
});

describe("fetchSaved", () => {
  it("returns saved posts", async () => {
    globalThis.fetch = mockFetchJson({ posts: [MOCK_POST], after: null });
    const result = await fetchSaved();
    expect(result.posts).toHaveLength(1);
  });

  it("returns empty on 401", async () => {
    globalThis.fetch = mockFetchJson({ error: "not_authenticated" }, 401);
    const result = await fetchSaved();
    expect(result.posts).toEqual([]);
  });
});

describe("getAuthStatus", () => {
  it("returns authenticated true when token stored", async () => {
    globalThis.fetch = mockFetchJson({ authenticated: true, username: "testuser" });
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(true);
    expect(result.username).toBe("testuser");
  });

  it("returns authenticated false on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await getAuthStatus();
    expect(result.authenticated).toBe(false);
  });
});

describe("saveToLibrary", () => {
  it("posts to knowledge ingest and returns id", async () => {
    globalThis.fetch = mockFetchJson({ id: "new-item", status: "pending" });
    const result = await saveToLibrary("https://reddit.com/r/test/comments/abc/", "Test Post");
    expect(result).not.toBeNull();
    expect(result!.id).toBe("new-item");
  });

  it("sends source as reddit-client", async () => {
    globalThis.fetch = mockFetchJson({ id: "x", status: "pending" });
    await saveToLibrary("https://reddit.com/r/test/");
    const body = JSON.parse(
      (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body as string,
    );
    expect(body.source).toBe("reddit-client");
  });

  it("returns null on network error", async () => {
    globalThis.fetch = mockFetchFail();
    const result = await saveToLibrary("https://reddit.com/r/test/");
    expect(result).toBeNull();
  });
});
```

- [ ] **Step 3.2 — Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos/desktop && npx vitest run tests/reddit.test.ts
```

Expected: FAIL — module `../src/lib/reddit` does not exist.

### Step 3.3 — Implement lib/reddit.ts

Create `desktop/src/lib/reddit.ts`:

```ts
/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface RedditPost {
  id: string;
  subreddit: string;
  title: string;
  author: string;
  selftext: string;
  score: number;
  upvote_ratio: number;
  num_comments: number;
  created_utc: number;
  url: string;
  permalink: string;
  flair: string | null;
  is_self: boolean;
}

export interface RedditComment {
  id: string;
  author: string;
  body: string;
  score: number;
  created_utc: number;
  depth: number;
  parent_id: string;
  edited: boolean | number;
  distinguished: string | null;
}

export interface RedditThread {
  post: RedditPost;
  comments: RedditComment[];
}

export interface RedditListing {
  posts: RedditPost[];
  after: string | null;
}

export interface RedditAuthStatus {
  authenticated: boolean;
  username?: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const res = await fetch(url, { ...init, headers: { Accept: "application/json", ...init?.headers } });
    if (!res.ok) return fallback;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

/* ------------------------------------------------------------------ */
/*  Thread                                                             */
/* ------------------------------------------------------------------ */

export async function fetchThread(url: string): Promise<RedditThread | null> {
  const qs = new URLSearchParams({ url });
  const data = await fetchJson<{ post?: RedditPost; comments?: RedditComment[] }>(
    `/api/reddit/thread?${qs}`,
    {},
  );
  if (!data.post) return null;
  return { post: data.post, comments: Array.isArray(data.comments) ? data.comments : [] };
}

/* ------------------------------------------------------------------ */
/*  Subreddit listing                                                  */
/* ------------------------------------------------------------------ */

export async function fetchSubreddit(
  name: string,
  sort = "hot",
  after?: string,
): Promise<RedditListing> {
  const qs = new URLSearchParams({ name, sort });
  if (after) qs.set("after", after);
  const data = await fetchJson<{ posts?: RedditPost[]; after?: string | null }>(
    `/api/reddit/subreddit?${qs}`,
    {},
  );
  return {
    posts: Array.isArray(data.posts) ? data.posts : [],
    after: data.after ?? null,
  };
}

/* ------------------------------------------------------------------ */
/*  Search                                                             */
/* ------------------------------------------------------------------ */

export async function searchReddit(
  query: string,
  subreddit?: string,
  after?: string,
): Promise<RedditListing> {
  const qs = new URLSearchParams({ q: query });
  if (subreddit) qs.set("subreddit", subreddit);
  if (after) qs.set("after", after);
  const data = await fetchJson<{ posts?: RedditPost[]; after?: string | null }>(
    `/api/reddit/search?${qs}`,
    {},
  );
  return {
    posts: Array.isArray(data.posts) ? data.posts : [],
    after: data.after ?? null,
  };
}

/* ------------------------------------------------------------------ */
/*  Saved posts (OAuth)                                                */
/* ------------------------------------------------------------------ */

export async function fetchSaved(after?: string): Promise<RedditListing> {
  const qs = new URLSearchParams();
  if (after) qs.set("after", after);
  const query = qs.toString();
  const data = await fetchJson<{ posts?: RedditPost[]; after?: string | null }>(
    `/api/reddit/saved${query ? `?${query}` : ""}`,
    {},
  );
  return {
    posts: Array.isArray(data.posts) ? data.posts : [],
    after: data.after ?? null,
  };
}

/* ------------------------------------------------------------------ */
/*  Auth                                                               */
/* ------------------------------------------------------------------ */

export async function getAuthStatus(): Promise<RedditAuthStatus> {
  const data = await fetchJson<RedditAuthStatus>(
    "/api/reddit/auth/status",
    { authenticated: false },
  );
  return data;
}

/* ------------------------------------------------------------------ */
/*  Save to Knowledge Base                                             */
/* ------------------------------------------------------------------ */

export async function saveToLibrary(
  url: string,
  title?: string,
): Promise<{ id: string; status: string } | null> {
  try {
    const res = await fetch("/api/knowledge/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        url,
        title: title ?? "",
        text: "",
        categories: [],
        source: "reddit-client",
      }),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}
```

- [ ] **Step 3.4 — Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos/desktop && npx vitest run tests/reddit.test.ts
```

Expected: All tests green.

---

## Task 4 — RedditClientApp.tsx + Registration

**Files:**
- Create: `desktop/src/apps/RedditClientApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

### Step 4.1 — Implement RedditClientApp.tsx

Create `desktop/src/apps/RedditClientApp.tsx`:

```tsx
import { useState, useEffect, useCallback, useMemo } from "react";
import {
  MessageCircle,
  Search,
  ChevronLeft,
  ExternalLink,
  BookmarkPlus,
  BookmarkCheck,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  AlertCircle,
  LogIn,
} from "lucide-react";
import { Button, Card, CardContent, Input, Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";
import {
  fetchThread,
  fetchSubreddit,
  searchReddit,
  fetchSaved,
  getAuthStatus,
  saveToLibrary,
} from "@/lib/reddit";
import { listItems, listSnapshots } from "@/lib/knowledge";
import type { RedditPost, RedditThread, RedditComment, RedditAuthStatus } from "@/lib/reddit";
import type { KnowledgeItem, Snapshot } from "@/lib/knowledge";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type View = "feed" | "thread" | "diff";
type FeedMode = "home" | "saved" | "subreddit";
type SortMode = "hot" | "new" | "top" | "rising";

interface SaveState {
  [postId: string]: "idle" | "saving" | "saved" | "error";
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const DEFAULT_SUBREDDITS = ["LocalLLaMA", "SBC", "selfhosted", "MachineLearning"];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const timeAgo = (ts: number): string => {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
};

const scoreLabel = (n: number): string =>
  n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

/* ------------------------------------------------------------------ */
/*  CommentNode                                                        */
/* ------------------------------------------------------------------ */

function CommentNode({ comment, maxDepth = 4 }: { comment: RedditComment; maxDepth?: number }) {
  const [collapsed, setCollapsed] = useState(false);

  if (comment.body === "[more]") {
    return (
      <div style={{ marginLeft: comment.depth * 16 }} className="text-xs text-shell-text-tertiary py-1">
        [load more replies]
      </div>
    );
  }

  const isDeleted = comment.author === "[deleted]" || comment.body === "[deleted]";

  return (
    <div style={{ marginLeft: comment.depth * 16 }} className="my-1">
      <div className="flex items-center gap-2 text-[11px] text-shell-text-tertiary mb-0.5">
        <button
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand comment" : "Collapse comment"}
          className="text-shell-text-tertiary hover:text-shell-text focus:outline-none"
        >
          {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        </button>
        <span className={isDeleted ? "italic text-shell-text-tertiary" : "text-shell-text-secondary"}>
          u/{comment.author}
        </span>
        <span>↑ {scoreLabel(comment.score)}</span>
        <span>{timeAgo(comment.created_utc)}</span>
        {comment.edited && <span className="text-[10px]">(edited)</span>}
      </div>
      {!collapsed && (
        <p
          className={`text-xs leading-relaxed whitespace-pre-wrap ml-4 ${
            isDeleted ? "text-shell-text-tertiary italic" : "text-shell-text"
          }`}
        >
          {comment.body}
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ThreadCard                                                         */
/* ------------------------------------------------------------------ */

function ThreadCard({
  post,
  savedItem,
  saveState,
  onOpen,
  onSave,
}: {
  post: RedditPost;
  savedItem: KnowledgeItem | undefined;
  saveState: "idle" | "saving" | "saved" | "error";
  onOpen: (post: RedditPost) => void;
  onSave: (post: RedditPost) => void;
}) {
  const isMonitored =
    savedItem && (savedItem.monitor.current_interval ?? 0) > 0;

  return (
    <Card
      className="border border-white/5 hover:border-white/10 transition-colors cursor-pointer bg-shell-surface/20"
      role="listitem"
    >
      <CardContent className="p-3">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0" onClick={() => onOpen(post)}>
            <h3 className="text-sm font-medium leading-snug mb-1 line-clamp-2">{post.title}</h3>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-shell-text-tertiary">
              <span className="bg-orange-500/20 text-orange-400 border border-orange-500/30 px-1.5 py-0.5 rounded text-[10px] font-medium">
                r/{post.subreddit}
              </span>
              <span>u/{post.author}</span>
              <span>·</span>
              <span>{timeAgo(post.created_utc)}</span>
              <span>·</span>
              <span>↑ {scoreLabel(post.score)}</span>
              <span>·</span>
              <span>💬 {post.num_comments}</span>
              {post.flair && (
                <>
                  <span>·</span>
                  <span className="bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-[10px]">
                    {post.flair}
                  </span>
                </>
              )}
              {isMonitored && (
                <span
                  className="flex items-center gap-1 text-amber-400"
                  title={`Last polled ${timeAgo(savedItem!.monitor.last_poll ?? 0)}`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse inline-block" />
                  monitoring
                </span>
              )}
              {savedItem?.categories.map((cat) => (
                <span
                  key={cat}
                  className="bg-accent/10 text-accent border border-accent/20 px-1.5 py-0.5 rounded text-[10px]"
                >
                  {cat}
                </span>
              ))}
            </div>
          </div>
          <Button
            size="sm"
            variant="ghost"
            aria-label={saveState === "saved" ? "Already saved" : "Save to Library"}
            aria-busy={saveState === "saving"}
            disabled={saveState === "saved" || saveState === "saving"}
            onClick={(e) => { e.stopPropagation(); onSave(post); }}
            className="shrink-0 h-7 w-7 p-0"
          >
            {saveState === "saved" ? (
              <BookmarkCheck size={14} className="text-green-400" />
            ) : saveState === "saving" ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <BookmarkPlus size={14} />
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  RedditClientApp                                                    */
/* ------------------------------------------------------------------ */

export function RedditClientApp({ windowId: _windowId }: { windowId: string }) {
  /* ---------- view state ---------- */
  const [view, setView] = useState<View>("feed");
  const [feedMode, setFeedMode] = useState<FeedMode>("home");
  const [subredditInput, setSubredditInput] = useState("");
  const [activeSubreddit, setActiveSubreddit] = useState(DEFAULT_SUBREDDITS[0]);
  const [sortMode, setSortMode] = useState<SortMode>("hot");
  const [pinnedSubreddits, setPinnedSubreddits] = useState<string[]>(DEFAULT_SUBREDDITS);

  /* ---------- feed state ---------- */
  const [posts, setPosts] = useState<RedditPost[]>([]);
  const [after, setAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  /* ---------- thread state ---------- */
  const [selectedThread, setSelectedThread] = useState<RedditThread | null>(null);
  const [selectedPost, setSelectedPost] = useState<RedditPost | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadTab, setThreadTab] = useState("comments");

  /* ---------- snapshots (history tab) ---------- */
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);

  /* ---------- knowledge items (for monitoring badges + saved check) ---------- */
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItem[]>([]);

  /* ---------- save states ---------- */
  const [saveStates, setSaveStates] = useState<SaveState>({});

  /* ---------- auth ---------- */
  const [authStatus, setAuthStatus] = useState<RedditAuthStatus>({ authenticated: false });
  const [authBannerDismissed, setAuthBannerDismissed] = useState(false);

  /* ---------- mobile ---------- */
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  /* ---------------------------------------------------------------- */
  /*  Data fetching                                                    */
  /* ---------------------------------------------------------------- */

  const fetchPosts = useCallback(async (resetAfter = true) => {
    setLoading(true);
    try {
      if (search.trim()) {
        const result = await searchReddit(search.trim(), feedMode === "subreddit" ? activeSubreddit : undefined);
        setPosts(result.posts);
        setAfter(result.after);
      } else if (feedMode === "saved") {
        const result = await fetchSaved();
        setPosts(result.posts);
        setAfter(result.after);
      } else {
        const sub = feedMode === "subreddit" ? activeSubreddit : "popular";
        const result = await fetchSubreddit(sub, sortMode, resetAfter ? undefined : after ?? undefined);
        setPosts((prev) => resetAfter ? result.posts : [...prev, ...result.posts]);
        setAfter(result.after);
      }
    } catch {
      setPosts([]);
    }
    setLoading(false);
  }, [feedMode, activeSubreddit, sortMode, search, after]);

  useEffect(() => {
    fetchPosts(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedMode, activeSubreddit, sortMode]);

  const fetchKnowledgeItems = useCallback(async () => {
    try {
      const result = await listItems({ source_type: "reddit", limit: 200 });
      setKnowledgeItems(result.items);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchKnowledgeItems();
  }, [fetchKnowledgeItems]);

  const fetchAuthStatus = useCallback(async () => {
    const status = await getAuthStatus();
    setAuthStatus(status);
  }, []);

  useEffect(() => {
    fetchAuthStatus();
  }, [fetchAuthStatus]);

  /* ---------------------------------------------------------------- */
  /*  Open thread                                                      */
  /* ---------------------------------------------------------------- */

  const openThread = useCallback(async (post: RedditPost) => {
    setSelectedPost(post);
    setView("thread");
    setThreadLoading(true);
    setSelectedThread(null);
    setSnapshots([]);
    setThreadTab("comments");

    const thread = await fetchThread(post.url || `https://www.reddit.com${post.permalink}`);
    setSelectedThread(thread);
    setThreadLoading(false);

    // Load snapshots if saved
    const ki = knowledgeItems.find((k) => k.source_url.includes(post.id));
    if (ki) {
      const snaps = await listSnapshots(ki.id);
      setSnapshots(snaps);
    }
  }, [knowledgeItems]);

  const goBackToFeed = useCallback(() => {
    setView("feed");
    setSelectedThread(null);
    setSelectedPost(null);
    setSnapshots([]);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Save                                                             */
  /* ---------------------------------------------------------------- */

  const handleSave = useCallback(async (post: RedditPost) => {
    setSaveStates((prev) => ({ ...prev, [post.id]: "saving" }));
    const threadUrl = `https://www.reddit.com${post.permalink}`;
    const result = await saveToLibrary(threadUrl, post.title);
    if (result) {
      setSaveStates((prev) => ({ ...prev, [post.id]: "saved" }));
      await fetchKnowledgeItems();
    } else {
      setSaveStates((prev) => ({ ...prev, [post.id]: "error" }));
    }
  }, [fetchKnowledgeItems]);

  /* ---------------------------------------------------------------- */
  /*  Derived                                                          */
  /* ---------------------------------------------------------------- */

  const savedItemForPost = useCallback(
    (post: RedditPost): KnowledgeItem | undefined =>
      knowledgeItems.find((k) => k.source_url.includes(post.id)),
    [knowledgeItems],
  );

  const saveStateForPost = useCallback(
    (post: RedditPost): "idle" | "saving" | "saved" | "error" => {
      if (saveStates[post.id]) return saveStates[post.id];
      if (savedItemForPost(post)) return "saved";
      return "idle";
    },
    [saveStates, savedItemForPost],
  );

  /* ---------------------------------------------------------------- */
  /*  Sidebar                                                          */
  /* ---------------------------------------------------------------- */

  const sidebarUI = (
    <nav
      className={
        isMobile
          ? "w-full flex flex-col overflow-hidden h-full"
          : "w-48 shrink-0 border-r border-white/5 bg-shell-surface/30 flex flex-col overflow-hidden"
      }
      aria-label="Reddit navigation"
    >
      <div className="flex items-center gap-2 px-3 py-3 border-b border-white/5 shrink-0">
        <MessageCircle size={15} className="text-orange-400" />
        <h1 className="text-sm font-semibold">Reddit</h1>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-4">
        {/* Auth status */}
        {authStatus.authenticated && (
          <div className="text-[10px] text-green-400 px-2">
            u/{authStatus.username}
          </div>
        )}

        {/* Feed modes */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">Browse</p>
          <div className="space-y-0.5">
            {(["home", "saved"] as FeedMode[]).map((mode) => (
              <Button
                key={mode}
                variant={feedMode === mode ? "secondary" : "ghost"}
                size="sm"
                aria-pressed={feedMode === mode}
                onClick={() => setFeedMode(mode)}
                className="w-full justify-start text-xs h-7 px-2 capitalize"
                disabled={mode === "saved" && !authStatus.authenticated}
              >
                {mode === "home" ? "Popular" : "Saved Posts"}
              </Button>
            ))}
          </div>
        </section>

        {/* Subreddits */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">Subreddits</p>
          <div className="space-y-0.5">
            {pinnedSubreddits.map((sub) => (
              <Button
                key={sub}
                variant={feedMode === "subreddit" && activeSubreddit === sub ? "secondary" : "ghost"}
                size="sm"
                aria-pressed={feedMode === "subreddit" && activeSubreddit === sub}
                onClick={() => { setFeedMode("subreddit"); setActiveSubreddit(sub); }}
                className="w-full justify-start text-xs h-7 px-2"
              >
                r/{sub}
              </Button>
            ))}
          </div>

          {/* Add subreddit */}
          <div className="flex gap-1 mt-1.5 px-0.5">
            <Input
              value={subredditInput}
              onChange={(e) => setSubredditInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && subredditInput.trim()) {
                  const name = subredditInput.trim().replace(/^r\//, "");
                  if (!pinnedSubreddits.includes(name)) {
                    setPinnedSubreddits((prev) => [...prev, name]);
                  }
                  setFeedMode("subreddit");
                  setActiveSubreddit(name);
                  setSubredditInput("");
                }
              }}
              placeholder="r/add..."
              className="h-6 text-xs"
              aria-label="Add subreddit"
            />
          </div>
        </section>

        {/* Monitored */}
        <section>
          <p className="text-[10px] uppercase tracking-wider text-shell-text-tertiary px-2 mb-1.5">Monitored</p>
          <div className="space-y-0.5">
            {knowledgeItems
              .filter((k) => (k.monitor.current_interval ?? 0) > 0)
              .slice(0, 5)
              .map((item) => (
                <Button
                  key={item.id}
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start text-xs h-7 px-2 text-left"
                  onClick={() => {
                    const url = item.source_url;
                    openThread({ id: item.source_id ?? "", subreddit: String(item.metadata.subreddit ?? ""), title: item.title, author: item.author, selftext: "", score: Number(item.metadata.score ?? 0), upvote_ratio: Number(item.metadata.upvote_ratio ?? 0), num_comments: Number(item.metadata.num_comments ?? 0), created_utc: item.created_at, url, permalink: url, flair: String(item.metadata.flair ?? "") || null, is_self: true });
                  }}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0 mr-1.5" />
                  <span className="truncate">{item.title}</span>
                </Button>
              ))}
          </div>
        </section>
      </div>
    </nav>
  );

  /* ---------------------------------------------------------------- */
  /*  Feed view                                                        */
  /* ---------------------------------------------------------------- */

  const feedUI = (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Auth banner */}
      {!authStatus.authenticated && !authBannerDismissed && (
        <div className="flex items-center gap-3 px-4 py-2 bg-orange-500/10 border-b border-orange-500/20 text-xs shrink-0">
          <LogIn size={13} className="text-orange-400 shrink-0" />
          <span className="flex-1 text-shell-text-secondary">
            Connect Reddit to access saved posts and personalised feeds.
          </span>
          <a
            href="/api/reddit/auth/start"
            target="_blank"
            rel="noopener noreferrer"
            className="text-orange-400 underline shrink-0"
          >
            Connect
          </a>
          <button
            onClick={() => setAuthBannerDismissed(true)}
            aria-label="Dismiss banner"
            className="text-shell-text-tertiary hover:text-shell-text focus:outline-none shrink-0"
          >
            ✕
          </button>
        </div>
      )}

      {/* Search + sort bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5 shrink-0">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") fetchPosts(true); }}
            placeholder="Search Reddit..."
            className="pl-7 h-7 text-xs"
            aria-label="Search Reddit"
          />
        </div>
        <div className="flex gap-0.5" role="group" aria-label="Sort mode">
          {(["hot", "new", "top"] as SortMode[]).map((s) => (
            <Button
              key={s}
              size="sm"
              variant={sortMode === s ? "secondary" : "ghost"}
              onClick={() => setSortMode(s)}
              className="h-7 px-2 text-xs capitalize"
            >
              {s}
            </Button>
          ))}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => fetchPosts(true)}
          className="h-7 w-7 p-0"
          aria-label="Refresh"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
        </Button>
      </div>

      {/* Post list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2" role="list" aria-label="Reddit posts">
        {loading && posts.length === 0 && (
          <div className="flex items-center justify-center py-12 text-sm text-shell-text-tertiary">
            Loading...
          </div>
        )}
        {!loading && posts.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 gap-2 text-shell-text-tertiary">
            <AlertCircle size={20} />
            <span className="text-sm">No posts found</span>
          </div>
        )}
        {posts.map((post) => (
          <ThreadCard
            key={post.id}
            post={post}
            savedItem={savedItemForPost(post)}
            saveState={saveStateForPost(post)}
            onOpen={openThread}
            onSave={handleSave}
          />
        ))}
        {after && !loading && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fetchPosts(false)}
            className="w-full text-xs"
          >
            Load more
          </Button>
        )}
      </div>
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Thread view                                                      */
  /* ---------------------------------------------------------------- */

  const threadSavedItem = selectedPost ? savedItemForPost(selectedPost) : undefined;

  const threadUI = selectedPost && (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5 shrink-0">
        <Button
          variant="ghost"
          size="sm"
          onClick={goBackToFeed}
          className="h-7 gap-1 text-xs"
          aria-label="Back to feed"
        >
          <ChevronLeft size={13} />
          Back
        </Button>
        <div className="flex-1 min-w-0 text-[11px] text-shell-text-tertiary truncate">
          <span className="text-orange-400">r/{selectedPost.subreddit}</span>
          {" · u/"}{selectedPost.author}
          {" · "}{timeAgo(selectedPost.created_utc)}
          {" · ↑ "}{scoreLabel(selectedPost.score)}
          {" · "}{Math.round(selectedPost.upvote_ratio * 100)}%
        </div>
        <a
          href={`https://www.reddit.com${selectedPost.permalink}`}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Open on Reddit"
          className="p-1.5 rounded hover:bg-white/5"
        >
          <ExternalLink size={13} />
        </a>
        <Button
          size="sm"
          variant={threadSavedItem ? "secondary" : "ghost"}
          aria-label={threadSavedItem ? "Already saved to Library" : "Save to Library"}
          aria-busy={saveStates[selectedPost.id] === "saving"}
          onClick={() => handleSave(selectedPost)}
          disabled={!!threadSavedItem || saveStates[selectedPost.id] === "saving"}
          className="h-7 gap-1 text-xs"
        >
          {threadSavedItem ? (
            <><BookmarkCheck size={13} className="text-green-400" /> Saved</>
          ) : (
            <><BookmarkPlus size={13} /> Save</>
          )}
        </Button>
      </div>

      {/* Thread content */}
      <div className="flex-1 overflow-y-auto p-4">
        {threadLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-shell-text-tertiary">
            Loading thread...
          </div>
        ) : selectedThread ? (
          <>
            {/* Title */}
            <h2 className="text-base font-semibold mb-2 leading-snug">{selectedThread.post.title}</h2>

            {/* Pills */}
            <div className="flex flex-wrap gap-1.5 mb-3">
              {selectedThread.post.flair && (
                <span className="bg-white/5 border border-white/10 px-2 py-0.5 rounded text-[11px]">
                  {selectedThread.post.flair}
                </span>
              )}
              {threadSavedItem?.categories.map((cat) => (
                <span
                  key={cat}
                  className="bg-accent/10 text-accent border border-accent/20 px-2 py-0.5 rounded text-[11px]"
                >
                  {cat}
                </span>
              ))}
            </div>

            {/* Summary box */}
            {threadSavedItem?.summary && (
              <details className="mb-4 bg-white/3 border border-white/5 rounded p-3">
                <summary className="text-xs text-shell-text-tertiary cursor-pointer select-none">
                  AI summary
                </summary>
                <p className="mt-2 text-xs leading-relaxed text-shell-text-secondary">
                  {threadSavedItem.summary}
                </p>
              </details>
            )}

            {/* Selftext */}
            {selectedThread.post.selftext && selectedThread.post.selftext !== "[deleted]" && (
              <p className="text-sm leading-relaxed whitespace-pre-wrap mb-4 text-shell-text-secondary border-b border-white/5 pb-4">
                {selectedThread.post.selftext}
              </p>
            )}

            {/* Tabs */}
            <Tabs value={threadTab} onValueChange={setThreadTab}>
              <TabsList className="mb-3" role="tablist" aria-label="Thread sections">
                <TabsTrigger value="comments" role="tab" aria-selected={threadTab === "comments"}>
                  Comments ({selectedThread.post.num_comments})
                </TabsTrigger>
                <TabsTrigger value="history" role="tab" aria-selected={threadTab === "history"}>
                  History ({snapshots.length})
                </TabsTrigger>
                <TabsTrigger value="metadata" role="tab" aria-selected={threadTab === "metadata"}>
                  Metadata
                </TabsTrigger>
              </TabsList>

              {/* Comments tab */}
              <TabsContent value="comments" role="tabpanel">
                {selectedThread.comments.length === 0 ? (
                  <p className="text-sm text-shell-text-tertiary">No comments.</p>
                ) : (
                  <div className="space-y-1">
                    {selectedThread.comments
                      .filter((c) => c.depth <= 4)
                      .map((c) => (
                        <CommentNode key={c.id} comment={c} />
                      ))}
                  </div>
                )}
              </TabsContent>

              {/* History tab */}
              <TabsContent value="history" role="tabpanel">
                {snapshots.length === 0 ? (
                  <p className="text-sm text-shell-text-tertiary">
                    {threadSavedItem ? "No snapshots yet." : "Save this thread to start monitoring."}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {snapshots.map((snap) => {
                      const diff = snap.diff_json as Record<string, unknown>;
                      const newComments = Number(diff.new_comments ?? 0);
                      const deletedComments = Number(diff.deleted_comments ?? 0);
                      const voteDelta = Number(diff.vote_delta ?? 0);
                      return (
                        <div
                          key={snap.id}
                          className="flex items-center gap-3 text-xs border border-white/5 rounded p-2"
                        >
                          <span className="text-shell-text-tertiary shrink-0">
                            {new Date(snap.snapshot_at * 1000).toLocaleString()}
                          </span>
                          <span className="text-green-400">+{newComments} comments</span>
                          {deletedComments > 0 && (
                            <span className="text-red-400">-{deletedComments} deleted</span>
                          )}
                          {voteDelta !== 0 && (
                            <span className={voteDelta > 0 ? "text-green-400" : "text-red-400"}>
                              {voteDelta > 0 ? "+" : ""}{voteDelta} votes
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </TabsContent>

              {/* Metadata tab */}
              <TabsContent value="metadata" role="tabpanel">
                <div className="space-y-1.5 text-xs">
                  {[
                    ["Subreddit", `r/${selectedThread.post.subreddit}`],
                    ["Author", `u/${selectedThread.post.author}`],
                    ["Score", String(selectedThread.post.score)],
                    ["Upvote ratio", `${Math.round(selectedThread.post.upvote_ratio * 100)}%`],
                    ["Comments", String(selectedThread.post.num_comments)],
                    ["Created", new Date(selectedThread.post.created_utc * 1000).toLocaleString()],
                    ["Flair", selectedThread.post.flair ?? "—"],
                    ...(threadSavedItem
                      ? [
                          ["KB status", threadSavedItem.status],
                          ["Monitor interval", threadSavedItem.monitor.current_interval ? `${Math.round(threadSavedItem.monitor.current_interval / 3600)}h` : "—"],
                          ["Last poll", threadSavedItem.monitor.last_poll ? timeAgo(threadSavedItem.monitor.last_poll) : "—"],
                        ]
                      : []),
                  ].map(([label, value]) => (
                    <div key={label} className="flex gap-3">
                      <span className="text-shell-text-tertiary w-28 shrink-0">{label}</span>
                      <span>{value}</span>
                    </div>
                  ))}
                </div>
              </TabsContent>
            </Tabs>
          </>
        ) : (
          <div className="flex items-center justify-center py-16 gap-2 text-sm text-shell-text-tertiary">
            <AlertCircle size={16} />
            Failed to load thread.
          </div>
        )}
      </div>
    </div>
  );

  /* ---------------------------------------------------------------- */
  /*  Root render                                                      */
  /* ---------------------------------------------------------------- */

  if (isMobile) {
    return (
      <div className="flex flex-col w-full h-full overflow-hidden text-shell-text">
        {view === "feed" ? (
          <>
            {sidebarUI}
            {feedUI}
          </>
        ) : (
          threadUI
        )}
      </div>
    );
  }

  return (
    <div className="flex w-full h-full overflow-hidden text-shell-text">
      {view === "feed" && sidebarUI}
      {view === "feed" ? feedUI : threadUI}
    </div>
  );
}
```

### Step 4.2 — Register in app-registry.ts

Add the reddit entry after the library entry in `desktop/src/registry/app-registry.ts`:

```typescript
  { id: "reddit", name: "Reddit", icon: "message-circle", category: "platform", component: () => import("@/apps/RedditClientApp").then((m) => ({ default: m.RedditClientApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: false, launchpadOrder: 14 },
```

Place it immediately after the library line:
```typescript
  { id: "library", name: "Library", ... launchpadOrder: 13.5 },
  { id: "reddit", name: "Reddit", ... launchpadOrder: 14 },    // <-- add here
```

---

## Task 5 — Manual Testing Checklist

Run after all tasks above are green.

- [ ] **Step 5.1 — Backend unit tests**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_fetcher_reddit.py tests/test_routes_reddit.py -v
```

All tests must pass.

- [ ] **Step 5.2 — Frontend unit tests**

```bash
cd /home/jay/tinyagentos/desktop
npx vitest run tests/reddit.test.ts
```

All tests must pass.

- [ ] **Step 5.3 — IngestPipeline smoke test**

```bash
cd /home/jay/tinyagentos
python -c "
import asyncio, httpx
from tinyagentos.knowledge_fetchers.reddit import fetch_thread, flatten_to_text, extract_metadata
async def test():
    async with httpx.AsyncClient() as c:
        post, comments = await fetch_thread('https://www.reddit.com/r/LocalLLaMA/comments/1c3lre7/whats_the_best_llm_for_coding_as_of_today/', c)
    print('Post:', post.title[:60])
    print('Comments:', len(comments))
    print('Meta:', extract_metadata(post))
    text = flatten_to_text(post, comments)
    print('Text length:', len(text))
asyncio.run(test())
"
```

Expected output: post title printed, comment count > 0, metadata dict printed, text length > 500.

- [ ] **Step 5.4 — Full app smoke test**

Start dev server: `cd /home/jay/tinyagentos/desktop && npm run dev`

1. Open Reddit Client from launchpad — should appear as "Reddit" with message-circle icon at position 14.
2. Feed loads r/LocalLLaMA hot posts.
3. Click a thread — thread view opens with post body and comments.
4. Click "Save to Library" on a thread — button shows spinner then "Saved" (green bookmark).
5. Open Library app — saved thread appears with source_type=reddit, status=ready after pipeline completes.
6. Return to Reddit Client — saved thread shows amber monitoring dot.
7. History tab on saved thread — shows "No snapshots yet." initially (correct, monitoring hasn't run).
8. Metadata tab — shows subreddit, score, comments, KB status=ready.
9. Search: type "Orange Pi" in search bar, press Enter — results appear.
10. Add subreddit: type "SBC" in sidebar input, press Enter — r/SBC appears in list and loads.
11. Mobile: resize window to < 640px — sidebar hides in thread view, back button returns to feed+sidebar.

- [ ] **Step 5.5 — Route registration check**

```bash
cd /home/jay/tinyagentos
python -c "
from tinyagentos.routes.reddit import router
routes = [r.path for r in router.routes]
print(routes)
assert '/api/reddit/thread' in routes
assert '/api/reddit/subreddit' in routes
assert '/api/reddit/saved' in routes
assert '/api/reddit/auth/status' in routes
print('All routes registered.')
"
```

---

## Dependencies

| Package | Already Present? | Use |
|---------|-----------------|-----|
| `httpx` | Yes | Backend HTTP client |
| `pytest-asyncio` | Yes | Async test runner |
| `respx` | Yes | HTTP mock in tests |
| lucide-react | Yes | Icons (MessageCircle, BookmarkPlus, etc.) |
| Tailwind CSS | Yes | Styling |
| shadcn/ui barrel | Yes | Button, Card, Input, Tabs |

No new packages required.

---

## Rollback

If the route registration breaks the server: remove the `include_router(reddit_router)` line. The frontend app will show empty state gracefully (all `fetchJson` helpers return fallbacks on non-2xx).
