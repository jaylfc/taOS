import sqlite3

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.routes.desktop import SPA_DIR


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
    # Auth middleware requires a configured user — set up a test admin so all
    # routes respond normally instead of returning 401 needs_onboarding.
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _record = app.state.auth.find_user("admin")
    _uid = _record["id"] if _record else ""
    _token = app.state.auth.create_session(user_id=_uid, long_lived=True)
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
