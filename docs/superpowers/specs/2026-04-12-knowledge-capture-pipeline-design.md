# Knowledge Capture Pipeline

## Overview

A personal knowledge capture system that lets users save content from any platform (Reddit, YouTube, X, GitHub, articles, files) into a local knowledge base that AI agents can search, cite, and learn from. Content is downloaded, transcribed, summarised, embedded, auto-categorised, and monitored for changes over time.

The system follows Approach 2: Platform Apps + Shared Knowledge Base. Each platform gets its own focused app (Reddit Client, YouTube Library, X Monitor, GitHub Browser) writing to a shared Knowledge Base Service. A unified Library app provides cross-platform browsing and management. Mobile share sheet apps (iOS/Android) and a browser extension provide ingest from any device.

---

## Architecture

```
[Reddit Client] [YouTube Library] [X Monitor] [GitHub Browser] [Share Sheet] [Browser Extension]
      \              |                |            |                |              /
       └─────────────────────────────────────────────────────────────────────────┘
                                     │
                           Knowledge Base Service
                           ├── IngestPipeline (download, transcript, summarise, embed)
                           ├── MonitorService (poll, diff, smart decay)
                           ├── CategoryEngine (rules + LLM fallback)
                           ├── KnowledgeStore (SQLite + QMD vectors)
                           └── AccessControl (personal library, agent subscriptions)
                                     │
                           ┌─────────┴──────────┐
                       [Library App]      [Agent Memory API]
                       (unified view)     (search, cite, notify)
```

---

## Data Model

### KnowledgeItem

Every piece of captured content becomes a KnowledgeItem stored in `data/knowledge.db`:

```
KnowledgeItem:
  id:           text (uuid)
  source_type:  text (reddit, youtube, x, github, article, file, manual)
  source_url:   text (original URL)
  source_id:    text (platform-specific ID, e.g. reddit thread ID, video ID)
  title:        text
  author:       text (u/username, @handle, channel name, GitHub user)
  summary:      text (LLM-generated, 2-3 sentences)
  content:      text (full text / transcript / markdown)
  media_path:   text (local path to downloaded media, nullable)
  thumbnail:    text (local path to thumbnail, nullable)
  categories:   json (list of category strings)
  tags:         json (list of user + auto tags)
  metadata:     json (platform-specific: subreddit, upvotes, views, etc.)
  status:       text (pending, processing, ready, error)
  monitor:      json (poll config: frequency, decay_rate, last_poll, pinned)
  created_at:   real
  updated_at:   real
```

FTS5 index on `title + content + summary + author` for keyword search. Vector embeddings via QMD in a `knowledge` collection for semantic search.

Media files stored in `data/knowledge-media/` with paths referenced by `media_path` and `thumbnail`.

### Monitoring Snapshots

```
knowledge_snapshots:
  id:            integer
  item_id:       text (FK to KnowledgeItem)
  snapshot_at:   real
  content_hash:  text (SHA256 of content at this point)
  diff_json:     text (what changed since last snapshot)
  metadata_json: text (votes, views, comment count at this point)
```

Each poll creates a snapshot. The Library app diff view compares snapshots over time. Deleted content (e.g. removed Reddit comments) is preserved with the last-known text from the previous snapshot.

### Category Rules

```
category_rules:
  id:         integer
  pattern:    text (match expression)
  match_on:   text (source_url, source_type, author, title, subreddit, channel)
  category:   text (target category)
  priority:   integer (higher = checked first)
```

### Agent Subscriptions

```
agent_knowledge_subscriptions:
  agent_name:  text
  category:    text
  auto_ingest: boolean (true = embed into agent memory, false = notify only)
```

---

## Ingest Pipeline

When a URL or content arrives from any source it goes through a standard async pipeline.

**Step 1: Resolve** -- identify `source_type` from URL pattern. `reddit.com/r/` = reddit, `youtube.com/watch` = youtube, `x.com/` or `twitter.com/` = x, `github.com/` = github, everything else = article.

**Step 2: Download** -- platform-specific fetcher:

- **Reddit**: fetch `.json` URL, extract post + all comments + metadata (upvotes, author, timestamps)
- **YouTube**: `yt-dlp` for metadata + thumbnail, transcript via YouTube captions or Whisper. Video download opt-in (off by default)
- **X**: fetch via cookie-authed API or browser container cookies
- **GitHub**: `gh` CLI or API -- repo README, issue body + comments, PR diff + discussion, starred repo metadata
- **Article**: readability extraction (strip nav/ads, keep content), download images locally
- **File**: copy to `knowledge-media/`, extract text (PDF, markdown, plaintext)

**Step 2b: Screenshot fallback** -- if readability extraction returns empty or garbage (below quality threshold of 100 chars), fall back to full-page screenshot via browser container. Playwright `page.screenshot({ fullPage: true })` captures the entire scrollable page as one tall image. The screenshot is:

