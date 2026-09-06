# Chat Phase 2b-2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship per-message affordances (edit-own, delete-own, copy link, mark-unread) and pinning (human-only with agent pin-request via 📌 self-reaction) on taOS chat.

**Architecture:** Additive `deleted_at` column + reuse existing `chat_pins` table; new author-checked REST endpoints; overflow menu (⋯) on `MessageHoverActions`; header pin badge popover; deep-link scroll via `?msg=<id>` query param. Soft delete (tombstones preserve thread anchors); always-editable text-only edits; web-URL copy-link; existing `chat_read_positions` drives mark-unread rewind.

**Tech Stack:** Python 3.12 + FastAPI + aiosqlite + httpx (backend); React + TypeScript + Tailwind + Vitest (desktop); Playwright (E2E).

---

## File structure

### Backend
- `tinyagentos/chat/message_store.py` — add `deleted_at` column in SCHEMA + `soft_delete_message`, `pin_message`, `unpin_message`, `get_pins`, `is_pinned` store methods. Update `delete_message` → calls `soft_delete_message` (soft is now canonical).
- `tinyagentos/chat/channel_store.py` — add `rewind_read_cursor(user_id, channel_id, before_ts)` method.
- `tinyagentos/routes/chat.py` — 6 new endpoints: `GET /channels/{id}/pins`, `POST /messages/{id}/pin`, `DELETE /messages/{id}/pin`, `PATCH /messages/{id}`, `DELETE /messages/{id}`, `POST /channels/{id}/read-cursor/rewind`.
- `tinyagentos/chat/reactions.py` — extend semantic dispatcher: 📌 by an agent on its own message sets `metadata.pin_requested = true`.

### Backend tests
- `tests/test_chat_pins.py` (new)
- `tests/test_chat_edit_delete.py` (new)
- `tests/test_chat_mark_unread.py` (new)
- `tests/test_chat_pin_request.py` (new)
- `tests/test_chat_messages.py` (modify — existing hard-delete test updated to soft-delete expectation)
- `tests/test_routes_agents.py` (modify — existing cleanup test updated)

### Frontend
- `desktop/src/lib/chat-messages-api.ts` — new client.
- `desktop/src/apps/chat/MessageOverflowMenu.tsx` — overflow dropdown.
- `desktop/src/apps/chat/MessageEditor.tsx` — inline text edit input.
- `desktop/src/apps/chat/MessageTombstone.tsx` — "deleted" placeholder.
- `desktop/src/apps/chat/PinBadge.tsx` — header badge showing pin count.
- `desktop/src/apps/chat/PinnedMessagesPopover.tsx` — dropdown listing pins.
- `desktop/src/apps/chat/PinRequestAffordance.tsx` — "Pin this?" inline pill.
- `desktop/src/apps/chat/MessageHoverActions.tsx` — add ⋯ button.
- `desktop/src/apps/MessagesApp.tsx` — integrate (overflow wiring, pin badge, deep-link scroll, pin-request affordance render, tombstone render, edit-in-place).

### Frontend tests (all under `desktop/src/apps/chat/__tests__/` except the api client):
- `chat-messages-api.test.ts` (under `desktop/src/lib/__tests__/`)
- `MessageOverflowMenu.test.tsx`
- `MessageEditor.test.tsx`
- `MessageTombstone.test.tsx`
- `PinBadge.test.tsx`
- `PinnedMessagesPopover.test.tsx`
- `PinRequestAffordance.test.tsx`

### Docs + E2E
- `docs/chat-guide.md` — append "Edit + delete", "Pinning", "Copy link + deep links", "Mark unread" sections.
- `tests/e2e/test_chat_phase2b2a.py` (new, env-gated).

### Build
- `static/desktop/**` — rebuild on Task 18.

---

## Task 1: `deleted_at` column + soft-delete store method

**Files:**
- Modify: `tinyagentos/chat/message_store.py`
- Modify: `tests/test_chat_messages.py`
- Modify: `tests/test_routes_agents.py`

- [ ] **Step 1: Add `deleted_at` column to SCHEMA**

Edit `tinyagentos/chat/message_store.py` — in the `MESSAGES_SCHEMA` string (around line 9-30), add a column after `edited_at`:

```python
MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    ...
    edited_at REAL,
    deleted_at REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    ...
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_channel ON chat_messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id);
```

Right after the final `CREATE INDEX ...` line in the string, add a migration guard for existing databases:

```python
-- additive migration for existing DBs
ALTER TABLE chat_messages ADD COLUMN deleted_at REAL;
"""
```

**Note:** SQLite `ADD COLUMN` is idempotent-unsafe — re-running fails with "duplicate column name". Use a try/except wrapper via the existing `BaseStore` migration mechanism. Check `tinyagentos/base_store.py` to confirm how migrations are applied; if it swallows duplicate-column errors, the string is fine. Otherwise, wrap with `try/except` in `init()`. Look for existing `ALTER TABLE` usage in `chat_messages.py` SCHEMA — there's likely a pattern already.

If the SCHEMA execution doesn't tolerate `ALTER` on existing DBs, add an explicit guarded migration in `ChatMessageStore.init()`:

```python
async def init(self) -> None:
    await super().init()
    try:
        await self._db.execute("ALTER TABLE chat_messages ADD COLUMN deleted_at REAL")
        await self._db.commit()
    except Exception:
        # column already exists
        pass
```

Pick whichever matches the existing pattern in the file.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_chat_messages.py`:

```python
@pytest.mark.asyncio
async def test_soft_delete_sets_deleted_at(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="hello",
    )
    ok = await store.soft_delete_message(msg["id"])
    assert ok is True
    got = await store.get_message(msg["id"])
    assert got is not None  # row preserved
    assert got["deleted_at"] is not None
    assert got["content"] == "hello"  # content preserved for admin recovery


