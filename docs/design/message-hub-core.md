> **SUPERSEDED 2026-07-26 - the persistence model described here is changing.**
>
> This doc describes the controller's own `chat_messages` store as the canonical
> home for all chat. Jay has decided that **all chat moves onto the taOSmd A2A
> bus**, that taOSmd becomes a required dependency rather than a swappable memory
> framework, and that **existing chat history migrates** rather than staying in
> place. Conversations become first-class memory.
>
> The schema here remains accurate for what exists TODAY and for the migration
> source. Do not treat it as the long-term design.
>
> Current statement: jaylfc/taOS#2150.

# Message Hub Core

**Status:** Implemented - chat messages, channels, canvas store, and WebSocket hub all landed. See `tinyagentos/routes/chat_*.py` and `tinyagentos/chat_*.py` for the runtime.

## Overview

A built-in Discord/Slack-style messaging system where users communicate with their agents, collaborate on files, and view rich visual output. Real-time WebSocket-based with markdown formatting, code blocks, interactive components, file sharing, threads, reactions, and a canvas system powered by CanvasX for visual agent output.

This is Spec 1 of 3. Spec 2 covers the Rich UI (chat page, markdown rendering, emoji picker). Spec 3 covers Agent Integration (slash commands, external channel bridge, session attachment).

## Message Store

### Schema

```sql
CREATE TABLE chat_messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    thread_id TEXT,
    author_id TEXT NOT NULL,
    author_type TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'text',
    content_blocks TEXT DEFAULT '[]',
    embeds TEXT DEFAULT '[]',
    components TEXT DEFAULT '[]',
    attachments TEXT DEFAULT '[]',
    reactions TEXT DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'complete',
    edited_at REAL,
    pinned INTEGER DEFAULT 0,
    ephemeral INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_channel ON chat_messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_msg_thread ON chat_messages(thread_id);

CREATE TABLE chat_channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    members TEXT DEFAULT '[]',
    settings TEXT DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_message_at REAL
);
CREATE INDEX IF NOT EXISTS idx_chat_channel_type ON chat_channels(type);

CREATE TABLE chat_read_positions (
    user_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    last_read_message_id TEXT,
    last_read_at REAL NOT NULL,
    PRIMARY KEY (user_id, channel_id)
);

CREATE TABLE chat_pins (
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    pinned_by TEXT NOT NULL,
    pinned_at REAL NOT NULL,
    PRIMARY KEY (channel_id, message_id)
);

CREATE TABLE chat_attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    path TEXT NOT NULL,
    thumbnail_path TEXT,
    uploaded_by TEXT NOT NULL,
    uploaded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_attach_msg ON chat_attachments(message_id);
```

### Field Details

**author_type:** `user`, `agent`, `app-expert`, `system`

**content_type:** `text` (markdown), `canvas` (opens split view), `system` (join/leave/pin notifications)

**content_blocks:** Optional JSON array for structured multi-part messages from agent frameworks. Supports OpenClaw-compatible content block format:
```json
[
  {"type": "text", "text": "Here's the analysis:"},
  {"type": "image", "url": "/workspace/chart.png", "alt": "Revenue chart"},
  {"type": "code", "language": "python", "text": "df.groupby('month').sum()"}
]
```
When `content_blocks` is non-empty, clients render blocks. When empty, clients render the `content` field as markdown. This gives us compatibility with frameworks that produce structured output while keeping simple text messages simple.

**state:** `pending` (agent is processing), `streaming` (tokens arriving), `complete`, `error`. Allows the UI to show a typing/thinking indicator for the specific message being generated.

**embeds:** JSON array of rich embed cards:
```json
[{
  "title": "Build Results",
  "description": "All 877 tests passing",
  "color": "#2ecc71",
  "fields": [
    {"name": "Duration", "value": "3m 2s", "inline": true},
    {"name": "Coverage", "value": "84%", "inline": true}
  ],
  "thumbnail": "/workspace/badge.png",
  "footer": {"text": "CI Pipeline", "icon": "/static/ci-icon.png"}
}]
```

**components:** JSON array of action rows containing interactive elements:
```json
[
  [
    {"type": "button", "label": "Approve", "action": "approve_pr", "style": "primary"},
    {"type": "button", "label": "Request Changes", "action": "request_changes", "style": "danger"},
    {"type": "button", "label": "View Diff", "action": "open_url", "url": "/workspace/diff.html"}
  ],
  [
    {"type": "select", "placeholder": "Assign reviewer...", "action": "assign_reviewer",
     "options": [{"label": "Agent Alpha", "value": "alpha"}, {"label": "Agent Beta", "value": "beta"}]}
  ]
]
```

**reactions:** JSON object mapping emoji to list of user IDs:
```json
{":thumbsup:": ["user", "agent-alpha"], ":rocket:": ["user"]}
```

**metadata:** Freeform JSON for content-type-specific data:
- For `canvas` messages: `{"canvas_id": "abc123", "canvas_url": "/canvas/abc123"}`
- For agent messages: `{"model": "qwen3-4b", "tokens": 342, "tool_calls": [...]}`
- For system messages: `{"event": "member_joined", "member": "agent-beta"}`

