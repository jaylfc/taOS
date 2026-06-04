from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import taosmd.agents as tm_agents

from tinyagentos.agent_db import find_agent, get_agent_summaries
from tinyagentos.config import save_config_locked, validate_agent_name, unique_agent_slug
from tinyagentos.routes import agent_archive
from tinyagentos.routes import agent_deploy
logger = logging.getLogger(__name__)

EXPORT_VERSION = 1

router = APIRouter()


class AgentCreate(BaseModel):
    name: str
    host: str
    qmd_index: str
    color: str = "#888888"
    can_read_user_memory: bool = False


class AgentUpdate(BaseModel):
    host: str | None = None
    qmd_index: str | None = None
    color: str | None = None
    emoji: str | None = None
    can_read_user_memory: bool | None = None


@router.get("/api/agents")
async def list_agents(request: Request):
    """List all configured agents."""
    return request.app.state.config.agents


@router.get("/api/agents/containers")
async def list_agent_containers(request: Request):
    """List live LXC container status for all agent containers."""
    from tinyagentos.containers import list_containers
    containers = await list_containers(prefix="taos-agent-")
    return [
        {
            "name": c.name,
            "agent_name": c.name.removeprefix("taos-agent-"),
            "status": c.status,
            "ip": c.ip,
            "memory_mb": c.memory_mb,
            "cpu_cores": c.cpu_cores,
        }
        for c in containers
    ]


@router.get("/api/agents/archived")
async def list_archived_agents(request: Request):
    config = request.app.state.config
    return config.archived_agents


@router.get("/api/agents/{name}/deploy-status")
async def get_deploy_status(request: Request, name: str):
    """Get the background deploy task status for an agent."""
    deploy_tasks = request.app.state.deploy_tasks
    task = deploy_tasks.get(name)
    if task is None:
        return JSONResponse({"error": f"No deploy task found for '{name}'"}, status_code=404)
    return task


@router.get("/api/agents/{name}")
async def get_agent_endpoint(request: Request, name: str):
    """Get agent details by name."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    return agent


@router.post("/api/agents")
async def add_agent(request: Request, body: AgentCreate):
    """Add a new agent to the configuration.

    The user-supplied name is stored verbatim as ``display_name`` for UI
    purposes and slugified into ``name`` for container and path safety.
    If the slug collides with an existing agent, a numeric suffix is
    appended until it's unique.
    """
    config = request.app.state.config
    display_name = body.name.strip()
    name_error = validate_agent_name(display_name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)

    try:
        unique_slug = unique_agent_slug(config, display_name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    agent = body.model_dump()
    agent["name"] = unique_slug
    agent["display_name"] = display_name
    config.agents.append(agent)
    await save_config_locked(config, config.config_path)
    return {"status": "created", "name": unique_slug, "display_name": display_name}


@router.put("/api/agents/{name}")
async def update_agent(request: Request, name: str, body: AgentUpdate):
    """Update an existing agent's configuration."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    if body.host is not None:
        agent["host"] = body.host
    if body.qmd_index is not None:
        agent["qmd_index"] = body.qmd_index
    if body.color is not None:
        agent["color"] = body.color
    if body.emoji is not None:
        emoji = body.emoji.strip()
        agent["emoji"] = emoji if emoji else None
    if body.can_read_user_memory is not None:
        agent["can_read_user_memory"] = body.can_read_user_memory
    await save_config_locked(config, config.config_path)
    return {"status": "updated", "name": name}


class AgentPermissions(BaseModel):
    can_read_user_memory: bool | None = None


