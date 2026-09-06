from __future__ import annotations

"""RED tests for yt-dlp invocation hygiene (tsk-biyrqn).

These tests verify the three acceptance criteria:
  1. PATH without yt-dlp -> item status error with the named message, not ready
  2. multi-line dump-json fixture parses
  3. two concurrent fetches, cancel one -> the other's process survives
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from tinyagentos.knowledge_fetchers.youtube import fetch, download_video
from tinyagentos.knowledge_fetchers.x import fetch_tweet_ytdlp


_YTDLP_NOT_FOUND_MSG = "yt-dlp not installed -- install the optional media extra"


_FAKE_INFO = {
    "id": "test123",
    "title": "Test Video Title",
    "channel": "Test Channel",
    "uploader": "Test Uploader",
    "description": "A test description",
    "view_count": 12345,
    "like_count": 678,
    "duration": 300.0,
    "upload_date": "20240101",
    "thumbnail": "https://i.ytimg.com/vi/test123/maxresdefault.jpg",
    "chapters": [
        {"title": "Intro", "start_time": 0.0, "end_time": 60.0},
    ],
}


def _make_mock_proc(returncode=0, stdout=b"", stderr=b""):
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout, stderr))
    mock_proc.returncode = returncode
    mock_proc.kill = MagicMock()
    return mock_proc


# ---------------------------------------------------------------------------
# Acceptance (1): PATH without yt-dlp -> item status error with the named
# message, not ready
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_ytdlp_not_installed_raises_named_error(tmp_path):
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match=_YTDLP_NOT_FOUND_MSG):
            await fetch("https://www.youtube.com/watch?v=test123", media_dir=tmp_path)


@pytest.mark.asyncio
async def test_fetch_tweet_ytdlp_not_installed_raises_named_error():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match=_YTDLP_NOT_FOUND_MSG):
            await fetch_tweet_ytdlp("https://twitter.com/test/status/123")


@pytest.mark.asyncio
async def test_download_video_ytdlp_not_installed_raises_named_error(tmp_path):
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match=_YTDLP_NOT_FOUND_MSG):
            await download_video(
                "https://www.youtube.com/watch?v=test123",
                output_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# Acceptance (2): multi-line dump-json fixture parses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_parses_multiline_dump_single_json(tmp_path):
    multi_line_json = json.dumps([
        {
            "id": "test123",
            "title": "Test Video Title",
            "channel": "Test Channel",
            "uploader": "Test Uploader",
            "description": "A test description",
            "view_count": 12345,
            "like_count": 678,
            "duration": 300.0,
            "upload_date": "20240101",
            "thumbnail": "https://i.ytimg.com/vi/test123/maxresdefault.jpg",
            "chapters": [
                {"title": "Intro", "start_time": 0.0, "end_time": 60.0},
            ],
        }
    ], indent=2).encode()

    meta_proc = _make_mock_proc(stdout=multi_line_json)
    media_proc = _make_mock_proc()

    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_proc
        return media_proc

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            result = await fetch("https://www.youtube.com/watch?v=test123", media_dir=tmp_path)

    assert result["title"] == "Test Video Title"
    assert result["metadata"]["video_id"] == "test123"


@pytest.mark.asyncio
async def test_fetch_tweet_parses_multiline_dump_single_json():
    multi_line_json = json.dumps([
        {
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
            "thumbnails": [],
        }
    ], indent=2).encode()

    mock_proc = _make_mock_proc(stdout=multi_line_json)

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await fetch_tweet_ytdlp("https://twitter.com/test/status/123")

    assert result is not None
    assert result["id"] == "1234567890"
    assert result["text"] == "This is a test tweet about AI."


# ---------------------------------------------------------------------------
# Acceptance (3): two concurrent fetches, cancel one -> the other's process
# survives
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_fetch_cancel_does_not_kill_other(tmp_path):
    mock_proc1 = AsyncMock()
    mock_proc1.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    mock_proc1.returncode = None
    mock_proc1.kill = MagicMock()

    mock_proc2 = AsyncMock()
    mock_proc2.communicate = AsyncMock(return_value=(json.dumps([_FAKE_INFO]).encode(), b""))
    mock_proc2.returncode = 0
    mock_proc2.kill = MagicMock()

    media_proc2 = AsyncMock()
    media_proc2.communicate = AsyncMock(return_value=(b"", b""))
    media_proc2.returncode = 0
    media_proc2.kill = MagicMock()

    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_proc1
        elif call_count == 2:
            return mock_proc2
        else:
            return media_proc2

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            task1 = asyncio.create_task(
                fetch("https://www.youtube.com/watch?v=test1", media_dir=tmp_path)
            )
            task2 = asyncio.create_task(
                fetch("https://www.youtube.com/watch?v=test2", media_dir=tmp_path)
            )

            await asyncio.sleep(0)
            task1.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task1

            result = await task2

            # task1's proc was killed (cleanup in finally)
            mock_proc1.kill.assert_called_once()
            # task2 completes successfully despite task1 being cancelled
            assert result["title"] == "Test Video Title"
