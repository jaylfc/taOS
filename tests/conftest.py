import hashlib
import hmac
import json as _json
import os
import sqlite3
import sys
import time

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.routes.desktop import SPA_DIR


# ---------------------------------------------------------------------------
# Cluster HMAC pairing helpers (used by cluster tests across multiple files)
# ---------------------------------------------------------------------------

def _cluster_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def sign_worker_request(
    key: bytes,
    name: str,
    method: str,
    path: str,
    body: bytes,
) -> dict:
    """Return the three HMAC auth headers for a worker request."""
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    sig = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-TAOS-Worker-Name": name,
        "X-TAOS-Timestamp": ts,
        "X-TAOS-Signature": sig,
    }


async def _pair_and_register_worker(
    client,
    app,
    payload: dict,
    code_prefix: str = "test-pairing-code",
) -> object:
    """Pair a worker and POST to /api/cluster/workers with HMAC auth.

    Drives the full announce -> confirm -> claim flow to obtain a signing
    key, then sends the registration request with the correct headers.
    Returns the httpx Response from the final POST.
    """
    name = payload["name"]
    url = payload.get("url", "http://localhost:9000")
    platform = payload.get("platform", "linux")
    code = code_prefix + name

    await app.state.cluster_pairing.init()
    ch = _cluster_code_hash(code)

    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": name, "url": url, "platform": platform, "code_hash": ch},
    )
    assert resp.status_code == 200, f"announce failed for {name!r}: {resp.text}"

    resp = await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": name, "code": code},
    )
    assert resp.status_code == 200, f"confirm failed for {name!r}: {resp.text}"

    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": name, "code": code},
    )
    assert resp.status_code == 200, f"claim failed for {name!r}: {resp.text}"
    key = bytes.fromhex(resp.json()["signing_key"])

    body = _json.dumps(payload).encode()
    headers = sign_worker_request(key, name, "POST", "/api/cluster/workers", body)
    return await client.post(
        "/api/cluster/workers",
        content=body,
        headers={**headers, "content-type": "application/json"},
    )


@pytest.fixture
def pair_and_register_worker():
    """Function fixture so test files in any directory can use the pairing
    helper without importing from conftest (tests/ is not a package, so
    ``from tests.conftest import ...`` breaks under CI's import mode)."""
    return _pair_and_register_worker


# macOS + Python 3.14: after the interpreter loads ObjC-backed extension
# modules (psutil, zeroconf, Pillow, lxml …), forking a child process with
# subprocess violates macOS's "unsafe after ObjC runtime init" restriction and
# produces SIGSEGV in git/bash children (exit code -11).  Setting this env var
# tells the ObjC runtime to skip the fork-safety check in child processes.
# The variable propagates automatically to every subprocess the test suite
# spawns; it is a no-op on Linux (ignored) so CI is unaffected.
if sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def _patch_aiosqlite_daemon_threads():
    """Patch aiosqlite's Connection so its worker thread is a daemon thread.

    When aiosqlite connections are not explicitly closed before the asyncio
    event loop shuts down, their background worker threads remain blocked on
    SimpleQueue.get().  Because the thread is NOT a daemon, Python's
    interpreter shutdown joins it — waiting forever for a thread that will
    never receive the stop sentinel.  This causes pytest to hang for tens of
    minutes after printing the test summary.

    Observed on CI (Python 3.12 / 3.13, Ubuntu): after the suite finishes
    pytest is killed by the 45-minute Actions timeout rather than exiting
    normally.  The same underlying issue causes a SIGSEGV on Python 3.14
    macOS when the semaphore is torn down under the blocked thread.

    Fix (two layers):
    1. Mark the worker thread daemon=True so interpreter shutdown kills it
       instead of joining it — avoids the indefinite block.
    2. Guard call_soon_threadsafe with an is_closed() pre-check so the
       worker does not crash if it receives a future tied to a dead loop.

    Applied to all supported Python versions (3.11+) because the hang
    reproduces on 3.12 and 3.13 in CI.  The patch is safe: daemon=True
    only affects abnormal exit (loop closed before Connection.close());
    normal teardown still sends the stop sentinel via the queue.
    """
    import aiosqlite.core as _core
    from threading import Thread

    _STOP = _core._STOP_RUNNING_SENTINEL

    def _threadsafe_call(loop, callback, *args):
        """Deliver result/exception only if the event loop is still alive."""
        try:
            if not loop.is_closed():
                loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # Race: loop closed between the is_closed() check and the call.
            pass

    def _safe_worker(tx):
        while True:
            future, function = tx.get()
            try:
                result = function()
                if future:
                    _threadsafe_call(
                        future.get_loop(), _core.set_result, future, result
                    )
                if result is _STOP:
                    break
            except BaseException as exc:
                if future:
                    _threadsafe_call(
                        future.get_loop(), _core.set_exception, future, exc
                    )

    _core._connection_worker_thread = _safe_worker

    # Monkey-patch Connection.__init__ to mark the worker thread daemon so
    # interpreter shutdown does not wait (and deadlock) on it.
    _orig_init = _core.Connection.__init__

    def _patched_init(self, connector, iter_chunk_size, loop=None):
        _orig_init(self, connector, iter_chunk_size, loop)
        self._thread.daemon = True

    _core.Connection.__init__ = _patched_init


