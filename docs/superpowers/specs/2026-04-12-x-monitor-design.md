# X Monitor

## Overview

An X/Twitter monitoring and archiving app for taOS. Save threads, track engagement over time, and set up standing author watches that auto-capture new posts. Full thread reconstruction stitches reply chains into readable documents. Authentication via Agent Browser cookies (primary) with yt-dlp/gallery-dl fallback for one-off URL saves.

Build order: #5 in the Knowledge Capture Pipeline.

---

## Architecture

```
desktop/src/
├── apps/XMonitorApp.tsx            # Main app component
└── lib/x-monitor.ts                # Typed fetch wrappers for /api/x/*

tinyagentos/
├── knowledge_fetchers/x.py         # XFetcher: cookie-authed GraphQL + yt-dlp fallback
└── routes/x.py                     # /api/x/* endpoints
```

Registered in `app-registry.ts` as:
- `id: "x-monitor"`
- `name: "X"`
- `icon: "at-sign"`
- `category: "platform"`
- `launchpadOrder: 15.5`
- `singleton: true`
- `pinned: false`
- `defaultSize: { w: 1000, h: 650 }`
- `minSize: { w: 550, h: 400 }`

---

## Auth

### Primary: Agent Browser Cookies

User logs into X in an Agent Browser. Cookies exported automatically via `GET /api/agent-browsers/{profile}/cookies?domain=x.com`. The backend reads `auth_token` and `ct0` cookies to authenticate GraphQL requests.

Login status indicator in the sidebar: green dot when valid cookies detected, red when expired or missing. "Log in via Agent Browser" link opens the Agent Browsers app.

### Fallback: yt-dlp / gallery-dl

For one-off saves when no browser session is available. User pastes a tweet URL, backend uses `yt-dlp` or `gallery-dl` to extract the tweet content, media, and metadata. No auth needed for public tweets. Cannot access timelines, bookmarks, or search.

---

## Layout

Same two-state pattern: `list` (default) and `detail`.

### List View

Sidebar filters:
- Watched Authors (standing rules, each showing post count and last check time)
- Saved Threads
- Bookmarks (from X, cookie auth required)
- Monitored (engagement tracking active)
- Categories
- Status

Item cards: Author avatar placeholder + @handle, tweet text (2-line clamp), engagement stats (likes, reposts, replies, views), relative time, media indicator (image/video badge), category pills, monitoring badge.

### Detail View

Full replacement with back button.

