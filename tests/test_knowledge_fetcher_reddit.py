from __future__ import annotations

"""Tests for tinyagentos.knowledge_fetchers.reddit"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.knowledge_fetchers.reddit import (
    RedditPost,
    RedditComment,
    fetch_thread,
    fetch_subreddit,
    flatten_to_text,
    extract_metadata,
    _normalise_url,
    _parse_comment,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def _make_http_client(json_data) -> AsyncMock:
    """Build a mock httpx.AsyncClient whose .get returns a pre-set JSON payload."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=json_data)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# test_normalise_url
# ---------------------------------------------------------------------------

def test_normalise_url_no_json_suffix():
    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/my_post"
    result = _normalise_url(url)
    assert result.endswith(".json?limit=500") or "limit=500" in result
    assert ".json" in result


def test_normalise_url_already_has_json_suffix():
    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/my_post.json"
    result = _normalise_url(url)
    # Should not double .json
    assert result.count(".json") == 1


def test_normalise_url_strips_query_params():
    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/?utm_source=share"
    result = _normalise_url(url)
    assert "utm_source" not in result
    assert "limit=500" in result


def test_normalise_url_uses_oauth_host_with_token():
    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"
    result = _normalise_url(url, token="mytoken")
    assert "oauth.reddit.com" in result


def test_normalise_url_uses_www_without_token():
    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"
    result = _normalise_url(url)
    assert "www.reddit.com" in result
    assert "oauth" not in result


# ---------------------------------------------------------------------------
# test_fetch_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_thread_post_fields():
    fixture = _load_fixture("reddit_thread.json")
    client = _make_http_client(fixture)

    post, comments = await fetch_thread(
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
        http_client=client,
    )

    assert post.id == "abc123"
    assert post.subreddit == "LocalLLaMA"
    assert post.title == "Running LLMs on Orange Pi 5 — my experience"
    assert post.author == "edge_hacker"
    assert post.score == 342
    assert post.upvote_ratio == 0.97
    assert post.num_comments == 42
    assert post.flair == "Discussion"
    assert post.is_self is True
    assert "Orange Pi" in post.selftext


@pytest.mark.asyncio
async def test_fetch_thread_comment_count():
    fixture = _load_fixture("reddit_thread.json")
    client = _make_http_client(fixture)

    _, comments = await fetch_thread(
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
        http_client=client,
    )

    # Fixture has: cmt001 (top-level), cmt003 (deleted, top-level), more stub
    assert len(comments) == 3


@pytest.mark.asyncio
async def test_fetch_thread_nested_comment():
    fixture = _load_fixture("reddit_thread.json")
    client = _make_http_client(fixture)

    _, comments = await fetch_thread(
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
        http_client=client,
    )

    top = comments[0]
    assert top.id == "cmt001"
    assert top.depth == 0
    assert len(top.replies) == 1

    nested = top.replies[0]
    assert nested.id == "cmt002"
    assert nested.depth == 1
    assert nested.author == "arm_dev"
    # edited field is a float timestamp
    assert isinstance(nested.edited, float)


# ---------------------------------------------------------------------------
# test_fetch_thread_deleted_comment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_thread_deleted_comment():
    fixture = _load_fixture("reddit_thread.json")
    client = _make_http_client(fixture)

    _, comments = await fetch_thread(
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
        http_client=client,
    )

    deleted = comments[1]
    assert deleted.id == "cmt003"
    assert deleted.author == "[deleted]"
    assert deleted.body == "[deleted]"


@pytest.mark.asyncio
async def test_fetch_thread_more_stub():
    fixture = _load_fixture("reddit_thread.json")
    client = _make_http_client(fixture)

    _, comments = await fetch_thread(
        "https://www.reddit.com/r/LocalLLaMA/comments/abc123/",
        http_client=client,
    )

    more_stub = comments[2]
    assert more_stub.author == "[more]"
    assert "more replies" in more_stub.body


# ---------------------------------------------------------------------------
# test_flatten_to_text
# ---------------------------------------------------------------------------

def _make_post(**kwargs) -> RedditPost:
    defaults = dict(
        id="abc123",
        subreddit="LocalLLaMA",
        title="Test post title",
        author="testuser",
        selftext="This is the post body.",
        score=100,
        upvote_ratio=0.95,
        num_comments=5,
        created_utc=1712000000.0,
        url="https://reddit.com/r/LocalLLaMA/comments/abc123/",
        permalink="/r/LocalLLaMA/comments/abc123/",
        flair="Discussion",
        is_self=True,
    )
    defaults.update(kwargs)
    return RedditPost(**defaults)


