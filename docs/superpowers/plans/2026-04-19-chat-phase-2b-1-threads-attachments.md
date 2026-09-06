# Chat Phase 2b-1 — Threads + Attachments + Shared File Picker + Chat Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Threaded replies (right-side panel, narrow routing, per-thread policy), attachments via a reusable shell file picker (disk + user workspace + agent workspaces), and a canonical `docs/chat-guide.md` with an in-app `/help` surface.

**Architecture:** Attachments land as a new jsonb column on `chat_messages` plus a new `POST /api/chat/attachments/from-path` for VFS sources. Threads reuse the existing `thread_id` column; a new `tinyagentos/chat/threads.py` handles narrow recipient resolution (parent author + repliers + `@mentions`, with `@all` escalating channel-wide) and the router grows one branch for thread routing. `/help` is a server-side text intercept, bypassing the Phase 2a bare-slash guardrail. Frontend factors the Files-app VFS browser into a shell primitive that both the Files app and a new `SharedFilePickerDialog` consume.

**Tech Stack:** Python 3.12, FastAPI, pytest + pytest-asyncio, React + TypeScript, Vitest. Spec at `docs/superpowers/specs/2026-04-19-chat-phase-2b-1-threads-attachments-design.md`.

---

## File Structure

**New backend files:**
- `tinyagentos/chat/threads.py` — thread recipient resolver + context builder
- `tinyagentos/chat/help.py` — `/help [topic]` command handler
- `tests/test_chat_threads.py`
- `tests/test_chat_help.py`
- `tests/test_chat_attachments.py`

**New frontend files:**
- `desktop/src/shell/VfsBrowser.tsx` — factored from FilesApp (dual consumer)
- `desktop/src/shell/FilePicker.tsx` — `SharedFilePickerDialog`
- `desktop/src/shell/file-picker-api.ts` — `openFilePicker(...)`
- `desktop/src/apps/chat/MessageHoverActions.tsx` — reaction + reply-in-thread + more
- `desktop/src/apps/chat/ThreadIndicator.tsx` — "N replies · Xm ago" chip
- `desktop/src/apps/chat/ThreadPanel.tsx` — right-side thread view
- `desktop/src/apps/chat/AttachmentsBar.tsx` — pre-send thumbnails
- `desktop/src/apps/chat/AttachmentGallery.tsx` — in-message gallery
- `desktop/src/apps/chat/AttachmentLightbox.tsx` — fullscreen viewer
- `desktop/src/lib/chat-attachments-api.ts` — upload + from-path client
- `desktop/src/lib/use-thread-panel.ts` — panel state hook
- Component tests under `__tests__/`

**New docs:**
- `docs/chat-guide.md` — canonical guide (retroactive P1 + 2a + 2b-1)

**Modified backend:**
- `tinyagentos/chat/message_store.py` — `attachments` column migration, persist attachments, `get_thread_messages`
- `tinyagentos/agent_chat_router.py` — thread-aware recipient branch, per-thread policy key
- `tinyagentos/routes/chat.py` — `/help` intercept, thread-messages GET, `attachments/from-path` POST, message send accepts `attachments`
- `tinyagentos/bridge_session.py` — event payload includes `thread_id` + `attachments`
- `tinyagentos/scripts/install_{hermes,smolagents,langroid,pocketflow,openai_agents_sdk,openai-agents-sdk}.sh` — bridge `_render_context` appends attachment footer

**Modified frontend:**
- `desktop/src/apps/FilesApp.tsx` — consume shared `VfsBrowser`
- `desktop/src/apps/MessagesApp.tsx` — integrate hover actions, threads, attachments, "?" icon
- `static/desktop/**` — rebuilt bundle

---

## Task 1: Attachments column migration + message_store

**Files:**
- Modify: `tinyagentos/chat/message_store.py`
- Test: `tests/test_chat_attachments.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_attachments.py`:

```python
import json
import pytest
from tinyagentos.chat.message_store import ChatMessageStore


@pytest.mark.asyncio
async def test_send_message_persists_attachments(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    atts = [
        {"filename": "screenshot.png", "mime_type": "image/png",
         "size": 312456, "url": "/api/chat/files/abc-screenshot.png",
         "source": "disk"},
    ]
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="look", content_type="text", state="complete",
        metadata=None, attachments=atts,
    )
    assert msg["attachments"] == atts


@pytest.mark.asyncio
async def test_send_message_defaults_attachments_to_empty_list(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="plain", content_type="text", state="complete",
        metadata=None,
    )
    assert msg["attachments"] == []


@pytest.mark.asyncio
async def test_get_message_round_trips_attachments(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    atts = [{"filename": "r.pdf", "mime_type": "application/pdf",
             "size": 500, "url": "/api/chat/files/r.pdf", "source": "workspace"}]
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="see", content_type="text", state="complete",
        metadata=None, attachments=atts,
    )
    roundtripped = await store.get_message(msg["id"])
    assert roundtripped["attachments"] == atts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_attachments.py -v`
Expected: FAIL — `send_message` doesn't accept `attachments`; `attachments` column doesn't exist.

- [ ] **Step 3: Add the migration + column**

In `tinyagentos/chat/message_store.py`, in the `init()` method where the table is created, add `attachments TEXT NOT NULL DEFAULT '[]'` to the schema, and add an idempotent `ALTER TABLE` for existing databases.

Find the `CREATE TABLE IF NOT EXISTS chat_messages (...)` statement. Add `attachments TEXT NOT NULL DEFAULT '[]'` as a new column definition. Then, after the CREATE TABLE statement runs, add the migration:

```python
# Idempotent migration for databases created before Phase 2b-1.
try:
    await self._conn.execute(
        "ALTER TABLE chat_messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'"
    )
except Exception:
    pass  # column already exists
```

- [ ] **Step 4: Thread the field through send_message**

Update the `send_message` signature and body:

```python
async def send_message(
    self,
    *,
    channel_id: str,
    author_id: str,
    author_type: str,
    content: str,
    content_type: str,
    state: str,
    metadata: dict | None,
    thread_id: str | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    ...
```

In the INSERT column list, add `attachments`. In the VALUES binding, add `json.dumps(attachments or [])`. Return the message dict with `attachments` parsed.

Update `_parse` (the row-to-dict helper) to JSON-decode `attachments`, defaulting to `[]` on parse failure.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_attachments.py -v`
Expected: 3 pass.

Run: `PYTHONPATH=. pytest tests/test_chat_messages.py -v` (existing suite must stay green).
Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/chat/message_store.py tests/test_chat_attachments.py
git commit -m "feat(chat): attachments column on chat_messages + persist/parse round-trip"
```

---

## Task 2: `POST /api/chat/attachments/from-path` endpoint + ACL

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_attachments.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_attachments.py`:

```python
import os
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


@pytest.mark.asyncio
async def test_from_path_copies_workspace_file_and_returns_record(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    # seed a file in the user workspace
    ws = tmp_path / "agent-workspaces" / "user"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "report.md").write_text("# hi")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/chat/attachments/from-path",
            json={"path": "/workspaces/user/report.md", "source": "workspace"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "report.md"
        assert body["mime_type"] == "text/markdown"
        assert body["source"] == "workspace"
        assert body["url"].startswith("/api/chat/files/")
        # physical file exists
        stored_name = body["url"].rsplit("/", 1)[-1]
        assert (tmp_path / "chat-files" / stored_name).exists()


@pytest.mark.asyncio
async def test_from_path_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/chat/attachments/from-path",
            json={"path": "/workspaces/user/../../../etc/passwd", "source": "workspace"},
        )
        assert r.status_code in (400, 403)


