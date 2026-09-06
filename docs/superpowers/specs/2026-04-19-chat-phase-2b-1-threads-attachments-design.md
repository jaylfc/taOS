# Chat Phase 2b-1 — Threads + Attachments + Shared File Picker + Chat Guide

**Date:** 2026-04-19
**Scope:** The two load-bearing Phase 2b features (threads, attachments), the `SharedFilePickerDialog` shell primitive that the attachment flow consumes, and the first version of `docs/chat-guide.md` with an in-app `/help` surface.

## Goal

One sentence: make the chat feel finished for real multi-user + multi-agent use — threaded replies so side-conversations don't flood the channel; attachments with a reusable file picker that works across disk, user workspace, and agent workspaces; and a canonical guide so people can actually use the mechanics we've shipped.

## Non-goals (explicit)

- **Pinning, read receipts, ephemeral messages, per-message affordances (edit-own, delete-own, copy link, mark-unread)** — Phase 2b-2.
- **Cross-app drag-drop between taOS app windows** — task #60, parallel shell primitive; chat attachments work standalone and inherit cross-app drop for free when that lands.
- **Multimodal bridge work** (hermes vision, openclaw vision, etc.) — per-framework upgrade tasks, post-2b-1. This phase just threads attachment metadata + URL to the bridge; bridges do what they can with it.
- **Mobile variants** — separate Mobile Chat Polish phase.
- **In-app help panel** (full help UI inside the app) — Phase 2b-2; we ship an external-link "?" + `/help` commands for now.
- **Recent files / shared folders / cloud sources** in the file picker — Phase 2b-2+ polish.

## Architecture

```
─────────────── message posted ────────────────
                    │
                    ▼
        routes/chat.py POST /api/chat/messages
         │
         ├─ starts with /help[...]?  →  chat/help.py   ──▶ system message reply
         │                                                (bypass bare-slash guard)
         │
         ├─ bare-slash guard (unchanged — Phase 2a)
         │
         ├─ has attachments[]?  →  store in messages.attachments jsonb
         │                         (validate count ≤ 10, ACL each from-path entry)
         │
         ├─ message persisted
         │
         └─ AgentChatRouter.dispatch
              │
              ├─ thread_id set?  →  threads.resolve_recipients(...)
              │                     (narrow scope + @all escalation + thread policy)
              │
              └─ no thread_id    →  existing channel fanout (Phase 1/2a, unchanged)

─────────────── upload flows ──────────────────
  composer paperclip / drag-drop / paste   ─┐
                                             ├▶ POST /api/chat/upload (disk bytes)
  SharedFilePickerDialog "Disk" tab        ─┘
                                             
  SharedFilePickerDialog "My workspace"      ─┐
  SharedFilePickerDialog "Agent workspace"  ─┴▶ POST /api/chat/attachments/from-path
                                                  (server-side copy, ACL check)
```

## Data Model

### `chat_messages.attachments` (new column)

```sql
ALTER TABLE chat_messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]';
```

Stores a JSON array:

```json
[
  {
    "filename": "screenshot.png",
    "mime_type": "image/png",
    "size": 312456,
    "url": "/api/chat/files/<stored_name>",
    "source": "disk" | "workspace" | "agent-workspace"
  },
  ...
]
```

Empty array for messages with no attachments (existing rows default to `'[]'` via the migration — no backfill work).

### `chat_messages.thread_id` (already in schema)

Already set up in Phase 1 (nullable `TEXT` column + `idx_chat_messages_thread`). No schema change; just start using it.

### File picker — no persistence

The `SharedFilePickerDialog` is client-side only; its "Recent files" list (if added in 2b-2) would persist per-user via `desktop_settings`, but 2b-1 has no persistence.

## Threads

### Right-side panel

- Mount point: same slot as `ChannelSettingsPanel` (right-hand edge, 360 px). Mutex: opening threads closes the settings panel and vice versa.
- State: `openThread: { channelId, parentId } | null` on `MessagesApp`. Closing the thread panel sets it to `null`.
- Component: new `desktop/src/apps/chat/ThreadPanel.tsx`. Receives `{ channelId, parentId, onClose }`. Fetches `GET /api/chat/channels/{id}/threads/{parent_id}/messages`. Composer at the bottom sends with `thread_id = parentId`.
- Composer inside panel: shares the existing composer primitives — slash menu, mention parser, typing emitter, bare-slash guardrail. Keystrokes `@`/`/` behave identically.

### Per-message hover actions

