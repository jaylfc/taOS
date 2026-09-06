# Message Hub — Plan 1: Message & Channel Stores

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

**Goal:** Build the SQLite data layer for the chat system: message store with rich content support, channel store with types/members, read position tracking, and file attachment metadata.

**Architecture:** Two new BaseStore subclasses (ChatMessageStore, ChatChannelStore) following the existing pattern. Single SQLite database at `data/chat.db`. All methods async. The stores are pure data access with no WebSocket or routing logic.

**Tech Stack:** Python 3.10+, aiosqlite, BaseStore pattern, pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/chat/message_store.py` | ChatMessageStore — CRUD for messages, reactions, pins, search |
| Create | `tinyagentos/chat/channel_store.py` | ChatChannelStore — CRUD for channels, members, read positions |
| Create | `tinyagentos/chat/__init__.py` | Package init |
| Modify | `tinyagentos/app.py` | Register stores on app.state |
| Modify | `tests/conftest.py` | Init/close chat stores in fixtures |
| Create | `tests/test_chat_messages.py` | Message store tests |
| Create | `tests/test_chat_channels.py` | Channel store tests |

---

### Task 1: ChatMessageStore

**Files:**
- Create: `tinyagentos/chat/__init__.py`
- Create: `tinyagentos/chat/message_store.py`
- Test: `tests/test_chat_messages.py`

- [ ] **Step 1: Write tests**

Create `tests/test_chat_messages.py` with these tests (each creates a fresh store in tmp_path):

- `test_send_message` — send a text message, verify it returns a dict with id, content, author_id, state="complete"
- `test_get_message` — send then get by id, verify all fields present
- `test_get_messages_paginated` — send 5 messages, get with limit=3, verify 3 returned in chronological order
- `test_get_messages_before` — send 5, get before message 3's timestamp, verify only 2 returned
- `test_send_with_embeds` — send message with embeds list, verify stored and retrieved as list
- `test_send_with_components` — send message with button components, verify round-trip
- `test_send_with_attachments` — send with attachment metadata, verify round-trip
- `test_edit_message` — send, edit, verify content changed and edited_at set
- `test_delete_message` — send, delete, verify get returns None
- `test_add_reaction` — send, add reaction, verify reactions dict updated
- `test_remove_reaction` — add then remove reaction, verify removed
- `test_multiple_reactions` — two different emoji from two authors, verify both stored
- `test_update_state` — send, update to "streaming", verify state changed
- `test_pin_message` — send, pin, verify pinned=1
- `test_unpin_message` — pin then unpin, verify pinned=0
- `test_search_messages` — send 3 messages with different content, search for keyword, verify matches
- `test_search_in_channel` — messages in two channels, search with channel filter
- `test_canvas_message` — send with content_type="canvas" and metadata with canvas_url, verify round-trip
- `test_content_blocks` — send with content_blocks array, verify round-trip

- [ ] **Step 2: Create chat package**

```bash
mkdir -p tinyagentos/chat
```

Create `tinyagentos/chat/__init__.py`:
```python
"""Chat system — message hub with channels, threads, and rich content."""
```

- [ ] **Step 3: Implement ChatMessageStore**

Create `tinyagentos/chat/message_store.py` with `ChatMessageStore(BaseStore)`.

Schema: the `chat_messages` and `chat_attachments` tables from the spec.

Methods:
- `send_message(channel_id, author_id, author_type, content, content_type="text", thread_id=None, embeds=None, components=None, attachments=None, content_blocks=None, metadata=None, state="complete")` -> dict. Generates UUID hex[:12] id, stores with json.dumps for list/dict fields, returns the full message dict.
- `get_message(message_id)` -> dict | None
- `get_messages(channel_id, limit=50, before=None, after=None)` -> list[dict]. Paginated by created_at. Returns chronological order.
- `edit_message(message_id, content)` -> None. Sets edited_at to time.time().
- `delete_message(message_id)` -> bool
- `add_reaction(message_id, emoji, user_id)` -> None. Loads reactions JSON, appends user_id to emoji list, saves.
- `remove_reaction(message_id, emoji, user_id)` -> None. Removes user_id from emoji list. Removes emoji key if empty.
- `update_state(message_id, state)` -> None
- `pin_message(channel_id, message_id, user_id)` -> None. Inserts into chat_pins, sets pinned=1 on message.
- `unpin_message(channel_id, message_id)` -> None. Deletes from chat_pins, sets pinned=0.
- `search(query, channel_id=None, author_id=None, limit=50)` -> list[dict]. LIKE search on content field.

All dict fields (embeds, components, attachments, reactions, content_blocks, metadata) stored as JSON strings, parsed on read.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_chat_messages.py -v`
Expected: 19 PASS

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/__init__.py tinyagentos/chat/message_store.py tests/test_chat_messages.py
git commit -m "feat: add ChatMessageStore with rich content, reactions, pins, and search"
```

---

### Task 2: ChatChannelStore

**Files:**
- Create: `tinyagentos/chat/channel_store.py`
- Test: `tests/test_chat_channels.py`

- [ ] **Step 1: Write tests**

Create `tests/test_chat_channels.py`:

- `test_create_channel` — create a topic channel, verify returns dict with id, name, type
- `test_get_channel` — create then get, verify all fields
- `test_list_channels` — create 3 channels, list all, verify 3 returned
- `test_list_channels_for_member` — create 2 channels with different members, list for a specific member, verify only theirs returned
- `test_create_dm` — create dm type, verify members set correctly
- `test_create_group` — create group with 3 members
- `test_create_thread` — create thread type (would have a parent_channel_id in settings)
- `test_update_channel` — create, update description and topic, verify changes
- `test_delete_channel` — create, delete, verify gone
- `test_add_member` — create channel, add a member, verify in members list
- `test_remove_member` — add then remove member, verify removed
- `test_update_read_position` — update read position, verify stored
- `test_get_unread_counts` — create channel, send some messages (need message store for this), update read position to an earlier message, verify unread count > 0
- `test_update_last_message_at` — update, verify timestamp changed

- [ ] **Step 2: Implement ChatChannelStore**

Create `tinyagentos/chat/channel_store.py` with `ChatChannelStore(BaseStore)`.

Schema: `chat_channels` and `chat_read_positions` tables from the spec.

Methods:
- `create_channel(name, type, created_by, members=None, description="", topic="", settings=None)` -> dict. UUID hex[:12] id. Members stored as JSON array.
- `get_channel(channel_id)` -> dict | None
- `list_channels(member_id=None)` -> list[dict]. If member_id, filter by JSON `members LIKE %member_id%`.
- `update_channel(channel_id, name=None, description=None, topic=None, settings=None)` -> None
- `delete_channel(channel_id)` -> bool
- `add_member(channel_id, member_id)` -> None. Loads members JSON, appends if not present.
- `remove_member(channel_id, member_id)` -> None. Removes from list.
- `update_read_position(user_id, channel_id, message_id)` -> None. INSERT OR REPLACE into chat_read_positions.
- `get_unread_counts(user_id)` -> dict[str, int]. For each channel the user is a member of, count messages after their read position.
- `update_last_message_at(channel_id)` -> None. Sets to time.time().

Note: `get_unread_counts` needs access to the messages table. Since both tables are in the same DB file, the channel store can query chat_messages directly. Alternatively, accept a `message_store` reference. Simplest: put both tables in the same DB and query directly.

**Design decision:** Use a single DB file (`chat.db`) with both tables. ChatChannelStore gets its own class but shares the DB path. Both stores can open the same file. Or better: make a single `ChatStore` that has both sets of methods. Actually, follow the existing pattern (each store = one file, one DB). But for `get_unread_counts` to work, the channel store needs access to messages. Solution: the unread count query joins against chat_messages, so both tables must be in the same DB. Create both stores pointing at the same `chat.db` path and include all table schemas in both (SQLite's `CREATE TABLE IF NOT EXISTS` is idempotent).

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_chat_channels.py -v`
Expected: 14 PASS

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/chat/channel_store.py tests/test_chat_channels.py
git commit -m "feat: add ChatChannelStore with DM/group/topic types, members, and read tracking"
```

---

### Task 3: Wire into app.py + conftest

**Files:**
- Modify: `tinyagentos/app.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Register stores in app.py**

Read `tinyagentos/app.py`. Add:
- Import both stores from `tinyagentos.chat`
- Create instances pointing at `data_dir / "chat.db"`
- Init in lifespan, set on app.state (`chat_messages`, `chat_channels`), close in cleanup
- Add to eager state section

- [ ] **Step 2: Update conftest.py**

Read `tests/conftest.py`. Add chat store init/close to both `client` and `client_with_qmd` fixtures, following the exact pattern of other stores.

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/e2e --tb=short -q`
Expected: all tests pass (877+33 new = ~910)

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/app.py tests/conftest.py
git commit -m "feat: wire chat message and channel stores into app lifecycle"
```