def pytest_configure(config):
    """Stub the SPA bundle so the test suite doesn't depend on a real
    `npm run build`. Two tests need actual files on disk to exercise
    desktop routes (test_root_redirects_to_desktop checks the body
    contains "taOS"; the sw.js header test reads the file). Building
    the real bundle in every CI matrix job added ~3-5 min × 3 — the
    SPA build itself stays covered by the lint job. Stubs are only
    created when the file is missing so a real local build is left
    untouched."""
    SPA_DIR.mkdir(parents=True, exist_ok=True)
    stubs = {
        "index.html": "<!doctype html><title>taOS</title>",
        "chat.html": "<!doctype html><title>taOS chat</title>",
        "sw.js": "// stub service worker for tests\n",
    }
    for name, body in stubs.items():
        f = SPA_DIR / name
        if not f.exists():
            f.write_text(body)

    # Apply the aiosqlite daemon-thread patch unconditionally: the hang
    # (pytest blocked after test summary) reproduces on 3.12 and 3.13 in
    # CI, not just on 3.14.  The SIGSEGV on 3.14 macOS has the same root
    # cause.  daemon=True is safe for all supported versions.
    _patch_aiosqlite_daemon_threads()


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with a default test config."""
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    # Mark setup as complete so first-boot redirect does not interfere with tests
    (tmp_path / ".setup_complete").touch()
    return tmp_path


@pytest.fixture
def app(tmp_data_dir):
    """Create a TinyAgentOS app with test config."""
    return create_app(data_dir=tmp_data_dir)


@pytest_asyncio.fixture
async def client(app, tmp_data_dir):
    """Async test client with metrics store initialised and proper teardown."""
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    notif_store = app.state.notifications
    if notif_store._db is not None:
        await notif_store.close()
    await notif_store.init()
    await app.state.qmd_client.init()
    secrets_store = app.state.secrets
    if secrets_store._db is not None:
        await secrets_store.close()
    await secrets_store.init()
    scheduler = app.state.scheduler
    if scheduler._db is not None:
        await scheduler.close()
    await scheduler.init()
    channel_store = app.state.channels
    if channel_store._db is not None:
        await channel_store.close()
    await channel_store.init()
    relationship_mgr = app.state.relationships
    if relationship_mgr._db is not None:
        await relationship_mgr.close()
    await relationship_mgr.init()
    conversion_mgr = app.state.conversion
    if conversion_mgr._db is not None:
        await conversion_mgr.close()
    await conversion_mgr.init()
    training_mgr = app.state.training
    if training_mgr._db is not None:
        await training_mgr.close()
    await training_mgr.init()
    agent_messages = app.state.agent_messages
    if agent_messages._db is not None:
        await agent_messages.close()
    await agent_messages.init()
    shared_folders = app.state.shared_folders
    if shared_folders._db is not None:
        await shared_folders.close()
    await shared_folders.init()
    streaming_sessions = app.state.streaming_sessions
    if streaming_sessions._db is not None:
        await streaming_sessions.close()
    await streaming_sessions.init()
    expert_agents = app.state.expert_agents
    if expert_agents._db is not None:
        await expert_agents.close()
    await expert_agents.init()
    chat_messages = app.state.chat_messages
    if chat_messages._db is not None:
        await chat_messages.close()
    await chat_messages.init()
    chat_channels = app.state.chat_channels
    if chat_channels._db is not None:
        await chat_channels.close()
    await chat_channels.init()
    project_store = app.state.project_store
    if project_store._db is not None:
        await project_store.close()
    await project_store.init()
    project_task_store = app.state.project_task_store
    if project_task_store._db is not None:
        await project_task_store.close()
    await project_task_store.init()
    app.state.projects_root.mkdir(parents=True, exist_ok=True)
    canvas_store = app.state.canvas_store
    if canvas_store._db is not None:
        await canvas_store.close()
    await canvas_store.init()
    themes = app.state.themes
    if themes._db is not None:
        await themes.close()
    await themes.init()
    # BrowserApp v2 stores
    from tinyagentos.routes.desktop_browser.store import BrowserStore, BrowserCookieStore
    _browser_store = BrowserStore(tmp_data_dir / "browser.sqlite3")
    await _browser_store.init()
    app.state.browser_store = _browser_store
    _browser_cookie_store = BrowserCookieStore(
        tmp_data_dir / "browser_cookies.sqlite3",
        key_hex="0" * 64,
    )
    await _browser_cookie_store.init()
    app.state.browser_cookie_store = _browser_cookie_store
    # Lifespan-owned objects set to None by create_app() — tests that bypass
    # the lifespan need these initialised so routes don't fail on NoneType.
    from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore, CopilotHub
    app.state.copilot_ticket_store = CopilotTicketStore()
    app.state.copilot_hub = CopilotHub()
    # Auth middleware requires a configured user — set up a test admin so all
    # routes respond normally instead of returning 401 needs_onboarding.
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _record = app.state.auth.find_user("admin")
    _uid = _record["id"] if _record else ""
    _token = app.state.auth.create_session(user_id=_uid, long_lived=True)
    # Mark startup complete so the guard middleware lets test requests through.
    # The test client bypasses the lifespan, so we set this manually after all
    # stores have been manually initialized above.
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
    ) as c:
        yield c
    await canvas_store.close()
    await project_task_store.close()
    await project_store.close()
    await chat_channels.close()
    await chat_messages.close()
    await expert_agents.close()
    await streaming_sessions.close()
    await shared_folders.close()
    await agent_messages.close()
    await conversion_mgr.close()
    await training_mgr.close()
    await relationship_mgr.close()
    await channel_store.close()
    await scheduler.close()
    await secrets_store.close()
    await notif_store.close()
    await store.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()
    await _browser_store.close()
    await _browser_cookie_store.close()


def create_test_qmd_db(db_path):
    """Create a minimal QMD-compatible SQLite database for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
            hash TEXT NOT NULL, created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(collection, path)
        )
    """)
    conn.execute("CREATE TABLE content_vectors (hash TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, pos INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL, embedded_at TEXT NOT NULL, PRIMARY KEY (hash, seq))")
    conn.execute("CREATE TABLE store_collections (name TEXT PRIMARY KEY, path TEXT NOT NULL, pattern TEXT NOT NULL DEFAULT '**/*.md')")
    conn.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(filepath, title, body, tokenize='porter unicode61')")
    conn.execute("INSERT INTO content VALUES ('abc123', 'Meeting notes about Q2 roadmap and budget planning', '2026-04-01')")
    conn.execute("INSERT INTO content VALUES ('def456', 'Python tutorial on async programming with asyncio', '2026-04-02')")
    conn.execute("INSERT INTO content VALUES ('ghi789', 'Weekly standup: discussed deployment pipeline issues', '2026-04-03')")
    conn.execute("INSERT INTO documents VALUES (1, 'transcripts', 'meeting-q2.md', 'Q2 Roadmap Meeting', 'abc123', '2026-04-01', '2026-04-01', 1)")
    conn.execute("INSERT INTO documents VALUES (2, 'notes', 'async-python.md', 'Async Python', 'def456', '2026-04-02', '2026-04-02', 1)")
    conn.execute("INSERT INTO documents VALUES (3, 'transcripts', 'standup-apr3.md', 'Weekly Standup', 'ghi789', '2026-04-03', '2026-04-03', 1)")
    conn.execute("INSERT INTO content_vectors VALUES ('abc123', 0, 0, 'qwen3-embedding', '2026-04-01')")
    conn.execute("INSERT INTO content_vectors VALUES ('def456', 0, 0, 'qwen3-embedding', '2026-04-02')")
    conn.execute("INSERT INTO content_vectors VALUES ('ghi789', 0, 0, 'qwen3-embedding', '2026-04-03')")
    conn.execute("INSERT INTO store_collections VALUES ('transcripts', '/data/transcripts', '**/*.md')")
    conn.execute("INSERT INTO store_collections VALUES ('notes', '/data/notes', '**/*.md')")
    conn.execute("INSERT INTO documents_fts (rowid, filepath, title, body) VALUES (1, 'transcripts/meeting-q2.md', 'Q2 Roadmap Meeting', 'Meeting notes about Q2 roadmap and budget planning')")
    conn.execute("INSERT INTO documents_fts (rowid, filepath, title, body) VALUES (2, 'notes/async-python.md', 'Async Python', 'Python tutorial on async programming with asyncio')")
    conn.execute("INSERT INTO documents_fts (rowid, filepath, title, body) VALUES (3, 'transcripts/standup-apr3.md', 'Weekly Standup', 'Weekly standup: discussed deployment pipeline issues')")
    conn.commit()
    conn.close()


@pytest.fixture
def qmd_db_path(tmp_path):
    """Create a test QMD database and return its path."""
    db_path = tmp_path / "index.sqlite"
    create_test_qmd_db(db_path)
    return db_path


@pytest.fixture
def app_with_qmd(tmp_data_dir, tmp_path, monkeypatch):
    """Create app with a QMD database available for the test-agent."""
    qmd_cache = tmp_path / "qmd_cache"
    qmd_cache.mkdir()
    create_test_qmd_db(qmd_cache / "test.sqlite")

    _app = create_app(data_dir=tmp_data_dir)

    import tinyagentos.agent_db as agent_db_mod
    monkeypatch.setattr(agent_db_mod, "QMD_CACHE_DIR", qmd_cache)

    return _app


@pytest_asyncio.fixture
async def client_with_qmd(app_with_qmd):
    """Async test client with QMD database available."""
    store = app_with_qmd.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    notif_store = app_with_qmd.state.notifications
    if notif_store._db is not None:
        await notif_store.close()
    await notif_store.init()
    await app_with_qmd.state.qmd_client.init()
    secrets_store = app_with_qmd.state.secrets
    if secrets_store._db is not None:
        await secrets_store.close()
    await secrets_store.init()
    scheduler = app_with_qmd.state.scheduler
    if scheduler._db is not None:
        await scheduler.close()
    await scheduler.init()
    channel_store = app_with_qmd.state.channels
    if channel_store._db is not None:
        await channel_store.close()
    await channel_store.init()
    relationship_mgr = app_with_qmd.state.relationships
    if relationship_mgr._db is not None:
        await relationship_mgr.close()
    await relationship_mgr.init()
    conversion_mgr = app_with_qmd.state.conversion
    if conversion_mgr._db is not None:
        await conversion_mgr.close()
    await conversion_mgr.init()
    training_mgr = app_with_qmd.state.training
    if training_mgr._db is not None:
        await training_mgr.close()
    await training_mgr.init()
    agent_messages = app_with_qmd.state.agent_messages
    if agent_messages._db is not None:
        await agent_messages.close()
    await agent_messages.init()
    shared_folders = app_with_qmd.state.shared_folders
    if shared_folders._db is not None:
        await shared_folders.close()
    await shared_folders.init()
    streaming_sessions = app_with_qmd.state.streaming_sessions
    if streaming_sessions._db is not None:
        await streaming_sessions.close()
    await streaming_sessions.init()
    expert_agents = app_with_qmd.state.expert_agents
    if expert_agents._db is not None:
        await expert_agents.close()
    await expert_agents.init()
    chat_messages = app_with_qmd.state.chat_messages
    if chat_messages._db is not None:
        await chat_messages.close()
    await chat_messages.init()
    chat_channels = app_with_qmd.state.chat_channels
    if chat_channels._db is not None:
        await chat_channels.close()
    await chat_channels.init()
    project_store = app_with_qmd.state.project_store
    if project_store._db is not None:
        await project_store.close()
    await project_store.init()
    project_task_store = app_with_qmd.state.project_task_store
    if project_task_store._db is not None:
        await project_task_store.close()
    await project_task_store.init()
    app_with_qmd.state.projects_root.mkdir(parents=True, exist_ok=True)
    canvas_store = app_with_qmd.state.canvas_store
    if canvas_store._db is not None:
        await canvas_store.close()
    await canvas_store.init()
    app_with_qmd.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _record = app_with_qmd.state.auth.find_user("admin")
    _uid = _record["id"] if _record else ""
    _token = app_with_qmd.state.auth.create_session(user_id=_uid, long_lived=True)
    app_with_qmd.state._startup_complete = True
    transport = ASGITransport(app=app_with_qmd)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
    ) as c:
        yield c
    await canvas_store.close()
    await project_task_store.close()
    await project_store.close()
    await chat_channels.close()
    await chat_messages.close()
    await expert_agents.close()
    await streaming_sessions.close()
    await shared_folders.close()
    await agent_messages.close()
    await conversion_mgr.close()
    await training_mgr.close()
    await relationship_mgr.close()
    await channel_store.close()
    await scheduler.close()
    await secrets_store.close()
    await notif_store.close()
    await store.close()
    await app_with_qmd.state.qmd_client.close()
    await app_with_qmd.state.http_client.aclose()