- New `MessageHoverActions.tsx`: small row that appears on message hover (desktop only), positioned top-right of the message bubble.
- Order: `😀 Reaction · 💬 Reply in thread · ⋯ More`. The "More" opens the existing context menu for parity with right-click.
- Also fills the Phase 1 gap where reactions existed at the backend but had no visible add-button.
- Keyboard path: `Shift+F10` on a focused message row opens the full context menu (which now also includes "Reply in thread").

### Parent indicator chip

Under any message with `reply_count > 0`:

```
💬 N replies · last reply 2m ago
```

Clickable. Opens the thread panel. A subtle visual indicator — no bold, just a muted chip.

### Thread list view?

Out of scope for 2b-1. Users reach threads via the parent chip. A "All threads" per-channel list is Phase 2b-2+.

### Router — thread-aware recipient resolution

New module `tinyagentos/chat/threads.py`:

```python
async def resolve_thread_recipients(
    message: dict, channel: dict, chat_messages
) -> tuple[list[str], dict[str, bool]]:
    """Return (recipients, force_by_slug) for a message in a thread.

    Narrow-by-default:
      - parent message's author (if agent)
      - all prior repliers in the thread (agents only)
      - explicit @<slug> mentions in the new message

    @all escalation: if mentions.all is True, recipients expand to every
    channel agent (same as channel-scope @all).
    """
```

`agent_chat_router._route_inner` grows a branch: if the message has `thread_id`, call `resolve_thread_recipients` instead of the channel recipient logic. All other Phase 1/2a behaviour (mention parser, bare-slash, force_respond, policy, context) still applies — just with thread-scoped inputs.

### Thread policy scoping

`GroupPolicy.may_send` is called with a synthetic channel key `f"{channel_id}:thread:{thread_id}"`. A thread has its own hop counter, cooldown window, rate cap — independent of the parent channel. The same `max_hops`/`cooldown_seconds`/`rate_cap_per_minute` settings apply (no thread-level overrides for MVP).

### Thread context window

- Source: last 20 messages of *the thread*, oldest first, via `chat_messages.get_thread_messages(channel_id, parent_id, limit=20)`.
- Prepended: the parent message as a "root" turn. Bridges see a linear thread transcript with the user's original message at the top.
- Token budget: same 4000-token cap as channel context.

### Endpoints

```
GET /api/chat/channels/{channel_id}/threads/{parent_id}/messages
    Returns the thread's replies (not the parent), oldest first.
    Auth: same as /messages endpoint.

POST /api/chat/messages
    Existing. Accepts an optional `thread_id` in the body. When set, the
    router uses thread-aware recipient resolution; bare-slash guardrail
    still applies (a "/help" reply in a thread still requires @mention).
```

### Thread policy — @all inside a thread

- `@all` inside a thread fans out to every channel-member agent (not just thread participants), each with `force_respond=true`. Same behaviour as channel-scope `@all`, just routed to the thread.
- Hop counter for those newly-added agents starts at `next_hops = parent_hops + 1`; subsequent thread replies continue the chain scoped to the thread.

## Attachments

### Composer-side UX

Three entry points, one downstream flow:

1. **Paperclip button** (primary, discoverable): click → calls `openFilePicker({ sources: ['disk', 'workspace', 'agent-workspace'], multi: true })`.
2. **Drag-drop**: files dropped on composer (or anywhere in the chat pane) trigger the `disk`-source upload path directly; no picker.
3. **Paste**: clipboard paste of image data in the composer intercepts the paste event, uploads the image, adds to attachments bar.

### `AttachmentsBar` (pre-send)

- Renders above the composer once ≥1 attachment is queued.
- Each queued attachment shows:
  - Thumbnail (images) or icon (other)
  - Filename, size
  - Upload-progress bar (or spinner)
  - `×` to remove
- Attachments stay queued until the message is sent (or the user removes them or backs out).
- Enforces the 10-attachment cap client-side; paperclip/drop of an 11th shows a transient toast.

### `AttachmentGallery` (in-message)

- Single image → inline image with `max-width: 560px; max-height: 400px`; click opens `AttachmentLightbox`.
- 2+ images → 2×2 grid preview (first 4); overflow shows `+N more` on the 4th tile. Any click opens lightbox scoped to the message's image set, arrow-key navigable.
- Files (non-image) → tile rows: icon (MIME-based) + filename + size + download icon. Click downloads (new tab).
- Mixed messages (images + files) → gallery of images on top, tile rows below.

### `AttachmentLightbox`

- Fullscreen overlay, dim backdrop, image centered.
- `←` / `→` arrows navigate within the message's image set.
- `Esc` closes.
- `Download` button in a small top-right overlay.
- No zoom in 2b-1 (basic viewer — zoom is Phase 2b-2 polish).

