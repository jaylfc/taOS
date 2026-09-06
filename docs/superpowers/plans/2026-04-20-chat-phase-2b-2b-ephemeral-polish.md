# Chat Phase 2b-2b/c — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship ephemeral messages + 3 polish features (in-app help panel, all-threads list, lightbox zoom) in one PR.

**Architecture:** Additive `expires_at` column + background sweep task + 2 new GET endpoints + 3 new components + extended ChannelSettings + extended AttachmentLightbox.

---

## Task 1: `expires_at` column + ephemeral sweep

**Files:**
- Modify: `tinyagentos/chat/message_store.py` (schema + `send_message` kwarg + sweep helper)
- Modify: `tinyagentos/app.py` (register periodic sweep)
- Test: `tests/test_chat_ephemeral.py` (new)

### Step 1: Schema + migration

In `tinyagentos/chat/message_store.py`, add column:

```python
# in MESSAGES_SCHEMA:
    expires_at REAL,
```

In `ChatMessageStore.init()`, add guarded migration (like `deleted_at`):

```python
try:
    await self._db.execute("ALTER TABLE chat_messages ADD COLUMN expires_at REAL")
    await self._db.commit()
except Exception:
    pass
```

### Step 2: `send_message` accepts `expires_at`

Add `expires_at: float | None = None` kwarg. Include in INSERT:

```python
"""INSERT INTO chat_messages
   (id, channel_id, thread_id, author_id, author_type, content,
    content_type, content_blocks, embeds, components, attachments,
    reactions, state, pinned, ephemeral, metadata, created_at, expires_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
```

Pass `expires_at` through in the tuple.

### Step 3: `sweep_expired` method

Add to `ChatMessageStore`:

```python
async def sweep_expired(self) -> list[str]:
    """Soft-delete messages past their expires_at. Returns list of deleted ids."""
    import time as _time
    now = _time.time()
    async with self._db.execute(
        "SELECT id, channel_id FROM chat_messages "
        "WHERE expires_at IS NOT NULL AND expires_at < ? AND deleted_at IS NULL",
        (now,),
    ) as cursor:
        rows = await cursor.fetchall()
    ids = []
    for row in rows:
        await self.soft_delete_message(row[0])
        ids.append((row[0], row[1]))
    return ids
```

### Step 4: Tests

`tests/test_chat_ephemeral.py`:

```python
import pytest, time
from tinyagentos.chat.message_store import ChatMessageStore


@pytest.mark.asyncio
async def test_send_message_with_expires_at(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    exp = time.time() + 3600
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="ephemeral", expires_at=exp,
    )
    got = await store.get_message(msg["id"])
    assert got["expires_at"] is not None
    assert abs(got["expires_at"] - exp) < 1


@pytest.mark.asyncio
async def test_send_message_without_expires_at_defaults_null(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="not ephemeral",
    )
    got = await store.get_message(msg["id"])
    assert got["expires_at"] is None


@pytest.mark.asyncio
async def test_sweep_expired_soft_deletes(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    past = time.time() - 10
    future = time.time() + 3600
    expired_msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="gone", expires_at=past,
    )
    live_msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="live", expires_at=future,
    )
    swept = await store.sweep_expired()
    assert len(swept) == 1
    got = await store.get_message(expired_msg["id"])
    assert got["deleted_at"] is not None
    got_live = await store.get_message(live_msg["id"])
    assert got_live["deleted_at"] is None
```

### Step 5: Register periodic sweep

In `tinyagentos/app.py`, find the startup/lifespan block. Add background task:

```python
async def _ephemeral_sweep_loop(app):
    import asyncio as _asyncio
    store = app.state.chat_messages
    hub = getattr(app.state, "chat_hub", None)
    while True:
        try:
            deleted = await store.sweep_expired()
            if hub is not None:
                for mid, cid in deleted:
                    await hub.broadcast(cid, {
                        "type": "message_delete",
                        "seq": hub.next_seq(),
                        "channel_id": cid,
                        "message_id": mid,
                        "deleted_at": __import__("time").time(),
                    })
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("ephemeral sweep failed: %s", e)
        await _asyncio.sleep(300)  # 5 min
```

Register on startup (use the existing lifespan pattern — look for other `asyncio.create_task(...)` in app.py).

