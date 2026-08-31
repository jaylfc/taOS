import hashlib
import hmac
import json as _json
import os
import sqlite3
import sys
import time
from unittest.mock import patch

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.routes.desktop import SPA_DIR


# ---------------------------------------------------------------------------
# CSRF in tests — ON by default, opted OUT explicitly.
#
# This used to be inverted: an autouse fixture no-op'd `verify_csrf` for every
# test file whose path did not contain the substring "test_csrf".  Measured on
# dev at the time of the change: 788 test files, exactly ONE inside that
# carve-out, so 787 ran against an app whose CSRF dependency did nothing, and
# 223 of those issue POSTs.
#
# That is an over-privileged fixture: it grants the test more privilege than
# the real caller has, so the test cannot observe the check the real caller
# must satisfy.  It is what hid #2081 (the CSRF login lockout).  A first repro
# written as an ordinary test returned 303 and PASSED, and the tell was that
# the CONTROL passed identically — the shape you get when the input never
# reaches the system under test.
#
# Two properties of the replacement matter:
#
#   * The default is the REAL implementation.  A test written tomorrow gets
#     production behaviour without anyone remembering to ask for it, and a new
#     CSRF regression is red by default rather than invisible by default.
#   * Opting out is an explicit MARKER, not a filename.  The old carve-out was
#     a substring match on the path, so renaming a file silently re-armed the
#     bypass with no failure anywhere.  A marker cannot be triggered by
#     accident, it is greppable, and `tests/test_csrf_bypass_debt.py` holds the
#     list of modules that still use it so the debt cannot grow unnoticed.
#
# To opt a whole module out, put this at module scope:
#
#     pytestmark = pytest.mark.csrf_bypass
#
# Do NOT add it to silence a new red.  A red here is a route that the real
# caller could not reach the way the test reaches it.
# ---------------------------------------------------------------------------

from starlette.requests import HTTPConnection as _HTTPConnection

CSRF_BYPASS_MARKER = "csrf_bypass"


def _noop_verify_csrf(conn: _HTTPConnection) -> None:
    # Typed as HTTPConnection (base of Request + WebSocket) so this override is
    # FastAPI-injectable on both http and websocket routes, matching the real
    # verify_csrf. A `Request` param TypeErrors on a websocket scope and
    # `Request | None` is not a valid injectable type at all.
    return


# The header-echoing hook lives in its own module so that the ~11 test modules
# building their own AsyncClient can import it without a bare
# `from conftest import ...` -- `tests/` is not a package and several
# conftest.py files exist, so that import binds whichever one is on sys.path
# first (card `tsk-xplzqy`).  Re-exported here under the old private names so
# the shared `client` fixture below reads unchanged.
from taos_test_csrf import (  # noqa: E402
    TEST_CSRF_TOKEN as _TEST_CSRF_TOKEN,
    csrf_event_hooks,
    echo_csrf_cookie_into_header as _echo_csrf_cookie_into_header,
)


@pytest.fixture(autouse=True)
def _bypass_csrf_in_tests(request):
    """Run against the REAL verify_csrf unless the test opts out.

    Opt out with ``@pytest.mark.csrf_bypass`` on the test, its class, or the
    module (``pytestmark``).  ``get_closest_marker`` sees all three.

    The patch must be in place BEFORE the app is built: `register_all_routers`
    does ``from ... import verify_csrf`` and freezes the resulting object into
    ``Depends(...)`` at ``include_router`` time, so patching the module
    attribute after ``create_app`` changes nothing.  Wrapping the whole test —
    as this fixture does — is what makes it take effect.
    """
    if request.node.get_closest_marker(CSRF_BYPASS_MARKER) is None:
        yield
        return

    with patch(
        "tinyagentos.middleware.csrf.verify_csrf",
        _noop_verify_csrf,
    ):
        yield


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

    # init() opens a fresh aiosqlite connection every call, so only run it
    # when the store has not been initialised yet (avoids leaking connections
    # in tests that pair multiple workers).
    if app.state.cluster_pairing._db is None:  # noqa: SLF001
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


# ---------------------------------------------------------------------------
# Core-dependency integrity guard.
#
# A stale or incomplete package install -- an empty directory left on sys.path
# that Python treats as a PEP 420 namespace package, or a partially-written
# artifact -- makes a module importable but missing its public API.  anyio
# calls sniffio.current_async_library on every async test; a partial sniffio
# raises AttributeError instead of ModuleNotFoundError, so the failure
# surfaces as 500+ identical tracebacks attributed to whichever PR happened
# to run (see tsk-2nvear).
#
# This guard catches the shape at session start with a single loud error and
# prints name==version, __file__ / __path__ / __spec__ /
# submodule_search_locations / the resolved package set so the cause is
# observable, not asserted. Both candidate causes (stale install vs. version
# bump) are named; the version discriminates.
#
# Each entry maps a module name to the attributes that MUST be present on it
# whenever the module is importable.  A module that is genuinely absent
# (ModuleNotFoundError) is fine -- code paths that need it already guard the
# import.  Only the importable-but-attribute-less shape is a defect.
# ---------------------------------------------------------------------------