@pytest.mark.asyncio
async def test_soft_delete_nonexistent_returns_false(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    ok = await store.soft_delete_message("does-not-exist")
    assert ok is False
```

Check whether `ChatMessageStore` is already imported at the top of `test_chat_messages.py`; if not, add the import.

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_messages.py::test_soft_delete_sets_deleted_at -v`
Expected: FAIL — AttributeError: `soft_delete_message` not defined.

- [ ] **Step 4: Add `soft_delete_message` method**

In `tinyagentos/chat/message_store.py`, add after `edit_message`:

```python
async def soft_delete_message(self, message_id: str) -> bool:
    """Mark message as soft-deleted; returns True if a row was updated."""
    now = time.time()
    cursor = await self._db.execute(
        "UPDATE chat_messages SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
        (now, message_id),
    )
    await self._db.commit()
    return cursor.rowcount > 0
```

- [ ] **Step 5: Update existing `delete_message` to soft-delete (canonical)**

Replace the existing `delete_message`:

```python
async def delete_message(self, message_id: str) -> bool:
    """Canonical delete = soft delete (Phase 2b-2a)."""
    return await self.soft_delete_message(message_id)
```

This means row persists after `delete_message`.

- [ ] **Step 6: Update existing hard-delete test in `tests/test_chat_messages.py`**

Find the existing test at `tests/test_chat_messages.py:121` that expects `get_message` to return `None` after `delete_message`. Change the assertion to soft-delete semantics:

```python
# OLD
deleted = await store.delete_message(msg["id"])
assert deleted is True
got = await store.get_message(msg["id"])
assert got is None

# NEW
deleted = await store.delete_message(msg["id"])
assert deleted is True
got = await store.get_message(msg["id"])
assert got is not None
assert got["deleted_at"] is not None
```

- [ ] **Step 7: Update `tests/test_routes_agents.py:929` cleanup block**

The existing usage is agent-cleanup that hard-deletes messages. Change the assertion around the delete to not rely on the row being gone:

```python
# Was probably: assert await msg_store.get_message(id) is None
# Update to reflect soft-delete semantics, OR remove the assertion if it was just for cleanup
```

Read the file around the line reference before editing — if the test only calls `delete_message` for cleanup (not asserting), it's safe to leave the call in place. Only change assertions that specifically require row removal.

- [ ] **Step 8: Run full test_chat_messages + test_routes_agents**

Run: `PYTHONPATH=. pytest tests/test_chat_messages.py tests/test_routes_agents.py -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add tinyagentos/chat/message_store.py tests/test_chat_messages.py tests/test_routes_agents.py
git commit -m "feat(chat): soft-delete messages via deleted_at column; canonical delete = soft"
```

---

## Task 2: Pin store methods + 50-pin cap

**Files:**
- Modify: `tinyagentos/chat/message_store.py`
- Test: `tests/test_chat_pins.py` (new)

The `chat_pins` table already exists in the schema (columns `channel_id`, `message_id`, `pinned_by`, `pinned_at`, PK `(channel_id, message_id)`). We add store methods around it.

- [ ] **Step 1: Write failing tests**

Create `tests/test_chat_pins.py`:

```python
import pytest
from tinyagentos.chat.message_store import ChatMessageStore


@pytest.mark.asyncio
async def test_pin_and_list_pins(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="hi",
    )
    await store.pin_message("c1", msg["id"], pinned_by="user:jay")
    pins = await store.get_pins("c1")
    assert len(pins) == 1
    assert pins[0]["id"] == msg["id"]
    assert pins[0]["pinned_by"] == "user:jay"
    assert pins[0]["pinned_at"] is not None


@pytest.mark.asyncio
async def test_unpin_message(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="hi",
    )
    await store.pin_message("c1", msg["id"], pinned_by="user:jay")
    ok = await store.unpin_message("c1", msg["id"])
    assert ok is True
    pins = await store.get_pins("c1")
    assert pins == []
    # unpin again -> False
    ok2 = await store.unpin_message("c1", msg["id"])
    assert ok2 is False


@pytest.mark.asyncio
async def test_pin_cap_at_50(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    ids = []
    for i in range(50):
        m = await store.send_message(
            channel_id="c1", author_id="tom", author_type="agent", content=f"m{i}",
        )
        ids.append(m["id"])
        await store.pin_message("c1", m["id"], pinned_by="user:jay")
    # 51st should raise
    m51 = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="m51",
    )
    with pytest.raises(ValueError, match="pin cap"):
        await store.pin_message("c1", m51["id"], pinned_by="user:jay")


@pytest.mark.asyncio
async def test_pin_idempotent(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    m = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="x",
    )
    await store.pin_message("c1", m["id"], pinned_by="user:jay")
    await store.pin_message("c1", m["id"], pinned_by="user:jay")  # no raise
    pins = await store.get_pins("c1")
    assert len(pins) == 1


@pytest.mark.asyncio
async def test_is_pinned(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    m = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="x",
    )
    assert await store.is_pinned(m["id"]) is False
    await store.pin_message("c1", m["id"], pinned_by="user:jay")
    assert await store.is_pinned(m["id"]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_pins.py -v`
Expected: FAIL — `pin_message`/`get_pins`/`unpin_message`/`is_pinned` not defined.

- [ ] **Step 3: Add store methods**

In `tinyagentos/chat/message_store.py`, after `delete_message`:

```python
PIN_CAP_PER_CHANNEL = 50


async def pin_message(self, channel_id: str, message_id: str, pinned_by: str) -> None:
    """Pin a message in a channel. Idempotent. Raises ValueError if pin cap
    (50) would be exceeded by a new pin."""
    # Check if already pinned (idempotent)
    async with self._db.execute(
        "SELECT 1 FROM chat_pins WHERE channel_id = ? AND message_id = ?",
        (channel_id, message_id),
    ) as cursor:
        already = await cursor.fetchone()
    if already:
        return
    # Check cap
    async with self._db.execute(
        "SELECT COUNT(*) FROM chat_pins WHERE channel_id = ?", (channel_id,)
    ) as cursor:
        row = await cursor.fetchone()
    count = row[0] if row else 0
    if count >= PIN_CAP_PER_CHANNEL:
        raise ValueError(f"pin cap ({PIN_CAP_PER_CHANNEL}) reached")
    now = time.time()
    await self._db.execute(
        "INSERT INTO chat_pins (channel_id, message_id, pinned_by, pinned_at) VALUES (?, ?, ?, ?)",
        (channel_id, message_id, pinned_by, now),
    )
    await self._db.commit()


async def unpin_message(self, channel_id: str, message_id: str) -> bool:
    cursor = await self._db.execute(
        "DELETE FROM chat_pins WHERE channel_id = ? AND message_id = ?",
        (channel_id, message_id),
    )
    await self._db.commit()
    return cursor.rowcount > 0


async def is_pinned(self, message_id: str) -> bool:
    async with self._db.execute(
        "SELECT 1 FROM chat_pins WHERE message_id = ?", (message_id,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def get_pins(self, channel_id: str) -> list[dict]:
    """Return pinned messages in this channel, newest pin first, with
    `pinned_by` and `pinned_at` fields merged into each message dict."""
    async with self._db.execute(
        """SELECT m.*, p.pinned_by, p.pinned_at
           FROM chat_messages m
           INNER JOIN chat_pins p
             ON p.message_id = m.id AND p.channel_id = m.channel_id
           WHERE p.channel_id = ? AND m.deleted_at IS NULL
           ORDER BY p.pinned_at DESC""",
        (channel_id,),
    ) as cursor:
        rows = await cursor.fetchall()
        description = cursor.description
    return [_parse(row, description) for row in rows]
```

Add `PIN_CAP_PER_CHANNEL = 50` as a module-level constant near the top of the file.

- [ ] **Step 4: Run tests to pass**

Run: `PYTHONPATH=. pytest tests/test_chat_pins.py -v`
Expected: 5 pass.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/message_store.py tests/test_chat_pins.py
git commit -m "feat(chat): pin/unpin/list-pins store methods with 50-pin cap"
```

---

## Task 3: `rewind_read_cursor` in channel_store

**Files:**
- Modify: `tinyagentos/chat/channel_store.py`
- Test: `tests/test_chat_mark_unread.py` (new, store-level section)

- [ ] **Step 1: Write failing test**

Create `tests/test_chat_mark_unread.py`:

```python
import pytest
import time
from tinyagentos.chat.channel_store import ChatChannelStore


@pytest.mark.asyncio
async def test_rewind_read_cursor_sets_last_read_at(tmp_path):
    store = ChatChannelStore(tmp_path / "channels.db")
    await store.init()
    await store.mark_read("jay", "c1", message_id="m5", ts=1000.0)
    # Rewind to before_ts=500
    await store.rewind_read_cursor("jay", "c1", before_ts=500.0)
    # Unread counts should reflect the rewind — since last_read_at is now
    # before anything, but we need a message past 500 to see effect.
    # Verify the column directly:
    async with store._db.execute(
        "SELECT last_read_at FROM chat_read_positions WHERE user_id = ? AND channel_id = ?",
        ("jay", "c1"),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 500.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_mark_unread.py::test_rewind_read_cursor_sets_last_read_at -v`
Expected: FAIL — `rewind_read_cursor` not defined.

- [ ] **Step 3: Add the method to `ChatChannelStore`**

Find the existing `mark_read` method in `tinyagentos/chat/channel_store.py` (around line 260-275). Add right after it:

```python
async def rewind_read_cursor(self, user_id: str, channel_id: str, before_ts: float) -> None:
    """Set the user's last_read_at for this channel to `before_ts`. Used for
    mark-unread: UI passes msg.created_at - epsilon to make everything from
    that message forward count as unread.
    Upserts the position row if it doesn't exist."""
    await self._db.execute(
        """INSERT INTO chat_read_positions (user_id, channel_id, last_read_message_id, last_read_at)
           VALUES (?, ?, '', ?)
           ON CONFLICT(user_id, channel_id) DO UPDATE SET last_read_at = excluded.last_read_at""",
        (user_id, channel_id, before_ts),
    )
    await self._db.commit()
```

Confirm `chat_read_positions` has a composite unique constraint on `(user_id, channel_id)`. If not, adjust the upsert accordingly. Most likely it does — look at the schema in the same file.

- [ ] **Step 4: Run test**

Run: `PYTHONPATH=. pytest tests/test_chat_mark_unread.py::test_rewind_read_cursor_sets_last_read_at -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/channel_store.py tests/test_chat_mark_unread.py
git commit -m "feat(chat): rewind_read_cursor for mark-unread"
```

---

## Task 4: `GET /api/chat/channels/{id}/pins` endpoint

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_pins.py` (extend with HTTP test)

- [ ] **Step 1: Write failing test**

Append to `tests/test_chat_pins.py`:

```python
import yaml
from httpx import AsyncClient, ASGITransport


def _make_pins_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


async def _authed_pins_client(tmp_path):
    app = _make_pins_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )
    return app, client


@pytest.mark.asyncio
async def test_get_pins_endpoint(tmp_path):
    app, client = await _authed_pins_client(tmp_path)
    async with client:
        # Seed a channel and a pinned message
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "pin me", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        await app.state.chat_messages.pin_message(ch_id, msg_id, pinned_by="user:admin")
        # HTTP GET
        r = await client.get(f"/api/chat/channels/{ch_id}/pins")
        assert r.status_code == 200
        body = r.json()
        assert "pins" in body
        assert len(body["pins"]) == 1
        assert body["pins"][0]["id"] == msg_id
        assert body["pins"][0]["pinned_by"] == "user:admin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_pins.py::test_get_pins_endpoint -v`
Expected: FAIL with 404 (route not defined).

- [ ] **Step 3: Add endpoint**

In `tinyagentos/routes/chat.py`, near the existing `get_thread_messages_endpoint` (around line 425-431), add:

```python
@router.get("/api/chat/channels/{channel_id}/pins")
async def get_channel_pins(channel_id: str, request: Request):
    store = request.app.state.chat_messages
    pins = await store.get_pins(channel_id)
    return JSONResponse({"pins": pins})
```

- [ ] **Step 4: Run test**

Run: `PYTHONPATH=. pytest tests/test_chat_pins.py::test_get_pins_endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_pins.py
git commit -m "feat(chat): GET /channels/{id}/pins endpoint"
```

---

## Task 5: `POST/DELETE /api/chat/messages/{id}/pin` endpoints (human-only + cap)

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_pins.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_pins.py`:

```python
@pytest.mark.asyncio
async def test_pin_endpoint_success(tmp_path):
    app, client = await _authed_pins_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "pin me", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r = await client.post(f"/api/chat/messages/{msg_id}/pin")
        assert r.status_code == 200, r.json()
        # Now listed in pins
        pins_r = await client.get(f"/api/chat/channels/{ch_id}/pins")
        assert len(pins_r.json()["pins"]) == 1


@pytest.mark.asyncio
async def test_unpin_endpoint(tmp_path):
    app, client = await _authed_pins_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        await app.state.chat_messages.pin_message(ch_id, msg_id, pinned_by="user:admin")
        r = await client.delete(f"/api/chat/messages/{msg_id}/pin")
        assert r.status_code == 204
        pins_r = await client.get(f"/api/chat/channels/{ch_id}/pins")
        assert pins_r.json()["pins"] == []


@pytest.mark.asyncio
async def test_pin_nonexistent_message_returns_404(tmp_path):
    app, client = await _authed_pins_client(tmp_path)
    async with client:
        r = await client.post("/api/chat/messages/nonexistent/pin")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_pin_cap_returns_409(tmp_path):
    app, client = await _authed_pins_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        # Pre-seed 50 pins via the store
        for i in range(50):
            m = await app.state.chat_messages.send_message(
                channel_id=ch_id, author_id="user", author_type="user", content=f"m{i}",
            )
            await app.state.chat_messages.pin_message(ch_id, m["id"], pinned_by="user:admin")
        # 51st via endpoint
        m51 = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="user", author_type="user", content="m51",
        )
        r = await client.post(f"/api/chat/messages/{m51['id']}/pin")
        assert r.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_pins.py -v -k "pin_endpoint or unpin or nonexistent or cap"`
Expected: FAIL (routes not defined).

- [ ] **Step 3: Add endpoints**

In `tinyagentos/routes/chat.py`, near the reactions endpoints (around line 650):

```python
@router.post("/api/chat/messages/{message_id}/pin")
async def pin_message_endpoint(message_id: str, request: Request):
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None or msg.get("deleted_at"):
        return JSONResponse({"error": "message not found"}, status_code=404)
    # TODO-AUTH: In Phase 2b-2a we check via the auth session; if the current
    # principal is an agent, return 403. For now use author_type fallback.
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.find_user_by_session(token) if hasattr(auth, "find_user_by_session") else None
    pinned_by = f"user:{session_user['id']}" if session_user else "user:unknown"
    try:
        await msg_store.pin_message(msg["channel_id"], message_id, pinned_by=pinned_by)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    # Clear pin_requested flag if it was set
    meta = msg.get("metadata") or {}
    if meta.get("pin_requested"):
        meta.pop("pin_requested", None)
        await msg_store.set_metadata(message_id, meta)
    # Broadcast pin event
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "pin", "seq": hub.next_seq(),
        "channel_id": msg["channel_id"], "message_id": message_id, "pinned_by": pinned_by,
    })
    return JSONResponse({"ok": True, "pinned_by": pinned_by})