### Step 6: Commit

```bash
git add tinyagentos/chat/message_store.py tinyagentos/app.py tests/test_chat_ephemeral.py
git commit -m "feat(chat): expires_at column + periodic sweep for ephemeral messages"
```

---

## Task 2: Channel settings `ephemeral_ttl_seconds` + POST /messages passes expires_at

**Files:**
- Modify: `tinyagentos/routes/chat.py` (`POST /api/chat/messages` computes expires_at)
- Modify: `tinyagentos/chat/channel_store.py` (if settings has an allow-list for keys, extend it to include `ephemeral_ttl_seconds`)

### Step 1: Extend POST /messages

In `post_message` handler, after fetching channel, compute expires_at:

```python
channel = await ch_store.get_channel(channel_id)
ttl = None
if channel and channel.get("settings"):
    ttl = channel["settings"].get("ephemeral_ttl_seconds")
import time as _time
expires_at = (_time.time() + ttl) if isinstance(ttl, (int, float)) and ttl > 0 else None
```

Pass `expires_at=expires_at` into `msg_store.send_message(...)`.

### Step 2: Do the same for WS `message` handler in chat.py

Same logic at `elif msg_type == "message":` branch.

### Step 3: Test

Extend `tests/test_chat_ephemeral.py`:

```python
@pytest.mark.asyncio
async def test_post_message_in_ephemeral_channel_sets_expires_at(tmp_path):
    # create app, create channel with ephemeral_ttl_seconds in settings,
    # POST a message, verify expires_at is set.
    ...
```

Skip if too involved — unit coverage in Task 1 is sufficient.

### Step 4: Commit

```bash
git add tinyagentos/routes/chat.py tests/test_chat_ephemeral.py
git commit -m "feat(chat): routes honor channel ephemeral_ttl_seconds when sending"
```

---

## Task 3: `GET /api/chat/channels/{id}/threads`

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Modify: `tinyagentos/chat/message_store.py` (`get_channel_threads` method)
- Test: `tests/test_chat_threads.py` (extend)

### Step 1: Store method

```python
async def get_channel_threads(self, channel_id: str) -> list[dict]:
    """Return parents of all threads in a channel, with reply counts."""
    async with self._db.execute(
        """SELECT
             parent.*,
             COUNT(reply.id) AS reply_count,
             MAX(reply.created_at) AS last_reply_at
           FROM chat_messages parent
           INNER JOIN chat_messages reply
             ON reply.thread_id = parent.id
             AND reply.channel_id = parent.channel_id
             AND reply.deleted_at IS NULL
           WHERE parent.channel_id = ?
             AND parent.deleted_at IS NULL
           GROUP BY parent.id
           ORDER BY last_reply_at DESC""",
        (channel_id,),
    ) as cursor:
        rows = await cursor.fetchall()
        description = cursor.description
    return [_parse(row, description) for row in rows]
```

### Step 2: Endpoint

```python
@router.get("/api/chat/channels/{channel_id}/threads")
async def get_channel_threads_endpoint(channel_id: str, request: Request):
    store = request.app.state.chat_messages
    threads = await store.get_channel_threads(channel_id)
    return JSONResponse({"threads": threads})
```

### Step 3: Test

Extend `tests/test_chat_threads.py`:

```python
@pytest.mark.asyncio
async def test_get_channel_threads_endpoint(tmp_path):
    app, client = await _authed_thread_client(tmp_path)
    async with client:
        ch_r = await client.post("/api/chat/channels",
            json={"name":"g","type":"group","description":"","topic":"",
                  "members":["user","tom"],"created_by":"user"})
        ch_id = ch_r.json()["id"]
        p1 = await client.post("/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "parent one", "content_type": "text"})
        parent_id = p1.json()["id"]
        await client.post("/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "reply", "content_type": "text", "thread_id": parent_id})
        r = await client.get(f"/api/chat/channels/{ch_id}/threads")
        assert r.status_code == 200
        data = r.json()
        assert len(data["threads"]) == 1
        assert data["threads"][0]["id"] == parent_id
        assert data["threads"][0]["reply_count"] == 1
```

### Step 4: Commit

```bash
git add tinyagentos/chat/message_store.py tinyagentos/routes/chat.py tests/test_chat_threads.py
git commit -m "feat(chat): GET /channels/{id}/threads lists thread parents with reply counts"
```

