# Chat Phase 2b-2a — Per-message affordances + pinning

**Status:** Design approved 2026-04-19.

## Goal

Add the per-message UX layer to taOS chat: edit-own, delete-own, copy-link (deep-link), mark-unread, pin/unpin (human-only) with an agent pin-request flow. Ships as a single PR on branch `feat/chat-phase-2b-2a-per-msg`.

Phase 2b-2b (read receipts + ephemeral) and 2b-2c (UI polish — in-app help, all-threads, zoom, picker recents) are separate sub-phases, not in this spec.

## Non-goals

- Edit history / version stack (YAGNI — Slack's always-editable text-only model is enough).
- Hard delete or admin-undelete UI (soft delete is terminal for 2b-2a; recovery is a future admin tool).
- Notification refire on mark-unread (taOS has no push notifications yet).
- Pin announcements ("Jay pinned a message") as first-class system messages — 2b-2c polish.
- Custom URL scheme (`taos://...`) — plain web URLs with `?msg=<id>` are enough.
- Per-user read cursor infrastructure beyond the existing `chat_read_positions` table.

## Decisions

1. **Pinning governance**: humans pin/unpin directly; agents request a pin via a 📌 reaction added by the message's own author (agent). UI shows a "Pin this?" affordance next to the message; one human click approves.
2. **Edit-own**: text-only, always editable, `(edited)` marker. No attachments mutation, no thread_id mutation, no history.
3. **Delete-own**: soft delete via `deleted_at` timestamp. Tombstone in UI. Thread replies stay anchored to the parent id (never orphan). No admin undelete in scope.
4. **Copy link**: `https://<host>/chat/<channel_id>?msg=<message_id>`. MessagesApp scrolls to + briefly highlights the target message on load.
5. **Mark unread**: rewinds `chat_read_positions.last_read_at` for the current user to `msg.created_at - 0.001`. Channel badge logic uses the existing `get_unread_counts` store method.
6. **Pin cap**: 50 pins per channel. 409 Conflict when exceeded.

## Architecture

### Data model

New columns on `chat_messages`:

| Column | Type | Notes |
|---|---|---|
| `pinned_at` | `REAL NULL` | Unix ts; presence = pinned. |
| `pinned_by` | `TEXT NULL` | `user_id` of the human who pinned. |
| `deleted_at` | `REAL NULL` | Unix ts of soft delete. `content` preserved on row for admin recovery but never returned by the default list endpoint. |
| `edited_at` | `REAL NULL` | Already exists — just expose + set on edit. |

Migration: additive `ALTER TABLE chat_messages ADD COLUMN ...`, no data backfill needed (all default NULL).

Existing `chat_read_positions` table (already in `channel_store.py`) is reused for mark-unread. No new columns.

### Endpoints

All under `/api/chat/` per existing convention.

**Pinning:**
- `POST /api/chat/messages/{message_id}/pin` — body: `{}`. 200 on success. Errors: 403 if the authenticated principal is not a human, 404 if message not found, 409 if pin cap exceeded.
- `DELETE /api/chat/messages/{message_id}/pin` — 204 on success. 403 for non-humans. 404 if message not found or not pinned.
- `GET /api/chat/channels/{channel_id}/pins` — returns `{pins: [<message>, ...]}` ordered by `pinned_at DESC`. Public to channel members.

**Edit / delete:**
- `PATCH /api/chat/messages/{message_id}` — body: `{content: string}`. 200 with updated message on success. 403 if caller isn't the author. 404 if message not found. Sets `edited_at = now()`. Text-only; any other field in body is rejected with 400.
- `DELETE /api/chat/messages/{message_id}` — soft delete. Sets `deleted_at = now()`. 204 on success. 403 if caller isn't the author. 404 if message not found. Idempotent (second delete = 204).

**Mark unread:**
- `POST /api/chat/channels/{channel_id}/read-cursor/rewind` — body: `{before_message_id: string}`. 200 on success. Sets `chat_read_positions.last_read_at` for `(current_user, channel_id)` to `msg.created_at - 0.001`. 404 if the referenced message is not in this channel.

**Agent pin-request flow (no new endpoint):**
Reuse the existing reaction dispatcher (`tinyagentos/chat/reactions.py`). When an agent adds a 📌 reaction to a message it authored, a router rule sets `metadata.pin_requested = true` on the message and broadcasts the update. No backend pin actually happens — only a flag. Humans see a "Pin this?" inline affordance; clicking it calls `POST .../pin` normally and clears the flag.

The authentication check for `POST /pin` uses the existing session user's `author_type` — humans only (check `auth.author_type == "user"`). Agents that try to POST directly get 403.

### Frontend

New and changed components under `desktop/src/apps/chat/`:

- **`MessageHoverActions.tsx`** (existing): add a `⋯` overflow button that opens a dropdown with (conditional on ownership + state):
  - Edit (author only, not deleted)
  - Delete (author only, not deleted)
  - Copy link (everyone, not deleted)
  - Pin / Unpin (humans only)
  - Mark unread (everyone)
- **`MessageOverflowMenu.tsx`** (new): the dropdown body rendered by the hover toolbar's ⋯ button. Uses the existing dropdown primitive in `shell/`.
- **`PinnedMessagesPopover.tsx`** (new): floating popover from the channel-header pin badge. Shows pinned messages with mini message renderer + "Jump to" button. Empty state: "No pinned messages yet."
- **`PinBadge.tsx`** (new): `📌 N` button in the channel header next to the `ⓘ` settings icon. Hidden when N=0.
- **`PinRequestAffordance.tsx`** (new): inline "Pin this?" pill shown below an agent's message when `metadata.pin_requested === true`. Clicking calls pin API + clears the flag.
- **`MessageEditor.tsx`** (new): inline textarea replacing message content during edit. Enter saves, Esc cancels. Preserves composer-style keybindings.
- **`MessageTombstone.tsx`** (new): renders grey italic `This message was deleted` when `deleted_at` is set.

Changes in `MessagesApp.tsx`:
- Deep-link scroll: on mount / channel switch, read `?msg=<id>` from `window.location.search`. After the message list loads, scroll the target into view with a short highlight pulse animation (2s yellow outline).
- Read the pin count for the current channel and render `PinBadge` when > 0.

### API client

- `desktop/src/lib/chat-messages-api.ts` (new tiny module): `pinMessage(id)`, `unpinMessage(id)`, `listPins(channelId)`, `editMessage(id, content)`, `deleteMessage(id)`, `markUnread(channelId, beforeMessageId)`.

### Bridge impact

None for 2b-2a. Agents can't edit/delete others' messages and don't own the pin endpoint. The 📌-reaction pin-request path uses the existing reaction endpoint.

## Data flow

**Edit:** user opens overflow menu → clicks Edit → `MessageEditor` renders with current content → user saves → `PATCH /api/chat/messages/{id}` → store updates row, broadcasts the updated message via `hub.broadcast` → all listeners (including sender) receive the WS event and replace their local message.

**Delete:** user opens overflow menu → clicks Delete → confirm dialog → `DELETE /api/chat/messages/{id}` → store sets `deleted_at` → `hub.broadcast` delivers the update → UI re-renders message as tombstone.

**Pin:** human clicks Pin → `POST .../pin` → store sets `pinned_at`, `pinned_by` → `hub.broadcast` update → pin badge count refreshes; popover content refreshes on open.

**Pin-request (agent):** agent adds 📌 reaction to its own message via existing reaction pipeline → reactions semantic dispatcher (`tinyagentos/chat/reactions.py`) checks `emoji == "📌" and reactor_id == message.author_id and reactor_type == "agent"` → sets `metadata.pin_requested = true` → broadcast update. UI renders `PinRequestAffordance`. Human clicks → normal pin flow; the handler on success also clears the flag (one additional store call).

**Mark unread:** user clicks Mark Unread → `POST .../read-cursor/rewind` with `before_message_id` → store rewinds `last_read_at` → channel badge updates via the existing unread-counts polling/WS flow.

**Copy link:** pure client — composes URL, writes to `navigator.clipboard`.

**Deep link:** MessagesApp mounts → detects `?msg=<id>` → fetches channel messages (existing flow) → when render completes, scrolls the node with that id into view and adds a `data-highlight` class for 2 seconds.

## Error handling

- **Edit non-own**: backend 403. Frontend hides the Edit action unless `msg.author_id === currentUser.id`.
- **Delete non-own**: same — 403 + hidden action.
- **Pin by non-human**: 403. Frontend hides Pin/Unpin actions for non-humans (but agents don't have a frontend, so this is defense-in-depth).
- **Pin cap exceeded**: 409. Frontend surfaces "Pin limit reached (50)" via the existing toast/error component.
- **Message not found (edit/delete/pin after it was already deleted)**: 404. Frontend shows a small inline error ("This message no longer exists") and refreshes the list.
- **Deep link to a message not in the channel or user doesn't have access**: silently ignore — no error, just don't scroll (the user will see the channel normally).
- **Mark unread before the first message**: passes through (rewind before any message makes the channel unread from the start); no error.

## Testing

**Backend (pytest):**

- `tests/test_chat_pins.py` (new):
  - Pin a message → listed in `GET /pins`
  - Unpin → removed from list
  - Agent POST /pin → 403
  - Pin 51st message → 409
  - Unpin a non-pinned message → 404
  - Pin a deleted message → 404 (or allowed? decision: 404 — can't pin tombstones)
- `tests/test_chat_edit_delete.py` (new):
  - Author PATCH → 200, content updated, `edited_at` set
  - Non-author PATCH → 403
  - PATCH with extra fields → 400
  - PATCH deleted message → 404
  - Author DELETE → 204, subsequent GET shows tombstone-shape (deleted_at set, content may be redacted)
  - Non-author DELETE → 403
  - Idempotent: DELETE twice → 204 both times
- `tests/test_chat_mark_unread.py` (new):
  - Rewind to a specific message → `last_read_at` set to `msg.created_at - 0.001`
  - Unread count reflects the rewind
  - Rewind to non-existent message → 404
- `tests/test_chat_pin_request.py` (new):
  - Agent adds 📌 to own message via reaction endpoint → message `metadata.pin_requested = true`
  - Agent adds 📌 to another agent's message → no flag set
  - Human adds 📌 (plain reaction) → no flag (humans pin via endpoint, not via reactions)

**Frontend (vitest + React Testing Library):**

- `MessageOverflowMenu.test.tsx`: action visibility per ownership + state + author_type.
- `PinnedMessagesPopover.test.tsx`: empty state; renders list; "Jump to" triggers callback.
- `PinBadge.test.tsx`: hidden at 0, renders count otherwise.
- `PinRequestAffordance.test.tsx`: renders when flag set; click fires the pin handler.
- `MessageEditor.test.tsx`: Enter saves, Esc cancels.
- `MessageTombstone.test.tsx`: renders expected text.
- `deep-link.test.tsx` (in MessagesApp test suite, if one exists): `?msg=<id>` triggers scroll-into-view.

**E2E (Playwright, gated):**

- `tests/e2e/test_chat_phase2b2a.py`:
  - Open own message → overflow → Edit → save → new text visible + `(edited)`.
  - Open own message → overflow → Delete → confirm → tombstone.
  - Pin badge appears after pinning; popover shows the message; "Jump to" scrolls.
  - Copy link → paste URL into address bar → scroll + highlight lands on the message.

## Rollout

Single PR off `feat/chat-phase-2b-2a-per-msg` (already branched from 2b-1). Merges after PR #236 (Phase 2b-1) so the base is stable.

Backward compatibility: migration is additive (`ALTER TABLE ... ADD COLUMN`). Old clients see the new columns as extra fields and ignore them. No breaking changes.

## Out of scope — tracked for later sub-phases

- **Phase 2b-2b**: read receipts (per-user seen indicators), ephemeral messages (TTL auto-delete).
- **Phase 2b-2c**: in-app help panel, all-threads list, lightbox zoom, file picker recents.
- Pin announcement system messages.
- Admin undelete UI.
- Edit history.
