def register_all_routers(app):
    """Register all application routers on the FastAPI app.

    Imports are kept function-local (mirroring the original inline block in
    create_app) to preserve lazy import behaviour and avoid circular imports
    at package import time.
    """
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

    from tinyagentos.routes.guides import router as guides_router
    app.include_router(guides_router)

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

    from tinyagentos.routes.desktop_browser import router as desktop_browser_router
    app.include_router(desktop_browser_router)

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

    from tinyagentos.routes.project_canvas import router as project_canvas_router
    app.include_router(project_canvas_router)

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
    from tinyagentos.routes.chat_files import router as chat_files_router
    app.include_router(chat_files_router)
    from tinyagentos.routes.chat_admin import router as chat_admin_router
    app.include_router(chat_admin_router)

    from tinyagentos.routes.canvas import router as canvas_router
    app.include_router(canvas_router)

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

    from tinyagentos.routes.browser_sessions import router as browser_sessions_router
    app.include_router(browser_sessions_router)

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

    from tinyagentos.routes import themes as themes_routes
    app.include_router(themes_routes.router)

    from tinyagentos.routes import framework as framework_routes
    app.include_router(framework_routes.router)

    # Lobby demo (internal only — not included in public builds)
    try:
        from tinyagentos.lobby.routes import router as lobby_router
        app.include_router(lobby_router)
    except ImportError:
        pass  # Lobby not present in public release

    from tinyagentos.routes.agent_debugger import router as agent_debugger_router
    app.include_router(agent_debugger_router)

    from tinyagentos.routes.shortcuts import router as shortcuts_router
    app.include_router(shortcuts_router)

    from tinyagentos.routes.shortcut_proxy import router as shortcut_proxy_router
    app.include_router(shortcut_proxy_router)

    from tinyagentos.routes.taos_agent import router as taos_agent_router
    app.include_router(taos_agent_router)

    from tinyagentos.routes.taosmd import router as taosmd_router
    app.include_router(taosmd_router)

    from tinyagentos.routes.setup import router as setup_router
    app.include_router(setup_router)

    from tinyagentos.routes.gh_webhook import router as gh_webhook_router
    app.include_router(gh_webhook_router)

    from tinyagentos.routes.events import router as events_router
    app.include_router(events_router)

    # OTLP/HTTP+JSON receiver — Phase 2 observability.
    # POST /v1/traces accepts ExportTraceServiceRequest JSON and writes spans
    # to the per-agent SpanStore (app.state.span_store_registry).
    from tinyagentos.otel.receiver import router as otel_receiver_router
    app.include_router(otel_receiver_router)