@router.put("/api/agents/{name}/permissions")
async def update_agent_permissions(request: Request, name: str, body: AgentPermissions):
    """Update an agent's permissions (e.g. user memory access)."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    if body.can_read_user_memory is not None:
        agent["can_read_user_memory"] = body.can_read_user_memory
    await save_config_locked(config, config.config_path)
    return {
        "status": "updated",
        "name": name,
        "permissions": {
            "can_read_user_memory": agent.get("can_read_user_memory", False),
        },
    }


@router.delete("/api/agents/{name}")
async def delete_agent(request: Request, name: str):
    """Archive an agent instead of hard-deleting it. The agent's
    container, workspace, and memory are preserved under an archive
    bucket so the user can restore it later — or permanently purge it
    via ``DELETE /api/agents/archived/{id}``.
    """
    result = await agent_archive.archive_agent_fully(request, name)
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=result["status_code"])
    return result


class DeployAgentRequest(BaseModel):
    name: str
    framework: str = "none"
    model: str | None = None
    color: str = "#888888"
    # Optional unicode emoji shown next to the agent in the UI.  Stored on
    # the agent record; the frontend falls back to a framework-specific
    # default when unset.
    emoji: str | None = None
    memory_limit: str | None = None
    cpu_limit: int | None = None
    can_read_user_memory: bool = False
    # Optional pin: user explicitly picked a specific worker to run on.
    # None means "controller decides" (and for worker-hosted models the
    # controller will route to wherever the model already lives).
    target_worker: str | None = None
    # Worker failure policy fields (added in worker-failure-handling).
    on_worker_failure: str = "pause"
    fallback_models: list[str] = []
    # Persona fields (v2 persona system).
    soul_md: str = ""
    agent_md: str = ""
    memory_plugin: str | None = "taosmd"
    # Per-agent override for taOSmd device + tier. None → use global default
    # from data_dir/taosmd_default.json set by the memory wizard.
    memory_config: dict | None = None
    source_persona_id: str | None = None
    save_to_library: dict | None = None  # {"name": str, "description": str|None}
    # KV-cache quantisation — sent by DeployWizard; defaults mirror normalize_agent.
    kv_cache_quant_k: str = "fp16"
    kv_cache_quant_v: str = "fp16"
    kv_cache_quant_boundary_layers: int = 0


@router.post("/api/agents/deploy")
async def deploy_agent_endpoint(request: Request, body: DeployAgentRequest):
    """Deploy a new agent.

    Resolution order for the requested model (task #176 route-only stub):

    1. Controller-local model — runs on the controller like today.
    2. Cloud provider model (openai / anthropic / litellm) — unchanged,
       LiteLLM proxy handles it on the controller.
    3. Worker-hosted model, no ``target_worker`` pin — route to the
       worker that holds the model and return 202. The actual remote
       launch is deferred to Phase 1.5; for now we return the holder's
       name so the caller (and the user) knows where the agent needs
       to land.
    4. Worker-hosted model, ``target_worker`` pinned AND that worker has
       the model — runs on the pinned worker (also 202 stub today).
    5. Worker-hosted model, ``target_worker`` pinned but that worker
       does NOT have the model — 409 with the list of workers that do.
       We never silently retarget a pinned deploy.
    6. Model not found anywhere in the cluster — 404.
    """
    config = request.app.state.config
    display_name = body.name.strip()
    name_error = validate_agent_name(display_name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)
    # Derive a container-safe slug and ensure uniqueness
    try:
        unique_slug = unique_agent_slug(config, display_name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    # Rewrite body.name to the unique slug; the original user-entered name
    # is preserved as display_name for the UI.
    body.name = unique_slug
    # Validate framework against catalog and check RAM headroom.
    fw_err = agent_deploy.validate_framework_and_ram(request, body)
    if fw_err is not None:
        return fw_err

    # ------------------------------------------------------------------
    # Cross-worker deploy routing (task #176 stub)
    # ------------------------------------------------------------------
    routed = agent_deploy.resolve_deploy_routing(request, body)
    if routed is not None:
        return routed

    # Register the agent with taosmd BEFORE mutating config so a failure
    # here aborts cleanly with no half-state.
    try:
        tm_agents.register_agent(unique_slug)
    except tm_agents.AgentExistsError:
        pass  # idempotent — agent already registered, proceed normally
    except Exception as e:
        logger.exception("register_agent(%s) failed", unique_slug)
        return JSONResponse(
            {"error": f"Could not register agent with taosmd: {e}"},
            status_code=500,
        )

    # Add agent entry immediately with deploying status. qmd_url has
    # been removed from the agent schema — every agent reads and writes
    # through the shared host qmd.service, addressed by agent name and
    # the bind-mounted per-agent SQLite at /memory. See
    # docs/design/framework-agnostic-runtime.md.
    from tinyagentos.config import normalize_agent
    emoji = (body.emoji or "").strip() or None
    new_agent = normalize_agent({
        "name": body.name,
        "display_name": display_name,
        "host": "",
        "color": body.color,
        "emoji": emoji,
        "status": "deploying",
        "can_read_user_memory": body.can_read_user_memory,
        "on_worker_failure": body.on_worker_failure,
        "fallback_models": [m for m in body.fallback_models if m],
        "model": body.model,
        "framework": body.framework,
    })
    # Apply persona fields to the agent record.
    new_agent["soul_md"] = body.soul_md
    new_agent["agent_md"] = body.agent_md
    new_agent["memory_plugin"] = body.memory_plugin
    new_agent["memory_config"] = body.memory_config
    new_agent["source_persona_id"] = body.source_persona_id
    new_agent["migrated_to_v2_personas"] = True
    # Apply KV-cache quantisation choices from the deploy wizard.
    new_agent["kv_cache_quant_k"] = body.kv_cache_quant_k
    new_agent["kv_cache_quant_v"] = body.kv_cache_quant_v
    new_agent["kv_cache_quant_boundary_layers"] = body.kv_cache_quant_boundary_layers

    config.agents.append(new_agent)
    await save_config_locked(config, config.config_path)

    # Write to user persona library if requested.
    if body.save_to_library:
        store = getattr(request.app.state, "user_personas", None)
        if store is not None:
            store.create(
                name=body.save_to_library.get("name") or body.name,
                description=body.save_to_library.get("description"),
                soul_md=body.soul_md,
                agent_md=body.agent_md,
            )

    # Record deploy task
    deploy_tasks = request.app.state.deploy_tasks
    deploy_tasks[body.name] = {"status": "deploying", "name": body.name}

    from tinyagentos.deployer import deploy_agent, DeployRequest
    data_dir = request.app.state.data_dir
    llm_proxy = getattr(request.app.state, "llm_proxy", None)

    async def _background_deploy():
        try:
            result = await deploy_agent(DeployRequest(
                name=body.name,
                framework=body.framework,
                model=body.model,
                data_dir=data_dir,
                color=body.color,
                emoji=emoji,
                memory_limit=body.memory_limit,
                cpu_limit=body.cpu_limit,
                extra_config={
                    "llm_proxy": llm_proxy,
                    "registry": request.app.state.registry,
                },
                can_read_user_memory=body.can_read_user_memory,
            ))
            agent = find_agent(config, body.name)
            if result.get("success"):
                if agent is not None:
                    agent["host"] = result.get("ip", "")
                    agent["status"] = "running"
                    agent["llm_key"] = result.get("llm_key")
                    # Save config now so the bootstrap endpoint can return
                    # the llm_key before we start the openclaw service.
                    await save_config_locked(config, config.config_path)
                    # Start the openclaw service now that llm_key is written.
                    # install.sh enables the service but defers start so the
                    # bootstrap endpoint (which requires llm_key) succeeds.
                    if body.framework == "openclaw":
                        try:
                            from tinyagentos.containers import exec_in_container
                            container_name = f"taos-agent-{body.name}"
                            start_code, start_out = await exec_in_container(
                                container_name,
                                ["systemctl", "start", "openclaw.service"],
                                timeout=30,
                            )
                            if start_code != 0:
                                logger.warning(
                                    "openclaw service start returned %d: %s",
                                    start_code, start_out
                                )
                            else:
                                logger.info("openclaw.service started in %s", container_name)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Failed to start openclaw.service: %s", exc)
                    # Auto-create a 1:1 DM channel so the Messages app
                    # shows this agent immediately. Safe to call even if
                    # the channel exists from a prior failed deploy — the
                    # store doesn't enforce uniqueness on name, so at worst
                    # you get duplicate entries. In practice this branch
                    # only runs on first successful deploy for a given
                    # agent record because the route creates the agent
                    # with status='deploying'.
                    if not agent.get("chat_channel_id"):
                        try:
                            ch_store = request.app.state.chat_channels
                            channel = await ch_store.create_channel(
                                name=display_name,
                                type="dm",
                                created_by="user",
                                members=["user", body.name],
                                description=f"Direct messages with {display_name}",
                            )
                            if channel and channel.get("id"):
                                agent["chat_channel_id"] = channel["id"]
                        except Exception as exc:  # noqa: BLE001
                            # DM channel creation failing shouldn't fail
                            # the whole deploy — the user can still see
                            # the agent in the Agents app and retry from
                            # there. Log and move on.
                            logger.warning("DM channel create failed for %s: %s", body.name, exc)
                deploy_tasks[body.name] = {"status": "success", "name": body.name, "result": result}
            else:
                if agent is not None:
                    agent["status"] = "failed"
                deploy_tasks[body.name] = {
                    "status": "failed",
                    "name": body.name,
                    "error": result.get("error", "unknown error"),
                }
        except Exception as exc:  # noqa: BLE001
            agent = find_agent(config, body.name)
            if agent is not None:
                agent["status"] = "failed"
            deploy_tasks[body.name] = {
                "status": "failed",
                "name": body.name,
                "error": str(exc),
            }
        finally:
            await save_config_locked(config, config.config_path)

    asyncio.create_task(_background_deploy())

    # Archive smoke-check: verify trace path end-to-end after provisioning.
    # A failure here does NOT abort the deploy — it surfaces a warning flag.
    smoke_ok = await agent_deploy.archive_smoke_check(request, unique_slug, body.framework)

    return {"status": "deploying", "name": body.name, "archive_smoke_ok": smoke_ok}


async def _bulk_container_op(config, op):
    """Run an async container op over every configured agent, collecting per-agent results.

    `op` is an async callable taking the container name (e.g. start_container).
    Returns {agent_name: {"success": bool}} or {"success": False, "error": str} on exception.
    """
    results = {}
    for agent in config.agents:
        name = agent["name"]
        try:
            result = await op(f"taos-agent-{name}")
            results[name] = {"success": result.get("success", False)}
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
    return results


@router.post("/api/agents/bulk/start")
async def bulk_start_agents(request: Request):
    """Start all agent containers."""
    from tinyagentos.containers import start_container
    config = request.app.state.config
    results = await _bulk_container_op(config, start_container)
    return {"action": "start", "results": results}


@router.post("/api/agents/bulk/stop")
async def bulk_stop_agents(request: Request):
    """Stop all agent containers, running graceful prepare first."""
    from tinyagentos.containers import stop_container
    config = request.app.state.config
    orchestrator = getattr(request.app.state, "orchestrator", None)
    report = {}
    if orchestrator is not None:
        report = await orchestrator.prepare("all", "stop")
    results = await _bulk_container_op(config, stop_container)
    return {"action": "stop", "prepare_report": report, "results": results}


@router.post("/api/agents/bulk/restart")
async def bulk_restart_agents(request: Request):
    """Restart all agent containers."""
    from tinyagentos.containers import restart_container
    config = request.app.state.config
    results = await _bulk_container_op(config, restart_container)
    return {"action": "restart", "results": results}


@router.post("/api/agents/{name}/start")
async def start_agent(request: Request, name: str):
    """Start an agent's LXC container."""
    from tinyagentos.containers import start_container
    return await start_container(f"taos-agent-{name}")


@router.post("/api/agents/{name}/pause")
async def pause_agent(request: Request, name: str):
    """Gracefully prepare an agent for pause (paused=True, container still running)."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    report = {}
    if orchestrator is not None:
        report = await orchestrator.prepare([name], "pause")
    return {"status": "paused", "name": name, "report": report}


@router.post("/api/agents/{name}/stop")
async def stop_agent(request: Request, name: str):
    """Gracefully prepare then stop an agent's LXC container."""
    from tinyagentos.containers import stop_container
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    report = {}
    if orchestrator is not None:
        report = await orchestrator.prepare([name], "stop")
    stop_result = await stop_container(f"taos-agent-{name}")
    return {"prepare_report": report, "stop_result": stop_result}


@router.post("/api/agents/{name}/restart")
async def restart_agent(request: Request, name: str):
    """Restart an agent's LXC container."""
    from tinyagentos.containers import restart_container
    return await restart_container(f"taos-agent-{name}")


@router.get("/api/agents/{name}/logs")
async def agent_logs(request: Request, name: str, lines: int = 100):
    """Get recent journal logs from an agent's container."""
    from tinyagentos.containers import get_container_logs
    logs = await get_container_logs(f"taos-agent-{name}", lines=lines)
    return {"name": name, "logs": logs}


@router.get("/api/agents/{name}/export")
async def export_agent(request: Request, name: str):
    """Export an agent's full config as portable JSON."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    # Gather channel assignments
    channel_store = request.app.state.channels
    channels = await channel_store.list_for_agent(name)
    channel_export = [
        {"type": ch["type"], "config": ch.get("config", {})}
        for ch in channels
    ]

    # Gather group memberships
    relationship_mgr = request.app.state.relationships
    groups = await relationship_mgr.get_agent_groups(name)
    group_names = [g["name"] for g in groups]

    return {
        "version": EXPORT_VERSION,
        "agent": {k: v for k, v in agent.items()},
        "channels": channel_export,
        "groups": group_names,
    }


class AgentImport(BaseModel):
    version: int = 1
    agent: dict
    channels: list[dict] = []
    groups: list[str] = []


@router.post("/api/agents/import")
async def import_agent(request: Request, body: AgentImport):
    """Import an agent from an exported JSON config."""
    config = request.app.state.config

    agent_data = body.agent
    name = agent_data.get("name", "")
    if not name:
        return JSONResponse({"error": "Agent name is required in export data"}, status_code=400)
    name_error = validate_agent_name(name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)
    if find_agent(config, name):
        return JSONResponse({"error": f"Agent '{name}' already exists"}, status_code=409)

    # Create the agent
    config.agents.append(agent_data)
    await save_config_locked(config, config.config_path)

    # Restore channel assignments
    channel_store = request.app.state.channels
    for ch in body.channels:
        await channel_store.add(name, ch.get("type", ""), ch.get("config", {}))

    # Restore group memberships
    relationship_mgr = request.app.state.relationships
    existing_groups = await relationship_mgr.list_groups()
    group_map = {g["name"]: g["id"] for g in existing_groups}
    for group_name in body.groups:
        if group_name in group_map:
            await relationship_mgr.add_member(group_map[group_name], name)

    return {"status": "imported", "name": name}


@router.delete("/api/agents/{name}/destroy")
async def destroy_agent(request: Request, name: str):
    """Kept for API compatibility. Same behaviour as DELETE
    /api/agents/{name} — archives the agent. True permanent deletion
    happens via ``DELETE /api/agents/archived/{id}``.
    """
    result = await agent_archive.archive_agent_fully(request, name)
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=result["status_code"])
    return result


@router.post("/api/agents/archived/{archive_id}/restore")
async def restore_archived_agent(request: Request, archive_id: str):
    return await agent_archive.restore_archived(request, archive_id)


@router.delete("/api/agents/archived/{archive_id}")
async def purge_archived_agent(request: Request, archive_id: str):
    return await agent_archive.purge_archived(request, archive_id)


@router.post("/api/agents/{name}/resume")
async def resume_agent(request: Request, name: str):
    """Clear the paused flag on an agent, allowing it to accept new calls."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    if not agent.get("paused", False):
        return {"status": "ok", "name": name, "paused": False}
    agent["paused"] = False
    await save_config_locked(config, config.config_path)
    return {"status": "resumed", "name": name, "paused": False}


class PersonaPatch(BaseModel):
    soul_md: str | None = None
    agent_md: str | None = None
    source_persona_id: str | None = None


@router.patch("/api/agents/{slug}/persona")
async def patch_agent_persona(request: Request, slug: str, body: PersonaPatch):
    """Partially update an agent's persona fields (soul_md, agent_md, source_persona_id)."""
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    if body.soul_md is not None:
        agent["soul_md"] = body.soul_md
    if body.agent_md is not None:
        agent["agent_md"] = body.agent_md
    if body.source_persona_id is not None:
        agent["source_persona_id"] = body.source_persona_id
    await save_config_locked(config, config.config_path)
    return {"status": "ok", "agent": agent}


class MemoryPatch(BaseModel):
    memory_plugin: str


_VALID_MEMORY_PLUGINS = {"taosmd", "none"}


@router.patch("/api/agents/{slug}/memory")
async def patch_agent_memory(request: Request, slug: str, body: MemoryPatch):
    """Set the memory_plugin for an agent. Valid values: 'taosmd', 'none'."""
    if body.memory_plugin not in _VALID_MEMORY_PLUGINS:
        return JSONResponse(
            {"error": f"Invalid memory_plugin '{body.memory_plugin}'. Must be one of: {sorted(_VALID_MEMORY_PLUGINS)}"},
            status_code=400,
        )
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    agent["memory_plugin"] = body.memory_plugin
    await save_config_locked(config, config.config_path)
    return {"status": "ok", "agent": agent}


@router.post("/api/agents/{slug}/dismiss-migration-banner")
async def dismiss_migration_banner(request: Request, slug: str):
    """Flip migrated_to_v2_personas to True, hiding the migration banner."""
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": f"Agent '{slug}' not found"}, status_code=404)
    agent["migrated_to_v2_personas"] = True
    await save_config_locked(config, config.config_path)
    return {"status": "ok"}


class AgentModelUpdate(BaseModel):
    model: str


@router.post("/api/agents/{name}/model")
async def update_agent_model(request: Request, name: str, body: AgentModelUpdate):
    """Update an agent's primary model and resume it if it was paused.

    Validates the requested model against currently-reachable cluster models
    (local backend catalog + online workers).  Returns 409 if the model is
    not reachable anywhere in the cluster right now.
    """
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    model_id = body.model.strip()
    if not model_id:
        return JSONResponse({"error": "model must not be empty"}, status_code=400)

    # Validate reachability against the live cluster state.
    from tinyagentos.cluster.model_resolver import resolve_model_location

    location = resolve_model_location(request, model_id)

    if location.kind == "not_found":
        return JSONResponse(
            {
                "error": (
                    f"model '{model_id}' is not reachable anywhere in the cluster right now. "
                    f"Make sure the model is downloaded and the worker is online."
                ),
                "model": model_id,
            },
            status_code=409,
        )

    # Update the agent's model and clear the paused flag so it can
    # immediately start accepting calls on the new model.
    agent["model"] = model_id
    was_paused = agent.get("paused", False)
    agent["paused"] = False
    await save_config_locked(config, config.config_path)

    # Re-scope the agent's LiteLLM virtual key so it's actually ALLOWED to use
    # the new model — without this the change only updates taOS's record and
    # LiteLLM would 403 the new model. The key value is unchanged, so no
    # container restart/env push is needed; the framework's /v1/models (with
    # this key) reflects the new permitted set immediately. (Routing-only mode
    # without a key DB is a no-op.) Pushing model.primary into the framework
    # config + reverse reconcile from the framework are tracked in #570.
    proxy = getattr(request.app.state, "llm_proxy", None)
    llm_key = agent.get("llm_key")
    key_rescoped = False
    if proxy is not None and llm_key:
        try:
            key_rescoped = await proxy.update_agent_key(llm_key, [model_id])
        except Exception:
            logger.exception("update_agent_model: re-scoping key for %s failed", name)

    return {
        "status": "updated",
        "name": name,
        "model": model_id,
        "resumed": was_paused,
        "location": location.kind,
        "key_rescoped": key_rescoped,
    }