### SharedFilePickerDialog

Shell primitive at `desktop/src/shell/FilePicker.tsx`. Callable from any app:

```tsx
import { openFilePicker } from "@/shell/file-picker-api";

const selections = await openFilePicker({
  sources: ["disk", "workspace", "agent-workspace"],
  accept: "image/*,.pdf,.md",
  multi: true,
});
// selections: FileSelection[]
```

Types:

```ts
type FileSelection =
  | { source: "disk"; file: File }
  | { source: "workspace"; path: string }
  | { source: "agent-workspace"; slug: string; path: string };
```

Dialog UX:

- Centred modal, fixed 720×540, dim backdrop, Esc closes.
- Top tab bar: `Disk · My workspace · Agent workspaces`.
- **Disk** tab: single button "Choose files from disk" that triggers a native `<input type=file>` picker. Returns File objects when confirmed.
- **My workspace** tab: VFS tree on the left (collapsible folders), file list on the right. Uses the existing Files-app VFS-browser component, factored out (see Reuse below).
- **Agent workspaces** tab: dropdown of agents at the top; selecting one roots the VFS browser at that agent's workspace. All agent workspaces are **read-only** in the picker for 2b-1 — the user can pick files to attach but cannot save/modify. Writing back to agent workspaces is a future permissions pass.
- Bottom: `Cancel · Select (N)`.
- Multi-select: `Cmd/Ctrl+Click` within a tab. Cross-tab multi-select not supported in MVP (users pick from one source per invocation).

Reuse: the Files app's VFS-browser lives in `desktop/src/apps/FilesApp.tsx`. Factor the tree + listing into `desktop/src/shell/VfsBrowser.tsx`; both Files app and FilePicker consume it. This is targeted refactoring that serves the current goal; don't restructure anything else in the Files app.

### Backend

**Existing** (unchanged): `POST /api/chat/upload` (multipart, disk), `GET /api/chat/files/{name}` (serve).

**New** — `POST /api/chat/attachments/from-path`:

```
Body: { "path": "/workspaces/user/reports/q3.pdf", "source": "workspace" }
     or  { "path": "/workspaces/tom/outputs/summary.md", "source": "agent-workspace", "slug": "tom" }

ACL:
  - workspace: user owns this path (TAOS_USER = "user" MVP)
  - agent-workspace: user can read the agent's workspace (always true in MVP;
    permission model hardening is a later pass)

Behaviour:
  - Resolve path against the VFS root (data_dir/workspaces/{slug or 'user'})
  - Check file exists, is not a directory, size < 100 MB (shared config with disk upload)
  - Copy to data_dir/chat-files/<random-id>-<original_basename>
  - Return the attachment record:
    {
      "filename": "q3.pdf",
      "mime_type": "application/pdf",
      "size": 1234567,
      "url": "/api/chat/files/<random-id>-q3.pdf",
      "source": "workspace"
    }
```

**Message send path** (`POST /api/chat/messages`, `tinyagentos/routes/chat.py`):

- Body accepts `attachments: [AttachmentRecord, ...]` (after upload/from-path have returned individual records, client POSTs the full array).
- Server validates: ≤ 10 items, each has `url` starting with `/api/chat/files/`, each referenced file exists on disk.
- Persists into `chat_messages.attachments` jsonb.

### Message_store

`send_message` accepts an `attachments: list[dict] | None` parameter, persists into the new column. `get_messages` / `get_message` return the parsed list. Thread-message query:

```python
async def get_thread_messages(self, channel_id: str, parent_id: str, limit: int = 20) -> list[dict]:
    """Return replies in a thread (not the parent), oldest first, up to `limit`."""
```

### Bridge event payload

`bridge_session.enqueue_user_message` event data gets a new field:

```json
{
  "id": "...",
  "trace_id": "...",
  "channel_id": "...",
  "thread_id": "thread-parent-id" | null,
  "from": "user",
  "text": "...",
  "hops_since_user": 0,
  "force_respond": false,
  "context": [...],
  "attachments": [
    {"filename": "screenshot.png", "mime_type": "image/png", "size": 312456, "url": "/api/chat/files/..."}
  ]
}
```

Existing bridges ignore unknown fields. 2b-1 also upgrades the 6 bridges' `_render_context` helper to append a human-readable footer for attachments in the prompt text:

```
User attached: screenshot.png (image/png, 312 KB), report.pdf (application/pdf, 2.1 MB)
```

Multimodal vision work is out of scope for this phase.