@pytest.mark.asyncio
async def test_from_path_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    ws = tmp_path / "agent-workspaces" / "user"
    ws.mkdir(parents=True, exist_ok=True)
    big = ws / "big.bin"
    big.write_bytes(b"0" * (101 * 1024 * 1024))  # 101 MB

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/chat/attachments/from-path",
            json={"path": "/workspaces/user/big.bin", "source": "workspace"},
        )
        assert r.status_code == 413 or r.status_code == 400
        assert "too large" in r.json().get("error", "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_attachments.py -v -k "from_path"`
Expected: 3 FAIL — endpoint missing.

- [ ] **Step 3: Implement the endpoint**

In `tinyagentos/routes/chat.py`, add near the upload endpoint:

```python
import mimetypes
import secrets
import shutil
from pathlib import Path as _Path

_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024  # 100 MB


def _resolve_workspace_path(data_dir: Path, source: str, slug: str | None, vfs_path: str) -> Path:
    """Resolve a VFS path like '/workspaces/user/foo.md' to an on-disk
    absolute path under data_dir/agent-workspaces/{slug-or-user}.
    Raises ValueError on traversal or bad shape.
    """
    if not vfs_path.startswith("/workspaces/"):
        raise ValueError("path must start with /workspaces/")
    parts = vfs_path.split("/", 3)  # ['', 'workspaces', '<slug>', 'rest...']
    if len(parts) < 3 or not parts[2]:
        raise ValueError("path missing slug")
    owner = parts[2]
    if source == "agent-workspace":
        if not slug or slug != owner:
            raise ValueError("slug must match path owner for agent-workspace")
    if source == "workspace":
        if owner != "user":
            raise ValueError("workspace source requires /workspaces/user/...")
    rel = parts[3] if len(parts) > 3 else ""
    root = (data_dir / "agent-workspaces" / owner).resolve()
    target = (root / rel).resolve()
    # Traversal check: target must be inside root.
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise ValueError("path traversal rejected")
    if not target.exists() or target.is_dir():
        raise ValueError("file not found")
    return target


@router.post("/api/chat/attachments/from-path")
async def attachment_from_path(body: dict, request: Request):
    """Server-side reference to a file in a workspace. Copies into
    chat-files/ and returns the attachment record."""
    vfs_path = (body or {}).get("path")
    source = (body or {}).get("source")
    slug = (body or {}).get("slug")
    if not vfs_path or source not in ("workspace", "agent-workspace"):
        return JSONResponse({"error": "path and source in {workspace,agent-workspace} required"}, status_code=400)
    data_dir = Path(getattr(request.app.state, "data_dir", Path(os.environ.get("TAOS_DATA_DIR", "./data"))))
    try:
        src = _resolve_workspace_path(data_dir, source, slug, vfs_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if src.stat().st_size > _MAX_ATTACHMENT_BYTES:
        return JSONResponse({"error": "file too large (100 MB max)"}, status_code=413)
    chat_files = data_dir / "chat-files"
    chat_files.mkdir(parents=True, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}-{src.name}"
    dest = chat_files / stored_name
    shutil.copy2(src, dest)
    mime, _ = mimetypes.guess_type(src.name)
    return JSONResponse({
        "filename": src.name,
        "mime_type": mime or "application/octet-stream",
        "size": src.stat().st_size,
        "url": f"/api/chat/files/{stored_name}",
        "source": source,
    }, status_code=200)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_attachments.py -v`
Expected: 6 pass (3 from Task 1 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_attachments.py
git commit -m "feat(chat): POST /api/chat/attachments/from-path for workspace file refs"
```

---

## Task 3: `POST /api/chat/messages` accepts attachments[]

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_attachments.py` (extend)

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_send_message_with_attachments_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    # seed a file that /api/chat/files/ would serve
    (tmp_path / "chat-files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chat-files" / "abc-file.png").write_bytes(b"x")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={
                "channel_id": ch_id, "author_id": "user",
                "author_type": "user", "content": "here",
                "content_type": "text",
                "attachments": [
                    {"filename": "file.png", "mime_type": "image/png",
                     "size": 1, "url": "/api/chat/files/abc-file.png",
                     "source": "disk"},
                ],
            },
        )
        assert r.status_code in (200, 201)
        body = r.json()
        assert body["attachments"][0]["filename"] == "file.png"


@pytest.mark.asyncio
async def test_send_message_rejects_more_than_10_attachments(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    (tmp_path / "chat-files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chat-files" / "f.png").write_bytes(b"x")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        atts = [{"filename": "f.png", "mime_type": "image/png", "size": 1,
                 "url": "/api/chat/files/f.png", "source": "disk"}] * 11
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "overflow",
                  "content_type": "text", "attachments": atts},
        )
        assert r.status_code == 400
        assert "10" in r.json().get("error", "")


@pytest.mark.asyncio
async def test_send_message_rejects_bad_url_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "bad",
                  "content_type": "text",
                  "attachments": [
                      {"filename": "f", "mime_type": "x", "size": 1,
                       "url": "https://evil.example/f", "source": "disk"}
                  ]},
        )
        assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_attachments.py -v -k "send_message"`
Expected: 3 FAIL.

- [ ] **Step 3: Implement in routes/chat.py**

In the POST `/api/chat/messages` handler, before calling `chat_messages.send_message(...)`, extract + validate `attachments`:

```python
attachments = (body or {}).get("attachments") or []
if not isinstance(attachments, list):
    return JSONResponse({"error": "attachments must be a list"}, status_code=400)
if len(attachments) > 10:
    return JSONResponse({"error": "max 10 attachments per message"}, status_code=400)
data_dir = Path(getattr(request.app.state, "data_dir", Path(os.environ.get("TAOS_DATA_DIR", "./data"))))
chat_files = data_dir / "chat-files"
for att in attachments:
    if not isinstance(att, dict):
        return JSONResponse({"error": "each attachment must be a dict"}, status_code=400)
    url = att.get("url", "")
    if not url.startswith("/api/chat/files/"):
        return JSONResponse({"error": "attachment url must be served from /api/chat/files/"}, status_code=400)
    stored_name = url.rsplit("/", 1)[-1]
    if not (chat_files / stored_name).exists():
        return JSONResponse({"error": f"attachment file not found: {stored_name}"}, status_code=400)
```

Then pass `attachments=attachments` into the `send_message(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_attachments.py -v`
Expected: 9 pass (3 from Task 1 + 3 from Task 2 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_attachments.py
git commit -m "feat(chat): POST /api/chat/messages accepts attachments[] with validation"
```

---

## Task 4: Thread messages query + GET endpoint

**Files:**
- Modify: `tinyagentos/chat/message_store.py`
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_threads.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_threads.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app
from tinyagentos.chat.message_store import ChatMessageStore


@pytest.mark.asyncio
async def test_get_thread_messages_returns_replies_oldest_first(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    parent = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="parent", content_type="text", state="complete", metadata=None,
    )
    r1 = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="r1", content_type="text", state="complete", metadata=None,
        thread_id=parent["id"],
    )
    r2 = await store.send_message(
        channel_id="c1", author_id="don", author_type="agent",
        content="r2", content_type="text", state="complete", metadata=None,
        thread_id=parent["id"],
    )
    msgs = await store.get_thread_messages(channel_id="c1", parent_id=parent["id"], limit=20)
    assert [m["id"] for m in msgs] == [r1["id"], r2["id"]]
    # parent is NOT included
    assert all(m["id"] != parent["id"] for m in msgs)


@pytest.mark.asyncio
async def test_get_thread_messages_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        # post a parent + reply via HTTP
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "parent", "content_type": "text"},
        )
        parent_id = r.json()["id"]
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "reply", "content_type": "text",
                  "thread_id": parent_id},
        )
        assert r.status_code in (200, 201)
        r = await client.get(f"/api/chat/channels/{ch_id}/threads/{parent_id}/messages")
        assert r.status_code == 200
        body = r.json()
        assert len(body["messages"]) == 1
        assert body["messages"][0]["content"] == "reply"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_threads.py -v`
Expected: FAIL — method + endpoint missing.

- [ ] **Step 3: Add `get_thread_messages` to message_store**

In `tinyagentos/chat/message_store.py`:

```python
async def get_thread_messages(
    self, channel_id: str, parent_id: str, limit: int = 20,
) -> list[dict]:
    """Return messages in a thread (not the parent), oldest first.

    Thread replies are persisted with thread_id = parent_id.
    """
    async with self._conn.execute(
        "SELECT * FROM chat_messages "
        "WHERE channel_id = ? AND thread_id = ? "
        "ORDER BY created_at ASC LIMIT ?",
        (channel_id, parent_id, limit),
    ) as cursor:
        rows = await cursor.fetchall()
        description = cursor.description
    return [_parse(row, description) for row in rows]
```

Also update `send_message` to accept `thread_id: str | None = None` (wire into the INSERT — `thread_id` column already exists) if not already done in Task 1.

- [ ] **Step 4: Add the GET endpoint**

In `tinyagentos/routes/chat.py`:

```python
@router.get("/api/chat/channels/{channel_id}/threads/{parent_id}/messages")
async def get_thread_messages_endpoint(
    channel_id: str, parent_id: str, request: Request, limit: int = 20,
):
    store = request.app.state.chat_messages
    msgs = await store.get_thread_messages(channel_id, parent_id, limit=min(limit, 100))
    return JSONResponse({"messages": msgs})
```

Also update the POST `/api/chat/messages` handler to forward `thread_id` from the request body into `chat_messages.send_message(...)`. Find the existing `send_message` call in that handler; add `thread_id=body.get("thread_id")` to its kwargs.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_threads.py -v`
Expected: 2 pass.

Run: `PYTHONPATH=. pytest tests/test_chat_messages.py tests/test_chat_channels.py -v` — existing tests must stay green.

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/chat/message_store.py tinyagentos/routes/chat.py tests/test_chat_threads.py
git commit -m "feat(chat): get_thread_messages + GET /channels/{id}/threads/{parent}/messages"
```

---

## Task 5: `threads.py` recipient resolver

**Files:**
- Create: `tinyagentos/chat/threads.py`
- Test: `tests/test_chat_threads.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_threads.py`:

```python
from unittest.mock import AsyncMock, MagicMock
from tinyagentos.chat.threads import resolve_thread_recipients


def _ch(members, muted=None):
    return {
        "id": "c1", "type": "group", "members": members,
        "settings": {"muted": muted or []},
    }


@pytest.mark.asyncio
async def test_narrow_scope_parent_author_and_mentions():
    """parent=tom (agent), mentions @linus: recipients = {tom, linus}."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {
        "author_id": "user", "author_type": "user",
        "content": "@linus thoughts?", "thread_id": "p1",
    }
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm,
    )
    assert sorted(recipients) == ["linus", "tom"]
    assert forced["linus"] is True
    # tom (parent author) is a recipient but not force_respond unless mentioned
    assert forced.get("tom") is not True


@pytest.mark.asyncio
async def test_narrow_scope_prior_repliers():
    """Thread already has don as a replier → don is a recipient even if not mentioned."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[
        {"author_id": "don", "author_type": "agent"},
    ])
    msg = {"author_id": "user", "author_type": "user",
           "content": "more thoughts?", "thread_id": "p1"}
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm,
    )
    assert "don" in recipients