**Thread content:** Full reconstructed thread — all tweets by the original author stitched in order. Quote tweets shown inline with border. Media (images) shown inline. Video links shown as clickable URLs (playback via YouTube Library if it's a video platform link).

**Reply tree:** Other users' replies below the thread, collapsible threaded view (same pattern as Reddit Client). Each reply: @handle, text, likes, time. Capped at 3 levels visible.

**Engagement stats bar:** Likes, reposts, quotes, replies, views — with trend indicators if monitoring is active (e.g. "↑ 142 → 387 likes since saved").

**Tabs:** Thread (default), History (engagement snapshots over time, graph of likes/reposts/views), Metadata (raw tweet data, author info, media URLs).

**Action bar:** Open on X, Re-fetch, Monitor engagement, Stop monitoring, Delete.

**"Shared with" line:** Contextual agent subscriptions, same as Library App.

---

## Author Watching

### Standing Rules

"Watch @handle" creates a persistent monitoring rule:

```
AuthorWatch:
  handle: string           # @handle (without @)
  filters:
    all_posts: boolean     # capture everything
    min_likes: number      # only posts above this engagement threshold
    threads_only: boolean  # only multi-tweet threads, not single tweets
    media_only: boolean    # only posts with images/video
  frequency: number        # poll interval in seconds (default: 1800 = 30 min)
  enabled: boolean
  last_check: number       # timestamp
  created_at: number
```

Each check fetches the author's recent timeline via cookie-authed GraphQL. New posts matching filters are auto-ingested into the Knowledge Base via `/api/knowledge/ingest`.

Author watches appear as scheduled tasks in the Routines app (#193). Users can pause, resume, change frequency, or delete from either the X Monitor app or the Routines app.

### Configuration UI

In the sidebar under "Watched Authors": click "+ Watch Author" to add a new watch. Form: @handle input, filter toggles, frequency picker. Edit existing watches by clicking them.

---

## Manual Saves

"Save this tweet/thread" via URL input in the app header. Workflow:

1. User pastes X URL (tweet, thread, or author profile)
2. If cookie auth available: fetch via GraphQL (full metadata + engagement)
3. If no auth: fall back to yt-dlp/gallery-dl (tweet content + media, limited metadata)
4. Reconstruct full thread if it's a reply
5. Ingest into Knowledge Base

---

## Thread Reconstruction

When saving a tweet that is part of a thread:

1. Walk the reply chain **up** to find the root tweet (follow `in_reply_to_status_id`)
2. Walk **down** through the original author's replies to collect the full thread
3. Stitch all tweets by the same author into one document, in chronological order
4. Other users' replies stored separately as metadata (like Reddit comments)
5. The full thread becomes the `content` field of the KnowledgeItem
6. Individual tweet metadata (likes, reposts per tweet) stored in `metadata`

---

## Monitoring

### Engagement Tracking

Saved threads/tweets are monitored for engagement changes:
- Likes, reposts, quote tweets, reply count, view count
- Each poll creates a snapshot with current values
- History tab shows engagement over time
- Deleted tweets detected and marked (last-known text preserved from previous snapshot)

### Source Defaults

| Config | Value |
|--------|-------|
| Base frequency | 30 minutes |
| Decay multiplier | 2.0x |
| Floor | 30 days (from MonitorService) |
| Pin override | Available |

### Deleted Content

When a monitored tweet is deleted:
- The KnowledgeItem is preserved with all content from the last successful fetch
- Status updated to "deleted_upstream"
- Monitoring stops for that item
- The deletion event is recorded in the snapshot history

---

## Backend: XFetcher

File: `tinyagentos/knowledge_fetchers/x.py`

### Cookie-Authed Path

Uses X's GraphQL API with `auth_token` and `ct0` cookies:
- `fetch_tweet(tweet_id, cookies, http_client) -> dict` — single tweet with full metadata
- `fetch_thread(tweet_id, cookies, http_client) -> list[dict]` — reconstructed thread (walk up/down)
- `fetch_author_timeline(handle, cookies, http_client, count=20) -> list[dict]` — recent posts
- `fetch_bookmarks(cookies, http_client) -> list[dict]` — user's bookmarks

Headers: `Cookie: auth_token={token}; ct0={ct0}`, `x-csrf-token: {ct0}`, `authorization: Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=...` (public bearer token).

### yt-dlp Fallback Path

- `fetch_tweet_ytdlp(url) -> dict` — `yt-dlp --dump-json --no-download {url}`, parse JSON for tweet text, author, media URLs, engagement stats

### IngestPipeline Wire-In

```python
if source_type == "x":
    from tinyagentos.knowledge_fetchers.x import fetch_thread, fetch_tweet_ytdlp
    cookies = await self._get_x_cookies()  # from Agent Browsers API
    if cookies:
        tweets = await fetch_thread(source_id, cookies, self._http_client)
    else:
        tweets = [await fetch_tweet_ytdlp(url)]
    content = "\n\n".join(t["text"] for t in tweets)
    return content, tweets[0]["author"], tweets[0]["author"], metadata
```

---

## Backend: X API Routes

File: `tinyagentos/routes/x.py`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/x/tweet/{tweet_id}` | Fetch single tweet |
| GET | `/api/x/thread/{tweet_id}` | Fetch reconstructed thread |
| GET | `/api/x/author/{handle}` | Fetch author's recent timeline |
| GET | `/api/x/bookmarks` | Fetch user's bookmarks (cookie auth required) |
| GET | `/api/x/auth/status` | Cookie auth status (valid/expired/missing) |
| POST | `/api/x/watch` | Create author watch rule |
| GET | `/api/x/watches` | List all author watch rules |
| PUT | `/api/x/watch/{handle}` | Update watch rule (filters, frequency, enabled) |
| DELETE | `/api/x/watch/{handle}` | Delete watch rule |

---

## Frontend: lib/x-monitor.ts

Types:
```typescript
Tweet { id: string; author: string; handle: string; text: string; likes: number; reposts: number; quotes: number; replies: number; views: number; created_at: number; media: { type: string; url: string }[]; is_thread: boolean }
XThread { tweets: Tweet[]; reply_tree: Tweet[] }
AuthorWatch { handle: string; filters: { all_posts: boolean; min_likes: number; threads_only: boolean; media_only: boolean }; frequency: number; enabled: boolean; last_check: number; created_at: number }
XAuthStatus { authenticated: boolean; handle?: string; expires?: number }
```

Functions:
```typescript
fetchTweet(tweetId: string): Promise<Tweet | null>
fetchThread(tweetId: string): Promise<XThread | null>
fetchAuthorTimeline(handle: string): Promise<Tweet[]>
fetchBookmarks(): Promise<Tweet[]>
getAuthStatus(): Promise<XAuthStatus>
listWatches(): Promise<AuthorWatch[]>
createWatch(handle: string, filters: AuthorWatch["filters"], frequency?: number): Promise<boolean>
updateWatch(handle: string, updates: Partial<AuthorWatch>): Promise<boolean>
deleteWatch(handle: string): Promise<boolean>
saveToLibrary(url: string): Promise<{ id: string; status: string } | null>
```

---

## Browsing History

Lightweight history of tweets/threads viewed in the app, even if not saved to the Knowledge Base. Enables quick recovery of content you browsed but didn't save.

**Storage:** SQLite table `x_history` in `data/x-history.db`:
```
x_history:
  url: text PRIMARY KEY
  tweet_id: text
  author: text
  handle: text
  text_preview: text (first 200 chars)
  likes: integer
  viewed_at: real
```

NOT a KnowledgeItem — no ingest, no embedding. Just a breadcrumb.

**Sidebar:** "History" section showing recently viewed tweets/threads, newest first. Click to re-open. "Save to Library" button to promote to a full KnowledgeItem.

**Retention:** Default 30 days. "Clear history" button.

**Recording:** Every time a tweet/thread is opened in the detail view, upsert into history table.

---

## Dependencies

- Agent Browsers app (#176) — required for cookie auth
- yt-dlp — fallback for one-off saves (already required by YouTube Library)

---

## Non-Goals

- Posting tweets or replying (read-only)
- DM access
- Spaces / audio content
- Full timeline browsing (not a Twitter client — focused on monitoring and archiving)
- Ads or promoted content