---

## Task 4: `GET /api/docs/chat-guide`

**File:** `tinyagentos/routes/chat.py` (or a new `tinyagentos/routes/docs.py`)

### Step 1: Endpoint

```python
@router.get("/api/docs/chat-guide")
async def get_chat_guide():
    from pathlib import Path as _Path
    # Locate the guide relative to the package install — use the same
    # logic as the static file handlers.
    guide = _Path(__file__).resolve().parent.parent.parent / "docs" / "chat-guide.md"
    if not guide.exists():
        return JSONResponse({"error": "guide not found"}, status_code=404)
    return JSONResponse({"markdown": guide.read_text(encoding="utf-8")})
```

### Step 2: Commit

```bash
git add tinyagentos/routes/chat.py
git commit -m "feat(chat): GET /api/docs/chat-guide serves markdown for in-app help panel"
```

---

## Task 5: Frontend — ChannelSettingsPanel ephemeral dropdown + header badge

**File:** `desktop/src/apps/chat/ChannelSettingsPanel.tsx`

### Step 1: Add dropdown

Find the existing settings fields (mode, max_hops, etc.). Add:

```tsx
<div>
  <label className="block text-xs text-white/60 mb-1">Disappearing messages</label>
  <select
    value={String(settings.ephemeral_ttl_seconds ?? 0)}
    onChange={(e) => setSettings({
      ...settings,
      ephemeral_ttl_seconds: Number(e.target.value) || null,
    })}
    className="w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-sm"
  >
    <option value="0">Off</option>
    <option value="3600">1 hour</option>
    <option value="86400">24 hours</option>
    <option value="604800">7 days</option>
    <option value="2592000">30 days</option>
  </select>
</div>
```

### Step 2: MessagesApp header badge

In the channel header, near the `ⓘ` settings icon, add:

```tsx
{currentChannel?.settings?.ephemeral_ttl_seconds && (
  <span
    className="ml-1 text-xs text-amber-300/80"
    title={`Messages disappear after ${formatTTL(currentChannel.settings.ephemeral_ttl_seconds)}`}
  >
    ⏳ {formatTTL(currentChannel.settings.ephemeral_ttl_seconds)}
  </span>
)}
```

Add a `formatTTL(seconds)` helper: returns "1h", "24h", "7d", "30d".

### Step 3: Commit

```bash
git add desktop/src/apps/chat/ChannelSettingsPanel.tsx desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): channel ephemeral TTL dropdown + header badge"
```

---

## Task 6: HelpPanel component + replace external "?" link

**Files:**
- Create: `desktop/src/apps/chat/HelpPanel.tsx`
- Create: `desktop/src/apps/chat/__tests__/HelpPanel.test.tsx`
- Modify: `desktop/src/apps/MessagesApp.tsx` (replace `<a href=..."?" target="_blank">` with button + state)

### Step 1: Write failing test

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { HelpPanel } from "../HelpPanel";

describe("HelpPanel", () => {
  it("fetches and renders markdown", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ markdown: "# Hello\n\nguide body" }),
    }) as any;
    render(<HelpPanel onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/guide body/i)).toBeInTheDocument()
    );
  });

  it("Esc closes", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ markdown: "" }) }) as any;
    const onClose = vi.fn();
    render(<HelpPanel onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("backdrop click closes", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ markdown: "" }) }) as any;
    const onClose = vi.fn();
    render(<HelpPanel onClose={onClose} />);
    fireEvent.click(screen.getByTestId("help-panel-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });
});
```

### Step 2: Implement HelpPanel.tsx

```tsx
import { useEffect, useState } from "react";