@pytest.mark.asyncio
async def test_at_all_escalates_to_all_channel_agents():
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {"author_id": "user", "author_type": "user",
           "content": "@all weigh in", "thread_id": "p1"}
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm,
    )
    assert sorted(recipients) == ["don", "linus", "tom"]
    assert all(forced[s] is True for s in recipients)


@pytest.mark.asyncio
async def test_muted_agent_excluded_from_thread():
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {"author_id": "user", "author_type": "user",
           "content": "hi", "thread_id": "p1"}
    recipients, _ = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"], muted=["tom"]), cm,
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_author_never_recipient():
    """Agent tom replies in a thread → tom is not re-notified."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[
        {"author_id": "tom", "author_type": "agent"},
    ])
    msg = {"author_id": "tom", "author_type": "agent",
           "content": "follow-up", "thread_id": "p1"}
    recipients, _ = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm,
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_user_parent_author_not_recipient():
    """Parent authored by user → parent-author rule adds nobody (user is not an agent)."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {"author_id": "user", "author_type": "user",
           "content": "kickoff", "thread_id": "p1"}
    recipients, _ = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm,
    )
    # No agent has opted in yet, no mentions → empty recipients
    assert recipients == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_threads.py -v`
Expected: FAIL — `tinyagentos.chat.threads` module missing.

- [ ] **Step 3: Implement**

Create `tinyagentos/chat/threads.py`:

```python
"""Thread-aware recipient resolution for agent chat routing.

Narrow-by-default scope: parent-message author (if agent), prior thread
repliers, and explicit @<slug> mentions in the new message. @all inside
a thread escalates to every channel-member agent with force_respond=true.

Muted agents are excluded. The message author is always excluded
(threads don't re-notify the speaker).
"""
from __future__ import annotations

from tinyagentos.chat.mentions import parse_mentions


async def resolve_thread_recipients(
    message: dict, channel: dict, chat_messages,
) -> tuple[list[str], dict[str, bool]]:
    """Return (recipients, force_by_slug) for a message in a thread.

    Args:
        message: the new message being routed. Must have thread_id, author_id,
                 author_type, content.
        channel: the channel dict including members and settings.muted.
        chat_messages: the ChatMessageStore (needs get_message, get_thread_messages).
    """
    author = message["author_id"]
    thread_id = message.get("thread_id")
    if not thread_id:
        return [], {}

    members = channel.get("members") or []
    muted = set((channel.get("settings") or {}).get("muted") or [])
    candidates_all = [m for m in members if m and m != author and m != "user" and m not in muted]

    mentions = parse_mentions(message.get("content") or "", members)

    # @all escalation — fan out to every agent in channel.
    if mentions.all:
        return list(candidates_all), {m: True for m in candidates_all}

    recipients: set[str] = set()
    forced: dict[str, bool] = {}

    # Parent author (if agent, and not the current author).
    parent = await chat_messages.get_message(thread_id)
    if parent and parent.get("author_type") == "agent":
        parent_author = parent.get("author_id")
        if parent_author and parent_author != author and parent_author not in muted:
            recipients.add(parent_author)

    # Prior repliers (agents only).
    prior = await chat_messages.get_thread_messages(
        channel_id=channel["id"], parent_id=thread_id, limit=200,
    )
    for m in prior:
        if m.get("author_type") == "agent":
            aid = m.get("author_id")
            if aid and aid != author and aid not in muted:
                recipients.add(aid)

    # Explicit mentions (force_respond).
    for slug in mentions.explicit:
        if slug in candidates_all:
            recipients.add(slug)
            forced[slug] = True

    return sorted(recipients), forced
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_threads.py -v`
Expected: 8 pass (2 from Task 4 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/threads.py tests/test_chat_threads.py
git commit -m "feat(chat): threads.resolve_thread_recipients for narrow thread routing"
```

---

## Task 6: agent_chat_router integrates threads

**Files:**
- Modify: `tinyagentos/agent_chat_router.py`
- Test: `tests/test_agent_chat_router.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_chat_router.py`:

```python
@pytest.mark.asyncio
async def test_router_uses_thread_resolver_when_thread_id_present():
    """A message with thread_id set goes through threads.resolve_thread_recipients,
    skipping the channel fanout path."""
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    # chat_messages needs get_message + get_thread_messages for the resolver
    state.chat_messages.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    state.chat_messages.get_thread_messages = AsyncMock(return_value=[])
    router = AgentChatRouter(state)
    message = {
        "id": "m1", "author_id": "user", "author_type": "user",
        "content": "thoughts?", "thread_id": "p1",
        "metadata": {"hops_since_user": 0},
    }
    await router._route(message, _channel(["user", "tom", "don"], "quiet"))
    slugs = sorted(c[0] for c in bridge.calls)
    # tom is the parent author → recipient; don is not mentioned + not prior replier → skipped.
    assert slugs == ["tom"]


@pytest.mark.asyncio
async def test_router_thread_policy_key_is_scoped():
    """Policy key used in thread routing should be channel_id:thread:<id>,
    so a thread doesn't consume the channel's rate cap or block unrelated
    channel messages."""
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    state.chat_messages.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    state.chat_messages.get_thread_messages = AsyncMock(return_value=[])
    router = AgentChatRouter(state)
    # Route a thread message.
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "go", "thread_id": "p1",
           "metadata": {"hops_since_user": 0}}
    await router._route(msg, _channel(["user", "tom"], "quiet"))
    # The policy should have recorded a send keyed "c1:thread:p1" — check by trying to
    # route a channel-scope message next; it should NOT be rate-limited by the thread send.
    msg2 = {"id": "m2", "author_id": "user", "author_type": "user",
            "content": "channel msg", "metadata": {"hops_since_user": 0}}
    await router._route(msg2, _channel(["user", "tom"], "lively"))
    # Expect 2 bridge calls total (one per message), since policy keys are independent.
    assert len(bridge.calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_agent_chat_router.py -v -k "thread"`
Expected: FAIL.

- [ ] **Step 3: Integrate threads into `_route_inner`**

At the top of `_route_inner`, after the `content_type == "system"` guard, add a branch:

```python
thread_id = message.get("thread_id")
if thread_id:
    from tinyagentos.chat.threads import resolve_thread_recipients
    recipients, force_by_slug = await resolve_thread_recipients(
        message, channel, self._state.chat_messages,
    )
    if not recipients:
        return
    # Thread policy key scopes hops/cooldown/rate-cap per thread.
    policy_key = f"{channel['id']}:thread:{thread_id}"
else:
    # ... existing channel recipient selection code ...
    policy_key = channel["id"]
```

Change every `policy.may_send(channel["id"], agent_name, settings)` and `policy.record_send(channel["id"], agent_name)` to use `policy_key` instead of `channel["id"]`.

Update the enqueued event payload to include `thread_id`:

```python
await bridge.enqueue_user_message(
    agent_name,
    {
        # ... existing fields ...
        "thread_id": thread_id,  # None for channel messages
    },
)
```

The thread context window is built from thread messages, not channel messages. In the context-fetch block (the `try: recent = await self._state.chat_messages.get_messages(...)`), branch on thread_id:

```python
context = []
if hasattr(self._state, "chat_messages"):
    try:
        from tinyagentos.chat.context_window import build_context_window
        if thread_id:
            recent = await self._state.chat_messages.get_thread_messages(
                channel_id=channel["id"], parent_id=thread_id, limit=30,
            )
            # Prepend the parent as the root turn.
            parent = await self._state.chat_messages.get_message(thread_id)
            if parent:
                recent = [parent] + list(recent)
        else:
            recent = await self._state.chat_messages.get_messages(
                channel_id=channel["id"], limit=30,
            )
        context = build_context_window(recent, limit=20, max_tokens=4000)
    except Exception:
        logger.warning("context fetch failed for channel %s", channel.get("id"), exc_info=True)
        context = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_agent_chat_router.py -v`
Expected: all pass (new + existing).

Run: `PYTHONPATH=. pytest tests/ -x -q`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/agent_chat_router.py tests/test_agent_chat_router.py
git commit -m "feat(chat): router integrates thread-aware recipients + per-thread policy + thread context"
```

---

## Task 7: `/help` command module

**Files:**
- Create: `tinyagentos/chat/help.py`
- Test: `tests/test_chat_help.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_help.py`:

```python
import pytest
from tinyagentos.chat.help import handle_help, KNOWN_TOPICS


def test_overview_on_empty_args():
    out = handle_help("")
    assert "chat-guide" in out.lower()
    # lists known topics
    for t in ["threads", "attachments", "mentions"]:
        assert t in out


def test_specific_topic_returns_section():
    out = handle_help("threads")
    assert "thread" in out.lower()
    assert "chat-guide" in out.lower()  # link to full guide


def test_unknown_topic_returns_generic_message():
    out = handle_help("unknownthing")
    assert "unknown" in out.lower() or "try /help" in out.lower()


def test_all_documented_topics_have_handlers():
    for t in KNOWN_TOPICS:
        out = handle_help(t)
        assert len(out) > 0
        assert "error" not in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_help.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `tinyagentos/chat/help.py`:

