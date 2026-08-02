def register_all_routers(app):
    """Register all application routers on the FastAPI app.

    Imports are kept function-local (mirroring the original inline block in
    create_app) to preserve lazy import behaviour and avoid circular imports
    at package import time.
    """
    from fastapi import Depends

    from tinyagentos.middleware.csrf import verify_csrf

    _csrf = [Depends(verify_csrf)]

    from tinyagentos.routes.auth import router as auth_router
    app.include_router(auth_router, dependencies=_csrf)

    from tinyagentos.routes.system import router as system_router
    app.include_router(system_router, dependencies=_csrf)

    from tinyagentos.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router, dependencies=_csrf)

    # Agent registry must be registered before the generic /api/agents/{name}
    # route so that /api/agents/registry/* paths resolve correctly.
    from tinyagentos.routes.agent_registry import router as agent_registry_router
    app.include_router(agent_registry_router, dependencies=_csrf)

    # Consent loop - registered before /api/agents/{name} so that
    # /api/agents/auth-requests/* paths are not captured as an agent name.
    from tinyagentos.routes.agent_auth_requests import router as agent_auth_requests_router
    app.include_router(agent_auth_requests_router, dependencies=_csrf)

    # Gated delegation (#161) - registered before /api/agents/{name} so that
    # /api/agents/{from_agent}/delegate is unambiguous even though it shares
    # the {name} path segment (path length differs so this isn't strictly
    # required, but it keeps every /api/agents/... override grouped here).
    from tinyagentos.routes.delegation import router as delegation_router
    app.include_router(delegation_router, dependencies=_csrf)

    # project_invites is registered before the agents router because it owns the
    # literal /api/agents/invites routes, which must win over the agents router's
    # /api/agents/{name} dynamic route (otherwise GET/DELETE resolve to "agent
    # not found"). Its own routes are all literal, so this reorder cannot shadow
    # any dynamic route in the projects/agents routers.
    from tinyagentos.routes.project_invites import router as project_invites_router
    app.include_router(project_invites_router, dependencies=_csrf)

    from tinyagentos.routes.agents import router as agents_router
    app.include_router(agents_router, dependencies=_csrf)

    from tinyagentos.routes.librarian import router as librarian_router
    app.include_router(librarian_router, dependencies=_csrf)

    from tinyagentos.routes.memory import router as memory_router
    app.include_router(memory_router, dependencies=_csrf)

    from tinyagentos.routes.user_memory import router as user_memory_router
    app.include_router(user_memory_router, dependencies=_csrf)

    from tinyagentos.routes.user_personas import router as user_personas_router
    app.include_router(user_personas_router, dependencies=_csrf)

    from tinyagentos.routes.settings import router as settings_router
    app.include_router(settings_router, dependencies=_csrf)

    from tinyagentos.routes.share import router as share_router
    app.include_router(share_router, dependencies=_csrf)

    from tinyagentos.routes.store import router as store_router
    app.include_router(store_router, dependencies=_csrf)

    from tinyagentos.routes import projects as projects_routes
    app.include_router(projects_routes.router, dependencies=_csrf)

    from tinyagentos.routes.community import router as community_router
    app.include_router(community_router, dependencies=_csrf)

    from tinyagentos.routes import routines as routines_routes
    app.include_router(routines_routes.router, dependencies=_csrf)

    from tinyagentos.routes.github_sync import router as github_sync_router
    app.include_router(github_sync_router, dependencies=_csrf)

    from tinyagentos.routes.decisions import router as decisions_router
    app.include_router(decisions_router, dependencies=_csrf)

    from tinyagentos.routes.devices import router as devices_router
    app.include_router(devices_router, dependencies=_csrf)

    from tinyagentos.routes.observatory import router as observatory_router
    app.include_router(observatory_router, dependencies=_csrf)

    from tinyagentos.routes.store_install import router as store_install_router
    app.include_router(store_install_router, dependencies=_csrf)

    from tinyagentos.routes.guides import router as guides_router
    app.include_router(guides_router, dependencies=_csrf)

    from tinyagentos.routes.models import router as models_router
    app.include_router(models_router, dependencies=_csrf)

    from tinyagentos.routes.images import router as images_router
    app.include_router(images_router, dependencies=_csrf)

    from tinyagentos.routes.music import router as music_router
    app.include_router(music_router, dependencies=_csrf)

    from tinyagentos.routes.images_edit import router as images_edit_router
    app.include_router(images_edit_router, dependencies=_csrf)

    from tinyagentos.routes.a2a_bus import router as a2a_bus_router
    app.include_router(a2a_bus_router, dependencies=_csrf)

    from tinyagentos.routes.scheduler import router as scheduler_router
    app.include_router(scheduler_router, dependencies=_csrf)

    from tinyagentos.routes.benchmarks import router as benchmarks_router
    app.include_router(benchmarks_router, dependencies=_csrf)

    from tinyagentos.routes.torrent import router as torrent_router
    app.include_router(torrent_router, dependencies=_csrf)

    from tinyagentos.routes.video import router as video_router
    app.include_router(video_router, dependencies=_csrf)

    from tinyagentos.routes.notifications import router as notifications_router
    app.include_router(notifications_router, dependencies=_csrf)

    from tinyagentos.routes.relationships import router as relationships_router
    app.include_router(relationships_router, dependencies=_csrf)

    from tinyagentos.routes.secrets import router as secrets_router
    app.include_router(secrets_router, dependencies=_csrf)

    from tinyagentos.routes.broker import router as broker_router
    app.include_router(broker_router, dependencies=_csrf)

    from tinyagentos.routes.mail import router as mail_router
    app.include_router(mail_router, dependencies=_csrf)

    from tinyagentos.routes.desktop_browser import router as desktop_browser_router
    app.include_router(desktop_browser_router, dependencies=_csrf)

    from tinyagentos.routes.channels import router as channels_router
    app.include_router(channels_router, dependencies=_csrf)

    from tinyagentos.routes.tasks import router as tasks_router
    app.include_router(tasks_router, dependencies=_csrf)

    from tinyagentos.routes.import_data import router as import_router
    app.include_router(import_router, dependencies=_csrf)

    from tinyagentos.routes.cluster import router as cluster_router
    app.include_router(cluster_router, dependencies=_csrf)

    from tinyagentos.routes.cluster_migrate import router as cluster_migrate_router
    app.include_router(cluster_migrate_router, dependencies=_csrf)

    from tinyagentos.routes.cluster_capability import router as cluster_capability_router
    app.include_router(cluster_capability_router, dependencies=_csrf)

    from tinyagentos.routes.training import router as training_router
    app.include_router(training_router, dependencies=_csrf)

    from tinyagentos.routes.conversion import router as conversion_router
    app.include_router(conversion_router, dependencies=_csrf)

    from tinyagentos.routes.workspace import router as workspace_router
    app.include_router(workspace_router, dependencies=_csrf)

    from tinyagentos.routes.user_workspace import router as user_workspace_router
    app.include_router(user_workspace_router, dependencies=_csrf)

    from tinyagentos.routes.agent_workspace import router as agent_workspace_router
    app.include_router(agent_workspace_router, dependencies=_csrf)

    from tinyagentos.routes.project_files import router as project_files_router
    app.include_router(project_files_router, dependencies=_csrf)

    from tinyagentos.routes.project_canvas import router as project_canvas_router
    app.include_router(project_canvas_router, dependencies=_csrf)

    from tinyagentos.routes.project_doc_review import router as project_doc_review_router
    app.include_router(project_doc_review_router, dependencies=_csrf)

    from tinyagentos.routes.desktop_control import router as desktop_control_router
    app.include_router(desktop_control_router, dependencies=_csrf)

    from tinyagentos.routes.shared_folders import router as shared_folders_router
    app.include_router(shared_folders_router, dependencies=_csrf)

    from tinyagentos.routes.providers import router as providers_router
    app.include_router(providers_router, dependencies=_csrf)

    from tinyagentos.routes.channel_hub import router as channel_hub_router_routes
    app.include_router(channel_hub_router_routes, dependencies=_csrf)

    from tinyagentos.routes.search import router as search_router
    app.include_router(search_router, dependencies=_csrf)

    from tinyagentos.routes.streaming import router as streaming_router
    app.include_router(streaming_router, dependencies=_csrf)

    from tinyagentos.routes.templates import router as templates_router
    app.include_router(templates_router, dependencies=_csrf)

    from tinyagentos.routes.chat import router as chat_router
    app.include_router(chat_router, dependencies=_csrf)
    from tinyagentos.routes.chat_files import router as chat_files_router
    app.include_router(chat_files_router, dependencies=_csrf)
    from tinyagentos.routes.chat_admin import router as chat_admin_router
    app.include_router(chat_admin_router, dependencies=_csrf)

    from tinyagentos.routes.canvas import router as canvas_router
    app.include_router(canvas_router, dependencies=_csrf)

    from tinyagentos.routes.desktop import router as desktop_router
    app.include_router(desktop_router, dependencies=_csrf)

    from tinyagentos.routes.games import router as games_router
    app.include_router(games_router, dependencies=_csrf)

    from tinyagentos.routes.game_assets import router as game_assets_router
    app.include_router(game_assets_router, dependencies=_csrf)

    from tinyagentos.routes.terminal import router as terminal_router
    app.include_router(terminal_router, dependencies=_csrf)

    from tinyagentos.routes.skills import router as skills_router
    app.include_router(skills_router, dependencies=_csrf)

    from tinyagentos.routes.skill_exec import router as skill_exec_router
    app.include_router(skill_exec_router, dependencies=_csrf)

    from tinyagentos.routes.activity import router as activity_router
    app.include_router(activity_router, dependencies=_csrf)

    from tinyagentos.routes.frameworks import router as frameworks_router
    app.include_router(frameworks_router, dependencies=_csrf)

    from tinyagentos.routes.knowledge import router as knowledge_router
    app.include_router(knowledge_router, dependencies=_csrf)

    from tinyagentos.routes.agent_browsers import router as agent_browsers_router
    app.include_router(agent_browsers_router, dependencies=_csrf)

    from tinyagentos.routes.browser_sessions import router as browser_sessions_router
    app.include_router(browser_sessions_router, dependencies=_csrf)

    from tinyagentos.routes.reddit import router as reddit_router
    app.include_router(reddit_router, dependencies=_csrf)

    from tinyagentos.routes.github import router as github_router
    app.include_router(github_router, dependencies=_csrf)

    from tinyagentos.routes.github_oauth import router as github_oauth_router
    app.include_router(github_oauth_router, dependencies=_csrf)

    from tinyagentos.routes.youtube import router as youtube_router
    app.include_router(youtube_router, dependencies=_csrf)

    from tinyagentos.routes.x import router as x_router
    app.include_router(x_router, dependencies=_csrf)

    from tinyagentos.routes.browsing_history import router as browsing_history_router
    app.include_router(browsing_history_router, dependencies=_csrf)

    from tinyagentos.routes.knowledge_graph import router as kg_router
    app.include_router(kg_router, dependencies=_csrf)

    from tinyagentos.routes.archive import router as archive_router
    app.include_router(archive_router, dependencies=_csrf)

    from tinyagentos.routes.catalog import router as catalog_router
    app.include_router(catalog_router, dependencies=_csrf)

    from tinyagentos.routes.memory_management import router as memory_mgmt_router
    app.include_router(memory_mgmt_router, dependencies=_csrf)

    from tinyagentos.routes.jobs import router as jobs_router
    app.include_router(jobs_router, dependencies=_csrf)

    from tinyagentos.routes.mcp import router as mcp_router
    app.include_router(mcp_router, dependencies=_csrf)

    from tinyagentos.routes.trace import router as trace_router
    app.include_router(trace_router, dependencies=_csrf)

    from tinyagentos.routes.openclaw import router as openclaw_router
    app.include_router(openclaw_router, dependencies=_csrf)

    from tinyagentos.routes.disk_quota import router as disk_quota_router
    app.include_router(disk_quota_router, dependencies=_csrf)

    from tinyagentos.routes.recycle import router as recycle_router
    app.include_router(recycle_router, dependencies=_csrf)

    from tinyagentos.routes.service_proxy import router as service_proxy_router
    app.include_router(service_proxy_router, dependencies=_csrf)

    from tinyagentos.routes.apps import router as apps_router
    app.include_router(apps_router, dependencies=_csrf)

    from tinyagentos.routes import admin_prompts as admin_prompts_routes
    app.include_router(admin_prompts_routes.router, dependencies=_csrf)

    from tinyagentos.routes import themes as themes_routes
    app.include_router(themes_routes.router, dependencies=_csrf)

    from tinyagentos.routes import framework as framework_routes
    app.include_router(framework_routes.router, dependencies=_csrf)

    # Lobby demo (internal only - not included in public builds)
    try:
        from tinyagentos.lobby.routes import router as lobby_router
        app.include_router(lobby_router, dependencies=_csrf)
    except ImportError:
        pass  # Lobby not present in public release

    from tinyagentos.routes.agent_debugger import router as agent_debugger_router
    app.include_router(agent_debugger_router, dependencies=_csrf)

    from tinyagentos.routes.shortcuts import router as shortcuts_router
    app.include_router(shortcuts_router, dependencies=_csrf)

    from tinyagentos.routes.shortcut_proxy import router as shortcut_proxy_router
    app.include_router(shortcut_proxy_router, dependencies=_csrf)

    from tinyagentos.routes.taos_agent import router as taos_agent_router
    app.include_router(taos_agent_router, dependencies=_csrf)

    from tinyagentos.routes.taosmd import router as taosmd_router
    app.include_router(taosmd_router, dependencies=_csrf)

    from tinyagentos.routes.setup import router as setup_router
    app.include_router(setup_router, dependencies=_csrf)

    from tinyagentos.routes.gh_webhook import router as gh_webhook_router
    app.include_router(gh_webhook_router, dependencies=_csrf)

    from tinyagentos.routes.events import router as events_router
    app.include_router(events_router, dependencies=_csrf)

    from tinyagentos.routes.event_stream import router as event_stream_router
    app.include_router(event_stream_router, dependencies=_csrf)

    # OTLP/HTTP+JSON receiver -- Phase 2 observability.
    # POST /v1/traces accepts ExportTraceServiceRequest JSON and writes spans
    # to the per-agent SpanStore (app.state.span_store_registry).
    from tinyagentos.otel.receiver import router as otel_receiver_router
    app.include_router(otel_receiver_router, dependencies=_csrf)

    from tinyagentos.routes.userspace_apps import router as userspace_apps_router
    app.include_router(userspace_apps_router, dependencies=_csrf)

    from tinyagentos.routes.feedback import router as feedback_router
    app.include_router(feedback_router, dependencies=_csrf)

    from tinyagentos.routes.client_logs import router as client_logs_router
    app.include_router(client_logs_router, dependencies=_csrf)

    # Logs app backend (#1548 part 1): journald/file log sources + bug-report bundle.
    from tinyagentos.routes.system_logs import router as system_logs_router
    app.include_router(system_logs_router, dependencies=_csrf)

    from tinyagentos.routes.account_proxy import router as account_proxy_router
    app.include_router(account_proxy_router, dependencies=_csrf)

    # Local hub API (hub social slice 2): the node's own profile + object store.
    from tinyagentos.routes.hub import router as hub_router
    app.include_router(hub_router, dependencies=_csrf)

    from tinyagentos.routes.office import router as office_router
    app.include_router(office_router, dependencies=_csrf)
    from tinyagentos.routes.songs import router as songs_router
    app.include_router(songs_router, dependencies=_csrf)
    from tinyagentos.routes.web import router as web_router
    app.include_router(web_router, dependencies=_csrf)
    from tinyagentos.routes.designs import router as designs_router
    app.include_router(designs_router, dependencies=_csrf)
    from tinyagentos.routes.coding import router as coding_router
    app.include_router(coding_router, dependencies=_csrf)
    from tinyagentos.routes.store_submissions import router as store_submissions_router
    app.include_router(store_submissions_router, dependencies=_csrf)
    from tinyagentos.routes.install_registry import router as install_registry_router
    app.include_router(install_registry_router, dependencies=_csrf)

    from tinyagentos.routes.manifest import router as manifest_router
    app.include_router(manifest_router, dependencies=_csrf)

    from tinyagentos.routes.app_permissions import router as app_permissions_router
    app.include_router(app_permissions_router, dependencies=_csrf)

    from tinyagentos.routes.user_shares import router as user_shares_router
    app.include_router(user_shares_router, dependencies=_csrf)

    from tinyagentos.routes.agent_model_api import router as agent_model_api_router
    app.include_router(agent_model_api_router, dependencies=_csrf)

    from tinyagentos.routes.agent_model_keys import router as agent_model_keys_router
    app.include_router(agent_model_keys_router, dependencies=_csrf)

    from tinyagentos.routes.agent_images import router as agent_images_router
    app.include_router(agent_images_router, dependencies=_csrf)

    from tinyagentos.routes.notes import router as notes_router
    app.include_router(notes_router, dependencies=_csrf)

    from tinyagentos.routes.todo import router as todo_router
    app.include_router(todo_router, dependencies=_csrf)

    from tinyagentos.routes.coding_sessions import router as coding_sessions_router
    app.include_router(coding_sessions_router, dependencies=_csrf)

    from tinyagentos.routes.receipts import router as receipts_router
    app.include_router(receipts_router, dependencies=_csrf)

    from tinyagentos.routes.library import router as library_router
    app.include_router(library_router, dependencies=_csrf)

    from tinyagentos.routes import wallhaven as wallhaven_routes
    app.include_router(wallhaven_routes.router, dependencies=_csrf)

    from tinyagentos.routes.peer import router as peer_router
    app.include_router(peer_router)  # CSRF-exempt — bearer-only auth

    from tinyagentos.routes.council import router as council_router
    app.include_router(council_router, dependencies=_csrf)