def _make_comment(**kwargs) -> RedditComment:
    defaults = dict(
        id="cmt001",
        author="commenter",
        body="Top-level comment body.",
        score=10,
        created_utc=1712001000.0,
        depth=0,
        parent_id="t3_abc123",
        replies=[],
        edited=False,
        distinguished=None,
    )
    defaults.update(kwargs)
    return RedditComment(**defaults)


def test_flatten_to_text_contains_title():
    post = _make_post()
    text = flatten_to_text(post, [])
    assert "# Test post title" in text


def test_flatten_to_text_contains_selftext():
    post = _make_post()
    text = flatten_to_text(post, [])
    assert "This is the post body." in text


def test_flatten_to_text_separator():
    post = _make_post()
    text = flatten_to_text(post, [])
    assert "---" in text


def test_flatten_to_text_top_comment():
    post = _make_post()
    comment = _make_comment()
    text = flatten_to_text(post, [comment])
    assert "commenter" in text
    assert "Top-level comment body." in text


def test_flatten_to_text_nested_comment_indented():
    post = _make_post()
    nested = _make_comment(
        id="cmt002", author="nested_user", body="Nested reply here.", depth=1,
        parent_id="t1_cmt001",
    )
    top = _make_comment(replies=[nested])
    text = flatten_to_text(post, [top])
    lines = text.splitlines()
    # Find the nested comment line — it should start with spaces
    nested_lines = [l for l in lines if "Nested reply here." in l]
    assert nested_lines, "nested comment body not found"
    assert nested_lines[0].startswith("  "), "nested comment should be indented"


def test_flatten_to_text_no_selftext():
    post = _make_post(selftext="")
    text = flatten_to_text(post, [])
    # Should not have double separator or empty lines before ---
    assert "# Test post title" in text
    assert "---" in text


# ---------------------------------------------------------------------------
# test_extract_metadata
# ---------------------------------------------------------------------------

def test_extract_metadata_keys():
    post = _make_post()
    meta = extract_metadata(post)
    expected_keys = {"subreddit", "score", "upvote_ratio", "num_comments", "created_utc", "flair", "is_self"}
    assert expected_keys == set(meta.keys())


def test_extract_metadata_values():
    post = _make_post()
    meta = extract_metadata(post)
    assert meta["subreddit"] == "LocalLLaMA"
    assert meta["score"] == 100
    assert meta["upvote_ratio"] == 0.95
    assert meta["num_comments"] == 5
    assert meta["flair"] == "Discussion"
    assert meta["is_self"] is True


# ---------------------------------------------------------------------------
# test_fetch_subreddit
# ---------------------------------------------------------------------------

def _make_subreddit_listing(posts_data: list[dict], after: str | None = None) -> dict:
    children = [{"kind": "t3", "data": pd} for pd in posts_data]
    return {
        "kind": "Listing",
        "data": {
            "children": children,
            "after": after,
            "before": None,
        },
    }


def _post_data_stub(idx: int) -> dict:
    return {
        "id": f"post{idx:03d}",
        "subreddit": "LocalLLaMA",
        "title": f"Post number {idx}",
        "author": f"author{idx}",
        "selftext": "",
        "score": idx * 10,
        "upvote_ratio": 0.9,
        "num_comments": idx,
        "created_utc": float(1712000000 + idx),
        "url": f"https://reddit.com/r/LocalLLaMA/comments/post{idx:03d}/",
        "permalink": f"/r/LocalLLaMA/comments/post{idx:03d}/",
        "link_flair_text": None,
        "is_self": True,
    }


@pytest.mark.asyncio
async def test_fetch_subreddit_returns_posts():
    stubs = [_post_data_stub(i) for i in range(3)]
    fixture = _make_subreddit_listing(stubs, after="t3_post002")
    client = _make_http_client(fixture)

    posts, next_after = await fetch_subreddit(
        subreddit="LocalLLaMA",
        sort="hot",
        after=None,
        http_client=client,
    )

    assert len(posts) == 3
    assert posts[0].id == "post000"
    assert posts[0].subreddit == "LocalLLaMA"
    assert next_after == "t3_post002"


@pytest.mark.asyncio
async def test_fetch_subreddit_no_next_after():
    stubs = [_post_data_stub(i) for i in range(2)]
    fixture = _make_subreddit_listing(stubs, after=None)
    client = _make_http_client(fixture)

    _, next_after = await fetch_subreddit(
        subreddit="LocalLLaMA",
        sort="new",
        after=None,
        http_client=client,
    )

    assert next_after is None


@pytest.mark.asyncio
async def test_fetch_subreddit_empty_listing():
    fixture = _make_subreddit_listing([], after=None)
    client = _make_http_client(fixture)

    posts, next_after = await fetch_subreddit(
        subreddit="emptysub",
        sort="hot",
        after=None,
        http_client=client,
    )

    assert posts == []
    assert next_after is None


