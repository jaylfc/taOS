from __future__ import annotations

"""Tests for tinyagentos.knowledge_fetchers.youtube."""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from tinyagentos.knowledge_fetchers.youtube import (
    parse_vtt,
    format_timestamp,
    fetch,
    download_video,
)


# ---------------------------------------------------------------------------
# parse_vtt tests
# ---------------------------------------------------------------------------

SIMPLE_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:03.000
Hello world

00:00:04.000 --> 00:00:06.000
This is a test

"""


def test_parse_vtt_basic():
    segments = parse_vtt(SIMPLE_VTT)
    assert len(segments) == 2
    assert segments[0]["start"] == 1.0
    assert segments[0]["end"] == 3.0
    assert segments[0]["text"] == "Hello world"
    assert segments[1]["start"] == 4.0
    assert segments[1]["end"] == 6.0
    assert segments[1]["text"] == "This is a test"


DEDUP_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:02.000
Same line

00:00:02.000 --> 00:00:03.000
Same line

00:00:03.000 --> 00:00:04.000
Different line

"""


def test_parse_vtt_deduplication():
    segments = parse_vtt(DEDUP_VTT)
    # Two identical "Same line" blocks should collapse into one
    assert len(segments) == 2
    assert segments[0]["text"] == "Same line"
    # End time should be extended to cover both cues
    assert segments[0]["end"] == 3.0
    assert segments[1]["text"] == "Different line"


def test_parse_vtt_empty():
    assert parse_vtt("") == []
    assert parse_vtt("WEBVTT\n\nNOTE no captions here\n\n") == []


def test_parse_vtt_strips_inline_tags():
    vtt = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "<00:00:01.000><c>Hello</c> <c>world</c>\n\n"
    )
    segments = parse_vtt(vtt)
    assert len(segments) == 1
    assert "Hello" in segments[0]["text"]
    assert "<c>" not in segments[0]["text"]


# ---------------------------------------------------------------------------
# format_timestamp tests
# ---------------------------------------------------------------------------

def test_format_timestamp_seconds_only():
    assert format_timestamp(45.0) == "00:45"


def test_format_timestamp_minutes():
    assert format_timestamp(90.0) == "01:30"


def test_format_timestamp_hours():
    assert format_timestamp(3661.0) == "01:01:01"


def test_format_timestamp_zero():
    assert format_timestamp(0.0) == "00:00"


def test_format_timestamp_exact_hour():
    assert format_timestamp(3600.0) == "01:00:00"


# ---------------------------------------------------------------------------
# fetch() mocked tests
# ---------------------------------------------------------------------------

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
        {"title": "Main", "start_time": 60.0, "end_time": 300.0},
    ],
}


def _make_mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout, stderr))
    mock_proc.returncode = returncode
    mock_proc.wait = AsyncMock(return_value=returncode)
    return mock_proc


@pytest.mark.asyncio
async def test_fetch_mocked(tmp_path):
    json_bytes = json.dumps(_FAKE_INFO).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
    thumb_proc = _make_mock_proc()
    cap_proc = _make_mock_proc()

    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_proc
        elif call_count == 2:
            return thumb_proc
        else:
            return cap_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await fetch("https://www.youtube.com/watch?v=test123", media_dir=tmp_path)

    assert result["title"] == "Test Video Title"
    assert result["author"] == "Test Channel"
    assert result["content"] == ""  # no VTT file in tmp_path
    assert result["metadata"]["video_id"] == "test123"
    assert result["metadata"]["channel"] == "Test Channel"
    assert result["metadata"]["views"] == 12345
    assert result["metadata"]["likes"] == 678
    assert result["metadata"]["duration"] == 300.0
    assert result["metadata"]["upload_date"] == "20240101"
    assert len(result["metadata"]["chapters"]) == 2
    assert result["metadata"]["chapters"][0]["title"] == "Intro"
    assert result["metadata"]["transcript_segments"] == []


@pytest.mark.asyncio
async def test_fetch_mocked_with_transcript(tmp_path):
    """fetch() should parse a VTT file if it exists in media_dir."""
    # Write a fake VTT file for video id "test123"
    vtt_content = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "Hello from transcript\n\n"
        "00:00:04.000 --> 00:00:06.000\n"
        "Second line\n\n"
    )
    (tmp_path / "test123.en.vtt").write_text(vtt_content)

    json_bytes = json.dumps(_FAKE_INFO).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
    thumb_proc = _make_mock_proc()
    cap_proc = _make_mock_proc()

    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_proc
        elif call_count == 2:
            return thumb_proc
        else:
            return cap_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await fetch("https://www.youtube.com/watch?v=test123", media_dir=tmp_path)

    assert "Hello from transcript" in result["content"]
    assert len(result["metadata"]["transcript_segments"]) == 2


@pytest.mark.asyncio
async def test_fetch_metadata_error_raises(tmp_path):
    """fetch() raises RuntimeError if yt-dlp metadata step fails."""
    err_proc = _make_mock_proc(returncode=1, stderr=b"ERROR: video unavailable")

    with patch("asyncio.create_subprocess_exec", return_value=err_proc):
        with pytest.raises(RuntimeError, match="yt-dlp metadata fetch failed"):
            await fetch("https://www.youtube.com/watch?v=bad", media_dir=tmp_path)


# ---------------------------------------------------------------------------
# download_video() mocked tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_video_mocked_720(tmp_path):
    stdout_text = b"[download] Destination: /some/path/test123.mp4\n"
    dl_proc = _make_mock_proc(stdout=stdout_text)

    with patch("asyncio.create_subprocess_exec", return_value=dl_proc) as mock_exec:
        result = await download_video(
            "https://www.youtube.com/watch?v=test123",
            quality="720",
            output_dir=tmp_path,
        )

    args = mock_exec.call_args[0]
    # Check format string for 720p
    assert "-f" in args
    fmt_idx = list(args).index("-f")
    assert "height<=720" in args[fmt_idx + 1]
    assert result == "/some/path/test123.mp4"


@pytest.mark.asyncio
async def test_download_video_mocked_best(tmp_path):
    stdout_text = b"[download] Destination: /some/path/test123.webm\n"
    dl_proc = _make_mock_proc(stdout=stdout_text)

    with patch("asyncio.create_subprocess_exec", return_value=dl_proc) as mock_exec:
        await download_video(
            "https://www.youtube.com/watch?v=test123",
            quality="best",
            output_dir=tmp_path,
        )

    args = mock_exec.call_args[0]
    fmt_idx = list(args).index("-f")
    assert args[fmt_idx + 1] == "bestvideo+bestaudio/best"


@pytest.mark.asyncio
async def test_download_video_failure_returns_none(tmp_path):
    err_proc = _make_mock_proc(returncode=1, stderr=b"Download error")

    with patch("asyncio.create_subprocess_exec", return_value=err_proc):
        result = await download_video(
            "https://www.youtube.com/watch?v=bad",
            output_dir=tmp_path,
        )

    assert result is None


@pytest.mark.asyncio
async def test_download_video_no_destination_line_returns_none(tmp_path):
    """If yt-dlp succeeds but prints no 'Destination:' line, return None."""
    proc = _make_mock_proc(stdout=b"some other output\n")

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await download_video(
            "https://www.youtube.com/watch?v=test123",
            output_dir=tmp_path,
        )

    assert result is None