```python
"""/help command handler — posts short cheat sheets into the channel
as system messages. Full reference lives in docs/chat-guide.md.
"""
from __future__ import annotations

GUIDE_URL = "https://github.com/jaylfc/tinyagentos/blob/master/docs/chat-guide.md"

KNOWN_TOPICS = (
    "channels",
    "mentions",
    "hops",
    "reactions",
    "slash",
    "settings",
    "context",
    "threads",
    "attachments",
    "help",
)

_OVERVIEW = f"""**taOS chat — quick help**

- `@tom`, `@all`, `@humans` — target specific recipients
- `/` in composer opens the command picker for the current channel's agents
- `ⓘ` in the header opens channel settings (mode, members, muted, etc.)
- Right-click / hover a message for actions (reply in thread, react, etc.)

Try `/help <topic>` where topic is one of: {", ".join(t for t in KNOWN_TOPICS if t != "help")}.
Full guide: {GUIDE_URL}
"""

_TOPICS: dict[str, str] = {
    "channels": f"""**Channels**
- DM (2 members), group (many), topic (many, focused)
- Group/topic channels have a mode: `quiet` (respond when @mentioned only) or `lively` (every agent decides per message)
- DMs always lively — the 1:1 agent always replies.

Details: {GUIDE_URL}#channels-and-modes""",
    "mentions": f"""**Mentions**
- `@tom` — target one agent
- `@all` — every agent in the channel
- `@humans` — ping humans
- Case-insensitive; word boundary so `email@x.com` doesn't count

Details: {GUIDE_URL}#mentions""",
    "hops": f"""**Hops, cooldown, rate-cap**
- Hop counter resets on each user message; caps chains between agents (default 3)
- Per-agent cooldown prevents burst replies (default 5 s)
- Per-channel rate cap (default 20/min) is a circuit breaker
- `@mention` overrides all three caps

Details: {GUIDE_URL}#hops-cooldown-rate-cap""",
    "reactions": f"""**Reactions**
- Any emoji — click 😀 on a message's hover row
- `👎` by the channel's human on an agent reply → regenerate
- `🙋` by an agent → "hand raise" (shows a badge; no auto-reply)

Details: {GUIDE_URL}#reactions""",
    "slash": f"""**Slash menu**
- Type `/` at the start of a message to open the command picker
- Commands grouped by agent; fuzzy filter as you type
- Enter selects → inserts `@<agent> /<cmd>` into the composer

Details: {GUIDE_URL}#slash-menu""",
    "settings": f"""**Channel settings**
- `ⓘ` in chat header opens the settings panel (right side)
- Rename, topic, members, muted agents, mode, max hops, cooldown
- DMs have no settings panel (two-member 1:1)

Details: {GUIDE_URL}#channel-settings""",
    "context": f"""**Agent context menu**
- Right-click an agent's name or avatar anywhere for actions
- DM, (un)mute, remove, view info, jump to agent settings
- Shift+F10 on a focused message row opens the same menu

Details: {GUIDE_URL}#agent-context-menu""",
    "threads": f"""**Threads**
- Hover a message → `💬 Reply in thread` opens a right-side panel
- Thread replies have narrow routing — parent author + prior repliers + @mentions
- `@all` inside a thread escalates to every channel agent
- Hops, cooldown, rate-cap all scoped per thread

Details: {GUIDE_URL}#threads""",
    "attachments": f"""**Attachments**
- Paperclip button, drag-and-drop, or paste from clipboard
- Paperclip opens a file picker with tabs: Disk / My workspace / Agent workspaces
- Up to 10 attachments per message; 100 MB max per file
- Images render inline; 2+ images → gallery grid

Details: {GUIDE_URL}#attachments""",
    "help": f"""**/help**
- `/help` on its own — overview + topic list
- `/help <topic>` — the section for that topic
- Topics: {", ".join(t for t in KNOWN_TOPICS if t != "help")}

Full guide: {GUIDE_URL}""",
}


def handle_help(args: str) -> str:
    """Return the system-message text for `/help [topic]`."""
    topic = (args or "").strip().lower().split()
    if not topic:
        return _OVERVIEW
    key = topic[0]
    if key in _TOPICS:
        return _TOPICS[key]
    return f"Unknown help topic '{key}'. Try `/help` for the overview."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_help.py -v`
Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/help.py tests/test_chat_help.py
git commit -m "feat(chat): /help command — overview + per-topic cheat sheets"
```

---

## Task 8: `/help` interception in routes/chat.py

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_help.py` (extend)

- [ ] **Step 1: Write the failing test**

Append:

```python
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


@pytest.mark.asyncio
async def test_help_message_intercepted_posts_system_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "/help",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201)
        body = r.json()
        assert body.get("handled") == "help"
        # confirm a system message is persisted
        msgs = await app.state.chat_messages.get_messages(channel_id=ch_id, limit=5)
        sys_msgs = [m for m in msgs if m.get("author_type") == "system"]
        assert len(sys_msgs) == 1
        assert "chat-guide" in sys_msgs[0]["content"].lower()


@pytest.mark.asyncio
async def test_help_bypasses_bare_slash_guardrail(tmp_path, monkeypatch):
    """/help in a non-DM channel without @mention must NOT hit the 400 guard —
    it's a taOS control action, not a framework command."""
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom", "don"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user",
                  "author_type": "user", "content": "/help threads",
                  "content_type": "text"},
        )
        assert r.status_code in (200, 201)
        assert r.json().get("handled") == "help"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_help.py -v`
Expected: FAIL — interception not wired.

- [ ] **Step 3: Intercept `/help` in routes/chat.py**

In the POST `/api/chat/messages` handler, BEFORE the bare-slash guardrail (currently the first thing after channel-id extraction), add:

```python
content = body.get("content") or ""
if content.startswith("/help"):
    from tinyagentos.chat.help import handle_help
    args = content[5:].lstrip()
    system_text = handle_help(args)
    sys_msg = await request.app.state.chat_messages.send_message(
        channel_id=channel_id, author_id="system", author_type="system",
        content=system_text, content_type="text", state="complete",
        metadata=None,
    )
    await request.app.state.chat_channels.update_last_message_at(channel_id)
    await request.app.state.chat_hub.broadcast(
        channel_id,
        {"type": "message", "seq": request.app.state.chat_hub.next_seq(), **sys_msg},
    )
    return JSONResponse({"ok": True, "handled": "help", "system_message": sys_msg}, status_code=200)
```

The existing bare-slash guardrail logic still runs for any other `/`-prefixed message.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_help.py -v`
Expected: 6 pass.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_help.py
git commit -m "feat(chat): /help intercept in POST /api/chat/messages (bypasses bare-slash guard)"
```

---

## Task 9: Bridge event payload (thread_id + attachments)

**Files:**
- Modify: `tinyagentos/bridge_session.py`
- Test: `tests/test_bridge_session_phase1.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bridge_session_phase1.py`:

```python
@pytest.mark.asyncio
async def test_enqueue_passes_thread_id_and_attachments():
    reg = BridgeSessionRegistry()
    await reg.enqueue_user_message("tom", {
        "id": "m1", "trace_id": "m1", "channel_id": "c1",
        "from": "user", "text": "see file", "hops_since_user": 0,
        "force_respond": False, "context": [],
        "thread_id": "t-parent",
        "attachments": [
            {"filename": "a.png", "mime_type": "image/png",
             "size": 1, "url": "/api/chat/files/abc.png"},
        ],
    })
    frames = []
    async for frame in reg.subscribe("tom"):
        frames.append(frame)
        break
    assert "thread_id" in frames[0]
    assert "a.png" in frames[0]
```

- [ ] **Step 2: Run test**

Run: `PYTHONPATH=. pytest tests/test_bridge_session_phase1.py -v -k "thread_id"`
Expected: this likely already passes because `enqueue_user_message` serialises the whole msg dict. Confirm by inspection.

If it fails, update `enqueue_user_message` to pass through `thread_id` and `attachments` into the SSE frame — but the existing Phase 2a implementation already puts the whole msg dict into the event's `data`, so no change needed.

- [ ] **Step 3: Commit (if any changes)**

If the test passes with no changes, skip the commit — annotate in the plan that this task was a no-op verification.

```bash
git add tests/test_bridge_session_phase1.py
git commit -m "test(bridge): verify thread_id + attachments pass through enqueue_user_message"
```

---

## Task 10: Bridge scripts — attachment footer in context

**Files:**
- Modify: 6 install scripts

For each `tinyagentos/scripts/install_{hermes,smolagents,langroid,pocketflow,openai_agents_sdk,openai-agents-sdk}.sh`, locate `_render_context(ctx)` and add an attachment footer:

- [ ] **Step 1: Update `_render_context` to optionally include attachments**

Find the existing helper:

```python
def _render_context(ctx):
    if not ctx:
        return ""
    lines = []
    for m in ctx:
        who = m.get("author_id") or "?"
        lines.append(f"{who}: {m.get('content','')}")
    return "\n".join(lines)
```

Leave it as-is. Add a new helper `_render_attachments`:

```python
def _render_attachments(atts):
    if not atts:
        return ""
    parts = []
    for a in atts:
        size_kb = max(1, int(a.get("size", 0) / 1024))
        parts.append(f"{a.get('filename','file')} ({a.get('mime_type','?')}, {size_kb} KB)")
    return "User attached: " + ", ".join(parts)
```

- [ ] **Step 2: Wire the footer into `handle` / `handle_user_message`**

In the 5 non-hermes bridges, inside `handle(c, evt, ch)`, change the `full` composition to include attachments:

```python
ctx = _render_context(evt.get("context") or [])
attach_line = _render_attachments(evt.get("attachments") or [])
base = text if not ctx else f"Recent conversation:\n{ctx}\n\nCurrent: {text}"
full = f"{base}\n{attach_line}" if attach_line else base
```