@router.delete("/api/chat/messages/{message_id}/pin")
async def unpin_message_endpoint(message_id: str, request: Request):
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    ok = await msg_store.unpin_message(msg["channel_id"], message_id)
    if not ok:
        return JSONResponse({"error": "message not pinned"}, status_code=404)
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "unpin", "seq": hub.next_seq(),
        "channel_id": msg["channel_id"], "message_id": message_id,
    })
    return Response(status_code=204)
```

Add `from starlette.responses import Response` at the top of `routes/chat.py` if not already imported. Check existing imports — if `JSONResponse` comes from `starlette.responses`, `Response` is in the same module.

- [ ] **Step 4: Add `set_metadata` helper to message_store.py**

Since the pin endpoint clears `pin_requested`, add this in `tinyagentos/chat/message_store.py` after `edit_message`:

```python
async def set_metadata(self, message_id: str, metadata: dict) -> None:
    await self._db.execute(
        "UPDATE chat_messages SET metadata = ? WHERE id = ?",
        (json.dumps(metadata), message_id),
    )
    await self._db.commit()
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=. pytest tests/test_chat_pins.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/routes/chat.py tinyagentos/chat/message_store.py tests/test_chat_pins.py
git commit -m "feat(chat): POST/DELETE /messages/{id}/pin endpoints with cap + broadcast"
```

---

## Task 6: `PATCH /api/chat/messages/{id}` (edit, author-only, text-only)

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_edit_delete.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_chat_edit_delete.py`:

```python
import pytest
import yaml
from httpx import AsyncClient, ASGITransport


def _make_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


async def _authed_client(tmp_path, username="admin"):
    app = _make_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user(username, f"{username} Name", "", "testpass")
    rec = app.state.auth.find_user(username)
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )
    return app, client, rec


@pytest.mark.asyncio
async def test_edit_own_message_sets_edited_at(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "v1", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r = await client.patch(
            f"/api/chat/messages/{msg_id}",
            json={"content": "v2"},
        )
        assert r.status_code == 200, r.json()
        assert r.json()["content"] == "v2"
        assert r.json()["edited_at"] is not None


@pytest.mark.asyncio
async def test_edit_non_own_returns_403(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        # Post as a DIFFERENT author
        m = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="tom", author_type="agent", content="tom's",
        )
        r = await client.patch(f"/api/chat/messages/{m['id']}", json={"content": "hacked"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_edit_rejects_non_content_fields(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        r = await client.patch(
            f"/api/chat/messages/{m_r.json()['id']}",
            json={"content": "ok", "thread_id": "evil"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_edit_deleted_message_returns_404(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        await app.state.chat_messages.soft_delete_message(msg_id)
        r = await client.patch(f"/api/chat/messages/{msg_id}", json={"content": "y"})
        assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_edit_delete.py -v -k "edit"`
Expected: FAIL with 405 (PATCH not defined).

- [ ] **Step 3: Add PATCH endpoint**

In `tinyagentos/routes/chat.py`, near the new pin endpoints:

```python
@router.patch("/api/chat/messages/{message_id}")
async def edit_message_endpoint(message_id: str, request: Request):
    body = await request.json()
    # Reject fields other than content
    allowed = {"content"}
    if set(body.keys()) - allowed:
        return JSONResponse(
            {"error": "only 'content' may be edited"}, status_code=400,
        )
    if "content" not in body or not isinstance(body["content"], str):
        return JSONResponse({"error": "content required"}, status_code=400)
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None or msg.get("deleted_at"):
        return JSONResponse({"error": "message not found"}, status_code=404)
    # Author check
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.find_user_by_session(token) if hasattr(auth, "find_user_by_session") else None
    caller_id = session_user["id"] if session_user else None
    if msg["author_id"] != caller_id:
        return JSONResponse({"error": "not the author"}, status_code=403)
    await msg_store.edit_message(message_id, body["content"])
    updated = await msg_store.get_message(message_id)
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "message_update", "seq": hub.next_seq(), **updated,
    })
    return JSONResponse(updated)
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_chat_edit_delete.py -v -k "edit"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_edit_delete.py
git commit -m "feat(chat): PATCH /messages/{id} for author-only text edits"
```

---

