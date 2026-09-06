# YouTube Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the YouTube Library app — ingest videos via yt-dlp, iframe playback, expandable transcripts with chapters, configurable downloads with quality presets.

**Architecture:** `YouTubeFetcher` backend (yt-dlp subprocess), FastAPI routes at `/api/youtube/*`, `YouTubeLibraryApp.tsx` frontend (list/detail with embedded player + transcript panel), `lib/youtube.ts` API helpers.

**Tech Stack:** Python, yt-dlp (subprocess), FastAPI, React, TypeScript, Tailwind, Vitest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-youtube-library-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/knowledge_fetchers/youtube.py` | YouTubeFetcher — yt-dlp metadata, thumbnail, transcript extraction |
| Create | `tests/test_knowledge_fetcher_youtube.py` | Backend fetcher tests |
| Create | `tinyagentos/routes/youtube.py` | `/api/youtube/*` endpoints |
| Create | `tests/test_routes_youtube.py` | Route-level tests |
| Modify | `tinyagentos/knowledge_ingest.py` | Wire youtube fetcher into _download() |
| Modify | `tinyagentos/app.py` | Include youtube router |
| Create | `desktop/src/lib/youtube.ts` | TypeScript types + fetch wrappers |
| Create | `desktop/tests/youtube.test.ts` | Frontend API helper tests |
| Create | `desktop/src/apps/YouTubeLibraryApp.tsx` | Main app component |
| Modify | `desktop/src/registry/app-registry.ts` | Register youtube-library entry |

---

## Task 1: YouTubeFetcher Backend + Tests

**Files:**
- Create: `tinyagentos/knowledge_fetchers/youtube.py`
- Create: `tests/test_knowledge_fetcher_youtube.py`

- [ ] **Step 1: Write failing tests**

Test `fetch()` with mocked yt-dlp subprocess output. Test VTT transcript parsing. Test `download_video()` with mocked subprocess.

- [ ] **Step 2: Implement YouTubeFetcher**

Key functions:
- `fetch(url, http_client)` — runs `yt-dlp --dump-json --no-download {url}`, parses metadata, downloads thumbnail, extracts captions (VTT → `[{start, end, text}]`), returns dict for KnowledgeItem
- `download_video(url, quality, output_dir)` — runs `yt-dlp -f "bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"`, returns local file path
- `parse_vtt(vtt_text)` — regex parser for VTT to timestamp segments
- All yt-dlp calls via `asyncio.create_subprocess_exec` (no import dependency)

- [ ] **Step 3: Wire into IngestPipeline**

Add `if source_type == "youtube"` branch in `knowledge_ingest.py._download()`.

- [ ] **Step 4: Run tests, commit**

```bash
git commit -m "feat(youtube): add YouTubeFetcher with yt-dlp subprocess and tests"
```

---

## Task 2: YouTube API Routes

**Files:**
- Create: `tinyagentos/routes/youtube.py`
- Create: `tests/test_routes_youtube.py`
- Modify: `tinyagentos/app.py`

Endpoints:
- `POST /api/youtube/ingest` — submit URL, delegates to /api/knowledge/ingest
- `POST /api/youtube/download` — trigger download with quality setting, background task
- `GET /api/youtube/download-status/{item_id}` — check progress
- `GET /api/youtube/transcript/{item_id}` — parsed transcript segments

- [ ] **Steps: Write tests, implement routes, wire into app.py, run tests, commit**

```bash
git commit -m "feat(youtube): add API routes for ingest, download, and transcript"
```

---

## Task 3: Frontend API Helpers + Tests

**Files:**
- Create: `desktop/src/lib/youtube.ts`
- Create: `desktop/tests/youtube.test.ts`

Types: `TranscriptSegment`, `Chapter`, `YouTubeMetadata`, `DownloadStatus`
Functions: `ingestVideo`, `downloadVideo`, `getDownloadStatus`, `getTranscript`

- [ ] **Steps: Write tests, implement, run tests, commit**

```bash
git commit -m "feat(youtube): add frontend API types and helpers with tests"
```

---

## Task 4: YouTubeLibraryApp Component + Registration

**Files:**
- Create: `desktop/src/apps/YouTubeLibraryApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

Two-state layout (list/detail) following Library App pattern.

**List view:** Sidebar (channels, downloaded, monitored, categories), item cards with thumbnails, search + sort.

**Detail view:**
- YouTube iframe embed (`enablejsapi=1`). If downloaded, HTML5 `<video>` from local storage.
- Summary card below player
- Expandable transcript panel: chapters as collapsible sections (when available), fall back to timestamped segments. Clickable timestamps seek player via `postMessage`. Currently-playing segment highlighted. Searchable.
- Tabs: Transcript, History, Metadata
- Action bar: Open on YouTube, Re-ingest, Download media (quality picker dropdown: 360p/720p/1080p/Best/Manual), Stop monitoring, Delete

Registration: `id: "youtube-library"`, `icon: "play-circle"`, `launchpadOrder: 14.5`

- [ ] **Steps: Create component, register, build check, test, commit**

```bash
git commit -m "feat(youtube): add YouTube Library app with full UI"
```

---

## Task 5: Manual Testing

- [ ] Ingest a YouTube URL, verify metadata + transcript captured
- [ ] Verify iframe player loads and plays
- [ ] Click transcript timestamps, verify player seeks
- [ ] Test chapter collapsing (if video has chapters)
- [ ] Test download with quality picker (if yt-dlp available)
- [ ] Test mobile layout
- [ ] Verify ARIA and keyboard nav
