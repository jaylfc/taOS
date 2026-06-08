from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles


class _CacheAwareStaticFiles(StaticFiles):
    """StaticFiles that sets Cache-Control by file type.

    index.html / manifests / the legacy sw.js must always revalidate so
    PWAs pick up rebuilds; everything else under /static/ is fingerprinted
    or static-forever (icons, wallpaper) and is safe to cache a long time.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        filename = path.rsplit("/", 1)[-1].lower()
        is_manifest_json = (
            filename.startswith("manifest") and filename.endswith(".json")
        )
        if (
            filename.endswith((".html", ".webmanifest"))
            or filename == "sw.js"
            or is_manifest_json
        ):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        else:
            response.headers.setdefault(
                "Cache-Control", "public, max-age=86400"
            )
        return response

from tinyagentos.auth import AuthManager
from tinyagentos.backend_fallback import BackendFallback
from tinyagentos.capabilities import CapabilityChecker
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.router import TaskRouter
from tinyagentos.config import auto_register_from_manifest, load_config, save_config, save_config_locked
from tinyagentos.lifecycle_manager import LifecycleManager
from tinyagentos.channels import ChannelStore
from tinyagentos.download_manager import DownloadManager
from tinyagentos.metrics import MetricsStore
from tinyagentos.notifications import NotificationStore
from tinyagentos.qmd_client import QmdClient
from tinyagentos.backend_adapters import check_backend_health
from tinyagentos.benchmark import BenchmarkStore
from tinyagentos.installation_state import InstallationState
from tinyagentos.scheduler import BackendCatalog, HistoryStore, ScoreCache, TaskScheduler
from tinyagentos.scheduler.discovery import build_scheduler as build_resource_scheduler
from tinyagentos.torrent_settings import TorrentSettingsStore
from tinyagentos.relationships import RelationshipManager
from tinyagentos.secrets import SecretsStore
from tinyagentos.training import TrainingManager
from tinyagentos.conversion import ConversionManager
from tinyagentos.agent_messages import AgentMessageStore
from tinyagentos.shared_folders import SharedFolderManager
from tinyagentos.streaming import StreamingSessionStore
from tinyagentos.expert_agents import ExpertAgentStore
from tinyagentos.app_orchestrator import AppOrchestrator
from tinyagentos.computer_use import ComputerUseManager
from tinyagentos.webhook_notifier import WebhookNotifier
from tinyagentos.llm_proxy import LLMProxy
from tinyagentos.litellm_migrate import migrate as _litellm_migrate
from tinyagentos.agent_image import ensure_image_present as _ensure_agent_image_present
from tinyagentos.agent_image import is_prefetch_enabled as _is_prefetch_enabled
from tinyagentos.agent_image import register_prefetch_endpoint
from tinyagentos.auto_update import AutoUpdateService
from tinyagentos.restart_orchestrator import RestartOrchestrator, apply_pending_restart_check, resume_agents_from_notes
from tinyagentos.channel_hub.router import MessageRouter
from tinyagentos.channel_hub.adapter_manager import AdapterManager
from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.channel_store import ChatChannelStore
from tinyagentos.chat.hub import ChatHub
from tinyagentos.chat.canvas import CanvasStore
from tinyagentos.desktop_settings import DesktopSettingsStore
from tinyagentos.user_memory import UserMemoryStore
from tinyagentos.user_personas import UserPersonaStore
from tinyagentos.installed_apps import InstalledAppsStore
from tinyagentos.skills import SkillStore
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_ingest import IngestPipeline
from tinyagentos.knowledge_categories import CategoryEngine
from tinyagentos.knowledge_monitor import MonitorService
from tinyagentos.mcp import MCPServerStore, MCPSupervisor
from tinyagentos.frameworks import FRAMEWORKS, FrameworkManifestError, validate_framework_manifest

PROJECT_DIR = Path(__file__).parent.parent

# Paths that must remain accessible before startup completes (health checks,
# static assets, auth endpoints).  Everything else gets 503 until the lifespan
# finishes its init sequence.
_STARTUP_EXEMPT_PATHS = frozenset({"/api/health", "/api/version"})
_STARTUP_EXEMPT_PREFIXES = ("/static/", "/desktop/", "/chat-pwa/", "/ws/", "/auth/", "/setup", "/shortcut/")


def _resolve_browser_cookie_key(data_dir: "Path") -> str:
    """Resolve the SQLCipher key for the browser cookie store.

    Precedence:
      1. TAOS_BROWSER_COOKIE_KEY_HEX env var (must be 64 hex chars) — for
         recovery / pinned-key deployments.
      2. data_dir / "browser_cookie_key.hex" — read existing per-install
         random key, or create a new one with secrets.token_hex(32) if
         absent. File is mode 0o600.

    Returns a 64-char hex string suitable for BrowserCookieStore(key_hex=…).
    """
    import os
    import secrets

    env_key = os.environ.get("TAOS_BROWSER_COOKIE_KEY_HEX", "").strip()
    if env_key:
        if len(env_key) != 64:
            raise RuntimeError(
                "TAOS_BROWSER_COOKIE_KEY_HEX must be exactly 64 hex chars"
            )
        return env_key

    key_path = data_dir / "browser_cookie_key.hex"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()

    new_key = secrets.token_hex(32)  # 256 bits
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(new_key, encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        # On Windows / non-POSIX this is a no-op; the env-var path is
        # the recommended fallback there.
        pass
    return new_key


def create_app(data_dir: Path | None = None, catalog_dir: Path | None = None) -> FastAPI:
    from tinyagentos.registry import AppRegistry
    from tinyagentos.hardware import get_hardware_profile

    data_dir = data_dir or PROJECT_DIR / "data"
    config_path = data_dir / "config.yaml"
    # Copy example config on first run
    if not config_path.exists():
        example = data_dir / "config.yaml.example"
        if example.exists():
            import shutil
            shutil.copy2(example, config_path)
    config = load_config(config_path)

    # Sweep config.backends for duplicates accumulated over restarts —
    # auto-register and the manual /api/providers POST both write here,
    # and a manifest id rename or repeated registration could land two
    # entries that share (type, url) or even share name. johny saw this
    # on #312 with two rkllama entries and no way to remove them. Dedupe
    # by name first, then by (type, url) tuple — persist the cleaned
    # list so the file matches what we serve.
    if config.backends:
        seen_names: set[str] = set()
        seen_url_type: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for b in config.backends:
            name = b.get("name") or ""
            url = b.get("url") or ""
            btype = b.get("type") or ""
            url_type_key = (btype, url) if url and btype else None
            if name and name in seen_names:
                logger.warning(
                    "config.backends: dropping duplicate-by-name entry %r", name,
                )
                continue
            if url_type_key and url_type_key in seen_url_type:
                logger.warning(
                    "config.backends: dropping duplicate-by-(type,url) entry "
                    "name=%r type=%r url=%r",
                    name, btype, url,
                )
                continue
            if name:
                seen_names.add(name)
            if url_type_key:
                seen_url_type.add(url_type_key)
            deduped.append(b)
        if len(deduped) != len(config.backends):
            config.backends = deduped
            if config.config_path and config.config_path.exists():
                save_config(config, config.config_path)

    # Hardware profile drives auto-registration: a service whose manifest
    # declares e.g. ``arm-npu-*: full`` and ``x86-cuda-*: unsupported``
    # shouldn't be added as a backend on an x86 controller (rk-llama.cpp
    # was registering everywhere before this). Load it BEFORE the
    # auto-register loop so the gate is informed.
    hardware_path = data_dir / "hardware.json"
    hardware_profile = get_hardware_profile(hardware_path)

    # Auto-register any taOS-managed services that have a lifecycle block
    # in their app-catalog manifest but are not yet in config.backends.
    # This runs synchronously at create_app time (before the lifespan starts)
    # so the BackendCatalog is built with complete backend list.
    _services_dir = (PROJECT_DIR / "app-catalog" / "services")
    if _services_dir.exists():
        _any_added = False
        for _manifest in _services_dir.glob("*/manifest.yaml"):
            try:
                added = auto_register_from_manifest(
                    _manifest, config, hardware_profile=hardware_profile,
                )
                if added:
                    logger.info("auto-registered backend from manifest: %s", _manifest)
                    _any_added = True
            except Exception:
                logger.exception("failed to auto-register from manifest %s", _manifest)
        if _any_added and config.config_path and config.config_path.exists():
            # Persist newly registered backends to config.yaml synchronously
            # (lifespan hasn't started yet so we use the sync save).
            save_config(config, config.config_path)

    for fw_id, entry in FRAMEWORKS.items():
        try:
            validate_framework_manifest(
                fw_id, entry,
                require_update_fields=bool(entry.get("release_source")),
            )
        except FrameworkManifestError:
            logger.exception("framework manifest validation failed")
            # Do NOT raise — legacy manifests can still run agents; only update paths are disabled.

    catalog_dir = catalog_dir or PROJECT_DIR / "app-catalog"
    # hardware_path / hardware_profile already loaded above before the
    # auto-register loop; don't re-probe.
    installed_path = data_dir / "installed.json"
    registry = AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)

    metrics_store = MetricsStore(data_dir / "metrics.db")
    notif_store = NotificationStore(data_dir / "notifications.db")
    mcp_store = MCPServerStore(data_dir / "mcp.db")
    qmd_client = QmdClient(config.qmd.get("url", "http://localhost:7832"))
    http_client = httpx.AsyncClient(timeout=30)
    torrent_settings_store = TorrentSettingsStore(data_dir / "torrent_settings.json")
    download_manager = DownloadManager(torrent_settings_store=torrent_settings_store)
    secrets_store = SecretsStore(data_dir / "secrets.db")
    relationship_mgr = RelationshipManager(data_dir / "relationships.db")
    channel_store = ChannelStore(data_dir / "channels.db")
    scheduler = TaskScheduler(data_dir / "scheduler.db")
    benchmark_store = BenchmarkStore(data_dir / "benchmarks.db")
    score_cache = ScoreCache(benchmark_store, poll_interval_seconds=15.0)
    scheduler_history_store = HistoryStore(data_dir / "scheduler_history.db")

    async def _probe_backend(backend: dict) -> dict:
        return await check_backend_health(http_client, backend)

    backend_catalog = BackendCatalog(
        backends=config.backends,
        probe_fn=_probe_backend,
        interval_seconds=30.0,
    )
    fallback = BackendFallback(config.backends, http_client)
    cluster_manager = ClusterManager(notifications=notif_store)
    task_router = TaskRouter(cluster_manager, http_client)
    cap_checker = CapabilityChecker(hardware_profile, cluster_manager)
    cluster_manager._capabilities = cap_checker  # wire after creation (circular dep)
    training_manager = TrainingManager(data_dir / "training.db")
    conversion_manager = ConversionManager(data_dir / "conversion.db")
    agent_messages = AgentMessageStore(data_dir / "agent_messages.db")
    shared_folders = SharedFolderManager(data_dir / "shared_folders.db", data_dir / "shared-folders")
    streaming_sessions = StreamingSessionStore(data_dir / "streaming.db")
    expert_agents = ExpertAgentStore(data_dir / "expert_agents.db")
    app_orchestrator = AppOrchestrator(cluster_manager, streaming_sessions, http_client)
    computer_use = ComputerUseManager()
    auth_manager = AuthManager(data_dir)
    webhook_notifier = WebhookNotifier(config.to_dict())
    notif_store.set_webhook_notifier(webhook_notifier)
    # Optional Postgres URL for LiteLLM's virtual key store. When this
    # file is present, LiteLLM can mint per-agent keys via /key/generate;
    # otherwise the deployer falls back to the shared master key. See
    # docs/design/framework-agnostic-runtime.md.
    db_url_path = data_dir / ".litellm_db_url"
    db_url = db_url_path.read_text().strip() if db_url_path.exists() else None
    # Read the local auth token so LLMProxy can forward it to LiteLLM's
    # subprocess — otherwise the taOS callback can't POST llm_call events
    # back to /api/trace and the 401s fill the log instead of trace rows.
    local_token_path = data_dir / ".auth_local_token"
    local_token = local_token_path.read_text().strip() if local_token_path.exists() else None
    llm_proxy = LLMProxy(
        port=4000,
        database_url=db_url,
        local_token=local_token,
        # registry lets generate_litellm_config register installed local
        # models (e.g. gemma-4-e2b-gguf) as LiteLLM model_name aliases
        # routing through the matching backend's URL. Without it, the
        # agent picker can show a local model but chatting with it 400s
        # at the proxy because no alias exists for that model_name.
        registry=registry,
        data_dir=data_dir,
    )
    channel_hub_router = MessageRouter()
    adapter_manager = AdapterManager(channel_hub_router)
    chat_messages = ChatMessageStore(data_dir / "chat.db")
    chat_channels = ChatChannelStore(data_dir / "chat.db")
    from tinyagentos.projects.project_store import ProjectStore
    from tinyagentos.projects.task_store import ProjectTaskStore
    from tinyagentos.projects.events import ProjectEventBroker
    from tinyagentos.projects.canvas.store import ProjectCanvasStore as ProjectCanvasStoreImpl
    from tinyagentos.projects.canvas.snapshotter import CanvasSnapshotter
    project_store = ProjectStore(data_dir / "projects.db")
    project_event_broker = ProjectEventBroker()
    project_task_store = ProjectTaskStore(data_dir / "projects.db", broker=project_event_broker)
    project_canvas_store = ProjectCanvasStoreImpl(data_dir / "projects.db", broker=project_event_broker)
    projects_root = data_dir / "projects"
    chat_hub = ChatHub()
    canvas_store = CanvasStore(data_dir / "canvas.db")
    desktop_settings = DesktopSettingsStore(data_dir / "desktop.db")
    user_memory = UserMemoryStore(data_dir / "user_memory.db")
    user_personas = UserPersonaStore(data_dir / "user_personas.db")
    installed_apps = InstalledAppsStore(data_dir / "installed_apps.db")
    skills = SkillStore(data_dir / "skills.db")
    from tinyagentos.themes.store import ThemeStore
    themes = ThemeStore(data_dir / "themes.sqlite3")
    knowledge_store = KnowledgeStore(
        data_dir / "knowledge.db",
        media_dir=data_dir / "knowledge-media",
    )
    knowledge_category_engine = CategoryEngine(
        store=knowledge_store,
        http_client=http_client,
        llm_url=config.backends[0].get("url", "") if config.backends else "",
    )
    knowledge_ingest = IngestPipeline(
        store=knowledge_store,
        http_client=http_client,
        notifications=notif_store,
        category_engine=knowledge_category_engine,
        qmd_base_url=config.qmd.get("url", "http://localhost:7832"),
        llm_base_url=config.backends[0].get("url", "") if config.backends else "",
    )
    knowledge_monitor = MonitorService(store=knowledge_store, http_client=http_client)

    from tinyagentos.agent_browsers import AgentBrowsersManager
    agent_browsers = AgentBrowsersManager(db_path=data_dir / "agent-browsers.db", mock=True)

    from tinyagentos.browser_sessions import BrowserSessionManager
    browser_sessions = BrowserSessionManager(data_dir / "browser_sessions.db")

    from taosmd import BrowsingHistory as BrowsingHistoryStore
    browsing_history = BrowsingHistoryStore(db_path=data_dir / "browsing-history.db")

    from taosmd import KnowledgeGraph as TemporalKnowledgeGraph
    knowledge_graph = TemporalKnowledgeGraph(db_path=data_dir / "knowledge-graph.db")

    from taosmd import Archive as ArchiveStore
    archive = ArchiveStore(archive_dir=data_dir / "archive", index_path=data_dir / "archive-index.db")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await metrics_store.init()
        await notif_store.init()
        await qmd_client.init()
        await secrets_store.init()
        await relationship_mgr.init()
        await channel_store.init()
        await scheduler.init()
        await training_manager.init()
        await conversion_manager.init()
        await agent_messages.init()
        await shared_folders.init()
        await streaming_sessions.init()
        await expert_agents.init()
        await chat_messages.init()
        await chat_channels.init()
        await project_store.init()
        await project_task_store.init()
        await project_canvas_store.init()
        projects_root.mkdir(parents=True, exist_ok=True)
        await canvas_store.init()
        await desktop_settings.init()
        await user_memory.init()
        await installed_apps.init()
        await skills.init()
        await themes.init()
        app.state.themes = themes
        await knowledge_store.init()
        await mcp_store.init()
        mcp_supervisor = MCPSupervisor(mcp_store, catalog=registry, notif_store=notif_store, secrets_store=secrets_store)
        app.state.mcp_store = mcp_store
        app.state.mcp_supervisor = mcp_supervisor
        app.state.knowledge_store = knowledge_store
        app.state.ingest_pipeline = knowledge_ingest
        app.state.knowledge_monitor = knowledge_monitor
        await knowledge_monitor.start()
        await agent_browsers.init()
        app.state.agent_browsers = agent_browsers
        await browser_sessions.init()
        app.state.browser_sessions = browser_sessions
        import secrets as _secrets
        app.state.browser_session_signing_key = _secrets.token_bytes(32)
        # Wire the unified browser runtime: populate browser_container_runner +
        # host_hardware on app.state, and fold existing agent_browsers profiles
        # into the unified session store (idempotent on each restart).
        try:
            from tinyagentos.services.mdns_publisher import _detect_primary_ipv4
            from tinyagentos.browser_sessions import wire_browser_runtime
            _host_ip = _detect_primary_ipv4() or "127.0.0.1"
            await wire_browser_runtime(
                app.state, hardware_profile, agent_browsers, browser_sessions,
                host_ip=_host_ip,
            )
        except Exception:
            logger.exception("browser runtime wiring failed — host browser sessions unavailable")
        await browsing_history.init()
        app.state.browsing_history = browsing_history
        await knowledge_graph.init()
        app.state.knowledge_graph = knowledge_graph
        await archive.init()
        app.state.archive = archive

        # BrowserApp v2 stores. Cookie store uses a per-install random
        # key persisted in data_dir; PR 5+ will replace this with a
        # per-user Argon2-derived key bound to the login password.
        #
        # The key file is created with mode 0o600 (owner read/write only).
        # Override via TAOS_BROWSER_COOKIE_KEY_HEX env var for recovery
        # (must be 64 hex chars). PR 5+ migration will need to either
        # call PRAGMA rekey to re-encrypt existing DB with the per-user
        # key, or wipe browser_cookies.sqlite3 and accept one-time logout.
        from tinyagentos.routes.desktop_browser.store import (
            BrowserStore,
            BrowserCookieStore,
        )
        browser_store = BrowserStore(data_dir / "browser.sqlite3")
        await browser_store.init()
        app.state.browser_store = browser_store

        cookie_key_hex = _resolve_browser_cookie_key(data_dir)
        browser_cookie_store = BrowserCookieStore(
            data_dir / "browser_cookies.sqlite3",
            key_hex=cookie_key_hex,
        )
        await browser_cookie_store.init()
        app.state.browser_cookie_store = browser_cookie_store

        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore, CopilotHub
        app.state.copilot_ticket_store = CopilotTicketStore()
        app.state.copilot_hub = CopilotHub()

        from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
        app.state.vapid_keypair = load_or_create_vapid_keypair(data_dir)

        await benchmark_store.init()
        await scheduler_history_store.init()
        app.state.config = config
        app.state.config_path = config_path
        app.state.data_dir = data_dir

        # Backfill all agents to the v2 persona shape and register each with
        # taosmd.  Idempotent — safe to run on every startup.
        try:
            from tinyagentos.migrations import migrate_persona_v2
            import taosmd.agents as _tm_agents
            migrate_persona_v2(config.agents, register_fn=_tm_agents.register_agent)
            if config.config_path and config.config_path.exists():
                await save_config_locked(config, config.config_path)
        except Exception:
            logger.exception("persona_v2 startup migration failed")
        # Probe installed framework versions in the BACKGROUND so the UI can
        # show whether each agent is up-to-date before any manual check is
        # triggered. Each probe does an `incus exec` into the agent container,
        # which is 1-3s cold; serialising 6+ of them blocks uvicorn from
        # accepting requests for 10-20s after a restart. The Update modal
        # polls /api/settings/update-status and feels "hung" that whole time.
        # Backgrounding this makes the restart feel instant; tags populate
        # a few seconds later and the Framework tab re-probes on open anyway.
        async def _probe_framework_versions() -> None:
            try:
                from tinyagentos.framework_update import _read_installed_tag
                for agent_dict in config.agents:
                    if agent_dict.get("framework_version_tag") is not None:
                        continue
                    fw_id = agent_dict.get("framework")
                    manifest = FRAMEWORKS.get(fw_id, {})
                    if not manifest.get("service_name"):
                        continue
                    container = f"taos-agent-{agent_dict['name']}"
                    try:
                        tag = await _read_installed_tag(container)
                        if tag:
                            agent_dict["framework_version_tag"] = tag
                    except Exception:
                        logger.warning("framework probe failed for %s", agent_dict.get("name"))
                if config.config_path:
                    await save_config_locked(config, config.config_path)
            except Exception:
                logger.exception("framework version probe failed")

        asyncio.create_task(_probe_framework_versions())

        async def _ephemeral_sweep_loop(app: FastAPI) -> None:
            import asyncio as _asyncio
            _store = app.state.chat_messages
            _hub = getattr(app.state, "chat_hub", None)
            while True:
                try:
                    deleted = await _store.sweep_expired()
                    if _hub is not None:
                        for mid, cid in deleted:
                            await _hub.broadcast(cid, {
                                "type": "message_delete",
                                "seq": _hub.next_seq(),
                                "channel_id": cid,
                                "message_id": mid,
                                "deleted_at": __import__("time").time(),
                            })
                except Exception as _e:
                    logger.warning("ephemeral sweep failed: %s", _e)
                await _asyncio.sleep(300)

        asyncio.create_task(_ephemeral_sweep_loop(app))

        async def _browser_reap_loop(app: FastAPI) -> None:
            import asyncio as _asyncio
            mgr = app.state.browser_sessions
            cluster = app.state.cluster_manager
            while True:
                try:
                    auth_token = getattr(app.state, "browser_worker_auth_token", None)
                    reaped = await mgr.reap_idle()  # flips stale running→idle, returns ids
                    for sid in reaped:
                        try:
                            s = await mgr.get_session(sid)
                            if s and s.get("node") and s.get("container_id"):
                                w = cluster.get_worker(s["node"])
                                if w is not None:
                                    await mgr.stop_on_worker(
                                        sid, worker_url=w.url, container_id=s["container_id"],
                                        auth_token=auth_token, set_status=None,
                                    )
                        except Exception as _sid_e:
                            logger.warning("browser reap: stop for %s failed: %s", sid, _sid_e)
                except Exception as _e:
                    logger.warning("browser reap failed: %s", _e)
                await _asyncio.sleep(300)

        asyncio.create_task(_browser_reap_loop(app))

        # Per-agent state lives on the host and is mounted into containers.
        # See docs/design/framework-agnostic-runtime.md.
        app.state.agent_workspaces_dir = data_dir / "agent-workspaces"
        app.state.agent_memory_dir = data_dir / "agent-memory"
        app.state.agent_workspaces_dir.mkdir(parents=True, exist_ok=True)
        app.state.agent_memory_dir.mkdir(parents=True, exist_ok=True)
        app.state.models_dir = data_dir / "models"
        app.state.models_dir.mkdir(parents=True, exist_ok=True)
        # Shared model layout root (~/models/<backend>/<family>/<id>/...)
        # — the new home for everything backend installers download.
        # Kept distinct from the legacy data/models scan target so old
        # files there still get discovered.
        from tinyagentos.installers.model_paths import models_root
        app.state.models_root = models_root()
        app.state.models_root.mkdir(parents=True, exist_ok=True)
        app.state.metrics = metrics_store
        app.state.notifications = notif_store
        app.state.qmd_client = qmd_client
        app.state.http_client = http_client
        app.state.download_manager = download_manager
        app.state.torrent_settings_store = torrent_settings_store
        app.state.secrets = secrets_store
        app.state.relationships = relationship_mgr
        app.state.channels = channel_store
        app.state.fallback = fallback
        app.state.scheduler = scheduler
        app.state.cluster_manager = cluster_manager
        app.state.task_router = task_router
        app.state.capabilities = cap_checker
        app.state.training = training_manager
        app.state.conversion = conversion_manager
        app.state.agent_messages = agent_messages
        app.state.shared_folders = shared_folders
        app.state.streaming_sessions = streaming_sessions
        app.state.expert_agents = expert_agents
        app.state.app_orchestrator = app_orchestrator
        app.state.computer_use = computer_use
        app.state.auth = auth_manager
        # Ensure the local token file exists before any request can arrive.
        # Logs the path at INFO so the user can find it.
        _local_token_path = auth_manager.local_token_path()
        auth_manager.get_local_token()
        logger.info("local auth token path: %s", _local_token_path)
        app.state.webhook_notifier = webhook_notifier
        app.state.llm_proxy = llm_proxy
        app.state.channel_hub_router = channel_hub_router
        channel_hub_router.set_archive(archive)  # Wire archive for zero-loss channel message capture
        app.state.adapter_manager = adapter_manager
        app.state.channel_hub_connectors = {}
        app.state.deploy_tasks = {}
        from tinyagentos.routes.agents import IdempotencyCache
        app.state.idempotency_cache = IdempotencyCache()
        app.state.chat_messages = chat_messages
        app.state.chat_channels = chat_channels
        app.state.project_store = project_store
        app.state.project_task_store = project_task_store
        app.state.project_event_broker = project_event_broker
        app.state.project_canvas_store = project_canvas_store
        app.state.projects_root = projects_root
        app.state.chat_hub = chat_hub
        from tinyagentos.chat.group_policy import GroupPolicy
        app.state.group_policy = GroupPolicy()
        from tinyagentos.chat.reactions import WantsReplyRegistry
        app.state.wants_reply = WantsReplyRegistry()
        from tinyagentos.chat.typing_registry import TypingRegistry
        app.state.typing = TypingRegistry()
        from tinyagentos.agent_chat_router import AgentChatRouter
        app.state.agent_chat_router = AgentChatRouter(app.state)
        app.state.canvas_store = canvas_store
        app.state.desktop_settings = desktop_settings
        app.state.user_memory = user_memory
        app.state.user_personas = user_personas
        app.state.installed_apps = installed_apps
        app.state.skills = skills
        app.state.benchmark_store = benchmark_store
        app.state.score_cache = score_cache
        app.state.scheduler_history_store = scheduler_history_store
        orchestrator = RestartOrchestrator(app.state)
        app.state.orchestrator = orchestrator
        # Boot-time: check if a pending restart was applied successfully
        try:
            await apply_pending_restart_check(app.state)
        except Exception:
            logger.exception("boot-time pending restart check failed")
        # Boot-time: resume any agents that have resume notes from a prior shutdown
        try:
            await resume_agents_from_notes(app.state)
        except Exception:
            logger.exception("boot-time agent resume failed")
        # Optionally start LiteLLM proxy (non-fatal if not installed).
        # Resolve every backend's api_key_secret so LiteLLM's
        # os.environ/<name> markers land in the subprocess env and
        # cloud providers can actually authenticate.
        try:
            # Apply LiteLLM's Prisma schema before spawning the proxy so
            # /key/generate works on fresh installs. No-ops when no DB is
            # configured or the schema is already present.
            try:
                _litellm_migrate(data_dir)
            except Exception:
                logger.exception("litellm prisma migration failed — virtual keys will not work")
            # Kick off the one-time agent base image import in the background.
            # Only runs when the user has explicitly opted in via
            # TAOS_PREFETCH_BASE_IMAGE=1. Non-fatal — if GitHub is
            # unreachable or the tarball isn't published yet, deploys
            # fall back to images:debian/bookworm.
            try:
                if _is_prefetch_enabled():
                    asyncio.create_task(_ensure_agent_image_present())
                else:
                    logger.debug(
                        "agent_image: base image prefetch disabled "
                        "(set TAOS_PREFETCH_BASE_IMAGE=1 to enable)"
                    )
            except Exception:
                logger.exception("agent base image bootstrap scheduling failed")
            resolved_secrets: dict[str, str] = {}
            for backend in config.backends:
                name = backend.get("api_key_secret")
                if not name or name in resolved_secrets:
                    continue
                try:
                    rec = await secrets_store.get(name)
                except Exception as exc:
                    logger.warning("llm_proxy: secret lookup for %s failed: %s", name, exc)
                    continue
                if rec and rec.get("value"):
                    resolved_secrets[name] = rec["value"]
            await llm_proxy.start(config.backends, secrets=resolved_secrets)
        except Exception:
            pass  # LiteLLM is optional
        # Start background health monitor
        from tinyagentos.health import HealthMonitor
        monitor = HealthMonitor(config, metrics_store, qmd_client, http_client, notif_store)
        app.state.registry = registry
        app.state.hardware_profile = hardware_profile
        app.state.health_monitor = monitor
        await monitor.start()

        # Hourly auto-update checker. Polls the git remote, notifies the
        # user on new commits, optionally applies automatically (user
        # toggle via /api/preferences/auto-update).
        auto_updater = AutoUpdateService(
            project_dir=PROJECT_DIR,
            notif_store=notif_store,
            settings_store=desktop_settings,
            app_state=app.state,
        )
        app.state.auto_updater = auto_updater
        try:
            await auto_updater.start()
        except Exception:
            logger.exception("auto-update service failed to start")
        # Keep LiteLLM's model_list fresh as cloud provider catalogs change
        # upstream (e.g. a newly published model), without a restart.
        from tinyagentos.provider_refresh import CloudProviderRefresher
        provider_refresher = CloudProviderRefresher(app.state)
        app.state.provider_refresher = provider_refresher
        try:
            await provider_refresher.start()
        except Exception:
            logger.exception("cloud provider refresher failed to start")
        # Reverse sync: detect when a user changes the model in the
        # framework's native TUI and update the taOS agent record.
        from tinyagentos.framework_model_sync import FrameworkModelReconciler
        framework_reconciler = FrameworkModelReconciler(app.state)
        app.state.framework_reconciler = framework_reconciler
        try:
            await framework_reconciler.start()
        except Exception:
            logger.exception("framework model reconciler failed to start")
        await cluster_manager.start()
        # Enroll this controller as the 'local' cluster worker so route-layer
        # code (get_local_worker) picks up the in-memory signing key.
        from tinyagentos.cluster.local_worker import enroll_local_worker
        from tinyagentos.cluster.worker_registry import set_active_manager
        from dataclasses import asdict as _asdict
        _bind_port = config.server.get("port", 6969)
        # Give the local worker the controller's own hardware + backends so the
        # Cluster view shows the host's real CPU/RAM/NPU and loaded backends.
        _local_hw = _asdict(hardware_profile) if hardware_profile is not None else {}
        await enroll_local_worker(
            cluster_manager,
            bind_port=_bind_port,
            hardware=_local_hw,
            backends=list(config.backends or []),
        )
        set_active_manager(cluster_manager)
        # Self-heartbeat the local worker so it stays online + refreshes its
        # backends/loaded-models like a real worker (it never gets heartbeats
        # from elsewhere — it IS the controller). Source backends from the LIVE
        # catalog (which probes for loaded models), not config.backends (static,
        # no model data). The lambda is evaluated each tick so it self-heals
        # once the catalog has completed its first probe.
        from tinyagentos.cluster.local_worker import local_heartbeat_loop
        app.state.local_heartbeat_task = asyncio.create_task(
            local_heartbeat_loop(
                cluster_manager,
                config,
                backends_provider=lambda: [e.to_dict() for e in backend_catalog.backends()],
            ),
            name="local-heartbeat",
        )
        # Start the live backend catalog — everything that asks "what's
        # available?" reads from this rather than the filesystem.
        try:
            await backend_catalog.start()
        except Exception:
            logger.exception("backend catalog failed to start — routes will fall back to static config")
        app.state.backend_catalog = backend_catalog

        # LifecycleManager — on-demand start/stop for auto-managed backends.
        lifecycle_manager = LifecycleManager(backend_catalog)
        app.state.lifecycle_manager = lifecycle_manager

        # Trace registry — per-agent hourly-bucketed SQLite for zero-loss capture.
        from tinyagentos.trace_store import TraceStoreRegistry
        app.state.trace_registry = TraceStoreRegistry(data_dir)

        # OTel receiver + emitter — Phase 2 observability.
        # SpanStoreRegistry is created here (lifespan) and stored on app.state
        # so both the /v1/traces receiver route and the /otel-spans read route
        # share the same registry instance.
        from tinyagentos.otel.receiver import setup_receiver
        from tinyagentos.otel.emitter import OTelEmitter
        _span_registry = setup_receiver(app.state, data_dir)
        # Wire the emitter into each AgentTraceStore so record() → emit() works.
        # Emitter points at this process's own /v1/traces route (same port as the
        # main app).  The port is read from config; default 6969.
        _bind_port_for_emitter = config.server.get("port", 6969)
        _otel_emitter = OTelEmitter(
            receiver_url=f"http://localhost:{_bind_port_for_emitter}"
        )
        app.state.otel_emitter = _otel_emitter
        # Inject the emitter into the trace registry so AgentTraceStore.record()
        # can call it after each write.
        app.state.trace_registry.set_emitter(_otel_emitter)

        # Bridge session registry — per-agent queue + accumulator for openclaw.
        from tinyagentos.bridge_session import BridgeSessionRegistry
        app.state.bridge_sessions = BridgeSessionRegistry(
            trace_registry=app.state.trace_registry,
            chat_messages=chat_messages,
            chat_channels=chat_channels,
            chat_hub=chat_hub,
            archive=getattr(app.state, "archive", None),
        )
        app.state.bridge_sessions._router = app.state.agent_chat_router

        # After the first probe, mark auto-managed backends that are not
        # currently reachable as "stopped" so the scheduler knows to start
        # them on demand rather than treating them as permanently broken.
        for _entry in backend_catalog.backends():
            _b_conf = next(
                (b for b in config.backends if b["name"] == _entry.name), {}
            )
            if _b_conf.get("auto_manage") and _entry.status != "ok":
                backend_catalog.set_lifecycle_state(_entry.name, "stopped")

        # Joined view of the registry cache + live catalog probes.
        # Used by the Store / Dashboard / Models routes instead of
        # registry.is_installed() / list_installed() directly.
        app.state.installation_state = InstallationState(registry, backend_catalog)

        # In-memory install progress store. Read by /api/store/install-progress*
        # endpoints; written by the install-v2 dispatcher and the
        # download_file callback.
        from tinyagentos.install_progress import get_global_store
        app.state.install_progress_store = get_global_store()

        # LiteLLM config reload on catalog change — keeps the proxy's
        # routing table in sync with live backend state. Subscriber is
        # a no-op if the proxy isn't running (LiteLLM not installed) or
        # if the catalog signature hasn't changed.
        async def _reload_llm_proxy_on_catalog_change() -> None:
            if not llm_proxy.is_running():
                return
            # Re-resolve secrets so rotated keys or newly-added providers
            # that changed between SIGHUPs pick up the current values.
            resolved: dict[str, str] = {}
            for backend in config.backends:
                name = backend.get("api_key_secret")
                if not name or name in resolved:
                    continue
                try:
                    rec = await secrets_store.get(name)
                except Exception:
                    continue
                if rec and rec.get("value"):
                    resolved[name] = rec["value"]
            await llm_proxy.reload_config(config.backends, secrets=resolved)

        backend_catalog.subscribe(_reload_llm_proxy_on_catalog_change)

        # Start the score cache — bridges the async benchmark store to the
        # scheduler's sync admission path via a 15s polling loop.
        try:
            await score_cache.start()
        except Exception:
            logger.exception("score cache failed to start — scheduler will route by tier only")

        # Build the resource scheduler from hardware profile + live catalog.
        # Phase 1: local resources only (NPU + CPU), capability-based routing
        # with fallback and priority. Cluster-aware dispatch is Phase 3.
        try:
            resource_scheduler = build_resource_scheduler(
                hardware_profile,
                backend_catalog,
                benchmark_store=benchmark_store,
                score_cache=score_cache,
                history_store=scheduler_history_store,
            )
            app.state.resource_scheduler = resource_scheduler
            logger.info(
                "resource scheduler ready: %s",
                [r.name for r in resource_scheduler.resources()],
            )
        except Exception:
            logger.exception("resource scheduler failed to build — routes will use static config")
            app.state.resource_scheduler = None
        # Detect and set container runtime
        from tinyagentos.containers.backend import configure_container_runtime
        configure_container_runtime(config)

        # Disk quota monitor — build and attach to app state so the route
        # handler can reuse the same instance (preserves in-memory last_state).
        try:
            from tinyagentos.disk_quota import DiskQuotaMonitor
            from tinyagentos.containers.backend import get_backend as _get_container_backend
            _dq_backend = _get_container_backend()
            app.state.disk_quota_monitor = DiskQuotaMonitor(config, _dq_backend, notif_store)
        except Exception:
            logger.exception(
                "disk quota monitor failed to initialise (likely because no container backend is "
                "configured); container-quota tracking disabled, system-wide disk usage still "
                "reported via shutil.disk_usage()"
            )
            app.state.disk_quota_monitor = None

        try:
            from tinyagentos.projects.a2a import backfill_all as _a2a_backfill_all
            _n = await _a2a_backfill_all(chat_channels, project_store, config=config)
            logger.info("a2a backfill: ensured channels for %d active projects", _n)
        except Exception:
            logger.exception("a2a backfill failed")

        # Beads bridge: project task graph ↔ A2A coordination channel.
        # See docs/superpowers/specs/2026-04-27-projects-beads-bridge-design.md.
        # Construction failure must not break boot — log and continue
        # without a bridge; routes already null-check app.state.beads_bridge.
        try:
            from tinyagentos.projects.beads_bridge import BeadsBridge
            beads_bridge = BeadsBridge(
                project_store=project_store,
                task_store=project_task_store,
                channel_store=chat_channels,
                msg_store=chat_messages,
                broker=project_event_broker,
                data_root=projects_root,
                config=config,
            )
            await beads_bridge.start()
            await beads_bridge.backfill_active()
            app.state.beads_bridge = beads_bridge
            logger.info("beads bridge ready")
        except Exception:
            logger.exception("beads bridge failed to start — continuing without")
            app.state.beads_bridge = None

        try:
            canvas_snapshotter = CanvasSnapshotter(
                project_store=project_store,
                canvas_store=project_canvas_store,
                broker=project_event_broker,
                data_root=projects_root,
            )
            await canvas_snapshotter.start()
            await canvas_snapshotter.backfill_active()
            app.state.canvas_snapshotter = canvas_snapshotter
        except Exception:
            logger.exception("canvas snapshotter failed to start")
            app.state.canvas_snapshotter = None

        # mDNS publisher — advertises http://taos.local:<port>/ on the LAN.
        # Failure must never break startup; the publisher swallows its own
        # errors but wrap it again here for belt-and-braces.
        try:
            from tinyagentos.services.mdns_publisher import MdnsPublisher
            mdns_publisher = MdnsPublisher(port=config.server.get("port", 6969))
            await mdns_publisher.start()
            app.state.mdns_publisher = mdns_publisher
        except Exception:
            logger.exception("mdns publisher failed to start — continuing without")
            app.state.mdns_publisher = None

        # System event bus — unified typed-event broadcast.
        from tinyagentos.events import EventBus, SystemEventStore
        _system_events = SystemEventStore(data_dir / "system-events.db")
        await _system_events.init()
        app.state.system_events = _system_events
        app.state.event_bus = EventBus()

        # All startup init complete — allow requests through.
        app.state._startup_complete = True
        logger.info("startup complete — accepting requests")

        yield
        # NOTE: controller restart/shutdown does NOT touch agent containers —
        # agents and LiteLLM keep running independently, so there's nothing to
        # gracefully drain here. Only true system halt (system-shutdown) and
        # explicit agent pause/stop go through the orchestrator.
        # Unregister mDNS first so the goodbye packet goes out before other
        # services start tearing down (and potentially blocking the loop).
        _mdns = getattr(app.state, "mdns_publisher", None)
        if _mdns is not None:
            try:
                await _mdns.stop()
            except Exception:
                logger.exception("mdns publisher stop failed")
        adapter_manager.stop_all()
        for c in list(getattr(app.state, "channel_hub_connectors", {}).values()):
            await c.stop()
        await score_cache.stop()
        await backend_catalog.stop()
        _hb_task = getattr(app.state, "local_heartbeat_task", None)
        if _hb_task is not None:
            _hb_task.cancel()
            # Await it so the loop has actually exited before teardown moves on
            # (cancel() alone doesn't guarantee the coroutine has unwound).
            try:
                await _hb_task
            except asyncio.CancelledError:
                pass
        await cluster_manager.stop()
        llm_proxy.stop()
        try:
            from tinyagentos.taos_agent_runtime import stop_taos_opencode_server
            await stop_taos_opencode_server(app.state)
        except Exception:
            logger.exception("taos opencode server stop failed")
        await monitor.stop()
        try:
            await auto_updater.stop()
        except Exception:
            pass
        try:
            await provider_refresher.stop()
        except Exception:
            pass
        try:
            await framework_reconciler.stop()
        except Exception:
            pass
        await app.state.mcp_supervisor.stop_all()
        await app.state.trace_registry.close_all()
        _emitter = getattr(app.state, "otel_emitter", None)
        if _emitter is not None:
            try:
                await _emitter.close()
            except Exception:
                pass
        _span_reg = getattr(app.state, "span_store_registry", None)
        if _span_reg is not None:
            try:
                await _span_reg.close_all()
            except Exception:
                pass
        await mcp_store.close()
        await scheduler_history_store.close()
        await benchmark_store.close()
        await skills.close()
        await themes.close()
        await knowledge_monitor.stop()
        await knowledge_store.close()
        await agent_browsers.close()
        await browser_sessions.close()
        await browsing_history.close()
        await knowledge_graph.close()
        await archive.close()
        await installed_apps.close()
        await user_memory.close()
        await desktop_settings.close()
        await canvas_store.close()
        try:
            bb = getattr(app.state, "beads_bridge", None)
            if bb is not None:
                await bb.stop()
        except Exception:
            logger.exception("beads bridge stop failed")
        cs_snap = getattr(app.state, "canvas_snapshotter", None)
        if cs_snap is not None:
            try:
                await cs_snap.stop()
            except Exception:
                logger.exception("canvas snapshotter stop failed")
        await project_canvas_store.close()
        await project_task_store.close()
        await project_store.close()
        await chat_channels.close()
        await chat_messages.close()
        await expert_agents.close()
        await streaming_sessions.close()
        await shared_folders.close()
        await agent_messages.close()
        await conversion_manager.close()
        await training_manager.close()
        await scheduler.close()
        await channel_store.close()
        await relationship_mgr.close()
        await browser_cookie_store.close()
        await browser_store.close()
        await secrets_store.close()
        await notif_store.close()
        await app.state.system_events.close()
        await metrics_store.close()
        await qmd_client.close()
        await http_client.aclose()

    app = FastAPI(title="TinyAgentOS", version="0.1.0", lifespan=lifespan)

    # Auth middleware — must be added before GZip so it runs first
    from tinyagentos.auth_middleware import AuthMiddleware
    app.add_middleware(AuthMiddleware)

    from tinyagentos.middleware.version_header import VersionHeaderMiddleware
    app.add_middleware(VersionHeaderMiddleware)

    # GZip compression for faster transfers on slow SD card / network
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Startup guard — return 503 for non-exempt requests that arrive before
    # the lifespan has finished initialising app state.  Added last so it is
    # outermost (Starlette wraps in reverse add_middleware order) and runs
    # before auth, preventing partially-constructed state from reaching routes.
    from starlette.middleware.base import BaseHTTPMiddleware
    from fastapi.responses import JSONResponse as _JSONResponse

    class _StartupGuardMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if not getattr(request.app.state, "_startup_complete", False):
                path = request.url.path
                if path not in _STARTUP_EXEMPT_PATHS and not any(
                    path.startswith(p) for p in _STARTUP_EXEMPT_PREFIXES
                ):
                    return _JSONResponse(
                        {"detail": "Service starting, please retry shortly"},
                        status_code=503,
                    )
            return await call_next(request)

    app.add_middleware(_StartupGuardMiddleware)

    # Startup state flags — lifespan sets _startup_complete = True once all
    # init is done.  The startup guard middleware returns 503 until then.
    app.state._startup_complete = False

    # Set state eagerly so it's available even without lifespan (e.g. tests)
    app.state.config = config
    app.state.config_path = config_path
    app.state.data_dir = data_dir
    app.state.agent_workspaces_dir = data_dir / "agent-workspaces"
    app.state.agent_memory_dir = data_dir / "agent-memory"
    app.state.agent_workspaces_dir.mkdir(parents=True, exist_ok=True)
    app.state.agent_memory_dir.mkdir(parents=True, exist_ok=True)
    app.state.metrics = metrics_store
    app.state.notifications = notif_store
    app.state.qmd_client = qmd_client
    app.state.http_client = http_client
    app.state.download_manager = download_manager
    app.state.secrets = secrets_store
    app.state.relationships = relationship_mgr
    app.state.channels = channel_store
    app.state.fallback = fallback
    app.state.scheduler = scheduler
    app.state.registry = registry
    app.state.hardware_profile = hardware_profile
    app.state.cluster_manager = cluster_manager
    app.state.task_router = task_router
    app.state.capabilities = cap_checker
    app.state.training = training_manager
    app.state.conversion = conversion_manager
    app.state.agent_messages = agent_messages
    app.state.shared_folders = shared_folders
    app.state.streaming_sessions = streaming_sessions
    app.state.expert_agents = expert_agents
    app.state.app_orchestrator = app_orchestrator
    app.state.computer_use = computer_use
    app.state.auth = auth_manager
    app.state.webhook_notifier = webhook_notifier
    app.state.llm_proxy = llm_proxy
    app.state.channel_hub_router = channel_hub_router
    app.state.adapter_manager = adapter_manager
    app.state.channel_hub_connectors = {}
    app.state.deploy_tasks = {}
    from tinyagentos.routes.agents import IdempotencyCache
    app.state.idempotency_cache = IdempotencyCache()
    app.state.chat_messages = chat_messages
    app.state.chat_channels = chat_channels
    app.state.project_store = project_store
    app.state.project_task_store = project_task_store
    app.state.project_event_broker = project_event_broker
    app.state.project_canvas_store = project_canvas_store
    app.state.beads_bridge = None
    app.state.canvas_snapshotter = None
    projects_root.mkdir(parents=True, exist_ok=True)
    app.state.projects_root = projects_root
    app.state.chat_hub = chat_hub
    # wants_reply and typing are initialised by the lifespan — do not create
    # duplicate instances here that would shadow the lifespan-created ones.
    app.state.wants_reply = None
    app.state.typing = None
    app.state.canvas_store = canvas_store
    app.state.desktop_settings = desktop_settings
    app.state.user_memory = user_memory
    app.state.user_personas = user_personas
    app.state.installed_apps = installed_apps
    app.state.skills = skills
    app.state.themes = themes
    app.state.knowledge_store = knowledge_store
    app.state.ingest_pipeline = knowledge_ingest
    app.state.knowledge_monitor = knowledge_monitor
    app.state.mcp_store = mcp_store
    # mcp_supervisor, orchestrator, trace_registry, bridge_sessions,
    # copilot_ticket_store, copilot_hub, and vapid_keypair are all created by
    # the lifespan.  Setting None here ensures attribute-existence checks in
    # routes work correctly during any brief pre-startup window, and that the
    # lifespan-created instances are never shadowed by stale eager objects.
    app.state.mcp_supervisor = None
    app.state.orchestrator = None
    app.state.latest_framework_versions = {}
    import platform as _platform
    app.state.host_arch = _platform.machine()
    app.state.trace_registry = None
    app.state.otel_emitter = None
    app.state.span_store_registry = None
    app.state.bridge_sessions = None
    app.state.copilot_ticket_store = None
    app.state.copilot_hub = None
    app.state.vapid_keypair = None

    # Detect and set container runtime (eager, so tests work without lifespan)
    try:
        from tinyagentos.containers.backend import configure_container_runtime
        configure_container_runtime(config)
    except Exception:
        logger.exception("container backend auto-init failed")

    # Mount static files
    static_dir = PROJECT_DIR / "static"
    if static_dir.exists():
        app.mount("/static", _CacheAwareStaticFiles(directory=str(static_dir)), name="static")

    # Mount workspace for serving generated images and other workspace files
    workspace_dir = data_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/data/workspace", StaticFiles(directory=str(workspace_dir)), name="workspace")

    # Desktop SPA assets are served by the desktop route handler (routes/desktop.py)

    # Register all routers (extracted to routes/register_all_routers)
    from tinyagentos.routes import register_all_routers
    register_all_routers(app)

    # Agent base image prefetch status endpoint
    register_prefetch_endpoint(app)

    return app


def main():
    import uvicorn
    config = load_config(PROJECT_DIR / "data" / "config.yaml")
    app = create_app()
    uvicorn.run(
        app,
        host=config.server.get("host", "0.0.0.0"),
        port=config.server.get("port", 6969),
        backlog=128,
    )


def gui():
    """Launch the TinyAgentOS web UI in a browser window."""
    import subprocess
    import webbrowser
    port = 6969
    url = f"http://localhost:{port}"
    # Try Chromium in app mode first (cleanest look), fall back to default browser
    for browser in ["chromium-browser", "chromium", "google-chrome"]:
        try:
            subprocess.Popen([browser, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    # Fallback: open in default browser
    webbrowser.open(url)