## Task 7: `DELETE /api/chat/messages/{id}` (soft delete, author-only, idempotent)

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_edit_delete.py` (extend)

- [ ] **Step 1: Write failing tests**

Append:

```python
@pytest.mark.asyncio
async def test_delete_own_returns_204_and_sets_deleted_at(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "bye", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r = await client.delete(f"/api/chat/messages/{msg_id}")
        assert r.status_code == 204
        # Re-fetch — row still exists but with deleted_at
        got = await app.state.chat_messages.get_message(msg_id)
        assert got["deleted_at"] is not None


@pytest.mark.asyncio
async def test_delete_non_own_returns_403(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="tom", author_type="agent", content="tom's",
        )
        r = await client.delete(f"/api/chat/messages/{m['id']}")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_idempotent(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r1 = await client.delete(f"/api/chat/messages/{msg_id}")
        r2 = await client.delete(f"/api/chat/messages/{msg_id}")
        assert r1.status_code == 204
        assert r2.status_code == 204  # idempotent
```

- [ ] **Step 2: Run tests**

Run: `PYTHONPATH=. pytest tests/test_chat_edit_delete.py -v -k "delete"`
Expected: FAIL.

- [ ] **Step 3: Add DELETE endpoint**

```python
@router.delete("/api/chat/messages/{message_id}")
async def delete_message_endpoint(message_id: str, request: Request):
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    # Author check
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.find_user_by_session(token) if hasattr(auth, "find_user_by_session") else None
    caller_id = session_user["id"] if session_user else None
    if msg["author_id"] != caller_id:
        return JSONResponse({"error": "not the author"}, status_code=403)
    await msg_store.soft_delete_message(message_id)
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "message_delete", "seq": hub.next_seq(),
        "channel_id": msg["channel_id"], "message_id": message_id,
    })
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_chat_edit_delete.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_edit_delete.py
git commit -m "feat(chat): DELETE /messages/{id} for author-only soft delete"
```

---

## Task 8: `POST /api/chat/channels/{id}/read-cursor/rewind`

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_mark_unread.py` (extend with HTTP section)

- [ ] **Step 1: Write failing test**

Append to `tests/test_chat_mark_unread.py`:

```python
import yaml
from httpx import AsyncClient, ASGITransport


def _make_unread_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


async def _authed_unread_client(tmp_path):
    app = _make_unread_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )
    return app, client, rec


@pytest.mark.asyncio
async def test_rewind_endpoint_sets_cursor(tmp_path):
    app, client, rec = await _authed_unread_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m1 = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="user", author_type="user", content="m1",
        )
        m2 = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="user", author_type="user", content="m2",
        )
        # Mark all read first
        await app.state.chat_channels.mark_read(rec["id"], ch_id, m2["id"], m2["created_at"])
        # Rewind to before m2
        r = await client.post(
            f"/api/chat/channels/{ch_id}/read-cursor/rewind",
            json={"before_message_id": m2["id"]},
        )
        assert r.status_code == 200
        # Now unread count should be 1 (m2 alone)
        counts = await app.state.chat_channels.get_unread_counts(rec["id"])
        assert counts.get(ch_id, 0) >= 1


@pytest.mark.asyncio
async def test_rewind_unknown_message_returns_404(tmp_path):
    app, client, rec = await _authed_unread_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        r = await client.post(
            f"/api/chat/channels/{ch_id}/read-cursor/rewind",
            json={"before_message_id": "nonexistent"},
        )
        assert r.status_code == 404
```

- [ ] **Step 2: Run test**

Run: `PYTHONPATH=. pytest tests/test_chat_mark_unread.py::test_rewind_endpoint_sets_cursor -v`
Expected: FAIL.

- [ ] **Step 3: Add endpoint**

In `tinyagentos/routes/chat.py`:

```python
@router.post("/api/chat/channels/{channel_id}/read-cursor/rewind")
async def rewind_read_cursor_endpoint(channel_id: str, request: Request):
    body = await request.json()
    before_id = body.get("before_message_id")
    if not before_id:
        return JSONResponse({"error": "before_message_id required"}, status_code=400)
    msg_store = request.app.state.chat_messages
    ch_store = request.app.state.chat_channels
    msg = await msg_store.get_message(before_id)
    if msg is None or msg["channel_id"] != channel_id:
        return JSONResponse({"error": "message not in channel"}, status_code=404)
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.find_user_by_session(token) if hasattr(auth, "find_user_by_session") else None
    if session_user is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    await ch_store.rewind_read_cursor(session_user["id"], channel_id, msg["created_at"] - 0.001)
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_chat_mark_unread.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_mark_unread.py
git commit -m "feat(chat): POST /channels/{id}/read-cursor/rewind for mark-unread"
```

---

## Task 9: Agent 📌 self-reaction → `pin_requested` flag

**Files:**
- Modify: `tinyagentos/chat/reactions.py` (or wherever the reaction semantic dispatcher lives — check repo)
- Test: `tests/test_chat_pin_request.py` (new)

The existing dispatcher in `tinyagentos/chat/reactions.py` is `maybe_trigger_semantic` with signature:

```python
async def maybe_trigger_semantic(
    *, emoji: str, message: dict, reactor_id: str, reactor_type: str,
    channel: dict, state,
) -> None
```

It reads `state.chat_messages`, `state.bridge_sessions`, `state.wants_reply` via `getattr(state, ..., None)`. We add a 📌 branch alongside the 👎 and 🙋 branches.

- [ ] **Step 1: Write failing test**

Create `tests/test_chat_pin_request.py`:

```python
import pytest
from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.reactions import maybe_trigger_semantic


class _FakeState:
    def __init__(self, store):
        self.chat_messages = store


@pytest.mark.asyncio
async def test_agent_pin_own_message_sets_flag(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="see this",
    )
    await maybe_trigger_semantic(
        emoji="📌", message=msg,
        reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=_FakeState(store),
    )
    updated = await store.get_message(msg["id"])
    assert updated["metadata"].get("pin_requested") is True


@pytest.mark.asyncio
async def test_agent_pin_other_message_does_not_set_flag(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="don", author_type="agent", content="don's msg",
    )
    await maybe_trigger_semantic(
        emoji="📌", message=msg,
        reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=_FakeState(store),
    )
    updated = await store.get_message(msg["id"])
    assert updated["metadata"].get("pin_requested") is None


@pytest.mark.asyncio
async def test_human_pin_reaction_does_not_set_flag(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="x",
    )
    await maybe_trigger_semantic(
        emoji="📌", message=msg,
        reactor_id="jay", reactor_type="user",
        channel={"id": "c1"}, state=_FakeState(store),
    )
    updated = await store.get_message(msg["id"])
    assert updated["metadata"].get("pin_requested") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_pin_request.py -v`
Expected: FAIL.

- [ ] **Step 3: Extend the reactions dispatcher**

In `tinyagentos/chat/reactions.py`, add a new branch after the existing 🙋 branch (around line 85):

```python
    if emoji == "📌" and reactor_type == "agent" and reactor_id == message.get("author_id"):
        msg_store = getattr(state, "chat_messages", None)
        if msg_store is None:
            return
        meta = dict(message.get("metadata") or {})
        meta["pin_requested"] = True
        await msg_store.set_metadata(message["id"], meta)
        return
```

The branch order doesn't matter (each uses early `return`), but keep it at the bottom of the function next to 🙋.

- [ ] **Step 4: Run tests to pass**

Run: `PYTHONPATH=. pytest tests/test_chat_pin_request.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/reactions.py tests/test_chat_pin_request.py
git commit -m "feat(chat): 📌 by agent on own message sets metadata.pin_requested"
```

---

## Task 10: `chat-messages-api.ts` frontend client

**Files:**
- Create: `desktop/src/lib/chat-messages-api.ts`
- Test: `desktop/src/lib/__tests__/chat-messages-api.test.ts` (new)

- [ ] **Step 1: Write failing tests**

Create `desktop/src/lib/__tests__/chat-messages-api.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  pinMessage, unpinMessage, listPins,
  editMessage, deleteMessage, markUnread,
} from "../chat-messages-api";

describe("chat-messages-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    ) as unknown as typeof fetch;
  });

  it("pinMessage POSTs /messages/{id}/pin", async () => {
    await pinMessage("m1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1/pin",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("unpinMessage DELETEs", async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 204 })) as unknown as typeof fetch;
    await unpinMessage("m1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1/pin",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("listPins GETs and returns pins", async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({ pins: [{ id: "m1" }] }),
    })) as unknown as typeof fetch;
    const pins = await listPins("c1");
    expect(fetch).toHaveBeenCalledWith("/api/chat/channels/c1/pins");
    expect(pins).toEqual([{ id: "m1" }]);
  });

  it("editMessage PATCHes with content", async () => {
    await editMessage("m1", "new text");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ content: "new text" }),
      }),
    );
  });

  it("deleteMessage DELETEs", async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 204 })) as unknown as typeof fetch;
    await deleteMessage("m1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/messages/m1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("markUnread POSTs rewind endpoint", async () => {
    await markUnread("c1", "m2");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1/read-cursor/rewind",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ before_message_id: "m2" }),
      }),
    );
  });

  it("throws on non-OK with server error", async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 409,
      json: () => Promise.resolve({ error: "pin cap (50) reached" }),
    })) as unknown as typeof fetch;
    await expect(pinMessage("m1")).rejects.toThrow("pin cap");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npm test -- --run chat-messages-api`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `desktop/src/lib/chat-messages-api.ts`:

```typescript
async function _ensureOk(r: Response): Promise<void> {
  if (r.ok) return;
  let body: { error?: string } | null = null;
  try { body = await r.json(); } catch { /* ignore */ }
  throw new Error(body?.error || `HTTP ${r.status}`);
}

export async function pinMessage(messageId: string): Promise<void> {
  const r = await fetch(`/api/chat/messages/${messageId}/pin`, { method: "POST" });
  await _ensureOk(r);
}

export async function unpinMessage(messageId: string): Promise<void> {
  const r = await fetch(`/api/chat/messages/${messageId}/pin`, { method: "DELETE" });
  await _ensureOk(r);
}

export async function listPins(channelId: string): Promise<unknown[]> {
  const r = await fetch(`/api/chat/channels/${channelId}/pins`);
  await _ensureOk(r);
  const body = await r.json();
  return body.pins || [];
}

export async function editMessage(messageId: string, content: string): Promise<unknown> {
  const r = await fetch(`/api/chat/messages/${messageId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  await _ensureOk(r);
  return r.json();
}

export async function deleteMessage(messageId: string): Promise<void> {
  const r = await fetch(`/api/chat/messages/${messageId}`, { method: "DELETE" });
  await _ensureOk(r);
}

export async function markUnread(channelId: string, beforeMessageId: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${channelId}/read-cursor/rewind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ before_message_id: beforeMessageId }),
  });
  await _ensureOk(r);
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run chat-messages-api`
Expected: 7 pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/lib/chat-messages-api.ts desktop/src/lib/__tests__/chat-messages-api.test.ts
git commit -m "feat(desktop): chat-messages-api client (pin/unpin/edit/delete/mark-unread)"
```

---

## Task 11: `MessageOverflowMenu` component

**Files:**
- Create: `desktop/src/apps/chat/MessageOverflowMenu.tsx`
- Test: `desktop/src/apps/chat/__tests__/MessageOverflowMenu.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `desktop/src/apps/chat/__tests__/MessageOverflowMenu.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageOverflowMenu } from "../MessageOverflowMenu";