_CORE_DEP_CONTRACTS: dict[str, tuple[str, ...]] = {
    "sniffio": ("current_async_library", "AsyncLibraryNotFoundError"),
    "anyio": ("run", "create_task_group", "from_thread"),
    "httpx": ("AsyncClient", "Client", "Response"),
    "httpcore": ("ConnectionPool", "AsyncConnectionPool", "Response"),
    "idna": ("encode", "decode"),
    "certifi": ("where",),
    "pydantic": ("BaseModel", "TypeAdapter"),
    "sqlcipher3": ("dbapi2", "connect"),
    "fastapi": ("FastAPI", "APIRouter"),
}


def _check_core_deps(
    contracts: dict[str, tuple[str, ...]] | None = None,
) -> list[tuple[str, tuple[str, ...], object]]:
    """Return a list of importable-but-attribute-less core dependencies.

    Each problem is a ``(module_name, missing_attrs, module)`` tuple. An empty
    list means every module is either absent (fine) or fully present (fine).
    """
    if contracts is None:
        contracts = _CORE_DEP_CONTRACTS
    import importlib

    problems: list[tuple[str, tuple[str, ...], object]] = []
    for mod_name, required_attrs in contracts.items():
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        missing = tuple(a for a in required_attrs if not hasattr(mod, a))
        if missing:
            problems.append((mod_name, missing, mod))
    return problems


