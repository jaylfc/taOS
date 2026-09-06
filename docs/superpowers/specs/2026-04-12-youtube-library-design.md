# YouTube Library

## Overview

A YouTube browsing and archiving app for taOS. Ingest videos by URL — metadata, thumbnails, and transcripts are captured automatically. Watch videos via YouTube iframe embed, with optional local download for offline access. Transcripts displayed alongside the player with clickable timestamps. All saved videos enter the Knowledge Base for search, categorisation, and agent access.

Build order: #4 in the Knowledge Capture Pipeline.

---

## Architecture

```
desktop/src/
├── apps/YouTubeLibraryApp.tsx     # Main app component
└── lib/youtube.ts                 # Typed fetch wrappers for /api/youtube/*

tinyagentos/
├── knowledge_fetchers/youtube.py  # YouTubeFetcher: yt-dlp subprocess wrapper
└── routes/youtube.py              # /api/youtube/* endpoints
```

Registered in `app-registry.ts` as:
- `id: "youtube-library"`
- `name: "YouTube"`
- `icon: "play-circle"`
- `category: "platform"`
- `launchpadOrder: 14.5`
- `singleton: true`
- `pinned: false`
- `defaultSize: { w: 1050, h: 700 }`
- `minSize: { w: 600, h: 450 }`

---

## Auth

YouTube metadata and transcripts are public — no auth required for core functionality. yt-dlp handles extraction without API keys.

Optional: YouTube Data API key (stored in Secrets) for higher quota access to channel subscriptions, playlists, and watch history. Not required for v1.

---

## Layout

Same two-state pattern as Library App: `list` (default) and `detail`.

### List View

Sidebar filters: Channels (from saved videos), Downloaded (local copies), Monitored, Categories, Status.

Item cards: Thumbnail (from yt-dlp), title, channel name, view count, duration, relative date, category pills, download status badge.

Search bar with keyword/semantic toggle. Sort by newest, most viewed, recently added.

### Detail View

Full replacement with back button.

**Player area:** YouTube iframe embed (`youtube.com/embed/{id}?enablejsapi=1`). If video is downloaded locally, play from local storage via HTML5 `<video>` tag instead.

**Summary card:** LLM-generated summary below the player.

**Expandable transcript panel:** Collapsible panel below summary.
- If video has YouTube chapters: collapsible chapter sections, each containing timestamped segments
- If no chapters: flat list of timestamped segments
- Each segment: timestamp button (clickable, seeks player via postMessage) + text
- Currently-playing segment highlighted
- Searchable within transcript
- "No transcript available" message when captions don't exist

**Tabs:** Transcript (default, same content as the expandable panel for full-page view), History (monitoring snapshots), Metadata (views, likes, channel, description, tags, publish date).

**Action bar:** Open on YouTube, Re-ingest, Download media (with quality picker), Stop monitoring, Delete.

---

## Download System

**Quality presets:**
- Low (360p) — smallest file
- Medium (720p) — default
- High (1080p)
- Best (highest available)
- Manual override — specific resolution/codec selection

**Download location:** Defaults to `knowledge-media/youtube/`. Configurable per-download or globally in Settings.

**Format:** Default mp4 (h264) for compatibility. Option for webm/vp9 for smaller files.

**Download status:** Shown on item cards as badges (not downloaded, downloading with progress, downloaded with file size).

**Storage management:** Downloaded videos show file size. "Delete local copy" removes the file but keeps the KnowledgeItem (metadata, transcript, thumbnail preserved).

### Research: Small Files + AI Upscaling

Investigate saving at lower quality (360p/480p) and using AI upscaling on playback:
- Real-ESRGAN or similar for video upscaling
- Jellyfin integration for hardware-accelerated transcoding (see #192)
- Trade-off analysis: storage saved vs playback quality vs CPU/GPU cost

---

## Backend: YouTubeFetcher

File: `tinyagentos/knowledge_fetchers/youtube.py`

Uses `asyncio.create_subprocess_exec` to call yt-dlp as a subprocess (no import dependency — yt-dlp upgrades don't break the Python env).

### `fetch(url, http_client) -> dict`

1. Run `yt-dlp --dump-json --no-download {url}` to extract metadata
2. Parse JSON output for: title, channel, description, view_count, like_count, duration, upload_date, thumbnail URL, chapters
3. Download thumbnail via httpx to `knowledge-media/youtube/`
4. Extract captions: pick best English track (manual subtitles preferred over auto-captions)
5. Parse VTT captions into `[{start, end, text}]` segments
6. Return dict mapping to KnowledgeItem fields:
   - `title`: video title
   - `author`: channel name
   - `content`: full transcript text (segments joined)
   - `summary`: (populated by IngestPipeline LLM step)
   - `thumbnail`: local path to downloaded thumbnail
   - `metadata`: `{channel, views, likes, duration, chapters, upload_date, video_id}`

### `download_video(url, quality, output_dir) -> str`

1. Run `yt-dlp -f "bestvideo[height<={quality}]+bestaudio/best[height<={quality}]" -o "{output_dir}/%(id)s.%(ext)s" {url}`
2. Return local file path
3. Update KnowledgeItem `media_path` in the database

### IngestPipeline Wire-In

```python
if source_type == "youtube":
    from tinyagentos.knowledge_fetchers.youtube import fetch
    result = await fetch(url, self._http_client)
    return result["content"], result["title"], result["author"], result["metadata"]
```

---

## Backend: YouTube API Routes

File: `tinyagentos/routes/youtube.py`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/youtube/ingest` | Submit YouTube URL for ingest (delegates to /api/knowledge/ingest with source_type override) |
| POST | `/api/youtube/download` | Trigger video download with quality setting. Returns immediately, download runs in background. |
| GET | `/api/youtube/download-status/{item_id}` | Check download progress |
| GET | `/api/youtube/transcript/{item_id}` | Get parsed transcript segments with timestamps |

---

## Frontend: lib/youtube.ts

Types:
```typescript
TranscriptSegment { start: number; end: number; text: string }
Chapter { title: string; start: number; end: number }
YouTubeMetadata { channel: string; views: number; likes: number; duration: number; chapters: Chapter[]; upload_date: string; video_id: string }
DownloadStatus { status: "idle" | "downloading" | "complete" | "error"; progress?: number; file_size?: string; path?: string }
```

Functions:
```typescript
ingestVideo(url: string): Promise<{ id: string; status: string } | null>
downloadVideo(itemId: string, quality: string): Promise<boolean>
getDownloadStatus(itemId: string): Promise<DownloadStatus>
getTranscript(itemId: string): Promise<TranscriptSegment[]>
```

---

## Non-Goals

- YouTube account login / subscriptions / playlists (future, needs OAuth)
- Video editing or clipping
- Comment browsing (YouTube comments are low-signal compared to Reddit)
- Live stream recording
- Audio-only extraction (future enhancement)
