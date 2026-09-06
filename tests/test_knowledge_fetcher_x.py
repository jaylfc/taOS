from __future__ import annotations

"""Tests for tinyagentos.knowledge_fetchers.x"""

import json
import pytest
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
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps(SAMPLE_YTDLP_OUTPUT).encode(), b"")
    )

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
    """fetch_tweet_ytdlp returns None when yt-dlp exits non-zero."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"ERROR: Not found"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await fetch_tweet_ytdlp("https://twitter.com/bad/status/999")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_invalid_json():
    """fetch_tweet_ytdlp returns None when yt-dlp outputs invalid JSON."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"NOT_JSON", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await fetch_tweet_ytdlp("https://twitter.com/test/status/123")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_missing_counts():
    """fetch_tweet_ytdlp handles missing engagement counts gracefully."""
    minimal_output = {
        "id": "987",
        "description": "Hello",
        "uploader": "Alice",
        "uploader_id": "alice",
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps(minimal_output).encode(), b"")
    )

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
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(json.dumps(output).encode(), b"")
    )

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

@pytest.fixture
def watch_store(tmp_path):
    store = XWatchStore(db_path=tmp_path / "x-watches.db")
    store.init()
    yield store
    store.close()


def test_watch_store_init_creates_db(tmp_path):
    db_path = tmp_path / "sub" / "x-watches.db"
    store = XWatchStore(db_path=db_path)
    store.init()
    assert db_path.exists()
    store.close()


def test_watch_store_create_and_get(watch_store):
    watch = watch_store.create_watch("elonmusk", frequency=3600)
    assert watch["handle"] == "elonmusk"
    assert watch["frequency"] == 3600
    assert watch["enabled"] == 1
    assert watch["filters"] == {}
    assert watch["last_check"] == 0

    fetched = watch_store.get_watch("elonmusk")
    assert fetched is not None
    assert fetched["handle"] == "elonmusk"


def test_watch_store_create_strips_at(watch_store):
    watch = watch_store.create_watch("@someuser")
    assert watch["handle"] == "someuser"


def test_watch_store_create_duplicate_raises(watch_store):
    watch_store.create_watch("alice")
    with pytest.raises(ValueError, match="already exists"):
        watch_store.create_watch("alice")


def test_watch_store_create_with_filters(watch_store):
    filters = {"min_likes": 100, "threads_only": True}
    watch = watch_store.create_watch("testuser", filters=filters, frequency=900)
    assert watch["filters"]["min_likes"] == 100
    assert watch["filters"]["threads_only"] is True
    assert watch["frequency"] == 900


def test_watch_store_list_watches(watch_store):
    watch_store.create_watch("user1")
    watch_store.create_watch("user2")
    watches = watch_store.list_watches()
    assert len(watches) == 2
    handles = {w["handle"] for w in watches}
    assert "user1" in handles
    assert "user2" in handles


def test_watch_store_list_empty(watch_store):
    watches = watch_store.list_watches()
    assert watches == []


def test_watch_store_update_frequency(watch_store):
    watch_store.create_watch("charlie", frequency=1800)
    updated = watch_store.update_watch("charlie", {"frequency": 600})
    assert updated is not None
    assert updated["frequency"] == 600


def test_watch_store_update_enabled(watch_store):
    watch_store.create_watch("dave")
    updated = watch_store.update_watch("dave", {"enabled": 0})
    assert updated is not None
    assert updated["enabled"] == 0


def test_watch_store_update_filters(watch_store):
    watch_store.create_watch("eve")
    new_filters = {"all_posts": True, "min_likes": 50}
    updated = watch_store.update_watch("eve", {"filters": new_filters})
    assert updated is not None
    assert updated["filters"]["all_posts"] is True


def test_watch_store_update_nonexistent_returns_none(watch_store):
    result = watch_store.update_watch("ghost", {"frequency": 100})
    assert result is None


def test_watch_store_delete_existing(watch_store):
    watch_store.create_watch("frank")
    deleted = watch_store.delete_watch("frank")
    assert deleted is True
    assert watch_store.get_watch("frank") is None


def test_watch_store_delete_nonexistent(watch_store):
    deleted = watch_store.delete_watch("nobody")
    assert deleted is False


def test_watch_store_get_nonexistent(watch_store):
    result = watch_store.get_watch("nobody")
    assert result is None


def test_watch_store_requires_init():
    store = XWatchStore()
    with pytest.raises(RuntimeError, match="init()"):
        store.list_watches()