## Chat Guide + `/help`

### `docs/chat-guide.md`

Single canonical markdown, committed in this phase. Sections:

1. **Overview** — what the taOS chat is, channel types (DM, group, topic).
2. **Channels and modes** — quiet vs lively, when to use each, how to switch (settings panel + `/quiet`/`/lively` backend).
3. **Mentions** — `@slug`, `@all`, `@humans`, word-boundary rules, case-insensitivity.
4. **Hops, cooldown, rate-cap** — how the router prevents loop-spam; when a message is silently dropped; how to tune via settings.
5. **Reactions** — emoji reactions; `👎` regeneration semantics; `🙋` hand-raise semantics.
6. **Slash menu** — `/` opens picker, command discovery via frameworks manifest, per-agent targeting.
7. **Channel settings** — right-panel overview.
8. **Agent context menu** — right-click / hover actions.
9. **Threads** *(new in 2b-1)* — opening, replying, routing rules, context window.
10. **Attachments** *(new in 2b-1)* — disk/drag/paste/paperclip/shared-picker, 10-per-message cap, preview rules, agent visibility.
11. **`/help`** — how to use it, topic list.

Structure per section: `## Section — quick` (one sentence) + `### Details` (rules, edge cases). Audience: a new taOS user in front of the chat for the first time.

### `/help [topic]` command

Server-side special case in `routes/chat.py` — detected before the bare-slash guardrail, since it's a taOS control action not a framework command:

```
POST /api/chat/messages  (content starts with "/help")
  →  chat/help.py.handle(content, channel_id) → system message text
  →  persist as system-authored message in the channel + broadcast
  →  skip agent routing
  →  200 { "ok": true, "handled": "help" }
```

Topic parser: `/help` (no args) → overview cheat sheet + link. `/help <topic>` → the section block for that topic. Topics map 1:1 to section headings: `channels`, `mentions`, `hops`, `reactions`, `slash`, `settings`, `context`, `threads`, `attachments`, `help`. Unknown topic → generic "try `/help`" message.

The system messages it posts are short (10–15 lines max) and include the GitHub link to the full guide.

### "?" icon

Small help icon in the chat header (between channel name and the Phase 2a settings ⓘ icon). `target="_blank"` link to `https://github.com/jaylfc/tinyagentos/blob/master/docs/chat-guide.md`. Phase 2b-2 replaces this with an in-app help panel.

### Going-forward rule

Every new chat-feature PR must include a `docs/chat-guide.md` update for any user-visible change. Added to the plan's Task checklist as a hard requirement.

## Components

### New backend files

- `tinyagentos/chat/threads.py` — thread recipient resolution + context builder
- `tinyagentos/chat/help.py` — `/help` command parser + response composer
- `tests/test_chat_threads.py` — thread routing + context
- `tests/test_chat_help.py` — /help topics + parser
- `tests/test_chat_attachments.py` — from-path ACL, cap, validation

### New frontend files

- `desktop/src/shell/FilePicker.tsx` — SharedFilePickerDialog
- `desktop/src/shell/file-picker-api.ts` — `openFilePicker(...)`
- `desktop/src/shell/VfsBrowser.tsx` — factored from FilesApp (dual consumer: picker + Files app)
- `desktop/src/apps/chat/ThreadPanel.tsx` — right-side thread view
- `desktop/src/apps/chat/MessageHoverActions.tsx` — hover row (reaction + reply)
- `desktop/src/apps/chat/ThreadIndicator.tsx` — "N replies · Xm ago" chip
- `desktop/src/apps/chat/AttachmentsBar.tsx` — pre-send thumbnails
- `desktop/src/apps/chat/AttachmentGallery.tsx` — in-message rendering
- `desktop/src/apps/chat/AttachmentLightbox.tsx` — fullscreen viewer
- `desktop/src/lib/chat-attachments-api.ts` — upload + from-path client
- `desktop/src/lib/use-thread-panel.ts` — state hook: `openThread(parentId)`, `closeThread()`
- Component tests under `__tests__/` for each

### Modified files