### Channel Types

| Type | Description | Auto-created |
|---|---|---|
| `dm` | Direct message between user and one agent | When agent is deployed |
| `group` | Multi-party (user + multiple agents) | When user creates a group |
| `topic` | Named room ("#research", "#design") | User-created |
| `thread` | Sub-conversation under a message | When user replies to a message |
| `agent-session` | Linked to a streaming app session | When streaming app launches |

### Channel Settings

```json
{
  "notifications": "all" | "mentions" | "none",
  "slow_mode_seconds": 0,
  "read_only": false,
  "pinned_message_ids": []
}
```

## WebSocket Hub

### Connection

Client connects to `ws://host:6969/ws/chat`. Authentication via session cookie (same as the rest of the platform). One connection per browser tab, can join multiple channels.

### Protocol

All messages are JSON with a `type` field and a `seq` number for ordering.

**Client to server:**

| Type | Payload | Description |
|---|---|---|
| `join` | `{channel_id}` | Subscribe to channel events |
| `leave` | `{channel_id}` | Unsubscribe |
| `message` | `{channel_id, content, content_type?, thread_id?, attachments?, embeds?, components?}` | Send a message |
| `typing` | `{channel_id}` | Typing indicator (throttle client-side to 1 per 3s) |
| `reaction` | `{message_id, emoji, action: "add" \| "remove"}` | React to a message |
| `edit` | `{message_id, content}` | Edit own message |
| `delete` | `{message_id}` | Delete own message |
| `component_action` | `{message_id, action, value?}` | User clicked a button or selected an option |
| `mark_read` | `{channel_id, message_id}` | Update read position |

**Server to client:**

| Type | Payload | Description |
|---|---|---|
| `message` | Full message object | New message in a joined channel |
| `message_delta` | `{message_id, channel_id, delta, seq}` | Streaming token delta for an agent response |
| `message_state` | `{message_id, state}` | Message state changed (pending/streaming/complete/error) |
| `message_edit` | `{message_id, content, edited_at}` | Message was edited |
| `message_delete` | `{message_id, channel_id}` | Message was deleted |
| `typing` | `{channel_id, user_id, user_type}` | Someone is typing |
| `reaction_update` | `{message_id, reactions}` | Reactions changed |
| `presence` | `{user_id, status: "online" \| "offline" \| "idle"}` | Presence change |
| `channel_update` | `{channel_id, ...changed_fields}` | Channel settings changed |
| `canvas_open` | `{message_id, canvas_url}` | Agent requesting canvas view |
| `component_response` | `{message_id, action, result}` | Result of a component action |

### Hub Architecture

```python
class ChatHub:
    _channels: dict[str, set[WebSocket]]   # channel_id -> connected clients
    _presence: dict[str, dict]              # user_id -> {status, last_seen, channels}
    _typing: dict[str, dict[str, float]]   # channel_id -> {user_id: timestamp}
    _seq: int                               # global sequence counter

    async def broadcast(channel_id, event)   # send to all clients in a channel
    async def send_to(user_id, event)        # send to a specific user (ephemeral)
    async def handle_message(ws, data)       # route incoming events
```

Single-process, in-memory. For future multi-host scaling, the broadcast layer could be backed by Redis pub/sub, but that's out of scope.

### Agent Message Flow

Agents don't connect via WebSocket. They interact through HTTP:

1. User sends message in chat via WebSocket
2. ChatHub stores the message, broadcasts to channel
3. ChatHub creates a `pending` placeholder message for the agent's response
4. ChatHub calls the agent's framework adapter via HTTP (same Channel Hub pattern)
5. Framework adapter streams tokens back via SSE or chunked HTTP
6. ChatHub broadcasts `message_delta` events as tokens arrive
7. When complete, ChatHub updates message state to `complete` and broadcasts `message_state`

This keeps agent interaction HTTP-based (compatible with all 18 framework adapters) while users get real-time streaming.

## Canvas System (CanvasX)

### Architecture

CanvasX is vendored into TinyAgentOS as the canvas renderer. It runs as a local service (or embedded route) that:
1. Accepts content from agents via API
2. Renders it as interactive HTML with native components (charts, forms, tables, kanban, code blocks)
3. Serves it at `/canvas/{id}` in an iframe within the chat split view
4. Supports live updates via WebSocket so agents can modify the canvas in real time

### Flow

1. Agent decides to show visual content
2. Agent calls `POST /api/canvas/generate` with content + style hint
3. API returns `{canvas_id, canvas_url}`
4. Agent sends a chat message with `content_type: "canvas"` and the canvas URL in metadata
5. Chat UI receives the message, opens split view: canvas iframe on left, chat on right
6. Agent can update the canvas via `POST /api/canvas/{id}/update`
7. Canvas WebSocket pushes live updates to the iframe

### Canvas API

