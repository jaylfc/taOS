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
    mock_proc.kill = MagicMock()
    return mock_proc


@pytest.mark.asyncio
async def test_fetch_mocked(tmp_path):
    json_bytes = json.dumps([_FAKE_INFO]).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
    media_proc = _make_mock_proc()

    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_proc
        else:
            return media_proc

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
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

    json_bytes = json.dumps([_FAKE_INFO]).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
    media_proc = _make_mock_proc()

    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return meta_proc
        else:
            return media_proc

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            result = await fetch("https://www.youtube.com/watch?v=test123", media_dir=tmp_path)

    assert "Hello from transcript" in result["content"]
    assert len(result["metadata"]["transcript_segments"]) == 2


@pytest.mark.asyncio
async def test_fetch_metadata_error_raises(tmp_path):
    """fetch() raises RuntimeError if yt-dlp metadata step fails."""
    err_proc = _make_mock_proc(returncode=1, stderr=b"ERROR: video unavailable")

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=err_proc):
            with pytest.raises(RuntimeError, match="yt-dlp metadata fetch failed"):
                await fetch("https://www.youtube.com/watch?v=bad", media_dir=tmp_path)


# ---------------------------------------------------------------------------
# download_video() mocked tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_video_mocked_720(tmp_path):
    # --print after_move:filepath writes the final path, one line per download.
    stdout_text = b"/some/path/test123.mp4\n"
    dl_proc = _make_mock_proc(stdout=stdout_text)

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
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
    # Uses the machine-readable --print route, not stdout scraping
    assert "--print" in args
    assert "after_move:filepath" in args
    assert result == "/some/path/test123.mp4"


@pytest.mark.asyncio
async def test_download_video_mocked_best(tmp_path):
    stdout_text = b"/some/path/test123.webm\n"
    dl_proc = _make_mock_proc(stdout=stdout_text)

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=dl_proc) as mock_exec:
            await download_video(
                "https://www.youtube.com/watch?v=test123",
                quality="best",
                output_dir=tmp_path,
            )

    args = mock_exec.call_args[0]
    fmt_idx = list(args).index("-f")
    assert args[fmt_idx + 1] == "bestvideo+bestaudio/best"
    assert "--print" in args
    assert "after_move:filepath" in args


@pytest.mark.asyncio
async def test_download_video_failure_returns_none(tmp_path):
    err_proc = _make_mock_proc(returncode=1, stderr=b"Download error")

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=err_proc):
            result = await download_video(
                "https://www.youtube.com/watch?v=bad",
                output_dir=tmp_path,
            )

    assert result is None


@pytest.mark.asyncio
async def test_download_video_no_filepath_printed_returns_none(tmp_path):
    """If yt-dlp succeeds but --print yields no filepath, return None."""
    proc = _make_mock_proc(stdout=b"")

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await download_video(
                "https://www.youtube.com/watch?v=test123",
                output_dir=tmp_path,
            )

    assert result is None