export function HelpPanel({ onClose }: { onClose: () => void }) {
  const [markdown, setMarkdown] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/docs/chat-guide")
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d) => setMarkdown(d.markdown || ""))
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div
        data-testid="help-panel-backdrop"
        className="fixed inset-0 z-50 bg-black/60"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Chat guide"
        className="fixed inset-4 md:inset-10 z-50 bg-shell-surface border border-white/10 rounded-lg shadow-xl flex flex-col"
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <span className="text-sm font-semibold">Chat guide</span>
          <button onClick={onClose} aria-label="Close" className="p-1 hover:bg-white/5 rounded">✕</button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-4 prose prose-invert max-w-none text-sm">
          {error && <div role="alert" className="text-red-300">{error}</div>}
          {markdown && <pre className="whitespace-pre-wrap font-sans">{markdown}</pre>}
        </div>
      </div>
    </>
  );
}
```

(Simple pre-rendering is sufficient; real markdown formatting can come later. The content is already human-readable.)

### Step 3: Wire in MessagesApp

Replace the `<a aria-label="Open chat guide" href=...>?` with:

```tsx
<button
  aria-label="Open chat guide"
  onClick={() => setShowHelp(true)}
  className="ml-1 opacity-60 hover:opacity-100 text-[12px]"
>?</button>

{showHelp && <HelpPanel onClose={() => setShowHelp(false)} />}
```

Add `const [showHelp, setShowHelp] = useState(false);` to state.

### Step 4: Commit

```bash
git add desktop/src/apps/chat/HelpPanel.tsx desktop/src/apps/chat/__tests__/HelpPanel.test.tsx desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): in-app HelpPanel replaces external chat-guide link"
```

---

## Task 7: AllThreadsList panel

**Files:**
- Create: `desktop/src/apps/chat/AllThreadsList.tsx`
- Create: `desktop/src/apps/chat/__tests__/AllThreadsList.test.tsx`
- Modify: `desktop/src/apps/MessagesApp.tsx`

### Step 1: Test (smoke)

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AllThreadsList } from "../AllThreadsList";

describe("AllThreadsList", () => {
  it("empty state", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, json: () => Promise.resolve({ threads: [] }),
    }) as any;
    render(<AllThreadsList channelId="c1" onJump={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/no threads/i)).toBeInTheDocument());
  });

  it("renders thread rows", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, json: () => Promise.resolve({ threads: [{
        id: "m1", author_id: "tom", content: "thread parent",
        reply_count: 2, last_reply_at: Date.now() / 1000,
      }] }),
    }) as any;
    render(<AllThreadsList channelId="c1" onJump={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/thread parent/i)).toBeInTheDocument());
    expect(screen.getByText(/2 repl/)).toBeInTheDocument();
  });
});
```

### Step 2: Implement AllThreadsList