# ---------------------------------------------------------------------------
# R2-16: unbounded recursion in _parse_comment / _flatten_comment
#          and edited=True -> 1.0 (1970 epoch) timestamp coercion
# ---------------------------------------------------------------------------

def _build_deep_comment_json(depth: int) -> dict:
    """Build a Reddit comment *data* dict forming a single chain of
    ``depth`` nesting levels (each parent has exactly one t1 child reply)."""
    node_data: dict = {
        "id": "leaf",
        "author": "deep_user",
        "body": "deepest reply",
        "score": 1,
        "created_utc": 1712000000.0,
        "depth": depth,
        "parent_id": "t1_parent",
        "edited": False,
        "distinguished": None,
        "replies": "",
    }
    for d in range(depth - 1, -1, -1):
        node_data = {
            "id": f"deep_{d}",
            "author": "deep_user",
            "body": f"level {d}",
            "score": 1,
            "created_utc": 1712000000.0 + d,
            "depth": d,
            "parent_id": "t3_root" if d == 0 else f"t1_deep_{d + 1}",
            "edited": False,
            "distinguished": None,
            "replies": {
                "kind": "Listing",
                "data": {
                    "children": [{"kind": "t1", "data": node_data}],
                    "after": None,
                    "before": None,
                },
            },
        }
    return node_data


def _build_deep_comment_chain(depth: int) -> RedditComment:
    """Build a deeply nested RedditComment chain of ``depth`` levels.

    Construction is iterative (no recursion) so it works regardless of the
    parser/flatten implementation.
    """
    node = _make_comment(
        id="leaf", author="deep_user", body="deepest reply.", depth=depth,
    )
    for d in range(depth - 1, -1, -1):
        node = _make_comment(
            id=f"deep_{d}", author="deep_user", body=f"level {d}",
            depth=d, replies=[node],
        )
    return node


def test_parse_comment_2000_deep_tree_no_recursion_error():
    """R2-16: a synthetic 2 000-deep comment tree must not raise RecursionError."""
    deep_data = _build_deep_comment_json(2000)
    comment = _parse_comment(deep_data)
    # Walk to the deepest node to confirm the full tree was built.
    node = comment
    count = 0
    while node.replies:
        node = node.replies[0]
        count += 1
    assert count == 2000  # 2 001 nodes total: depth 0 .. 2 000


def test_flatten_to_text_2000_deep_tree_no_recursion_error():
    """R2-16: flattening a 2 000-deep tree must not raise RecursionError."""
    comment = _build_deep_comment_chain(2000)
    post = _make_post()
    text = flatten_to_text(post, [comment])
    assert "deep_user" in text
    assert "deepest reply" in text


def test_parse_comment_edited_true_not_epoch_timestamp():
    """R2-16: edited=True (Reddit legacy boolean) must not become 1.0."""
    data = {
        "id": "cmt_edited",
        "author": "editor",
        "body": "I edited this comment.",
        "score": 5,
        "created_utc": 1712000000.0,
        "depth": 0,
        "parent_id": "t3_root",
        "edited": True,
        "distinguished": None,
        "replies": "",
    }
    comment = _parse_comment(data)
    assert comment.edited != 1.0
    assert comment.edited != 1
    assert comment.edited is None  # edited, but no timestamp


def test_parse_comment_edited_float_preserved():
    """A numeric edited value is preserved as a float timestamp."""
    data = {
        "id": "cmt_ts",
        "author": "editor",
        "body": "Edited at a known time.",
        "score": 5,
        "created_utc": 1712000000.0,
        "depth": 0,
        "parent_id": "t3_root",
        "edited": 1712002500.0,
        "distinguished": None,
        "replies": "",
    }
    comment = _parse_comment(data)
    assert comment.edited == 1712002500.0
    assert isinstance(comment.edited, float)


def test_parse_comment_edited_false_preserved():
    """edited=False means the comment was not edited."""
    data = {
        "id": "cmt_notedited",
        "author": "original",
        "body": "Never edited.",
        "score": 5,
        "created_utc": 1712000000.0,
        "depth": 0,
        "parent_id": "t3_root",
        "edited": False,
        "distinguished": None,
        "replies": "",
    }
    comment = _parse_comment(data)
    assert comment.edited is False


def test_parse_comment_edited_int_preserved():
    """An integer edited value (epoch seconds) is preserved as a float."""
    data = {
        "id": "cmt_int_edit",
        "author": "editor",
        "body": "Edited at an integer epoch.",
        "score": 5,
        "created_utc": 1712000000.0,
        "depth": 0,
        "parent_id": "t3_root",
        "edited": 1712002500,
        "distinguished": None,
        "replies": "",
    }
    comment = _parse_comment(data)
    assert comment.edited == 1712002500.0
    assert isinstance(comment.edited, float)
