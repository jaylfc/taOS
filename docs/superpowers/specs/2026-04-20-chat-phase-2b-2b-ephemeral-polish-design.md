# Chat Phase 2b-2b/c — ephemeral messages + UI polish

**Status:** Approved 2026-04-20.

## Goal

Ship the remaining polish features from the original Phase 2b-2 bucket — minus read receipts (Slack/Discord don't do them; unread badges already cover the use case).

Four independent features, single PR:
1. Ephemeral messages (channel-level TTL, Signal/WhatsApp style).
2. In-app help panel (replaces external "?" link).
3. All-threads list (browse all threads in a channel).
4. Lightbox zoom (zoom + pan in the image viewer).

## Non-goals

- Read receipts per message (dropped — neither Slack nor Discord do them).
- Per-message ephemeral TTL (channel-level only).
- Rich-text help content, full markdown renderer (plain markdown → HTML is enough).
- Multi-image zoom sync.

## 1. Ephemeral messages

### Data model

- `channel.settings.ephemeral_ttl_seconds: number | null` — null = off. Set via channel settings panel.
- `chat_messages.expires_at: REAL NULL` — computed at send time as `created_at + ttl` when channel has TTL set.

### Send-time compute

In `ChatMessageStore.send_message`, accept `expires_at` kwarg. Routes that call `send_message` look up channel settings and pass `expires_at = created_at + ephemeral_ttl_seconds` if set.

### Expiry sweep

New module `tinyagentos/chat/ephemeral_sweeper.py`:
- `async def sweep_expired(store, hub)` — iterates `chat_messages WHERE expires_at < now AND deleted_at IS NULL`, calls `store.soft_delete_message(id)` per row + broadcasts `message_delete` event.
- Registered as a background task in `app.py`, runs every 5 min.

### UI

- `ChannelSettingsPanel`: dropdown for "Disappearing messages": Off / 1 hour / 24 hours / 7 days / 30 days. Maps to seconds.
- Channel header shows a ⏳ badge with TTL text when ephemeral is on ("⏳ 24h").

## 2. In-app help panel

### Backend

- `GET /api/docs/chat-guide` — serves `docs/chat-guide.md` content as markdown.

### UI

- New component `desktop/src/apps/chat/HelpPanel.tsx` — modal overlay on the messages app.
- Replace the existing "?" external `<a>` in chat header with a button that opens `HelpPanel`.
- Modal renders markdown via existing `renderContent` helper (or a minimal md→JSX pass).
- Esc + backdrop click closes. Anchor hash navigation works (clicking a header anchor inside the modal jumps in-modal, not in the browser).

## 3. All-threads list

### Backend

- `GET /api/chat/channels/{id}/threads` — returns `{threads: [{parent_message, reply_count, last_reply_at}]}`. Built from `SELECT DISTINCT thread_id FROM chat_messages WHERE channel_id = ? AND thread_id IS NOT NULL`, joined with parent message.

### UI

- New component `desktop/src/apps/chat/AllThreadsList.tsx` — right-side panel (mutex with settings + thread panel).
- Opened via a "Threads" button in the channel header.
- List shows each thread's parent message excerpt + reply count + last reply time.
- Click a row → opens the thread panel for that parent.

## 4. Lightbox zoom

### UI changes only

Extend `desktop/src/apps/chat/AttachmentLightbox.tsx`:
- Keyboard: `+` zoom in 20%, `-` zoom out, `0` reset, mouse wheel zooms centered on cursor.
- Double-click toggles 1x / 2x.
- Pan with pointer drag when zoomed > 1x.
- Reset zoom on image navigation (← / →).

## Testing

### Backend unit tests
- `tests/test_chat_ephemeral.py` (new): `send_message` with ttl sets `expires_at`; sweep_expired soft-deletes + broadcasts; channel without ttl doesn't set expires_at.
- `tests/test_chat_threads.py` (extend): new `GET /threads` endpoint returns thread list.
- `tests/test_chat_docs.py` (new): `GET /api/docs/chat-guide` returns guide markdown.

### Frontend unit tests
- `HelpPanel.test.tsx`: renders content, Esc closes, backdrop click closes.
- `AllThreadsList.test.tsx`: renders empty state, renders list, jump-to-thread callback.
- `AttachmentLightbox.test.tsx` (extend): zoom in / out / reset keyboard + wheel.

### E2E
- `tests/e2e/test_chat_phase2b2b.py`: ephemeral channel, help panel open, all-threads panel open.

## Rollout

Single PR. Additive (new column, new endpoints, new components). No breaking changes.

## Out of scope

- Read receipts (dropped).
- Per-message TTL.
- Ephemeral message TTL countdown timer in the UI.
- Rich markdown features (code syntax highlighting in help panel).
- Full thread search.