```tsx
import { useEffect, useState } from "react";

interface ThreadParent {
  id: string;
  author_id: string;
  content: string;
  reply_count: number;
  last_reply_at?: number | null;
}

export function AllThreadsList({
  channelId, onJump, onClose,
}: {
  channelId: string;
  onJump: (parentId: string) => void;
  onClose: () => void;
}) {
  const [threads, setThreads] = useState<ThreadParent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/chat/channels/${channelId}/threads`)
      .then((r) => r.ok ? r.json() : { threads: [] })
      .then((d) => setThreads(d.threads || []))
      .finally(() => setLoading(false));
  }, [channelId]);

  return (
    <aside
      role="complementary"
      aria-label="All threads"
      className="fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 flex flex-col z-40"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <span className="text-sm font-semibold">Threads</span>
        <button onClick={onClose} aria-label="Close" className="p-1 hover:bg-white/5 rounded">✕</button>
      </header>
      <div className="flex-1 overflow-y-auto">
        {loading && <div className="p-4 text-sm text-white/50">Loading…</div>}
        {!loading && threads.length === 0 && (
          <div className="p-4 text-sm text-white/50">No threads in this channel yet.</div>
        )}
        {!loading && threads.map((t) => (
          <button
            key={t.id}
            onClick={() => onJump(t.id)}
            className="w-full text-left p-3 border-b border-white/5 hover:bg-white/5"
          >
            <div className="text-xs opacity-60 mb-0.5">@{t.author_id}</div>
            <div className="text-sm line-clamp-2">{t.content}</div>
            <div className="text-xs text-sky-300/70 mt-1">
              💬 {t.reply_count} repl{t.reply_count === 1 ? "y" : "ies"}
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
```

### Step 3: MessagesApp integration

Add state:
```tsx
const [showAllThreads, setShowAllThreads] = useState(false);
```

Add button to channel header (right next to ⓘ):
```tsx
{currentChannel && currentChannel.type !== "dm" && (
  <button
    aria-label="All threads"
    onClick={() => { closeThread(); setShowSettings(false); setShowAllThreads(true); }}
    className="ml-1 opacity-60 hover:opacity-100 text-[12px]"
  >💬</button>
)}
```

Mount near ThreadPanel:
```tsx
{showAllThreads && selectedChannel && (
  <AllThreadsList
    channelId={selectedChannel}
    onJump={(parentId) => {
      setShowAllThreads(false);
      handleOpenThreadFor(selectedChannel, parentId);
    }}
    onClose={() => setShowAllThreads(false)}
  />
)}
```

Ensure mutex: opening AllThreadsList closes settings/thread; opening those closes AllThreadsList. Update handleOpenSettings + handleOpenThreadFor to `setShowAllThreads(false)`.

### Step 4: Commit

```bash
git add desktop/src/apps/chat/AllThreadsList.tsx desktop/src/apps/chat/__tests__/AllThreadsList.test.tsx desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): AllThreadsList panel to browse all threads in a channel"
```

---

## Task 8: AttachmentLightbox zoom

**File:** `desktop/src/apps/chat/AttachmentLightbox.tsx`

Add zoom state + handlers:

```tsx
const [zoom, setZoom] = useState(1);
const [pan, setPan] = useState({ x: 0, y: 0 });
```

Keyboard:
```tsx
if (e.key === "+") setZoom(z => Math.min(4, z * 1.2));
if (e.key === "-") setZoom(z => Math.max(1, z / 1.2));
if (e.key === "0") { setZoom(1); setPan({ x: 0, y: 0 }); }
```

Wheel:
```tsx
onWheel={(e) => {
  e.preventDefault();
  setZoom(z => Math.max(1, Math.min(4, z * (e.deltaY < 0 ? 1.1 : 0.9))));
}}
```

Double-click:
```tsx
onDoubleClick={(e) => {
  e.stopPropagation();
  setZoom(z => z === 1 ? 2 : 1);
  setPan({ x: 0, y: 0 });
}}
```

Pan when zoomed: pointer-drag updates `pan`. Apply transform:

```tsx
<img
  src={current.url}
  alt={current.filename}
  style={{ transform: `scale(${zoom}) translate(${pan.x}px, ${pan.y}px)` }}
  ...
/>
```

Reset zoom on image navigate:
```tsx
if (e.key === "ArrowLeft") { setZoom(1); setPan({ x: 0, y: 0 }); setIdx((i) => Math.max(0, i - 1)); }
```

### Commit

```bash
git add desktop/src/apps/chat/AttachmentLightbox.tsx
git commit -m "feat(desktop): AttachmentLightbox zoom + pan (keyboard + wheel + double-click)"
```

---

## Task 9: Rebuild bundle + E2E

### Rebuild

```bash
cd desktop && npm run build
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
git add -A static/desktop desktop/tsconfig.tsbuildinfo
git commit -m "build: rebuild desktop bundle for Phase 2b-2b/c"
```

### E2E stubs

Create `tests/e2e/test_chat_phase2b2b.py` (env-gated stubs):

```python
"""Phase 2b-2b/c E2E stubs.

Requires TAOS_E2E_URL set.
"""
import os
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("TAOS_E2E_URL"), reason="TAOS_E2E_URL required"),
]
URL = os.environ.get("TAOS_E2E_URL", "")


def test_help_panel_opens(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="Open chat guide").click()
    expect(page.get_by_role("dialog", name=re.compile("Chat guide", re.I))).to_be_visible()


def test_all_threads_list_opens(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="All threads").click()
    expect(page.get_by_role("complementary", name=re.compile("All threads", re.I))).to_be_visible()
```

Commit:
```bash
git add tests/e2e/test_chat_phase2b2b.py
git commit -m "test(e2e): Phase 2b-2b/c — help panel + all-threads stubs"
```

---

## Final

```bash
PYTHONPATH=. pytest tests/test_chat_ephemeral.py tests/test_chat_threads.py -v
cd desktop && npm test -- --run
cd desktop && npm run build

git push -u origin feat/chat-phase-2b-2b-ephemeral-polish
gh pr create --base master \
  --title "Chat Phase 2b-2b/c — ephemeral messages + in-app help + all-threads + lightbox zoom" \
  --body-file docs/superpowers/specs/2026-04-20-chat-phase-2b-2b-ephemeral-polish-design.md
```
