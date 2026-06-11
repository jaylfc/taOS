# User Memory — taosmd-backed Personal Memory

**Date:** 2026-04-10
**Status:** Draft

## Overview

User memory gives every user their own personal context store — separate from agent memories. It captures conversation history, notes, file activity, search history, and clipboard snippets. Searchable via keyword (BM25) with a local SQLite FTS5 fallback. Agents can query user memory (with permission) to understand what the user has been working on.

Like Pieces App but self-hosted, offline-first, and agent-aware.

## Architecture

```
Host
├── TinyAgentOS (port 6969)
│   └── user_memory routes → taosmd proxy (:7900), namespace "user-memory"
│       └── SQLite FTS5 store (local fallback when taosmd is unreachable)
├── taosmd (port 7900) → shared memory backend
├── LXC: agent-alpha
│   └── QMD (port 7832) → agent's per-agent memory
└── LXC: agent-beta
    └── QMD (port 7832) → agent's per-agent memory
```

User memory rides the taosmd proxy on port 7900 under the agent namespace `user-memory`. Writes go to taosmd `POST /ingest` (and `POST /ingest/batch` for bulk migration); keyword reads go to `GET /search?mode=bm25`. A local SQLite FTS5 store acts as an automatic fallback if taosmd is unreachable. The taosmd base URL is read from `app.state.taosmd_url` at runtime; set the `TAOS_USER_MEMORY_URL` environment variable to override the default `http://localhost:7900`.

## What Gets Captured

### Automatic (opt-in per category in Settings)
- **Conversations** — messages sent/received in the Message Hub, with agent name and timestamp. Chunked per conversation turn.
- **File activity** — metadata when files are opened, edited, uploaded, shared. File name, path, type, which agent/app was involved. NOT file contents (privacy).
- **Search queries** — what the user searched for in global search, memory browser, file search. Helps surface "things you were looking for before."
- **Agent interactions** — summaries of what each agent did. "Steve analysed the Q3 report and found revenue up 15%."
- **Notes** — content from the Text Editor app, auto-indexed on save.

### Manual
- **Clipboard/snippets** — user can explicitly save snippets to memory via right-click "Save to Memory" or a keyboard shortcut.
- **Bookmarks** — save URLs, files, or search results for later.
- **Annotations** — add notes to any memory chunk.

## Collections

User memory is organised into collections (same as agent QMD):
- `conversations` — chat messages
- `notes` — text editor content
- `files` — file activity metadata
- `searches` — search history
- `snippets` — user-saved clipboard items
- `agent-activity` — agent interaction summaries

## Access Control