```
POST /api/canvas/generate     - create a new canvas
  Body: {content, style?, title?, agent_name?}
  Returns: {canvas_id, canvas_url, edit_token}

GET  /canvas/{id}             - serve the canvas page (iframe-friendly)
POST /api/canvas/{id}/update  - update canvas content (requires edit_token)
GET  /api/canvas/{id}/data    - get canvas data as JSON
DELETE /api/canvas/{id}       - delete a canvas
GET  /api/canvas              - list all canvases
```

### Canvas Content Types

Using CanvasX's native components:
- **Charts** (bar, line, pie, scatter)
- **Tables** (sortable, filterable)
- **Forms** (input fields, selects, checkboxes with submit actions)
- **Kanban boards** (columns with draggable cards)
- **Code blocks** (syntax highlighted, editable)
- **Cards/grids** (image + text layouts for presenting options)
- **Timelines** (event sequences)
- **Markdown** (rendered rich text)

The style hint (`auto`, `dashboard`, `report`, `tool`, `creative`, `data`, `list`) tells CanvasX how to layout the content.

### Interactive Canvas

When the user interacts with canvas elements (clicks a card, submits a form, drags a kanban item), the canvas sends the action back to the chat system via WebSocket. The agent receives it as a `component_action` event and can respond in chat or update the canvas.

## File Uploads

### Upload Endpoint

```
POST /api/chat/upload
  Multipart form: file + channel_id
  Returns: {attachment_id, filename, size, url, thumbnail_url?}
```

Files stored in `/data/chat-files/`. Thumbnails generated for images. The returned attachment object is included in the message's `attachments` array when the user sends the message.

### Supported Previews

- Images: inline preview with lightbox on click
- Video: inline player
- Audio: inline player
- PDF: first page thumbnail
- Code files: syntax-highlighted preview
- Other: file icon with download link

## Mention Parsing

Content is parsed for mentions before storage:

- `@agent-name` resolves to an agent mention (highlighted, triggers notification)
- `@everyone` notifies all members of the channel
- `@here` notifies online members only
- `#channel-name` becomes a clickable channel link

Mentions are stored as-is in the content field (no special encoding). The client renders them with highlighting. The server uses regex to extract mentions for notification routing.

## Read Position Tracking

The `chat_read_positions` table tracks each user's last-read message per channel. When the user opens a channel or sends `mark_read`, the position updates. The chat page shows:
- Bold channel names for channels with unread messages
- Unread count badge per channel
- "New messages" divider in the message list

## ChatMessageStore

`ChatMessageStore(BaseStore)` with methods:
- `send_message(channel_id, author_id, author_type, content, ...)` -> message dict
- `get_messages(channel_id, limit, before?, after?)` -> list[dict] (paginated)
- `get_message(message_id)` -> dict | None
- `edit_message(message_id, content)` -> None
- `delete_message(message_id)` -> bool
- `add_reaction(message_id, emoji, user_id)` -> None
- `remove_reaction(message_id, emoji, user_id)` -> None
- `update_state(message_id, state)` -> None
- `pin_message(channel_id, message_id, user_id)` -> None
- `unpin_message(channel_id, message_id)` -> None
- `search(query, channel_id?, author_id?, limit?)` -> list[dict]

## ChatChannelStore

Separate store or same DB with methods:
- `create_channel(name, type, members, created_by, ...)` -> channel dict
- `get_channel(channel_id)` -> dict | None
- `list_channels(user_id?)` -> list[dict]
- `update_channel(channel_id, ...)` -> None
- `delete_channel(channel_id)` -> bool
- `add_member(channel_id, member_id)` -> None
- `remove_member(channel_id, member_id)` -> None
- `update_read_position(user_id, channel_id, message_id)` -> None
- `get_unread_counts(user_id)` -> dict[channel_id, int]

## Routes

```
GET  /chat                              - main chat page
GET  /chat/{channel_id}                 - open specific channel
WS   /ws/chat                           - WebSocket hub
POST /api/chat/messages                 - send message (HTTP alternative to WS)
GET  /api/chat/channels                 - list channels
POST /api/chat/channels                 - create channel
GET  /api/chat/channels/{id}            - get channel details
PUT  /api/chat/channels/{id}            - update channel
DELETE /api/chat/channels/{id}          - delete channel
GET  /api/chat/channels/{id}/messages   - get messages (paginated)
POST /api/chat/upload                   - upload file attachment
GET  /api/chat/unread                   - get unread counts for all channels
POST /api/chat/channels/{id}/mark-read  - mark channel as read
GET  /api/chat/search                   - search messages
POST /api/canvas/generate               - create canvas
GET  /canvas/{id}                       - serve canvas page
POST /api/canvas/{id}/update            - update canvas
```

## Non-goals (Spec 1)

- Voice/video calls (future, possibly via WebRTC)
- Custom emoji upload (use standard Unicode emoji for now)
- Slash commands (Spec 3)
- External channel bridge (Spec 3)
- Workflow builder (future)
- E2E encryption (future, not needed for self-hosted single-user)