def _module_version(mod_name: str) -> str:
    """Return the installed distribution version for *mod_name*, or
    ``"unknown"`` if no matching distribution is found."""
    import importlib.metadata
    try:
        return importlib.metadata.version(mod_name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _verify_core_deps() -> None:
    """Fail loudly at session start if a core dependency is
    importable-but-attribute-less.

    Prints ``name==version``, ``__file__``, ``__path__``, ``__spec__``,
    and ``submodule_search_locations`` for each reported module so the
    cause is observable, not asserted.
    """
    import importlib.metadata

    problems = _check_core_deps()
    if not problems:
        return

    lines = [
        "CORE DEP GUARD: importable-but-attribute-less dependencies detected.",
        "Observation: an importable module lacks an attribute its contract",
        "requires. Candidate causes: a stale or partial install (empty",
        "namespace package on sys.path) or a version bump that moved or",
        "removed the API. The version and import path discriminate between",
        "them; both are reported below so neither cause is assumed.",
        "",
        "Diagnosis:",
    ]
    for mod_name, missing_attrs, mod in problems:
        version = _module_version(mod_name)
        spec = getattr(mod, "__spec__", None)
        search_locs = (
            getattr(spec, "submodule_search_locations", None) if spec else None
        )
        lines.append(f"  module: {mod_name}=={version}")
        lines.append(f"    missing attributes: {', '.join(missing_attrs)}")
        lines.append(f"    __file__ = {getattr(mod, '__file__', None)!r}")
        lines.append(f"    __path__ = {getattr(mod, '__path__', None)!r}")
        lines.append(f"    __spec__ = {spec!r}")
        lines.append(f"    submodule_search_locations = {search_locs!r}")
    dists = sorted(
        (f"{(d.metadata['Name'] or 'unknown')}=={d.version}"
         for d in importlib.metadata.distributions()),
        key=lambda s: s.lower(),
    )
    lines.append("")
    lines.append("Installed packages:")
    for dist in dists:
        lines.append(f"  {dist}")
    raise RuntimeError("\n".join(lines))


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

    # Fail loudly if a core transitive dependency is importable but
    # attribute-less (stale namespace package, incomplete install).
    # Catches the sniffio/anyio breakage shape before it turns into 500+
    # opaque AttributeErrors scattered across unrelated tests.
    _verify_core_deps()


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
    broker_store = app.state.broker_store
    if broker_store._db is not None:
        await broker_store.close()
    await broker_store.init()
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
    project_invites = app.state.project_invites
    if project_invites._db is not None:
        await project_invites.close()
    await project_invites.init()
    board_audit = app.state.board_audit
    if board_audit._db is not None:
        await board_audit.close()
    await board_audit.init()
    receipt_store = app.state.receipt_store
    if receipt_store._db is not None:
        await receipt_store.close()
    await receipt_store.init()
    task_strikes = app.state.task_strikes
    if task_strikes._db is not None:
        await task_strikes.close()
    await task_strikes.init()
    project_task_store = app.state.project_task_store
    if project_task_store._db is not None:
        await project_task_store.close()
    await project_task_store.init()
    project_element_store = app.state.project_element_store
    if project_element_store._db is not None:
        await project_element_store.close()
    await project_element_store.init()
    project_notes_store = app.state.project_notes_store
    if project_notes_store._db is not None:
        await project_notes_store.close()
    await project_notes_store.init()
    project_lists_store = app.state.project_lists_store
    if project_lists_store._db is not None:
        await project_lists_store.close()
    await project_lists_store.init()
    project_list_entries_store = app.state.project_list_entries_store
    if project_list_entries_store._db is not None:
        await project_list_entries_store.close()
    await project_list_entries_store.init()
    routine_store = app.state.routine_store
    if routine_store._db is not None:
        await routine_store.close()
    await routine_store.init()
    decision_store = app.state.decision_store
    if decision_store._db is not None:
        await decision_store.close()
    await decision_store.init()
    execution_policies = app.state.execution_policies
    if execution_policies._db is not None:
        await execution_policies.close()
    await execution_policies.init()
    coding_session_store = app.state.coding_session_store
    if coding_session_store._db is not None:
        await coding_session_store.close()
    await coding_session_store.init()
    container_request_store = app.state.container_request_store
    if container_request_store._db is not None:
        await container_request_store.close()
    await container_request_store.init()
    app.state.projects_root.mkdir(parents=True, exist_ok=True)
    canvas_store = app.state.canvas_store
    if canvas_store._db is not None:
        await canvas_store.close()
    await canvas_store.init()
    themes = app.state.themes
    if themes._db is not None:
        await themes.close()
    await themes.init()
    office_docs = app.state.office_docs
    if office_docs._db is not None:
        await office_docs.close()
    await office_docs.init()
    web_sites = app.state.web_sites
    if web_sites._db is not None:
        await web_sites.close()
    await web_sites.init()
    song_store = app.state.song_store
    if song_store._db is not None:
        await song_store.close()
    await song_store.init()
    lora_store = app.state.lora_store
    if lora_store._db is not None:
        await lora_store.close()
    await lora_store.init()
    design_docs = app.state.design_docs
    if design_docs._db is not None:
        await design_docs.close()
    await design_docs.init()
    # app_grants ledger (per-app capability grants) is lifespan-owned; tests that
    # bypass the lifespan must init it so the userspace broker can consult it.
    await app.state.app_grants.init()
    # license_acceptances ledger (non-commercial weights accept-gate, #169) is
    # also lifespan-owned; same bypass-init requirement.
    await app.state.license_acceptances.init()
    feedback_store = app.state.feedback_store
    if feedback_store._db is not None:
        await feedback_store.close()
    await feedback_store.init()
    client_log_store = app.state.client_log_store
    if client_log_store._db is not None:
        await client_log_store.close()
    await client_log_store.init()
    device_store = app.state.device_store
    if device_store._db is not None:
        await device_store.close()
    await device_store.init()
    device_pair_requests = app.state.device_pair_requests
    if device_pair_requests._db is not None:
        await device_pair_requests.close()
    await device_pair_requests.init()
    council_roles = app.state.council_roles
    if council_roles._db is not None:
        await council_roles.close()
    await council_roles.init()
    council_members = app.state.council_members
    if council_members._db is not None:
        await council_members.close()
    await council_members.init()
    # user_shares store (user-to-user resource sharing) is lifespan-owned;
    # tests that bypass the lifespan must init it so routes can consult it.
    user_shares = getattr(app.state, "user_shares", None)
    if user_shares is not None:
        if user_shares._db is not None:
            await user_shares.close()
        await user_shares.init()
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
        # Supplies the CSRF half of a signed-in browser's state on mutating
        # requests -- see _echo_csrf_cookie_into_header. Without it this client
        # is authenticated but holds no CSRF cookie, a state no real browser is
        # ever in, and every mutating request 403s for a reason that has
        # nothing to do with the route under test.
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    await canvas_store.close()
    await project_task_store.close()
    await routine_store.close()
    await board_audit.close()
    await project_store.close()
    await project_invites.close()
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
    await broker_store.close()
    await notif_store.close()
    await store.close()
    await office_docs.close()
    await web_sites.close()
    await song_store.close()
    await lora_store.close()
    await design_docs.close()
    await coding_session_store.close()
    await feedback_store.close()
    await client_log_store.close()
    await project_element_store.close()
    await project_notes_store.close()
    await project_lists_store.close()
    await project_list_entries_store.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()
    await _browser_store.close()
    await _browser_cookie_store.close()
    await app.state.council_roles.close()
    await app.state.council_members.close()
    if user_shares is not None:
        await user_shares.close()


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
    board_audit = app_with_qmd.state.board_audit
    if board_audit._db is not None:
        await board_audit.close()
    await board_audit.init()
    project_task_store = app_with_qmd.state.project_task_store
    if project_task_store._db is not None:
        await project_task_store.close()
    await project_task_store.init()
    project_element_store = app_with_qmd.state.project_element_store
    if project_element_store._db is not None:
        await project_element_store.close()
    await project_element_store.init()
    routine_store = app_with_qmd.state.routine_store
    if routine_store._db is not None:
        await routine_store.close()
    await routine_store.init()
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
        # Same reason as the `client` fixture above: this is a signed-in
        # browser, so every mutating request needs the double-submit header.
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    await canvas_store.close()
    await project_task_store.close()
    await routine_store.close()
    await board_audit.close()
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
    await project_element_store.close()
    await app_with_qmd.state.qmd_client.close()
    await app_with_qmd.state.http_client.aclose()