- Saved as WebP to `knowledge-media/`
- OCR'd for searchable text
- Thumbnail generated from the top viewport
- OCR text becomes the `content` field for search and embedding

Extraction hierarchy: API/structured data (best) > readability (good) > full-page screenshot + OCR (last resort).

**Step 3: Summarise** -- LLM generates a 2-3 sentence summary plus a "useful for" descriptor. Category tags applied (rules first, LLM fallback).

**Step 4: Embed** -- content sent to QMD for vector embedding into the `knowledge` collection. Chunked if content exceeds embedding model context window.

**Step 5: Store** -- KnowledgeItem written to SQLite, media to disk, status set to `ready`.

**Step 6: Notify** -- agents subscribed to matching categories get a channel notification. If `auto_ingest=true`, content is also embedded into the agent's per-agent QMD index.

The pipeline is async -- the API returns immediately with `status: pending` and the pipeline runs in the background.

---

## Monitor Service

Background service running inside the controller that keeps saved content alive.

**Poll loop**: every 60 seconds, checks which items are due for a poll based on `monitor.frequency` and `monitor.last_poll`.

**Per-source polling:**

- **Reddit**: re-fetch `.json` URL. Diff comments (new, edited, deleted). Track vote counts. Deleted comments stored with `[deleted]` marker but original text preserved from previous snapshot.
- **X**: re-fetch thread. Track likes, reposts, new replies, quote tweets.
- **GitHub**: re-fetch issue/PR via API. Track new comments, status changes, label changes.
- **YouTube**: re-fetch metadata. Track view count, like count, new comments.
- **Articles**: re-fetch and diff. Track edits (useful for stealth-edited news articles).

**Smart decay:**

After each poll:

- Changes detected: reset decay to 1.0 (item is active, keep polling at base frequency)
- No changes: multiply current interval by the decay multiplier
- Floor: never slower than once per 24 hours
- Ceiling: stop polling after the idle threshold
- User can pin any item to override decay ("always poll this hourly")

**Source defaults:**

| Source  | Initial frequency | Decay multiplier | Stop after idle |
|---------|-------------------|------------------|-----------------|
| Reddit  | 60 min            | 1.5x             | 30 days         |
| X       | 30 min            | 2.0x             | 14 days         |
| GitHub  | 6 hours           | 1.5x             | 60 days         |
| YouTube | 24 hours          | 2.0x             | 30 days         |
| Article | 24 hours          | 2.0x             | 14 days         |

---

## Category Engine

**Rule-based layer (runs first, free):**

User-editable rules match content to categories based on source URL, source type, author, title, subreddit, or channel. Patterns use glob matching (`*` wildcard). Rules checked in priority order; first match wins. Multiple rules can assign multiple categories to the same item. System seeds defaults based on existing agents on first run.

Example rules:

- `match_on=subreddit, pattern=LocalLLaMA` -> category `AI/ML`
- `match_on=author, pattern=@alexziskind` -> category `AI/ML`
- `match_on=source_url, pattern=github.com/rockchip*` -> category `Rockchip`
- `match_on=source_type, pattern=github` -> category `Development`

**LLM fallback (only for unmatched items):**

Single LLM call with title + summary + source, asking for 1-3 categories from the user's existing taxonomy. Can propose new categories (user confirms).

---

## Authentication

Three-tier auth hierarchy, cheapest first:

1. **API token / OAuth** (cheapest) -- GitHub, Reddit, Discord, Slack. Tokens stored in existing SecretsStore (encrypted).
2. **Cookie import from browser container** (medium) -- X, YouTube, sites without clean APIs. TAOS reads cookies from the persistent Chromium profile in the browser container.
3. **Full browser automation via agent** (most expensive) -- only when an agent needs to interact with a site. Requires running container + LLM.

---

## Platform Apps

### Reddit Client

- OAuth login (SecretsStore), cookie fallback from browser container
- Browse feed, saved posts, specific subreddits
- "Save to Library" button on any thread triggers ingest
- Inline comment view with upvote counts
- Monitoring badge on saved threads with last poll status
- Diff view: timeline of changes (new comments green, deleted red strikethrough, vote graph)

### YouTube Library

- OAuth for subscriptions, watch history, liked videos
- "Add to Knowledge Base" button triggers ingest (metadata, thumbnail, transcript)
- Embedded video player (streams from YouTube, not downloaded by default)
- "Download video" toggle per-item (opt-in, stored in `knowledge-media/`)
- Transcript view alongside player with timestamps
- Summary card with LLM-generated "useful for" descriptor

### X Monitor

- Cookie auth from browser container (X lacks clean OAuth for this use case)
- View bookmarks, liked posts
- Save threads with full reply chain
- Monitor saved threads for new replies, quote tweets, engagement
- Author tracking: "watch everything from @handle" as a standing rule

### GitHub Browser

- Auth via `gh` CLI token or OAuth
- Browse starred repos, recent activity, notifications
- Save repos (README, releases, top issues), issues/PRs (full discussion)
- Monitor for updates (new comments, status changes, releases)
- "Share with agent" quick-assign