- **User → User Memory**: always allowed (it's your data)
- **Agent → User Memory**: configurable per agent in Agent Settings
  - "Can read user memory" toggle (default: off)
  - When enabled, the agent is permitted to call `GET /api/user-memory/agent-search`
  - Agents search user memory through the TinyAgentOS API; they do not talk to taosmd directly
  - Agents CANNOT write to user memory (read-only access)

## Integration Points

### Global Search (Ctrl+Space)
Search results now include user memory chunks alongside apps and files. Results show source collection and preview.

### Memory App
Add a "My Memory" tab alongside agent memories. Same search modes (keyword/semantic/hybrid). Browse by collection. Delete individual chunks.

### Message Hub
After each conversation, the user's side of the conversation is auto-indexed into the `conversations` collection. This happens asynchronously — no impact on chat performance.

### Text Editor
On save, the note content is upserted into the `notes` collection. The note title becomes the chunk title.

### Agent Context
When an agent has user memory access, it can query the user's QMD to:
- Find relevant context for the current conversation
- Remember what the user asked about previously
- Access the user's notes and saved snippets

### Desktop Context Menu
Right-click anywhere → "Save to Memory" option. Saves selected text, current URL, or file reference to the `snippets` collection.

## User Memory Store

### Schema (extends platform DB)

```sql
CREATE TABLE IF NOT EXISTS user_memory_settings (
    user_id TEXT PRIMARY KEY,
    capture_conversations INTEGER DEFAULT 1,
    capture_files INTEGER DEFAULT 1,
    capture_searches INTEGER DEFAULT 0,
    capture_notes INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);
```

### UserMemoryStore Methods

```python
class UserMemoryStore(BaseStore):
    async def get_settings(user_id: str) -> dict
    async def update_settings(user_id: str, updates: dict) -> None
    async def search(user_id: str, query: str, collection: str = None, mode: str = "hybrid") -> list[dict]
    async def save_snippet(user_id: str, content: str, title: str = "", collection: str = "snippets") -> None
    async def delete_chunk(user_id: str, chunk_hash: str) -> bool
    async def get_stats(user_id: str) -> dict  # total chunks, per-collection counts
```

## Routes

```
GET  /api/user-memory/settings           — get capture settings
PUT  /api/user-memory/settings           — update capture settings
GET  /api/user-memory/search?q=...       — search user memory
GET  /api/user-memory/browse?collection=...&limit=... — browse chunks
POST /api/user-memory/save               — manually save a snippet
DELETE /api/user-memory/chunk/{hash}      — delete a chunk
GET  /api/user-memory/stats              — collection stats
GET  /api/user-memory/collections        — list collections
```

## Capture Pipeline

```
User Action (send message, save file, search, save note)
  → Event emitted to capture pipeline
  → Pipeline checks user settings (is this category enabled?)
  → If enabled: chunk the content, POST to user QMD for embedding
  → QMD stores in local SQLite with FTS5 + vector index
  → Available for search immediately
```

The pipeline is async — never blocks the user action. Writes reach taosmd via:
```
POST http://localhost:7900/ingest
Body: {text, agent: "user-memory", metadata: {collection, title, source_id}}
```
The local SQLite FTS5 store is always written first; taosmd ingest is best-effort and non-fatal.

## Privacy

- All data stays on device (QMD is local SQLite)
- User controls what gets captured (per-category toggles)
- User can delete any memory chunk
- Agent access is opt-in per agent
- No data leaves the network
- Backup/restore includes user memory

## Non-Goals (This Spec)

- Multi-user memory isolation (future — needs the multi-user system first)
- Cross-device sync (future — needs cloud features)
- Automatic summarisation of memory (future — needs local LLM)
- Memory retention policies (auto-delete after N days)
- Memory sharing between users

## Relationship to per-agent trace capture

User memory (this doc) and per-agent trace capture are distinct layers that
the taOSmd librarian bridges.

**User memory** is the user's own long-lived context — notes, clipboard
snippets, file activity, search history. It is owned by the user, scoped to
the user, and persists indefinitely unless the user deletes it.

**Per-agent trace capture** records every LLM call, message boundary, tool
invocation, and lifecycle event for a specific agent. Traces live in the
agent's home folder at `{data_dir}/agent-home/{slug}/.taos/trace/` and
travel with the agent on archive, restore, and backup. See
`docs/design/framework-agnostic-runtime.md` ("Per-agent trace capture") for
the path layout, envelope schema, and zero-loss contract.

**The librarian (taOSmd) bridges both.** Trace events feed the librarian's
zero-loss layer — every LLM call, message boundary, and (when available)
tool call and reasoning block is captured before the librarian decides what
to summarise vs. store verbatim. The librarian reads traces via
`GET /api/agents/{name}/trace` or direct SQLite and may cross-reference user
memory when the agent has `can_read_user_memory` permission. The librarian
summarises and annotates but does not delete raw trace envelopes.

The two layers intentionally do not share a schema. User memory entries
represent facts the user wants to keep; trace envelopes represent events that
happened during an agent run. An agent memory summary (stored in QMD) may
derive from traces, but the trace store and the QMD index are separate
stores on separate paths.