In hermes (`install_hermes.sh`, `handle_user_message`), prepend the attach_line similarly into the `user` role message.

- [ ] **Step 3: Lint all 6 scripts**

```bash
for f in tinyagentos/scripts/install_hermes.sh \
         tinyagentos/scripts/install_smolagents.sh \
         tinyagentos/scripts/install_langroid.sh \
         tinyagentos/scripts/install_pocketflow.sh \
         tinyagentos/scripts/install_openai_agents_sdk.sh \
         tinyagentos/scripts/install_openai-agents-sdk.sh; do
    bash -n "$f" && echo "$f ok" || echo "$f BAD"
done
```

Expected: all 6 `ok`.

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/scripts/install_hermes.sh tinyagentos/scripts/install_smolagents.sh \
        tinyagentos/scripts/install_langroid.sh tinyagentos/scripts/install_pocketflow.sh \
        tinyagentos/scripts/install_openai_agents_sdk.sh tinyagentos/scripts/install_openai-agents-sdk.sh
git commit -m "feat(bridges): append attachment footer to LLM context prompt"
```

---

## Task 11: Factor `VfsBrowser` out of FilesApp

**Files:**
- Create: `desktop/src/shell/VfsBrowser.tsx`
- Modify: `desktop/src/apps/FilesApp.tsx`
- Test: `desktop/src/shell/__tests__/VfsBrowser.test.tsx`

- [ ] **Step 1: Identify the existing browser code in FilesApp**

Run: `grep -n 'function.*Tree\|VFS\|browse\|folder\|directory' desktop/src/apps/FilesApp.tsx | head -20`

FilesApp already has tree + file-list rendering. The goal is to extract the inner browser (tree on left + file list on right) into a standalone component.

- [ ] **Step 2: Write a smoke test**

```tsx
// desktop/src/shell/__tests__/VfsBrowser.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VfsBrowser } from "../VfsBrowser";

describe("VfsBrowser", () => {
  it("renders the root listing from the API mock", async () => {
    global.fetch = vi.fn((url: string) => {
      if (url.includes("/api/files/list")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            entries: [
              { name: "report.md", type: "file", size: 100 },
              { name: "notes", type: "folder" },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404 } as Response);
    }) as unknown as typeof fetch;

    render(<VfsBrowser root="/workspaces/user" onSelect={vi.fn()} />);
    expect(await screen.findByText("report.md")).toBeInTheDocument();
    expect(screen.getByText("notes")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Extract + implement**

Create `desktop/src/shell/VfsBrowser.tsx` with the extracted tree+listing component. Interface:

```tsx
export type VfsEntry = { name: string; type: "file" | "folder"; size?: number };

export function VfsBrowser({
  root,
  onSelect,
  multi = false,
}: {
  root: string;
  onSelect: (path: string | string[]) => void;
  multi?: boolean;
}) { ... }
```

Internally fetches `/api/files/list?path={current}` (confirm endpoint by reading FilesApp — if it uses a different route, match that). Minimum viable: a single flat listing of the `root` folder with folder-click-to-navigate, file-click-to-select. Tree-on-left is a nice-to-have but can ship as a later iteration — flat listing is enough for the picker.

Update `desktop/src/apps/FilesApp.tsx` to consume `VfsBrowser` where the inline browser was. Keep all FilesApp-specific UI (header, context menus, etc.) around the browser.

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run VfsBrowser`
Expected: pass.

Open the FilesApp in dev mode and confirm it still works visually. If there's no dev-mode smoke test, a quick `cd desktop && npm run build` is enough to catch structural breakage.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/shell/VfsBrowser.tsx desktop/src/apps/FilesApp.tsx desktop/src/shell/__tests__/VfsBrowser.test.tsx
git commit -m "refactor(desktop): extract VfsBrowser to shell/ for shared use"
```

---

## Task 12: `SharedFilePickerDialog` + `openFilePicker`

**Files:**
- Create: `desktop/src/shell/FilePicker.tsx`
- Create: `desktop/src/shell/file-picker-api.ts`
- Test: `desktop/src/shell/__tests__/FilePicker.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// desktop/src/shell/__tests__/FilePicker.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FilePicker } from "../FilePicker";

describe("FilePicker", () => {
  it("shows the three tabs when all sources are requested", () => {
    render(
      <FilePicker
        sources={["disk", "workspace", "agent-workspace"]}
        multi
        onPick={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: /Disk/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /My workspace/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Agent workspaces/i })).toBeInTheDocument();
  });

  it("cancel calls onCancel and nothing else", () => {
    const onPick = vi.fn();
    const onCancel = vi.fn();
    render(<FilePicker sources={["disk"]} onPick={onPick} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onPick).not.toHaveBeenCalled();
  });

  it("Esc closes", () => {
    const onCancel = vi.fn();
    render(<FilePicker sources={["disk"]} onPick={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Implement FilePicker**

```tsx
// desktop/src/shell/FilePicker.tsx
import React, { useEffect, useRef, useState } from "react";
import { VfsBrowser } from "./VfsBrowser";

export type FileSelection =
  | { source: "disk"; file: File }
  | { source: "workspace"; path: string }
  | { source: "agent-workspace"; slug: string; path: string };

type Source = "disk" | "workspace" | "agent-workspace";

export function FilePicker({
  sources,
  accept,
  multi = false,
  onPick,
  onCancel,
}: {
  sources: Source[];
  accept?: string;
  multi?: boolean;
  onPick: (selections: FileSelection[]) => void;
  onCancel: () => void;
}) {
  const [activeTab, setActiveTab] = useState<Source>(sources[0]);
  const [queued, setQueued] = useState<FileSelection[]>([]);
  const [agents, setAgents] = useState<{ name: string }[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (sources.includes("agent-workspace")) {
      fetch("/api/agents")
        .then((r) => r.json())
        .then((list) => setAgents(Array.isArray(list) ? list : []))
        .catch(() => {});
    }
  }, [sources]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const onDiskFiles = (files: FileList | null) => {
    if (!files) return;
    const selections: FileSelection[] = [];
    for (const f of Array.from(files)) {
      selections.push({ source: "disk", file: f });
    }
    setQueued((prev) => multi ? [...prev, ...selections] : selections);
  };

  const onWorkspacePick = (path: string | string[]) => {
    const paths = Array.isArray(path) ? path : [path];
    const selections: FileSelection[] = paths.map((p) => ({ source: "workspace", path: p }));
    setQueued((prev) => multi ? [...prev, ...selections] : selections);
  };

  const onAgentWorkspacePick = (path: string | string[]) => {
    if (!selectedAgent) return;
    const paths = Array.isArray(path) ? path : [path];
    const selections: FileSelection[] = paths.map((p) => ({
      source: "agent-workspace", slug: selectedAgent, path: p,
    }));
    setQueued((prev) => multi ? [...prev, ...selections] : selections);
  };

  const confirm = () => onPick(queued);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Pick a file"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
    >
      <div className="bg-shell-surface border border-white/10 rounded-xl w-[720px] h-[540px] flex flex-col overflow-hidden">
        <div role="tablist" className="flex border-b border-white/10">
          {sources.includes("disk") && (
            <button role="tab" aria-selected={activeTab === "disk"}
              className={`px-4 py-2 text-sm ${activeTab === "disk" ? "border-b-2 border-sky-400" : "opacity-70"}`}
              onClick={() => setActiveTab("disk")}>Disk</button>
          )}
          {sources.includes("workspace") && (
            <button role="tab" aria-selected={activeTab === "workspace"}
              className={`px-4 py-2 text-sm ${activeTab === "workspace" ? "border-b-2 border-sky-400" : "opacity-70"}`}
              onClick={() => setActiveTab("workspace")}>My workspace</button>
          )}
          {sources.includes("agent-workspace") && (
            <button role="tab" aria-selected={activeTab === "agent-workspace"}
              className={`px-4 py-2 text-sm ${activeTab === "agent-workspace" ? "border-b-2 border-sky-400" : "opacity-70"}`}
              onClick={() => setActiveTab("agent-workspace")}>Agent workspaces</button>
          )}
        </div>

        <div className="flex-1 overflow-hidden">
          {activeTab === "disk" && (
            <div className="p-6 flex items-center justify-center">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple={multi}
                accept={accept}
                onChange={(e) => onDiskFiles(e.target.files)}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="px-4 py-2 bg-sky-500/20 text-sky-200 rounded"
              >Choose files from disk</button>
              {queued.filter((q) => q.source === "disk").length > 0 && (
                <div className="ml-6 text-xs text-shell-text-tertiary">
                  {queued.length} file(s) queued
                </div>
              )}
            </div>
          )}

          {activeTab === "workspace" && (
            <VfsBrowser root="/workspaces/user" onSelect={onWorkspacePick} multi={multi} />
          )}

          {activeTab === "agent-workspace" && (
            <div className="h-full flex flex-col">
              <div className="p-2 border-b border-white/10">
                <select
                  value={selectedAgent ?? ""}
                  onChange={(e) => setSelectedAgent(e.target.value || null)}
                  className="bg-white/5 border border-white/10 rounded px-2 py-1 text-sm"
                >
                  <option value="">Pick an agent…</option>
                  {agents.map((a) => <option key={a.name} value={a.name}>@{a.name}</option>)}
                </select>
              </div>
              {selectedAgent && (
                <VfsBrowser root={`/workspaces/${selectedAgent}`} onSelect={onAgentWorkspacePick} multi={multi} />
              )}
            </div>
          )}
        </div>

        <div className="border-t border-white/10 p-2 flex items-center justify-end gap-2 text-sm">
          <span className="opacity-60 mr-auto">{queued.length} selected</span>
          <button onClick={onCancel} className="px-3 py-1 opacity-70 hover:opacity-100">Cancel</button>
          <button onClick={confirm} disabled={queued.length === 0}
            className="px-3 py-1 bg-sky-500/30 text-sky-200 rounded disabled:opacity-40">
            Select ({queued.length})
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Imperative API**

```tsx
// desktop/src/shell/file-picker-api.ts
import React from "react";
import { createRoot } from "react-dom/client";
import { FilePicker, type FileSelection } from "./FilePicker";

type Source = "disk" | "workspace" | "agent-workspace";

export function openFilePicker(opts: {
  sources: Source[];
  accept?: string;
  multi?: boolean;
}): Promise<FileSelection[]> {
  return new Promise((resolve) => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    const cleanup = () => {
      root.unmount();
      container.remove();
    };

    root.render(
      <FilePicker
        sources={opts.sources}
        accept={opts.accept}
        multi={opts.multi}
        onPick={(sels) => { cleanup(); resolve(sels); }}
        onCancel={() => { cleanup(); resolve([]); }}
      />,
    );
  });
}
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npm test -- --run FilePicker`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/shell/FilePicker.tsx desktop/src/shell/file-picker-api.ts desktop/src/shell/__tests__/FilePicker.test.tsx
git commit -m "feat(desktop): SharedFilePickerDialog (shell primitive) + openFilePicker api"
```

---

## Task 13: `chat-attachments-api.ts` client

**Files:**
- Create: `desktop/src/lib/chat-attachments-api.ts`
- Test: `desktop/src/lib/__tests__/chat-attachments-api.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// desktop/src/lib/__tests__/chat-attachments-api.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { uploadDiskFile, attachmentFromPath } from "../chat-attachments-api";

describe("chat-attachments-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({
          filename: "f.png", mime_type: "image/png", size: 1,
          url: "/api/chat/files/abc-f.png", source: "disk",
        }),
      }),
    ) as unknown as typeof fetch;
  });

  it("uploadDiskFile POSTs multipart to /api/chat/upload", async () => {
    const f = new File(["x"], "f.png", { type: "image/png" });
    const rec = await uploadDiskFile(f);
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/upload",
      expect.objectContaining({ method: "POST" }),
    );
    expect(rec.filename).toBe("f.png");
  });

  it("attachmentFromPath POSTs workspace path", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({
          filename: "r.md", mime_type: "text/markdown", size: 10,
          url: "/api/chat/files/xyz-r.md", source: "workspace",
        }),
      }),
    ) as unknown as typeof fetch;
    const rec = await attachmentFromPath({
      path: "/workspaces/user/r.md", source: "workspace",
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/attachments/from-path",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ path: "/workspaces/user/r.md", source: "workspace" }),
      }),
    );
    expect(rec.source).toBe("workspace");
  });

  it("throws on non-OK with server's error", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false, status: 413,
        json: () => Promise.resolve({ error: "file too large (100 MB max)" }),
      }),
    ) as unknown as typeof fetch;
    const f = new File(["x"], "big.bin");
    await expect(uploadDiskFile(f)).rejects.toThrow("file too large");
  });
});
```

- [ ] **Step 2: Implement**

```typescript
// desktop/src/lib/chat-attachments-api.ts
export type AttachmentRecord = {
  filename: string;
  mime_type: string;
  size: number;
  url: string;
  source: "disk" | "workspace" | "agent-workspace";
};