# ---------------------------------------------------------------------------
# fetch() P2 (#2059): description + transcripts in all available languages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_returns_description_and_all_language_transcripts(tmp_path):
    """fetch() returns the description (previously dropped) and one
    transcript per caption language, requesting all languages from yt-dlp.
    The legacy ``content`` key stays the primary (English) transcript."""
    (tmp_path / "test123.en.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello in English\n\n"
    )
    (tmp_path / "test123.es.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHola en español\n\n"
    )

    json_bytes = json.dumps([_FAKE_INFO]).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
    media_proc = _make_mock_proc()

    seen_args: list[tuple] = []
    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        seen_args.append(args)
        if call_count == 1:
            return meta_proc
        return media_proc

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            result = await fetch(
                "https://www.youtube.com/watch?v=test123", media_dir=tmp_path
            )

    # Three invocations: metadata, manual captions, automatic captions.
    assert len(seen_args) == 3

    # Manual captions are requested for all languages, not just English,
    # in an invocation of their own (no --write-auto-sub).
    manual_args = seen_args[1]
    assert "--write-sub" in manual_args
    assert "--write-auto-sub" not in manual_args
    assert manual_args[list(manual_args).index("--sub-lang") + 1] == "all"

    # Automatic captions are restricted to en.*: with --sub-lang all,
    # --write-auto-sub would request every auto-translated track
    # (100+ per video), risking rate limits and timeouts.
    auto_args = seen_args[2]
    assert "--write-auto-sub" in auto_args
    assert "--write-sub" not in auto_args
    assert auto_args[list(auto_args).index("--sub-lang") + 1] == "en.*"

    # The description from the metadata JSON is no longer dropped.
    assert result["description"] == "A test description"

    transcripts = {t["language"]: t["content"] for t in result["transcripts"]}
    assert set(transcripts) == {"en", "es"}
    assert "Hello in English" in transcripts["en"]
    assert "Hola en español" in transcripts["es"]

    # Back-compat: content is the primary (English) transcript, and the
    # metadata names every language that was captured.
    assert result["content"] == transcripts["en"]
    assert set(result["metadata"]["transcript_languages"]) == {"en", "es"}


# ---------------------------------------------------------------------------
# fetch() review round on #4481: auto captions must not clobber manual ones,
# and exact "en" wins over English variants as the primary transcript
# ---------------------------------------------------------------------------

_MANUAL_EN_VTT = (
    "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nManual English line\n\n"
)
_AUTO_EN_VTT = (
    "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nAuto English line\n\n"
)


@pytest.mark.asyncio
async def test_fetch_auto_captions_do_not_overwrite_manual(tmp_path):
    """The automatic-caption invocation must not replace a manual caption
    file for the same language: yt-dlp overwrites existing subtitle files
    by default, which would discard the manual English transcript before
    artifact creation. The auto step therefore runs with --no-overwrites
    (it still downloads automatic languages that have no manual file)."""
    json_bytes = json.dumps([_FAKE_INFO]).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
    media_proc = _make_mock_proc()

    seen_args: list[tuple] = []
    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        seen_args.append(args)
        if call_count == 1:
            return meta_proc
        en_file = tmp_path / "test123.en.vtt"
        if "--write-sub" in args:
            # Manual step writes the manual English captions.
            en_file.write_text(_MANUAL_EN_VTT)
        elif "--write-auto-sub" in args:
            # Model yt-dlp: an existing subtitle file is replaced unless
            # --no-overwrites was passed.
            if "--no-overwrites" not in args or not en_file.exists():
                en_file.write_text(_AUTO_EN_VTT)
        return media_proc

    with patch("shutil.which", return_value="/usr/bin/yt-dlp"):
        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            result = await fetch(
                "https://www.youtube.com/watch?v=test123", media_dir=tmp_path
            )

    auto_args = seen_args[2]
    assert "--write-auto-sub" in auto_args
    assert "--no-overwrites" in auto_args
    # The manual English transcript survived the automatic step.
    assert "Manual English line" in result["content"]
    assert "Auto English line" not in result["content"]


@pytest.mark.asyncio
async def test_fetch_primary_prefers_exact_en_over_en_orig(tmp_path):
    """With both en-orig and en caption files present, the primary
    transcript is the exact "en" track: filename sorting puts en-orig
    first, so selection must search for exact "en" before en-* variants."""
    (tmp_path / "test123.en-orig.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nOriginal variant line\n\n"
    )
    (tmp_path / "test123.en.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nExact English line\n\n"
    )

    json_bytes = json.dumps([_FAKE_INFO]).encode()
    meta_proc = _make_mock_proc(stdout=json_bytes)
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
            result = await fetch(
                "https://www.youtube.com/watch?v=test123", media_dir=tmp_path
            )

    transcripts = {t["language"]: t["content"] for t in result["transcripts"]}
    assert set(transcripts) == {"en", "en-orig"}
    assert result["content"] == transcripts["en"]
    assert "Exact English line" in result["content"]
