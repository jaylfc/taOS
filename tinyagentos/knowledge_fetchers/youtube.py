from __future__ import annotations

"""YouTube fetcher for TinyAgentOS knowledge pipeline.

Uses yt-dlp as a subprocess to fetch metadata, thumbnails, captions,
and optionally download video files.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Tracked subprocesses so they can be killed on timeout or cancel.
_tracked_procs: list[asyncio.subprocess.Process] = []


def _cleanup_procs() -> None:
    """Kill all tracked subprocesses (called on timeout or cancel)."""
    for p in _tracked_procs:
        try:
            p.kill()
        except Exception:
            pass
    _tracked_procs.clear()

# Quality format map for yt-dlp -f flag
_QUALITY_FORMATS: dict[str, str] = {
    "360": "bestvideo[height<=360]+bestaudio/best[height<=360]",
    "480": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "best": "bestvideo+bestaudio/best",
}


# ---------------------------------------------------------------------------
# VTT parsing
# ---------------------------------------------------------------------------

def parse_vtt(vtt_text: str) -> list[dict]:
    """Parse a WebVTT string into a list of caption segments.

    Returns a list of dicts: [{"start": float, "end": float, "text": str}].
    Consecutive identical text entries are deduplicated (YouTube auto-captions
    repeat the same line across multiple cues).
    """
    segments: list[dict] = []
    lines = vtt_text.splitlines()

    # Timestamp pattern: HH:MM:SS.mmm --> HH:MM:SS.mmm (with optional position tags)
    ts_re = re.compile(
        r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
    )

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = ts_re.match(line)
        if m:
            start = _vtt_ts_to_seconds(m.group(1))
            end = _vtt_ts_to_seconds(m.group(2))
            # Collect text lines until next blank line
            text_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip():
                raw = lines[i].strip()
                # Strip VTT inline tags like <00:00:01.000><c>word</c>
                raw = re.sub(r"<[^>]+>", "", raw).strip()
                if raw:
                    text_lines.append(raw)
                i += 1
            text = " ".join(text_lines)
            if text:
                segments.append({"start": start, "end": end, "text": text})
        else:
            i += 1

    # Deduplicate consecutive identical text
    deduplicated: list[dict] = []
    for seg in segments:
        if deduplicated and deduplicated[-1]["text"] == seg["text"]:
            # Extend the end time of the previous segment
            deduplicated[-1]["end"] = seg["end"]
        else:
            deduplicated.append(dict(seg))

    return deduplicated


def _vtt_ts_to_seconds(ts: str) -> float:
    """Convert a VTT timestamp (HH:MM:SS.mmm or HH:MM:SS,mmm) to float seconds."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s


# ---------------------------------------------------------------------------
# Timestamp formatter
# ---------------------------------------------------------------------------

def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS or MM:SS string."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

async def fetch(
    url: str,
    media_dir: str | Path = "data/knowledge-media/youtube",
) -> dict:
    """Fetch metadata, thumbnail, and captions for a YouTube video.

    Uses yt-dlp as a subprocess. Does NOT download the video file itself;
    use ``download_video()`` for that.

    Returns a dict suitable for storage in KnowledgeItem fields.
    """
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Step 1: fetch JSON metadata (no download)
    # ------------------------------------------------------------------ #
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--dump-json", "--no-download", url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _tracked_procs.append(proc)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"yt-dlp metadata fetch failed for {url!r}: {err}")

    info = json.loads(stdout.decode())

    video_id: str = info.get("id", "")
    title: str = info.get("title", "")
    channel: str = info.get("channel") or info.get("uploader") or ""
    description: str = info.get("description") or ""
    view_count: int | None = info.get("view_count")
    like_count: int | None = info.get("like_count")
    duration: float | None = info.get("duration")
    upload_date: str = info.get("upload_date") or ""
    thumbnail_url: str = info.get("thumbnail") or ""

    # Chapters: yt-dlp returns list of {title, start_time, end_time}
    raw_chapters: list[dict] = info.get("chapters") or []
    chapters = [
        {
            "title": ch.get("title", ""),
            "start_time": ch.get("start_time", 0.0),
            "end_time": ch.get("end_time", 0.0),
        }
        for ch in raw_chapters
    ]

    # ------------------------------------------------------------------ #
    # Step 2: download thumbnail
    # ------------------------------------------------------------------ #
    thumbnail_path = media_dir / f"{video_id}.png"
    thumb_proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--write-thumbnail",
        "--skip-download",
        "--convert-thumbnails", "png",
        "-o", str(media_dir / "%(id)s"),
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _tracked_procs.append(thumb_proc)
    await thumb_proc.communicate()
    if thumb_proc.returncode != 0:
        logger.warning("yt-dlp thumbnail download failed for %s", url)

    # ------------------------------------------------------------------ #
    # Step 3: extract captions
    # ------------------------------------------------------------------ #
    cap_proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "--skip-download",
        "-o", str(media_dir / "%(id)s"),
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _tracked_procs.append(cap_proc)
    await cap_proc.communicate()
    if cap_proc.returncode != 0:
        logger.warning("yt-dlp caption extraction failed for %s", url)

    # ------------------------------------------------------------------ #
    # Step 4: parse VTT
    # ------------------------------------------------------------------ #
    segments: list[dict] = []
    # yt-dlp names auto-subs like: <id>.en.vtt or <id>.en-orig.vtt
    vtt_candidates = list(media_dir.glob(f"{video_id}*.vtt"))
    if vtt_candidates:
        vtt_file = vtt_candidates[0]
        try:
            vtt_text = vtt_file.read_text(encoding="utf-8", errors="replace")
            segments = parse_vtt(vtt_text)
        except Exception as exc:
            logger.warning("Failed to parse VTT for %s: %s", video_id, exc)

    transcript = "\n".join(seg["text"] for seg in segments)

    return {
        "title": title,
        "author": channel,
        "content": transcript,
        "thumbnail": str(thumbnail_path) if thumbnail_path.exists() else None,
        "metadata": {
            "video_id": video_id,
            "channel": channel,
            "views": view_count,
            "likes": like_count,
            "duration": duration,
            "upload_date": upload_date,
            "chapters": chapters,
            "transcript_segments": segments,
        },
    }


# ---------------------------------------------------------------------------
# Video download
# ---------------------------------------------------------------------------

async def download_video(
    url: str,
    quality: str = "720",
    output_dir: str | Path = "data/knowledge-media/youtube",
) -> str | None:
    """Download a YouTube video at the requested quality.

    Returns the output file path on success, None on failure.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fmt = _QUALITY_FORMATS.get(quality, _QUALITY_FORMATS["720"])
    output_template = str(output_dir / "%(id)s.%(ext)s")

    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "-f", fmt,
        "-o", output_template,
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _tracked_procs.append(proc)
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        logger.warning("yt-dlp download failed for %s: %s", url, err)
        return None

    # Try to find the downloaded file by parsing yt-dlp output
    output_text = stdout.decode(errors="replace")
    # yt-dlp prints lines like: [download] Destination: path/to/file.ext
    m = re.search(r"\[download\] Destination: (.+)", output_text)
    if m:
        return m.group(1).strip()

    # Fallback: return None — caller can scan output_dir for new files
    return None