describe("MessageOverflowMenu", () => {
  it("shows Edit+Delete when isOwn is true", () => {
    render(
      <MessageOverflowMenu
        isOwn={true}
        isHuman={true}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onCopyLink={vi.fn()}
        onPin={vi.fn()}
        onMarkUnread={vi.fn()}
      />,
    );
    expect(screen.getByRole("menuitem", { name: /Edit/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Delete/i })).toBeInTheDocument();
  });

  it("hides Edit+Delete when isOwn is false", () => {
    render(
      <MessageOverflowMenu
        isOwn={false}
        isHuman={true}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onCopyLink={vi.fn()}
        onPin={vi.fn()}
        onMarkUnread={vi.fn()}
      />,
    );
    expect(screen.queryByRole("menuitem", { name: /Edit/i })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /Delete/i })).toBeNull();
  });

  it("hides Pin when isHuman is false", () => {
    render(
      <MessageOverflowMenu
        isOwn={false}
        isHuman={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onCopyLink={vi.fn()}
        onPin={vi.fn()}
        onMarkUnread={vi.fn()}
      />,
    );
    expect(screen.queryByRole("menuitem", { name: /Pin/i })).toBeNull();
  });

  it("fires onEdit when Edit is clicked", () => {
    const onEdit = vi.fn();
    render(
      <MessageOverflowMenu
        isOwn={true}
        isHuman={true}
        onEdit={onEdit}
        onDelete={vi.fn()}
        onCopyLink={vi.fn()}
        onPin={vi.fn()}
        onMarkUnread={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("menuitem", { name: /Edit/i }));
    expect(onEdit).toHaveBeenCalled();
  });

  it("shows Unpin when isPinned is true", () => {
    render(
      <MessageOverflowMenu
        isOwn={false}
        isHuman={true}
        isPinned={true}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onCopyLink={vi.fn()}
        onPin={vi.fn()}
        onMarkUnread={vi.fn()}
      />,
    );
    expect(screen.getByRole("menuitem", { name: /Unpin/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /^Pin$/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npm test -- --run MessageOverflowMenu`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `desktop/src/apps/chat/MessageOverflowMenu.tsx`:

```tsx
export interface MessageOverflowMenuProps {
  isOwn: boolean;
  isHuman: boolean;
  isPinned?: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onCopyLink: () => void;
  onPin: () => void;
  onMarkUnread: () => void;
}

export function MessageOverflowMenu({
  isOwn,
  isHuman,
  isPinned = false,
  onEdit,
  onDelete,
  onCopyLink,
  onPin,
  onMarkUnread,
}: MessageOverflowMenuProps) {
  return (
    <div
      role="menu"
      aria-label="Message overflow menu"
      className="bg-shell-surface border border-white/10 rounded-md shadow-lg py-1 min-w-[160px] text-sm"
    >
      {isOwn && (
        <button role="menuitem" onClick={onEdit}
          className="block w-full text-left px-3 py-1.5 hover:bg-white/5">Edit</button>
      )}
      {isOwn && (
        <button role="menuitem" onClick={onDelete}
          className="block w-full text-left px-3 py-1.5 hover:bg-white/5 text-red-300">Delete</button>
      )}
      <button role="menuitem" onClick={onCopyLink}
        className="block w-full text-left px-3 py-1.5 hover:bg-white/5">Copy link</button>
      {isHuman && (
        <button role="menuitem" onClick={onPin}
          className="block w-full text-left px-3 py-1.5 hover:bg-white/5">
          {isPinned ? "Unpin" : "Pin"}
        </button>
      )}
      <button role="menuitem" onClick={onMarkUnread}
        className="block w-full text-left px-3 py-1.5 hover:bg-white/5">Mark unread</button>
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run MessageOverflowMenu`
Expected: 5 pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/chat/MessageOverflowMenu.tsx desktop/src/apps/chat/__tests__/MessageOverflowMenu.test.tsx
git commit -m "feat(desktop): MessageOverflowMenu dropdown (Edit/Delete/Copy link/Pin/Mark unread)"
```

---

## Task 12: `MessageEditor` + `MessageTombstone`

**Files:**
- Create: `desktop/src/apps/chat/MessageEditor.tsx`
- Create: `desktop/src/apps/chat/MessageTombstone.tsx`
- Test: `desktop/src/apps/chat/__tests__/MessageEditor.test.tsx`
- Test: `desktop/src/apps/chat/__tests__/MessageTombstone.test.tsx`

- [ ] **Step 1: Tests**

`MessageEditor.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageEditor } from "../MessageEditor";

describe("MessageEditor", () => {
  it("renders with initial text", () => {
    render(<MessageEditor initial="hi" onSave={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("textbox")).toHaveValue("hi");
  });

  it("Enter triggers save with trimmed text", () => {
    const onSave = vi.fn();
    render(<MessageEditor initial="hi" onSave={onSave} onCancel={vi.fn()} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "updated " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSave).toHaveBeenCalledWith("updated");
  });

  it("Esc triggers cancel", () => {
    const onCancel = vi.fn();
    render(<MessageEditor initial="hi" onSave={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("Shift+Enter inserts newline (does not save)", () => {
    const onSave = vi.fn();
    render(<MessageEditor initial="hi" onSave={onSave} onCancel={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", shiftKey: true });
    expect(onSave).not.toHaveBeenCalled();
  });
});
```

`MessageTombstone.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageTombstone } from "../MessageTombstone";

describe("MessageTombstone", () => {
  it("renders deleted message notice", () => {
    render(<MessageTombstone />);
    expect(screen.getByText(/deleted/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd desktop && npm test -- --run MessageEditor`
Run: `cd desktop && npm test -- --run MessageTombstone`
Expected: FAIL.

- [ ] **Step 3: Implement**

`MessageEditor.tsx`:

```tsx
import { useState } from "react";

export function MessageEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (content: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <textarea
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
        else if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          const trimmed = value.trim();
          if (trimmed) onSave(trimmed);
          else onCancel();
        }
      }}
      aria-label="Edit message"
      rows={1}
      className="w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-sm"
    />
  );
}
```

`MessageTombstone.tsx`:

```tsx
export function MessageTombstone() {
  return (
    <span className="text-white/40 italic text-sm">This message was deleted</span>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run MessageEditor`
Run: `cd desktop && npm test -- --run MessageTombstone`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/chat/MessageEditor.tsx desktop/src/apps/chat/MessageTombstone.tsx \
        desktop/src/apps/chat/__tests__/MessageEditor.test.tsx \
        desktop/src/apps/chat/__tests__/MessageTombstone.test.tsx
git commit -m "feat(desktop): MessageEditor (inline edit) + MessageTombstone"
```

---

## Task 13: `PinBadge` + `PinnedMessagesPopover`

**Files:**
- Create: `desktop/src/apps/chat/PinBadge.tsx`
- Create: `desktop/src/apps/chat/PinnedMessagesPopover.tsx`
- Tests: `desktop/src/apps/chat/__tests__/PinBadge.test.tsx`, `PinnedMessagesPopover.test.tsx`

- [ ] **Step 1: Tests**

`PinBadge.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PinBadge } from "../PinBadge";

describe("PinBadge", () => {
  it("renders null when count is 0", () => {
    const { container } = render(<PinBadge count={0} onClick={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders count when > 0", () => {
    render(<PinBadge count={3} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveTextContent("3");
  });

  it("fires onClick", () => {
    const onClick = vi.fn();
    render(<PinBadge count={1} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalled();
  });
});
```

`PinnedMessagesPopover.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PinnedMessagesPopover } from "../PinnedMessagesPopover";

describe("PinnedMessagesPopover", () => {
  it("shows empty state when pins is []", () => {
    render(<PinnedMessagesPopover pins={[]} onJumpTo={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/no pinned messages/i)).toBeInTheDocument();
  });

  it("renders a pinned message", () => {
    const pins = [{
      id: "m1", author_id: "tom", content: "important",
      created_at: 123, pinned_by: "user:jay", pinned_at: 200,
    }];
    render(<PinnedMessagesPopover pins={pins} onJumpTo={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("important")).toBeInTheDocument();
  });

  it("fires onJumpTo with message id", () => {
    const onJumpTo = vi.fn();
    const pins = [{ id: "m1", author_id: "tom", content: "x", created_at: 123, pinned_by: "u", pinned_at: 200 }];
    render(<PinnedMessagesPopover pins={pins} onJumpTo={onJumpTo} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Jump to/i }));
    expect(onJumpTo).toHaveBeenCalledWith("m1");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npm test -- --run PinBadge`
Run: `cd desktop && npm test -- --run PinnedMessagesPopover`
Expected: FAIL.

- [ ] **Step 3: Implement**

`PinBadge.tsx`:

```tsx
export function PinBadge({ count, onClick }: { count: number; onClick: () => void }) {
  if (count === 0) return null;
  return (
    <button
      onClick={onClick}
      className="ml-1 px-1.5 py-0.5 text-xs bg-white/5 hover:bg-white/10 rounded opacity-70 hover:opacity-100"
      aria-label={`Pinned messages (${count})`}
    >📌 {count}</button>
  );
}
```

`PinnedMessagesPopover.tsx`:

```tsx
export interface PinnedMessage {
  id: string;
  author_id: string;
  content: string;
  created_at: number;
  pinned_by: string;
  pinned_at: number;
}

export function PinnedMessagesPopover({
  pins,
  onJumpTo,
  onClose,
}: {
  pins: PinnedMessage[];
  onJumpTo: (messageId: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-label="Pinned messages"
      className="absolute top-full right-0 mt-1 w-[320px] max-h-[400px] overflow-y-auto bg-shell-surface border border-white/10 rounded-md shadow-lg z-40"
    >
      <header className="flex items-center justify-between px-3 py-2 border-b border-white/10">
        <span className="text-xs font-semibold">Pinned ({pins.length})</span>
        <button onClick={onClose} aria-label="Close" className="text-sm opacity-70 hover:opacity-100">×</button>
      </header>
      {pins.length === 0 ? (
        <div className="p-4 text-sm text-white/50">No pinned messages yet.</div>
      ) : (
        <ul className="divide-y divide-white/5">
          {pins.map((p) => (
            <li key={p.id} className="p-2 text-sm">
              <div className="text-xs opacity-60 mb-0.5">@{p.author_id}</div>
              <div className="line-clamp-2">{p.content}</div>
              <button
                onClick={() => onJumpTo(p.id)}
                className="mt-1 text-xs text-sky-300 hover:text-sky-200"
              >Jump to →</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run PinBadge`
Run: `cd desktop && npm test -- --run PinnedMessagesPopover`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/chat/PinBadge.tsx desktop/src/apps/chat/PinnedMessagesPopover.tsx \
        desktop/src/apps/chat/__tests__/PinBadge.test.tsx \
        desktop/src/apps/chat/__tests__/PinnedMessagesPopover.test.tsx
git commit -m "feat(desktop): PinBadge + PinnedMessagesPopover"
```

---

## Task 14: `PinRequestAffordance`

**Files:**
- Create: `desktop/src/apps/chat/PinRequestAffordance.tsx`
- Test: `desktop/src/apps/chat/__tests__/PinRequestAffordance.test.tsx`

- [ ] **Step 1: Test**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PinRequestAffordance } from "../PinRequestAffordance";

describe("PinRequestAffordance", () => {
  it("calls onApprove when clicked", () => {
    const onApprove = vi.fn();
    render(<PinRequestAffordance authorId="tom" onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: /Pin this/i }));
    expect(onApprove).toHaveBeenCalled();
  });

  it("shows author name in label", () => {
    render(<PinRequestAffordance authorId="tom" onApprove={vi.fn()} />);
    expect(screen.getByText(/tom/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test**

Run: `cd desktop && npm test -- --run PinRequestAffordance`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
export function PinRequestAffordance({
  authorId,
  onApprove,
}: {
  authorId: string;
  onApprove: () => void;
}) {
  return (
    <div className="mt-1 flex items-center gap-2 text-xs">
      <span className="text-white/60">@{authorId} wants to pin this</span>
      <button
        onClick={onApprove}
        className="px-2 py-0.5 bg-sky-500/20 text-sky-200 rounded hover:bg-sky-500/30"
        aria-label={`Pin this message from ${authorId}`}
      >📌 Pin this</button>
    </div>
  );
}
```

- [ ] **Step 4: Run test**

Run: `cd desktop && npm test -- --run PinRequestAffordance`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/chat/PinRequestAffordance.tsx \
        desktop/src/apps/chat/__tests__/PinRequestAffordance.test.tsx
git commit -m "feat(desktop): PinRequestAffordance inline pill for agent 📌 requests"
```

---

## Task 15: Extend `MessageHoverActions` to include ⋯ overflow button

**Files:**
- Modify: `desktop/src/apps/chat/MessageHoverActions.tsx`
- Test: existing `MessageHoverActions.test.tsx` (extend)

The current component renders 😀 / 💬 / ⋯ buttons. The ⋯ button currently calls `onMore(event)` which the caller uses for agent-context-menu. We need to keep that behavior AND make the ⋯ able to open the MessageOverflowMenu dropdown.

Decision: change `onMore` semantics. Keep it as the single ⋯ callback; the caller (MessagesApp) will decide whether to show the overflow menu or the agent context menu based on the event target. This keeps the component simple.

Actually simpler: leave `onMore` as-is. MessagesApp responds to onMore by opening the overflow menu, and inside the overflow menu the "..." now includes agent-context-menu actions (e.g. "DM this agent") as a sub-section. But that complicates things.

Simplest: split into two buttons. Rename and add:
- 😀 → onReact (unchanged)
- 💬 → onReplyInThread (unchanged)
- ⋯ → onOverflow(event) — opens overflow menu
- Context-menu (right-click) on the message already opens the agent-context-menu via existing MessagesApp logic — that path is unchanged.

So: rename `onMore` → `onOverflow` to reflect the new semantic, and MessagesApp wires onOverflow → open overflow menu anchored at event coords.

- [ ] **Step 1: Update MessageHoverActions test**

In `desktop/src/apps/chat/__tests__/MessageHoverActions.test.tsx`, replace `onMore` with `onOverflow`:

```tsx
it("calls onOverflow when the overflow button is clicked", () => {
  const onOverflow = vi.fn();
  render(<MessageHoverActions onReact={vi.fn()} onReplyInThread={vi.fn()} onOverflow={onOverflow} />);
  fireEvent.click(screen.getByRole("button", { name: /More/i }));
  expect(onOverflow).toHaveBeenCalled();
});
```

Replace any other occurrences of `onMore` in the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && npm test -- --run MessageHoverActions`
Expected: FAIL (prop name mismatch).

- [ ] **Step 3: Rename prop**

In `desktop/src/apps/chat/MessageHoverActions.tsx`, rename `onMore` → `onOverflow` in the interface and usage:

```tsx
export function MessageHoverActions({
  onReact,
  onReplyInThread,
  onOverflow,
}: {
  onReact: () => void;
  onReplyInThread: () => void;
  onOverflow: (e: React.MouseEvent) => void;
}) {
  return (
    <div
      role="toolbar"
      aria-label="Message actions"
      className="inline-flex items-center gap-0.5 bg-shell-surface border border-white/10 rounded-md shadow-sm px-1"
    >
      <button aria-label="Add reaction" onClick={onReact} className="p-1 hover:bg-white/5">😀</button>
      <button aria-label="Reply in thread" onClick={onReplyInThread} className="p-1 hover:bg-white/5">💬</button>
      <button aria-label="More" onClick={onOverflow} className="p-1 hover:bg-white/5">⋯</button>
    </div>
  );
}
```

- [ ] **Step 4: Run test**

Run: `cd desktop && npm test -- --run MessageHoverActions`
Expected: pass.

- [ ] **Step 5: Update MessagesApp caller**

In `desktop/src/apps/MessagesApp.tsx`, find the existing `<MessageHoverActions ... onMore={...} />` usage (added in Phase 2b-1 Task 17, around the message row render). Rename the prop:

```tsx
<MessageHoverActions
  onReact={() => setShowEmoji(showEmoji === msg.id ? null : msg.id)}
  onReplyInThread={() => handleOpenThreadFor(msg.channel_id ?? selectedChannel ?? "", msg.id)}
  onOverflow={(e) => {
    e.preventDefault();
    // Task 16 wires this to the overflow menu state
  }}
/>
```

(This is a rename-only change in MessagesApp; the actual overflow-menu wiring happens in Task 16.)

- [ ] **Step 6: Verify full test + build**

Run: `cd desktop && npm test -- --run` (ensure no regressions).
Run: `cd desktop && npm run build`.
Expected: pass / clean.

- [ ] **Step 7: Commit**

```bash
git add desktop/src/apps/chat/MessageHoverActions.tsx \
        desktop/src/apps/chat/__tests__/MessageHoverActions.test.tsx \
        desktop/src/apps/MessagesApp.tsx
git commit -m "refactor(desktop): rename MessageHoverActions.onMore to onOverflow"
```

---

## Task 16: MessagesApp integration — overflow menu, tombstone, edit-in-place, pin badge, deep-link scroll

**Files:**
- Modify: `desktop/src/apps/MessagesApp.tsx`

This is the integration task. Wires up:
- Overflow menu state + callbacks
- Tombstone rendering for `deleted_at` messages
- Edit-in-place swap
- Pin badge in header + pinned popover
- Pin-request affordance inline
- Deep-link scroll on `?msg=<id>`

- [ ] **Step 1: Add imports**

Near the top of `MessagesApp.tsx`:

```tsx
import { MessageOverflowMenu } from "./chat/MessageOverflowMenu";
import { MessageEditor } from "./chat/MessageEditor";
import { MessageTombstone } from "./chat/MessageTombstone";
import { PinBadge } from "./chat/PinBadge";
import { PinnedMessagesPopover, type PinnedMessage } from "./chat/PinnedMessagesPopover";
import { PinRequestAffordance } from "./chat/PinRequestAffordance";
import {
  pinMessage, unpinMessage, listPins,
  editMessage as apiEditMessage, deleteMessage as apiDeleteMessage,
  markUnread as apiMarkUnread,
} from "@/lib/chat-messages-api";
```

- [ ] **Step 2: Add state**

Near the other state declarations:

```tsx
const [overflowMenu, setOverflowMenu] = useState<{ messageId: string; x: number; y: number } | null>(null);
const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
const [pinnedPopoverOpen, setPinnedPopoverOpen] = useState(false);
const [pinnedMessages, setPinnedMessages] = useState<PinnedMessage[]>([]);
```

Add Message interface optional fields (if not already):

```tsx
interface Message {
  // ...existing fields...
  deleted_at?: number | null;
  metadata?: { pin_requested?: boolean; [k: string]: unknown };
}
```

- [ ] **Step 3: Fetch pins when channel changes**

In the existing useEffect that fetches messages on channel change, also fetch pins:

```tsx
useEffect(() => {
  if (!selectedChannel) return;
  listPins(selectedChannel).then((pins) => {
    setPinnedMessages(pins as PinnedMessage[]);
  }).catch(() => {});
}, [selectedChannel]);
```

- [ ] **Step 4: Overflow menu handlers**

```tsx
const currentUserId = /* however we currently read the session user id, e.g. */ "admin";
// Check an existing usage in the file to pin this down.

const handleEdit = (msgId: string) => {
  setEditingMessageId(msgId);
  setOverflowMenu(null);
};

const handleSaveEdit = async (msgId: string, content: string) => {
  try {
    await apiEditMessage(msgId, content);
    setEditingMessageId(null);
  } catch (e) {
    setSendError((e as Error).message);
  }
};

const handleDelete = async (msgId: string) => {
  setOverflowMenu(null);
  if (!window.confirm("Delete this message?")) return;
  try {
    await apiDeleteMessage(msgId);
  } catch (e) {
    setSendError((e as Error).message);
  }
};

const handleCopyLink = async (msgId: string) => {
  setOverflowMenu(null);
  const url = `${window.location.origin}/chat/${selectedChannel}?msg=${msgId}`;
  try {
    await navigator.clipboard.writeText(url);
  } catch { /* ignore */ }
};

const handlePin = async (msg: Message) => {
  setOverflowMenu(null);
  const isPinned = pinnedMessages.some((p) => p.id === msg.id);
  try {
    if (isPinned) await unpinMessage(msg.id);
    else await pinMessage(msg.id);
    if (selectedChannel) {
      const pins = await listPins(selectedChannel);
      setPinnedMessages(pins as PinnedMessage[]);
    }
  } catch (e) {
    setSendError((e as Error).message);
  }
};

const handleMarkUnread = async (msgId: string) => {
  setOverflowMenu(null);
  if (!selectedChannel) return;
  try {
    await apiMarkUnread(selectedChannel, msgId);
  } catch (e) {
    setSendError((e as Error).message);
  }
};

const handlePinRequest = async (msgId: string) => {
  try {
    await pinMessage(msgId);
    if (selectedChannel) {
      const pins = await listPins(selectedChannel);
      setPinnedMessages(pins as PinnedMessage[]);
    }
  } catch (e) {
    setSendError((e as Error).message);
  }
};
```

For `currentUserId` — look for existing session-user access in MessagesApp; use whatever is already there. If not present, fetch via a `/api/auth/me` call on mount and cache.

- [ ] **Step 5: Replace message row rendering logic**

In the message row JSX (the `{messages.map((msg, i) => { ... return (...) })` block), branch on delete/edit state:

Replace the content text block (the div that renders `{renderContent(msg.content)}`) with:

```tsx
{msg.deleted_at ? (
  <MessageTombstone />
) : editingMessageId === msg.id ? (
  <MessageEditor
    initial={msg.content}
    onSave={(content) => handleSaveEdit(msg.id, content)}
    onCancel={() => setEditingMessageId(null)}
  />
) : (
  <div className={`text-[13px] leading-relaxed whitespace-pre-wrap break-words ${isDeadAgent ? "text-white/45" : "text-white/80"}`}>
    {renderContent(msg.content)}
    {msg.edited_at && <span className="ml-1 text-[11px] text-white/25">(edited)</span>}
    {/* existing state indicators (pending/streaming/error) */}
  </div>
)}
```

Keep attachment gallery, reactions, thread indicator — only the content area toggles.

Add pin-request affordance right below content:

```tsx
{msg.metadata?.pin_requested && msg.author_type === "agent" && (
  <PinRequestAffordance
    authorId={msg.author_id}
    onApprove={() => handlePinRequest(msg.id)}
  />
)}
```

Update the MessageHoverActions onOverflow handler:

```tsx
onOverflow={(e) => {
  e.preventDefault();
  setOverflowMenu({ messageId: msg.id, x: e.clientX, y: e.clientY });
}}
```

- [ ] **Step 6: Overflow menu mount**

At the root return (near the ChannelSettingsPanel mount), add:

```tsx
{overflowMenu && (() => {
  const msg = messages.find((m) => m.id === overflowMenu.messageId);
  if (!msg) return null;
  return (
    <>
      {/* click-catcher */}
      <div className="fixed inset-0 z-40" onClick={() => setOverflowMenu(null)} />
      <div
        className="fixed z-50"
        style={{ top: overflowMenu.y, left: overflowMenu.x }}
      >
        <MessageOverflowMenu
          isOwn={msg.author_id === currentUserId}
          isHuman={true}  // caller is always a human here (this is the desktop UI)
          isPinned={pinnedMessages.some((p) => p.id === msg.id)}
          onEdit={() => handleEdit(msg.id)}
          onDelete={() => handleDelete(msg.id)}
          onCopyLink={() => handleCopyLink(msg.id)}
          onPin={() => handlePin(msg)}
          onMarkUnread={() => handleMarkUnread(msg.id)}
        />
      </div>
    </>
  );
})()}
```

- [ ] **Step 7: Pin badge in header**

In the chat header (next to the ⓘ settings button and the "?" guide icon), add:

```tsx
<PinBadge
  count={pinnedMessages.length}
  onClick={() => setPinnedPopoverOpen((open) => !open)}
/>
```

Add the popover right after:

```tsx
{pinnedPopoverOpen && (
  <PinnedMessagesPopover
    pins={pinnedMessages}
    onJumpTo={(id) => {
      setPinnedPopoverOpen(false);
      const el = document.querySelector(`[data-message-id="${id}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.classList.add("data-highlight");
      setTimeout(() => el?.classList.remove("data-highlight"), 2000);
    }}
    onClose={() => setPinnedPopoverOpen(false)}
  />
)}
```

Ensure the message row `<div>` has `data-message-id={msg.id}` so the `scrollIntoView` query works. Add that attribute if missing.

- [ ] **Step 8: Deep-link scroll on ?msg=<id>**

In the existing "on channel change" useEffect or a new one, after messages load:

```tsx
useEffect(() => {
  if (!selectedChannel || messages.length === 0) return;
  const params = new URLSearchParams(window.location.search);
  const msgId = params.get("msg");
  if (!msgId) return;
  const el = document.querySelector(`[data-message-id="${msgId}"]`);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("data-highlight");
    setTimeout(() => el.classList.remove("data-highlight"), 2000);
  }
}, [selectedChannel, messages.length]);
```

Add CSS rule for `.data-highlight` — if no global CSS file is used, put inline in `desktop/src/index.css` or append to whatever is being used for existing highlights:

```css
.data-highlight {
  outline: 2px solid rgb(250 204 21 / 0.8);
  outline-offset: 2px;
  transition: outline 0.2s ease-out;
}
```

- [ ] **Step 9: Build + test**

Run: `cd desktop && npm run build`
Expected: clean.

Run: `cd desktop && npm test -- --run`
Expected: no new failures.

- [ ] **Step 10: Commit**

```bash
git add desktop/src/apps/MessagesApp.tsx desktop/src/index.css
git commit -m "feat(desktop): integrate overflow menu, edit-in-place, pin badge, deep-link scroll into MessagesApp"
```

---

## Task 17: Update `docs/chat-guide.md` with Phase 2b-2a sections

**Files:**
- Modify: `docs/chat-guide.md`

- [ ] **Step 1: Add sections**

Append (or insert in the appropriate order) these sections to `docs/chat-guide.md`:

```markdown
## Edit + delete your own messages

- Hover a message → `⋯` → **Edit** (your own text only).
- Press **Enter** to save, **Esc** to cancel. A small `(edited)` marker shows afterwards.
- `⋯` → **Delete** removes your message with a tombstone ("This message was deleted"). Thread replies remain anchored to the parent. You cannot delete others' messages.

## Pinning

- Hover a message → `⋯` → **Pin**. The channel header shows a 📌 badge with the pin count.
- Click the badge to see the pinned list. Each entry has a **Jump to** link that scrolls to the original message.
- Up to 50 pins per channel.

### Agent pin requests

- An agent can ask for its own message to be pinned by adding a 📌 reaction to it.
- Humans see a `@agent wants to pin this` pill below the message with a one-click **Pin** button.
- Only humans can actually pin; agents can only request.

## Copy link + deep links

- `⋯` → **Copy link** copies a URL in the form `https://<host>/chat/<channel>?msg=<message>` to your clipboard.
- Opening that URL in taOS scrolls to the message and briefly highlights it. Paste into email, Slack, docs — anywhere you share links.

## Mark unread

- `⋯` → **Mark unread** rewinds your read cursor to just before that message.
- The channel list updates its unread badge accordingly. No notifications are re-fired.
```

- [ ] **Step 2: Commit**

```bash
git add docs/chat-guide.md
git commit -m "docs: chat guide — edit/delete/pin/copy-link/mark-unread sections"
```

---

## Task 18: Rebuild desktop bundle

- [ ] **Step 1: Build**

```bash
cd desktop && npm run build
```

- [ ] **Step 2: Commit rebuilt assets**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
git add -A static/desktop desktop/tsconfig.tsbuildinfo
git commit -m "build: rebuild desktop bundle for chat Phase 2b-2a"
```

---

## Task 19: Playwright E2E

**Files:**
- Create: `tests/e2e/test_chat_phase2b2a.py`

- [ ] **Step 1: Write env-gated tests**

```python
"""Phase 2b-2a desktop E2E.

Requires TAOS_E2E_URL and a test channel named 'roundtable' with at least
one message the test user authored.
"""
import os
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TAOS_E2E_URL"),
        reason="TAOS_E2E_URL required",
    ),
]
URL = os.environ.get("TAOS_E2E_URL", "")


def test_edit_own_message(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first = page.locator("[data-message-id]").first
    first.hover()
    page.get_by_role("button", name="More").click()
    page.get_by_role("menuitem", name=re.compile("Edit", re.I)).click()
    editor = page.get_by_role("textbox", name=re.compile("Edit message", re.I))
    editor.fill("edited content")
    editor.press("Enter")
    expect(page.get_by_text("edited content")).to_be_visible()
    expect(page.get_by_text("(edited)")).to_be_visible()


def test_delete_own_message_shows_tombstone(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first = page.locator("[data-message-id]").first
    first.hover()
    page.get_by_role("button", name="More").click()
    page.on("dialog", lambda d: d.accept())
    page.get_by_role("menuitem", name=re.compile("Delete", re.I)).click()
    expect(page.get_by_text("This message was deleted")).to_be_visible()


def test_pin_badge_and_popover(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first = page.locator("[data-message-id]").first
    first.hover()
    page.get_by_role("button", name="More").click()
    page.get_by_role("menuitem", name=re.compile("Pin", re.I)).click()
    # Badge visible
    expect(page.get_by_role("button", name=re.compile("Pinned messages", re.I))).to_be_visible()
    # Open popover
    page.get_by_role("button", name=re.compile("Pinned messages", re.I)).click()
    expect(page.get_by_role("dialog", name=re.compile("Pinned messages", re.I))).to_be_visible()


def test_deep_link_scroll(page: Page):
    # Assume at least one message exists in roundtable
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    msg = page.locator("[data-message-id]").first
    msg_id = msg.get_attribute("data-message-id")
    channel_id_match = page.url  # may not expose channel id; use an API call or nav for real test
    # For now just verify the query-param flow doesn't error
    page.goto(f"{URL}?msg={msg_id}")
    # After reload expect the message still visible (scrolled to)
    expect(page.locator(f"[data-message-id='{msg_id}']")).to_be_visible()
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/test_chat_phase2b2a.py
git commit -m "test(e2e): chat Phase 2b-2a — edit/delete/pin/deep-link"
```

---

## Final verification

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=. pytest tests/ -x -q --ignore=tests/e2e`
Expected: no new failures vs master baseline (1 pre-existing Mac-arm hardware test failure is acceptable).

Run: `cd desktop && npm test -- --run`
Expected: no new failures (3 pre-existing snap-zones failures acceptable).

Run: `cd desktop && npm run build`
Expected: clean.

- [ ] **Step 2: Open PR**

```bash
git push -u origin feat/chat-phase-2b-2a-per-msg
gh pr create --base master \
  --title "Chat Phase 2b-2a — per-message affordances + pinning" \
  --body-file docs/superpowers/specs/2026-04-19-chat-phase-2b-2a-per-msg-design.md
```