- `tinyagentos/agent_chat_router.py` — thread-aware recipient branch; policy key uses `channel_id:thread:<thread_id>` in thread context
- `tinyagentos/chat/message_store.py` — `attachments` column migration, `get_thread_messages`, persist attachments on send
- `tinyagentos/chat/group_policy.py` — no API change; the policy key convention is caller's responsibility
- `tinyagentos/routes/chat.py` — `/help` intercept, thread messages GET endpoint, attachments from-path POST, send accepts attachments
- `tinyagentos/bridge_session.py` — event payload includes `thread_id` + `attachments`
- `tinyagentos/scripts/install_hermes.sh` + 5 siblings — bridge's `_render_context` appends an attachments footer to the prompt text
- `tinyagentos/chat/context_window.py` — `build_context_window` optionally filters to thread messages (caller provides the message list; this function just trims; scope change needed in the router + store, not here)
- `desktop/src/apps/MessagesApp.tsx` — hover actions, thread panel wiring, attachment composer (paperclip + drop + paste), `/help` error-surfacing via system messages, "?" icon in header
- `desktop/src/apps/FilesApp.tsx` — consumes the new `VfsBrowser` component (moved out, behaviour unchanged)
- `docs/chat-guide.md` — **new** (retroactive Phase 1 + 2a + 2b-1)

## Error Handling

- **Thread fetch fails** → panel shows `"couldn't load this thread"` with a retry button; panel stays open.
- **Attachment upload fails** (any single file in a multi-file upload) → that file's row in `AttachmentsBar` shows `Failed — retry` with a retry button; other files continue. Send is disabled while any is failed or in-flight (except for a "send anyway" button that drops failed files).
- **`/attachments/from-path` ACL denied** → 403 with `{error: "forbidden"}`; client surfaces in picker.
- **File > 100 MB** → both upload paths reject with `{error: "file too large (100 MB max)"}`; client surfaces in AttachmentsBar.
- **`/help <topic>` unknown topic** → system message `"Unknown help topic 'foo'. Try /help for the overview."`
- **Thread @all escalation with muted agents in the channel** → muted agents are excluded (same muted-list check as channel routing).
- **Bridge receives attachments it can't handle** → it gets the text footer regardless; the structured `attachments` array is best-effort metadata, never blocking.
- **Dragging a file into the composer while the message is sending** → queue it; composer shows the new attachment ready for the next message. Don't block the in-flight send.

## Testing

### Unit

- `threads.resolve_thread_recipients` — 6 cases: narrow scope, `@all` escalation, `@<slug>` for non-thread-participant, muted agent excluded, parent author included if agent, hops propagation
- `help.handle` — 12 topic dispatch cases + unknown topic
- Attachment `/from-path` ACL — allowed/denied cases, path traversal rejection, size cap
- `message_store.get_thread_messages` — filters by thread_id, excludes parent, respects limit
- `AttachmentsBar` — queue/remove/progress states
- `AttachmentGallery` — 1 image, 4 images, 5+ images (overflow), mixed images + files
- `FilePicker` — tab switching, multi-select, cancel
- `VfsBrowser` — tree expand/collapse, listing, selection (tested once, used twice)

### Route

- `GET /threads/{parent_id}/messages` — returns thread replies, oldest first, excludes parent
- `POST /messages` with attachments — persists, rejects >10, rejects bad url shape
- `POST /attachments/from-path` — resolves workspace + agent-workspace paths, ACL, 100 MB cap, path traversal
- `POST /messages` with `thread_id` + `@all` — fans out to channel agents with `force_respond=true`
- `POST /messages` with `/help threads` — posts system message, no agent routing

### Integration

- Thread router end-to-end: user posts in thread → tom (parent author) notified with narrow scope → tom replies → don (not in thread, not mentioned) receives nothing → user posts `@all` in thread → both tom and don receive with force_respond
- Attachment + message: upload file, send with attachment, assert bridge event carries `attachments` array + text footer; assert in-message gallery renders it

### E2E (env-gated)

- Open thread panel from hover action → post reply → assert appears in thread panel, not main transcript
- Drag a file onto composer → attachment chip appears → send → gallery renders in transcript
- Paperclip → workspace tab → pick a file → send → gallery renders, no upload (from-path path)
- Paperclip → agent workspace → pick file owned by tom → send → renders
- `/help threads` in a group channel → system message appears with threads section

## Out of Scope (documented up-front)

See **Non-goals** at the top. Summarised here so plan-writers don't accidentally scope-creep:

- Pinning, read receipts, ephemeral messages, per-message affordances (edit / delete / copy link / mark unread) → **Phase 2b-2**
- Cross-app drag-drop between taOS windows → task #60 (parallel shell primitive)
- Multimodal bridge upgrades (vision per framework) → separate tasks per framework, Phase 3
- Mobile chat polish → separate downstream phase
- In-app help panel (rather than linking to GitHub) → Phase 2b-2
- Recent files / cloud sources / shared folders in picker → later polish

## Open Questions

None at spec time. Any ambiguity discovered during planning or implementation should be flagged back to this spec for amendment, not resolved silently.