### Library App (unified view)

- All saved content across all platforms
- Filter by source type, category, author, date, status
- Keyword search (FTS5) and semantic search (QMD vectors)
- Category management: create, rename, merge, set rules
- Agent subscription management
- Monitoring dashboard: what is being polled, recent changes, cold items
- Diff viewer for any monitored item

### Agent Browsers (container manager)

- Create/destroy persistent Chromium browser containers
- Assign containers to agents (each agent can have its own browser profile)
- View running containers with VNC preview thumbnails
- Login status per site (green dot = authenticated)
- Cookie export for platform auth fallback

---

## Mobile Share Sheet

### iOS

- Native Swift share extension
- Receives from any app: URLs, text, images, files
- Optional quick-tag popup (pick category or just send)
- App Group shared SQLite queue between extension and main app
- Background URLSession for reliable upload

### Android

- Kotlin intent filter for `ACTION_SEND` / `ACTION_SEND_MULTIPLE`
- WorkManager for background queue drain
- Same settings and transport pattern as iOS

### Transport (resilient delivery)

1. Try direct to TAOS controller (Tailscale IP or LAN IP)
2. If unreachable, try cloud relay via tinyagentos.com
3. If relay unreachable, save to local queue (SQLite on device)
4. Background sync drains queue when connectivity returns

### Browser Extension

- Chrome/Firefox toolbar button "Save to TAOS"
- Right-click context menu: save page, save selection, save image
- Same `/api/knowledge/ingest` endpoint and transport as mobile

### Ingest Endpoint

`POST /api/knowledge/ingest`:

```json
{
  "url": "https://...",
  "title": "optional override",
  "text": "selected text if any",
  "categories": ["optional", "pre-tags"],
  "source": "share-sheet-ios"
}
```

---

## Access Control and Agent Knowledge Flow

Three-layer model: personal library + category subscriptions + search fallback.

**Layer 1: Personal Library**

All captured content lives in `data/knowledge.db`. The user sees everything in the Library app. Agents do not see content by default.

**Layer 2: Category Subscriptions**

Configured per-agent: "research-agent subscribes to AI/ML and Rockchip". When a new item lands in a subscribed category:

- `auto_ingest=true`: content embedded into agent's per-agent QMD index + channel notification
- `auto_ingest=false`: notification only, agent pulls on demand

**Layer 3: Search Fallback**

Any agent can search the full knowledge base via `/api/knowledge/search` regardless of subscriptions. Returns results ranked by relevance. Subscriptions control proactive delivery, not access.

**Manual overrides:**

- Library app: share item with specific agent (one-off push)
- Library app: share category with all agents
- Agents app: "give this agent access to everything" toggle

**Agent context integration:**

When an agent processes a user message, the knowledge base is searched automatically alongside existing memory. Results appear as:

```
[Knowledge Base] "TurboQuant: 768K context on RTX 3060" (YouTube, Alex Ziskind, saved recently)
Summary: Demonstrates asymmetric KV cache quantization achieving 6x context extension...
```

Agents can cite saved content: "Based on the video you saved about TurboQuant benchmarks..."

---

## Build Order

1. Knowledge Base Service (KnowledgeStore, IngestPipeline, API routes)
2. Library App (unified view, search, category management)
3. Reddit Client (OAuth, browse, save, monitor, diff view)
4. YouTube Library (ingest, transcript, player, summary)
5. X Monitor (cookie auth, thread save, engagement tracking)
6. GitHub Browser (starred repos, issues, PRs, releases)
7. Agent Browsers (container manager, cookie export, VNC preview)
8. Mobile Share Sheet iOS (share extension, queue, transport)
9. Mobile Share Sheet Android (intent filter, WorkManager)
10. Browser Extension (Chrome/Firefox, context menu)

Each step is independently shippable. The Knowledge Base Service and Library App form the foundation; everything else is an ingest adapter with a platform-native UI.

---

## Integration with Existing Systems

- **UserMemoryStore**: knowledge base is a parallel system, not a replacement. User memory handles conversation snippets and notes. Knowledge base handles external content.
- **QMD**: vector embeddings for knowledge items use the same QMD service, in a `knowledge` collection separate from agent memory.
- **SharedFolderManager**: downloaded media in `knowledge-media/` can be exposed to agents via shared folder mounts.
- **Channel system**: notifications about new knowledge items delivered via existing channel infrastructure.
- **SecretsStore**: OAuth tokens and API keys for platform auth stored in existing encrypted secrets DB.
- **App catalog**: each platform app registered as a catalog entry, installable independently.
- **Desktop shell**: apps registered in `app-registry.ts` with standard window interface.

---

## Non-Goals

- Not a social media client for posting or interacting (read-only capture)
- Not a feed reader or RSS aggregator (saves specific items, not streams)
- Not a web archiver (captures content for agent use, not archival fidelity)
- Video transcoding or format conversion (videos stored as-is or not at all)
