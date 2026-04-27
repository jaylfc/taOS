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

    # Auto-register any taOS-managed services that have a lifecycle block
    # in their app-catalog manifest but are not yet in config.backends.
    # This runs synchronously at create_app time (before the lifespan starts)
    # so the BackendCatalog is built with complete backend list.
    _services_dir = (PROJECT_DIR / "app-catalog" / "services")
    if _services_dir.exists():
        _any_added = False
        for _manifest in _services_dir.glob("*/manifest.yaml"):
            try:
                added = auto_register_from_manifest(_manifest, config)
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
    hardware_path = data_dir / "hardware.json"
    hardware_profile = get_hardware_profile(hardware_path)
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
    llm_proxy = LLMProxy(port=4000, database_url=db_url, local_token=local_token)
    channel_hub_router = MessageRouter()
    adapter_manager = AdapterManager(channel_hub_router)
    chat_messages = ChatMessageStore(data_dir / "chat.db")
    chat_channels = ChatChannelStore(data_dir / "chat.db")
    from tinyagentos.projects.project_store import ProjectStore
    from tinyagentos.projects.task_store import ProjectTaskStore
    from tinyagentos.projects.events import ProjectEventBroker
    project_store = ProjectStore(data_dir / "projects.db")
    project_event_broker = ProjectEventBroker()
    project_task_store = ProjectTaskStore(data_dir / "projects.db", broker=project_event_broker)
    projects_root = data_dir / "projects"
    chat_hub = ChatHub()
    canvas_store = CanvasStore(data_dir / "canvas.db")
    desktop_settings = DesktopSettingsStore(data_dir / "desktop.db")
    user_memory = UserMemoryStore(data_dir / "user_memory.db")
    user_personas = UserPersonaStore(data_dir / "user_personas.db")
    installed_apps = InstalledAppsStore(data_dir / "installed_apps.db")
    skills = SkillStore(data_dir / "skills.db")
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
        projects_root.mkdir(parents=True, exist_ok=True)
        await canvas_store.init()
        await desktop_settings.init()
        await user_memory.init()
        await installed_apps.init()
        await skills.init()
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
        await browsing_history.init()
        app.state.browsing_history = browsing_history
        await knowledge_graph.init()
        app.state.knowledge_graph = knowledge_graph
        await archive.init()
        app.state.archive = archive
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

        # Per-agent state lives on the host and is mounted into containers.
        # See docs/design/framework-agnostic-runtime.md.
        app.state.agent_workspaces_dir = data_dir / "agent-workspaces"
        app.state.agent_memory_dir = data_dir / "agent-memory"
        app.state.agent_workspaces_dir.mkdir(parents=True, exist_ok=True)
        app.state.agent_memory_dir.mkdir(parents=True, exist_ok=True)
        app.state.models_dir = data_dir / "models"
        app.state.models_dir.mkdir(parents=True, exist_ok=True)
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
        app.state.chat_messages = chat_messages
        app.state.chat_channels = chat_channels
        app.state.project_store = project_store
        app.state.project_task_store = project_task_store
        app.state.project_event_broker = project_event_broker
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
            # Non-fatal — if GitHub is unreachable or the tarball isn't
            # published yet, deploys fall back to images:debian/bookworm.
            try:
                asyncio.create_task(_ensure_agent_image_present())
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
        await cluster_manager.start()
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
        from tinyagentos.containers.backend import detect_runtime, set_backend
        from tinyagentos.containers.lxc import LXCBackend
        from tinyagentos.containers.docker import DockerBackend
        runtime = getattr(config, "container_runtime", "auto")
        if runtime == "auto":
            runtime = detect_runtime()
        if runtime == "lxc":
            set_backend(LXCBackend())
        elif runtime in ("docker", "podman"):
            set_backend(DockerBackend(binary=runtime))

        # Disk quota monitor — build and attach to app state so the route
        # handler can reuse the same instance (preserves in-memory last_state).
        try:
            from tinyagentos.disk_quota import DiskQuotaMonitor
            from tinyagentos.containers.backend import get_backend as _get_container_backend
            _dq_backend = _get_container_backend()
            app.state.disk_quota_monitor = DiskQuotaMonitor(config, _dq_backend, notif_store)
        except Exception:
            logger.exception("disk quota monitor failed to initialise — disk routes will still work")
            app.state.disk_quota_monitor = None

        try:
            from tinyagentos.projects.a2a import backfill_all as _a2a_backfill_all
            _n = await _a2a_backfill_all(chat_channels, project_store)
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
            )
            await beads_bridge.start()
            await beads_bridge.backfill_active()
            app.state.beads_bridge = beads_bridge
            logger.info("beads bridge ready")
        except Exception:
            logger.exception("beads bridge failed to start — continuing without")
            app.state.beads_bridge = None

        yield
        # NOTE: controller restart/shutdown does NOT touch agent containers —
        # agents and LiteLLM keep running independently, so there's nothing to
        # gracefully drain here. Only true system halt (system-shutdown) and
        # explicit agent pause/stop go through the orchestrator.
        adapter_manager.stop_all()
        for c in list(getattr(app.state, "channel_hub_connectors", {}).values()):
            await c.stop()
        await score_cache.stop()
        await backend_catalog.stop()
        await cluster_manager.stop()
        llm_proxy.stop()
        await monitor.stop()
        try:
            await auto_updater.stop()
        except Exception:
            pass
        await app.state.mcp_supervisor.stop_all()
        await app.state.trace_registry.close_all()
        await mcp_store.close()
        await scheduler_history_store.close()
        await benchmark_store.close()
        await skills.close()
        await knowledge_monitor.stop()
        await knowledge_store.close()
        await agent_browsers.close()
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
        await secrets_store.close()
        await notif_store.close()
        await metrics_store.close()
        await qmd_client.close()
        await http_client.aclose()

    app = FastAPI(title="TinyAgentOS", version="0.1.0", lifespan=lifespan)

    # Auth middleware — must be added before GZip so it runs first
    from tinyagentos.auth_middleware import AuthMiddleware
    app.add_middleware(AuthMiddleware)

    # GZip compression for faster transfers on slow SD card / network
    app.add_middleware(GZipMiddleware, minimum_size=500)

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
    app.state.chat_messages = chat_messages
    app.state.chat_channels = chat_channels
    app.state.project_store = project_store
    app.state.project_task_store = project_task_store
    app.state.project_event_broker = project_event_broker
    app.state.beads_bridge = None
    projects_root.mkdir(parents=True, exist_ok=True)
    app.state.projects_root = projects_root
    app.state.chat_hub = chat_hub
    from tinyagentos.chat.reactions import WantsReplyRegistry as _WantsReplyRegistry
    app.state.wants_reply = _WantsReplyRegistry()
    from tinyagentos.chat.typing_registry import TypingRegistry as _TypingRegistry
    app.state.typing = _TypingRegistry()
    app.state.canvas_store = canvas_store
    app.state.desktop_settings = desktop_settings
    app.state.user_memory = user_memory
    app.state.user_personas = user_personas
    app.state.installed_apps = installed_apps
    app.state.skills = skills
    app.state.knowledge_store = knowledge_store
    app.state.ingest_pipeline = knowledge_ingest
    app.state.knowledge_monitor = knowledge_monitor
    app.state.mcp_store = mcp_store
    app.state.mcp_supervisor = MCPSupervisor(mcp_store, catalog=registry, notif_store=notif_store, secrets_store=secrets_store)
    app.state.orchestrator = RestartOrchestrator(app.state)
    app.state.latest_framework_versions = {}
    import platform as _platform
    app.state.host_arch = _platform.machine()

    from tinyagentos.trace_store import TraceStoreRegistry as _TraceStoreRegistry
    app.state.trace_registry = _TraceStoreRegistry(data_dir)

    from tinyagentos.bridge_session import BridgeSessionRegistry as _BridgeSessionRegistry
    app.state.bridge_sessions = _BridgeSessionRegistry(
        trace_registry=app.state.trace_registry,
        chat_messages=chat_messages,
        chat_channels=chat_channels,
        chat_hub=chat_hub,
        archive=getattr(app.state, "archive", None),
    )

    # Detect and set container runtime (eager, so tests work without lifespan)
    try:
        from tinyagentos.containers.backend import detect_runtime, set_backend
        from tinyagentos.containers.lxc import LXCBackend
        from tinyagentos.containers.docker import DockerBackend
        _runtime = getattr(config, "container_runtime", "auto")
        if _runtime == "auto":
            _runtime = detect_runtime()
        if _runtime == "lxc":
            set_backend(LXCBackend())
        elif _runtime in ("docker", "podman"):
            set_backend(DockerBackend(binary=_runtime))
    except Exception:
        pass  # Container runtime detection is non-fatal

    # Mount static files
    static_dir = PROJECT_DIR / "static"
    if static_dir.exists():
        app.mount("/static", _CacheAwareStaticFiles(directory=str(static_dir)), name="static")

    # Mount workspace for serving generated images and other workspace files
    workspace_dir = data_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/data/workspace", StaticFiles(directory=str(workspace_dir)), name="workspace")

    # Desktop SPA assets are served by the desktop route handler (routes/desktop.py)

    # Import and include routers
    from tinyagentos.routes.auth import router as auth_router
    app.include_router(auth_router)

    from tinyagentos.routes.system import router as system_router
    app.include_router(system_router)

    from tinyagentos.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    from tinyagentos.routes.agents import router as agents_router
    app.include_router(agents_router)

    from tinyagentos.routes.librarian import router as librarian_router
    app.include_router(librarian_router)

    from tinyagentos.routes.memory import router as memory_router
    app.include_router(memory_router)

    from tinyagentos.routes.user_memory import router as user_memory_router
    app.include_router(user_memory_router)

    from tinyagentos.routes.user_personas import router as user_personas_router
    app.include_router(user_personas_router)

    from tinyagentos.routes.settings import router as settings_router
    app.include_router(settings_router)

    from tinyagentos.routes.store import router as store_router
    app.include_router(store_router)

    from tinyagentos.routes import projects as projects_routes
    app.include_router(projects_routes.router)

    from tinyagentos.routes.store_install import router as store_install_router
    app.include_router(store_install_router)

    from tinyagentos.routes.models import router as models_router
    app.include_router(models_router)

    from tinyagentos.routes.images import router as images_router
    app.include_router(images_router)

    from tinyagentos.routes.scheduler import router as scheduler_router
    app.include_router(scheduler_router)

    from tinyagentos.routes.benchmarks import router as benchmarks_router
    app.include_router(benchmarks_router)

    from tinyagentos.routes.torrent import router as torrent_router
    app.include_router(torrent_router)

    from tinyagentos.routes.video import router as video_router
    app.include_router(video_router)

    from tinyagentos.routes.notifications import router as notifications_router
    app.include_router(notifications_router)

    from tinyagentos.routes.relationships import router as relationships_router
    app.include_router(relationships_router)

    from tinyagentos.routes.secrets import router as secrets_router
    app.include_router(secrets_router)

    from tinyagentos.routes.channels import router as channels_router
    app.include_router(channels_router)

    from tinyagentos.routes.tasks import router as tasks_router
    app.include_router(tasks_router)

    from tinyagentos.routes.import_data import router as import_router
    app.include_router(import_router)

    from tinyagentos.routes.cluster import router as cluster_router
    app.include_router(cluster_router)

    from tinyagentos.routes.cluster_migrate import router as cluster_migrate_router
    app.include_router(cluster_migrate_router)

    from tinyagentos.routes.training import router as training_router
    app.include_router(training_router)

    from tinyagentos.routes.conversion import router as conversion_router
    app.include_router(conversion_router)

    from tinyagentos.routes.workspace import router as workspace_router
    app.include_router(workspace_router)

    from tinyagentos.routes.user_workspace import router as user_workspace_router
    app.include_router(user_workspace_router)

    from tinyagentos.routes.agent_workspace import router as agent_workspace_router
    app.include_router(agent_workspace_router)

    from tinyagentos.routes.project_files import router as project_files_router
    app.include_router(project_files_router)

    from tinyagentos.routes.shared_folders import router as shared_folders_router
    app.include_router(shared_folders_router)

    from tinyagentos.routes.providers import router as providers_router
    app.include_router(providers_router)

    from tinyagentos.routes.channel_hub import router as channel_hub_router_routes
    app.include_router(channel_hub_router_routes)

    from tinyagentos.routes.search import router as search_router
    app.include_router(search_router)

    from tinyagentos.routes.streaming import router as streaming_router
    app.include_router(streaming_router)

    from tinyagentos.routes.templates import router as templates_router
    app.include_router(templates_router)

    from tinyagentos.routes.chat import router as chat_router
    app.include_router(chat_router)

    from tinyagentos.routes.desktop import router as desktop_router
    app.include_router(desktop_router)

    from tinyagentos.routes.games import router as games_router
    app.include_router(games_router)

    from tinyagentos.routes.terminal import router as terminal_router
    app.include_router(terminal_router)

    from tinyagentos.routes.skills import router as skills_router
    app.include_router(skills_router)

    from tinyagentos.routes.skill_exec import router as skill_exec_router
    app.include_router(skill_exec_router)

    from tinyagentos.routes.activity import router as activity_router
    app.include_router(activity_router)

    from tinyagentos.routes.frameworks import router as frameworks_router
    app.include_router(frameworks_router)

    from tinyagentos.routes.knowledge import router as knowledge_router
    app.include_router(knowledge_router)

    from tinyagentos.routes.agent_browsers import router as agent_browsers_router
    app.include_router(agent_browsers_router)

    from tinyagentos.routes.reddit import router as reddit_router
    app.include_router(reddit_router)

    from tinyagentos.routes.github import router as github_router
    app.include_router(github_router)

    from tinyagentos.routes.youtube import router as youtube_router
    app.include_router(youtube_router)

    from tinyagentos.routes.x import router as x_router
    app.include_router(x_router)

    from tinyagentos.routes.browsing_history import router as browsing_history_router
    app.include_router(browsing_history_router)

    from tinyagentos.routes.knowledge_graph import router as kg_router
    app.include_router(kg_router)

    from tinyagentos.routes.archive import router as archive_router
    app.include_router(archive_router)

    from tinyagentos.routes.catalog import router as catalog_router
    app.include_router(catalog_router)

    from tinyagentos.routes.memory_management import router as memory_mgmt_router
    app.include_router(memory_mgmt_router)

    from tinyagentos.routes.jobs import router as jobs_router
    app.include_router(jobs_router)

    from tinyagentos.routes.mcp import router as mcp_router
    app.include_router(mcp_router)

    from tinyagentos.routes.trace import router as trace_router
    app.include_router(trace_router)

    from tinyagentos.routes.openclaw import router as openclaw_router
    app.include_router(openclaw_router)

    from tinyagentos.routes.disk_quota import router as disk_quota_router
    app.include_router(disk_quota_router)

    from tinyagentos.routes.recycle import router as recycle_router
    app.include_router(recycle_router)

    from tinyagentos.routes.service_proxy import router as service_proxy_router
    app.include_router(service_proxy_router)

    from tinyagentos.routes.apps import router as apps_router
    app.include_router(apps_router)

    from tinyagentos.routes import admin_prompts as admin_prompts_routes
    app.include_router(admin_prompts_routes.router)

    from tinyagentos.routes import framework as framework_routes
    app.include_router(framework_routes.router)

    # Lobby demo (internal only — not included in public builds)
    try:
        from tinyagentos.lobby.routes import router as lobby_router
        app.include_router(lobby_router)
    except ImportError:
        pass  # Lobby not present in public release

    # --- Memory Management Routes ---
    from tinyagentos.routes.memory_management import router as memory_management_router
    app.include_router(memory_management_router)

    return app


def main():
    import uvicorn
    config = load_config(PROJECT_DIR / "data" / "config.yaml")
    app = create_app()
    uvicorn.run(app, host=config.server.get("host", "0.0.0.0"), port=config.server.get("port", 6969))


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