async function _ensureOk(r: Response): Promise<void> {
  if (r.ok) return;
  let body: { error?: string } | null = null;
  try { body = await r.json(); } catch { /* ignore */ }
  throw new Error(body?.error || `HTTP ${r.status}`);
}

export async function uploadDiskFile(file: File, channelId?: string): Promise<AttachmentRecord> {
  const form = new FormData();
  form.append("file", file);
  if (channelId) form.append("channel_id", channelId);
  const r = await fetch("/api/chat/upload", { method: "POST", body: form });
  await _ensureOk(r);
  return r.json();
}

export async function attachmentFromPath(body: {
  path: string;
  source: "workspace" | "agent-workspace";
  slug?: string;
}): Promise<AttachmentRecord> {
  const r = await fetch("/api/chat/attachments/from-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await _ensureOk(r);
  return r.json();
}
```

- [ ] **Step 3: Run tests + commit**

Run: `cd desktop && npm test -- --run chat-attachments-api`
Expected: 3 pass.

```bash
git add desktop/src/lib/chat-attachments-api.ts desktop/src/lib/__tests__/chat-attachments-api.test.ts
git commit -m "feat(desktop): chat-attachments-api client (upload + from-path)"
```

---

## Task 14: `AttachmentsBar` + `AttachmentGallery` + `AttachmentLightbox`

**Files:**
- Create: `desktop/src/apps/chat/AttachmentsBar.tsx`
- Create: `desktop/src/apps/chat/AttachmentGallery.tsx`
- Create: `desktop/src/apps/chat/AttachmentLightbox.tsx`
- Tests: `desktop/src/apps/chat/__tests__/Attachment*.test.tsx`

- [ ] **Step 1: AttachmentsBar (pre-send)**

Create `desktop/src/apps/chat/AttachmentsBar.tsx`:

```tsx
import React from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";

export type PendingAttachment = {
  id: string;
  filename: string;
  size: number;
  mime_type?: string;
  record?: AttachmentRecord;  // set once upload completes
  error?: string;
  uploading?: boolean;
};

export function AttachmentsBar({
  items,
  onRemove,
  onRetry,
}: {
  items: PendingAttachment[];
  onRemove: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div
      aria-label="Pending attachments"
      className="px-4 py-2 border-t border-white/10 flex gap-2 flex-wrap"
    >
      {items.map((it) => (
        <div key={it.id} className="flex items-center gap-2 bg-white/5 rounded px-2 py-1 text-xs max-w-[220px]">
          <span className="truncate">{it.filename}</span>
          <span className="opacity-50">{Math.max(1, Math.round(it.size / 1024))} KB</span>
          {it.uploading && <span className="opacity-70">…</span>}
          {it.error && (
            <button aria-label="Retry upload" onClick={() => onRetry(it.id)} className="text-red-300">retry</button>
          )}
          <button aria-label={`Remove ${it.filename}`} onClick={() => onRemove(it.id)} className="opacity-70 hover:opacity-100">×</button>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: AttachmentGallery (in-message)**

```tsx
// desktop/src/apps/chat/AttachmentGallery.tsx
import React, { useState } from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";
import { AttachmentLightbox } from "./AttachmentLightbox";

export function AttachmentGallery({ attachments }: { attachments: AttachmentRecord[] }) {
  const [lightboxStart, setLightboxStart] = useState<number | null>(null);
  if (!attachments?.length) return null;
  const images = attachments.filter((a) => a.mime_type?.startsWith("image/"));
  const files = attachments.filter((a) => !a.mime_type?.startsWith("image/"));

  const gridClass = images.length > 1 ? "grid grid-cols-2 gap-1 max-w-md" : "";

  return (
    <div className="flex flex-col gap-2 mt-1">
      {images.length > 0 && (
        <div className={gridClass}>
          {images.slice(0, 4).map((img, i) => (
            <button key={img.url} onClick={() => setLightboxStart(i)} className="block">
              <img
                src={img.url}
                alt={img.filename}
                className={images.length === 1
                  ? "max-w-[560px] max-h-[400px] rounded"
                  : "object-cover w-full h-32 rounded"}
              />
              {images.length > 4 && i === 3 && (
                <span className="absolute inset-0 bg-black/60 flex items-center justify-center text-white">
                  +{images.length - 4} more
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-col gap-1">
          {files.map((f) => (
            <a key={f.url} href={f.url} target="_blank" rel="noreferrer"
               className="flex items-center gap-2 bg-white/5 hover:bg-white/10 rounded px-2 py-1 text-sm max-w-sm">
              <span aria-hidden>📄</span>
              <span className="truncate">{f.filename}</span>
              <span className="ml-auto text-xs opacity-60">
                {Math.max(1, Math.round(f.size / 1024))} KB
              </span>
            </a>
          ))}
        </div>
      )}
      {lightboxStart !== null && (
        <AttachmentLightbox
          images={images}
          startIndex={lightboxStart}
          onClose={() => setLightboxStart(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: AttachmentLightbox**

```tsx
// desktop/src/apps/chat/AttachmentLightbox.tsx
import React, { useEffect, useState } from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";

export function AttachmentLightbox({
  images, startIndex, onClose,
}: {
  images: AttachmentRecord[];
  startIndex: number;
  onClose: () => void;
}) {
  const [idx, setIdx] = useState(startIndex);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") setIdx((i) => Math.max(0, i - 1));
      if (e.key === "ArrowRight") setIdx((i) => Math.min(images.length - 1, i + 1));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [images.length, onClose]);

  const current = images[idx];
  return (
    <div
      role="dialog"
      aria-label="Image viewer"
      className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center"
      onClick={onClose}
    >
      <img src={current.url} alt={current.filename}
           className="max-w-[90vw] max-h-[90vh]"
           onClick={(e) => e.stopPropagation()} />
      <div className="absolute top-4 right-4 flex gap-2">
        <a href={current.url} download={current.filename}
           onClick={(e) => e.stopPropagation()}
           className="bg-white/10 hover:bg-white/20 rounded px-3 py-1 text-sm">Download</a>
        <button onClick={onClose} className="bg-white/10 hover:bg-white/20 rounded px-3 py-1 text-sm">Close</button>
      </div>
      {images.length > 1 && (
        <div className="absolute bottom-4 text-white/70 text-xs">{idx + 1} / {images.length}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Component tests**

Create `__tests__/AttachmentGallery.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AttachmentGallery } from "../AttachmentGallery";

const img = (url: string, name: string) => ({
  filename: name, mime_type: "image/png", size: 1, url, source: "disk" as const,
});

describe("AttachmentGallery", () => {
  it("renders nothing for empty list", () => {
    const { container } = render(<AttachmentGallery attachments={[]} />);
    expect(container.firstChild).toBeNull();
  });
  it("renders a single image inline", () => {
    render(<AttachmentGallery attachments={[img("/a.png", "a.png")]} />);
    expect(screen.getByAltText("a.png")).toBeInTheDocument();
  });
  it("renders a grid for 2+ images", () => {
    render(<AttachmentGallery attachments={[
      img("/a.png", "a.png"), img("/b.png", "b.png"),
    ]} />);
    expect(screen.getByAltText("a.png")).toBeInTheDocument();
    expect(screen.getByAltText("b.png")).toBeInTheDocument();
  });
  it("renders file tiles for non-image attachments", () => {
    render(<AttachmentGallery attachments={[{
      filename: "r.pdf", mime_type: "application/pdf",
      size: 1000, url: "/r.pdf", source: "disk",
    }]} />);
    expect(screen.getByText("r.pdf")).toBeInTheDocument();
  });
});
```

Create `__tests__/AttachmentsBar.test.tsx` with 2 tests (renders nothing for empty; renders filename + remove button for one item).

- [ ] **Step 5: Run tests**

Run: `cd desktop && npm test -- --run Attachment`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add desktop/src/apps/chat/AttachmentsBar.tsx \
        desktop/src/apps/chat/AttachmentGallery.tsx \
        desktop/src/apps/chat/AttachmentLightbox.tsx \
        desktop/src/apps/chat/__tests__/AttachmentsBar.test.tsx \
        desktop/src/apps/chat/__tests__/AttachmentGallery.test.tsx
git commit -m "feat(desktop): AttachmentsBar + AttachmentGallery + AttachmentLightbox"
```

---

## Task 15: `MessageHoverActions` + `ThreadIndicator` + `ThreadPanel` + `use-thread-panel`

**Files:**
- Create: `desktop/src/apps/chat/MessageHoverActions.tsx`
- Create: `desktop/src/apps/chat/ThreadIndicator.tsx`
- Create: `desktop/src/apps/chat/ThreadPanel.tsx`
- Create: `desktop/src/lib/use-thread-panel.ts`
- Tests under `__tests__/`

- [ ] **Step 1: MessageHoverActions**

```tsx
// desktop/src/apps/chat/MessageHoverActions.tsx
import React from "react";

export function MessageHoverActions({
  onReact,
  onReplyInThread,
  onMore,
}: {
  onReact: () => void;
  onReplyInThread: () => void;
  onMore: (e: React.MouseEvent) => void;
}) {
  return (
    <div
      role="toolbar"
      aria-label="Message actions"
      className="inline-flex items-center gap-0.5 bg-shell-surface border border-white/10 rounded-md shadow-sm px-1"
    >
      <button aria-label="Add reaction" onClick={onReact} className="p-1 hover:bg-white/5">😀</button>
      <button aria-label="Reply in thread" onClick={onReplyInThread} className="p-1 hover:bg-white/5">💬</button>
      <button aria-label="More" onClick={onMore} className="p-1 hover:bg-white/5">⋯</button>
    </div>
  );
}
```

- [ ] **Step 2: ThreadIndicator**

```tsx
// desktop/src/apps/chat/ThreadIndicator.tsx
import React from "react";

export function ThreadIndicator({
  replyCount, lastReplyAt, onOpen,
}: {
  replyCount: number;
  lastReplyAt?: number | null;
  onOpen: () => void;
}) {
  if (replyCount === 0) return null;
  const label = lastReplyAt
    ? `💬 ${replyCount} repl${replyCount === 1 ? "y" : "ies"} · last reply ${relative(lastReplyAt)}`
    : `💬 ${replyCount} repl${replyCount === 1 ? "y" : "ies"}`;
  return (
    <button
      onClick={onOpen}
      className="mt-1 px-2 py-0.5 text-xs text-sky-200 hover:bg-white/5 rounded"
      aria-label="Open thread"
    >{label}</button>
  );
}

function relative(ts: number): string {
  const now = Date.now() / 1000;
  const delta = Math.max(0, now - ts);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}
```

- [ ] **Step 3: use-thread-panel**

```typescript
// desktop/src/lib/use-thread-panel.ts
import { useState } from "react";

export function useThreadPanel() {
  const [openThread, setOpen] = useState<{ channelId: string; parentId: string } | null>(null);

  return {
    openThread,
    openThreadFor: (channelId: string, parentId: string) => setOpen({ channelId, parentId }),
    closeThread: () => setOpen(null),
  };
}
```

- [ ] **Step 4: ThreadPanel**

```tsx
// desktop/src/apps/chat/ThreadPanel.tsx
import React, { useEffect, useState } from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";
import { AttachmentGallery } from "./AttachmentGallery";

type Msg = {
  id: string;
  author_id: string;
  author_type: string;
  content: string;
  created_at: number;
  attachments?: AttachmentRecord[];
};

export function ThreadPanel({
  channelId,
  parentId,
  onClose,
  onSend,
}: {
  channelId: string;
  parentId: string;
  onClose: () => void;
  onSend: (content: string, attachments: AttachmentRecord[]) => Promise<void>;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [parent, setParent] = useState<Msg | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [input, setInput] = useState("");

  useEffect(() => {
    let alive = true;
    fetch(`/api/chat/channels/${channelId}/threads/${parentId}/messages`)
      .then((r) => r.json())
      .then((d) => { if (alive) setMsgs(d.messages || []); })
      .catch(() => { if (alive) setErr("couldn't load this thread"); });
    fetch(`/api/chat/messages/${parentId}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (alive && d) setParent(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [channelId, parentId]);

  const submit = async () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    try { await onSend(trimmed, []); setInput(""); setErr(null); }
    catch (e) { setErr(e instanceof Error ? e.message : "send failed"); }
  };

  return (
    <aside
      role="complementary"
      aria-label="Thread"
      className="fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 shadow-xl flex flex-col z-40"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <h2 className="text-sm font-semibold">Thread</h2>
        <button onClick={onClose} aria-label="Close" className="text-lg leading-none">×</button>
      </header>
      <div className="flex-1 overflow-y-auto p-3 space-y-2 text-sm">
        {parent && (
          <article className="pb-2 border-b border-white/10">
            <div className="text-xs opacity-70">@{parent.author_id}</div>
            <div>{parent.content}</div>
            <AttachmentGallery attachments={parent.attachments || []} />
          </article>
        )}
        {msgs.map((m) => (
          <article key={m.id}>
            <div className="text-xs opacity-70">@{m.author_id}</div>
            <div>{m.content}</div>
            <AttachmentGallery attachments={m.attachments || []} />
          </article>
        ))}
        {err && <div role="alert" className="text-xs text-red-300">{err}</div>}
      </div>
      <footer className="border-t border-white/10 p-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }}
          placeholder="Reply in thread…"
          className="w-full bg-white/5 border border-white/10 rounded px-2 py-1.5 text-sm"
        />
      </footer>
    </aside>
  );
}
```

- [ ] **Step 5: Tests**

`__tests__/MessageHoverActions.test.tsx` — 3 tests (all three buttons fire their callbacks).
`__tests__/ThreadIndicator.test.tsx` — 3 tests (empty count → null, singular "reply", plural "replies", relative time renders).

- [ ] **Step 6: Run + commit**

Run: `cd desktop && npm test -- --run MessageHoverActions && npm test -- --run ThreadIndicator && npm test -- --run ThreadPanel`
Expected: all pass.

```bash
git add desktop/src/apps/chat/MessageHoverActions.tsx \
        desktop/src/apps/chat/ThreadIndicator.tsx \
        desktop/src/apps/chat/ThreadPanel.tsx \
        desktop/src/lib/use-thread-panel.ts \
        desktop/src/apps/chat/__tests__/MessageHoverActions.test.tsx \
        desktop/src/apps/chat/__tests__/ThreadIndicator.test.tsx
git commit -m "feat(desktop): MessageHoverActions + ThreadIndicator + ThreadPanel + use-thread-panel"
```

---

## Task 16: `docs/chat-guide.md`

**Files:**
- Create: `docs/chat-guide.md`

- [ ] **Step 1: Write the guide**

Create `docs/chat-guide.md` covering all sections described in the spec. Each section: `## Section — quick` (1 sentence) + `### Details` (rules, edge cases, tips).

Section order (1:1 with `/help` topics):

1. Overview
2. Channels and modes
3. Mentions
4. Hops, cooldown, rate-cap
5. Reactions
6. Slash menu
7. Channel settings
8. Agent context menu
9. Threads
10. Attachments
11. /help

Each section should be concise — aim for ~15-30 lines per section including code/UI examples. The whole file should be ~300-500 lines. Use real taOS agent names (tom, don, etc.) in examples. Link sections via anchors so `/help` topic responses can deep-link.

- [ ] **Step 2: Commit**

```bash
git add docs/chat-guide.md
git commit -m "docs: canonical chat guide — retroactive P1 + 2a + 2b-1 coverage"
```

---

## Task 17: Integrate into MessagesApp

**Files:**
- Modify: `desktop/src/apps/MessagesApp.tsx`

Wire everything together. This is the big integration task — similar to Phase 2a Task 13.

- [ ] **Step 1: Imports**

Add to the top of `desktop/src/apps/MessagesApp.tsx`:

```tsx
import { MessageHoverActions } from "./chat/MessageHoverActions";
import { ThreadIndicator } from "./chat/ThreadIndicator";
import { ThreadPanel } from "./chat/ThreadPanel";
import { AttachmentsBar, type PendingAttachment } from "./chat/AttachmentsBar";
import { AttachmentGallery } from "./chat/AttachmentGallery";
import { uploadDiskFile, attachmentFromPath, type AttachmentRecord } from "@/lib/chat-attachments-api";
import { useThreadPanel } from "@/lib/use-thread-panel";
import { openFilePicker } from "@/shell/file-picker-api";
```

- [ ] **Step 2: New state + hook**

```tsx
const { openThread, openThreadFor, closeThread } = useThreadPanel();
const [hoveredMessageId, setHoveredMessageId] = useState<string | null>(null);
const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
```

- [ ] **Step 3: Hover actions in message row**

In the JSX where message rows render, attach mouse handlers + render hover actions on the hovered row:

```tsx
<li
  onMouseEnter={() => setHoveredMessageId(msg.id)}
  onMouseLeave={() => setHoveredMessageId((id) => id === msg.id ? null : id)}
  className="relative ..."
>
  {/* existing message content */}
  {hoveredMessageId === msg.id && (
    <div className="absolute top-0 right-2 -translate-y-1/2">
      <MessageHoverActions
        onReact={() => { /* open reaction picker — reuse emoji picker */ setShowEmoji(msg.id); }}
        onReplyInThread={() => openThreadFor(msg.channel_id, msg.id)}
        onMore={(e) => { e.preventDefault(); setContextMenu({ slug: msg.author_id, x: e.clientX, y: e.clientY }); }}
      />
    </div>
  )}
  {/* attachments + thread indicator */}
  <AttachmentGallery attachments={msg.attachments || []} />
  <ThreadIndicator
    replyCount={msg.reply_count || 0}
    lastReplyAt={msg.last_reply_at}
    onOpen={() => openThreadFor(msg.channel_id, msg.id)}
  />
</li>
```

(If `msg.reply_count` / `last_reply_at` aren't on the message yet, skip rendering the indicator unless present. The backend can provide these in a later pass; UI is defensive.)

- [ ] **Step 4: Thread panel mount**

Near the root return (alongside ChannelSettingsPanel):

```tsx
{openThread && (
  <ThreadPanel
    channelId={openThread.channelId}
    parentId={openThread.parentId}
    onClose={closeThread}
    onSend={async (content, attachments) => {
      await fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_id: openThread.channelId,
          author_id: "user", author_type: "user",
          content, content_type: "text",
          thread_id: openThread.parentId,
          attachments,
        }),
      });
    }}
  />
)}
```

Ensure ChannelSettingsPanel and ThreadPanel are mutex: opening threads closes settings and vice versa.

- [ ] **Step 5: Attachment wiring — paperclip button + drag-drop + paste**

In the composer area, add a paperclip button next to the send button:

```tsx
<button
  aria-label="Attach files"
  onClick={async () => {
    const selections = await openFilePicker({
      sources: ["disk", "workspace", "agent-workspace"],
      multi: true,
    });
    for (const sel of selections) {
      const id = Math.random().toString(36).slice(2);
      setPendingAttachments((p) => [...p, {
        id, filename: sel.source === "disk" ? sel.file.name : sel.path.split("/").pop() || "",
        size: sel.source === "disk" ? sel.file.size : 0, uploading: true,
      }]);
      try {
        const rec = sel.source === "disk"
          ? await uploadDiskFile(sel.file, selectedChannel ?? undefined)
          : await attachmentFromPath({ path: sel.path, source: sel.source, slug: sel.source === "agent-workspace" ? sel.slug : undefined });
        setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, record: rec, uploading: false } : x));
      } catch (e) {
        setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, uploading: false, error: (e as Error).message } : x));
      }
    }
  }}
>📎</button>
```

Drag-drop: add `onDragOver={(e) => e.preventDefault()}` and `onDrop` on the chat surface:

```tsx
onDrop={(e) => {
  e.preventDefault();
  for (const f of Array.from(e.dataTransfer.files)) {
    const id = Math.random().toString(36).slice(2);
    setPendingAttachments((p) => [...p, { id, filename: f.name, size: f.size, uploading: true }]);
    uploadDiskFile(f, selectedChannel ?? undefined)
      .then((rec) => setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, record: rec, uploading: false } : x)))
      .catch((err) => setPendingAttachments((p) => p.map((x) => x.id === id ? { ...x, uploading: false, error: err.message } : x)));
  }
}}
```

Paste: on the composer input:

```tsx
onPaste={(e) => {
  const files = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
  if (files.length === 0) return;
  e.preventDefault();
  for (const f of files) { /* same upload flow as drag-drop */ }
}}
```

- [ ] **Step 6: Render AttachmentsBar above composer**

```tsx
<AttachmentsBar
  items={pendingAttachments}
  onRemove={(id) => setPendingAttachments((p) => p.filter((x) => x.id !== id))}
  onRetry={(id) => { /* re-run upload for that id */ }}
/>
```

- [ ] **Step 7: Attach `attachments` on message send**

In `sendMessage`, after assembling the message body:

```tsx
const attachments = pendingAttachments
  .filter((a) => a.record && !a.error)
  .map((a) => a.record!);
// POST with attachments
```

After successful send, clear the bar: `setPendingAttachments([])`.

- [ ] **Step 8: "?" icon in chat header**

Add next to the ⓘ settings icon:

```tsx
<a
  aria-label="Open chat guide"
  href="https://github.com/jaylfc/tinyagentos/blob/master/docs/chat-guide.md"
  target="_blank"
  rel="noreferrer"
  className="ml-1 opacity-60 hover:opacity-100"
>?</a>
```

- [ ] **Step 9: Build + test**

Run: `cd desktop && npm run build`
Expected: passes.

Run: `cd desktop && npm test -- --run`
Expected: same pass/fail count as base (3 pre-existing snap-zones); no new failures.

- [ ] **Step 10: Commit**

```bash
git add desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): integrate threads, attachments, hover actions, ? icon into MessagesApp"
```

---

## Task 18: Rebuild desktop bundle

- [ ] **Step 1: Build**

```bash
cd desktop && npm run build
```

- [ ] **Step 2: Commit**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
git add -A static/desktop desktop/tsconfig.tsbuildinfo
git commit -m "build: rebuild desktop bundle for chat Phase 2b-1"
```

---

## Task 19: Playwright E2E

**Files:**
- Create: `tests/e2e/test_chat_phase2b1.py`

- [ ] **Step 1: Write env-gated tests**

```python
# tests/e2e/test_chat_phase2b1.py
"""Phase 2b-1 desktop E2E.

Requires TAOS_E2E_URL set; skipped locally.
"""
import os
import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("TAOS_E2E_URL"),
                       reason="TAOS_E2E_URL required"),
]
URL = os.environ.get("TAOS_E2E_URL", "")


def test_thread_panel_opens_from_hover_and_persists_reply(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    # Hover first message
    first_msg = page.locator("[data-message-id]").first
    first_msg.hover()
    page.get_by_role("button", name="Reply in thread").click()
    expect(page.get_by_role("complementary", name="Thread")).to_be_visible()
    composer = page.get_by_placeholder("Reply in thread…")
    composer.fill("hello thread")
    composer.press("Enter")
    # The reply should appear inside the panel
    expect(page.get_by_text("hello thread")).to_be_visible()


def test_paperclip_opens_file_picker(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="Attach files").click()
    expect(page.get_by_role("dialog", name="Pick a file")).to_be_visible()
    page.get_by_role("button", name="Cancel").click()
    expect(page.get_by_role("dialog", name="Pick a file")).not_to_be_visible()


def test_help_posts_system_message(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    composer = page.get_by_placeholder("Message")
    composer.fill("/help threads")
    composer.press("Enter")
    expect(page.get_by_text(/narrow routing|threads/i)).to_be_visible()
```

- [ ] **Step 2: Confirm SKIP locally + commit**

Run: `PYTHONPATH=. pytest tests/e2e/test_chat_phase2b1.py -v`
Expected: SKIPPED.

```bash
git add tests/e2e/test_chat_phase2b1.py
git commit -m "test(e2e): chat Phase 2b-1 — thread panel, paperclip picker, /help"
```

---

## Final verification

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=. pytest tests/ -x -q`
Expected: no new failures vs master baseline.

Run: `cd desktop && npm test -- --run`
Expected: same baseline (3 pre-existing snap-zones).

Run: `cd desktop && npm run build`
Expected: clean.

- [ ] **Step 2: Open PR**

```bash
git push -u origin feat/chat-phase-2b-1-threads-attachments
gh pr create --base master \
  --title "Chat Phase 2b-1 — threads + attachments + shared file picker + chat guide" \
  --body-file docs/superpowers/specs/2026-04-19-chat-phase-2b-1-threads-attachments-design.md
```
