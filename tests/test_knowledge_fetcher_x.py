from __future__ import annotations

"""Tests for tinyagentos.knowledge_fetchers.x"""

import json
import pytest
import pytest_asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.knowledge_fetchers.x import (
    fetch_tweet_ytdlp,
    fetch_tweet_cookies,
    reconstruct_thread,
    stitch_thread_text,
    extract_metadata,
    XWatchStore,
    WATCH_SCHEMA,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_YTDLP_OUTPUT = {
    "id": "1234567890",
    "description": "This is a test tweet about AI.",
    "uploader": "Test User",
    "uploader_id": "testhandle",
    "like_count": 42,
    "repost_count": 7,
    "view_count": 1500,
    "timestamp": 1700000000.0,
    "url": "https://example.com/video.mp4",
    "ext": "mp4",
    "thumbnails": [
        {"url": "https://example.com/thumb.jpg"},
    ],
}

SAMPLE_TWEET = {
    "id": "1234567890",
    "author": "Test User",
    "handle": "testhandle",
    "text": "This is a test tweet about AI.",
    "likes": 42,
    "reposts": 7,
    "views": 1500,
    "created_at": 1700000000.0,
    "media": [
        {"type": "video", "url": "https://example.com/video.mp4"},
        {"type": "image", "url": "https://example.com/thumb.jpg"},
    ],
}


# ---------------------------------------------------------------------------
# fetch_tweet_ytdlp tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_success():
    """fetch_tweet_ytdlp returns a correctly shaped dict on success."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps([SAMPLE_YTDLP_OUTPUT]).encode(), b"")
    )

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_tweet_ytdlp("https://twitter.com/testhandle/status/1234567890")

    assert result is not None
    assert result["id"] == "1234567890"
    assert result["author"] == "Test User"
    assert result["handle"] == "testhandle"
    assert result["text"] == "This is a test tweet about AI."
    assert result["likes"] == 42
    assert result["reposts"] == 7
    assert result["views"] == 1500
    assert result["created_at"] == 1700000000.0
    assert len(result["media"]) >= 1
    assert result["media"][0]["type"] == "video"


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_nonzero_returncode():
    """fetch_tweet_ytdlp raises RuntimeError when yt-dlp exits non-zero."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"ERROR: Not found"))

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="yt-dlp failed"):
                await fetch_tweet_ytdlp("https://twitter.com/bad/status/999")


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_invalid_json():
    """fetch_tweet_ytdlp raises RuntimeError when yt-dlp outputs invalid JSON."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"NOT_JSON", b""))

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="fetch_tweet_ytdlp error"):
                await fetch_tweet_ytdlp("https://twitter.com/test/status/123")


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_missing_counts():
    """fetch_tweet_ytdlp handles missing engagement counts gracefully."""
    minimal_output = {
        "id": "987",
        "description": "Hello",
        "uploader": "Alice",
        "uploader_id": "alice",
    }
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps([minimal_output]).encode(), b"")
    )

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_tweet_ytdlp("https://twitter.com/alice/status/987")

    assert result is not None
    assert result["likes"] == 0
    assert result["reposts"] == 0
    assert result["views"] == 0
    assert result["media"] == []


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_handle_from_uploader_url():
    """fetch_tweet_ytdlp falls back to uploader_url to extract handle."""
    output = {
        "id": "111",
        "description": "Test",
        "uploader": "Bob",
        "uploader_id": "",
        "uploader_url": "https://twitter.com/bobhandle",
    }
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps([output]).encode(), b"")
    )

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_tweet_ytdlp("https://twitter.com/bobhandle/status/111")

    assert result is not None
    assert result["handle"] == "bobhandle"


# ---------------------------------------------------------------------------
# fetch_tweet_cookies tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_tweet_cookies_returns_none():
    """fetch_tweet_cookies always returns None in v1."""
    mock_client = AsyncMock()
    result = await fetch_tweet_cookies("123", {"auth_token": "x"}, mock_client)
    assert result is None


# ---------------------------------------------------------------------------
# stitch_thread_text tests
# ---------------------------------------------------------------------------

def test_stitch_thread_text_single_tweet():
    tweets = [{"handle": "alice", "text": "Hello world"}]
    result = stitch_thread_text(tweets)
    assert result == "@alice\nHello world"


def test_stitch_thread_text_multiple_tweets():
    tweets = [
        {"handle": "alice", "text": "First tweet"},
        {"handle": "alice", "text": "Second tweet"},
    ]
    result = stitch_thread_text(tweets)
    assert result == "@alice\nFirst tweet\n\n@alice\nSecond tweet"


def test_stitch_thread_text_empty():
    result = stitch_thread_text([])
    assert result == ""


def test_stitch_thread_text_no_handle():
    tweets = [{"handle": "", "text": "Anonymous tweet"}]
    result = stitch_thread_text(tweets)
    assert result == "Anonymous tweet"


def test_stitch_thread_text_mixed_handles():
    tweets = [
        {"handle": "alice", "text": "Original"},
        {"handle": "", "text": "No handle here"},
        {"handle": "bob", "text": "Reply"},
    ]
    result = stitch_thread_text(tweets)
    parts = result.split("\n\n")
    assert parts[0] == "@alice\nOriginal"
    assert parts[1] == "No handle here"
    assert parts[2] == "@bob\nReply"


# ---------------------------------------------------------------------------
# extract_metadata tests
# ---------------------------------------------------------------------------

def test_extract_metadata_full_tweet():
    result = extract_metadata(SAMPLE_TWEET)
    assert result["likes"] == 42
    assert result["reposts"] == 7
    assert result["views"] == 1500
    assert result["handle"] == "testhandle"
    assert result["created_at"] == 1700000000.0


def test_extract_metadata_empty_tweet():
    result = extract_metadata({})
    assert result["likes"] == 0
    assert result["reposts"] == 0
    assert result["views"] == 0
    assert result["handle"] == ""
    assert result["created_at"] == 0


def test_extract_metadata_keys():
    result = extract_metadata(SAMPLE_TWEET)
    assert set(result.keys()) == {"likes", "reposts", "views", "handle", "created_at"}


# ---------------------------------------------------------------------------
# XWatchStore tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def watch_store(tmp_path):
    store = XWatchStore(db_path=tmp_path / "x-watches.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_watch_store_init_creates_db(tmp_path):
    db_path = tmp_path / "sub" / "x-watches.db"
    store = XWatchStore(db_path=db_path)
    await store.init()
    assert db_path.exists()
    await store.close()


@pytest.mark.asyncio
async def test_watch_store_create_and_get(watch_store):
    watch = await watch_store.create_watch("user-1", "elonmusk", frequency=3600)
    assert watch["handle"] == "elonmusk"
    assert watch["frequency"] == 3600
    assert watch["enabled"] == 1
    assert watch["filters"] == {}
    assert watch["last_check"] == 0

    fetched = await watch_store.get_watch("elonmusk", "user-1")
    assert fetched is not None
    assert fetched["handle"] == "elonmusk"


@pytest.mark.asyncio
async def test_watch_store_create_strips_at(watch_store):
    watch = await watch_store.create_watch("user-1", "@someuser")
    assert watch["handle"] == "someuser"


@pytest.mark.asyncio
async def test_watch_store_create_duplicate_raises(watch_store):
    await watch_store.create_watch("user-1", "alice")
    with pytest.raises(ValueError, match="already exists"):
        await watch_store.create_watch("user-1", "alice")


@pytest.mark.asyncio
async def test_watch_store_create_with_filters(watch_store):
    filters = {"min_likes": 100, "threads_only": True}
    watch = await watch_store.create_watch("user-1", "testuser", filters=filters, frequency=900)
    assert watch["filters"]["min_likes"] == 100
    assert watch["filters"]["threads_only"] is True
    assert watch["frequency"] == 900


@pytest.mark.asyncio
async def test_watch_store_list_watches(watch_store):
    await watch_store.create_watch("user-1", "user1")
    await watch_store.create_watch("user-1", "user2")
    watches = await watch_store.list_watches("user-1")
    assert len(watches) == 2
    handles = {w["handle"] for w in watches}
    assert "user1" in handles
    assert "user2" in handles


@pytest.mark.asyncio
async def test_watch_store_list_empty(watch_store):
    watches = await watch_store.list_watches("user-1")
    assert watches == []


@pytest.mark.asyncio
async def test_watch_store_update_frequency(watch_store):
    await watch_store.create_watch("user-1", "charlie", frequency=1800)
    updated = await watch_store.update_watch("charlie", "user-1", {"frequency": 600})
    assert updated is not None
    assert updated["frequency"] == 600


@pytest.mark.asyncio
async def test_watch_store_update_enabled(watch_store):
    await watch_store.create_watch("user-1", "dave")
    updated = await watch_store.update_watch("dave", "user-1", {"enabled": 0})
    assert updated is not None
    assert updated["enabled"] == 0


@pytest.mark.asyncio
async def test_watch_store_update_filters(watch_store):
    await watch_store.create_watch("user-1", "eve")
    new_filters = {"all_posts": True, "min_likes": 50}
    updated = await watch_store.update_watch("eve", "user-1", {"filters": new_filters})
    assert updated is not None
    assert updated["filters"]["all_posts"] is True


@pytest.mark.asyncio
async def test_watch_store_update_nonexistent_returns_none(watch_store):
    result = await watch_store.update_watch("ghost", "user-1", {"frequency": 100})
    assert result is None


@pytest.mark.asyncio
async def test_watch_store_delete_existing(watch_store):
    await watch_store.create_watch("user-1", "frank")
    deleted = await watch_store.delete_watch("frank", "user-1")
    assert deleted is True
    assert await watch_store.get_watch("frank", "user-1") is None


@pytest.mark.asyncio
async def test_watch_store_delete_nonexistent(watch_store):
    deleted = await watch_store.delete_watch("nobody", "user-1")
    assert deleted is False


@pytest.mark.asyncio
async def test_watch_store_get_nonexistent(watch_store):
    result = await watch_store.get_watch("nobody", "user-1")
    assert result is None


@pytest.mark.asyncio
async def test_watch_store_requires_init(tmp_path):
    store = XWatchStore(db_path=tmp_path / "x-watches.db")
    with pytest.raises(RuntimeError, match="init()"):
        await store.list_watches("user")


# ---------------------------------------------------------------------------
# RED tests for R2-20: XWatchStore must use data_dir, aiosqlite, user_id scoping,
# and be wired onto app.state.
# ---------------------------------------------------------------------------

def test_x_watch_store_app_state_after_create_app(tmp_path):
    from tinyagentos.app import create_app

    app = create_app(data_dir=tmp_path)
    assert hasattr(app.state, "x_watch_store")
    assert app.state.x_watch_store is not None
    import asyncio

    asyncio.run(app.state.x_watch_store.close())


def test_x_watch_store_path_under_data_dir(tmp_path, monkeypatch):
    from tinyagentos.app import create_app

    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    data_dir = tmp_path / "my_data"
    app = create_app(data_dir=data_dir)
    store = app.state.x_watch_store
    assert store is not None
    assert store.db_path == data_dir / "x-watches.db"
    import asyncio

    asyncio.run(store.close())


@pytest.mark.asyncio
async def test_x_watch_store_list_scoped_by_user(tmp_path):
    store = XWatchStore(db_path=tmp_path / "x-watches.db")
    await store.init()
    await store.create_watch("user-alice", "alice")
    await store.create_watch("user-bob", "bob")
    alice_watches = await store.list_watches("user-alice")
    bob_watches = await store.list_watches("user-bob")
    assert len(alice_watches) == 1
    assert alice_watches[0]["handle"] == "alice"
    assert len(bob_watches) == 1
    assert bob_watches[0]["handle"] == "bob"
    await store.close()
